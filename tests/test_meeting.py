"""Tests fuer den Termin-Agenten, den Katalog der Terminarten und die Orchestrierung."""

import unittest
from datetime import date, datetime

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents import orchestrator
from local_agents.meeting_agent import analyse, brief_notes, role_in, situation
from local_agents.meeting_types import BY_ID, MEETING_TYPES, keyword_hint
from local_agents.profile import Context, Profile
from local_agents.store import Store, StoredEvent

models.ALLOW_MODEL_REQUESTS = False

PROFILE = Profile(
    role="Teamleitung",
    contexts=(
        Context("intern", "intern", "Intern"),
        Context("kunde-acme", "kunde", "Kunde ACME", description="Projekt Datenplattform", role="Solution Architect"),
    ),
)
DAILY = StoredEvent("d", "Plattform - Daily", datetime(2026, 11, 16, 9), datetime(2026, 11, 16, 9, 15), "kunde-acme", attendees=8)
GOOD = {
    "type": "daily",
    "reason": "Titel  enthält Daily.",
    "deliverables": ["Stand der Architekturthemen seit gestern", " Offene Entscheidungen "],
}


def answering(*outputs: dict, seen: list | None = None) -> FunctionModel:
    replies = list(outputs)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append(info.instructions)
        if not info.output_tools:  # Status-Agent: freier Text
            return ModelResponse(parts=[TextPart("Als Nächstes das Daily.")])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, replies.pop(0))])

    return FunctionModel(respond)


class TestCatalogue(unittest.TestCase):
    def test_keyword_hints(self) -> None:
        cases = {
            "Plattform - Daily": "daily",
            "Sprint Review Team A": "sprint_review",
            "Retro Q4": "retrospektive",
            "Data Science Development Sync": "jour_fixe",
            "Brainstorming Onboarding": "workshop",
            "Company Call": "grosse_runde",
            "Mittagessen": None,
        }
        for title, expected in cases.items():
            hint = keyword_hint(title)
            self.assertEqual(hint.id if hint else None, expected, title)

    def test_every_type_is_complete(self) -> None:
        self.assertEqual(len(BY_ID), len(MEETING_TYPES))
        for meeting_type in MEETING_TYPES:
            self.assertTrue(meeting_type.label and meeting_type.purpose and meeting_type.contributions)


class MeetingCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.store.upsert_event(DAILY)
        self.store.upsert_task("d", "vorbereitung", "Vorbereitung: Plattform - Daily", "kunde-acme", date(2026, 11, 15), "planner")
        self.task = self.store.tasks()[0]


class TestMeetingAgent(MeetingCase):
    def test_role_comes_from_the_context_of_the_meeting(self) -> None:
        self.assertEqual(role_in(PROFILE, "kunde-acme"), "Solution Architect")
        self.assertEqual(role_in(PROFILE, "intern"), "Teamleitung")
        self.assertEqual(
            situation(DAILY, PROFILE),
            "Titel: Plattform - Daily\n"
            "Zeit: Mo 16.11.2026 09:00, 15 Minuten\n"
            "Eingeladene: 8\n"
            "Kontext: Kunde ACME (Kundenprojekt) - Projekt Datenplattform\n"
            "Rolle des Nutzers in diesem Kontext: Solution Architect\n"
            "Stichwort im Titel deutet auf: daily",
        )

    def test_analysis_is_stored_and_reused(self) -> None:
        seen: list = []
        brief = analyse(self.task, DAILY, PROFILE, self.store, answering(GOOD, seen=seen))
        self.assertEqual(
            (brief.meeting_type, brief.reason, brief.deliverables, brief.role),
            ("daily", "Titel enthält Daily.", ("Stand der Architekturthemen seit gestern", "Offene Entscheidungen"), "Solution Architect"),
        )
        self.assertIn("- daily: Daily / Stand-up (Scrum).", seen[0])
        self.assertIn("Rolle des Nutzers in diesem Kontext: Solution Architect", seen[0])
        self.assertEqual(self.store.brief(self.task.id), brief)
        # Zweiter Aufruf braucht kein Modell, ausser es wird verlangt oder der Titel aendert sich.
        self.assertEqual(analyse(self.task, DAILY, PROFILE, self.store, None), brief)
        again = analyse(self.task, DAILY, PROFILE, self.store, answering({**GOOD, "type": "jour_fixe"}), fresh=True)
        self.assertEqual(again.meeting_type, "jour_fixe")
        self.assertEqual([run["agent"] for run in self.store.runs()], ["termin", "termin"])

    def test_unknown_type_is_sent_back_and_long_lists_are_cut(self) -> None:
        many = {**GOOD, "deliverables": [f"Punkt {n}" for n in range(8)]}
        brief = analyse(self.task, DAILY, PROFILE, self.store, answering({**GOOD, "type": "kaffee"}, many))
        self.assertEqual((brief.meeting_type, len(brief.deliverables)), ("daily", 5))

    def test_falls_back_to_the_catalogue_if_the_model_fails(self) -> None:
        bad = {**GOOD, "type": "kaffee"}
        brief = analyse(self.task, DAILY, PROFILE, self.store, answering(bad, bad, bad))
        self.assertEqual(brief.meeting_type, "daily")  # Stichwort im Titel
        self.assertEqual(brief.deliverables, BY_ID["daily"].contributions)
        self.assertIn("nicht einordnen", brief.reason)

    def test_notes_show_catalogue_and_deliverables(self) -> None:
        brief = analyse(self.task, DAILY, PROFILE, self.store, answering(GOOD))
        notes = brief_notes(brief, PROFILE, "kunde-acme")
        self.assertEqual(
            list(notes),
            [
                "Terminart: Daily / Stand-up, Scrum",
                "Ergebnisse, die dir für diesen Termin helfen (als Solution Architect, Kunde ACME)",
            ],
        )
        first, second = notes.values()
        self.assertEqual(first[0], "Woran erkannt: Titel enthält Daily.")
        self.assertEqual(first[2:4], ["Was man dort üblicherweise beiträgt:", "- Was seit dem letzten Daily erreicht wurde"])
        self.assertEqual(second, ["- Stand der Architekturthemen seit gestern", "- Offene Entscheidungen"])


class TestOrchestrator(MeetingCase):
    def run_chain(self, now: datetime, *outputs: dict) -> str:
        model = answering(*outputs)
        return orchestrator.run(now, self.store, PROFILE, lambda agent: model)

    def test_status_hands_the_next_task_to_the_meeting_agent(self) -> None:
        output = self.run_chain(datetime(2026, 11, 15, 19, 0), GOOD)
        self.assertTrue(output.startswith("Als Nächstes das Daily.\n\n**Stand So 15.11.2026, 19:00 Uhr"), output)
        self.assertIn("Als Nächstes: #1 Vorbereitung: Plattform - Daily [Kunde ACME; heute fällig;", output)
        self.assertIn("\n\n**Terminart: Daily / Stand-up, Scrum**\nWoran erkannt: Titel enthält Daily.\n", output)
        self.assertTrue(output.endswith("- Stand der Architekturthemen seit gestern\n- Offene Entscheidungen"), output)
        self.assertEqual(self.store.brief(1).meeting_type, "daily")

    def test_other_kinds_of_tasks_stop_after_the_status(self) -> None:
        self.store.set_status(1, "erledigt")
        self.store.upsert_task("d", "verschieben", "Nachricht an Erika Muster: Daily verschieben", "intern", date(2026, 11, 15), "planner")
        output = self.run_chain(datetime(2026, 11, 15, 19, 0))
        self.assertTrue(output.endswith("Für Aufgaben der Art „verschieben“ gibt es noch keinen Agenten, der sie weiterbearbeitet."), output)
        self.assertIsNone(self.store.brief(2))

    def test_nothing_to_do(self) -> None:
        self.store.set_status(1, "erledigt")
        output = self.run_chain(datetime(2026, 11, 15, 19, 0))
        self.assertTrue(output.endswith("Es steht keine Aufgabe an."), output)


if __name__ == "__main__":
    unittest.main()

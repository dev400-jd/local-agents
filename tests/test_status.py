"""Tests fuer den Status-Agenten."""

import unittest
from datetime import date, datetime

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents.profile import Context, Profile
from local_agents.status_agent import (
    StatusDeps,
    answer,
    approved_at,
    item_line,
    next_task,
    next_tasks,
    remaining,
    status_heading,
)
from local_agents.store import DONE, Store, StoredEvent

models.ALLOW_MODEL_REQUESTS = False

PROFILE = Profile(contexts=(Context("intern", "intern", "Intern"), Context("kunde-acme", "kunde", "Kunde ACME")))
NOW = datetime(2026, 11, 16, 8, 30)  # Montag


class StatusCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        for event_id, title, day, hour in (("d", "Daily", 16, 9), ("j", "Jour fixe", 18, 10), ("a", "Alt", 16, 8)):
            start = datetime(2026, 11, day, hour)
            self.store.upsert_event(StoredEvent(event_id, title, start, start.replace(minute=30), "intern"))
        add = self.store.upsert_task
        add("j", "vorbereitung", "Vorbereitung: Jour fixe", "intern", date(2026, 11, 17), "planner")  # 1
        add("d", "vorbereitung", "Vorbereitung: Daily", "kunde-acme", date(2026, 11, 15), "planner")  # 2
        add("j", "verschieben", "Nachricht an Erika Muster: Jour fixe verschieben", "intern", date(2026, 11, 16), "planner")  # 3
        add("a", "vorbereitung", "Vorbereitung: Alt", "intern", date(2026, 11, 15), "planner")  # 4
        self.store.add_task("manuell", "Folien bauen", "sonstig", date(2026, 11, 20), "rueckmeldung")  # 5
        add("d", "verschieben", "Erledigt", "intern", date(2026, 11, 15), "planner")  # 6
        self.store.set_status(6, DONE)


class TestNextTasks(StatusCase):
    def test_orders_by_urgency_and_marks_past_meetings(self) -> None:
        items = next_tasks(self.store, NOW)
        self.assertEqual(
            [(item.task.id, item.level, item.note()) for item in items],
            [
                (2, "überfällig", "Termin in 30 Min"),
                (3, "heute", "Termin in 2 Tagen 1 Std"),
                (1, "morgen", "Termin in 2 Tagen 1 Std"),
                (5, "später", ""),
                (4, "hinfällig", "Termin hat begonnen"),
            ],
        )

    def test_next_task_is_the_most_urgent_one(self) -> None:
        item = next_task(self.store, NOW)
        self.assertEqual(
            item_line(item, PROFILE),
            "#2 Vorbereitung: Daily [Kunde ACME; überfällig; fertig bis So 15.11.; Termin in 30 Min]",
        )
        self.assertEqual(
            remaining(next_tasks(self.store, NOW)),
            "Danach noch 3 weitere Aufgaben: 1 heute fällig, 1 morgen fällig, 1 später fällig.",
        )

    def test_next_task_moves_on_with_the_clock(self) -> None:
        later = datetime(2026, 11, 16, 9, 5)  # das Daily laeuft schon
        self.assertEqual(next_task(self.store, later).task.id, 3)
        self.assertEqual(next_task(self.store, datetime(2026, 11, 19, 8, 0)).task.id, 5)
        self.assertEqual(remaining(next_tasks(self.store, datetime(2026, 11, 19, 8, 0))), "Danach ist nichts mehr offen.")

    def test_nothing_to_do(self) -> None:
        empty = Store(":memory:")
        self.addCleanup(empty.close)
        self.assertEqual(next_tasks(empty, NOW), [])
        self.assertIsNone(next_task(empty, NOW))


class TestApproval(StatusCase):
    def test_latest_feedback_or_approval_run_counts(self) -> None:
        self.assertIsNone(approved_at(self.store))
        self.assertEqual(status_heading(NOW, None), "Stand Mo 16.11.2026, 08:30 Uhr (Plan noch nicht abgesegnet)")
        self.store.finish_run(self.store.start_run("planner"), "ok")
        self.assertIsNone(approved_at(self.store))
        self.store.finish_run(self.store.start_run("freigabe"), "ok")
        approved = approved_at(self.store)
        self.assertIsNotNone(approved)
        self.assertIn(f"(Plan abgesegnet am {approved:%d.%m. %H:%M})", status_heading(NOW, approved))


class TestStatusAgent(StatusCase):
    def test_model_gets_time_and_tasks_and_list_is_appended(self) -> None:
        seen: list[str] = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.append(info.instructions)
            return ModelResponse(parts=[TextPart("Zuerst das Daily.")])

        deps = StatusDeps(now=NOW, store=self.store, profile=PROFILE)
        output = answer("Was steht an?", deps, FunctionModel(respond))
        self.assertEqual(
            output,
            "Zuerst das Daily.\n\n**Stand Mo 16.11.2026, 08:30 Uhr (Plan noch nicht abgesegnet)**\n"
            "Als Nächstes: #2 Vorbereitung: Daily [Kunde ACME; überfällig; fertig bis So 15.11.; Termin in 30 Min]\n"
            "Danach noch 3 weitere Aufgaben: 1 heute fällig, 1 morgen fällig, 1 später fällig.",
        )
        self.assertIn("Stand Mo 16.11.2026, 08:30 Uhr", seen[0])
        self.assertIn("Naechste Aufgabe: #2 Vorbereitung: Daily", seen[0])
        self.assertNotIn("Jour fixe", seen[0])


if __name__ == "__main__":
    unittest.main()

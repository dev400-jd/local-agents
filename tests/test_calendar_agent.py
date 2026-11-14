"""Tests des Kalender-Agenten ohne Ollama und ohne echten Kalender."""

import dataclasses
import unittest
from datetime import date, datetime

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents.calendar_agent import MAX_RANGE_DAYS, CalendarDeps, answer, calendar_agent
from local_agents.calendar_events import Event, format_events, next_week
from local_agents.profile import Context, Profile

# Kein Test darf versehentlich ein echtes Modell aufrufen.
models.ALLOW_MODEL_REQUESTS = False

EVENTS = [
    Event(
        datetime(2026, 11, 17, 9, 0),
        datetime(2026, 11, 17, 10, 0),
        "Standup",
        location="Teams",
        attendee_domains=("beispiel.de", "beispiel.de"),
    ),
    Event(datetime(2026, 11, 16, 0, 0), datetime(2026, 11, 16, 23, 59), "Urlaub", all_day=True),
    Event(
        datetime(2026, 11, 16, 14, 0),
        datetime(2026, 11, 16, 15, 30),
        "Review",
        organizer_domain="acme.example",
        attendee_domains=("beispiel.de", "acme.example"),
    ),
]

PROFILE = Profile(
    name="Erika",
    contexts=(
        Context("intern", "intern", "Intern", domains=frozenset({"beispiel.de"})),
        Context("kunde-acme", "kunde", "Kunde ACME", domains=frozenset({"acme.example"})),
    ),
)


def scripted_model(start: str, end: str, **extra: str) -> FunctionModel:
    """Modell, das list_events(start, end, ...) aufruft und das Ergebnis wiedergibt."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for part in messages[-1].parts:
            if part.part_kind == "tool-return":
                label = "FEHLER" if part.outcome == "retried" else "TERMINE"
                return ModelResponse(parts=[TextPart(f"{label}: {part.content}")])
        return ModelResponse(parts=[ToolCallPart("list_events", {"start": start, "end": end, **extra})])

    return FunctionModel(respond)


class TestNextWeek(unittest.TestCase):
    def test_from_saturday(self) -> None:
        self.assertEqual(next_week(date(2026, 11, 14)), (date(2026, 11, 16), date(2026, 11, 22)))

    def test_from_monday_skips_current_week(self) -> None:
        self.assertEqual(next_week(date(2026, 11, 16)), (date(2026, 11, 23), date(2026, 11, 29)))


class TestFormatEvents(unittest.TestCase):
    def test_groups_by_day_in_order(self) -> None:
        self.assertEqual(
            format_events(EVENTS, 1000),
            "Mo 16.11.2026\n"
            "  ganztaegig Urlaub\n"
            "  14:00-15:30 Review\n"
            "Di 17.11.2026\n"
            "  09:00-10:00 Standup (Ort: Teams)",
        )

    def test_tags_each_event(self) -> None:
        text = format_events(EVENTS[:1], 1000, tag=lambda event: "Intern")
        self.assertIn("  09:00-10:00 [Intern] Standup (Ort: Teams)", text)

    def test_empty(self) -> None:
        self.assertEqual(format_events([], 1000), "Keine Termine im Zeitraum.")

    def test_truncates(self) -> None:
        text = format_events(EVENTS, 20)
        self.assertTrue(text.endswith("[gekuerzt: 3 Termine insgesamt]"), text)


class TestCalendarAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[tuple[date, date]] = []

    def fetch(self, start: date, end: date) -> list[Event]:
        self.calls.append((start, end))
        return EVENTS

    def run_agent(self, start: str, end: str, profile: Profile = Profile(), **extra: str) -> str:
        deps = CalendarDeps(today=date(2026, 11, 14), profile=profile, fetch=self.fetch)
        model = scripted_model(start, end, **extra)
        return answer("Frage", deps, model)

    def test_lists_events_for_range(self) -> None:
        output = self.run_agent("2026-11-16", "2026-11-22")
        self.assertEqual(self.calls, [(date(2026, 11, 16), date(2026, 11, 22))])
        self.assertTrue(output.startswith("TERMINE: Mo 16.11.2026"), output)
        self.assertIn("Standup", output)

    def test_rejects_reversed_range(self) -> None:
        output = self.run_agent("2026-11-22", "2026-11-16")
        self.assertTrue(output.startswith("FEHLER:"), output)
        self.assertEqual(self.calls, [])

    def test_rejects_too_long_range(self) -> None:
        output = self.run_agent("2026-01-01", "2026-12-31")
        self.assertIn(str(MAX_RANGE_DAYS), output)
        self.assertEqual(self.calls, [])

    def test_without_profile_no_tags(self) -> None:
        self.assertNotIn("[", self.run_agent("2026-11-16", "2026-11-22"))

    def test_profile_tags_events_with_context(self) -> None:
        output = self.run_agent("2026-11-16", "2026-11-22", profile=PROFILE)
        self.assertIn("[Sonstiges] Urlaub", output)
        self.assertIn("[Kunde ACME] Review", output)
        self.assertIn("[Intern] Standup", output)

    def holiday(self, day: datetime, title: str, calendar: str = "Feiertage in Deutschland") -> Event:
        return Event(day, day.replace(hour=23), title, all_day=True, calendar=calendar)

    def test_holiday_of_other_states_is_hidden_at_location(self) -> None:
        self.fetch = lambda start, end: [
            self.holiday(datetime(2026, 11, 18), "Buss- und Bettag"),
            self.holiday(datetime(2026, 11, 22), "Totensonntag"),
            *EVENTS,
        ]
        output = self.run_agent("2026-11-16", "2026-11-22", profile=Profile(state="HE"))
        self.assertNotIn("Bettag", output)
        self.assertNotIn("Totensonntag", output)
        self.assertNotIn("Feiertag", output)
        self.assertIn("Standup", output)

    def test_holiday_shown_with_states_when_location_unknown(self) -> None:
        self.fetch = lambda start, end: [self.holiday(datetime(2026, 11, 18), "Buss- und Bettag")]
        output = self.run_agent("2026-11-16", "2026-11-22")
        self.assertIn("[gesetzlicher Feiertag nur in Sachsen] Buss- und Bettag", output)

    def test_hint_for_meetings_on_a_real_holiday(self) -> None:
        day = datetime(2026, 6, 4)
        self.fetch = lambda start, end: [
            self.holiday(day, "Fronleichnam"),
            Event(day.replace(hour=9), day.replace(hour=10), "Daily"),
        ]
        output = self.run_agent("2026-06-01", "2026-06-07", profile=Profile(state="HE"))
        self.assertIn("[gesetzlicher Feiertag in Hessen, arbeitsfrei] Fronleichnam", output)
        self.assertTrue(
            output.endswith(
                "\n\n**Feiertagshinweise**\n- Do 04.06.: Fronleichnam ist in Hessen "
                "gesetzlicher Feiertag (arbeitsfrei), trotzdem 1 Termin eingetragen."
            ),
            output,
        )

    def test_no_hint_for_real_holiday_without_meetings(self) -> None:
        self.fetch = lambda start, end: [self.holiday(datetime(2026, 6, 4), "Fronleichnam")]
        output = self.run_agent("2026-06-01", "2026-06-07", profile=Profile(state="HE"))
        self.assertIn("Fronleichnam", output)
        self.assertNotIn("Feiertagshinweise", output)

    def test_holiday_from_second_calendar_is_dropped(self) -> None:
        day = datetime(2026, 6, 4)
        self.fetch = lambda start, end: [
            self.holiday(day, "Fronleichnam", "Deutsche Feiertage"),
            self.holiday(day, "Fronleichnam (regional)", "Feiertage in Deutschland"),
        ]
        output = self.run_agent("2026-06-01", "2026-06-07", profile=Profile(state="HE"))
        self.assertEqual(output.count("Fronleichnam"), 1)

    def test_all_day_event_outside_holiday_calendar_keeps_context(self) -> None:
        output = self.run_agent("2026-11-16", "2026-11-22", profile=Profile(state="HE"))
        self.assertIn("ganztaegig Urlaub", output)

    def onsite_profile(self) -> Profile:
        # Di ist Kundentag; der interne Termin "Standup" liegt am Di 17.11.
        customer = dataclasses.replace(PROFILE.contexts[1], stress="hoch", onsite_days=frozenset({1}))
        return Profile(contexts=(PROFILE.contexts[0], customer))

    def test_appends_move_suggestions(self) -> None:
        output = self.run_agent("2026-11-16", "2026-11-22", profile=self.onsite_profile())
        self.assertTrue(output.startswith("TERMINE: "), output)
        self.assertIn(
            "\n\n**Verschiebevorschläge**\n"
            "- Di 17.11. ist Kundentag (Kunde ACME), 1 interner Termin verschieben:\n"
            "  - 09:00-10:00 Standup",
            output,
        )
        # Das Modell bekommt die Vorschlaege nicht zu sehen, nur der Nutzer.
        self.assertEqual(output.count("Verschiebevorschläge"), 1)

    def test_no_move_suggestions_when_filtering_for_customer(self) -> None:
        output = self.run_agent(
            "2026-11-16", "2026-11-22", profile=self.onsite_profile(), context="kunde-acme"
        )
        self.assertNotIn("Verschiebevorschläge", output)

    def test_filters_by_context(self) -> None:
        output = self.run_agent("2026-11-16", "2026-11-22", profile=PROFILE, context="kunde-acme")
        self.assertIn("Review", output)
        self.assertNotIn("Standup", output)
        self.assertNotIn("Urlaub", output)

    def test_rejects_unknown_context(self) -> None:
        output = self.run_agent("2026-11-16", "2026-11-22", profile=PROFILE, context="privat")
        self.assertTrue(output.startswith("FEHLER:"), output)
        self.assertIn("kunde-acme", output)
        self.assertEqual(self.calls, [])

    def test_instructions_describe_profile(self) -> None:
        seen: list[str] = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.append(info.instructions or "")
            return ModelResponse(parts=[TextPart("ok")])

        deps = CalendarDeps(today=date(2026, 11, 14), profile=PROFILE, fetch=self.fetch)
        calendar_agent.run_sync("Frage", model=FunctionModel(respond), deps=deps)
        self.assertIn("- kunde-acme: Kunde ACME", seen[0])

    def test_instructions_name_next_week(self) -> None:
        seen: list[str] = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.append(info.instructions or "")
            return ModelResponse(parts=[TextPart("ok")])

        deps = CalendarDeps(today=date(2026, 11, 14), fetch=self.fetch)
        calendar_agent.run_sync("Frage", model=FunctionModel(respond), deps=deps)
        self.assertIn("Heute ist Sa, 2026-11-14", seen[0])
        self.assertIn("von 2026-11-16 bis 2026-11-22", seen[0])


if __name__ == "__main__":
    unittest.main()

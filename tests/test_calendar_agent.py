"""Tests des Kalender-Agenten ohne Ollama und ohne echten Kalender."""

import unittest
from datetime import date, datetime

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents.calendar_agent import MAX_RANGE_DAYS, CalendarDeps, calendar_agent
from local_agents.calendar_events import Event, format_events, next_week

# Kein Test darf versehentlich ein echtes Modell aufrufen.
models.ALLOW_MODEL_REQUESTS = False

EVENTS = [
    Event(datetime(2026, 11, 17, 9, 0), datetime(2026, 11, 17, 10, 0), "Standup", location="Teams"),
    Event(datetime(2026, 11, 16, 0, 0), datetime(2026, 11, 16, 23, 59), "Urlaub", all_day=True),
    Event(datetime(2026, 11, 16, 14, 0), datetime(2026, 11, 16, 15, 30), "Review"),
]


def scripted_model(start: str, end: str) -> FunctionModel:
    """Modell, das list_events(start, end) aufruft und das Ergebnis wiedergibt."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for part in messages[-1].parts:
            if part.part_kind == "tool-return":
                label = "FEHLER" if part.outcome == "retried" else "TERMINE"
                return ModelResponse(parts=[TextPart(f"{label}: {part.content}")])
        return ModelResponse(parts=[ToolCallPart("list_events", {"start": start, "end": end})])

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

    def run_agent(self, start: str, end: str) -> str:
        deps = CalendarDeps(today=date(2026, 11, 14), fetch=self.fetch)
        return calendar_agent.run_sync("Frage", model=scripted_model(start, end), deps=deps).output

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

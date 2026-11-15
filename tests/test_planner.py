"""Tests fuer den Planungs-Agenten und den Aufgaben-Befehl."""

import contextlib
import io
import unittest
from datetime import date, datetime, timedelta

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents import tasks as tasks_cli
from local_agents.calendar_events import Event
from local_agents.planner_agent import PlannerDeps, plan_events, run
from local_agents.profile import Context, Profile
from local_agents.store import CHANGED, DISCARDED, DONE, NEW, OPEN, UNCHANGED, Store

models.ALLOW_MODEL_REQUESTS = False

PROFILE = Profile(
    contexts=(
        Context("intern", "intern", "Intern", domains=frozenset({"beispiel.de"})),
        Context("kunde-acme", "kunde", "Kunde ACME", domains=frozenset({"acme.example"})),
    ),
    plan_skip=("firmenrunde",),
)
START, END = date(2026, 11, 16), date(2026, 11, 22)


def meeting(day: int, clock: str, title: str, who: str = "beispiel.de", uid: str = "") -> Event:
    hour, minute = map(int, clock.split(":"))
    start = datetime(2026, 11, day, hour, minute)
    return Event(
        start,
        start + timedelta(minutes=30),
        title,
        organizer_domain=who,
        attendee_domains=(who, who),
        uid=uid or title,
    )


DAILY = meeting(16, "09:00", "Daily", who="acme.example")
WEEK = [
    DAILY,
    meeting(18, "10:00", "Jour fixe"),
    meeting(18, "12:00", "Firmenrunde"),
    Event(datetime(2026, 11, 19), datetime(2026, 11, 19, 23), "Urlaub", all_day=True),
]


class TestPlanEvents(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def plan(self, events: list[Event], profile: Profile = PROFILE):
        return plan_events(events, profile, self.store, START, END)

    def test_task_is_due_the_day_before_the_meeting(self) -> None:
        plan = self.plan(WEEK)
        daily = plan.tasks[0]
        self.assertEqual(daily.title, "Vorbereitung: Daily (Mo 16.11. 09:00)")
        self.assertEqual((daily.due_date, daily.context, daily.status), (date(2026, 11, 15), "kunde-acme", OPEN))

    def test_skips_all_day_and_excluded_meetings(self) -> None:
        plan = self.plan(WEEK)
        self.assertEqual([task.title.split(" (")[0] for task in plan.tasks], ["Vorbereitung: Daily", "Vorbereitung: Jour fixe"])
        self.assertEqual(plan.counts[NEW], 2)

    def test_lead_days_from_profile(self) -> None:
        import dataclasses

        plan = self.plan([DAILY], dataclasses.replace(PROFILE, lead_days=3))
        self.assertEqual(plan.tasks[0].due_date, date(2026, 11, 13))

    def test_second_run_creates_no_duplicates(self) -> None:
        self.plan(WEEK)
        plan = self.plan(WEEK)
        self.assertEqual((plan.counts[NEW], plan.counts[UNCHANGED], plan.discarded), (0, 2, 0))
        self.assertEqual(len(self.store.tasks()), 2)

    def test_series_instances_get_separate_tasks(self) -> None:
        series = [meeting(day, "09:00", "Daily", uid="serie") for day in (16, 18, 20)]
        self.assertEqual(len(self.plan(series).tasks), 3)

    def test_duplicate_calendar_entries_get_one_task(self) -> None:
        twice = [meeting(17, "16:30", "Sync", uid="a"), meeting(17, "16:30", "Sync", uid="b")]
        self.assertEqual(len(self.plan(twice).tasks), 1)

    def test_cancelled_meeting_discards_its_open_task(self) -> None:
        self.plan(WEEK)
        plan = self.plan([DAILY])
        self.assertEqual(plan.discarded, 1)
        statuses = {task.title.split(" (")[0]: task.status for task in self.store.tasks()}
        self.assertEqual(statuses, {"Vorbereitung: Daily": OPEN, "Vorbereitung: Jour fixe": DISCARDED})
        self.assertEqual(len(plan.tasks), 1)

    def test_renamed_meeting_updates_task_and_keeps_status(self) -> None:
        self.plan([meeting(16, "09:00", "Daily", uid="x")])
        self.store.set_status(self.store.tasks()[0].id, DONE)
        plan = self.plan([meeting(16, "09:00", "Daily neu", uid="x")])
        self.assertEqual(plan.counts[CHANGED], 1)
        self.assertEqual((plan.tasks[0].title, plan.tasks[0].status), ("Vorbereitung: Daily neu (Mo 16.11. 09:00)", DONE))


def scripted_model(call_tool: bool = True) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for part in messages[-1].parts:
            if part.part_kind == "tool-return":
                return ModelResponse(parts=[TextPart(f"GEPLANT: {part.content}")])
        if not call_tool:
            return ModelResponse(parts=[TextPart("Ich plane nichts.")])
        return ModelResponse(parts=[ToolCallPart("plan_week", {"start": "2026-11-16", "end": "2026-11-22"})])

    return FunctionModel(respond)


class TestPlannerAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.deps = PlannerDeps(
            today=date(2026, 11, 14), store=self.store, profile=PROFILE, fetch=lambda start, end: WEEK
        )

    def test_writes_tasks_and_appends_list(self) -> None:
        output = run("Frage", self.deps, scripted_model())
        self.assertTrue(
            output.startswith(
                "GEPLANT: Aufgaben in der Datenbank: 2 neu, 0 geändert, 0 unverändert, 0 verworfen. "
                "Faellig je Tag: So 15.11.: 1, Di 17.11.: 1."
            ),
            output,
        )
        self.assertIn("\n\n**Aufgaben für 16.11.–22.11.2026 (2 neu, 0 geändert, 0 unverändert, 0 verworfen)**\n", output)
        self.assertIn("- fertig bis So 15.11.2026:\n  - #1 Vorbereitung: Daily (Mo 16.11. 09:00) [Kunde ACME]", output)
        self.assertEqual(len(self.store.tasks()), 2)

    def test_plans_next_week_even_if_model_skips_the_tool(self) -> None:
        output = run("Frage", self.deps, scripted_model(call_tool=False))
        self.assertTrue(output.startswith("Ich plane nichts.\n\n**Aufgaben für 16.11.–22.11.2026"), output)
        self.assertEqual(len(self.store.tasks()), 2)

    def test_logs_the_run(self) -> None:
        run("Frage", self.deps, scripted_model())
        (logged,) = self.store.runs()
        self.assertEqual((logged["agent"], logged["status"]), ("planner", "ok"))
        self.assertIn("2 neu", logged["summary"])

    def test_logs_failed_run(self) -> None:
        def broken(start: date, end: date) -> list[Event]:
            raise RuntimeError("kaputt")

        self.deps.fetch = broken
        with self.assertRaises(RuntimeError):
            run("Frage", self.deps, scripted_model())
        self.assertEqual(self.store.runs()[0]["status"], "fehler")


class TestTasksCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        plan_events(WEEK, PROFILE, self.store, START, END)

    def call(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = tasks_cli.main(["tasks", *args], store=self.store, profile=PROFILE)
        return code, out.getvalue()

    def test_lists_open_tasks_by_due_date(self) -> None:
        code, text = self.call()
        self.assertEqual(code, 0)
        self.assertEqual(
            text,
            "fertig bis So 15.11.2026\n"
            "  #1 Vorbereitung: Daily (Mo 16.11. 09:00) [Kunde ACME]\n"
            "fertig bis Di 17.11.2026\n"
            "  #2 Vorbereitung: Jour fixe (Mi 18.11. 10:00) [Intern]\n",
        )

    def test_done_task_leaves_the_open_list_but_stays_in_all(self) -> None:
        self.assertEqual(self.call("erledigt", "#1"), (0, "Aufgabe #1: erledigt.\n"))
        self.assertNotIn("#1", self.call()[1])
        self.assertIn("#1 Vorbereitung: Daily (Mo 16.11. 09:00) [Kunde ACME, erledigt]", self.call("alle")[1])
        self.call("offen", "1")
        self.assertIn("#1", self.call()[1])

    def test_unknown_task_and_bad_arguments(self) -> None:
        self.assertEqual(self.call("erledigt", "99")[0], 1)
        self.assertEqual(self.call("loeschen", "1")[0], 2)


if __name__ == "__main__":
    unittest.main()

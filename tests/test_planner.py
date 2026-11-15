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
from local_agents.planner_agent import PlannerDeps, briefing, plan_events, run
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


ONSITE = Profile(
    contexts=(
        PROFILE.contexts[0],
        Context(
            "kunde-acme",
            "kunde",
            "Kunde ACME",
            domains=frozenset({"acme.example"}),
            onsite_days=frozenset({2}),
        ),
    )
)


def internal(day: int, clock: str, title: str, **extra) -> Event:
    import dataclasses

    return dataclasses.replace(meeting(day, clock, title), **extra)


class TestMoveTasks(unittest.TestCase):
    """Interne Termine am Kundentag (Mittwoch) bekommen eine Nachricht-Aufgabe."""

    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def plan(self, events: list[Event], profile: Profile = ONSITE, today: date | None = None):
        return plan_events(events, profile, self.store, START, END, today)

    def moves(self, plan) -> list:
        return [task for task in plan.tasks if task.kind == "verschieben"]

    def test_message_to_organizer_is_due_days_ahead(self) -> None:
        plan = self.plan([internal(18, "10:00", "Jour fixe", organizer="Erika Muster"), DAILY])
        (move,) = self.moves(plan)
        self.assertEqual(
            move.title,
            "Nachricht an Erika Muster: Jour fixe (Mi 18.11. 10:00) verschieben, "
            "weil Kundentag Kunde ACME",
        )
        self.assertEqual((move.due_date, move.context), (date(2026, 11, 15), "intern"))
        # Die Vorbereitung bleibt daneben bestehen, der Kundentermin bekommt nur sie.
        self.assertEqual(len(plan.tasks), 3)

    def test_buffer_from_profile_and_not_before_today(self) -> None:
        import dataclasses

        event = internal(18, "10:00", "Jour fixe")
        plan = self.plan([event], dataclasses.replace(ONSITE, move_lead_days=5))
        self.assertEqual(self.moves(plan)[0].due_date, date(2026, 11, 13))
        plan = self.plan([event], dataclasses.replace(ONSITE, move_lead_days=5), today=date(2026, 11, 15))
        self.assertEqual(self.moves(plan)[0].due_date, date(2026, 11, 15))

    def test_own_meeting_unknown_organizer_and_large_round(self) -> None:
        many = tuple(["beispiel.de"] * 12)
        plan = self.plan(
            [
                internal(18, "09:00", "Eigener", organizer="Ich", organizer_is_me=True),
                internal(18, "10:00", "Ohne Namen"),
                internal(18, "11:00", "Alle", organizer="Erika Muster", attendee_domains=many),
            ]
        )
        self.assertEqual(
            [task.title for task in self.moves(plan)],
            [
                "Selbst verschieben: Eigener (Mi 18.11. 09:00), weil Kundentag Kunde ACME",
                "Nachricht an Organisator: Ohne Namen (Mi 18.11. 10:00) verschieben, "
                "weil Kundentag Kunde ACME",
                "Absage an Erika Muster: Alle (Mi 18.11. 11:00), weil Kundentag Kunde ACME "
                "(große Runde mit 12 Eingeladenen)",
            ],
        )

    def test_block_under_stress_gets_move_tasks(self) -> None:
        import dataclasses

        stressed = dataclasses.replace(
            ONSITE, contexts=(ONSITE.contexts[0], dataclasses.replace(ONSITE.contexts[1], stress="hoch", onsite_days=frozenset()))
        )
        long = internal(17, "10:00", "Workshop", end=datetime(2026, 11, 17, 11, 30))
        (move,) = self.moves(self.plan([long], stressed))
        self.assertEqual(
            move.title,
            "Nachricht an Organisator: Workshop (Di 17.11. 10:00) verschieben, "
            "weil interner Block 10:00-11:30, Stresslevel hoch bei Kunde ACME",
        )
        self.assertEqual(move.due_date, date(2026, 11, 14))

    def test_task_is_discarded_and_reopened_with_the_rule(self) -> None:
        event = internal(18, "10:00", "Jour fixe")
        self.plan([event])
        plan = self.plan([event], PROFILE)  # ohne Kundentag
        self.assertEqual((plan.discarded, self.moves(plan)), (1, []))
        plan = self.plan([event])
        self.assertEqual((plan.counts[CHANGED], len(self.moves(plan))), (1, 1))
        self.assertEqual(self.moves(plan)[0].status, OPEN)

    def test_second_run_changes_nothing(self) -> None:
        events = [internal(18, "10:00", "Jour fixe", organizer="Erika Muster")]
        self.plan(events, today=date(2026, 11, 14))
        plan = self.plan(events, today=date(2026, 11, 14))
        self.assertEqual((plan.counts[UNCHANGED], plan.counts[NEW], plan.discarded), (2, 0, 0))
        self.assertEqual(self.store.events(START, END)[0].organizer, "Erika Muster")


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

    def test_reports_move_tasks_to_the_model(self) -> None:
        self.deps.profile = ONSITE
        output = run("Frage", self.deps, scripted_model())
        self.assertIn("Davon 2 Nachrichten, um interne Termine zu verschieben.", output)
        self.assertIn("  - #3 Nachricht an Organisator: Jour fixe (Mi 18.11. 10:00) verschieben", output)

    def test_briefing_covers_open_tasks_of_the_planned_week(self) -> None:
        self.deps.profile = ONSITE
        run("Frage", self.deps, scripted_model())
        self.store.set_status(1, "erledigt")
        self.deps.today = date(2026, 11, 15)
        text = briefing(self.deps)
        self.assertTrue(
            text.startswith(
                "Für heute sind zwei Nachrichten eingeplant, an den Organisator, damit die "
                "internen Termine am Mittwoch verschoben werden."
            ),
            text,
        )
        self.assertNotIn("Montag und", text)  # die erledigte Vorbereitung fuers Daily fehlt

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

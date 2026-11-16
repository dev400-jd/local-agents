"""Tests fuer den Kalenderabgleich: verschobene, abgesagte und neue Termine."""

import dataclasses
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from local_agents import sync
from local_agents.calendar_events import CalendarAccessError, Event
from local_agents.planner_agent import plan_events
from local_agents.profile import Context, Profile
from local_agents.store import DISCARDED, DONE, OPEN, REJECTED, Store

PROFILE = Profile(
    contexts=(Context("intern", "intern", "Intern", domains=frozenset({"beispiel.de"})),)
)
START, END = date(2026, 11, 16), date(2026, 11, 22)
NOW = datetime(2026, 11, 16, 8, 0)


def meeting(day: int, clock: str, title: str, uid: str = "", **extra) -> Event:
    hour, minute = map(int, clock.split(":"))
    start = datetime(2026, 11, 1, hour, minute) + timedelta(days=day - 1)
    return Event(
        start,
        start + timedelta(minutes=30),
        title,
        organizer_domain="beispiel.de",
        attendee_domains=("beispiel.de", "beispiel.de"),
        uid=uid or title,
        **extra,
    )


def series(day: int, clock: str, title: str, uid: str) -> Event:
    event = meeting(day, clock, title, uid, recurring=True)
    return dataclasses.replace(event, occurrence=event.start)


class SyncCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def plan(self, events: list[Event]) -> None:
        """Der Planungs-Agent hat die Woche 16.-22.11. geplant."""
        plan_events(events, PROFILE, self.store, START, END)
        self.store.mark_planned(START, END)

    def sync(self, events: list[Event], now: datetime = NOW):
        return sync.sync(self.store, PROFILE, now, lambda start, end: events)

    def task(self, number: int = 1):
        return next(task for task in self.store.tasks() if task.id == number)


class TestMovedMeetings(SyncCase):
    def test_single_meeting_keeps_task_status_and_brief(self) -> None:
        self.plan([meeting(17, "10:00", "Review", uid="r")])
        self.store.set_status(1, DONE)
        self.store.save_brief(1, "Review", "review", "weil", ("Folien",))
        plan = self.sync([meeting(19, "14:00", "Review", uid="r")])
        task = self.task()
        self.assertEqual(len(self.store.tasks()), 1)
        self.assertEqual((task.status, task.due_date), (DONE, date(2026, 11, 18)))
        self.assertEqual(task.title, "Vorbereitung: Review (Do 19.11. 14:00)")
        self.assertIsNotNone(self.store.brief(1))
        self.assertEqual(
            [(move.old_start.day, move.new_start.day) for move in plan.moves], [(17, 19)]
        )
        self.assertEqual(plan.discarded, 0)

    def test_rejected_task_stays_rejected_after_the_move(self) -> None:
        self.plan([meeting(17, "10:00", "Review", uid="r")])
        self.store.set_status(1, REJECTED)
        self.sync([meeting(18, "10:00", "Review", uid="r")])
        self.assertEqual([task.status for task in self.store.tasks()], [REJECTED])

    def test_meeting_moved_beyond_the_planned_week(self) -> None:
        self.plan([meeting(17, "10:00", "Review", uid="r")])
        self.sync([meeting(30, "10:00", "Review", uid="r"), meeting(30, "12:00", "Fremd")])
        # Die Aufgabe wandert mit; der fremde Termin der ungeplanten Woche bekommt keine.
        self.assertEqual(
            [(task.due_date, task.status) for task in self.store.tasks()],
            [(date(2026, 11, 29), OPEN)],
        )
        # Auch der naechste Abgleich findet ihn dort wieder.
        plan = self.sync([meeting(30, "10:00", "Review", uid="r"), meeting(30, "12:00", "Fremd")])
        self.assertEqual((len(self.store.tasks()), plan.moves, plan.discarded), (1, (), 0))

    def test_moved_occurrence_of_a_series_gets_its_own_calendar_id(self) -> None:
        days = [series(day, "09:00", "Daily", "serie") for day in (16, 17, 18)]
        self.plan(days)
        moved = meeting(17, "15:00", "Daily", uid="einzeln", detached=True)
        moved = dataclasses.replace(moved, occurrence=datetime(2026, 11, 17, 9, 0))
        plan = self.sync([days[0], moved, days[2]])
        self.assertEqual(len(self.store.tasks()), 3)
        self.assertEqual(self.task(2).title, "Vorbereitung: Daily (Di 17.11. 15:00)")
        self.assertEqual((len(plan.moves), plan.discarded), (1, 0))
        # Noch einmal verschoben: Kennung und Ursprung bleiben, die Aufgabe auch.
        again = dataclasses.replace(
            moved, start=datetime(2026, 11, 18, 16, 0), end=datetime(2026, 11, 18, 16, 30)
        )
        self.sync([days[0], again, days[2]])
        self.assertEqual(len(self.store.tasks()), 3)
        self.assertEqual(self.task(2).title, "Vorbereitung: Daily (Mi 18.11. 16:00)")

    def test_whole_series_at_a_new_time(self) -> None:
        self.plan([series(day, "09:00", "Daily", "serie") for day in (17, 18)])
        self.store.set_status(2, DONE)
        later = [series(day, "09:30", "Daily", "serie") for day in (17, 18, 24)]
        plan = self.sync(later)
        self.assertEqual(
            [(task.title, task.status) for task in self.store.tasks()],
            [
                ("Vorbereitung: Daily (Di 17.11. 09:30)", OPEN),
                ("Vorbereitung: Daily (Mi 18.11. 09:30)", DONE),
            ],
        )
        self.assertEqual(len(plan.moves), 2)

    def test_cancelled_occurrence_is_not_mistaken_for_next_weeks(self) -> None:
        self.plan([series(17, "09:00", "Daily", "serie")])
        plan = self.sync([series(24, "09:00", "Daily", "serie")])
        self.assertEqual([task.status for task in self.store.tasks()], [DISCARDED])
        self.assertEqual((plan.moves, plan.discarded), ((), 1))


class TestSync(SyncCase):
    def test_cancelled_meeting_discards_the_open_task(self) -> None:
        self.plan([meeting(17, "10:00", "Review"), meeting(18, "10:00", "Jour fixe")])
        plan = self.sync([meeting(18, "10:00", "Jour fixe")])
        self.assertEqual([task.status for task in self.store.tasks()], [DISCARDED, OPEN])
        self.assertEqual(plan.discarded, 1)

    def test_new_meeting_gets_a_task_only_on_planned_days_and_before_it_starts(self) -> None:
        self.plan([meeting(18, "10:00", "Jour fixe")])
        events = [
            meeting(16, "07:00", "Schon vorbei"),
            meeting(18, "10:00", "Jour fixe"),
            meeting(19, "11:00", "Neu"),
            meeting(24, "11:00", "Ungeplante Woche"),
        ]
        self.sync(events)
        self.assertEqual(
            [task.title.split(" (")[0] for task in self.store.tasks()],
            ["Vorbereitung: Jour fixe", "Vorbereitung: Neu"],
        )

    def test_past_meetings_are_left_alone(self) -> None:
        self.plan([meeting(16, "09:00", "Daily"), meeting(18, "10:00", "Jour fixe")])
        self.sync([meeting(18, "10:00", "Jour fixe")], now=datetime(2026, 11, 17, 8, 0))
        self.assertEqual([task.status for task in self.store.tasks()], [OPEN, OPEN])

    def test_nothing_planned_reads_no_calendar(self) -> None:
        def fail(start: date, end: date) -> list[Event]:
            raise AssertionError("Kalender gelesen")

        self.assertIsNone(sync.sync(self.store, PROFILE, NOW, fail))

    def test_empty_calendar_discards_nothing(self) -> None:
        self.plan([meeting(17, "10:00", "Review")])
        with self.assertRaises(sync.SyncSkipped):
            self.sync([])
        self.assertEqual([task.status for task in self.store.tasks()], [OPEN])

    def test_old_database_counts_weeks_of_stored_events_as_planned(self) -> None:
        self.assertEqual(self.store.planned_days(START), set())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agents.db"
            old = Store(path)
            plan_events([meeting(17, "10:00", "Review")], PROFILE, old, START, END)
            old.db.execute("DROP TABLE planned_days")
            old.db.commit()
            old.close()
            reopened = Store(path)
            self.addCleanup(reopened.close)
            self.assertEqual(
                reopened.planned_days(START), {START + timedelta(days=n) for n in range(7)}
            )


class TestRefresh(SyncCase):
    def refresh(self, fetch, profile: Profile = PROFILE) -> list[str]:
        printed: list[str] = []
        sync.refresh(self.store, profile, NOW, fetch, printed.append)
        return printed

    def test_reports_changes_and_logs_the_run(self) -> None:
        self.plan([meeting(17, "10:00", "Review", uid="r"), meeting(18, "10:00", "Jour fixe")])
        printed = self.refresh(lambda start, end: [meeting(19, "14:00", "Review", uid="r")])
        self.assertEqual(
            printed[0].splitlines(),
            [
                "Kalender abgeglichen: 1 Termin verschoben, 1 Aufgabe angepasst, 1 Aufgabe verworfen.",
                "  verschoben: Review von Di 17.11. 10:00 auf Do 19.11. 14:00",
            ],
        )
        self.assertEqual(self.store.runs()[0]["agent"], "abgleich")

    def test_is_silent_without_changes(self) -> None:
        events = [meeting(17, "10:00", "Review")]
        self.plan(events)
        self.assertEqual(self.refresh(lambda start, end: events), [])
        self.assertEqual(len(self.store.runs()), 0)

    def test_unreadable_calendar_keeps_the_stored_state(self) -> None:
        self.plan([meeting(17, "10:00", "Review")])

        def broken(start: date, end: date) -> list[Event]:
            raise CalendarAccessError("Kein Zugriff auf den Kalender.")

        printed = self.refresh(broken)
        self.assertIn("nicht abgeglichen", printed[0])
        self.assertEqual([task.status for task in self.store.tasks()], [OPEN])

    def test_can_be_switched_off_in_the_profile(self) -> None:
        self.plan([meeting(17, "10:00", "Review")])
        off = dataclasses.replace(PROFILE, sync_enabled=False)
        self.assertEqual(self.refresh(lambda start, end: [], off), [])
        self.assertEqual([task.status for task in self.store.tasks()], [OPEN])


if __name__ == "__main__":
    unittest.main()

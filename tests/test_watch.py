"""Tests fuer die Kalenderwache: Zeitpunkt des Abgleichs, Meldungen, Dienstbeschreibung."""

import dataclasses
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from local_agents import watch
from local_agents.calendar_events import CalendarAccessError, Event
from local_agents.planner_agent import plan_events
from local_agents.profile import Context, Profile
from local_agents.store import Store

PROFILE = Profile(
    contexts=(Context("intern", "intern", "Intern", domains=frozenset({"beispiel.de"})),)
)
START, END = date(2026, 11, 16), date(2026, 11, 22)
NOW = datetime(2026, 11, 16, 8, 0)


def meeting(day: int, clock: str, title: str, uid: str = "") -> Event:
    hour, minute = map(int, clock.split(":"))
    start = datetime(2026, 11, day, hour, minute)
    return Event(
        start,
        start + timedelta(minutes=30),
        title,
        organizer_domain="beispiel.de",
        attendee_domains=("beispiel.de", "beispiel.de"),
        uid=uid or title,
    )


class TestWatcher(unittest.TestCase):
    def setUp(self) -> None:
        self.time = 0.0
        self.runs = 0
        self.watcher = watch.Watcher(self.count, lambda: self.time, quiet=5, fallback=100)

    def count(self) -> None:
        self.runs += 1

    def at(self, moment: float) -> bool:
        self.time = moment
        return self.watcher.tick()

    def test_syncs_once_at_start(self) -> None:
        self.assertTrue(self.at(0))
        self.assertFalse(self.at(1))
        self.assertEqual(self.runs, 1)

    def test_waits_for_quiet_after_a_burst_of_changes(self) -> None:
        self.at(0)
        for moment in (10, 12, 14):
            self.time = moment
            self.watcher.changed()
            self.assertFalse(self.watcher.tick())
        self.assertFalse(self.at(18))
        self.assertTrue(self.at(19))
        self.assertFalse(self.at(30))
        self.assertEqual(self.runs, 2)

    def test_syncs_again_after_the_fallback_interval(self) -> None:
        self.at(0)
        self.assertFalse(self.at(99))
        self.assertTrue(self.at(100))
        self.assertFalse(self.at(150))
        self.assertTrue(self.at(200))


class TestReporter(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "agents.db"
        store = Store(self.path)
        plan_events([meeting(17, "10:00", "Review", uid="r")], PROFILE, store, START, END)
        store.mark_planned(START, END)
        store.close()
        self.events = [meeting(17, "10:00", "Review", uid="r")]
        self.notes: list[tuple[str, str]] = []
        self.said: list[str] = []
        self.log: list[str] = []

    def reporter(self, profile: Profile = PROFILE, fetch=None) -> watch.Reporter:
        return watch.Reporter(
            profile,
            open_store=lambda: Store(self.path),
            fetch=fetch or (lambda start, end: self.events),
            notify=lambda text, title: self.notes.append((title, text)),
            speak=lambda text, profile: self.said.append(text),
            log=self.log.append,
            now=lambda: NOW,
        )

    def test_silent_without_changes(self) -> None:
        self.reporter()()
        self.assertEqual((self.notes, self.said, self.log), ([], [], []))

    def test_moved_meeting_is_notified_and_logged(self) -> None:
        self.events = [meeting(19, "14:00", "Review", uid="r")]
        self.reporter()()
        self.assertEqual(
            self.notes,
            [
                (
                    "Kalender abgeglichen",
                    "1 Termin verschoben, 1 Aufgabe angepasst.\n"
                    "verschoben: Review von Di 17.11. 10:00 auf Do 19.11. 14:00",
                )
            ],
        )
        self.assertEqual(self.said, [])
        self.assertTrue(self.log[0].startswith("16.11. 08:00 Kalender abgeglichen: 1 Termin verschoben"))
        store = Store(self.path)
        self.addCleanup(store.close)
        self.assertEqual(store.tasks()[0].title, "Vorbereitung: Review (Do 19.11. 14:00)")

    def test_speaks_only_when_the_profile_says_so(self) -> None:
        self.events = [meeting(19, "14:00", "Review", uid="r")]
        self.reporter(dataclasses.replace(PROFILE, watch_speak=True, watch_notify=False))()
        self.assertEqual(self.notes, [])
        self.assertEqual(
            self.said,
            [
                "Dein Kalender hat sich geändert: 1 Termin verschoben, 1 Aufgabe angepasst. "
                "Verschoben: Review von Di 17.11. 10:00 auf Do 19.11. 14:00."
            ],
        )

    def test_unreadable_calendar_is_notified_once(self) -> None:
        def broken(start: date, end: date) -> list[Event]:
            raise CalendarAccessError("Kein Zugriff auf den Kalender.")

        reporter = self.reporter(fetch=broken)
        reporter()
        reporter()
        self.assertEqual(len(self.notes), 1)
        self.assertEqual(self.notes[0][0], "Kalenderwache")
        self.assertEqual(len(self.log), 2)

    def test_locked_database_is_skipped(self) -> None:
        def locked() -> Store:
            raise sqlite3.OperationalError("database is locked")

        reporter = self.reporter()
        reporter.open_store = locked
        reporter()
        self.assertIn("übersprungen", self.log[0])


class TestLaunchAgent(unittest.TestCase):
    def test_plist_runs_the_watcher_with_this_python(self) -> None:
        plist = watch.agent_plist("/pfad/.venv/bin/python", Path("/pfad"), {"LOCAL_AGENTS_DB": "/x.db", "HOME": "/h"})
        self.assertEqual(plist["Label"], "local-agents.watch")
        self.assertEqual(plist["ProgramArguments"], ["/pfad/.venv/bin/python", "-m", "local_agents.watch"])
        self.assertEqual(plist["WorkingDirectory"], "/pfad")
        self.assertEqual(plist["EnvironmentVariables"], {"LOCAL_AGENTS_DB": "/x.db"})
        self.assertEqual(plist["KeepAlive"], {"SuccessfulExit": False})

    def test_no_environment_block_without_overrides(self) -> None:
        self.assertNotIn("EnvironmentVariables", watch.agent_plist("python", Path("/pfad"), {}))


if __name__ == "__main__":
    unittest.main()

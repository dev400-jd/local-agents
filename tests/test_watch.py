"""Tests fuer die Kalenderwache: Zeitpunkt des Abgleichs, Meldungen, Dienstbeschreibung."""

import dataclasses
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from local_agents import watch
from local_agents.asana_mirror import Mirror
from local_agents.asana_source import AsanaAccessError
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


class TestAsanaSync(unittest.TestCase):
    def setUp(self) -> None:
        self.time = 0.0
        self.logged: list[str] = []
        self.calls = 0
        self.exists = True
        self.fail = ""
        self.tasks = [{"gid": "1", "name": "Angebot prüfen", "modified_at": "2026-11-19T08:00:00.000Z"}]
        self.mirror_db = tempfile.NamedTemporaryFile(suffix=".db")
        self.addCleanup(self.mirror_db.close)
        self.asana = watch.AsanaSync(
            Profile(asana_sync_minutes=30),
            self.logged.append,
            connect=lambda: self.transport,
            clock=lambda: self.time,
            now=lambda: datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=self.time),
            exists=lambda: self.exists,
            open_mirror=lambda: Mirror(self.mirror_db.name),
        )

    def transport(self, path: str, params: dict) -> dict:
        self.calls += 1
        if self.fail:
            raise AsanaAccessError(self.fail)
        if path == "/users/me":
            return {"data": {"gid": "111", "workspaces": [{"gid": "ws"}]}}
        if path == "/tasks":
            return {"data": self.tasks}
        return {"data": []}

    def at(self, moment: float) -> bool:
        self.time = moment
        return self.asana.tick()

    def test_syncs_at_start_and_then_every_interval(self) -> None:
        self.assertTrue(self.at(0))
        self.assertEqual(len(self.logged), 1)
        self.assertTrue(self.logged[0].endswith("Asana: Voller Abgleich: 1 neu, 0 geändert, 0 unverändert, 0 entfernt."))
        self.assertFalse(self.at(1799))
        self.assertTrue(self.at(1800))
        self.assertEqual(len(self.logged), 1)  # nichts geaendert, nichts zu melden
        mirror = Mirror(self.mirror_db.name)
        self.addCleanup(mirror.close)
        self.assertEqual([task.name for task in mirror.tasks()], ["Angebot prüfen"])

    def test_no_mirror_no_sync_and_zero_switches_it_off(self) -> None:
        self.exists = False
        self.assertFalse(self.at(0))
        self.assertEqual(self.calls, 0)
        self.exists = True
        self.asana.profile = Profile(asana_sync_minutes=0)
        self.assertFalse(self.at(5000))
        self.assertEqual(self.calls, 0)

    def test_the_same_failure_is_logged_once(self) -> None:
        self.fail = "Asana nicht erreichbar: kein Netz"
        self.assertTrue(self.at(0))
        self.assertTrue(self.at(1800))
        self.assertEqual(len(self.logged), 1)
        self.assertTrue(self.logged[0].endswith("Asana nicht abgeglichen: Asana nicht erreichbar: kein Netz"))
        self.fail = ""
        self.assertTrue(self.at(3600))
        self.assertEqual(len(self.logged), 2)


class TestLaunchAgent(unittest.TestCase):
    def launchctl(self, loaded_for: int, failing: int):
        """Nachgebautes launchctl: Der Dienst bleibt nach bootout noch ``loaded_for`` Abfragen geladen,
        und die ersten ``failing`` Ladeversuche scheitern."""
        state = {"loaded": loaded_for, "failing": failing}
        calls: list[str] = []

        def run(*args: str) -> subprocess.CompletedProcess:
            calls.append(args[0])
            code = 0
            if args[0] == "print":
                code = 0 if state["loaded"] > 0 else 113
                state["loaded"] -= 1
            elif args[0] == "bootstrap" and state["failing"] > 0:
                state["failing"] -= 1
                code = 5
            return subprocess.CompletedProcess(args, code, "", "Bootstrap failed: 5: Input/output error" if code == 5 else "")

        return run, calls

    def test_restart_waits_until_the_old_service_is_gone(self) -> None:
        run, calls = self.launchctl(loaded_for=2, failing=1)
        waits: list[float] = []
        result = watch.restart(run, "gui/501", "/tmp/x.plist", sleep=waits.append)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls, ["bootout", "print", "print", "print", "bootstrap", "bootstrap"])
        self.assertEqual(waits, [0.5, 0.5, 1.0])

    def test_restart_gives_up_with_the_error_of_launchd(self) -> None:
        run, calls = self.launchctl(loaded_for=0, failing=99)
        result = watch.restart(run, "gui/501", "/tmp/x.plist", sleep=lambda seconds: None, attempts=3)
        self.assertEqual((result.returncode, calls.count("bootstrap")), (5, 4))
        self.assertIn("Input/output error", result.stderr)

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

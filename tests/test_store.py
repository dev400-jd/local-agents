"""Tests fuer die Agenten-Datenbank (SQLite im Arbeitsspeicher)."""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from local_agents.store import (
    CHANGED,
    DISCARDED,
    DONE,
    NEW,
    OPEN,
    UNCHANGED,
    Store,
    StoredEvent,
)


def event(event_id: str, day: int, title: str = "Daily") -> StoredEvent:
    return StoredEvent(
        event_id, title, datetime(2026, 11, day, 9, 0), datetime(2026, 11, day, 9, 30), "kunde-acme"
    )


class TestStore(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)

    def add(self, event_id: str, day: int, due: int) -> str:
        self.store.upsert_event(event(event_id, day))
        return self.store.upsert_task(
            event_id, "vorbereitung", f"Vorbereitung {event_id}", "kunde-acme", date(2026, 11, due), "test"
        )

    def test_new_task_is_open(self) -> None:
        self.assertEqual(self.add("a", 16, 15), NEW)
        (task,) = self.store.tasks()
        self.assertEqual((task.status, task.due_date, task.event_id), (OPEN, date(2026, 11, 15), "a"))

    def test_same_task_again_changes_nothing(self) -> None:
        self.add("a", 16, 15)
        self.assertEqual(self.add("a", 16, 15), UNCHANGED)
        self.assertEqual(len(self.store.tasks()), 1)

    def test_changed_due_date_keeps_status_and_result(self) -> None:
        self.add("a", 16, 15)
        task_id = self.store.tasks()[0].id
        self.store.set_status(task_id, DONE, result="fertig")
        self.assertEqual(self.add("a", 16, 14), CHANGED)
        (task,) = self.store.tasks()
        self.assertEqual((task.due_date, task.status, task.result), (date(2026, 11, 14), DONE, "fertig"))

    def test_tasks_are_ordered_by_due_date_and_filtered(self) -> None:
        self.add("b", 18, 17)
        self.add("a", 16, 15)
        self.assertEqual([t.event_id for t in self.store.tasks()], ["a", "b"])
        self.store.set_status(self.store.tasks()[0].id, DONE)
        self.assertEqual([t.event_id for t in self.store.tasks((OPEN,))], ["b"])
        self.assertEqual(self.store.tasks(due_to=date(2026, 11, 16))[0].event_id, "a")
        self.assertEqual(self.store.tasks(due_from=date(2026, 11, 16))[0].event_id, "b")

    def test_drop_events_discards_open_tasks_of_missing_events_only(self) -> None:
        for event_id, day in (("a", 16), ("b", 17), ("c", 18), ("spaeter", 25)):
            self.add(event_id, day, day - 1)
        self.store.set_status(self.store.tasks()[1].id, DONE)  # b ist erledigt
        discarded = self.store.drop_events(date(2026, 11, 16), date(2026, 11, 22), keep={"a"})
        self.assertEqual(discarded, 1)
        statuses = {task.title[-1]: task.status for task in self.store.tasks()}
        self.assertEqual(statuses, {"a": OPEN, "b": DONE, "c": DISCARDED, "r": OPEN})
        self.assertEqual([e.id for e in self.store.events(date(2026, 11, 1), date(2026, 11, 30))], ["a", "spaeter"])

    def test_set_status_rejects_unknown_status_and_reports_missing_task(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_status(1, "vielleicht")
        self.assertFalse(self.store.set_status(99, DONE))

    def test_runs_are_logged(self) -> None:
        run_id = self.store.start_run("planner")
        self.store.finish_run(run_id, "ok", "2 neu")
        (run,) = self.store.runs()
        self.assertEqual((run["agent"], run["status"], run["summary"]), ("planner", "ok", "2 neu"))
        self.assertIsNotNone(run["finished_at"])

    def test_file_database_persists_and_creates_folder(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "neu" / "agents.db"
        first = Store(path)
        first.upsert_event(event("a", 16))
        first.close()
        second = Store(path)
        self.addCleanup(second.close)
        self.assertEqual(len(second.events(date(2026, 11, 16), date(2026, 11, 16))), 1)


if __name__ == "__main__":
    unittest.main()

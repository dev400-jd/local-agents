"""Agenten-Datenbank: gemeinsamer Arbeitsstand aller Agenten in einer SQLite-Datei.

Drei Tabellen:

- ``events``: Termine, wie der Planungs-Agent sie gesehen hat.
- ``tasks``: Aufgaben mit Fertig-bis-Datum, Status und Ergebnis.
- ``runs``: Protokoll, welcher Agent wann lief.

Die Datei liegt ausserhalb des Repos (Termintitel, Kundennamen). Agenten laufen
nacheinander; gleichzeitige Schreiber sind nicht vorgesehen.
"""

import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "local-agents" / "agents.db"

OPEN, IN_PROGRESS, DONE, DISCARDED = "offen", "in_arbeit", "erledigt", "verworfen"
# Vom Nutzer herausgenommen. Anders als eine verworfene Aufgabe (Termin
# entfallen) legt der Planungs-Agent sie nicht wieder an.
REJECTED = "abgelehnt"
STATUSES = (OPEN, IN_PROGRESS, DONE, DISCARDED, REJECTED)
# Ergebnis von upsert_task.
NEW, CHANGED, UNCHANGED = "neu", "geaendert", "unveraendert"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,   -- Kalender-Kennung und Beginn
    title      TEXT NOT NULL,
    start      TEXT NOT NULL,      -- lokale Zeit, ISO 8601
    end        TEXT NOT NULL,
    context    TEXT NOT NULL,      -- Kontext-id aus dem Profil
    location   TEXT NOT NULL DEFAULT '',
    attendees  INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    organizer  TEXT NOT NULL DEFAULT ''   -- Anzeigename des Einladenden
);
CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY,
    event_id   TEXT REFERENCES events(id) ON DELETE SET NULL,
    kind       TEXT NOT NULL,      -- Art der Aufgabe, z. B. 'vorbereitung'
    title      TEXT NOT NULL,
    context    TEXT NOT NULL,
    due_date   TEXT NOT NULL,      -- fertig bis, ISO-Datum
    status     TEXT NOT NULL DEFAULT 'offen',
    agent      TEXT NOT NULL DEFAULT '',  -- zustaendiger Agent, leer = noch keiner
    result     TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (event_id, kind)
);
CREATE INDEX IF NOT EXISTS tasks_due ON tasks (status, due_date);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    agent       TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL DEFAULT 'laeuft',
    summary     TEXT NOT NULL DEFAULT ''
);
"""


@dataclass(frozen=True)
class StoredEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    context: str
    location: str = ""
    attendees: int = 0
    organizer: str = ""


@dataclass(frozen=True)
class Task:
    id: int
    event_id: str | None
    kind: str
    title: str
    context: str
    due_date: date
    status: str
    agent: str
    result: str


def db_path() -> Path:
    return Path(os.environ.get("LOCAL_AGENTS_DB", DEFAULT_DB_PATH))


class Store:
    """Zugriff auf die Agenten-Datenbank. ``path`` darf ":memory:" sein."""

    def __init__(self, path: Path | str | None = None) -> None:
        path = db_path() if path is None else path
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Pydantic AI fuehrt Werkzeuge in einem eigenen Thread aus; die Agenten
        # laufen aber nacheinander, also greift immer nur ein Thread zu.
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        # Datenbanken aus der Zeit vor der Spalte nachziehen.
        if "organizer" not in {row["name"] for row in self.db.execute("PRAGMA table_info(events)")}:
            self.db.execute("ALTER TABLE events ADD COLUMN organizer TEXT NOT NULL DEFAULT ''")

    def close(self) -> None:
        self.db.close()

    # --- Termine

    def upsert_event(self, event: StoredEvent) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO events (id, title, start, end, context, location, attendees, "
                "organizer, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET title = excluded.title, start = excluded.start, "
                "end = excluded.end, context = excluded.context, location = excluded.location, "
                "attendees = excluded.attendees, organizer = excluded.organizer, "
                "updated_at = excluded.updated_at",
                (
                    event.id,
                    event.title,
                    event.start.isoformat(),
                    event.end.isoformat(),
                    event.context,
                    event.location,
                    event.attendees,
                    event.organizer,
                    _now(),
                ),
            )

    def events(self, start: date, end: date) -> list[StoredEvent]:
        """Gespeicherte Termine, die von ``start`` bis einschliesslich ``end`` beginnen."""
        rows = self.db.execute(
            "SELECT * FROM events WHERE date(start) BETWEEN ? AND ? ORDER BY start, title",
            (start.isoformat(), end.isoformat()),
        )
        return [
            StoredEvent(
                row["id"],
                row["title"],
                datetime.fromisoformat(row["start"]),
                datetime.fromisoformat(row["end"]),
                row["context"],
                row["location"],
                row["attendees"],
                row["organizer"],
            )
            for row in rows
        ]

    def drop_events(self, start: date, end: date, keep: set[str]) -> int:
        """Entfernt Termine des Zeitraums, die es nicht mehr gibt (abgesagt, verschoben).

        Ihre offenen Aufgaben werden verworfen; erledigte bleiben stehen.
        Gibt die Zahl der verworfenen Aufgaben zurueck.
        """
        gone = [event.id for event in self.events(start, end) if event.id not in keep]
        discarded = 0
        with self.db:
            for event_id in gone:
                discarded += self.db.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? "
                    "WHERE event_id = ? AND status IN (?, ?)",
                    (DISCARDED, _now(), event_id, OPEN, IN_PROGRESS),
                ).rowcount
                self.db.execute("DELETE FROM events WHERE id = ?", (event_id,))
        return discarded

    # --- Aufgaben

    def upsert_task(
        self, event_id: str, kind: str, title: str, context: str, due_date: date, created_by: str
    ) -> str:
        """Legt die Aufgabe einer Art zu einem Termin an oder gleicht sie ab.

        Status und Ergebnis bleiben erhalten; nur eine verworfene Aufgabe wird
        wieder geoeffnet. Gibt NEW, CHANGED oder UNCHANGED zurueck.
        """
        row = self.db.execute(
            "SELECT * FROM tasks WHERE event_id = ? AND kind = ?", (event_id, kind)
        ).fetchone()
        now = _now()
        with self.db:
            if row is None:
                self.db.execute(
                    "INSERT INTO tasks (event_id, kind, title, context, due_date, created_by, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (event_id, kind, title, context, due_date.isoformat(), created_by, now, now),
                )
                return NEW
            current = (row["title"], row["context"], row["due_date"])
            status = OPEN if row["status"] == DISCARDED else row["status"]
            if current == (title, context, due_date.isoformat()) and status == row["status"]:
                return UNCHANGED
            self.db.execute(
                "UPDATE tasks SET title = ?, context = ?, due_date = ?, status = ?, updated_at = ? "
                "WHERE id = ?",
                (title, context, due_date.isoformat(), status, now, row["id"]),
            )
            return CHANGED

    def add_task(self, kind: str, title: str, context: str, due_date: date, created_by: str) -> int:
        """Legt eine Aufgabe ohne Termin an (etwa vom Nutzer ergaenzt) und gibt ihre Nummer zurueck."""
        now = _now()
        with self.db:
            return self.db.execute(
                "INSERT INTO tasks (kind, title, context, due_date, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, title, context, due_date.isoformat(), created_by, now, now),
            ).lastrowid

    def discard_task(self, event_id: str, kind: str) -> bool:
        """Verwirft die noch nicht erledigte Aufgabe einer Art zu einem Termin."""
        with self.db:
            cursor = self.db.execute(
                "UPDATE tasks SET status = ?, updated_at = ? "
                "WHERE event_id = ? AND kind = ? AND status IN (?, ?)",
                (DISCARDED, _now(), event_id, kind, OPEN, IN_PROGRESS),
            )
        return cursor.rowcount == 1

    def tasks(
        self,
        statuses: tuple[str, ...] | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
    ) -> list[Task]:
        """Aufgaben nach Fertig-bis-Datum, optional nach Status und Zeitraum gefiltert."""
        where, args = ["1 = 1"], []
        if statuses:
            where.append(f"tasks.status IN ({', '.join('?' * len(statuses))})")
            args.extend(statuses)
        if due_from:
            where.append("tasks.due_date >= ?")
            args.append(due_from.isoformat())
        if due_to:
            where.append("tasks.due_date <= ?")
            args.append(due_to.isoformat())
        rows = self.db.execute(
            "SELECT tasks.* FROM tasks LEFT JOIN events ON events.id = tasks.event_id "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY tasks.due_date, events.start, tasks.id",
            args,
        )
        return [
            Task(
                row["id"],
                row["event_id"],
                row["kind"],
                row["title"],
                row["context"],
                date.fromisoformat(row["due_date"]),
                row["status"],
                row["agent"],
                row["result"],
            )
            for row in rows
        ]

    def set_status(self, task_id: int, status: str, result: str | None = None) -> bool:
        """Setzt den Status einer Aufgabe; False, wenn es sie nicht gibt."""
        if status not in STATUSES:
            raise ValueError(f"Unbekannter Status {status!r}. Erlaubt: {', '.join(STATUSES)}.")
        with self.db:
            cursor = self.db.execute(
                "UPDATE tasks SET status = ?, result = COALESCE(?, result), updated_at = ? WHERE id = ?",
                (status, result, _now(), task_id),
            )
        return cursor.rowcount == 1

    # --- Laeufe

    def start_run(self, agent: str) -> int:
        with self.db:
            return self.db.execute(
                "INSERT INTO runs (agent, started_at) VALUES (?, ?)", (agent, _now())
            ).lastrowid

    def finish_run(self, run_id: int, status: str, summary: str = "") -> None:
        with self.db:
            self.db.execute(
                "UPDATE runs SET finished_at = ?, status = ?, summary = ? WHERE id = ?",
                (_now(), status, summary, run_id),
            )

    def runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

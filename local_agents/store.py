"""Agenten-Datenbank: gemeinsamer Arbeitsstand aller Agenten in einer SQLite-Datei.

Sieben Tabellen:

- ``events``: Termine, wie der Planungs-Agent sie gesehen hat.
- ``planned_days``: Tage, die schon geplant wurden; nur dort legt der
  Kalenderabgleich fuer neue Termine Aufgaben an.
- ``tasks``: Aufgaben mit Fertig-bis-Datum, Status und Ergebnis.
- ``briefs``: Einordnung des Termins hinter einer Aufgabe (Termin-Agent).
- ``story_notes``: Bemerkungen des Nutzers zu User Stories, je Daily (Scrum Master).
- ``preferences``: Vorgaben des Nutzers fuer einzelne Agenten, aus seinen Korrekturen.
- ``runs``: Protokoll, welcher Agent wann lief.

Die Datei liegt ausserhalb des Repos (Termintitel, Kundennamen). Agenten laufen
nacheinander; gleichzeitige Schreiber sind nicht vorgesehen.
"""

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "local-agents" / "agents.db"

OPEN, IN_PROGRESS, DONE, DISCARDED = "offen", "in_arbeit", "erledigt", "verworfen"
# Vom Nutzer herausgenommen. Anders als eine verworfene Aufgabe (Termin
# entfallen) legt der Planungs-Agent sie nicht wieder an.
REJECTED = "abgelehnt"
STATUSES = (OPEN, IN_PROGRESS, DONE, DISCARDED, REJECTED)
# Ergebnis von upsert_task.
NEW, CHANGED, UNCHANGED = "neu", "geaendert", "unveraendert"

# Status einer Bemerkung zu einer Story.
NOTE_OPEN, NOTE_DONE, NOTE_REMOVED = "offen", "besprochen", "entfernt"
NOTE_STATUSES = (NOTE_OPEN, NOTE_DONE, NOTE_REMOVED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         TEXT PRIMARY KEY,   -- Kalender-Kennung und urspruenglicher Beginn
    title      TEXT NOT NULL,
    start      TEXT NOT NULL,      -- lokale Zeit, ISO 8601
    end        TEXT NOT NULL,
    context    TEXT NOT NULL,      -- Kontext-id aus dem Profil
    location   TEXT NOT NULL DEFAULT '',
    attendees  INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    organizer  TEXT NOT NULL DEFAULT ''   -- Anzeigename des Einladenden
);
CREATE TABLE IF NOT EXISTS planned_days (
    day TEXT PRIMARY KEY               -- ISO-Datum
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
CREATE TABLE IF NOT EXISTS briefs (
    task_id      INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,      -- Termintitel, fuer den die Einordnung gilt
    meeting_type TEXT NOT NULL,      -- id aus meeting_types.py
    reason       TEXT NOT NULL DEFAULT '',
    deliverables TEXT NOT NULL,      -- JSON-Liste: Ergebnisse, die dem Nutzer helfen
    role         TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS story_notes (
    id         INTEGER PRIMARY KEY,
    story      TEXT NOT NULL DEFAULT '',  -- Name der Story, leer = nicht zugeordnet
    feature    TEXT NOT NULL DEFAULT '',
    text       TEXT NOT NULL,             -- die Bemerkung
    daily_date TEXT NOT NULL,             -- fuer welches Daily sie wichtig ist; leer = ohne Daily-Bezug
    status     TEXT NOT NULL DEFAULT 'offen',
    raw        TEXT NOT NULL DEFAULT '',  -- Wortlaut der Antwort des Nutzers
    source     TEXT NOT NULL DEFAULT '',  -- Kanal: terminal, dialog, ...
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS story_notes_daily ON story_notes (daily_date, status);
CREATE TABLE IF NOT EXISTS preferences (
    id         INTEGER PRIMARY KEY,
    agent      TEXT NOT NULL,             -- fuer welchen Agenten die Vorgabe gilt
    text       TEXT NOT NULL,             -- die Vorgabe in den Worten des Nutzers
    created_at TEXT NOT NULL
);
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


@dataclass(frozen=True)
class Brief:
    """Was der Termin-Agent ueber den Termin hinter einer Aufgabe herausgefunden hat."""

    task_id: int
    title: str
    meeting_type: str
    reason: str
    deliverables: tuple[str, ...]
    role: str = ""


@dataclass(frozen=True)
class StoryNote:
    """Eine Bemerkung des Nutzers zu einer Story, auf Wunsch fuer ein bestimmtes Daily."""

    id: int
    story: str
    feature: str
    text: str
    daily_date: date | None  # None = Vermerk ohne Daily-Bezug
    status: str
    raw: str = ""
    source: str = ""
    created_at: str = ""


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
        # Vor der Tabelle planned_days wurde wochenweise geplant: Die Wochen der
        # gespeicherten Termine gelten als geplant.
        if self.db.execute("SELECT 1 FROM planned_days LIMIT 1").fetchone() is None:
            for row in self.db.execute("SELECT DISTINCT date(start) AS day FROM events").fetchall():
                day = date.fromisoformat(row["day"])
                monday = day - timedelta(days=day.weekday())
                self.mark_planned(monday, monday + timedelta(days=6))

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

    def rekey_event(self, old_id: str, new_id: str) -> None:
        """Gibt einem Termin eine neue Kennung; seine Aufgaben wandern mit."""
        with self.db:
            self.db.execute(
                "INSERT INTO events (id, title, start, end, context, location, attendees, "
                "organizer, updated_at) SELECT ?, title, start, end, context, location, "
                "attendees, organizer, updated_at FROM events WHERE id = ?",
                (new_id, old_id),
            )
            self.db.execute("UPDATE tasks SET event_id = ? WHERE event_id = ?", (new_id, old_id))
            self.db.execute("DELETE FROM events WHERE id = ?", (old_id,))

    def mark_planned(self, start: date, end: date) -> None:
        """Merkt die Tage von ``start`` bis einschliesslich ``end`` als geplant."""
        days = [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO planned_days (day) VALUES (?)", [(d,) for d in days])

    def planned_days(self, start: date) -> set[date]:
        """Geplante Tage ab ``start``."""
        rows = self.db.execute("SELECT day FROM planned_days WHERE day >= ?", (start.isoformat(),))
        return {date.fromisoformat(row["day"]) for row in rows}

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

    def discard_started(self, now: datetime) -> int:
        """Verwirft unerledigte Aufgaben, deren Termin schon begonnen hat; gibt ihre Zahl zurueck."""
        with self.db:
            return self.db.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE status IN (?, ?) "
                "AND event_id IN (SELECT id FROM events WHERE start <= ?)",
                (DISCARDED, _now(), OPEN, IN_PROGRESS, now.isoformat()),
            ).rowcount

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

    # --- Einordnungen

    def save_brief(
        self,
        task_id: int,
        title: str,
        meeting_type: str,
        reason: str,
        deliverables: tuple[str, ...],
        role: str = "",
    ) -> Brief:
        """Speichert die Einordnung zu einer Aufgabe; eine vorhandene wird ersetzt."""
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO briefs (task_id, title, meeting_type, reason, deliverables, "
                "role, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, title, meeting_type, reason, json.dumps(list(deliverables), ensure_ascii=False), role, _now()),
            )
        return Brief(task_id, title, meeting_type, reason, tuple(deliverables), role)

    def brief(self, task_id: int) -> Brief | None:
        row = self.db.execute("SELECT * FROM briefs WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return Brief(
            row["task_id"],
            row["title"],
            row["meeting_type"],
            row["reason"],
            tuple(json.loads(row["deliverables"])),
            row["role"],
        )

    # --- Bemerkungen zu Stories

    def add_story_note(
        self,
        story: str,
        feature: str,
        text: str,
        daily_date: date | None = None,
        raw: str = "",
        source: str = "",
    ) -> StoryNote:
        now = _now()
        with self.db:
            note_id = self.db.execute(
                "INSERT INTO story_notes (story, feature, text, daily_date, raw, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (story, feature, text, daily_date.isoformat() if daily_date else "", raw, source, now),
            ).lastrowid
        return StoryNote(note_id, story, feature, text, daily_date, NOTE_OPEN, raw, source, now)

    def story_notes(
        self, statuses: tuple[str, ...] | None = None, daily_date: date | None = None
    ) -> list[StoryNote]:
        """Bemerkungen, die ohne Daily-Bezug zuerst, dann nach Daily-Datum.

        Optional nach Status und Tag gefiltert.
        """
        where, args = ["1 = 1"], []
        if statuses:
            where.append(f"status IN ({', '.join('?' * len(statuses))})")
            args.extend(statuses)
        if daily_date:
            where.append("daily_date = ?")
            args.append(daily_date.isoformat())
        rows = self.db.execute(
            f"SELECT * FROM story_notes WHERE {' AND '.join(where)} ORDER BY daily_date, id", args
        )
        return [
            StoryNote(
                row["id"],
                row["story"],
                row["feature"],
                row["text"],
                date.fromisoformat(row["daily_date"]) if row["daily_date"] else None,
                row["status"],
                row["raw"],
                row["source"],
                row["created_at"],
            )
            for row in rows
        ]

    def set_note_status(self, note_id: int, status: str) -> bool:
        if status not in NOTE_STATUSES:
            raise ValueError(f"Unbekannter Status {status!r}.")
        with self.db:
            cursor = self.db.execute(
                "UPDATE story_notes SET status = ? WHERE id = ?", (status, note_id)
            )
        return cursor.rowcount == 1

    # --- Vorgaben des Nutzers

    def add_preference(self, agent: str, text: str) -> int:
        """Merkt eine Vorgabe des Nutzers fuer kuenftige Laeufe eines Agenten."""
        with self.db:
            return self.db.execute(
                "INSERT INTO preferences (agent, text, created_at) VALUES (?, ?, ?)",
                (agent, " ".join(text.split()), _now()),
            ).lastrowid

    def preferences(self, agent: str, limit: int = 5) -> list[str]:
        """Die juengsten Vorgaben fuer einen Agenten, aelteste zuerst."""
        rows = self.db.execute(
            "SELECT text FROM preferences WHERE agent = ? ORDER BY id DESC LIMIT ?", (agent, limit)
        ).fetchall()
        return [row["text"] for row in reversed(rows)]

    def remove_preference(self, agent: str, number: int) -> bool:
        """Entfernt die Vorgabe mit dieser Nummer (wie von ``preference_rows`` gezeigt)."""
        with self.db:
            return self.db.execute(
                "DELETE FROM preferences WHERE agent = ? AND id = ?", (agent, number)
            ).rowcount == 1

    def preference_rows(self, agent: str) -> list[tuple[int, str]]:
        """Alle Vorgaben fuer einen Agenten mit ihrer Nummer."""
        rows = self.db.execute("SELECT id, text FROM preferences WHERE agent = ? ORDER BY id", (agent,))
        return [(row["id"], row["text"]) for row in rows]

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

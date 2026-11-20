"""Lokaler Spiegel der eigenen Asana-Aufgaben (nur lesend).

Aufruf:
  python -m local_agents.asana_mirror          abgleichen: nur, was sich seit dem letzten Mal geaendert hat
  python -m local_agents.asana_mirror voll     alles neu lesen, Verschwundenes austragen
  python -m local_agents.asana_mirror status   Bestand und Alter des Spiegels, ohne Asana zu fragen

Gespiegelt werden:
- Aufgaben, die dir zugewiesen sind: alle offenen und die in den letzten 90
  Tagen erledigten,
- Aufgaben, denen du folgst und die in den letzten 90 Tagen geaendert wurden
  (braucht die Asana-Suche, die es nur in bezahlten Plaenen gibt; sonst
  entfaellt dieser Teil mit einem Hinweis),
- je Aufgabe: Beschreibung, Faelligkeit, Zustaendige, Projekte und Abschnitte,
  benutzerdefinierte Felder und die letzten Kommentare.

Der Spiegel ist eine eigene SQLite-Datei neben der Agenten-Datenbank. Er ist
abgeleitet: Loeschen schadet nicht, ``voll`` baut ihn neu. Er enthaelt Inhalte
aus dem Asana deines Arbeitgebers im Klartext und gehoert nicht ins Repo.

Geschrieben wird nach Asana nichts: Dieses Modul kennt nur lesende Abfragen.
Suche und Gespraechs-Agent lesen den Spiegel, nicht Asana; sie sind so aktuell
wie der letzte Abgleich.
"""

import json
import os
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from local_agents.asana_source import (  # noqa: F401  (Transport wird von watch.py mitbenutzt)
    PAGE_SIZE,
    AsanaAccessError,
    Transport,
    asana_token,
    http_transport,
    plain_text,
)
from local_agents.store import db_path

COMPLETED_DAYS = 90  # so lange bleiben erledigte Aufgaben im Spiegel
FOLLOWED_DAYS = 90
MAX_PAGES = 50  # Obergrenze je Abfrage: 5000 Aufgaben
MAX_COMMENTS = 30  # je Aufgabe, die neuesten
MAX_COMMENT_CHARS = 1500
FULL_EVERY = timedelta(days=7)  # so oft traegt ein Abgleich von selbst Verschwundenes aus
PROGRESS_EVERY = 25

TASK_FIELDS = (
    "name,notes,completed,completed_at,due_on,due_at,assignee.name,modified_at,created_at,"
    "permalink_url,parent.name,memberships.project.name,memberships.section.name,"
    "custom_fields.name,custom_fields.display_value,num_subtasks"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    gid          TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    completed    INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT NOT NULL DEFAULT '',
    due          TEXT NOT NULL DEFAULT '',   -- ISO-Datum oder Zeitpunkt
    assignee     TEXT NOT NULL DEFAULT '',   -- Anzeigename
    mine         INTEGER NOT NULL DEFAULT 0, -- mir zugewiesen
    followed     INTEGER NOT NULL DEFAULT 0, -- ich folge ihr
    parent       TEXT NOT NULL DEFAULT '',   -- Name der uebergeordneten Aufgabe
    subtasks     INTEGER NOT NULL DEFAULT 0,
    projects     TEXT NOT NULL DEFAULT '[]', -- JSON: [[Projekt, Abschnitt], ...]
    fields       TEXT NOT NULL DEFAULT '{}', -- JSON: Feldname -> Anzeigewert
    url          TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT '',
    modified_at  TEXT NOT NULL DEFAULT '',
    seen_at      TEXT NOT NULL DEFAULT ''    -- letzter Abgleich, der sie gesehen hat
);
CREATE TABLE IF NOT EXISTS comments (
    gid        TEXT PRIMARY KEY,
    task_gid   TEXT NOT NULL REFERENCES tasks(gid) ON DELETE CASCADE,
    author     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    text       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS comments_task ON comments (task_gid, created_at);
CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Comment:
    author: str
    created_at: datetime
    text: str


@dataclass(frozen=True)
class MirroredTask:
    """Eine Asana-Aufgabe, wie sie im Spiegel steht."""

    gid: str
    name: str
    notes: str = ""
    completed: bool = False
    due: str = ""
    assignee: str = ""
    mine: bool = False
    followed: bool = False
    parent: str = ""
    subtasks: int = 0
    projects: tuple[tuple[str, str], ...] = ()  # (Projekt, Abschnitt)
    fields: tuple[tuple[str, str], ...] = ()  # (Feldname, Anzeigewert)
    url: str = ""
    modified_at: str = ""
    comments: tuple[Comment, ...] = ()


@dataclass
class SyncResult:
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    removed: int = 0
    comments: int = 0  # Aufgaben, deren Kommentare gelesen wurden
    full: bool = False
    notes: list[str] = field(default_factory=list)


def mirror_path() -> Path:
    return Path(os.environ.get("LOCAL_AGENTS_ASANA_DB", db_path().with_name("asana.db")))


class Mirror:
    """Zugriff auf den Spiegel. ``path`` darf ":memory:" sein."""

    def __init__(self, path: Path | str | None = None) -> None:
        path = mirror_path() if path is None else path
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # --- Stand des Abgleichs

    def get(self, key: str) -> str:
        row = self.db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else ""

    def set(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def last_sync(self) -> datetime | None:
        stamp = self.get("last_sync")
        return datetime.fromisoformat(stamp) if stamp else None

    # --- Schreiben (nur in den Spiegel)

    def upsert(self, task: dict, mine: bool, followed: bool, seen_at: str) -> str:
        """Traegt eine Aufgabe aus der API-Antwort ein; gibt "neu", "geaendert" oder "gleich" zurueck."""
        row = self.db.execute("SELECT modified_at, mine, followed FROM tasks WHERE gid = ?", (task["gid"],)).fetchone()
        projects = [
            [(m.get("project") or {}).get("name") or "", (m.get("section") or {}).get("name") or ""]
            for m in task.get("memberships") or []
            if (m.get("project") or {}).get("name")
        ]
        fields = {
            f["name"]: f["display_value"]
            for f in task.get("custom_fields") or []
            if f.get("name") and f.get("display_value")
        }
        values = (
            task.get("name") or "(ohne Titel)",
            task.get("notes") or "",
            int(bool(task.get("completed"))),
            task.get("completed_at") or "",
            task.get("due_at") or task.get("due_on") or "",
            (task.get("assignee") or {}).get("name") or "",
            int(mine or bool(row and row["mine"])),
            int(followed or bool(row and row["followed"])),
            (task.get("parent") or {}).get("name") or "",
            int(task.get("num_subtasks") or 0),
            json.dumps(projects, ensure_ascii=False),
            json.dumps(fields, ensure_ascii=False, sort_keys=True),
            task.get("permalink_url") or "",
            task.get("created_at") or "",
            task.get("modified_at") or "",
            seen_at,
            task["gid"],
        )
        with self.db:
            if row is None:
                self.db.execute(
                    "INSERT INTO tasks (name, notes, completed, completed_at, due, assignee, mine, followed, parent, "
                    "subtasks, projects, fields, url, created_at, modified_at, seen_at, gid) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    values,
                )
                return "neu"
            self.db.execute(
                "UPDATE tasks SET name = ?, notes = ?, completed = ?, completed_at = ?, due = ?, assignee = ?, "
                "mine = ?, followed = ?, parent = ?, subtasks = ?, projects = ?, fields = ?, url = ?, "
                "created_at = ?, modified_at = ?, seen_at = ? WHERE gid = ?",
                values,
            )
        return "gleich" if row["modified_at"] == (task.get("modified_at") or "") else "geaendert"

    def replace_comments(self, task_gid: str, stories: list[dict]) -> None:
        comments = [story for story in stories if story.get("resource_subtype") == "comment_added"]
        comments = sorted(comments, key=lambda story: story["created_at"])[-MAX_COMMENTS:]
        with self.db:
            self.db.execute("DELETE FROM comments WHERE task_gid = ?", (task_gid,))
            self.db.executemany(
                "INSERT OR REPLACE INTO comments (gid, task_gid, author, created_at, text) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        story["gid"],
                        task_gid,
                        (story.get("created_by") or {}).get("name") or "unbekannt",
                        story["created_at"],
                        _comment_text(story),
                    )
                    for story in comments
                ],
            )

    def remove_unseen(self, seen_at: str) -> int:
        """Entfernt Aufgaben, die der volle Abgleich nicht mehr gesehen hat."""
        with self.db:
            return self.db.execute("DELETE FROM tasks WHERE seen_at <> ?", (seen_at,)).rowcount

    # --- Lesen

    def tasks(self) -> list[MirroredTask]:
        """Alle gespiegelten Aufgaben mit ihren Kommentaren, offene zuerst, dann nach Faelligkeit."""
        comments: dict[str, list[Comment]] = {}
        for row in self.db.execute("SELECT * FROM comments ORDER BY created_at"):
            comments.setdefault(row["task_gid"], []).append(
                Comment(row["author"], _parse(row["created_at"]), row["text"])
            )
        rows = self.db.execute("SELECT * FROM tasks ORDER BY completed, due = '', due, name")
        return [
            MirroredTask(
                row["gid"],
                row["name"],
                row["notes"],
                bool(row["completed"]),
                row["due"],
                row["assignee"],
                bool(row["mine"]),
                bool(row["followed"]),
                row["parent"],
                row["subtasks"],
                tuple((project, section) for project, section in json.loads(row["projects"])),
                tuple(json.loads(row["fields"]).items()),
                row["url"],
                row["modified_at"],
                tuple(comments.get(row["gid"], ())),
            )
            for row in rows
        ]

    def counts(self) -> dict[str, int]:
        row = self.db.execute(
            "SELECT count(*) AS total, coalesce(sum(completed = 0), 0) AS open, coalesce(sum(mine), 0) AS mine, "
            "coalesce(sum(followed), 0) AS followed FROM tasks"
        ).fetchone()
        comments = self.db.execute("SELECT count(*) FROM comments").fetchone()[0]
        projects = {project for task in self.tasks() for project, _ in task.projects}
        return {
            "aufgaben": row["total"],
            "offen": row["open"],
            "zugewiesen": row["mine"],
            "gefolgt": row["followed"],
            "kommentare": comments,
            "projekte": len(projects),
        }


def _comment_text(story: dict) -> str:
    text = story.get("text") or plain_text(story.get("html_text") or "")
    text = text.strip()
    return text if len(text) <= MAX_COMMENT_CHARS else text[: MAX_COMMENT_CHARS - 1] + "…"


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# --- Abgleich


def _pages(transport: Transport, path: str, params: dict[str, str]) -> list[dict]:
    """Alle Seiten einer Abfrage, die mit ``offset`` blaettert."""
    found: list[dict] = []
    params = {**params, "limit": str(PAGE_SIZE)}
    for _ in range(MAX_PAGES):
        page = transport(path, params)
        found += page["data"]
        offset = (page.get("next_page") or {}).get("offset")
        if not offset:
            break
        params = {**params, "offset": offset}
    return found


def _followed(transport: Transport, workspace: str, user: str, since: datetime) -> list[dict]:
    """Aufgaben, denen der Nutzer folgt, geaendert seit ``since``. Die Suche blaettert ueber das Aenderungsdatum."""
    params = {
        "followers.any": user,
        "modified_at.after": _iso(since),
        "sort_by": "modified_at",
        "limit": str(PAGE_SIZE),
        "opt_fields": TASK_FIELDS,
    }
    tasks: dict[str, dict] = {}
    for _ in range(MAX_PAGES):
        batch = transport(f"/workspaces/{workspace}/tasks/search", params)["data"]
        new = [task for task in batch if task["gid"] not in tasks]
        tasks.update((task["gid"], task) for task in new)
        if len(batch) < PAGE_SIZE or not new:
            break
        params = {**params, "modified_at.before": min(task["modified_at"] for task in batch)}
    return list(tasks.values())


def sync(
    mirror: Mirror,
    transport: Transport,
    now: datetime,
    full: bool = False,
    out: Callable[[str], None] | None = None,
) -> SyncResult:
    """Gleicht den Spiegel mit Asana ab. ``now`` braucht eine Zeitzone.

    Ein voller Abgleich liest alles neu und traegt aus, was es nicht mehr gibt
    oder was nicht mehr zu dir gehoert. Sonst wird nur gelesen, was sich seit
    dem letzten Abgleich geaendert hat. Nach ``FULL_EVERY`` ist der naechste
    Abgleich von selbst ein voller.
    """
    last, last_full = mirror.last_sync(), mirror.get("last_full")
    full = full or last is None or not last_full or now - datetime.fromisoformat(last_full) >= FULL_EVERY
    result = SyncResult(full=full)
    seen_at = _iso(now)
    me = transport("/users/me", {"opt_fields": "name,workspaces.name"})["data"]
    # Etwas Ueberlappung, damit nichts zwischen zwei Abgleichen durchrutscht.
    since = None if full else last - timedelta(minutes=5)
    touched: dict[str, dict] = {}
    for workspace in me.get("workspaces", []):
        params = {
            "assignee": me["gid"],
            "workspace": workspace["gid"],
            "completed_since": _iso(now - timedelta(days=COMPLETED_DAYS)),
            "opt_fields": TASK_FIELDS,
        }
        if since is not None:
            params["modified_since"] = _iso(since)
        for task in _pages(transport, "/tasks", params):
            touched[task["gid"]] = {"task": task, "mine": True, "followed": False}
        try:
            followed_since = since or now - timedelta(days=FOLLOWED_DAYS)
            for task in _followed(transport, workspace["gid"], me["gid"], followed_since):
                touched.setdefault(task["gid"], {"task": task, "mine": False, "followed": False})["followed"] = True
        except AsanaAccessError as exc:
            if "402" not in str(exc):
                raise
            note = "Gefolgte Aufgaben fehlen: Die Asana-Suche gibt es nur in bezahlten Plänen."
            if note not in result.notes:
                result.notes.append(note)

    for number, entry in enumerate(touched.values(), start=1):
        task = entry["task"]
        outcome = mirror.upsert(task, entry["mine"], entry["followed"], seen_at)
        if outcome == "neu":
            result.new += 1
        elif outcome == "geaendert":
            result.changed += 1
        else:
            result.unchanged += 1
            # Ob ein neuer Kommentar das Aenderungsdatum der Aufgabe verschiebt, laesst
            # Asana offen. Der volle Abgleich liest deshalb die Kommentare aller offenen
            # Aufgaben neu, der kurze nur die der geaenderten.
            if not full or task.get("completed"):
                continue
        stories = _pages(
            transport,
            f"/tasks/{task['gid']}/stories",
            {"opt_fields": "text,html_text,created_at,created_by.name,resource_subtype"},
        )
        mirror.replace_comments(task["gid"], stories)
        result.comments += 1
        if out is not None and number % PROGRESS_EVERY == 0:
            out(f"  {number} von {len(touched)} Aufgaben …")
    if full:
        result.removed = mirror.remove_unseen(seen_at)
        mirror.set("last_full", now.isoformat())
    mirror.set("last_sync", now.isoformat())
    return result


# --- Bedienung


def result_line(result: SyncResult) -> str:
    kind = "Voller Abgleich" if result.full else "Abgleich"
    parts = [f"{result.new} neu", f"{result.changed} geändert"]
    if result.full:
        parts += [f"{result.unchanged} unverändert", f"{result.removed} entfernt"]
    return f"{kind}: {', '.join(parts)}."


def age_text(last: datetime | None, now: datetime) -> str:
    if last is None:
        return "noch nie abgeglichen"
    minutes = int((now - last).total_seconds() // 60)
    if minutes < 60:
        return f"vor {minutes} Min abgeglichen"
    if minutes < 48 * 60:
        return f"vor {minutes // 60} Std abgeglichen"
    return f"vor {minutes // (24 * 60)} Tagen abgeglichen"


def status_line(mirror: Mirror, now: datetime) -> str:
    counts = mirror.counts()
    return (
        f"Asana-Spiegel: {counts['aufgaben']} Aufgaben ({counts['offen']} offen; {counts['zugewiesen']} dir "
        f"zugewiesen, {counts['gefolgt']} gefolgt) aus {counts['projekte']} Projekten, "
        f"{counts['kommentare']} Kommentare; {age_text(mirror.last_sync(), now)}."
    )


def main(argv: list[str]) -> int:
    args = argv[1:]
    if len(args) > 1 or (args and args[0] not in ("voll", "status")):
        print(__doc__, file=sys.stderr)
        return 2
    mirror = Mirror()
    now = datetime.now().astimezone()
    try:
        if args == ["status"]:
            print(status_line(mirror, now))
            return 0
        try:
            result = sync(mirror, http_transport(asana_token()), now, full=args == ["voll"], out=print)
        except AsanaAccessError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(result_line(result))
        for note in result.notes:
            print(note)
        print(status_line(mirror, now))
        return 0
    finally:
        mirror.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))

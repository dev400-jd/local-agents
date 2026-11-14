"""Erwaehnungen des Nutzers aus Asana lesen (REST-API, nur lesend).

Asana hat keinen Filter "wo wurde ich erwaehnt". Der Weg hier:

1. Aufgaben suchen, denen der Nutzer folgt und die im Zeitraum geaendert
   wurden. Wer in der Asana-Oberflaeche erwaehnt wird, wird Mitbearbeiter der
   Aufgabe und folgt ihr damit.
2. Je Aufgabe die Kommentare des Zeitraums und die Beschreibung auf eine
   Erwaehnung pruefen. Im Rich-Text ist das ein Link mit
   ``data-asana-type="user"`` und der eigenen ``data-asana-gid``.

Die Suche gibt es nur in bezahlten Asana-Plaenen. Das Token kommt aus der
Umgebungsvariable ASANA_ACCESS_TOKEN oder aus dem macOS-Schluesselbund.
"""

import html
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

API_URL = "https://app.asana.com/api/1.0"
TOKEN_ENV = "ASANA_ACCESS_TOKEN"
KEYCHAIN_SERVICE = "local-agents-asana"
PAGE_SIZE = 100
# Obergrenzen, damit ein Lauf ueberschaubar bleibt (ein Abruf je Aufgabe).
MAX_SEARCH_PAGES = 5
MAX_EXCERPT_CHARS = 240

# Antwort der API als geparstes JSON: Pfad und Parameter rein, Rumpf raus.
Transport = Callable[[str, dict[str, str]], dict]


class AsanaAccessError(RuntimeError):
    """Asana ist nicht erreichbar oder der Zugriff ist nicht eingerichtet."""


@dataclass(frozen=True)
class Mention:
    """Eine Erwaehnung des Nutzers in einem Kommentar oder einer Beschreibung."""

    when: datetime
    author: str
    task: str
    kind: str  # "Kommentar" oder "Beschreibung"
    excerpt: str
    url: str = ""
    projects: tuple[str, ...] = ()
    completed: bool = False


def asana_token() -> str:
    """Liest das Personal Access Token aus Umgebung oder Schluesselbund."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    raise AsanaAccessError(
        "Kein Asana-Token gefunden. Token im Schluesselbund ablegen mit:\n"
        f"  security add-generic-password -s {KEYCHAIN_SERVICE} -a \"$USER\" -w\n"
        f"oder die Umgebungsvariable {TOKEN_ENV} setzen. Einrichtung: README."
    )


def http_transport(token: str) -> Transport:
    """Transport, der die Asana-API mit dem Token abfragt."""

    def get(path: str, params: dict[str, str]) -> dict:
        url = f"{API_URL}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 2:
                    time.sleep(min(int(exc.headers.get("Retry-After", "5")), 60))
                    continue
                raise AsanaAccessError(_explain(exc.code, path)) from exc
            except urllib.error.URLError as exc:
                raise AsanaAccessError(f"Asana nicht erreichbar: {exc.reason}") from exc
        raise AsanaAccessError("Asana drosselt die Anfragen; spaeter erneut versuchen.")

    return get


def _explain(status: int, path: str) -> str:
    reasons = {
        401: "Asana lehnt das Token ab (abgelaufen oder falsch).",
        402: "Die Asana-Suche gibt es nur in bezahlten Plaenen.",
        403: "Das Token darf darauf nicht zugreifen.",
        429: "Asana drosselt die Anfragen; spaeter erneut versuchen.",
    }
    return f"{reasons.get(status, 'Asana meldet einen Fehler.')} (HTTP {status} bei {path})"


def fetch_mentions(since: datetime, transport: Transport) -> list[Mention]:
    """Alle Erwaehnungen des Token-Inhabers seit ``since`` (mit Zeitzone), neueste zuerst."""
    me = transport("/users/me", {"opt_fields": "name,workspaces.name"})["data"]
    mentions: list[Mention] = []
    for workspace in me.get("workspaces", []):
        for task in _followed_tasks(workspace["gid"], me["gid"], since, transport):
            mentions.extend(_mentions_in_task(task, me["gid"], since, transport))
    return sorted(mentions, key=lambda mention: mention.when, reverse=True)


def _followed_tasks(workspace: str, user: str, since: datetime, transport: Transport) -> list[dict]:
    """Aufgaben, denen der Nutzer folgt und die seit ``since`` geaendert wurden."""
    params = {
        "followers.any": user,
        "modified_at.after": _iso(since),
        "sort_by": "modified_at",
        "limit": str(PAGE_SIZE),
        "opt_fields": "name,html_notes,permalink_url,projects.name,modified_at,"
        "created_at,created_by.name,completed",
    }
    tasks: dict[str, dict] = {}
    for _ in range(MAX_SEARCH_PAGES):
        batch = transport(f"/workspaces/{workspace}/tasks/search", params)["data"]
        new = [task for task in batch if task["gid"] not in tasks]
        tasks.update((task["gid"], task) for task in new)
        if len(batch) < PAGE_SIZE or not new:
            break
        # Die Suche kennt kein Blaettern: mit dem aeltesten Treffer weitersuchen.
        params = {**params, "modified_at.before": min(task["modified_at"] for task in batch)}
    return list(tasks.values())


def _mentions_in_task(task: dict, user: str, since: datetime, transport: Transport) -> list[Mention]:
    common = {
        "task": task.get("name") or "(ohne Titel)",
        "url": task.get("permalink_url") or "",
        "projects": tuple(project["name"] for project in task.get("projects") or []),
        "completed": bool(task.get("completed")),
    }
    found = []
    created = _parse(task["created_at"])
    # Eine Erwaehnung in der Beschreibung hat kein eigenes Datum; sie zaehlt,
    # wenn die Aufgabe im Zeitraum angelegt wurde.
    if created >= since and mentions_user(task.get("html_notes") or "", user):
        author = (task.get("created_by") or {}).get("name") or "unbekannt"
        excerpt = plain_text(task.get("html_notes") or "")
        found.append(Mention(created, author, kind="Beschreibung", excerpt=excerpt, **common))

    params = {
        "created_after": _iso(since),
        "limit": str(PAGE_SIZE),
        "opt_fields": "html_text,created_at,created_by.name,resource_subtype",
    }
    seen = set()
    while True:
        page = transport(f"/tasks/{task['gid']}/stories", params)
        for story in page["data"]:
            if story["gid"] in seen or story.get("resource_subtype") != "comment_added":
                continue
            seen.add(story["gid"])
            if mentions_user(story.get("html_text") or "", user):
                author = (story.get("created_by") or {}).get("name") or "unbekannt"
                excerpt = plain_text(story.get("html_text") or "")
                found.append(
                    Mention(_parse(story["created_at"]), author, kind="Kommentar", excerpt=excerpt, **common)
                )
        offset = (page.get("next_page") or {}).get("offset")
        if not offset:
            return found
        params = {**params, "offset": offset}


def mentions_user(rich_text: str, user: str) -> bool:
    """Ob der Rich-Text einen Erwaehnungs-Link auf die Nutzer-GID enthaelt."""
    for tag in re.findall(r"<a\b[^>]*>", rich_text):
        if 'data-asana-type="user"' in tag and f'data-asana-gid="{user}"' in tag:
            return True
    return False


def plain_text(rich_text: str) -> str:
    """Rich-Text ohne Tags, auf eine Zeile gebracht und gekuerzt."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", rich_text))
    text = " ".join(text.split())
    return text if len(text) <= MAX_EXCERPT_CHARS else text[: MAX_EXCERPT_CHARS - 1] + "…"


def _iso(moment: datetime) -> str:
    return moment.astimezone().isoformat(timespec="seconds")


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))

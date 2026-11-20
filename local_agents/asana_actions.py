"""Asana steuern: eine Aktion je Satz, mit Plan, Freigabe, Kontrolle und Protokoll.

Aufruf:
  python -m local_agents.asana_actions "Leg die Aufgabe Folien bauen bis Freitag an"
  python -m local_agents.asana_actions "Schließe die Aufgabe Folien bauen ab"
  python -m local_agents.asana_actions "Setz Angebot prüfen auf nächsten Dienstag"
  python -m local_agents.asana_actions "Verschiebe Angebot prüfen in den Abschnitt In Arbeit"
  python -m local_agents.asana_actions rueckgaengig     die letzte Aktion zuruecknehmen
  python -m local_agents.asana_actions protokoll        was der Agent in Asana geaendert hat

Geschrieben wird nur in Projekte, die im Profil unter ``[asana]`` als
``write_projects`` stehen. Ohne diesen Eintrag schreibt der Agent nichts.

Der Ablauf je Satz:

1. Uebersetzen (Modell): Aus dem Satz wird eine Aktion aus einem festen
   Katalog: anlegen, abschliessen, wieder oeffnen, Faelligkeit setzen, in einen
   Abschnitt verschieben, umbenennen, Unteraufgabe anlegen, Abschnitt anlegen. Das Modell sieht
   dabei keine Inhalte aus Asana, nur deinen Satz und die Namen der Abschnitte.
2. Aufloesen (Code): Der Code liest das Projekt frisch aus Asana, sucht die
   gemeinte Aufgabe und den Abschnitt und rechnet das Datum. Passt nichts oder
   passt mehreres, fragt er nach, statt zu raten.
3. Plan und Freigabe: Du siehst, was sich aendert (alt -> neu), und gibst es
   frei. Ohne Terminal wird nie ausgefuehrt.
4. Ausfuehren und kontrollieren (Code): Vor jeder Anfrage prueft der Code, dass
   sie nur das erlaubte Projekt trifft und im Katalog steht. Danach liest er
   die Aufgabe zurueck und vergleicht.
5. Protokoll: Jede Aenderung steht mit dem vorherigen Wert in der
   Agenten-Datenbank; ``rueckgaengig`` nimmt die letzte zurueck.

Dazu "Beschreibung setzen": Der Agent sucht im Vault das passende Dokument
(Protokoll, Recap) und laesst daraus eine Beschreibung fuer die Aufgabe
entwerfen (asana_describe.py). Du siehst den ganzen Text vor der Freigabe.

Was es nicht gibt: Kommentare, Zuweisungen an andere, Loeschen (ausser beim
Zuruecknehmen einer eben angelegten Aufgabe), Aenderungen ausserhalb der
erlaubten Projekte, mehrere Aktionen in einem Satz.
"""

import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from datetime import time as clock_time
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior

from local_agents import asana_describe, asana_mirror
from local_agents.agent import MODEL_NAME, MODEL_SETTINGS, ollama_model, structured
from local_agents.asana_describe import Drafter, NoDescription
from local_agents.asana_source import API_URL, AsanaAccessError, Transport, _explain, asana_token, http_transport
from local_agents.asana_tasks import _day, _one_day
from local_agents.asana_tasks import _words as _all_words
from local_agents.profile import Profile, ProfileError, load_profile
from local_agents.store import db_path

AGENT_NAME = "asana_aktion"
NEW_SECTION = "abschnitt_anlegen"
DESCRIBE = "beschreibung"
CREATE, COMPLETE, REOPEN, DUE, SECTION, RENAME, SUBTASK, UNCLEAR = (
    "anlegen",
    "abschliessen",
    "wieder_oeffnen",
    "faelligkeit",
    "abschnitt",
    "umbenennen",
    "unteraufgabe",
    "unklar",
)
LABELS = {
    CREATE: "Aufgabe anlegen",
    COMPLETE: "Aufgabe abschließen",
    REOPEN: "Aufgabe wieder öffnen",
    DUE: "Fälligkeit ändern",
    SECTION: "In Abschnitt verschieben",
    RENAME: "Aufgabe umbenennen",
    SUBTASK: "Unteraufgabe anlegen",
    NEW_SECTION: "Abschnitt anlegen",
    DESCRIBE: "Beschreibung setzen",
}
# Eine Zeitangabe am Ende eines Titels ("Folien bauen bis Freitag"): Sie gehoert zur Faelligkeit.
_DAY = r"(?:heute|morgen|übermorgen|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|\d{1,2}\.\s?\d{1,2}\.(?:\d{4})?)"
TRAILING_DATE = re.compile(rf"\s+(?:bis|am|für|zum|auf)\s+(?:zum\s+|den\s+|nächsten\s+|kommenden\s+)?({_DAY})$", re.IGNORECASE)
NO_DATE_WORDS = re.compile(r"\b(keine?|ohne|entfern\w*|lösch\w*|streich\w*|weg)\b")
TASK_FIELDS = "name,notes,completed,due_on,due_at,memberships.project.gid,memberships.section.gid,memberships.section.name,parent.gid"
MAX_TITLE_CHARS = 200
MAX_CANDIDATES = 8

# Worte, die eine Aufgabe oder einen Abschnitt nicht benennen ("den Brainstorm-Task", "im neuen Abschnitt").
FILLER = frozenset(
    "den dem des ein eine einen einem einer mein meine meinen task tasks aufgabe aufgaben ticket todo "
    "abschnitt abschnitts spalte neue neuen neuer neues bitte mal".split()
)


def _words(text: str) -> list[str]:
    return [word for word in _all_words(text) if word not in FILLER]


# Schreibende Anfrage: Methode, Pfad, Nutzlast (der Inhalt von "data") -> Antwort.
Writer = Callable[[str, str, dict], dict]


class Clarify(ValueError):
    """Die Aktion laesst sich nicht eindeutig aufloesen; der Text ist die Rueckfrage an den Nutzer."""


class Refused(RuntimeError):
    """Eine Anfrage wuerde die Grenzen verlassen (anderes Projekt, nicht im Katalog). Programmfehler, kein Nutzerfehler."""


# --- Wunsch des Nutzers (Modell)


class Wish(BaseModel):
    """Was der Nutzer in Asana tun will: genau eine Aktion."""

    aktion: Literal[
        "anlegen",
        "abschliessen",
        "wieder_oeffnen",
        "faelligkeit",
        "abschnitt",
        "umbenennen",
        "unteraufgabe",
        "abschnitt_anlegen",
        "beschreibung",
        "unklar",
    ] = Field(description="Die eine Aktion. „unklar“, wenn der Satz keine dieser Aktionen verlangt oder mehrere.")
    aufgabe: str = Field(
        "", description="Die Worte, mit denen der Nutzer die vorhandene Aufgabe benennt. Leer beim Anlegen."
    )
    titel: str = Field("", description="Titel der neuen Aufgabe oder Unteraufgabe, oder der neue Name beim Umbenennen.")
    faellig: str = Field(
        "",
        description="Zeitangabe in den Worten des Nutzers, mit Uhrzeit, wenn er eine nennt: „Freitag“, „morgen 15 Uhr“, „24.11.“. "
        "Leer, wenn der Satz keine nennt. Nur wenn der Nutzer den Termin ausdrücklich entfernen will: „keine“.",
    )
    abschnitt: str = Field("", description="Name des Abschnitts, wenn der Satz einen nennt.")
    quelle: str = Field(
        "",
        description="Nur bei beschreibung: die Worte, mit denen der Nutzer das Dokument benennt, aus dem "
        "die Beschreibung entstehen soll (etwa „Workshop-Recap“). Leer, wenn er keines nennt.",
    )


@dataclass
class WishDeps:
    sections: list[str] = field(default_factory=list)


wish_agent = Agent(deps_type=WishDeps, output_type=structured(Wish), model_settings=MODEL_SETTINGS, retries=2)


@wish_agent.instructions
def instructions(ctx: RunContext[WishDeps]) -> str:
    sections = "; ".join(ctx.deps.sections) or "(keine)"
    return (
        "Du übersetzt einen Satz des Nutzers in genau eine Aktion für Asana. Du führst nichts aus und "
        "siehst keine Aufgaben; der Code sucht die Aufgabe und fragt den Nutzer vor der Ausführung. Regeln:\n"
        "- anlegen: eine neue Aufgabe („leg an“, „erstelle“, „neue Aufgabe“, „trag ein“). titel ist ihr Name.\n"
        "- abschliessen: „schließe ab“, „erledigt“, „hak ab“, „fertig“. wieder_oeffnen: „öffne wieder“, „doch nicht erledigt“.\n"
        "- faelligkeit: Termin einer vorhandenen Aufgabe setzen, ändern oder entfernen („setz auf Freitag“, "
        "„verschiebe auf morgen“, „ohne Termin“).\n"
        "- abschnitt: eine vorhandene Aufgabe in einen Abschnitt verschieben („in den Abschnitt …“, „nach In Arbeit“, "
        "„pack … in …“, „tu … in …“, „sortiere … unter … ein“).\n"
        "- abschnitt_anlegen: einen neuen Abschnitt im Projekt anlegen („leg den Abschnitt … an“, „neuer "
        "Abschnitt …“). abschnitt ist sein Name; aufgabe und titel bleiben leer.\n"
        "- beschreibung: die Beschreibung einer vorhandenen Aufgabe aus einem Dokument schreiben lassen "
        "(„schreib die Beschreibung für … aus dem Recap“, „beschreibe … anhand des Protokolls“, „fülle die "
        "Beschreibung von … aus“). aufgabe ist die Aufgabe, quelle das genannte Dokument. Du schreibst die "
        "Beschreibung nicht selbst; titel bleibt leer. Ein Kommentar ist keine Beschreibung.\n"
        "- umbenennen: titel ist der neue Name, aufgabe der bisherige.\n"
        "- unteraufgabe: aufgabe ist die übergeordnete Aufgabe, titel die neue Unteraufgabe.\n"
        "- Nennt der Satz die Aufgabe nicht beim Namen, sondern verweist auf sie („den eben angelegten Task“, "
        "„die Aufgabe von gerade“, „die letzte Aufgabe“), dann ist das trotzdem eine klare Aktion: Trage "
        "den Verweis wörtlich in aufgabe ein. Der Code weiß, welche gemeint ist.\n"
        "- unklar: wenn der Satz etwas anderes will (kommentieren, zuweisen, löschen, mehrere Dinge auf "
        "einmal) oder keine Aktion nennt.\n"
        "- aufgabe und titel: nur der Name, wörtlich aus dem Satz, ohne Anführungszeichen, ohne das Verb "
        "und ohne Worte wie „die Aufgabe“. Eine Zeitangabe gehört nicht in den Titel, sondern nach faellig.\n"
        "- Beispiele: „Leg eine Aufgabe Reisekosten einreichen bis Freitag an“ → anlegen, titel "
        "„Reisekosten einreichen“, faellig „Freitag“. „Benenne Entwurf lesen in Entwurf prüfen um“ → "
        "umbenennen, aufgabe „Entwurf lesen“, titel „Entwurf prüfen“.\n"
        "- faellig: die Worte des Nutzers, kein ausgerechnetes Datum. „Verschiebe auf Freitag“ ist eine "
        "Fälligkeit, „verschiebe nach In Arbeit“ ein Abschnitt.\n"
        f"- abschnitt: beim Verschieben mit dem Namen aus dieser Liste, wenn er dort steht: {sections}\n"
        "  Steht er nicht dort, trage den Namen so ein, wie er im Satz steht.\n"
    )


# --- Stand des Projekts (frisch aus Asana)


@dataclass(frozen=True)
class LiveTask:
    gid: str
    name: str
    completed: bool = False
    due: str = ""  # ISO-Datum oder -Zeitpunkt
    notes: str = ""  # Beschreibung
    section_gid: str = ""
    section: str = ""
    parent: str = ""  # gid der uebergeordneten Aufgabe


@dataclass(frozen=True)
class ProjectState:
    gid: str
    name: str
    sections: tuple[tuple[str, str], ...]  # (gid, Name)
    tasks: tuple[LiveTask, ...]

    def task(self, gid: str) -> LiveTask | None:
        return next((task for task in self.tasks if task.gid == gid), None)


def _live(task: dict, project: str) -> LiveTask:
    membership = next(
        (m for m in task.get("memberships") or [] if (m.get("project") or {}).get("gid") == project), {}
    )
    section = membership.get("section") or {}
    return LiveTask(
        task["gid"],
        task.get("name") or "",
        bool(task.get("completed")),
        task.get("due_at") or task.get("due_on") or "",
        task.get("notes") or "",
        section.get("gid") or "",
        section.get("name") or "",
        (task.get("parent") or {}).get("gid") or "",
    )


def find_project(name: str, transport: Transport) -> tuple[str, str]:
    """(gid, Name) des Projekts mit genau diesem Namen. Wirft ``Clarify`` bei keinem oder mehreren."""
    me = transport("/users/me", {"opt_fields": "workspaces.name"})["data"]
    found: list[tuple[str, str]] = []
    for workspace in me.get("workspaces", []):
        page = transport(
            f"/workspaces/{workspace['gid']}/typeahead",
            {"resource_type": "project", "query": name, "count": "20", "opt_fields": "name"},
        )
        found += [(p["gid"], p["name"]) for p in page["data"] if p.get("name", "").casefold() == name.casefold()]
    if len(found) != 1:
        problem = "gibt es in Asana nicht" if not found else "gibt es mehrfach"
        raise Clarify(f"Das Projekt „{name}“ aus write_projects {problem}. Ich schreibe nichts.")
    return found[0]


def load_state(name: str, transport: Transport) -> ProjectState:
    """Liest Abschnitte und Aufgaben des Projekts jetzt aus Asana, nicht aus dem Spiegel."""
    gid, exact = find_project(name, transport)
    sections = asana_mirror._pages(transport, f"/projects/{gid}/sections", {"opt_fields": "name"})
    tasks = asana_mirror._pages(transport, f"/projects/{gid}/tasks", {"opt_fields": TASK_FIELDS})
    return ProjectState(
        gid,
        exact,
        tuple((section["gid"], section.get("name") or "") for section in sections),
        tuple(_live(task, gid) for task in tasks),
    )


# --- Aufloesen und planen (Code)


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    data: dict


@dataclass(frozen=True)
class Plan:
    """Eine aufgeloeste Aktion: was sich aendert, wie es ausgefuehrt und wie es zurueckgenommen wird."""

    action: str
    title: str  # Name der betroffenen (oder neuen) Aufgabe
    changes: tuple[tuple[str, str, str], ...]  # (Was, alt, neu)
    requests: tuple[Request, ...]
    undo: tuple[Request, ...]  # leer: Die Rueckrichtung ergibt sich erst aus der Antwort (neue Aufgabe)
    task_gid: str = ""  # betroffene vorhandene Aufgabe
    expect: tuple[tuple[str, object], ...] = ()  # Felder, die nach der Ausfuehrung so stehen muessen
    detail: tuple[str, ...] = ()  # weitere Zeilen fuer die Freigabe, etwa der Text einer neuen Beschreibung

    def lines(self, project: str) -> list[str]:
        lines = [f"{LABELS[self.action]} im Projekt „{project}“: {self.title}"]
        lines += [f"  {what}: {old} → {new}" if old else f"  {what}: {new}" for what, old, new in self.changes]
        return lines + list(self.detail)


def _moment(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _due_text(due: str) -> str:
    if not due:
        return "kein Termin"
    if "T" in due:
        local = _moment(due).astimezone()
        return f"{_day(local.date())}{local:%Y %H:%M} Uhr"
    day = date.fromisoformat(due)
    return f"{_day(day)}{day:%Y}"


# "15 Uhr", "um 15:30", "9.30 Uhr"
CLOCK = re.compile(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*uhr\b|\bum\s+(\d{1,2})[:.](\d{2})\b", re.IGNORECASE)


def due_value(words: str, today: date) -> str:
    """Die Faelligkeit, die die Worte nennen, wie Asana sie speichert.

    "" heisst: entfernen. Sonst ein Tag ("2026-11-24") oder, mit Uhrzeit, ein
    Zeitpunkt in UTC ("2026-11-21T14:00:00.000Z"). Wirft ``Clarify``.
    """
    if NO_DATE_WORDS.search(words.casefold()):
        return ""
    clock = CLOCK.search(words)
    day = _one_day(CLOCK.sub(" ", words), today, past=False)
    if day is None and clock:
        day = today  # nur eine Uhrzeit: heute
    if day is None:
        raise Clarify(
            f"Die Zeitangabe „{words}“ verstehe ich nicht. Nenn einen Tag: heute, morgen, einen Wochentag "
            "oder ein Datum (24.11.), auf Wunsch mit Uhrzeit (15 Uhr). Für „ohne Termin“: keine."
        )
    if not clock:
        return day.isoformat()
    hour, minute = int(clock.group(1) or clock.group(3)), int(clock.group(2) or clock.group(4) or 0)
    if hour > 23 or minute > 59:
        raise Clarify(f"Die Uhrzeit in „{words}“ gibt es nicht.")
    local = datetime.combine(day, clock_time(hour, minute)).astimezone()  # Zeitzone des Rechners
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def same_due(first: str, second: str) -> bool:
    if "T" in first and "T" in second:
        return _moment(first) == _moment(second)
    return first == second


def _similar(first: str, second: str) -> bool:
    """Ob zwei Worte dasselbe meinen: eines beginnt mit dem anderen, oder sie teilen einen langen Anfang.

    So findet "Brainstorming" die Aufgabe "... brainstormen".
    """
    if first.startswith(second) or second.startswith(first):
        return True
    shared = 0
    for a, b in zip(first, second):
        if a != b:
            break
        shared += 1
    return shared >= 5 and shared >= 0.6 * max(len(first), len(second))


def find_task(asked: str, tasks: list[LiveTask]) -> LiveTask:
    """Die gemeinte Aufgabe. Wirft ``Clarify`` bei keiner oder mehreren."""
    wanted = " ".join(asked.casefold().split())
    if not wanted:
        raise Clarify("Welche Aufgabe meinst du? Nenn ihren Namen.")
    exact = [task for task in tasks if task.name.casefold().strip() == wanted]
    if exact:
        return exact[0] if len(exact) == 1 else _ambiguous(asked, exact)
    words = _words(asked)
    found = exact or [
        task
        for task in tasks
        if wanted in task.name.casefold()
        or (words and all(any(_similar(part, word) for part in _words(task.name)) for word in words))
    ]
    if not found:
        # Der volle Name steht in den Worten, umgeben von anderem ("Benenne Folien bauen").
        found = [task for task in tasks if task.name.strip() and task.name.casefold().strip() in wanted]
    if len(found) == 1:
        return found[0]
    if not found:
        raise Clarify(f"Eine Aufgabe „{asked}“ finde ich im Projekt nicht.")
    return _ambiguous(asked, found)


def _ambiguous(asked: str, found: list[LiveTask]) -> LiveTask:
    names = "; ".join(task.name for task in found[:MAX_CANDIDATES])
    raise Clarify(f"„{asked}“ passt auf mehrere Aufgaben: {names}. Nenn den Namen genauer.")


def find_section(asked: str, sections: tuple[tuple[str, str], ...]) -> tuple[str, str]:
    wanted = " ".join(asked.casefold().split())
    found = [section for section in sections if section[1].casefold().strip() == wanted]
    if not found:
        words = _words(asked)
        found = [
            section
            for section in sections
            if wanted and wanted in section[1].casefold()
            or (words and all(any(_similar(part, word) for part in _words(section[1])) for word in words))
        ]
    if len(found) == 1:
        return found[0]
    names = "; ".join(name for _, name in sections) or "keine"
    if found:
        raise Clarify(f"Den Abschnitt „{asked}“ ist nicht eindeutig. Abschnitte im Projekt: {names}.")
    raise Clarify(
        f"Den Abschnitt „{asked}“ gibt es nicht. Abschnitte im Projekt: {names}. "
        f"Anlegen mit: „Leg den Abschnitt {asked.strip()} an“."
    )


def _title(text: str) -> str:
    title = " ".join(text.split()).strip(" \"'„“‚‘")
    if not title:
        raise Clarify("Wie soll die Aufgabe heißen?")
    if len(title) > MAX_TITLE_CHARS:
        raise Clarify(f"Der Titel ist länger als {MAX_TITLE_CHARS} Zeichen.")
    return title


def _due_fields(value: str, before: str = "") -> dict:
    """Die Felder fuer Asana: ein Zeitpunkt nach due_at, ein Tag nach due_on; Entfernen leert, was gesetzt war."""
    if "T" in value:
        return {"due_at": value}
    if not value and "T" in before:
        return {"due_at": None}
    return {"due_on": value or None}


def _restore_due(task: LiveTask) -> dict:
    if "T" in task.due:
        return {"due_at": task.due}
    return {"due_on": task.due or None}


def make_plan(wish: Wish, state: ProjectState, today: date) -> Plan:
    """Loest den Wunsch gegen den Stand des Projekts auf. Wirft ``Clarify`` mit der Rueckfrage."""
    action = wish.aktion
    if action == UNCLEAR:
        raise Clarify(
            "Das kann ich nicht ausführen. Möglich ist je Satz eine Aktion: Aufgabe anlegen, abschließen, "
            "wieder öffnen, Fälligkeit setzen oder entfernen, in einen Abschnitt verschieben, umbenennen, "
            "Unteraufgabe anlegen, Abschnitt anlegen, Beschreibung aus einem Dokument schreiben."
        )
    if action == DESCRIBE:
        raise Clarify("Für eine Beschreibung brauche ich den Suchindex; das geht nur über den Befehl selbst.")
    if action == NEW_SECTION:
        name = _title(wish.abschnitt or wish.titel)
        same = [section for _, section in state.sections if section.casefold() == name.casefold()]
        if same:
            raise Clarify(f"Den Abschnitt „{same[0]}“ gibt es im Projekt schon.")
        return Plan(
            NEW_SECTION,
            name,
            (("Abschnitt", "", name),),
            (Request("POST", f"/projects/{state.gid}/sections", {"name": name}),),
            (),
        )
    open_tasks = [task for task in state.tasks if not task.completed]
    if action in (CREATE, SUBTASK):
        # Das Modell laesst die Zeitangabe gelegentlich im Titel stehen; dann holt der Code sie heraus.
        dated = TRAILING_DATE.search(wish.titel)
        if dated and (not wish.faellig.strip() or NO_DATE_WORDS.search(wish.faellig.casefold())):
            wish = wish.model_copy(update={"titel": wish.titel[: dated.start()], "faellig": dated.group(1)})
    if action == CREATE:
        title = _title(wish.titel or wish.aufgabe)
        same = [task for task in open_tasks if task.name.casefold() == title.casefold()]
        if same and wish.abschnitt.strip() and not wish.faellig.strip():
            # "Tu Folien bauen in In Arbeit": Die Aufgabe gibt es schon, gemeint ist das Verschieben.
            return make_plan(Wish(aktion=SECTION, aufgabe=same[0].name, abschnitt=wish.abschnitt), state, today)
        if same:
            raise Clarify(f"Eine offene Aufgabe „{same[0].name}“ gibt es im Projekt schon.")
        data: dict = {"name": title, "projects": [state.gid], "assignee": "me"}
        changes = [("Titel", "", title), ("Zuständig", "", "du")]
        if wish.faellig.strip():
            due = due_value(wish.faellig, today)
            if due:
                data.update(_due_fields(due))
                changes.append(("Fällig", "", _due_text(due)))
        requests = [Request("POST", "/tasks", data)]
        if wish.abschnitt.strip():
            section_gid, section = find_section(wish.abschnitt, state.sections)
            requests.append(Request("POST", f"/sections/{section_gid}/addTask", {"task": "<neu>"}))
            changes.append(("Abschnitt", "", section))
        return Plan(CREATE, title, tuple(changes), tuple(requests), ())

    if action == REOPEN:
        task = find_task(wish.aufgabe, [task for task in state.tasks if task.completed])
        return Plan(
            REOPEN,
            task.name,
            (("Stand", "erledigt", "offen"),),
            (Request("PUT", f"/tasks/{task.gid}", {"completed": False}),),
            (Request("PUT", f"/tasks/{task.gid}", {"completed": True}),),
            task.gid,
            (("completed", False),),
        )
    task = find_task(wish.aufgabe, open_tasks)
    path = f"/tasks/{task.gid}"
    if action == COMPLETE:
        return Plan(
            COMPLETE,
            task.name,
            (("Stand", "offen", "erledigt"),),
            (Request("PUT", path, {"completed": True}),),
            (Request("PUT", path, {"completed": False}),),
            task.gid,
            (("completed", True),),
        )
    if action == DUE:
        if not wish.faellig.strip():
            raise Clarify(f"Auf wann soll „{task.name}“ fällig sein? Oder „keine“ für ohne Termin.")
        new = due_value(wish.faellig, today)
        if same_due(new, task.due):
            raise Clarify(f"„{task.name}“ steht schon auf {_due_text(task.due)}.")
        fields = _due_fields(new, task.due)
        return Plan(
            DUE,
            task.name,
            (("Fällig", _due_text(task.due), _due_text(new)),),
            (Request("PUT", path, fields),),
            (Request("PUT", path, _restore_due(task)),),
            task.gid,
            tuple(fields.items()),
        )
    if action == SECTION:
        section_gid, section = find_section(wish.abschnitt, state.sections)
        if section_gid == task.section_gid:
            raise Clarify(f"„{task.name}“ steht schon im Abschnitt „{section}“.")
        undo = (Request("POST", f"/sections/{task.section_gid}/addTask", {"task": task.gid}),) if task.section_gid else ()
        return Plan(
            SECTION,
            task.name,
            (("Abschnitt", task.section or "keiner", section),),
            (Request("POST", f"/sections/{section_gid}/addTask", {"task": task.gid}),),
            undo,
            task.gid,
            (("section", section_gid),),
        )
    if action == RENAME:
        title = _title(wish.titel)
        if title == task.name:
            raise Clarify(f"Die Aufgabe heißt schon „{title}“.")
        return Plan(
            RENAME,
            task.name,
            (("Titel", task.name, title),),
            (Request("PUT", path, {"name": title}),),
            (Request("PUT", path, {"name": task.name}),),
            task.gid,
            (("name", title),),
        )
    title = _title(wish.titel)
    return Plan(
        SUBTASK,
        f"{title} (unter „{task.name}“)",
        (("Unteraufgabe", "", title), ("Zuständig", "", "du")),
        (Request("POST", f"{path}/subtasks", {"name": title, "assignee": "me"}),),
        (),
        task.gid,
    )


def plan_description(wish: Wish, state: ProjectState, drafter: Drafter) -> Plan:
    """Der Plan fuer "Beschreibung setzen": Aufgabe aufloesen, Entwurf holen, alt und neu zeigen."""
    task = find_task(wish.aufgabe, [task for task in state.tasks if not task.completed])
    try:
        draft = drafter(task.name, wish.quelle)
    except NoDescription as exc:
        raise Clarify(str(exc)) from exc
    if draft.text.strip() == task.notes.strip():
        raise Clarify(f"„{task.name}“ hat genau diese Beschreibung schon.")
    partial = f", {draft.used} von {draft.total} Abschnitten berücksichtigt" if draft.used < draft.total else ""
    old = f"{len(task.notes.strip())} Zeichen" if task.notes.strip() else "leer"
    detail = [f"  Quelle: Dokument „{draft.document}“ ({draft.source}{partial})"]
    if draft.foreign:
        detail.append(f"  Nicht im Dokument, bitte prüfen: {', '.join(draft.foreign[:12])}")
    if task.notes.strip():
        detail += ["", "Bisherige Beschreibung (wird ersetzt):", *task.notes.strip().splitlines()]
    detail += ["", "Neue Beschreibung:", *draft.text.splitlines(), ""]
    path = f"/tasks/{task.gid}"
    return Plan(
        DESCRIBE,
        task.name,
        (("Beschreibung", old, f"{len(draft.text)} Zeichen aus „{draft.document}“"),),
        (Request("PUT", path, {"notes": draft.text}),),
        (Request("PUT", path, {"notes": task.notes}),),
        task.gid,
        (("notes", draft.text),),
        tuple(detail),
    )


# --- Ausfuehren (Code), mit Grenzkontrolle


UPDATE_KEYS = {"completed", "due_on", "due_at", "name", "notes"}


def check_allowed(request: Request, state: ProjectState, created: set[str]) -> None:
    """Wirft ``Refused``, wenn die Anfrage nicht im Katalog steht oder das erlaubte Projekt verlaesst.

    Das ist die letzte Sperre vor Asana: Sie gilt auch dann, wenn Modell oder
    Planung etwas Falsches geliefert haben.
    """
    known = {task.gid for task in state.tasks} | created
    sections = {gid for gid, _ in state.sections}
    method, path, data = request.method, request.path, request.data
    parts = path.strip("/").split("/")
    if method == "POST" and path == "/tasks":
        if data.get("projects") == [state.gid] and set(data) <= {"name", "projects", "assignee", "due_on", "due_at"} and data.get("assignee") == "me":
            return
    elif method == "PUT" and len(parts) == 2 and parts[0] == "tasks":
        if parts[1] in known and data and set(data) <= UPDATE_KEYS:
            return
    elif method == "POST" and len(parts) == 3 and parts[0] == "sections" and parts[2] == "addTask":
        if parts[1] in sections and data.get("task") in known and set(data) == {"task"}:
            return
    elif method == "POST" and len(parts) == 3 and parts[0] == "tasks" and parts[2] == "subtasks":
        if parts[1] in known and set(data) <= {"name", "assignee"} and data.get("assignee") == "me":
            return
    elif method == "POST" and path == f"/projects/{state.gid}/sections":
        if set(data) == {"name"} and data["name"]:
            return
    elif method == "DELETE" and len(parts) == 2 and parts[0] in ("tasks", "sections"):
        if parts[1] in created and not data:  # nur, was der Agent selbst angelegt hat
            return
    raise Refused(f"Anfrage außerhalb der Grenzen: {method} {path}")


def http_writer(token: str) -> Writer:
    """Schreibender Zugang zur Asana-API."""

    def send(method: str, path: str, data: dict) -> dict:
        body = None if method == "DELETE" else json.dumps({"data": data}).encode()
        request = urllib.request.Request(
            f"{API_URL}{path}",
            data=body,
            method=method,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200]
            raise AsanaAccessError(f"{_explain(exc.code, path)} {detail}".strip()) from exc
        except urllib.error.URLError as exc:
            raise AsanaAccessError(f"Asana nicht erreichbar: {exc.reason}") from exc

    return send


@dataclass
class Outcome:
    ok: bool
    lines: list[str]
    task_gid: str = ""
    undo: tuple[Request, ...] = ()


def execute(plan: Plan, state: ProjectState, writer: Writer, transport: Transport) -> Outcome:
    """Fuehrt den Plan aus und liest das Ergebnis zurueck. Ruft nie etwas auf, das ``check_allowed`` ablehnt."""
    created: set[str] = set()
    task_gid = plan.task_gid
    new_gid = ""
    for request in plan.requests:
        data = {key: (new_gid if value == "<neu>" else value) for key, value in request.data.items()}
        request = Request(request.method, request.path, data)
        check_allowed(request, state, created)
        response = writer(request.method, request.path, request.data)
        if request.method == "POST" and (request.path == "/tasks" or request.path.endswith(("/subtasks", "/sections"))):
            new_gid = (response.get("data") or {}).get("gid") or ""
            if not new_gid:
                return Outcome(False, ["Asana hat keine Kennung für das Angelegte geliefert; bitte in Asana nachsehen."])
            created.add(new_gid)
    if plan.action == NEW_SECTION:
        # Ein Abschnitt ist keine Aufgabe: eigene Kontrolle, eigenes Zuruecknehmen.
        after = transport(f"/sections/{new_gid}", {"opt_fields": "name"})["data"]
        undo = (Request("DELETE", f"/sections/{new_gid}", {}),)
        if after.get("name") != plan.title:
            return Outcome(False, [f"Ausgeführt, aber der Abschnitt heißt {after.get('name')!r}."], new_gid, undo)
        return Outcome(True, ["Erledigt und kontrolliert."], new_gid, undo)
    undo = plan.undo or ((Request("DELETE", f"/tasks/{new_gid}", {}),) if new_gid else ())
    checked = new_gid or task_gid
    after = transport(f"/tasks/{checked}", {"opt_fields": TASK_FIELDS})["data"]
    live = _live(after, state.gid)
    wrong = []
    for name, value in plan.expect:
        actual = live.section_gid if name == "section" else after.get(name)
        if name == "due_at" and actual and value and _moment(actual) == _moment(value):
            continue
        if name == "notes" and (actual or "").strip() == str(value).strip():
            continue  # Asana haengt gelegentlich einen Zeilenumbruch an
        if actual != value:
            wrong.append(f"{name} ist {actual!r}, erwartet {value!r}")
    if plan.action in (CREATE, SUBTASK) and not after.get("name"):
        wrong.append("die neue Aufgabe ist nicht lesbar")
    if wrong:
        return Outcome(False, ["Ausgeführt, aber die Kontrolle stimmt nicht: " + "; ".join(wrong)], checked, undo)
    return Outcome(True, ["Erledigt und kontrolliert."], checked, undo)


# --- Protokoll


class Journal:
    """Was der Agent in Asana geaendert hat, in der Agenten-Datenbank. ``path`` darf ":memory:" sein."""

    def __init__(self, path: str | None = None) -> None:
        self.db = sqlite3.connect(str(path or db_path()))
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS asana_journal ("
            "id INTEGER PRIMARY KEY, at TEXT NOT NULL, project TEXT NOT NULL, project_gid TEXT NOT NULL, "
            "action TEXT NOT NULL, title TEXT NOT NULL, changes TEXT NOT NULL, task_gid TEXT NOT NULL, "
            "undo TEXT NOT NULL, created INTEGER NOT NULL DEFAULT 0, undone_at TEXT NOT NULL DEFAULT '')"
        )

    def close(self) -> None:
        self.db.close()

    def add(self, now: datetime, state: ProjectState, plan: Plan, outcome: Outcome) -> int:
        with self.db:
            return self.db.execute(
                "INSERT INTO asana_journal (at, project, project_gid, action, title, changes, task_gid, undo, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    now.isoformat(timespec="seconds"),
                    state.name,
                    state.gid,
                    plan.action,
                    plan.title,
                    json.dumps(plan.changes, ensure_ascii=False),
                    outcome.task_gid,
                    json.dumps([[r.method, r.path, r.data] for r in outcome.undo], ensure_ascii=False),
                    int(plan.action in (CREATE, SUBTASK, NEW_SECTION)),
                ),
            ).lastrowid

    def rows(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM asana_journal ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def last_open(self, sections: bool | None = None) -> sqlite3.Row | None:
        """Die letzte nicht zurueckgenommene Aktion; mit ``sections`` nur an Abschnitten (True) oder nur an Aufgaben (False)."""
        where = {None: "", True: " AND action = ?", False: " AND action <> ?"}[sections]
        args = () if sections is None else (NEW_SECTION,)
        return self.db.execute(
            f"SELECT * FROM asana_journal WHERE undone_at = ''{where} ORDER BY id DESC LIMIT 1", args
        ).fetchone()

    def mark_undone(self, entry: int, now: datetime) -> None:
        with self.db:
            self.db.execute(
                "UPDATE asana_journal SET undone_at = ? WHERE id = ?", (now.isoformat(timespec="seconds"), entry)
            )


def journal_lines(rows: list[sqlite3.Row]) -> list[str]:
    lines = []
    for row in rows:
        changes = "; ".join(f"{what}: {old} → {new}" if old else f"{what}: {new}" for what, old, new in json.loads(row["changes"]))
        undone = " (zurückgenommen)" if row["undone_at"] else ""
        lines.append(f"{row['at'][:16].replace('T', ' ')} · {LABELS[row['action']]}: {row['title']} · {changes}{undone}")
    return lines or ["Noch keine Änderungen."]


# --- Bedienung


def write_project(profile: Profile) -> str:
    """Das Projekt, in das geschrieben werden darf. Wirft ``Clarify``, wenn keines oder mehrere eingetragen sind."""
    projects = profile.asana_write_projects
    if not projects:
        raise Clarify(
            "Schreiben nach Asana ist nicht freigegeben. Im Profil unter [asana] eintragen: "
            'write_projects = ["Name des Projekts"]'
        )
    if len(projects) > 1:
        raise Clarify("Unter write_projects steht mehr als ein Projekt; unterstützt ist vorerst genau eines.")
    return projects[0]


# "Die eben angelegte Aufgabe", "den Task von gerade": gemeint ist die Aufgabe der letzten Aktion.
LAST_TASK = re.compile(
    r"\b(eben|gerade|soeben|vorhin|zuletzt)\b.*\b(angelegt\w*|erstellt\w*|geändert\w*|task|aufgabe)\b"
    r"|\b(letzte[nr]?|vorige[nr]?)\s+(task|aufgabe)\b"
    r"|\b(task|aufgabe)\s+von\s+(eben|gerade|vorhin)\b",
    re.IGNORECASE,
)


LAST_SECTION = re.compile(
    r"\b(neue[nrms]?|eben|gerade|soeben|vorhin|zuletzt|letzte[nrms]?)\s+(?:\w+\s+)?(abschnitt|spalte)\b"
    r"|\b(abschnitt|spalte)\s+von\s+(eben|gerade|vorhin)\b",
    re.IGNORECASE,
)


def refer_to_last(sentence: str, wish: Wish, state: ProjectState, journal: "Journal") -> Wish:
    """Setzt fuer Verweise ein, was gemeint ist: "die eben angelegte Aufgabe", "der neue Abschnitt".

    Die Aufgabe ist die der letzten Aktion an einer Aufgabe, der Abschnitt der
    zuletzt angelegte. Nur wenn es sie im Projekt noch gibt; sonst bleibt der
    Wunsch, wie er ist, und die Aufloesung fragt nach.
    """
    if wish.aktion in (CREATE, UNCLEAR, NEW_SECTION):
        return wish
    changes: dict[str, str] = {}
    if wish.aktion == SECTION and LAST_SECTION.search(sentence):
        entry = journal.last_open(sections=True)
        names = dict(state.sections)
        if entry is not None and entry["project_gid"] == state.gid and entry["task_gid"] in names:
            changes["abschnitt"] = names[entry["task_gid"]]
    # "den eben angelegten Task in den neuen Abschnitt": Der Verweis auf die Aufgabe steht vor dem Abschnitt.
    about_task = LAST_SECTION.sub(" ", sentence) if changes else sentence
    if LAST_TASK.search(about_task):
        entry = journal.last_open(sections=False)
        task = state.task(entry["task_gid"]) if entry is not None and entry["project_gid"] == state.gid else None
        if task is not None:
            changes["aufgabe"] = task.name
    return wish.model_copy(update=changes) if changes else wish


# Was der Satz woertlich sagt, entscheidet der Code, bevor das Modell gefragt wird.
EVERYTHING = re.compile(
    r"\b(alle[nrs]?|sämtliche[nr]?|jede[nrs]?|beide[nr]?|mehrere|restliche[nr]?)\s+(\w+\s+)?(aufgaben?|tasks?|tickets?|todos?|unteraufgaben?)\b"
    r"|\balles\b",
    re.IGNORECASE,
)
ACTION_VERB = re.compile(
    r"\b(lege?|erstell\w*|schlie(?:ß|ss)\w*|hak\w*|erledig\w*|öffne\w*|setz\w*|verschieb\w*|schieb\w*|pack\w*|"
    r"benenn\w*|füg\w*|nimm|lösch\w*|trag\w*|weis\w*|kommentier\w*)\b",
    re.IGNORECASE,
)
CLAUSE = re.compile(r"\bund\b|\bdann\b|\bdanach\b|\baußerdem\b|;")


def refuse_by_wording(sentence: str) -> None:
    """Wirft ``Clarify`` fuer Saetze, die mehr als eine Aufgabe oder mehr als eine Aktion verlangen.

    Das Modell lehnt solche Saetze meist ab, aber nicht immer: Gemessen hat es
    aus "alle Aufgaben" einmal eine einzelne Aufgabe gemacht und von zwei
    Aktionen einmal nur die erste ausgefuehrt.
    """
    if EVERYTHING.search(sentence):
        raise Clarify("Mehrere Aufgaben auf einmal ändere ich nicht. Nenn eine Aufgabe beim Namen.")
    parts = [part for part in CLAUSE.split(sentence) if ACTION_VERB.search(part)]
    if len(parts) > 1:
        raise Clarify("Das sind mehrere Aktionen in einem Satz. Bitte eine nach der anderen.")


def named_section(sentence: str, wish: Wish, state: ProjectState) -> Wish:
    """Ergaenzt den Abschnitt, wenn der Satz einen des Projekts woertlich nennt und das Modell ihn ausgelassen hat.

    Gemessen: Bei "Neue Aufgabe ..., im Abschnitt In Arbeit" fehlte der
    Abschnitt in einem von fuenf Laeufen, und die Aufgabe waere ohne ihn
    angelegt worden.
    """
    if wish.aktion not in (CREATE, SECTION) or wish.abschnitt.strip():
        return wish
    found = [
        name
        for _, name in state.sections
        if name.strip()
        and re.search(
            rf"\b(?:abschnitt|spalte|in|im|nach|unter)\s+(?:den\s+|dem\s+|die\s+)?(?:abschnitt\s+|spalte\s+)?{re.escape(name.strip())}(?!\w)",
            sentence,
            re.IGNORECASE,
        )
    ]
    return wish.model_copy(update={"abschnitt": found[0]}) if len(found) == 1 else wish


def named_task(sentence: str, wish: Wish, state: ProjectState) -> Wish:
    """Ergaenzt die Aufgabe, wenn das Modell sie ausgelassen hat und der Satz genau eine beim vollen Namen nennt."""
    if wish.aktion in (CREATE, NEW_SECTION, UNCLEAR) or wish.aufgabe.strip():
        return wish
    lowered = sentence.casefold()
    found = [
        task.name
        for task in state.tasks
        if task.completed == (wish.aktion == REOPEN) and task.name.strip() and task.name.casefold().strip() in lowered
    ]
    return wish.model_copy(update={"aufgabe": found[0]}) if len(found) == 1 else wish


def interpret(sentence: str, state: ProjectState, model) -> Wish:
    try:
        return wish_agent.run_sync(sentence, deps=WishDeps([name for _, name in state.sections]), model=model).output
    except UnexpectedModelBehavior:
        return Wish(aktion=UNCLEAR)


def act(
    sentence: str,
    profile: Profile,
    model,
    transport: Transport,
    writer: Writer,
    journal: Journal,
    now: datetime,
    confirm: Callable[[str], str] | None,
    out: Callable[[str], None] = print,
    drafter: Drafter | None = None,
) -> str:
    """Ein Satz, eine Aktion. ``drafter`` entwirft Beschreibungen aus Dokumenten; ohne ihn gibt es die Aktion nicht.

    Gibt zurueck: "ausgefuehrt", "abgebrochen", "rueckfrage", "nicht_freigegeben" oder "fehler".

    ``confirm`` fragt den Nutzer; None (kein Terminal) heisst: nur der Plan, keine Ausfuehrung.
    """
    try:
        state = load_state(write_project(profile), transport)
        refuse_by_wording(sentence)
        wish = named_section(sentence, interpret(sentence, state, model), state)
        wish = refer_to_last(sentence, named_task(sentence, wish, state), state, journal)
        if wish.aktion == DESCRIBE and drafter is not None:
            plan = plan_description(wish, state, drafter)
        else:
            plan = make_plan(wish, state, now.astimezone().date())
    except Clarify as exc:
        out(str(exc))
        return "rueckfrage"
    out("\n".join(plan.lines(state.name)))
    if confirm is None:
        out("Nicht ausgeführt: Ohne Terminal gibt es keine Freigabe.")
        return "nicht_freigegeben"
    try:
        answer = confirm("Ausführen? (j/n) ").strip().casefold()
    except EOFError:
        answer = ""
    if answer not in ("j", "ja"):
        out("Abgebrochen, nichts geändert.")
        return "abgebrochen"
    outcome = execute(plan, state, writer, transport)
    journal.add(now, state, plan, outcome)
    out("\n".join(outcome.lines))
    if outcome.ok:
        out("Zurücknehmen: python -m local_agents.asana_actions rueckgaengig")
    return "ausgefuehrt" if outcome.ok else "fehler"


def undo_last(
    profile: Profile,
    transport: Transport,
    writer: Writer,
    journal: Journal,
    now: datetime,
    confirm: Callable[[str], str] | None,
    out: Callable[[str], None] = print,
) -> str:
    """Nimmt die letzte noch nicht zurueckgenommene Aktion zurueck, nach Freigabe."""
    entry = journal.last_open()
    if entry is None:
        out("Es gibt nichts zurückzunehmen.")
        return "rueckfrage"
    requests = [Request(method, path, data) for method, path, data in json.loads(entry["undo"])]
    if not requests:
        out(f"„{entry['title']}“ ({LABELS[entry['action']]}) lässt sich nicht automatisch zurücknehmen.")
        return "rueckfrage"
    try:
        state = load_state(write_project(profile), transport)
    except Clarify as exc:
        out(str(exc))
        return "rueckfrage"
    if state.gid != entry["project_gid"]:
        out("Die letzte Aktion gehört zu einem anderen Projekt als dem jetzt freigegebenen. Ich ändere nichts.")
        return "rueckfrage"
    deleting = any(request.method == "DELETE" for request in requests)
    what = "Der vorherige Stand wird wiederhergestellt"
    if deleting:
        what = "Der angelegte Abschnitt wird gelöscht" if entry["action"] == NEW_SECTION else "Die angelegte Aufgabe wird gelöscht"
    out(f"Zurücknehmen: {LABELS[entry['action']]}: {entry['title']}\n  {what}.")
    if confirm is None:
        out("Nicht ausgeführt: Ohne Terminal gibt es keine Freigabe.")
        return "nicht_freigegeben"
    try:
        answer = confirm("Ausführen? (j/n) ").strip().casefold()
    except EOFError:
        answer = ""
    if answer not in ("j", "ja"):
        out("Abgebrochen, nichts geändert.")
        return "abgebrochen"
    created = {entry["task_gid"]} if entry["created"] else set()
    for request in requests:
        check_allowed(request, state, created)
        writer(request.method, request.path, request.data)
    journal.mark_undone(entry["id"], now)
    out("Zurückgenommen.")
    return "ausgefuehrt"


@contextmanager
def description_drafter(profile: Profile):
    """Der Entwurfs-Schritt fuer Beschreibungen, mit frisch abgeglichenem Suchindex.

    Der Index wird erst geoeffnet, wenn eine Beschreibung verlangt ist.
    """
    opened: list = []

    def drafter(task: str, hint: str) -> asana_describe.Draft:
        from local_agents import search
        from local_agents.context_window import OllamaError, fixed_context
        from local_agents.store import Store

        store, index = Store(), search.Index()
        opened.extend((store, index))
        search.refresh(index, profile, store)
        embedder = search.prepare_meaning(index, profile)
        name = profile.model_for(asana_describe.AGENT_NAME)
        try:
            large = (fixed_context(name or MODEL_NAME) or 0) >= asana_describe.LARGE_CONTEXT
        except OllamaError:
            large = False
        budget = asana_describe.LARGE_BUDGET if large else asana_describe.SMALL_BUDGET
        return asana_describe.draft(index, task, hint, ollama_model(name), embedder, budget)

    try:
        yield drafter
    finally:
        for resource in opened:
            resource.close()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    journal = Journal()
    try:
        if argv[1] == "protokoll":
            print("\n".join(journal_lines(journal.rows())))
            return 0
        confirm = input if sys.stdin.isatty() else None
        now = datetime.now().astimezone()
        try:
            token = asana_token()
            transport, writer = http_transport(token), http_writer(token)
            if argv[1] == "rueckgaengig":
                result = undo_last(profile, transport, writer, journal, now, confirm)
            else:
                model = ollama_model(profile.model_for(AGENT_NAME))
                with description_drafter(profile) as drafter:
                    result = act(argv[1], profile, model, transport, writer, journal, now, confirm, drafter=drafter)
        except AsanaAccessError as exc:
            print(exc, file=sys.stderr)
            return 1
        if result == "ausgefuehrt" and asana_mirror.mirror_path().is_file():
            mirror = asana_mirror.Mirror()
            try:  # damit Suche und Abfragen die Aenderung gleich kennen
                asana_mirror.sync(mirror, transport, now)
            except AsanaAccessError:
                pass
            finally:
                mirror.close()
        return 0 if result in ("ausgefuehrt", "abgebrochen", "nicht_freigegeben") else 1
    finally:
        journal.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))

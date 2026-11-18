"""Echte Faelle fuer die Messung festhalten ("merke").

Aufruf:
  python -m local_agents.evals merke scrum_master              die letzte Antwort an den Scrum Master
  python -m local_agents.evals merke scrum_master "Wortlaut"   eine bestimmte Antwort
  python -m local_agents.evals merke rueckmeldung "Wortlaut"   eine Antwort auf den Plan, mit den jetzt offenen Aufgaben
  python -m local_agents.evals merke status                    die jetzt offenen Aufgaben zur jetzigen Uhrzeit
  python -m local_agents.evals merke termin 12                 der Termin hinter Aufgabe #12
  python -m local_agents.evals merke stories datei.md          eine Markdown-Datei mit Stories

Der Fall landet in ``<agent>-alltag.yaml`` im privaten Ordner der Messung
(ausserhalb des Repos, weil er Termintitel und Story-Inhalte enthaelt). Als
Erwartung steht dort, was der Agent tatsaechlich getan hat. War das falsch,
traegst du in der Datei ein, was richtig gewesen waere; erst dann misst der
Fall etwas.
"""

import sys
from datetime import datetime
from pathlib import Path

import yaml

from local_agents import evals, status_agent
from local_agents.eval_suites import AGENTS, _key
from local_agents.meeting_agent import role_in
from local_agents.meeting_types import OTHER_TYPE, keyword_hint
from local_agents.profile import CUSTOMER, INTERNAL, Profile, ProfileError, load_profile
from local_agents.store import IN_PROGRESS, OPEN, Store

MAX_NAME_CHARS = 40


class CaptureError(ValueError):
    """Der Fall laesst sich nicht festhalten (nichts gefunden, falsche Angabe)."""


def _name(now: datetime, text: str) -> str:
    short = " ".join(text.split())[:MAX_NAME_CHARS]
    return f"Alltag {now:%d.%m.%Y %H:%M:%S}: {short}"


def scrum_case(store: Store, profile: Profile, now: datetime, text: str = "") -> dict:
    """Eine Antwort an den Scrum Master samt dem, was er dazu vermerkt hat."""
    notes = store.story_notes()
    if text:
        wanted = text.strip()
    elif notes:
        wanted = max(notes, key=lambda note: note.id).raw
    else:
        raise CaptureError("Es gibt noch keine Bemerkung; gib den Wortlaut an.")
    recorded = [note for note in notes if note.raw == wanted]
    today = now.date()
    if recorded:
        # Nur die Bemerkungen der juengsten gleichlautenden Antwort.
        latest = max(note.created_at for note in recorded)
        recorded = [note for note in recorded if note.created_at == latest]
        today = datetime.fromisoformat(latest).date()
    folder = next((context.stories_dir for context in profile.contexts if context.stories_dir), "")
    setup: dict = {"heute": today}
    setup.update({"stories_dir": folder} if folder else {"stories": []})
    expected = [
        {"story": _key(note.story), "daily": note.daily_date or False, "enthaelt": []} for note in recorded
    ]
    return {
        "name": _name(now, wanted),
        "inputs": {"umgebung": setup, "eingabe": wanted},
        "evaluators": [{"Bemerkungen": {"erwartet": expected}} if expected else "Bemerkungen"],
    }


def feedback_case(store: Store, now: datetime, text: str) -> dict:
    """Eine Antwort auf den Plan, mit den jetzt offenen Aufgaben als Plan."""
    if not text.strip():
        raise CaptureError("Gib den Wortlaut der Antwort an.")
    tasks = [{"titel": task.title, "faellig": task.due_date} for task in store.tasks((OPEN, IN_PROGRESS))]
    return {
        "name": _name(now, text),
        "inputs": {"umgebung": {"heute": now.date(), "aufgaben": tasks}, "eingabe": text.strip()},
        "evaluators": [{"Aufgaben": {"raus": [], "neu": []}}],
    }


def status_case(store: Store, now: datetime) -> dict:
    """Die offenen Aufgaben zum jetzigen Zeitpunkt; erwartet wird die Aufgabe, die der Code waehlt."""
    items = status_agent.next_tasks(store, now)
    tasks = sorted(items, key=lambda item: item.task.id)
    setup = {
        "jetzt": now.replace(microsecond=0),
        "aufgaben": [
            {"titel": item.task.title, "faellig": item.task.due_date}
            | ({"termin": item.event.start} if item.event else {})
            for item in tasks
        ],
    }
    chosen = status_agent.next_task(store, now)
    position = next((n for n, item in enumerate(tasks, 1) if chosen and item.task.id == chosen.task.id), None)
    return {
        "name": _name(now, f"{len(tasks)} offene Aufgaben"),
        "inputs": {"umgebung": setup},
        "evaluators": [{"Status": {"naechste": position or "keine", "nennt": [], "nennt_nicht": []}}],
    }


def termin_case(store: Store, profile: Profile, now: datetime, task_id: int) -> dict:
    """Der Termin hinter einer Aufgabe; erwartet wird die bisher erkannte Terminart."""
    task = next((task for task in store.tasks() if task.id == task_id), None)
    if task is None or not task.event_id:
        raise CaptureError(f"Aufgabe #{task_id} gibt es nicht oder sie hat keinen Termin.")
    event = next((e for e in store.events(datetime.min.date(), datetime.max.date()) if e.id == task.event_id), None)
    if event is None:
        raise CaptureError(f"Der Termin zu Aufgabe #{task_id} ist nicht mehr gespeichert.")
    kind = next((c.kind for c in profile.contexts if c.id == event.context), INTERNAL)
    customer = next((c for c in profile.contexts if c.kind == CUSTOMER), None)
    internal = next((c for c in profile.contexts if c.kind == INTERNAL), None)
    brief = store.brief(task.id)
    hint = keyword_hint(event.title)
    return {
        "name": _name(now, event.title),
        "inputs": {
            "umgebung": {
                "rolle_intern": role_in(profile, internal.id) if internal else profile.role,
                "rolle_kunde": role_in(profile, customer.id) if customer else profile.role,
                "kunde": customer.label if customer else "Kunde",
            },
            "titel": event.title,
            "beginn": event.start,
            "minuten": int((event.end - event.start).total_seconds() // 60),
            "eingeladene": event.attendees,
            "kontext": "kunde" if kind == CUSTOMER else "intern",
        },
        "evaluators": [{"Terminart": {"art": brief.meeting_type if brief else (hint.id if hint else OTHER_TYPE)}}],
    }


def stories_case(now: datetime, path: str) -> dict:
    """Eine Datei mit Kuerzeln: Erkennt das Modell die Stories auch ohne die Kuerzel?"""
    file = Path(path).expanduser().resolve()
    if not file.is_file():
        raise CaptureError(f"{path}: Datei nicht gefunden.")
    return {
        "name": _name(now, file.name),
        "inputs": {"datei": str(file), "ohne_kuerzel": True},
        "evaluators": [{"Stories": {"wie_regel": True}}],
    }


def case_text(case: dict) -> str:
    return yaml.safe_dump([case], allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000)


def append_case(agent: str, case: dict, folder: Path) -> Path:
    """Haengt den Fall an ``<agent>-alltag.yaml`` an und prueft, dass die Datei danach laedt."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{agent}-alltag.yaml"
    before = path.read_text(encoding="utf-8") if path.exists() else None
    head = before
    if head is None:
        head = (
            f"# yaml-language-server: $schema={evals.schema_path(agent)}\n"
            "# Echte Fälle aus dem Alltag, festgehalten mit \"python -m local_agents.evals merke\".\n"
            "# Unter evaluators steht zunächst, was der Agent getan hat. Bitte prüfen und\n"
            "# eintragen, was richtig gewesen wäre.\n"
            f"name: {agent}-alltag\n"
            "cases:\n"
        )
    path.write_text(head.rstrip("\n") + "\n" + case_text(case), encoding="utf-8")
    try:
        evals.load_cases([folder])
    except evals.CaseError:
        # Die Datei soll nie kaputt zurueckbleiben.
        if before is None:
            path.unlink()
        else:
            path.write_text(before, encoding="utf-8")
        raise
    return path


def main(args: list[str]) -> int:
    if not args or args[0] not in AGENTS or len(args) > 2:
        print(__doc__, file=sys.stderr)
        return 2
    agent, argument = args[0], (args[1] if len(args) == 2 else "")
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    store, now = Store(), datetime.now()
    try:
        if agent == "scrum_master":
            case = scrum_case(store, profile, now, argument)
        elif agent == "rueckmeldung":
            case = feedback_case(store, now, argument)
        elif agent == "status":
            case = status_case(store, now)
        elif agent == "termin":
            if not argument.lstrip("#").isdigit():
                raise CaptureError("Gib die Nummer der Aufgabe an, etwa: merke termin 12")
            case = termin_case(store, profile, now, int(argument.lstrip("#")))
        else:
            case = stories_case(now, argument)
        path = append_case(agent, case, evals.private_dir())
    except (CaptureError, evals.CaseError) as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        store.close()
    print(f"Fall festgehalten in {path}:\n")
    print(case_text(case))
    print(
        "Unter evaluators steht, was der Agent getan hat. War das falsch, trag dort ein, "
        "was richtig gewesen wäre."
    )
    return 0

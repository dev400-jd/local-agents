"""Asana-Aufgaben abfragen: Listen und Mengen aus dem Spiegel.

Aufruf:
  python -m local_agents.asana_tasks                       offene Aufgaben, die dir zugewiesen sind
  python -m local_agents.asana_tasks "Was ist diese Woche fällig?"
  python -m local_agents.asana_tasks "Was hat sich seit Montag geändert?"
  python -m local_agents.asana_tasks "Wo warte ich auf andere?"
  python -m local_agents.asana_tasks "Wie viele offene Aufgaben gibt es im Projekt Vertrieb?"

Gelesen wird der Asana-Spiegel (asana_mirror.py), nicht Asana selbst; die
Antwort nennt, wie alt er ist. Der Ablauf:

1. Uebersetzen (Modell): Das Modell macht aus der Frage einen Filter mit
   festen Feldern: wem zugewiesen, Stand, Faelligkeit, Aenderung, Projekt,
   Person, Stichworte. Zeitangaben gibt es in den Worten des Nutzers wieder.
2. Pruefen und rechnen (Code): Der Code ergaenzt, was die Frage woertlich
   nennt und das Modell ausgelassen hat ("muss ich", "Projekt X"), rechnet die
   Zeitangaben in Tage um,
   und sucht das Projekt im Bestand. Geht eine Zeitangabe nicht auf, geht der
   Filter zurueck ans Modell; gibt es das Projekt nicht oder mehrfach, nennt
   die Antwort die Projekte zur Wahl.
3. Auflisten (Code): Der Code filtert, sortiert und schreibt die Liste. Das
   Modell bekommt die Aufgaben nicht zu sehen und formuliert nichts; es kann
   also keine Aufgabe erfinden, weglassen oder falsch zaehlen.

Ueber der Liste steht, wie der Filter verstanden wurde. Geschrieben wird
nichts, weder in den Spiegel noch nach Asana.
"""

import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior

from local_agents.agent import MODEL_SETTINGS, ollama_model, structured
from local_agents.asana_mirror import Mirror, MirroredTask, age_text, mirror_path
from local_agents.dates import DAY_WORDS, mentioned_dates
from local_agents.holidays import WEEKDAYS
from local_agents.profile import ProfileError, load_profile

AGENT_NAME = "asana_aufgaben"
MAX_LISTED = 40
MAX_COMMENT_CHARS = 110
STALE_AFTER = timedelta(hours=24)
DEFAULT_QUESTION = "Welche offenen Aufgaben sind mir zugewiesen?"

ME, OTHERS, ANYONE = "mir", "anderen", "egal"
OPEN, DONE, ANY = "offen", "erledigt", "egal"


class TaskFilter(BaseModel):
    """Der Filter, den das Modell aus der Frage macht."""

    wem: Literal["mir", "anderen", "egal"] = Field(
        ANYONE,
        description="Wem die Aufgaben zugewiesen sind: mir (dem Nutzer), anderen (er wartet auf jemanden), egal.",
    )
    stand: Literal["offen", "erledigt", "egal"] = Field(OPEN, description="Ohne Angabe: offen.")
    faellig: str = Field(
        "",
        description="Zeitangabe zur Fälligkeit in den Worten des Nutzers, etwa „heute“, „diese Woche“, "
        "„bis Freitag“, „überfällig“. Leer, wenn die Frage keine nennt.",
    )
    geaendert: str = Field(
        "",
        description="Zeitangabe zur letzten Änderung in den Worten des Nutzers, etwa „seit Montag“, "
        "„letzte 3 Tage“, „gestern“. Leer, wenn die Frage nicht nach Änderungen fragt.",
    )
    projekt: str = Field("", description="Name des Projekts, wenn die Frage eines nennt; sonst leer.")
    person: str = Field("", description="Name einer zuständigen Person, wenn die Frage eine nennt; sonst leer.")
    stichworte: str = Field("", description="Worte, die im Titel der Aufgabe stehen sollen; sonst leer.")
    nur_anzahl: bool = Field(False, description="True, wenn nur nach der Anzahl gefragt ist („wie viele“).")


@dataclass(frozen=True)
class Period:
    """Ein Zeitraum in ganzen Tagen; None heisst offen nach dieser Seite."""

    start: date | None
    end: date | None
    label: str


class NotUnderstood(ValueError):
    """Eine Angabe des Filters laesst sich nicht aufloesen; der Text sagt, welche und warum."""


class UnknownProject(NotUnderstood):
    """Das genannte Projekt gibt es nicht oder mehrfach. Das klaert der Nutzer, nicht das Modell."""


# --- Zeitangaben


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _month(day: date, shift: int) -> tuple[date, date]:
    index = day.year * 12 + day.month - 1 + shift
    first = date(index // 12, index % 12 + 1, 1)
    following = date((index + 1) // 12, (index + 1) % 12 + 1, 1)
    return first, following - timedelta(days=1)


def _one_day(words: str, today: date, past: bool) -> date | None:
    """Der eine Tag, den die Worte nennen. ``past``: Ein Wochentag meint den letzten, nicht den naechsten."""
    lowered = words.casefold()
    for word, days in (("vorgestern", -2), ("gestern", -1)):
        if re.search(rf"(?<!\w){word}(?!\w)", lowered):
            return today + timedelta(days=days)
    if past:
        for number, name in enumerate(DAY_WORDS):
            if re.search(rf"(?<!\w){name}s?(?!\w)", lowered):
                return today - timedelta(days=(today.weekday() - number) % 7)
        match = re.search(r"(?<!\d)(\d{1,2})\.\s?(\d{1,2})\.(?:(\d{4}))?", lowered)
        if match:
            try:
                found = date(int(match.group(3) or today.year), int(match.group(2)), int(match.group(1)))
            except ValueError:
                return None
            return found.replace(year=found.year - 1) if found > today and not match.group(3) else found
    found = mentioned_dates(words, today)
    return found[0] if found else None


def period(words: str, today: date, past: bool = False) -> Period | None:
    """Rechnet eine Zeitangabe in einen Zeitraum um; None bei leerer Angabe.

    ``past`` gilt fuer Aenderungen: "Montag" meint dann den letzten Montag.
    Wirft ``NotUnderstood``, wenn die Worte keine Zeitangabe ergeben.
    """
    lowered = " ".join(words.casefold().split())
    if not lowered:
        return None
    if re.search(r"überfällig|ueberfaellig|verspätet|zu spät", lowered):
        return Period(None, today - timedelta(days=1), "überfällig")
    count = re.search(r"(letzte[nr]?|vergangene[nr]?|nächste[nr]?|kommende[nr]?)\s+(\d+)\s+(tag|woche)", lowered)
    if count:
        days = int(count.group(2)) * (7 if count.group(3) == "woche" else 1)
        if count.group(1).startswith(("letzte", "vergangene")):
            return Period(today - timedelta(days=days), today, _span(today - timedelta(days=days), today))
        return Period(today, today + timedelta(days=days), _span(today, today + timedelta(days=days)))
    for pattern, shift in ((r"(diese[rn]?|laufende[rn]?|aktuelle[rn]?)", 0), (r"(nächste[rn]?|kommende[rn]?)", 1), (r"(letzte[rn]?|vergangene[rn]?|vorige[rn]?)", -1)):
        if re.search(pattern + r"\s+woche", lowered):
            start = _monday(today) + timedelta(weeks=shift)
            return Period(start, start + timedelta(days=6), _span(start, start + timedelta(days=6)))
        if re.search(pattern.replace("[rn]", "[rnsm]") + r"\s+monat", lowered):
            start, end = _month(today, shift)
            return Period(start, end, _span(start, end))
    since = re.search(r"\b(seit|ab)\b(.*)", lowered)
    if since:
        # "seit" blickt immer zurueck; "ab Montag" bei einer Faelligkeit meint den kommenden.
        day = _one_day(since.group(2), today, past or since.group(1) == "seit")
        if day is not None:
            return Period(day, None, f"seit {_day(day)}")
    until = re.search(r"\b(bis|vor)\b(.*)", lowered)
    if until:
        day = _one_day(until.group(2), today, past=False)
        if day is not None:
            return Period(None, day, f"bis {_day(day)}")
    day = _one_day(lowered, today, past)
    if day is not None:
        return Period(day, day, _day(day))
    raise NotUnderstood(
        f"Die Zeitangabe „{words}“ verstehe ich nicht. Möglich sind etwa: heute, morgen, gestern, ein Wochentag, "
        "ein Datum (24.11.), diese/nächste/letzte Woche, diesen Monat, letzte 3 Tage, seit Montag, bis Freitag, überfällig."
    )


def _day(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()]} {day:%d.%m.}"


def _span(start: date, end: date) -> str:
    return f"{start:%d.%m.}–{end:%d.%m.%Y}"


# --- Filtern


def _words(text: str) -> list[str]:
    return [word for word in re.findall(r"\w+", text.casefold()) if len(word) >= 3 and word not in ("projekt", "das", "der", "die", "und")]


def resolve_project(asked: str, projects: list[str]) -> str:
    """Das Projekt aus dem Bestand, das gemeint ist. Wirft ``NotUnderstood`` bei keinem oder mehreren."""
    wanted = asked.casefold().strip()
    exact = [name for name in projects if name.casefold() == wanted]
    if exact:
        return exact[0]
    words = _words(asked)
    found = [
        name
        for name in projects
        if wanted in name.casefold() or (words and all(any(part.startswith(word) for part in _words(name)) for word in words))
    ]
    if len(found) == 1:
        return found[0]
    listing = "; ".join(sorted(found or projects))
    problem = "passt auf mehrere Projekte" if found else "gibt es im Spiegel nicht"
    raise UnknownProject(f"Das Projekt „{asked}“ {problem}. Zur Wahl: {listing}.")


def due_day(task: MirroredTask) -> date | None:
    if not task.due:
        return None
    if "T" in task.due:
        return datetime.fromisoformat(task.due.replace("Z", "+00:00")).astimezone().date()
    return date.fromisoformat(task.due)


def modified_day(task: MirroredTask) -> date | None:
    """Tag der letzten Aenderung: das Aenderungsdatum der Aufgabe oder der letzte Kommentar, was spaeter ist."""
    stamps = [task.modified_at] if task.modified_at else []
    moments = [datetime.fromisoformat(stamp.replace("Z", "+00:00")) for stamp in stamps]
    moments += [comment.created_at for comment in task.comments[-1:]]
    return max(moments).astimezone().date() if moments else None


@dataclass
class QueryResult:
    tasks: list[MirroredTask]
    description: str  # wie der Filter verstanden wurde
    changed: bool = False  # nach Aenderungen gefragt: letzten Kommentar zeigen
    count_only: bool = False


def run(wanted: TaskFilter, tasks: list[MirroredTask], today: date) -> QueryResult:
    """Wendet den Filter an. Wirft ``NotUnderstood``, wenn Zeitangabe oder Projekt nicht aufgehen."""
    due = period(wanted.faellig, today)
    changed = period(wanted.geaendert, today, past=True)
    projects = sorted({project for task in tasks for project, _ in task.projects})
    project = resolve_project(wanted.projekt, projects) if wanted.projekt.strip() else ""
    person = wanted.person.casefold().strip()
    words = _words(wanted.stichworte)
    found = []
    for task in tasks:
        if wanted.wem == ME and not task.mine:
            continue
        if wanted.wem == OTHERS and (task.mine or not task.assignee):
            continue
        if wanted.stand == OPEN and task.completed or wanted.stand == DONE and not task.completed:
            continue
        if due is not None:
            day = due_day(task)
            if day is None or (due.start and day < due.start) or (due.end and day > due.end):
                continue
        if changed is not None:
            day = modified_day(task)
            if day is None or (changed.start and day < changed.start) or (changed.end and day > changed.end):
                continue
        if project and project not in {name for name, _ in task.projects}:
            continue
        if person and person not in task.assignee.casefold():
            continue
        if words and not all(any(part.startswith(word) for part in _words(task.name)) for word in words):
            continue
        found.append(task)
    if changed is not None:
        found.sort(key=lambda task: (modified_day(task) or date.min, task.name), reverse=True)
    else:
        found.sort(key=lambda task: (task.completed, due_day(task) is None, due_day(task) or date.max, task.name.casefold()))
    parts = [{OPEN: "offene", DONE: "erledigte", ANY: "alle"}[wanted.stand] + " Aufgaben"]
    if wanted.wem == ME:
        parts.append("dir zugewiesen")
    elif wanted.wem == OTHERS:
        parts.append("anderen zugewiesen")
    if person:
        parts.append(f"zuständig: {wanted.person.strip()}")
    if project:
        parts.append(f"Projekt „{project}“")
    if due is not None:
        parts.append(due.label if due.label == "überfällig" else f"fällig {due.label}")
    if changed is not None:
        parts.append(f"geändert {changed.label}")
    if words:
        parts.append(f"Titel enthält: {', '.join(words)}")
    return QueryResult(found, ", ".join(parts), changed is not None, wanted.nur_anzahl)


# --- Ausgabe


def task_lines(task: MirroredTask, today: date, changed: bool = False) -> list[str]:
    parts = [task.name + (" (erledigt)" if task.completed else "")]
    day = due_day(task)
    if day is not None:
        late = (today - day).days
        overdue = f", überfällig seit {late} Tag{'en' if late != 1 else ''}" if late > 0 and not task.completed else ""
        parts.append(f"fällig {_day(day)}{overdue}")
    if task.projects:
        project, section = task.projects[0]
        parts.append(project + (f" / {section}" if section else ""))
    if not task.mine and task.assignee:
        parts.append(f"zuständig: {task.assignee}")
    lines = ["- " + " · ".join(parts)]
    if changed and task.comments:
        last = task.comments[-1]
        text = " ".join(last.text.split())
        if len(text) > MAX_COMMENT_CHARS:
            text = text[:MAX_COMMENT_CHARS].rsplit(" ", 1)[0] + " …"
        lines.append(f"  letzter Kommentar {last.created_at.astimezone():%d.%m.} {last.author}: {text}")
    if task.url:
        lines.append(f"  {task.url}")
    return lines


def result_lines(result: QueryResult, today: date, synced: datetime | None, now: datetime) -> list[str]:
    count = len(result.tasks)
    lines = [f"{count} Aufgabe{'n' if count != 1 else ''} ({result.description})."]
    if not result.count_only:
        for task in result.tasks[:MAX_LISTED]:
            lines += task_lines(task, today, result.changed)
        if count > MAX_LISTED:
            lines.append(f"… und {count - MAX_LISTED} weitere.")
    stale = synced is None or now - synced >= STALE_AFTER
    lines.append(
        f"Stand: Asana-Spiegel, {age_text(synced, now)}."
        + (" Auffrischen: python -m local_agents.asana_mirror" if stale else "")
    )
    return lines


# --- Agent: Frage -> Filter


@dataclass
class TaskDeps:
    tasks: list[MirroredTask]
    today: date
    projects: list[str] = field(default_factory=list)
    question: str = ""


# Was die Frage woertlich sagt, entscheidet der Code; das Modell uebersieht es gelegentlich.
MINE_WORDS = re.compile(r"\b(meine[mnrs]?|bei mir|mir zugewiesen|(muss|soll|habe?|kann) ich)\b")
NAMED_PROJECT = re.compile(r"\b[Pp]rojekts?\s+[„\"']?([\w-]+(?:\s+[A-ZÄÖÜ][\w-]*)*)")


def adjusted(question: str, wanted: TaskFilter) -> TaskFilter:
    """Ergaenzt im Filter, was die Frage ausdruecklich nennt und das Modell ausgelassen hat."""
    changes: dict[str, str] = {}
    if wanted.wem == ANYONE and not wanted.person and MINE_WORDS.search(question.lower()):
        changes["wem"] = ME
    named = NAMED_PROJECT.search(question)
    if not wanted.projekt.strip() and named:
        # Auch ein Projekt, das es nicht gibt: Dann meldet die Antwort das, statt alles zu zeigen.
        changes["projekt"] = named.group(1)
    return wanted.model_copy(update=changes) if changes else wanted


task_agent = Agent(deps_type=TaskDeps, output_type=structured(TaskFilter), model_settings=MODEL_SETTINGS, retries=3)


@task_agent.instructions
def instructions(ctx: RunContext[TaskDeps]) -> str:
    projects = "; ".join(ctx.deps.projects) or "(keine)"
    return (
        "Du übersetzt eine Frage zu Asana-Aufgaben in einen Filter. Du beantwortest die Frage nicht "
        "und siehst die Aufgaben nicht; der Code wendet den Filter an. Regeln:\n"
        "- Fülle nur, was die Frage nennt. Alles andere bleibt beim Standard.\n"
        "- wem: „mir“ bei „meine Aufgaben“, „bei mir“, „was muss ich tun“, „was muss ich erledigen“. "
        "„anderen“ bei „auf wen warte ich“, „was liegt bei anderen“. Sonst „egal“.\n"
        "- stand: „erledigt“ nur bei „erledigt“, „abgeschlossen“, „fertig“. „egal“ bei „alle, auch erledigte“ "
        "und bei Fragen nach Änderungen. Sonst „offen“.\n"
        "- faellig und geaendert: Gib die Zeitangabe in den Worten des Nutzers wieder (etwa „diese Woche“, "
        "„bis Freitag“, „seit Montag“). Rechne kein Datum aus. „überfällig“ gehört in faellig.\n"
        "- Fragt der Nutzer ohne Zeitangabe nach Neuem („was gibt es Neues“, „Neuigkeiten“, „was tut sich“), "
        "dann geaendert = „letzte 7 Tage“ und stand = „egal“.\n"
        "- projekt: nur wenn die Frage ein Projekt nennt. Steht es in dieser Liste, nimm den Namen von dort: "
        f"{projects}\n"
        "  Nennt die Frage ein Projekt, das nicht in der Liste steht, trage den Namen so ein, wie er in "
        "der Frage steht. Lass das Feld dann nicht leer.\n"
        "- person: nur ein Name, der in der Frage steht.\n"
        "- stichworte: nur Worte, die ausdrücklich im Titel stehen sollen (etwa „Aufgaben mit Angebot im Titel“). "
        "Allgemeine Worte wie „Aufgaben“ oder „Asana“ gehören nicht hinein.\n"
        "- nur_anzahl: true bei „wie viele“."
    )


@task_agent.output_validator
def resolvable(ctx: RunContext[TaskDeps], wanted: TaskFilter) -> TaskFilter:
    wanted = adjusted(ctx.deps.question, wanted)
    try:
        run(wanted, ctx.deps.tasks, ctx.deps.today)
    except UnknownProject:
        # Kein zweiter Versuch: Liesse das Modell das Projekt weg, kaeme eine Liste
        # ueber alle Projekte heraus, als haette der Nutzer keines genannt.
        return wanted
    except NotUnderstood as exc:
        raise ModelRetry(f"{exc} Gib die Zeitangabe so wieder, wie sie in der Frage steht.") from exc
    return wanted


def answer(question: str, mirror: Mirror, model, now: datetime) -> str:
    """Die Antwort auf eine Listenfrage: wie verstanden, die Aufgaben, das Alter des Spiegels."""
    tasks = mirror.tasks()
    if not tasks:
        return "Der Asana-Spiegel ist leer. Zuerst abgleichen: python -m local_agents.asana_mirror"
    today = now.astimezone().date()
    projects = sorted({project for task in tasks for project, _ in task.projects})
    try:
        wanted = task_agent.run_sync(question, deps=TaskDeps(tasks, today, projects, question), model=model).output
    except UnexpectedModelBehavior:
        return (
            "Die Frage konnte ich nicht in einen Filter übersetzen. Versuch es mit einer klaren Angabe, etwa: "
            "„meine offenen Aufgaben, fällig diese Woche“ oder „geändert seit Montag im Projekt …“."
        )
    try:
        result = run(wanted, tasks, today)
    except UnknownProject as exc:
        return str(exc)
    return "\n".join(result_lines(result, today, mirror.last_sync(), now))


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print(__doc__, file=sys.stderr)
        return 2
    if not mirror_path().is_file():
        print("Es gibt noch keinen Asana-Spiegel. Zuerst: python -m local_agents.asana_mirror", file=sys.stderr)
        return 1
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    mirror = Mirror()
    try:
        question = argv[1] if len(argv) == 2 else DEFAULT_QUESTION
        print(answer(question, mirror, ollama_model(profile.model_for(AGENT_NAME)), datetime.now().astimezone()))
        return 0
    finally:
        mirror.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))

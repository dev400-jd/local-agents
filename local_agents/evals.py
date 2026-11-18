"""Messung: laesst Agenten gegen feste Faelle mit bekannter richtiger Antwort laufen.

Aufruf:
  python -m local_agents.evals                       alle Agenten mit Faellen
  python -m local_agents.evals scrum_master          nur dieser Agent
  python -m local_agents.evals --laeufe 3            jeden Fall dreimal (Standard 5)
  python -m local_agents.evals --modell NAME         anderes Ollama-Modell
  python -m local_agents.evals --fall Daily          nur Faelle mit "Daily" im Namen
  python -m local_agents.evals --liste               Faelle zeigen, nichts ausfuehren
  python -m local_agents.evals --schema              Schema-Dateien der Falldateien neu schreiben

Braucht ``pydantic-evals`` (``pip install -e '.[evals]'``). Das Paket liefert
das Dateiformat der Faelle, das wiederholte Ausfuehren und die Fortschritts-
anzeige; Pruefer, Kurzbericht und Verlauf stehen hier.

Anders als die Tests in ``tests/`` (geskriptetes Modell, pruefen den Code) laeuft
hier das echte lokale Modell. Weil es nicht jedes Mal gleich antwortet, laeuft
jeder Fall mehrfach; das Ergebnis ist eine Quote. Geprueft wird mit Code am
Endzustand (welche Bemerkung steht bei welcher Story, welche Aufgabe ist raus),
nicht mit einem zweiten Modell.

Faelle stehen als YAML-Dateien an zwei Orten; der Dateiname beginnt mit dem
Namen des Agenten (``scrum_master.yaml``, ``scrum_master-alltag.yaml``):

- ``evals/`` im Repo: erfundene Faelle.
- ``~/.local/share/local-agents/evals/`` (oder ``LOCAL_AGENTS_EVALS``): echte
  Faelle aus dem Alltag. Sie enthalten Termintitel und Story-Inhalte und
  bleiben deshalb ausserhalb des Repos.

Jeder Messlauf wird in ``ergebnisse.jsonl`` im zweiten Ordner festgehalten; die
Ausgabe nennt, was sich gegenueber dem letzten Lauf mit demselben Modell
geaendert hat. Aufbau der Falldateien: README, Abschnitt "Messen".
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext

from local_agents import feedback, scrum_master
from local_agents.agent import MODEL_NAME, OUTPUT_MODE, ollama_model
from local_agents.profile import Profile, ProfileError, load_profile
from local_agents.store import IN_PROGRESS, NOTE_OPEN, NOTE_REMOVED, OPEN, REJECTED, Store
from local_agents.stories import STORY_HEADING, Story, load_features

REPO_CASES = Path(__file__).resolve().parent.parent / "evals"
DEFAULT_PRIVATE_DIR = Path.home() / ".local" / "share" / "local-agents" / "evals"
RESULTS_NAME = "ergebnisse.jsonl"
# Drei Laeufe verdecken Schwankungen, die fuenf zeigen (README, Abschnitt "Stand").
DEFAULT_RUNS = 5
MAX_SHOWN_FAILURES = 3
MAX_SAID_CHARS = 300
# Ein haengender Modellaufruf zaehlt nach so vielen Sekunden als gescheiterter
# Lauf, statt die ganze Messung aufzuhalten.
TASK_TIMEOUT = 120.0


class CaseError(ValueError):
    """Eine Falldatei ist nicht lesbar oder unvollstaendig."""


class Strict(BaseModel):
    """Unbekannte Schluessel sind ein Fehler, damit Tippfehler in Falldateien auffallen."""

    model_config = ConfigDict(extra="forbid")


@dataclass
class CaseResult:
    name: str
    runs: int = 0
    passed: int = 0
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)  # je gescheitertem Lauf eine Zeile

    @property
    def mark(self) -> str:
        return "✓" if self.passed == self.runs else ("✗" if self.passed == 0 else "~")


def private_dir() -> Path:
    return Path(os.environ.get("LOCAL_AGENTS_EVALS", DEFAULT_PRIVATE_DIR))


def model_said(messages: list) -> str:
    """Die letzte Antwort des Modells in einer Zeile: Text oder Argumente des Werkzeugaufrufs."""
    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            parts = []
            for part in message.parts:
                if isinstance(part, TextPart):
                    parts.append(part.content)
                elif isinstance(part, ToolCallPart):
                    parts.append(part.args if isinstance(part.args, str) else json.dumps(part.args, ensure_ascii=False))
            text = " ".join(" ".join(parts).split())
            return text[:MAX_SAID_CHARS] + ("…" if len(text) > MAX_SAID_CHARS else "")
    return ""


def _known_keys(items: list[dict], allowed: set[str], where: str) -> None:
    for item in items:
        unknown = sorted(set(item) - allowed)
        if unknown:
            raise ValueError(f"{where}: unbekannte Schluessel {unknown}. Erlaubt: {sorted(allowed)}.")


# --- Scrum Master


class StoryItem(Strict):
    kuerzel: str = ""
    titel: str
    feature: str = ""


class ScrumSetup(Strict):
    """Was der Scrum Master im Fall kennt: das Datum und die Stories."""

    heute: date
    stories: list[StoryItem] = []
    stories_dir: str = ""  # statt stories: ein Ordner mit Markdown-Dateien


class ScrumInputs(Strict):
    umgebung: ScrumSetup
    eingabe: str
    vorher: list[str] = []  # fruehere Antworten derselben Runde, ungeprueft


class NoteOut(BaseModel):
    story: str  # Kuerzel, sonst Name; leer = keiner Story zugeordnet
    daily: date | None
    text: str


class ScrumOutput(BaseModel):
    """Endzustand nach der Eingabe."""

    bemerkungen: list[NoteOut] = []
    entfernt: int = 0
    modell: str = ""  # letzte Antwort des Modells, woertlich


def _key(story_name: str) -> str:
    """Kuerzel einer gespeicherten Story, sonst ihr Name."""
    match = STORY_HEADING.match(story_name)
    return match.group(1) if match else story_name


def scrum_task(model) -> Callable[[ScrumInputs], ScrumOutput]:
    def run(inputs: ScrumInputs) -> ScrumOutput:
        setup = inputs.umgebung
        if setup.stories_dir:
            folder = Path(setup.stories_dir).expanduser()
            stories = [("", story) for feature in load_features(folder) for story in feature.stories]
        else:
            stories = [("", Story(item.kuerzel, item.titel, item.feature)) for item in setup.stories]
        today = setup.heute
        store = Store(":memory:")
        try:
            agent = scrum_master.ScrumMaster(
                store, Profile(), model, datetime(today.year, today.month, today.day, 9, 0), stories
            )
            for earlier in inputs.vorher:
                agent.respond(earlier, "messung")
            before = {note.id for note in store.story_notes()}
            with capture_run_messages() as messages:
                agent.respond(inputs.eingabe, "messung")
            new = [note for note in store.story_notes((NOTE_OPEN,)) if note.id not in before]
            return ScrumOutput(
                bemerkungen=[NoteOut(story=_key(n.story), daily=n.daily_date, text=n.text) for n in new],
                entfernt=len(store.story_notes((NOTE_REMOVED,))),
                modell=model_said(messages),
            )
        finally:
            store.close()

    return run


@dataclass
class Bemerkungen(Evaluator[ScrumInputs, ScrumOutput, Any]):
    """Prueft die neu vermerkten Bemerkungen und die Zahl der zurueckgenommenen.

    Je erwarteter Bemerkung: ``story`` (Kuerzel; "" = keiner zugeordnet),
    ``daily`` (Datum oder false = ohne Daily-Bezug; fehlt = nicht geprueft),
    ``enthaelt`` und ``enthaelt_nicht`` (Textstuecke).
    """

    erwartet: list[dict] = field(default_factory=list)
    entfernt: int = 0

    def __post_init__(self) -> None:
        _known_keys(self.erwartet, {"story", "daily", "enthaelt", "enthaelt_nicht"}, "Bemerkungen.erwartet")

    def evaluate(self, ctx: EvaluatorContext[ScrumInputs, ScrumOutput, Any]) -> EvaluationReason:
        problems = []
        new = list(ctx.output.bemerkungen)
        if len(new) != len(self.erwartet):
            problems.append(f"{len(self.erwartet)} Bemerkungen erwartet, {len(new)} vermerkt")
        if ctx.output.entfernt != self.entfernt:
            problems.append(f"{self.entfernt} entfernt erwartet, {ctx.output.entfernt} entfernt")
        for want in self.erwartet:
            story = want.get("story", "")
            note = next((n for n in new if n.story.casefold() == story.casefold()), None)
            label = story or "ohne Story"
            if note is None:
                got = ", ".join(n.story or "ohne Story" for n in new) or "nichts"
                problems.append(f"Bemerkung zu {label} fehlt (übrig: {got})")
                continue
            new.remove(note)
            if "daily" in want:
                daily = want["daily"] or None
                if note.daily != daily:
                    problems.append(f"{label}: Daily {daily or 'keins'} erwartet, {note.daily or 'keins'} vermerkt")
            text = note.text.casefold()
            for part in want.get("enthaelt", []):
                if part.casefold() not in text:
                    problems.append(f"{label}: „{part}“ fehlt im Text")
            for part in want.get("enthaelt_nicht", []):
                if part.casefold() in text:
                    problems.append(f"{label}: „{part}“ gehört nicht in den Text")
        return EvaluationReason(value=not problems, reason="; ".join(problems))


# --- Rueckmeldung auf den Plan


class TaskItem(Strict):
    titel: str
    faellig: date


class FeedbackSetup(Strict):
    """Der Plan im Fall: Die Aufgaben bekommen die Nummern 1, 2, 3 ... in dieser Reihenfolge."""

    heute: date
    aufgaben: list[TaskItem] = []


class FeedbackInputs(Strict):
    umgebung: FeedbackSetup
    eingabe: str


class NewTaskOut(BaseModel):
    titel: str
    faellig: date


class FeedbackOutput(BaseModel):
    """Endzustand nach der Eingabe."""

    raus: list[int] = []
    neu: list[NewTaskOut] = []
    unklar: bool = False
    modell: str = ""


def feedback_task(model) -> Callable[[FeedbackInputs], FeedbackOutput]:
    def run(inputs: FeedbackInputs) -> FeedbackOutput:
        store = Store(":memory:")
        try:
            for item in inputs.umgebung.aufgaben:
                store.add_task("vorbereitung", item.titel, "intern", item.faellig, "messung")
            before = {task.id for task in store.tasks()}
            with capture_run_messages() as messages:
                outcome = feedback.handle(inputs.eingabe, store, Profile(), inputs.umgebung.heute, model)
            tasks = store.tasks()
            return FeedbackOutput(
                raus=sorted(task.id for task in tasks if task.status == REJECTED),
                neu=[
                    NewTaskOut(titel=task.title, faellig=task.due_date)
                    for task in tasks
                    if task.id not in before and task.status in (OPEN, IN_PROGRESS)
                ],
                unklar=any(line.startswith("- nicht verstanden") for line in outcome.lines),
                modell=model_said(messages),
            )
        finally:
            store.close()

    return run


@dataclass
class Aufgaben(Evaluator[FeedbackInputs, FeedbackOutput, Any]):
    """Prueft, welche Aufgaben herausgenommen und welche ergaenzt wurden.

    ``raus``: genau diese Nummern. Je neuer Aufgabe ``titel_enthaelt`` und
    optional ``faellig``. ``unklar``: ob der Agent nachfragt (fehlt = nicht geprueft).
    """

    raus: list[int] = field(default_factory=list)
    neu: list[dict] = field(default_factory=list)
    unklar: bool | None = None

    def __post_init__(self) -> None:
        _known_keys(self.neu, {"titel_enthaelt", "faellig"}, "Aufgaben.neu")

    def evaluate(self, ctx: EvaluatorContext[FeedbackInputs, FeedbackOutput, Any]) -> EvaluationReason:
        problems = []
        output = ctx.output
        if output.raus != sorted(self.raus):
            problems.append(f"raus erwartet {sorted(self.raus)}, herausgenommen {output.raus}")
        added = list(output.neu)
        if len(added) != len(self.neu):
            got = ", ".join(task.titel for task in added) or "nichts"
            problems.append(f"{len(self.neu)} neue Aufgaben erwartet, {len(added)} angelegt ({got})")
        for want in self.neu:
            part = want.get("titel_enthaelt", "")
            task = next((t for t in added if part.casefold() in t.titel.casefold()), None)
            if task is None:
                problems.append(f"neue Aufgabe mit „{part}“ fehlt")
                continue
            added.remove(task)
            if "faellig" in want and task.faellig != want["faellig"]:
                problems.append(f"„{part}“: fällig {want['faellig']} erwartet, {task.faellig} angelegt")
        if self.unklar is not None and output.unklar != self.unklar:
            problems.append("Rückfrage erwartet, keine gestellt" if self.unklar else "unnötige Rückfrage")
        return EvaluationReason(value=not problems, reason="; ".join(problems))


# --- Agenten und ihre Falldateien


@dataclass(frozen=True)
class Suite:
    """Was zur Messung eines Agenten gehoert."""

    dataset_type: Any  # Dataset[Eingabe, Ausgabe, Any]
    evaluators: tuple[type, ...]
    task: Callable[[object], Callable]  # Modell -> Aufgabe fuer pydantic-evals


AGENTS: dict[str, Suite] = {
    scrum_master.AGENT_NAME: Suite(Dataset[ScrumInputs, ScrumOutput, Any], (Bemerkungen,), scrum_task),
    feedback.AGENT_NAME: Suite(Dataset[FeedbackInputs, FeedbackOutput, Any], (Aufgaben,), feedback_task),
}


def schema_path(agent: str, folder: Path = REPO_CASES) -> Path:
    return folder / f"{agent}_schema.json"


def schema_text(agent: str) -> str:
    """JSON-Schema der Falldateien eines Agenten, fuer die Pruefung im Editor."""
    suite = AGENTS[agent]
    schema = suite.dataset_type.model_json_schema_with_evaluators(suite.evaluators)
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def load_cases(folders: list[Path]) -> dict[str, list[Case]]:
    """Alle Faelle der Ordner, je Agent. Der Dateiname beginnt mit dem Namen des Agenten."""
    cases: dict[str, list[Case]] = {}
    for folder in folders:
        for path in sorted(folder.glob("*.y*ml")) if folder.is_dir() else []:
            agent = next((name for name in AGENTS if path.name.startswith(name)), None)
            if agent is None:
                raise CaseError(
                    f"{path}: Der Dateiname muss mit einem Agenten beginnen: {', '.join(AGENTS)}."
                )
            suite = AGENTS[agent]
            try:
                dataset = suite.dataset_type.from_file(path, custom_evaluator_types=suite.evaluators)
            except ExceptionGroup as group:  # Fehler beim Anlegen der Pruefer
                details = "; ".join(str(exc) for exc in group.exceptions)
                raise CaseError(f"{path}: {details}") from group
            except Exception as exc:  # nicht lesbar, kein YAML, passt nicht zum Schema
                raise CaseError(f"{path}: {exc}") from exc
            for case in dataset.cases:
                if not case.name:
                    raise CaseError(f"{path}: Jeder Fall braucht einen Namen.")
                if not case.evaluators:
                    raise CaseError(f"{path}: Fall „{case.name}“ hat keinen Prüfer (evaluators).")
            cases.setdefault(agent, []).extend(dataset.cases)
    for agent, found in cases.items():
        names = [case.name for case in found]
        double = sorted({name for name in names if names.count(name) > 1})
        if double:
            raise CaseError(f"Fallnamen doppelt bei {agent}: {', '.join(double)}.")
    return cases


# --- Ausfuehren und Auswerten


def run_cases(agent: str, cases: list[Case], model, runs: int, progress: bool = False) -> list[CaseResult]:
    """Laesst die Faelle ``runs``-mal laufen und fasst je Fall zusammen."""
    suite = AGENTS[agent]
    dataset = suite.dataset_type(name=agent, cases=cases)
    report = dataset.evaluate_sync(
        suite.task(model),
        repeat=runs,
        max_concurrency=1,  # ein lokales Modell rechnet nicht schneller, wenn man es parallel fragt
        progress=progress,
        task_timeout=TASK_TIMEOUT,
    )
    results = {case.name: CaseResult(case.name) for case in cases}

    def number(result: CaseResult) -> int:
        result.runs += 1
        return result.runs

    for run in report.cases:
        result = results[run.source_case_name or run.name]
        lauf = number(result)
        result.seconds += run.task_duration
        problems = [a.reason or a.name for a in run.assertions.values() if not a.value]
        problems += [f"Prüfer {f.name}: {f.error_message}" for f in run.evaluator_failures]
        if problems:
            said = run.output.modell
            result.failures.append(
                f"Lauf {lauf}: {'; '.join(problems)}" + (f"\n          Modell: {said}" if said else "")
            )
        else:
            result.passed += 1
    for failure in report.failures:  # die Aufgabe selbst ist gescheitert: Fehler oder Zeitlimit
        result = results[failure.source_case_name or failure.name]
        lauf = number(result)
        result.failures.append(f"Lauf {lauf}: Fehler: {' '.join(failure.error_message.split())[:200]}")
    return list(results.values())


def code_state() -> str:
    """Kurzer Commit-Stand des Repos, mit "+" bei ungespeicherten Aenderungen."""
    folder = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=folder, capture_output=True, text=True, timeout=10
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=folder, capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return commit + ("+" if commit and dirty else "")


def record(agent: str, model_name: str, results: list[CaseResult], when: datetime, state: str) -> dict:
    return {
        "zeit": when.isoformat(timespec="seconds"),
        "agent": agent,
        "modell": model_name,
        "ausgabe": OUTPUT_MODE,
        "stand": state,
        "faelle": {
            r.name: {"bestanden": r.passed, "laeufe": r.runs, "sekunden": round(r.seconds, 1)}
            for r in results
        },
    }


def last_record(path: Path, agent: str, model_name: str) -> dict | None:
    """Der juengste gespeicherte Messlauf dieses Agenten mit diesem Modell und dieser Ausgabeart."""
    found = None
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            same_output = entry.get("ausgabe", "tool") == OUTPUT_MODE
            if entry.get("agent") == agent and entry.get("modell") == model_name and same_output:
                found = entry
    return found


def save_record(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def compare(previous: dict | None, results: list[CaseResult]) -> list[str]:
    """Was sich gegenueber dem letzten Messlauf geaendert hat."""
    if previous is None:
        return ["Erster Messlauf mit diesem Modell und dieser Ausgabeart, kein Vergleich."]
    old = previous.get("faelle", {})

    def rate(entry: dict) -> float:
        return entry["bestanden"] / entry["laeufe"] if entry.get("laeufe") else 0.0

    better, worse, new = [], [], []
    for result in results:
        if result.name not in old:
            new.append(result.name)
            continue
        before, now = rate(old[result.name]), result.passed / result.runs
        change = f"{result.name} ({old[result.name]['bestanden']}/{old[result.name]['laeufe']} → {result.passed}/{result.runs})"
        if now > before:
            better.append(change)
        elif now < before:
            worse.append(change)
    when = datetime.fromisoformat(previous["zeit"])
    lines = [f"Gegenüber dem letzten Lauf ({when:%d.%m.%Y %H:%M}, Stand {previous.get('stand') or 'unbekannt'}):"]
    for label, names in (("schlechter", worse), ("besser", better), ("neue Fälle", new)):
        lines += [f"  {label}: {name}" for name in names]
    if len(lines) == 1:
        lines.append("  keine Änderung")
    return lines


def report(agent: str, model_name: str, results: list[CaseResult], state: str) -> list[str]:
    runs = results[0].runs if results else 0
    lines = [
        f"{agent} · {model_name} · Ausgabe {OUTPUT_MODE} · {runs} Läufe je Fall · Stand {state or 'unbekannt'}"
    ]
    for result in results:
        seconds = result.seconds / result.runs if result.runs else 0.0
        lines.append(f"  {result.mark} {result.passed}/{result.runs}  {seconds:5.1f} s  {result.name}")
        lines += [f"        {failure}" for failure in result.failures[:MAX_SHOWN_FAILURES]]
    always = sum(result.passed == result.runs for result in results)
    total = sum(result.runs for result in results)
    duration = int(sum(result.seconds for result in results))
    lines.append(
        f"Fälle immer bestanden: {always} von {len(results)} · "
        f"Läufe bestanden: {sum(result.passed for result in results)} von {total} · "
        f"Dauer {duration // 60}:{duration % 60:02d} min"
    )
    return lines


def evaluate(
    agent: str,
    cases: list[Case],
    model,
    model_name: str,
    runs: int,
    results_path: Path | None,
    out: Callable[[str], None] = print,
    now: Callable[[], datetime] = datetime.now,
    state: str = "",
    progress: bool = False,
) -> list[CaseResult]:
    """Misst einen Agenten, gibt den Bericht aus und haelt den Lauf fest."""
    results = run_cases(agent, cases, model, runs, progress)
    for line in report(agent, model_name, results, state):
        out(line)
    if results_path is not None:
        for line in compare(last_record(results_path, agent, model_name), results):
            out(line)
        save_record(results_path, record(agent, model_name, results, now(), state))
    return results


def main(argv: list[str]) -> int:
    args = argv[1:]
    options = {"--laeufe": str(DEFAULT_RUNS), "--modell": "", "--fall": ""}
    wanted: list[str] = []
    listing = False
    while args:
        arg = args.pop(0)
        if arg == "--liste":
            listing = True
        elif arg == "--schema":
            for agent in AGENTS:
                schema_path(agent).write_text(schema_text(agent), encoding="utf-8")
                print(f"geschrieben: {schema_path(agent)}")
            return 0
        elif arg in options and args:
            options[arg] = args.pop(0)
        elif arg in AGENTS:
            wanted.append(arg)
        else:
            print(__doc__, file=sys.stderr)
            return 2
    if not options["--laeufe"].isdigit() or int(options["--laeufe"]) < 1:
        print("--laeufe braucht eine ganze Zahl ab 1.", file=sys.stderr)
        return 2
    try:
        cases = load_cases([REPO_CASES, private_dir()])
    except CaseError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        profile = load_profile()
    except ProfileError:
        profile = Profile()
    word = options["--fall"].casefold()
    exit_code = 0
    for agent in wanted or sorted(cases):
        selected = [case for case in cases.get(agent, []) if word in case.name.casefold()]
        if not selected:
            print(f"{agent}: keine Fälle.")
            continue
        if listing:
            print(f"{agent}: {len(selected)} Fälle")
            for case in selected:
                print(f"  {case.name}")
            continue
        name = options["--modell"] or profile.model_for(agent)
        results = evaluate(
            agent,
            selected,
            ollama_model(name),
            name or MODEL_NAME,
            int(options["--laeufe"]),
            # Ein Teil-Lauf wuerde den Vergleich verfaelschen, also nicht festhalten.
            None if word else private_dir() / RESULTS_NAME,
            state=code_state(),
            progress=sys.stderr.isatty(),
        )
        print()
        if any(result.passed < result.runs for result in results):
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))

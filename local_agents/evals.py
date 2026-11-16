"""Messung: laesst Agenten gegen feste Faelle mit bekannter richtiger Antwort laufen.

Aufruf:
  python -m local_agents.evals                       alle Agenten mit Faellen
  python -m local_agents.evals scrum_master          nur dieser Agent
  python -m local_agents.evals --laeufe 5            jeden Fall fuenfmal (Standard 3)
  python -m local_agents.evals --modell NAME         anderes Ollama-Modell
  python -m local_agents.evals --fall Daily          nur Faelle mit "Daily" im Namen
  python -m local_agents.evals --liste               Faelle zeigen, nichts ausfuehren

Anders als die Tests in ``tests/`` (geskriptetes Modell, pruefen den Code) laeuft
hier das echte lokale Modell. Weil es nicht jedes Mal gleich antwortet, laeuft
jeder Fall mehrfach; das Ergebnis ist eine Quote. Geprueft wird mit Code am
Endzustand (welche Bemerkung steht bei welcher Story, welche Aufgabe ist raus),
nicht mit einem zweiten Modell.

Faelle stehen als TOML-Dateien an zwei Orten:

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
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from local_agents import feedback, scrum_master
from local_agents.agent import MODEL_NAME, ollama_model
from local_agents.profile import Profile, ProfileError, load_profile
from local_agents.store import IN_PROGRESS, NOTE_OPEN, NOTE_REMOVED, OPEN, REJECTED, Store
from local_agents.stories import STORY_HEADING, Story, load_features

REPO_CASES = Path(__file__).resolve().parent.parent / "evals"
DEFAULT_PRIVATE_DIR = Path.home() / ".local" / "share" / "local-agents" / "evals"
RESULTS_NAME = "ergebnisse.jsonl"
DEFAULT_RUNS = 3
MAX_SHOWN_FAILURES = 3


class CaseError(ValueError):
    """Eine Falldatei ist nicht lesbar oder unvollstaendig."""


@dataclass(frozen=True)
class Case:
    """Ein Fall: Eingabe und was danach gelten muss."""

    name: str
    data: dict  # der [[fall]]-Block
    setup: dict  # der Rest der Datei: Stories, Aufgaben, heute
    source: str  # Dateiname, fuer Fehlermeldungen


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


# --- Falldateien


def load_cases(folders: list[Path]) -> dict[str, list[Case]]:
    """Alle Faelle der Ordner, je Agent."""
    cases: dict[str, list[Case]] = {}
    for folder in folders:
        for path in sorted(folder.glob("*.toml")) if folder.is_dir() else []:
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise CaseError(f"{path}: {exc}") from exc
            agent = data.get("agent")
            if agent not in AGENTS:
                raise CaseError(f"{path}: agent = {agent!r} unbekannt. Erlaubt: {', '.join(AGENTS)}.")
            setup = {key: value for key, value in data.items() if key != "fall"}
            if not isinstance(setup.get("heute"), date):
                raise CaseError(f"{path}: heute fehlt (Datum, z. B. heute = 2026-11-16).")
            for block in data.get("fall", []):
                if not block.get("name") or "eingabe" not in block:
                    raise CaseError(f"{path}: Jeder [[fall]] braucht name und eingabe.")
                cases.setdefault(agent, []).append(Case(block["name"], block, setup, path.name))
    for agent, found in cases.items():
        names = [case.name for case in found]
        double = sorted({name for name in names if names.count(name) > 1})
        if double:
            raise CaseError(f"Fallnamen doppelt bei {agent}: {', '.join(double)}.")
    return cases


# --- Scrum Master


def _stories(setup: dict) -> list[tuple[str, Story]]:
    """Stories des Falls: aus einem Ordner (``stories_dir``) oder direkt aus der Datei."""
    if setup.get("stories_dir"):
        folder = Path(setup["stories_dir"]).expanduser()
        return [("", story) for feature in load_features(folder) for story in feature.stories]
    return [
        ("", Story(item.get("kuerzel", ""), item["titel"], item.get("feature", "")))
        for item in setup.get("story", [])
    ]


def _key(story_name: str) -> str:
    """Kuerzel einer gespeicherten Story, sonst ihr Name."""
    match = STORY_HEADING.match(story_name)
    return match.group(1) if match else story_name


def run_scrum_master(case: Case, model) -> list[str]:
    """Fuehrt den Fall aus; gibt zurueck, was nicht stimmt (leer = bestanden)."""
    today = case.setup["heute"]
    store = Store(":memory:")
    try:
        agent = scrum_master.ScrumMaster(
            store, Profile(), model, datetime(today.year, today.month, today.day, 9, 0), _stories(case.setup)
        )
        for earlier in case.data.get("vorher", []):
            agent.respond(earlier, "messung")
        before = {note.id for note in store.story_notes()}
        agent.respond(case.data["eingabe"], "messung")
        new = [note for note in store.story_notes((NOTE_OPEN,)) if note.id not in before]
        removed = len(store.story_notes((NOTE_REMOVED,)))
    finally:
        store.close()
    problems = []
    expected = case.data.get("erwartet", [])
    if len(new) != len(expected):
        problems.append(f"{len(expected)} Bemerkungen erwartet, {len(new)} vermerkt")
    if removed != case.data.get("entfernt", 0):
        problems.append(f"{case.data.get('entfernt', 0)} entfernt erwartet, {removed} entfernt")
    left = list(new)
    for want in expected:
        story = want.get("story", "")
        note = next((n for n in left if _key(n.story).casefold() == story.casefold()), None)
        label = story or "ohne Story"
        if note is None:
            got = ", ".join(_key(n.story) or "ohne Story" for n in left) or "nichts"
            problems.append(f"Bemerkung zu {label} fehlt (übrig: {got})")
            continue
        left.remove(note)
        if "daily" in want:
            daily = want["daily"] or None
            if note.daily_date != daily:
                problems.append(f"{label}: Daily {daily or 'keins'} erwartet, {note.daily_date or 'keins'} vermerkt")
        text = note.text.casefold()
        for part in want.get("enthaelt", []):
            if part.casefold() not in text:
                problems.append(f"{label}: „{part}“ fehlt im Text")
        for part in want.get("enthaelt_nicht", []):
            if part.casefold() in text:
                problems.append(f"{label}: „{part}“ gehört nicht in den Text")
    return problems


# --- Rueckmeldung auf den Plan


def run_feedback(case: Case, model) -> list[str]:
    today = case.setup["heute"]
    store = Store(":memory:")
    try:
        for item in case.setup.get("aufgabe", []):
            store.add_task("vorbereitung", item["titel"], "intern", item["faellig"], "messung")
        before = {task.id for task in store.tasks()}
        outcome = feedback.handle(case.data["eingabe"], store, Profile(), today, model)
        tasks = store.tasks()
    finally:
        store.close()
    problems = []
    removed = sorted(task.id for task in tasks if task.status == REJECTED)
    if removed != sorted(case.data.get("raus", [])):
        problems.append(f"raus erwartet {sorted(case.data.get('raus', []))}, herausgenommen {removed}")
    added = [task for task in tasks if task.id not in before]
    expected = case.data.get("neu", [])
    if len(added) != len(expected):
        got = ", ".join(task.title for task in added) or "nichts"
        problems.append(f"{len(expected)} neue Aufgaben erwartet, {len(added)} angelegt ({got})")
    left = [task for task in added if task.status in (OPEN, IN_PROGRESS)]
    for want in expected:
        part = want.get("titel_enthaelt", "")
        task = next((t for t in left if part.casefold() in t.title.casefold()), None)
        if task is None:
            problems.append(f"neue Aufgabe mit „{part}“ fehlt")
            continue
        left.remove(task)
        if "faellig" in want and task.due_date != want["faellig"]:
            problems.append(f"„{part}“: fällig {want['faellig']} erwartet, {task.due_date} angelegt")
    if "unklar" in case.data:
        unclear = any(line.startswith("- nicht verstanden") for line in outcome.lines)
        if unclear != case.data["unklar"]:
            problems.append("Rückfrage erwartet, keine gestellt" if case.data["unklar"] else "unnötige Rückfrage")
    return problems


AGENTS: dict[str, Callable[[Case, object], list[str]]] = {
    scrum_master.AGENT_NAME: run_scrum_master,
    feedback.AGENT_NAME: run_feedback,
}


# --- Ausfuehren und Auswerten


def run_case(case: Case, runner: Callable[[Case, object], list[str]], model, runs: int) -> CaseResult:
    result = CaseResult(case.name)
    for number in range(1, runs + 1):
        started = time.monotonic()
        try:
            problems = runner(case, model)
        except Exception as exc:  # ein Fehler des Modells ist ein gescheiterter Lauf
            problems = [f"Fehler: {type(exc).__name__}: {str(exc)[:200]}"]
        result.seconds += time.monotonic() - started
        result.runs += 1
        if problems:
            result.failures.append(f"Lauf {number}: {'; '.join(problems)}")
        else:
            result.passed += 1
    return result


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
        "stand": state,
        "faelle": {
            r.name: {"bestanden": r.passed, "laeufe": r.runs, "sekunden": round(r.seconds, 1)}
            for r in results
        },
    }


def last_record(path: Path, agent: str, model_name: str) -> dict | None:
    """Der juengste gespeicherte Messlauf dieses Agenten mit diesem Modell."""
    found = None
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("agent") == agent and entry.get("modell") == model_name:
                found = entry
    return found


def save_record(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def compare(previous: dict | None, results: list[CaseResult]) -> list[str]:
    """Was sich gegenueber dem letzten Messlauf geaendert hat."""
    if previous is None:
        return ["Erster Messlauf mit diesem Modell, kein Vergleich."]
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
    lines = [f"{agent} · {model_name} · {runs} Läufe je Fall · Stand {state or 'unbekannt'}"]
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
) -> list[CaseResult]:
    """Misst einen Agenten, gibt den Bericht aus und haelt den Lauf fest."""
    results = []
    for case in cases:
        result = run_case(case, AGENTS[agent], model, runs)
        results.append(result)
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
                print(f"  {case.name}  ({case.source})")
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
        )
        print()
        if any(result.passed < result.runs for result in results):
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))

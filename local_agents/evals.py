"""Messung: laesst Agenten gegen feste Faelle mit bekannter richtiger Antwort laufen.

Aufruf:
  python -m local_agents.evals                       alle Agenten mit Faellen
  python -m local_agents.evals scrum_master status   nur diese Agenten (scrum_master,
                                                     rueckmeldung, status, termin, stories)
  python -m local_agents.evals --laeufe 3            jeden Fall dreimal (Standard 5)
  python -m local_agents.evals --modell NAME         anderes Ollama-Modell
  python -m local_agents.evals --fall Daily          nur Faelle mit "Daily" im Namen
  python -m local_agents.evals --liste               Faelle zeigen, nichts ausfuehren
  python -m local_agents.evals --schema              Schema-Dateien der Falldateien neu schreiben
  python -m local_agents.evals merke AGENT [...]     einen echten Fall festhalten (eval_capture.py)

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
from datetime import datetime
from pathlib import Path

from pydantic_evals import Case

from local_agents.agent import MODEL_NAME, OUTPUT_MODE, ollama_model
from local_agents.eval_suites import AGENTS
from local_agents.profile import Profile, ProfileError, load_profile

REPO_CASES = Path(__file__).resolve().parent.parent / "evals"
DEFAULT_PRIVATE_DIR = Path.home() / ".local" / "share" / "local-agents" / "evals"
RESULTS_NAME = "ergebnisse.jsonl"
# Drei Laeufe verdecken Schwankungen, die fuenf zeigen (README, Abschnitt "Stand").
DEFAULT_RUNS = 5
MAX_SHOWN_FAILURES = 3
# Fehlermeldungen, die einen Ausfall des Modellservers anzeigen (abgestuerzt,
# nicht erreichbar, Zeitlimit), nicht eine falsche Antwort des Modells.
OUTAGE_MARKS = ("ModelServerError", "ModelAPIError", "ModelHTTPError", "ConnectionError", "ConnectError", "TimeoutError", "timed out")


class CaseError(ValueError):
    """Eine Falldatei ist nicht lesbar oder unvollstaendig."""


@dataclass
class CaseResult:
    name: str
    runs: int = 0
    passed: int = 0
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)  # je gescheitertem Lauf eine Zeile
    outages: int = 0  # Laeufe, in denen der Modellserver nicht geantwortet hat

    @property
    def mark(self) -> str:
        return "✓" if self.passed == self.runs else ("✗" if self.passed == 0 else "~")


def private_dir() -> Path:
    return Path(os.environ.get("LOCAL_AGENTS_EVALS", DEFAULT_PRIVATE_DIR))


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
        task_timeout=suite.timeout,
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
        message = " ".join(failure.error_message.split())
        result.failures.append(f"Lauf {lauf}: Fehler: {message[:200]}")
        result.outages += any(mark in message for mark in OUTAGE_MARKS)
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
    outages = sum(result.outages for result in results)
    if outages:
        # Sonst gaelte ein abgestuerztes Modell als schlechtes Modell und der
        # naechste Lauf als Verbesserung.
        out(
            f"Messung unvollständig: In {outages} Läufen hat der Modellserver nicht geantwortet "
            "(abgestürzt, nicht erreichbar oder Zeitlimit). Das Ergebnis wird nicht festgehalten."
        )
    elif results_path is not None:
        for line in compare(last_record(results_path, agent, model_name), results):
            out(line)
        save_record(results_path, record(agent, model_name, results, now(), state))
    return results


def main(argv: list[str]) -> int:
    args = argv[1:]
    if args[:1] == ["merke"]:
        from local_agents import eval_capture  # erst hier: das Modul braucht dieses

        return eval_capture.main(args[1:])
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

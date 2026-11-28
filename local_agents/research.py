"""Gepaarter Ollama-Vergleich auf den versionierten Agentenfällen."""
import argparse
import fcntl
import hashlib
import json
import platform
import random
import sqlite3
import subprocess
import sys
import time
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from urllib.request import urlopen

from pydantic_ai.models.wrapper import WrapperModel

from local_agents.agent import MODEL_SETTINGS, OLLAMA_BASE_URL, OUTPUT_MODE, ollama_model
from local_agents.evals import AGENTS, REPO_CASES, code_state, load_cases

ROOT = REPO_CASES.parent


def api(path):
    with urlopen(OLLAMA_BASE_URL.removesuffix("/").removesuffix("/v1") + path, timeout=10) as response:
        return json.load(response)


def previous_cases(folders):
    excluded = set()
    for folder in folders:
        uri = (folder / "run.sqlite").resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            config, = db.execute("SELECT config FROM protocol").fetchone()
        for _, _, agent, case in json.loads(config)["schedule"]:
            excluded.add((agent, case))
    return excluded


def plan(args, cases, excluded=()):
    rng = random.Random(args.seed)
    selected = {}
    for agent in sorted(args.agent or cases):
        if AGENTS[agent].uses_model:
            names = sorted(case.name for case in cases[agent] if (agent, case.name) not in excluded)
            if not names:
                raise SystemExit(f"Keine unbenutzten Fälle in {agent}.")
            selected[agent] = rng.sample(names, min(args.cases or len(names), len(names)))
    blocks = []
    order = rng.sample(args.models, 2)
    for rep in range(args.repeats):
        tasks = [(agent, name) for agent, names in selected.items() for name in names]
        rng.shuffle(tasks)
        for model in order[::1 if rep % 2 == 0 else -1]:
            blocks.extend([rep + 1, model, agent, name] for agent, name in tasks)
    return blocks


def workload_files():
    paths = sorted(REPO_CASES.rglob("*")) + sorted((ROOT / "local_agents").glob("*.py"))
    paths += sorted((ROOT / "tests/data").glob("*"))
    for path in paths:
        if path.is_file() and path.name != "research_report.py":
            yield path.relative_to(ROOT), path.read_bytes()


def fingerprint(files):
    digest = hashlib.sha256()
    for path, contents in files:
        digest.update(str(path).encode() + b"\0" + hashlib.sha256(contents).digest())
    return digest.hexdigest()


def archive_workload(folder, files):
    # Das Archiv wird erst nach dem vollständigen Schreiben sichtbar.
    temp = folder / "sources.zip.tmp"
    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, contents in files:
            archive.writestr(str(path), contents)
    temp.replace(folder / "sources.zip")


class MeasuredModel(WrapperModel):
    """Protokolliert die vom Provider gemeldete Nutzung je Agentenaufruf."""

    def __init__(self, model):
        super().__init__(model)
        self.calls = []

    async def request(self, messages, model_settings, model_request_parameters):
        settings = model_settings or {}
        call = {"settings": {k: settings[k] for k in (
            "max_tokens", "openai_reasoning_effort", "temperature", "top_p", "seed"
        ) if k in settings}}
        self.calls.append(call)
        start = time.monotonic()
        try:
            response = await super().request(messages, model_settings, model_request_parameters)
        except Exception as exc:
            call["error"] = type(exc).__name__
            raise
        finally:
            call["seconds"] = time.monotonic() - start
        call.update(input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens,
                    parts=[part.part_kind for part in response.parts])
        return response


def trial(model, agent, name):
    case = next(c for c in load_cases([REPO_CASES])[agent] if c.name == name)
    suite = AGENTS[agent]
    measured = MeasuredModel(ollama_model(model))
    start = time.monotonic()
    report = suite.dataset_type(name=agent, cases=[case]).evaluate_sync(
        suite.task(measured), progress=False, max_concurrency=1
    )
    seconds = time.monotonic() - start
    if report.failures:
        message = report.failures[0].error_message
        status = "model_error" if "UnexpectedModelBehavior" in message else "execution_error"
        return dict(status=status, detail=message[:1200], seconds=seconds, calls=measured.calls)
    run, = report.cases
    checks = {key: dict(pass_check=a.value, reason=a.reason) for key, a in run.assertions.items()}
    errors = [f.error_message for f in run.evaluator_failures]
    status = "pass" if checks and all(c["pass_check"] for c in checks.values()) else "fail"
    return dict(status="grader_error" if errors or not checks else status, checks=checks,
                errors=errors, output=run.output.model_dump(mode="json"), seconds=seconds, calls=measured.calls)


def measure(args):
    excluded = previous_cases(args.exclude_run)
    blocks = plan(args, load_cases([REPO_CASES]), excluded)
    models = {m["name"]: m for m in api("/api/tags")["models"]}
    missing = set(args.models) - models.keys()
    if missing:
        raise SystemExit(f"Modell fehlt in Ollama: {sorted(missing)}")
    files = list(workload_files())
    protocol = dict(schema=2, excluded=[list(pair) for pair in sorted(excluded)], schedule=blocks, seed=args.seed, fingerprint=fingerprint(files),
                    models={m: models[m] for m in args.models}, baseline=args.models[0],
                    endpoint=OLLAMA_BASE_URL, ollama=api("/api/version"), output=OUTPUT_MODE,
                    settings=MODEL_SETTINGS, platform=platform.platform(), python=platform.python_version(),
                    dependencies={p: version(p) for p in ("pydantic-ai-slim", "pydantic-evals", "openai")},
                    timeouts={a: AGENTS[a].timeout for _, _, a, _ in blocks})
    args.out.mkdir(parents=True, exist_ok=True)
    with (
        (args.out / "run.lock").open("a") as lock,
        closing(sqlite3.connect(args.out / "run.sqlite")) as db,
        tempfile.TemporaryDirectory(prefix="local-agents-eval-") as workspace,
    ):
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit("In diesem Ausgabeverzeichnis läuft bereits eine Messung.") from exc
        db.execute("CREATE TABLE IF NOT EXISTS protocol (config TEXT, started TEXT, revision TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS trials (id INTEGER PRIMARY KEY, result TEXT)")
        existing = db.execute("SELECT config FROM protocol").fetchone()
        if existing and json.loads(existing[0]) != protocol:
            raise SystemExit("Protokoll geändert. Bitte neues Ausgabeverzeichnis verwenden.")
        if not existing or not (args.out / "sources.zip").is_file():
            archive_workload(args.out, files)
        if not existing:
            db.execute("INSERT INTO protocol VALUES (?,?,?)", (json.dumps(protocol), datetime.now(timezone.utc).isoformat(), code_state()))
            db.commit()
        # Die Worker lesen die eingefrorenen Quellen auch bei Änderungen am Checkout.
        for path, contents in files:
            dest = Path(workspace) / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(contents)
        for i, (_, model, agent, name) in enumerate(blocks):
            if db.execute("SELECT 1 FROM trials WHERE id=?", (i,)).fetchone():
                continue
            # Eigener Prozess begrenzt auch blockierende synchrone Agentenaufrufe.
            tags = {entry["name"]: entry["digest"] for entry in api("/api/tags")["models"]}
            if tags.get(model) != models[model]["digest"]:
                raise SystemExit(f"Modellartefakt für {model} hat sich während der Messung geändert.")
            deadline = AGENTS[agent].timeout
            started = datetime.now(timezone.utc).isoformat()
            start = time.monotonic()
            try:
                proc = subprocess.run([sys.executable, "-m", "local_agents.research", "_trial", model, agent, name],
                                      cwd=workspace, capture_output=True, text=True, timeout=deadline)
                if proc.returncode:
                    raise RuntimeError(f"Worker für {agent}/{name} beendet ({proc.returncode}): {proc.stderr[-1200:]}")
                result = json.loads(proc.stdout.splitlines()[-1])
            except subprocess.TimeoutExpired:
                result = dict(status="timeout", seconds=deadline)
            result.update(started=started, wall_seconds=time.monotonic() - start)
            db.execute("INSERT INTO trials VALUES (?,?)", (i, json.dumps(result, ensure_ascii=False)))
            db.commit()
            print(f"{i+1}/{len(blocks)} {model} {agent} · {name} · {result['status']} ({result['seconds']:.1f}s)", flush=True)


def main():
    if sys.argv[1:2] == ["_trial"]:
        print(json.dumps(trial(*sys.argv[2:]), ensure_ascii=False))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("models", nargs=2, metavar="MODELL")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--agent", nargs="+", choices=[a for a in AGENTS if AGENTS[a].uses_model])
    parser.add_argument("--cases", type=int, help="Zufallsstichprobe je Disziplin (Standard: alle)")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--exclude-run", type=Path, action="append", default=[],
                        help="Fälle eines früheren Vergleichs ausschließen (wiederholbar)")
    parser.add_argument("--seed", type=int, default=20261128)
    args = parser.parse_args()
    if len(set(args.models)) != 2 or args.repeats < 1 or (args.cases is not None and args.cases < 1):
        parser.error("Zwei verschiedene Modelle und positive Fall- und Laufzahlen erforderlich")
    measure(args)


if __name__ == "__main__":
    main()

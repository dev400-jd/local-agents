"""Wertet einen gespeicherten Vergleich offline aus."""
import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from statistics import mean, median

import numpy as np


def summarize(protocol, results, started, revision):
    schedule = protocol["schedule"]
    baseline = protocol["baseline"]
    candidate, = set(protocol["models"]) - {baseline}
    groups = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for i, (_, model, agent, case) in enumerate(schedule):
        groups[agent][case][model].append(results.get(i))
    lines = ["# Modellvergleich", "", f"Referenz **{baseline}**, Kandidat **{candidate}**. "
             f"{len(results)}/{len(schedule)} Versuche erfasst. Beginn {started}.",
             "", f"Quellstand `{revision}`, Ollama {protocol['ollama']['version']}, "
             f"Ausgabeart `{protocol['output']}`, Stichproben-Seed {protocol['seed']}.", ""]
    for model in (baseline, candidate):
        metadata = protocol["models"][model]
        lines.append(f"- {model} · {metadata['details']['parameter_size']} · "
                     f"{metadata['details']['quantization_level']} · Digest `{metadata['digest']}`")
    if len(results) != len(schedule):
        return "\n".join(lines + ["", "Zwischenstand. Die gepaarte Auswertung folgt nach dem letzten Versuch.", ""])
    lines.extend(["", "| Disziplin | Fälle | " + baseline + " | " + candidate +
                  " | Δ Prozentpunkte | Median Sekunden¹ (Ref. / Kand.) |",
                  "|---|---:|---:|---:|---:|---:|"])
    effects = []
    agent_means = defaultdict(list)
    pairs = Counter()
    perfect = Counter()
    totals = Counter()
    status = defaultdict(Counter)
    for agent, cases in sorted(groups.items()):
        scores = defaultdict(list)
        latency = defaultdict(list)
        differences = []
        for models in cases.values():
            case_rates = {}
            for model, trials in models.items():
                flags = [trial["status"] == "pass" for trial in trials]
                scores[model].extend(flags)
                case_rates[model] = mean(flags)
                perfect[model] += all(flags)
                totals[model] += 1
                latency[model].extend(trial["seconds"] for trial in trials)
                status[model].update(trial["status"] for trial in trials)
            differences.append(case_rates[candidate] - case_rates[baseline])
            for ref, cand in zip(models[baseline], models[candidate], strict=True):
                pairs[ref["status"] == "pass", cand["status"] == "pass"] += 1
        effects.append(differences)
        a, b = scores[baseline], scores[candidate]
        agent_means[baseline].append(mean(a))
        agent_means[candidate].append(mean(b))
        lines.append(f"| {agent} | {len(cases)} | {sum(a)}/{len(a)} | {sum(b)}/{len(b)} | "
                     f"{100*mean(differences):+.1f} | {median(latency[baseline]):.1f} / {median(latency[candidate]):.1f} |")
    effect = mean(mean(values) for values in effects)
    lines.extend(["", f"**Primärmaß** · Differenz der Erfolgsquoten bei gleichem Gewicht je Disziplin "
                  f"und je Fall innerhalb der Disziplin (Kandidat minus Referenz) **{effect*100:+.1f} Prozentpunkte**."])
    lines.append(f"Disziplinmittel {baseline} {100*mean(agent_means[baseline]):.1f} %, "
                 f"{candidate} {100*mean(agent_means[candidate]):.1f} %.")
    lines.append(f"Gepaarte Versuche: beide erfolgreich {pairs[True, True]}, "
                 f"nur Referenz {pairs[True, False]}, nur Kandidat {pairs[False, True]}, "
                 f"beide gescheitert {pairs[False, False]}.")
    if all(len(values) >= 2 for values in effects):
        rng = np.random.default_rng(protocol["seed"])
        draws = [rng.choice(values, size=(10000, len(values)), replace=True).mean(axis=1) for values in effects]
        low, high = 100 * np.quantile(np.mean(draws, axis=0), [.025, .975])
        lines.append(f"Exploratives 95-%-Perzentilintervall [{low:+.1f}, {high:+.1f}] Prozentpunkte. "
                     "10.000 gepaarte Bootstrap-Stichproben ziehen ganze Fälle innerhalb jeder Disziplin. "
                     "Ähnliche Fallvarianten und kleine Stichproben begrenzen die Aussagekraft.")
    lines += ["", "### Wiederholbarkeit und Ausfälle", ""]
    for model in (baseline, candidate):
        counts = ", ".join(f"{k} {v}" for k, v in sorted(status[model].items()))
        lines.append(f"- {model} bestand {perfect[model]}/{totals[model]} Fälle in allen Wiederholungen. {counts}.")
    lines += ["", "¹ Gemessen wird die Zeit für Aufgabe und Bewertung einschließlich Modellladezeit, sofern diese anfällt. "
              "Ein Zeitlimit geht mit seiner vollen Dauer ein. Sekundenwerte enthalten bei regulärem Abschluss keinen Prozessstart.",
              "", "Alle vorgesehenen Versuche zählen im Nenner. Nur bestandene Fälle zählen im Zähler. "
              "Ausführungs- und Prüferfehler belasten damit die gemessene Systemleistung. "
              "Die Fallprüfer definieren Erfolg am beobachteten Ergebnis. Alle Aussagen gelten für diese Modellartefakte, "
              "Agenten, Budgets und synthetischen Fälle.", ""]
    lines += ["### Fälle mit unterschiedlicher Erfolgsquote", "",
              f"| Disziplin | Fall | {baseline} | {candidate} |",
              "|---|---|---:|---:|"]
    discordant = []
    for agent, cases in groups.items():
        for case, models in cases.items():
            ref, cand = ([trial["status"] == "pass" for trial in models[m]] for m in (baseline, candidate))
            if sum(ref) != sum(cand):
                name = case.replace("|", r"\|")
                discordant.append((agent, name, f"| {agent} | {name} | {sum(ref)}/{len(ref)} | {sum(cand)}/{len(cand)} |"))
    if discordant:
        lines.extend(line for _, _, line in sorted(discordant))
    else:
        lines.append("| – | Alle Fallquoten gleich | – | – |")
    return "\n".join(lines + [""])


def read_run(folder):
    uri = (folder / "run.sqlite").resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        with db:
            db.execute("BEGIN")
            config, started, revision = db.execute("SELECT config, started, revision FROM protocol").fetchone()
            results = {i: json.loads(raw) for i, raw in db.execute("SELECT id, result FROM trials")}
    protocol = json.loads(config)
    validate(protocol, results)
    return protocol, results, started, revision


def validate(protocol, results):
    schedule = protocol["schedule"]
    expected_models = set(protocol["models"])
    if len(expected_models) != 2 or protocol["baseline"] not in expected_models:
        raise ValueError("Das Protokoll braucht zwei Modelle und eine gültige Referenz.")
    by_task = defaultdict(list)
    for rep, model, agent, case in schedule:
        by_task[agent, case, rep].append(model)
    if not by_task or any(len(models) != 2 or set(models) != expected_models for models in by_task.values()):
        raise ValueError("Ungepaarte oder doppelte Versuche im Protokoll.")
    for i, row in results.items():
        if not 0 <= i < len(schedule):
            raise ValueError(f"Ergebnis {i} gehört zu keinem geplanten Versuch.")
        if row["status"] not in {"pass", "fail", "timeout", "model_error", "execution_error", "grader_error"}:
            raise ValueError(f"Unbekannter Status in Versuch {i}.")
        if not math.isfinite(row["seconds"]) or row["seconds"] < 0:
            raise ValueError(f"Ungültige Laufzeit in Versuch {i}.")


def export_trials(protocol, results):
    """Eine Zeile je geplantem Versuch, auch wenn sein Ergebnis noch fehlt."""
    for i, (rep, model, agent, case) in enumerate(protocol["schedule"]):
        yield dict(id=i, repetition=rep, model=model, digest=protocol["models"][model]["digest"],
                   agent=agent, case=case, **results.get(i, {"status": "pending"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--jsonl", action="store_true", help="Einzelversuche für Notebooks ausgeben")
    args = parser.parse_args()
    run = read_run(args.folder)
    if args.jsonl:
        for row in export_trials(*run[:2]):
            print(json.dumps(row, ensure_ascii=False))
    else:
        print(summarize(*run))

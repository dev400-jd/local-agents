"""Paarung, Abbruch und Gewichtung sind Teil des Messverfahrens."""
import io
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from types import SimpleNamespace

from pydantic_ai.models.test import TestModel

from local_agents.research import measure, plan, previous_cases, trial
from local_agents.research_report import export_trials, read_run, summarize


class ResearchTest(unittest.TestCase):
    def test_schedule_pairs_models_and_reverses_blocks(self):
        cases = {"status": [SimpleNamespace(name=str(i)) for i in range(9)]}
        args = SimpleNamespace(seed=17, agent=None, models=["a", "b"], cases=3, repeats=2)
        schedule = plan(args, cases)
        self.assertEqual(schedule, plan(args, cases))
        self.assertEqual(len(schedule), 12)
        self.assertEqual(schedule[0][1], schedule[-1][1])
        self.assertNotEqual(schedule[0][1], schedule[3][1])
        for start in (0, 6):
            self.assertEqual([r[2:] for r in schedule[start:start+3]],
                             [r[2:] for r in schedule[start+3:start+6]])

    def test_call_accounting_distinguishes_fallback_from_actual_request(self):
        with patch("local_agents.research.ollama_model", return_value=TestModel()):
            skipped = trial("fake", "asana_beschreibung", "Kein passendes Dokument: melden")
            called = trial("fake", "status", "Heute fällige Aufgabe")
        self.assertEqual(skipped["calls"], [])
        self.assertEqual(skipped["status"], "pass")
        self.assertTrue(called["calls"][0]["output_tokens"] > 0)
        self.assertEqual(called["calls"][0]["parts"], ["text"])

    def test_resume_preserves_timeout_and_rejects_changed_protocol(self):
        models = {"models": [{"name": m, "digest": m} for m in ("a", "b")]}
        def api(path):
            return models if path == "/api/tags" else {"version": "test"}

        with (
            TemporaryDirectory() as tmp,
            patch("local_agents.research.api", side_effect=api),
            patch("local_agents.research.platform.platform", return_value="test"),
            patch("local_agents.research.code_state", return_value="test"),
            redirect_stdout(io.StringIO()),
        ):
            args = SimpleNamespace(out=Path(tmp), models=["a", "b"], seed=2, cases=1,
                                   agent=["status"], repeats=1, exclude_run=[])
            with patch("local_agents.research.subprocess.run",
                       side_effect=[subprocess.TimeoutExpired("worker", 120), KeyboardInterrupt()]):
                with self.assertRaises(KeyboardInterrupt):
                    measure(args)
            protocol, results, *_ = read_run(Path(tmp))
            self.assertEqual([r["status"] for r in export_trials(protocol, results)], ["timeout", "pending"])
            response = subprocess.CompletedProcess([], 0, stdout='{"status":"pass","seconds":2}')
            with patch("local_agents.research.subprocess.run", return_value=response) as worker:
                measure(args)
                measure(args)
                worker.assert_called_once()
                args.repeats = 2
                with self.assertRaises(SystemExit):
                    measure(args)
            self.assertEqual(len(read_run(Path(tmp))[1]), 2)
            excluded = previous_cases([Path(tmp)])
            self.assertEqual(len(excluded), 1)
            case = next(iter(excluded))[1]
            fresh = plan(args, {"status": [SimpleNamespace(name=case), SimpleNamespace(name="neu")]}, excluded)
            self.assertEqual({row[3] for row in fresh}, {"neu"})
            self.assertTrue((Path(tmp) / "sources.zip").is_file())

    def test_macro_effect_and_timeout_denominator(self):
        schedule = [[1, m, agent, c] for agent, cases in [("small", ["1"]), ("large", ["2", "3", "4"])]
                    for c in cases for m in ("a", "b")]
        info = dict(details=dict(parameter_size="", quantization_level=""), digest="x")
        protocol = dict(schedule=schedule, models={"a": info, "b": info}, baseline="a",
                        seed=0, ollama={"version": "test"}, output="tool")
        results = {i: dict(seconds=1., status="timeout" if agent == "small" and m == "b" else "pass")
                   for i, (_, m, agent, _) in enumerate(schedule)}
        report = summarize(protocol, results, "test", "test")
        self.assertIn("-50.0 Prozentpunkte", report)
        self.assertIn("timeout 1", report)
        results.pop(0)
        partial = summarize(protocol, results, "test", "test")
        self.assertIn("7/8", partial)
        self.assertNotIn("Primärmaß", partial)

"""Tests fuer das Messprogramm: Falldateien, Pruefer, Auswertung und Vergleich."""

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents import evals

models.ALLOW_MODEL_REQUESTS = False


def answering(payload: dict) -> FunctionModel:
    """Modell, das immer dieselbe strukturierte Antwort gibt."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, payload)])

    return FunctionModel(respond)


SCRUM_SETUP = {
    "heute": date(2026, 11, 16),
    "story": [
        {"kuerzel": "QG-1", "titel": "Prüfregeln", "feature": "Import"},
        {"kuerzel": "QG-3", "titel": "Freigabe", "feature": "Import"},
    ],
}


def scrum_case(**data) -> evals.Case:
    return evals.Case(data.get("name", "Fall"), data, SCRUM_SETUP, "test.toml")


class TestScrumMasterGrader(unittest.TestCase):
    def test_matching_note_passes(self) -> None:
        case = scrum_case(
            eingabe="Bei QG-3 warten wir bis 24.11. auf die Freigabe",
            erwartet=[{"story": "QG-3", "daily": False, "enthaelt": ["24.11."]}],
        )
        model = answering({"remarks": [{"story": "QG-3", "text": "warten bis 24.11. auf die Freigabe"}]})
        self.assertEqual(evals.run_scrum_master(case, model), [])

    def test_wrong_story_daily_and_text_are_named(self) -> None:
        case = scrum_case(
            eingabe="Fürs Daily morgen: die Freigabe kommt am 24.11.",
            erwartet=[{"story": "QG-3", "daily": date(2026, 11, 18), "enthaelt": ["Freigabe"]}],
        )
        model = answering({"remarks": [{"story": "QG-1", "text": "kommt am 24.11."}]})
        self.assertEqual(
            evals.run_scrum_master(case, model), ["Bemerkung zu QG-3 fehlt (übrig: QG-1)"]
        )
        model = answering({"remarks": [{"story": "QG-3", "text": "kommt am 24.11."}]})
        self.assertEqual(
            evals.run_scrum_master(case, model),
            ["QG-3: Daily 2026-11-18 erwartet, 2026-11-17 vermerkt", "QG-3: „Freigabe“ fehlt im Text"],
        )

    def test_count_and_forbidden_text(self) -> None:
        case = scrum_case(
            eingabe="QG-1 ist fertig und QG-3 wartet",
            erwartet=[
                {"story": "QG-1", "enthaelt_nicht": ["wartet"]},
                {"story": "QG-3"},
            ],
        )
        model = answering({"remarks": [{"story": "QG-1", "text": "QG-1 ist fertig, der Rest wartet"}]})
        self.assertEqual(
            evals.run_scrum_master(case, model),
            [
                "2 Bemerkungen erwartet, 1 vermerkt",
                "QG-1: „wartet“ gehört nicht in den Text",
                "Bemerkung zu QG-3 fehlt (übrig: nichts)",
            ],
        )

    def test_removal_counts_removed_notes(self) -> None:
        model = answering({"remarks": [{"story": "QG-1", "text": "ist fertig"}]})
        case = scrum_case(vorher=["QG-1 ist fertig"], eingabe="entferne das wieder", entfernt=1)
        self.assertEqual(evals.run_scrum_master(case, model), [])
        case = scrum_case(vorher=["QG-1 ist fertig"], eingabe="nichts Neues", entfernt=1)
        self.assertEqual(evals.run_scrum_master(case, model), ["1 entfernt erwartet, 0 entfernt"])


FEEDBACK_SETUP = {
    "heute": date(2026, 11, 20),
    "aufgabe": [
        {"titel": "Vorbereitung: Daily", "faellig": date(2026, 11, 22)},
        {"titel": "Vorbereitung: Jour fixe", "faellig": date(2026, 11, 22)},
    ],
}


def feedback_case(**data) -> evals.Case:
    return evals.Case("Fall", data, FEEDBACK_SETUP, "test.toml")


class TestFeedbackGrader(unittest.TestCase):
    def test_removed_and_added_tasks_pass(self) -> None:
        case = feedback_case(
            eingabe="Daily raus, dafür bis Dienstag die Demo",
            raus=[1],
            neu=[{"titel_enthaelt": "Demo", "faellig": date(2026, 11, 24)}],
            unklar=False,
        )
        model = answering({"remove": [1], "add": [{"title": "Demo aufsetzen", "due": "2026-11-24"}]})
        self.assertEqual(evals.run_feedback(case, model), [])

    def test_differences_are_named(self) -> None:
        case = feedback_case(
            eingabe="Daily raus, dafür bis Dienstag die Demo",
            raus=[1],
            neu=[{"titel_enthaelt": "Demo", "faellig": date(2026, 11, 24)}],
            unklar=False,
        )
        model = answering(
            {"remove": [2], "add": [{"title": "Demo aufsetzen", "due": "2026-11-25"}], "unclear": "Dings"}
        )
        self.assertEqual(
            evals.run_feedback(case, model),
            [
                "raus erwartet [1], herausgenommen [2]",
                "„Demo“: fällig 2026-11-24 erwartet, 2026-11-25 angelegt",
                "unnötige Rückfrage",
            ],
        )

    def test_approval_needs_no_model(self) -> None:
        def fail(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise AssertionError("Modell gefragt")

        case = feedback_case(eingabe="ja passt so", raus=[])
        self.assertEqual(evals.run_feedback(case, FunctionModel(fail)), [])


class TestRunAndReport(unittest.TestCase):
    def test_run_case_counts_passes_and_keeps_failures(self) -> None:
        outcomes = iter([[], ["Story falsch"], RuntimeError("kein Modell")])

        def runner(case: evals.Case, model) -> list[str]:
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        result = evals.run_case(scrum_case(eingabe="x"), runner, None, 3)
        self.assertEqual((result.passed, result.runs, result.mark), (1, 3, "~"))
        self.assertEqual(
            result.failures, ["Lauf 2: Story falsch", "Lauf 3: Fehler: RuntimeError: kein Modell"]
        )

    def results(self, **passed: int) -> list[evals.CaseResult]:
        return [evals.CaseResult(name, runs=3, passed=count, seconds=6.0) for name, count in passed.items()]

    def test_report_summarises_cases_and_runs(self) -> None:
        results = self.results(a=3, b=1, c=0)
        results[1].failures = ["Lauf 1: Story falsch", "Lauf 3: Daily falsch"]
        lines = evals.report("scrum_master", "modell", results, "abc123")
        self.assertEqual(lines[0], "scrum_master · modell · 3 Läufe je Fall · Stand abc123")
        self.assertEqual(lines[1], "  ✓ 3/3    2.0 s  a")
        self.assertEqual(lines[2:5], ["  ~ 1/3    2.0 s  b", "        Lauf 1: Story falsch", "        Lauf 3: Daily falsch"])
        self.assertEqual(
            lines[-1], "Fälle immer bestanden: 1 von 3 · Läufe bestanden: 4 von 9 · Dauer 0:18 min"
        )

    def test_compare_names_better_worse_and_new_cases(self) -> None:
        when = datetime(2026, 11, 15, 10, 0)
        previous = evals.record("scrum_master", "modell", self.results(a=3, b=1, c=2), when, "alt")
        lines = evals.compare(previous, self.results(a=2, b=3, c=2, d=3))
        self.assertEqual(
            lines,
            [
                "Gegenüber dem letzten Lauf (15.11.2026 10:00, Stand alt):",
                "  schlechter: a (3/3 → 2/3)",
                "  besser: b (1/3 → 3/3)",
                "  neue Fälle: d",
            ],
        )
        self.assertEqual(evals.compare(previous, self.results(a=3, b=1, c=2))[1], "  keine Änderung")
        self.assertIn("Erster Messlauf", evals.compare(None, [])[0])

    def test_evaluate_saves_the_run_and_compares_with_the_same_model_only(self) -> None:
        model = answering({"remarks": [{"story": "QG-1", "text": "ist fertig"}]})
        cases = [scrum_case(name="ok", eingabe="QG-1 ist fertig", erwartet=[{"story": "QG-1"}])]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sub" / evals.RESULTS_NAME
            printed: list[str] = []
            for name in ("modell-a", "modell-b", "modell-a"):
                printed.clear()
                evals.evaluate(
                    "scrum_master", cases, model, name, 2, path, printed.append,
                    now=lambda: datetime(2026, 11, 16, 12, 0), state="abc",
                )
            entries = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([entry["modell"] for entry in entries], ["modell-a", "modell-b", "modell-a"])
            self.assertEqual(entries[0]["faelle"]["ok"]["bestanden"], 2)
            self.assertIn("Gegenüber dem letzten Lauf", printed[-2])
            self.assertEqual(evals.last_record(path, "scrum_master", "modell-c"), None)


class TestCaseFiles(unittest.TestCase):
    def test_repo_cases_load_and_cover_both_agents(self) -> None:
        cases = evals.load_cases([evals.REPO_CASES])
        self.assertEqual(sorted(cases), ["rueckmeldung", "scrum_master"])
        self.assertGreaterEqual(len(cases["scrum_master"]), 15)
        self.assertGreaterEqual(len(cases["rueckmeldung"]), 12)

    def write(self, folder: str, text: str) -> list[Path]:
        (Path(folder) / "fall.toml").write_text(text, encoding="utf-8")
        return [Path(folder)]

    def test_broken_files_are_reported_with_their_path(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            for text, message in (
                ('agent = "gibtsnicht"\nheute = 2026-11-16\n', "unbekannt"),
                ('agent = "scrum_master"\n', "heute fehlt"),
                ('agent = "scrum_master"\nheute = 2026-11-16\n[[fall]]\nname = "x"\n', "name und eingabe"),
                (
                    'agent = "scrum_master"\nheute = 2026-11-16\n'
                    '[[fall]]\nname = "x"\neingabe = "a"\n[[fall]]\nname = "x"\neingabe = "b"\n',
                    "doppelt",
                ),
                ("agent = ", "fall.toml"),
            ):
                with self.assertRaises(evals.CaseError) as raised:
                    evals.load_cases(self.write(folder, text))
                self.assertIn(message, str(raised.exception))

    def test_missing_folder_is_no_error(self) -> None:
        self.assertEqual(evals.load_cases([Path("/gibt/es/nicht")]), {})


if __name__ == "__main__":
    unittest.main()

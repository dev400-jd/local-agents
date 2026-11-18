"""Tests fuer das Messprogramm: Falldateien, Pruefer, Auswertung und Vergleich."""

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_evals import Case

from local_agents import evals
from local_agents.testing import structured_reply

models.ALLOW_MODEL_REQUESTS = False


def answering(payload: dict) -> FunctionModel:
    """Modell, das immer dieselbe strukturierte Antwort gibt."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return structured_reply(info, payload)

    return FunctionModel(respond)


SCRUM_SETUP = evals.ScrumSetup(
    heute=date(2026, 11, 16),
    stories=[
        evals.StoryItem(kuerzel="QG-1", titel="Prüfregeln", feature="Import"),
        evals.StoryItem(kuerzel="QG-3", titel="Freigabe", feature="Import"),
    ],
)


def scrum_case(name: str = "Fall", vorher: tuple[str, ...] = (), **check) -> Case:
    eingabe = check.pop("eingabe")
    return Case(
        name=name,
        inputs=evals.ScrumInputs(umgebung=SCRUM_SETUP, eingabe=eingabe, vorher=list(vorher)),
        evaluators=(evals.Bemerkungen(**check),),
    )


def problems(agent: str, case: Case, model) -> list[str]:
    """Die Zeilen der gescheiterten Laeufe eines einzelnen Durchgangs."""
    return evals.run_cases(agent, [case], model, 1)[0].failures


class TestScrumMasterGrader(unittest.TestCase):
    def test_matching_note_passes(self) -> None:
        case = scrum_case(
            eingabe="Bei QG-3 warten wir bis 24.11. auf die Freigabe",
            erwartet=[{"story": "QG-3", "daily": False, "enthaelt": ["24.11."]}],
        )
        model = answering({"remarks": [{"story": "QG-3", "text": "warten bis 24.11. auf die Freigabe"}]})
        self.assertEqual(problems("scrum_master", case, model), [])

    def test_wrong_story_daily_and_text_are_named_with_the_models_answer(self) -> None:
        case = scrum_case(
            eingabe="Fürs Daily morgen: die Freigabe kommt am 24.11.",
            erwartet=[{"story": "QG-3", "daily": date(2026, 11, 18), "enthaelt": ["Freigabe"]}],
        )
        model = answering({"remarks": [{"story": "QG-1", "text": "kommt am 24.11."}]})
        self.assertEqual(
            problems("scrum_master", case, model),
            [
                "Lauf 1: Bemerkung zu QG-3 fehlt (übrig: QG-1)\n"
                '          Modell: {"remarks": [{"story": "QG-1", "text": "kommt am 24.11."}]}'
            ],
        )
        model = answering({"remarks": [{"story": "QG-3", "text": "kommt am 24.11."}]})
        self.assertIn(
            "Lauf 1: QG-3: Daily 2026-11-18 erwartet, 2026-11-17 vermerkt; QG-3: „Freigabe“ fehlt im Text",
            problems("scrum_master", case, model)[0],
        )

    def test_count_and_forbidden_text(self) -> None:
        case = scrum_case(
            eingabe="QG-1 ist fertig und QG-3 wartet",
            erwartet=[{"story": "QG-1", "enthaelt_nicht": ["wartet"]}, {"story": "QG-3"}],
        )
        model = answering({"remarks": [{"story": "QG-1", "text": "QG-1 ist fertig, der Rest wartet"}]})
        self.assertIn(
            "2 Bemerkungen erwartet, 1 vermerkt; QG-1: „wartet“ gehört nicht in den Text; "
            "Bemerkung zu QG-3 fehlt (übrig: nichts)",
            problems("scrum_master", case, model)[0],
        )

    def test_removal_counts_removed_notes(self) -> None:
        model = answering({"remarks": [{"story": "QG-1", "text": "ist fertig"}]})
        case = scrum_case(vorher=("QG-1 ist fertig",), eingabe="entferne das wieder", entfernt=1)
        self.assertEqual(problems("scrum_master", case, model), [])
        case = scrum_case(vorher=("QG-1 ist fertig",), eingabe="nichts Neues", entfernt=1)
        self.assertEqual(problems("scrum_master", case, model), ["Lauf 1: 1 entfernt erwartet, 0 entfernt"])

    def test_unknown_key_in_expectation_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as raised:
            evals.Bemerkungen(erwartet=[{"story": "QG-1", "enthält": ["x"]}])
        self.assertIn("unbekannte Schluessel ['enthält']", str(raised.exception))


FEEDBACK_SETUP = evals.FeedbackSetup(
    heute=date(2026, 11, 20),
    aufgaben=[
        evals.TaskItem(titel="Vorbereitung: Daily", faellig=date(2026, 11, 22)),
        evals.TaskItem(titel="Vorbereitung: Jour fixe", faellig=date(2026, 11, 22)),
    ],
)


def feedback_case(eingabe: str, **check) -> Case:
    return Case(
        name="Fall",
        inputs=evals.FeedbackInputs(umgebung=FEEDBACK_SETUP, eingabe=eingabe),
        evaluators=(evals.Aufgaben(**check),),
    )


class TestFeedbackGrader(unittest.TestCase):
    def test_removed_and_added_tasks_pass(self) -> None:
        case = feedback_case(
            "Daily raus, dafür bis Dienstag die Demo",
            raus=[1],
            neu=[{"titel_enthaelt": "Demo", "faellig": date(2026, 11, 24)}],
            unklar=False,
        )
        model = answering({"remove": [1], "add": [{"title": "Demo aufsetzen", "due": "2026-11-24"}]})
        self.assertEqual(problems("rueckmeldung", case, model), [])

    def test_differences_are_named(self) -> None:
        case = feedback_case(
            "Daily oder Jour fixe raus, dafür bis Dienstag die Demo",
            raus=[1],
            neu=[{"titel_enthaelt": "Demo", "faellig": date(2026, 11, 24)}],
            unklar=False,
        )
        model = answering({"remove": [2], "add": [{"title": "Demo aufsetzen"}], "unclear": "Dings"})
        self.assertIn(
            "Lauf 1: raus erwartet [1], herausgenommen [2]; "
            "„Demo“: fällig 2026-11-24 erwartet, 2026-11-20 angelegt; unnötige Rückfrage",
            problems("rueckmeldung", case, model)[0],
        )

    def test_approval_needs_no_model(self) -> None:
        def fail(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise AssertionError("Modell gefragt")

        self.assertEqual(problems("rueckmeldung", feedback_case("ja passt so"), FunctionModel(fail)), [])


class TestRunAndReport(unittest.TestCase):
    def test_runs_are_repeated_and_a_broken_model_counts_as_failed_run(self) -> None:
        calls = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("kein Modell")
            return structured_reply(info, {"remarks": [{"story": "QG-1", "text": "ist fertig"}]})

        cases = [
            scrum_case(name="a", eingabe="QG-1 ist fertig", erwartet=[{"story": "QG-1"}]),
            scrum_case(name="b", eingabe="nichts Neues"),
        ]
        a, b = evals.run_cases("scrum_master", cases, FunctionModel(respond), 3)
        self.assertEqual((a.name, a.passed, a.runs, a.mark), ("a", 2, 3, "~"))
        self.assertEqual(len(a.failures), 1)
        self.assertIn("Fehler: RuntimeError: kein Modell", a.failures[0])
        self.assertEqual((b.passed, b.runs, b.mark), (3, 3, "✓"))

    def results(self, **passed: int) -> list[evals.CaseResult]:
        return [evals.CaseResult(name, runs=3, passed=count, seconds=6.0) for name, count in passed.items()]

    def test_report_summarises_cases_and_runs(self) -> None:
        results = self.results(a=3, b=1, c=0)
        results[1].failures = ["Lauf 1: Story falsch", "Lauf 3: Daily falsch"]
        lines = evals.report("scrum_master", "modell", results, "abc123")
        self.assertEqual(
            lines[0], f"scrum_master · modell · Ausgabe {evals.OUTPUT_MODE} · 3 Läufe je Fall · Stand abc123"
        )
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
            self.assertEqual(entries[0]["ausgabe"], evals.OUTPUT_MODE)
            self.assertIn("Gegenüber dem letzten Lauf", printed[-2])
            self.assertEqual(evals.last_record(path, "scrum_master", "modell-c"), None)


VALID = """name: probe
cases:
- name: eins
  inputs:
    umgebung: &umgebung
      heute: 2026-11-16
      stories:
      - {kuerzel: QG-1, titel: Prüfregeln}
    eingabe: "QG-1 ist fertig"
  evaluators:
  - Bemerkungen:
      erwartet:
      - {story: QG-1, daily: false}
- name: zwei
  inputs: {umgebung: *umgebung, eingabe: "nichts Neues"}
  evaluators:
  - Bemerkungen
"""


class TestCaseFiles(unittest.TestCase):
    def test_repo_cases_load_and_cover_both_agents(self) -> None:
        cases = evals.load_cases([evals.REPO_CASES])
        self.assertEqual(sorted(cases), ["rueckmeldung", "scrum_master"])
        self.assertGreaterEqual(len(cases["scrum_master"]), 15)
        self.assertGreaterEqual(len(cases["rueckmeldung"]), 12)

    def test_schema_files_in_the_repo_are_current(self) -> None:
        for agent in evals.AGENTS:
            self.assertEqual(
                evals.schema_path(agent).read_text(encoding="utf-8"),
                evals.schema_text(agent),
                f"{agent}: python -m local_agents.evals --schema",
            )

    def load(self, name: str, text: str, second: tuple[str, str] | None = None):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / name).write_text(text, encoding="utf-8")
            if second:
                (Path(folder) / second[0]).write_text(second[1], encoding="utf-8")
            return evals.load_cases([Path(folder)])

    def test_file_name_decides_the_agent_and_files_are_merged(self) -> None:
        other = VALID.replace("eins", "drei").replace("zwei", "vier")
        cases = self.load("scrum_master.yaml", VALID, ("scrum_master-alltag.yaml", other))
        self.assertEqual([case.name for case in cases["scrum_master"]], ["drei", "vier", "eins", "zwei"])
        self.assertEqual(cases["scrum_master"][3].inputs.umgebung.stories[0].kuerzel, "QG-1")

    def test_broken_files_are_reported_with_their_path(self) -> None:
        for name, text, message in (
            ("unbekannt.yaml", VALID, "muss mit einem Agenten beginnen"),
            ("scrum_master.yaml", VALID.replace("eingabe:", "eingbae:", 1), "scrum_master.yaml"),
            ("scrum_master.yaml", VALID.replace("  evaluators:\n  - Bemerkungen\n", ""), "keinen Prüfer"),
            ("scrum_master.yaml", VALID.replace("daily: false", "dayli: false"), "unbekannte Schluessel"),
            ("scrum_master.yaml", "name: x\ncases: [", "scrum_master.yaml"),
        ):
            with self.assertRaises(evals.CaseError) as raised:
                self.load(name, text)
            self.assertIn(message, str(raised.exception), name)

    def test_duplicate_case_names_across_files_are_rejected(self) -> None:
        with self.assertRaises(evals.CaseError) as raised:
            self.load("scrum_master.yaml", VALID, ("scrum_master-zwei.yaml", VALID))
        self.assertIn("doppelt", str(raised.exception))

    def test_missing_folder_is_no_error(self) -> None:
        self.assertEqual(evals.load_cases([Path("/gibt/es/nicht")]), {})


if __name__ == "__main__":
    unittest.main()

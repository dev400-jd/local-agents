"""Tests fuer das Messprogramm: Falldateien, Pruefer, Auswertung und Vergleich."""

import json
import tempfile
import unittest
import warnings
from datetime import date, datetime
from pathlib import Path

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_evals import Case

from local_agents import eval_suites as suites
from local_agents import evals, message_agent
from local_agents.profile import Profile
from local_agents.store import StoredEvent, Task
from local_agents.testing import structured_reply

models.ALLOW_MODEL_REQUESTS = False


def setUpModule() -> None:
    # pydantic-evals fuehrt die Agenten in Hilfs-Threads aus; deren Ereignis-
    # schleifen raeumt Python erst am Ende ab und warnt dann. Fuer die Tests ohne Belang.
    warnings.simplefilter("ignore", ResourceWarning)


def answering(payload: dict) -> FunctionModel:
    """Modell, das immer dieselbe strukturierte Antwort gibt."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return structured_reply(info, payload)

    return FunctionModel(respond)


SCRUM_SETUP = suites.ScrumSetup(
    heute=date(2026, 11, 16),
    stories=[
        suites.StoryItem(kuerzel="QG-1", titel="Prüfregeln", feature="Import"),
        suites.StoryItem(kuerzel="QG-3", titel="Freigabe", feature="Import"),
    ],
)


def scrum_case(name: str = "Fall", vorher: tuple[str, ...] = (), **check) -> Case:
    eingabe = check.pop("eingabe")
    return Case(
        name=name,
        inputs=suites.ScrumInputs(umgebung=SCRUM_SETUP, eingabe=eingabe, vorher=list(vorher)),
        evaluators=(suites.Bemerkungen(**check),),
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
            suites.Bemerkungen(erwartet=[{"story": "QG-1", "enthält": ["x"]}])
        self.assertIn("unbekannte Schluessel ['enthält']", str(raised.exception))


FEEDBACK_SETUP = suites.FeedbackSetup(
    heute=date(2026, 11, 20),
    aufgaben=[
        suites.TaskItem(titel="Vorbereitung: Daily", faellig=date(2026, 11, 22)),
        suites.TaskItem(titel="Vorbereitung: Jour fixe", faellig=date(2026, 11, 22)),
    ],
)


def feedback_case(eingabe: str, **check) -> Case:
    return Case(
        name="Fall",
        inputs=suites.FeedbackInputs(umgebung=FEEDBACK_SETUP, eingabe=eingabe),
        evaluators=(suites.Aufgaben(**check),),
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


def saying(text: str) -> FunctionModel:
    """Modell, das immer denselben freien Text antwortet."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(respond)


WEEK = suites.StatusSetup(
    jetzt=datetime(2026, 11, 16, 9, 0),
    aufgaben=[
        suites.StatusTaskItem(
            titel="Vorbereitung: Architekturrunde (Di 17.11. 10:00)",
            faellig=date(2026, 11, 16),
            termin=datetime(2026, 11, 17, 10, 0),
        ),
        suites.StatusTaskItem(titel="Reisekosten einreichen", faellig=date(2026, 11, 18)),
    ],
)


def status_case(**check) -> Case:
    return Case(name="Fall", inputs=suites.StatusInputs(umgebung=WEEK), evaluators=(suites.Status(**check),))


class TestStatusGrader(unittest.TestCase):
    def test_answer_naming_the_next_task_passes(self) -> None:
        case = status_case(naechste=1, nennt=["Architekturrunde"], nennt_nicht=["Reisekosten"])
        model = saying("Als Nächstes die Vorbereitung der Architekturrunde, sie ist heute fällig, Termin am 17.11. um 10:00.")
        self.assertEqual(problems("status", case, model), [])

    def test_invented_number_other_task_and_forbidden_phrase_are_named(self) -> None:
        case = status_case(naechste=2, nennt=["Architekturrunde"], nennt_nicht=["Reisekosten"])
        model = saying("Du musst in 45 Minuten die Reisekosten einreichen.")
        self.assertIn(
            "Aufgabe 2 erwartet, der Code wählte 1; „Architekturrunde“ fehlt in der Antwort; "
            "„Reisekosten“ gehört nicht in die Antwort; „du musst“ gehört nicht in die Antwort; "
            "Zahlen nicht aus der Eingabe: 45",
            problems("status", case, model)[0],
        )

    def test_numbers_must_come_from_what_the_model_saw(self) -> None:
        facts = "Stand Mo 16.11.2026, 09:00 Uhr\n#3 Daily [fertig bis Di 17.11.; Termin in 2 Std 30 Min]"
        self.assertEqual(suites.invented_numbers("Aufgabe 3 ist am 17.11. fällig, in 2 Std 30 Min.", facts), [])
        self.assertEqual(suites.invented_numbers("In 150 Minuten, also um 11:30.", facts), ["150"])

    def test_every_repo_case_matches_what_the_code_picks(self) -> None:
        # Mit einer Antwort, die genau das Verlangte nennt, muss jeder Fall bestehen:
        # Das prueft die Angabe "naechste" und die Umgebung der Falldatei.
        for case in evals.load_cases([evals.REPO_CASES])["status"]:
            check = case.evaluators[0]
            model = saying(" ".join(check.nennt) or "Es steht keine Aufgabe an.")
            self.assertEqual(problems("status", case, model), [], case.name)


def termin_case(titel: str, art) -> Case:
    inputs = suites.TerminInputs(
        umgebung=suites.TerminSetup(rolle_kunde="Solution Architect"),
        titel=titel,
        beginn=datetime(2026, 11, 17, 9, 0),
        kontext="kunde",
    )
    return Case(name="Fall", inputs=inputs, evaluators=(suites.Terminart(art=art),))


class TestTerminGrader(unittest.TestCase):
    def test_right_type_passes_and_wrong_type_is_named(self) -> None:
        model = answering({"type": "daily", "reason": "Titel", "deliverables": ["Stand", "Hindernisse"]})
        self.assertEqual(problems("termin", termin_case("Daily", "daily"), model), [])
        self.assertEqual(problems("termin", termin_case("Daily", ["daily", "jour_fixe"]), model), [])
        self.assertIn(
            "Terminart workshop oder kickoff erwartet, erkannt daily",
            problems("termin", termin_case("Daily", ["workshop", "kickoff"]), model)[0],
        )

    def test_fallback_to_the_catalogue_counts_as_failure(self) -> None:
        model = answering({"type": "gibtsnicht", "reason": "x", "deliverables": ["a"]})
        self.assertIn("Rückfall auf den Katalog", problems("termin", termin_case("Daily", "daily"), model)[0])

    def test_unknown_type_in_the_case_file_is_rejected(self) -> None:
        for wrong in ({"art": "dayli"}, {"nicht": ["dayli"]}, {}):
            with self.assertRaises(ValueError):
                suites.Terminart(**wrong)

    def test_only_the_wrong_type_can_be_named(self) -> None:
        def case(nicht: list[str]) -> Case:
            checked = termin_case("Review der Abrechnung", "daily")
            return Case(name="Fall", inputs=checked.inputs, evaluators=(suites.Terminart(nicht=nicht),))

        model = answering({"type": "praesentation", "reason": "Titel", "deliverables": ["Unterlagen"]})
        self.assertEqual(problems("termin", case(["sprint_review"]), model), [])
        self.assertIn("Terminart praesentation ist hier falsch", problems("termin", case(["praesentation"]), model)[0])


DOCUMENT = """# Titel

## Ziel

Text.

## QG-1: Erste Story

Als Nutzer ...

## QG-2: Zweite Story

Als Nutzer ...
"""


def stories_case(ohne_kuerzel: bool = False, **check) -> Case:
    inputs = suites.StoriesInputs(markdown=DOCUMENT, ohne_kuerzel=ohne_kuerzel)
    return Case(name="Fall", inputs=inputs, evaluators=(suites.Stories(**check),))


class TestStoryDetection(unittest.TestCase):
    def test_expected_headings_pass_and_differences_are_named(self) -> None:
        case = stories_case(erwartet=["QG-1: Erste Story", "QG-2: Zweite Story"])
        self.assertEqual(problems("stories", case, answering({"stories": [2, 3]})), [])
        self.assertIn(
            "nicht erkannt (1 von 2): QG-2: Zweite Story; zu viel erkannt (1): Ziel",
            problems("stories", case, answering({"stories": [1, 2]}))[0],
        )

    def test_model_sees_headings_without_keys_and_rule_gives_the_expectation(self) -> None:
        seen: list[str] = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.append(info.instructions)
            return structured_reply(info, {"stories": [2, 3]})

        case = stories_case(ohne_kuerzel=True, wie_regel=True)
        self.assertEqual(problems("stories", case, FunctionModel(respond)), [])
        self.assertIn("## Erste Story", seen[0])
        self.assertNotIn("QG-1", seen[0])

    def test_file_path_is_relative_to_the_repo(self) -> None:
        inputs = suites.StoriesInputs(datei="tests/data/feature_beispiel.md", ohne_kuerzel=True)
        output = suites.stories_task(answering({"stories": []}))(inputs)
        self.assertGreater(len(output.nach_regel), 0)
        self.assertEqual(output.erkannt, [])


class TestMessageGrader(unittest.TestCase):
    def request_of(self, case: Case) -> message_agent.Request:
        inputs = case.inputs
        task = Task(1, "e", "verschieben", inputs.aufgabe, "intern", inputs.beginn.date(), "offen", "", "")
        event = StoredEvent("e", inputs.titel, inputs.beginn, inputs.beginn, "intern", organizer=inputs.organisator)
        profile = Profile(name=inputs.umgebung.absender, message_form=inputs.umgebung.anrede)
        return message_agent.build_request(task, event, profile)

    def test_repo_cases_pass_with_a_correct_draft_and_fail_on_the_fallback(self) -> None:
        for case in evals.load_cases([evals.REPO_CASES])["nachricht"]:
            if case.inputs.vorgaben:
                continue  # die Vorlage kennt keine Vorgaben
            text = message_agent.template(self.request_of(case))
            self.assertEqual(problems("nachricht", case, saying(text)), [], case.name)
            failed = problems("nachricht", case, saying("Geht nicht."))
            self.assertIn("Rückfall auf die Vorlage", failed[0], case.name)

    def test_required_and_forbidden_wording(self) -> None:
        case = evals.load_cases([evals.REPO_CASES])["nachricht"][0]
        text = message_agent.template(self.request_of(case)).replace("Kunden vor Ort", "Kunden ACME vor Ort")
        self.assertIn("„ACME“ gehört nicht in den Entwurf", problems("nachricht", case, saying(text))[0])
        check = suites.Nachricht(eines_von=["absage", "nicht dabei"])
        plain = Case(name="Fall", inputs=case.inputs, evaluators=(check,))
        good = message_agent.template(self.request_of(case))
        self.assertIn("keines von absage, nicht dabei im Entwurf", problems("nachricht", plain, saying(good))[0])


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

    def test_outage_of_the_model_server_is_reported_and_not_saved(self) -> None:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise ConnectionError("Ollama antwortet nicht")

        cases = [scrum_case(name="a", eingabe="QG-1 ist fertig", erwartet=[{"story": "QG-1"}])]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / evals.RESULTS_NAME
            printed: list[str] = []
            results = evals.evaluate("scrum_master", cases, FunctionModel(respond), "modell", 2, path, printed.append)
            self.assertEqual((results[0].passed, results[0].outages), (0, 2))
            self.assertIn("Messung unvollständig: In 2 Läufen", printed[-1])
            self.assertFalse(path.exists())

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
        self.assertEqual(sorted(cases), ["nachricht", "rueckmeldung", "scrum_master", "status", "stories", "termin"])
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

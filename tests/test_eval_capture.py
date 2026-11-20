"""Tests fuer das Festhalten echter Faelle (``evals merke``)."""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from local_agents import eval_capture as capture
from local_agents import evals
from local_agents.profile import Context, Profile
from local_agents.store import Store, StoredEvent

NOW = datetime(2026, 11, 16, 9, 0, 5)
PROFILE = Profile(
    role="Teamleiter",
    contexts=(
        Context("intern", "intern", "Intern"),
        Context("kunde-acme", "kunde", "Kunde ACME", role="Solution Architect", stories_dir="~/stories"),
    ),
)


class CaptureCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def saved(self, agent: str, case: dict):
        """Haengt den Fall an und gibt die geladenen Faelle des Agenten zurueck."""
        capture.append_case(agent, case, self.folder)
        return evals.load_cases([self.folder])[agent]


class TestScrumMaster(CaptureCase):
    def test_latest_answer_with_what_was_noted(self) -> None:
        self.store.add_story_note("QG-1: Prüfregeln", "Import", "ist fertig", None, "alt", "terminal")
        raw = "QG-3 morgen im Daily ansprechen und QG-1 ist fertig"
        self.store.add_story_note("QG-3: Freigabe", "Import", "ansprechen", date(2026, 11, 17), raw, "terminal")
        self.store.add_story_note("", "", "QG-1 ist fertig", None, raw, "terminal")
        case = capture.scrum_case(self.store, PROFILE, NOW)
        self.assertEqual(case["name"], "Alltag 16.11.2026 09:00:05: QG-3 morgen im Daily ansprechen und QG-1")
        self.assertEqual(case["inputs"]["eingabe"], raw)
        self.assertEqual(case["inputs"]["umgebung"]["stories_dir"], "~/stories")
        self.assertEqual(
            case["evaluators"],
            [
                {
                    "Bemerkungen": {
                        "erwartet": [
                            {"story": "", "daily": False, "enthaelt": []},
                            {"story": "QG-3", "daily": date(2026, 11, 17), "enthaelt": []},
                        ]
                    }
                }
            ],
        )
        loaded = self.saved("scrum_master", case)
        self.assertEqual(loaded[0].evaluators[0].erwartet[1]["story"], "QG-3")

    def test_given_wording_without_a_note_and_empty_database(self) -> None:
        with self.assertRaises(capture.CaptureError):
            capture.scrum_case(self.store, PROFILE, NOW)
        case = capture.scrum_case(self.store, Profile(), NOW, "QG-9 ist neu")
        self.assertEqual(case["inputs"]["umgebung"], {"heute": NOW.date(), "stories": []})
        self.assertEqual(case["evaluators"], ["Bemerkungen"])
        self.assertEqual(len(self.saved("scrum_master", case)), 1)


class TestOtherAgents(CaptureCase):
    def plan(self) -> None:
        self.store.upsert_event(
            StoredEvent("a", "Jour fixe", datetime(2026, 11, 17, 14), datetime(2026, 11, 17, 15), "intern", attendees=3)
        )
        self.store.upsert_event(
            StoredEvent("b", "Daily Import", datetime(2026, 11, 16, 13), datetime(2026, 11, 16, 13, 15), "kunde-acme", attendees=8)
        )
        self.store.upsert_task("a", "vorbereitung", "Vorbereitung: Jour fixe", "intern", date(2026, 11, 16), "planner")
        self.store.upsert_task("b", "vorbereitung", "Vorbereitung: Daily Import", "kunde-acme", date(2026, 11, 15), "planner")
        self.store.add_task("manuell", "Angebot prüfen", "intern", date(2026, 11, 20), "rueckmeldung")

    def test_feedback_takes_the_open_tasks_as_plan(self) -> None:
        self.plan()
        self.store.set_status(3, "erledigt")
        case = capture.feedback_case(self.store, NOW, "  das Daily raus ")
        self.assertEqual(case["inputs"]["eingabe"], "das Daily raus")
        self.assertEqual(len(case["inputs"]["umgebung"]["aufgaben"]), 2)
        loaded = self.saved("rueckmeldung", case)
        self.assertEqual(loaded[0].inputs.umgebung.heute, NOW.date())
        with self.assertRaises(capture.CaptureError):
            capture.feedback_case(self.store, NOW, " ")

    def test_status_expects_the_task_the_code_picks(self) -> None:
        self.plan()
        case = capture.status_case(self.store, NOW)
        # Aufgabe 2 ist ueberfaellig und geht vor.
        self.assertEqual(case["evaluators"][0]["Status"]["naechste"], 2)
        self.assertEqual(case["inputs"]["umgebung"]["aufgaben"][2], {"titel": "Angebot prüfen", "faellig": date(2026, 11, 20)})
        loaded = self.saved("status", case)
        self.assertEqual(loaded[0].inputs.umgebung.aufgaben[1].termin, datetime(2026, 11, 16, 13))
        empty = Store(":memory:")
        self.addCleanup(empty.close)
        self.assertEqual(capture.status_case(empty, NOW)["evaluators"][0]["Status"]["naechste"], "keine")

    def test_meeting_takes_event_roles_and_the_known_type(self) -> None:
        self.plan()
        case = capture.termin_case(self.store, PROFILE, NOW, 2)
        self.assertEqual(
            case["inputs"],
            {
                "umgebung": {"rolle_intern": "Teamleiter", "rolle_kunde": "Solution Architect", "kunde": "Kunde ACME"},
                "titel": "Daily Import",
                "beginn": datetime(2026, 11, 16, 13),
                "minuten": 15,
                "eingeladene": 8,
                "kontext": "kunde",
            },
        )
        self.assertEqual(case["evaluators"], [{"Terminart": {"art": "daily"}}])  # aus dem Stichwort
        self.store.save_brief(2, "Daily Import", "jour_fixe", "weil", ("Stand",))
        self.assertEqual(capture.termin_case(self.store, PROFILE, NOW, 2)["evaluators"][0]["Terminart"]["art"], "jour_fixe")
        self.assertEqual(len(self.saved("termin", case)), 1)
        for missing in (3, 99):
            with self.assertRaises(capture.CaptureError):
                capture.termin_case(self.store, PROFILE, NOW, missing)

    def test_message_takes_the_move_task_event_and_preferences(self) -> None:
        self.plan()
        title = "Nachricht an Kim Beispiel: Jour fixe (Di 17.11. 14:00) verschieben, weil Kundentag Kunde ACME"
        self.store.upsert_task("a", "verschieben", title, "intern", date(2026, 11, 16), "planner")
        self.store.add_preference("nachricht", "kürzer")
        case = capture.nachricht_case(self.store, PROFILE, NOW, 4)
        self.assertEqual(case["inputs"]["aufgabe"], title)
        self.assertEqual(case["inputs"]["umgebung"], {"absender": "Absender", "anrede": "du"})
        self.assertEqual(case["inputs"]["vorgaben"], ["kürzer"])
        self.assertEqual(len(self.saved("nachricht", case)), 1)
        with self.assertRaises(capture.CaptureError):
            capture.nachricht_case(self.store, PROFILE, NOW, 1)  # eine Vorbereitung

    def test_stories_file_is_stored_with_its_full_path(self) -> None:
        file = self.folder / "feature.md"
        file.write_text("# Titel\n\n## QG-1: Story\n\nText\n", encoding="utf-8")
        case = capture.stories_case(NOW, str(file))
        self.assertEqual(case["inputs"], {"datei": str(file.resolve()), "ohne_kuerzel": True})
        self.assertEqual(len(self.saved("stories", case)), 1)
        with self.assertRaises(capture.CaptureError):
            capture.stories_case(NOW, str(self.folder / "fehlt.md"))

    def test_search_question_expects_the_current_best_hit(self) -> None:
        with tempfile.TemporaryDirectory() as vault:
            Path(vault, "feature.md").write_text("# Titel\n\n## QG-1: Prüfregeln\n\nRegeln für Lieferdaten.\n", encoding="utf-8")
            profile = Profile(contexts=(Context("kunde-acme", "kunde", "Kunde ACME", stories_dir=vault),))
            case = capture.suche_case(self.store, profile, NOW, "Lieferdaten")
            nothing = capture.suche_case(self.store, profile, NOW, "Weihnachtsfeier")
        self.assertEqual(case["inputs"], {"frage": "Lieferdaten"})
        self.assertEqual(case["evaluators"], [{"Treffer": {"zuerst": "QG-1: Prüfregeln (Titel)"}}])
        self.assertEqual(nothing["evaluators"], [{"Treffer": {"leer": True}}])
        self.assertEqual(len(self.saved("suche", case)), 1)
        with self.assertRaises(capture.CaptureError):
            capture.suche_case(self.store, profile, NOW, " ")


class TestAppend(CaptureCase):
    def test_second_case_is_appended_and_a_broken_one_leaves_the_file_untouched(self) -> None:
        first = capture.scrum_case(self.store, Profile(), NOW, "QG-1 ist fertig")
        second = capture.scrum_case(self.store, Profile(), NOW, "QG-2 wartet")
        path = capture.append_case("scrum_master", first, self.folder)
        capture.append_case("scrum_master", second, self.folder)
        self.assertEqual(path.name, "scrum_master-alltag.yaml")
        self.assertEqual(len(evals.load_cases([self.folder])["scrum_master"]), 2)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# yaml-language-server: $schema="))
        with self.assertRaises(evals.CaseError):  # gleicher Name noch einmal
            capture.append_case("scrum_master", first, self.folder)
        self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_broken_first_case_leaves_no_file(self) -> None:
        broken = {"name": "x", "inputs": {"titel": "ohne Umgebung"}, "evaluators": [{"Terminart": {"art": "daily"}}]}
        with self.assertRaises(evals.CaseError):
            capture.append_case("termin", broken, self.folder)
        self.assertEqual(list(self.folder.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

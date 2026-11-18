"""Tests fuer das Lesen der User Stories aus Markdown (erfundenes Beispiel-Feature)."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents.testing import structured_reply
from local_agents import stories
from local_agents.profile import Context, Profile
from local_agents.stories import (
    INDEX_FILE,
    load_features,
    model_stories,
    outline,
    parse_feature,
    parse_table,
    read_index,
)

models.ALLOW_MODEL_REQUESTS = False

EXAMPLE = Path(__file__).parent / "data" / "feature_beispiel.md"
VARIANTS = Path(__file__).parent / "data" / "feature_varianten.md"


class TestParseFeature(unittest.TestCase):
    def setUp(self) -> None:
        self.feature = parse_feature(EXAMPLE.read_text(encoding="utf-8"), EXAMPLE)

    def test_feature_parts(self) -> None:
        feature = self.feature
        self.assertEqual(feature.title, "Bestellimport mit Qualitätsprüfung")
        self.assertIn("**Ziel:** Bestellungen aus dem Shop", feature.intro)
        self.assertNotIn("---", feature.intro.splitlines())
        self.assertEqual(list(feature.sections), ["Überblick", "Offene Punkte"])
        self.assertEqual(feature.sections["Offene Punkte"], "Noch zu klären: Aufbewahrungsfrist.")

    def test_stories_with_key_title_and_fields(self) -> None:
        first, second = self.feature.stories
        self.assertEqual((first.key, first.title), ("QG-1", "Prüfregeln für `bestellung_roh`"))
        self.assertEqual(first.name, "QG-1: Prüfregeln für `bestellung_roh`")
        self.assertEqual(first.feature, "Bestellimport mit Qualitätsprüfung")
        self.assertEqual(
            list(first.fields),
            ["Story", "Schätzung", "Fachlicher Inhalt", "Voraussetzungen", "Akzeptanzkriterien", "Aufgaben"],
        )
        self.assertTrue(first.fields["Story"].startswith("Als Datenverantwortliche möchte ich"))
        self.assertEqual((first.estimate, second.estimate), ("3 PT", "1 PT"))
        self.assertEqual((second.key, second.title), ("TR-2", "Bericht bereitstellen"))

    def test_tables_become_rows(self) -> None:
        story = self.feature.stories[0]
        self.assertEqual(
            story.table("Aufgaben"),
            [
                {"Nr.": "1", "Aufgabe": "Regeln festlegen", "Aufwand": "1 PT", "Rolle": "Solution Architect"},
                {"Nr.": "2", "Aufgabe": "Regeln umsetzen", "Aufwand": "2 PT", "Rolle": "Entwicklung"},
            ],
        )
        self.assertEqual(story.table("Akzeptanzkriterien")[1]["Kriterium"], "Bericht nennt Anzahl a | b")
        self.assertEqual(story.table("Story"), [])
        self.assertEqual(parse_table("kein | Tabellenkopf"), [])


class TestOtherLayouts(unittest.TestCase):
    """Stories eine Ebene tiefer, ohne Doppelpunkt, mit Angabenzeile und ohne Feldnamen."""

    def setUp(self) -> None:
        self.feature = parse_feature(VARIANTS.read_text(encoding="utf-8"), VARIANTS)
        self.lp1, self.lp2, self.zi = self.feature.stories

    def test_headings_groups_and_sections(self) -> None:
        self.assertEqual(self.feature.title, "Lieferantenportal – Features und Stories")
        self.assertEqual(
            [(story.key, story.title, story.group) for story in self.feature.stories],
            [
                ("LP-1", "Anmeldung am Portal einrichten", "Gruppe A – Anbindung"),
                ("LP-2", "Ablageordner je Lieferant", "Gruppe A – Anbindung"),
                ("ZI-0a", "Offene Punkte klären", "Gruppe B – Freigaben"),
            ],
        )
        self.assertEqual(list(self.feature.sections), ["Vorgehen", "Gruppe A – Anbindung", "Anhang"])
        self.assertEqual(self.feature.sections["Gruppe A – Anbindung"], "Diese Stories gehören zusammen.")

    def test_meta_line_with_dots(self) -> None:
        self.assertEqual(self.lp1.estimate, "2 PT")
        self.assertEqual(self.lp1.fields["Sprint"], "12")
        self.assertEqual(self.lp1.fields["Angaben"], "lp-anmeldung · Hoch")
        self.assertNotIn("Abhängig von", self.lp1.fields)

    def test_free_story_text_and_unlabelled_tables(self) -> None:
        self.assertEqual(self.lp1.fields["Story"], "Als Lieferant möchte ich mich anmelden, damit ich Daten liefern kann.")
        self.assertEqual(self.lp1.table("Akzeptanzkriterien"), [{"Nr.": "A1", "Kriterium": "Anmeldung mit Firmenkonto klappt", "Nachweis": "Test"}])
        self.assertEqual(self.lp1.table("Aufgaben")[0]["Rolle"], "DE + SA")

    def test_meta_line_with_bars_italic_story_and_inline_fields(self) -> None:
        self.assertEqual((self.lp2.estimate, self.lp2.fields["Phase"]), ("1 PT", "Sprint 13"))
        self.assertEqual(self.lp2.fields["Story"], "Als Betreiber möchte ich getrennte Ordner, damit nichts vermischt wird.")
        self.assertEqual(self.lp2.fields["Einordnung und Nutzen"], "Trennt die Lieferungen sauber.")
        self.assertEqual(len(self.lp2.table("Akzeptanzkriterien")), 2)
        self.assertEqual(self.lp2.fields["Abhängigkeiten und Risiken"], "Hängt an LP-1.")

    def test_meta_line_in_backticks_and_labelled_table(self) -> None:
        self.assertEqual((self.zi.estimate, self.zi.fields["Rolle"]), ("0,5 PT", "SA"))
        self.assertEqual(self.zi.fields["Angaben"], "Sprint 12 (Kickoff)")
        self.assertEqual(self.zi.fields["Story"], "Als Team möchten wir offene Punkte klären.")
        self.assertEqual(self.zi.table("Klärung")[0]["Frage"], "Wer gibt frei?")

    def test_overview_shows_groups(self) -> None:
        self.assertEqual(
            stories.format_overview([self.feature]),
            "Feature: Lieferantenportal – Features und Stories (3 Stories, feature_varianten.md)\n"
            "  Gruppe A – Anbindung\n"
            "    LP-1: Anmeldung am Portal einrichten [2 PT, 1 Akzeptanzkriterien, 1 Aufgaben]\n"
            "    LP-2: Ablageordner je Lieferant [1 PT, 2 Akzeptanzkriterien]\n"
            "  Gruppe B – Freigaben\n"
            "    ZI-0a: Offene Punkte klären [0,5 PT]",
        )


class TestTableOwnership(unittest.TestCase):
    def test_table_after_text_belongs_to_the_field_named_on_its_own_line(self) -> None:
        text = (
            "# Feature: X\n\n## AB-1: Titel\n\n**Story**\n\nAls Nutzer ...\n\n"
            "**Gemeinsame Regeln**\n\nGilt für alle Stories.\n\n| Nr. | Kriterium |\n|---|---|\n| 1 | Regel |\n\n"
            "**Akzeptanzkriterien**\n\n| Nr. | Kriterium | Nachweis |\n|---|---|---|\n| 1 | Echt | Test |\n| 2 | Auch | Test |\n"
        )
        (story,) = parse_feature(text).stories
        self.assertEqual(list(story.fields), ["Story", "Gemeinsame Regeln", "Akzeptanzkriterien"])
        self.assertEqual([row["Kriterium"] for row in story.table("Akzeptanzkriterien")], ["Echt", "Auch"])
        self.assertEqual(story.table("Gemeinsame Regeln"), [{"Nr.": "1", "Kriterium": "Regel"}])


class TestFolderAndCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        text = EXAMPLE.read_text(encoding="utf-8")
        (self.folder / "Feature_Bestellimport_2026-11-01.md").write_text(text.replace("3 PT", "2 PT"), encoding="utf-8")
        (self.folder / "Feature_Bestellimport_2026-11-11.md").write_text(text, encoding="utf-8")
        (self.folder / "notizen.md").write_text("# Notizen\n\nOhne Stories.\n", encoding="utf-8")
        self.profile = Profile(
            contexts=(Context("kunde-acme", "kunde", "Kunde ACME", stories_dir=str(self.folder)),)
        )

    def call(self, *args: str, profile: Profile | None = None) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = stories.main(
                ["stories", *args], profile=profile or self.profile, classify=lambda sections: set()
            )
        return code, out.getvalue()

    def test_newest_file_of_a_feature_wins_and_notes_are_ignored(self) -> None:
        (feature,) = load_features(self.folder)
        self.assertEqual(feature.path.name, "Feature_Bestellimport_2026-11-11.md")
        self.assertEqual(feature.stories[0].estimate, "3 PT")

    def test_overview(self) -> None:
        self.assertEqual(
            self.call(),
            (
                0,
                "Feature: Bestellimport mit Qualitätsprüfung (2 Stories, Feature_Bestellimport_2026-11-11.md)\n"
                "  QG-1: Prüfregeln für `bestellung_roh` [3 PT, 2 Akzeptanzkriterien, 2 Aufgaben]\n"
                "  TR-2: Bericht bereitstellen [1 PT]\n",
            ),
        )

    def test_single_story_and_errors(self) -> None:
        code, text = self.call("qg-1")
        self.assertEqual(code, 0)
        self.assertTrue(text.startswith("QG-1: Prüfregeln für `bestellung_roh`\nFeature: Bestellimport"), text)
        self.assertIn("**Schätzung:** 3 PT", text)  # der Abschnitt unveraendert
        self.assertTrue(self.call("Bericht")[1].startswith("TR-2: Bericht bereitstellen"))
        self.assertEqual(self.call("XX-9")[0], 1)
        self.assertEqual(self.call(profile=Profile())[0], 1)


FREE_FORM = """# Umzug der Berichte

Einleitung.

## Hintergrund

Warum wir umziehen.

## Arbeitspakete

### Alte Berichte inventarisieren

#### Beschreibung

Liste aller Berichte erstellen.

#### Akzeptanzkriterien

| Nr | Kriterium |
|---|---|
| 1 | Liste ist vollständig |

```bash
# kein Abschnitt, nur ein Kommentar im Code
```

### Neue Ablage einrichten

Aufwand: 2 PT | Sprint: 4

Ordner anlegen und Rechte vergeben.

## Anhang

Glossar.
"""


def model_marking(*numbers: int, seen: list | None = None) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append(info.instructions)
        return structured_reply(info, {"stories": list(numbers)})

    return FunctionModel(respond)


class TestStructureIndependent(unittest.TestCase):
    """Dateien ohne Kuerzel: Gliederung, Markierung durch das Modell, Index."""

    def setUp(self) -> None:
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.folder / "umzug.md").write_text(FREE_FORM, encoding="utf-8")

    def test_outline_ignores_headings_in_code(self) -> None:
        intro, sections = outline(FREE_FORM)
        self.assertEqual(intro, [])
        self.assertEqual(
            [(section.level, section.heading) for section in sections],
            [
                (1, "Umzug der Berichte"),
                (2, "Hintergrund"),
                (2, "Arbeitspakete"),
                (3, "Alte Berichte inventarisieren"),
                (4, "Beschreibung"),
                (4, "Akzeptanzkriterien"),
                (3, "Neue Ablage einrichten"),
                (2, "Anhang"),
            ],
        )

    def test_rule_finds_nothing_without_keys(self) -> None:
        self.assertEqual(parse_feature(FREE_FORM).stories, ())
        notes: list[str] = []
        self.assertEqual(load_features(self.folder, notes=notes), [])
        self.assertEqual(notes, ["umzug.md: keine Stories erkannt."])

    def test_model_marks_stories_on_the_outline_only(self) -> None:
        seen: list = []
        sections = outline(FREE_FORM)[1]
        self.assertEqual(model_stories(sections, model_marking(3, 6, seen=seen)), {3, 6})
        self.assertIn("3. ### Alte Berichte inventarisieren | 0 Zeilen", seen[0])
        self.assertIn("6. ### Neue Ablage einrichten | 2 Zeilen | beginnt: Aufwand: 2 PT | Sprint: 4", seen[0])
        self.assertNotIn("Liste ist vollständig", seen[0])  # Inhalt geht nicht ans Modell
        # Nummern ausserhalb der Liste gehen zurueck ans Modell; scheitert es, gilt nichts.
        self.assertEqual(model_stories(sections, model_marking(99)), set())

    def test_marked_stories_are_parsed_and_stored_in_the_index(self) -> None:
        notes: list[str] = []
        calls = []

        def classify(sections):
            calls.append(len(sections))
            return {2, 3, 6}  # 2 ist die Gruppe ueber den beiden Stories

        (feature,) = load_features(self.folder, classify, notes)
        self.assertEqual(feature.found_by, "Index")
        first, second = feature.stories
        self.assertEqual((first.key, first.name, first.group), ("", "Alte Berichte inventarisieren", "Arbeitspakete"))
        self.assertEqual(first.fields["Beschreibung"], "Liste aller Berichte erstellen.")
        self.assertEqual(first.table("Akzeptanzkriterien"), [{"Nr": "1", "Kriterium": "Liste ist vollständig"}])
        self.assertIn("# kein Abschnitt, nur ein Kommentar im Code", first.text)
        self.assertEqual((second.estimate, second.fields["Sprint"]), ("2 PT", "4"))
        self.assertEqual(second.fields["Story"], "Ordner anlegen und Rechte vergeben.")
        self.assertEqual(list(feature.sections), ["Hintergrund", "Anhang"])
        self.assertIn("umzug.md: 2 Stories vom Modell erkannt. Bitte prüfen", notes[0])
        entry = read_index(self.folder)["umzug.md"]
        self.assertEqual(entry["stories"], ["Alte Berichte inventarisieren", "Neue Ablage einrichten"])
        self.assertEqual((entry["quelle"], entry["geprueft"]), ("modell", False))
        # Zweiter Lauf: Index gilt, das Modell wird nicht mehr gefragt.
        self.assertEqual(len(load_features(self.folder, classify)[0].stories), 2)
        self.assertEqual(len(load_features(self.folder)[0].stories), 2)
        self.assertEqual(calls, [8])

    def test_changed_file_is_marked_again_unless_checked_by_the_user(self) -> None:
        load_features(self.folder, lambda sections: {3, 6})
        path = self.folder / "umzug.md"
        path.write_text(FREE_FORM + "\nNeu.\n", encoding="utf-8")
        self.assertEqual(len(load_features(self.folder, lambda sections: {3})[0].stories), 1)
        # Der Nutzer korrigiert den Index und haelt ihn fest.
        index = read_index(self.folder)
        index["umzug.md"].update(stories=["Neue Ablage einrichten"], geprueft=True)
        (self.folder / INDEX_FILE).write_text(json.dumps(index), encoding="utf-8")
        path.write_text(FREE_FORM + "\nNoch neuer.\n", encoding="utf-8")
        (feature,) = load_features(self.folder, self.fail)
        self.assertEqual([story.name for story in feature.stories], ["Neue Ablage einrichten"])

    def test_overview_marks_model_detection(self) -> None:
        features = load_features(self.folder, lambda sections: {3, 6})
        self.assertEqual(
            stories.format_overview(features),
            "Feature: Umzug der Berichte (2 Stories, umzug.md, vom Modell erkannt)\n"
            "  Arbeitspakete\n"
            "    Alte Berichte inventarisieren [1 Akzeptanzkriterien]\n"
            "    Neue Ablage einrichten [2 PT]",
        )


if __name__ == "__main__":
    unittest.main()

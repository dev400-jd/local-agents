"""Tests fuer den Gespraechs-Agenten: Auswahl der Eintraege, Pruefung der Antwort, Gespraech."""

import os

os.environ["LOCAL_AGENTS_ASANA_DB"] = "/gibt/es/nicht/asana.db"  # nie den echten Asana-Spiegel lesen

import unittest
from pathlib import Path

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents import ask, search
from local_agents.profile import Profile
from local_agents.search import REMARK, STORY, Index, Passage
from local_agents.store import Store

models.ALLOW_MODEL_REQUESTS = False

VAULT = Path(__file__).resolve().parent.parent / "evals" / "suche_vault"
QUALITY, CONNECT = "Datenqualität im Wareneingang", "Anbindung der Lieferanten"


def saying(*answers: str, seen: list | None = None) -> FunctionModel:
    """Modell, das nacheinander die gegebenen Antworten liefert (die letzte immer wieder)."""
    replies = list(answers)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append(info.instructions)
        return ModelResponse(parts=[TextPart(replies.pop(0) if len(replies) > 1 else replies[0])])

    return FunctionModel(respond)


class AskCase(unittest.TestCase):
    def setUp(self) -> None:
        store = Store(":memory:")
        store.add_story_note("QG-3: Quarantäne für abgewiesene Lieferungen", "", "Frist ist mit dem Datenschutz nicht geklärt.")
        self.index = Index(":memory:")
        self.addCleanup(self.index.close)
        self.index.update(search.story_passages("Beispiel", VAULT) + search.store_passages(store, Profile()))
        store.close()

    def select(self, question: str, focus: str = "") -> ask.Selection:
        return ask.select(self.index, question, None, focus)

    def names(self, selection: ask.Selection) -> list[str]:
        return [title.split(":")[0] for title in selection.titles()]


class TestSelection(AskCase):
    def test_feature_is_named_by_a_word_only_its_title_has(self) -> None:
        features = self.index.features()
        self.assertEqual(features, [CONNECT, QUALITY])
        self.assertEqual(ask.named_feature("Was ist im Feature Anbindung offen?", features), CONNECT)
        self.assertEqual(ask.named_feature("Wie steht es um die Datenqualität?", features), QUALITY)
        self.assertEqual(ask.named_feature("Was ist offen?", features), "")
        # "Lieferanten" steht nur in einem Titel, "Stories" und "Feature" benennen keines.
        self.assertEqual(ask.named_feature("Welche Stories hat das Feature?", features), "")
        self.assertEqual(ask.named_feature("Anbindung oder Datenqualität?", features), "")

    def test_position_words(self) -> None:
        cases = {
            "Was ist als Letztes zu tun?": (ask.LAST, ""),
            "Womit fängt das Feature an?": (ask.FIRST, ""),
            "Was kommt nach QG-3?": (ask.NEXT, "qg-3"),
            "Was steht vor an-2?": (ask.PREVIOUS, "an-2"),
            "Was ist mit QG-3?": ("", ""),
        }
        for question, expected in cases.items():
            self.assertEqual(ask.position_asked(question), expected, question)

    def test_last_and_first_story_of_a_named_feature(self) -> None:
        last = self.select("Fasse in einem Satz zusammen, was im Feature Anbindung als Letztes zu tun ist")
        self.assertEqual((self.names(last), last.feature, len(last.outline)), (["AN-4"], CONNECT, 4))
        self.assertEqual(last.how, f"letzte Story im Feature „{CONNECT}“")
        self.assertEqual(self.names(self.select("Womit beginnt die Datenqualität?")), ["QG-1"])
        # Ohne genanntes Feature und ohne bisheriges Thema ist bei zwei Features unklar, welches gemeint ist.
        self.assertNotEqual(self.select("Was ist als Letztes zu tun?").how[:6], "letzte")

    def test_story_before_and_after_a_named_one(self) -> None:
        after = self.select("Was kommt nach QG-3?")
        self.assertEqual((self.names(after), after.how), (["QG-4"], f"nächste Story im Feature „{QUALITY}“ (bezogen auf QG-3)"))
        self.assertEqual(self.names(self.select("Was steht vor AN-2?")), ["AN-1"])
        edge = self.select("Was steht vor QG-1?")
        self.assertEqual((self.names(edge), edge.how), (["QG-1"], f"QG-1 ist die erste Story im Feature „{QUALITY}“"))

    def test_named_story_comes_with_its_remarks(self) -> None:
        selection = self.select("Was ist mit QG-3?")
        self.assertEqual([entry.kind for entry in selection.entries], [STORY, REMARK])
        self.assertEqual((selection.feature, selection.how), (QUALITY, "Kürzel in der Frage: QG-3"))
        self.assertTrue(selection.focus.endswith("QG-3: Quarantäne für abgewiesene Lieferungen"))

    def test_follow_up_stays_with_the_story_and_a_new_topic_leaves_it(self) -> None:
        focus = self.select("Was ist mit QG-3?").focus
        for question in ("und die Akzeptanzkriterien?", "Wer hat dazu etwas angemerkt?", "Aufwand?"):
            follow = self.select(question, focus)
            self.assertEqual((self.names(follow), follow.how), (["QG-3", "Bemerkung zu QG-3"], "bisheriges Thema"), question)
        fresh = self.select("Wie melden große Lieferanten ihre Lieferungen über die Schnittstelle?", focus)
        self.assertEqual((self.names(fresh)[0], fresh.how[:5]), ("AN-2", "Suche"))
        self.assertEqual(self.names(self.select("Was ist als Letztes zu tun?", focus)), ["QG-4"])  # im Feature des Themas

    def test_overview_gives_the_outline(self) -> None:
        one = self.select("Welche Stories gibt es zur Datenqualität?")
        self.assertEqual(one.outline[0], "QG-1: Prüfregeln für Lieferdaten festlegen")
        self.assertEqual((len(one.outline), one.how), (4, f"Gliederung des Features „{QUALITY}“"))
        every = self.select("Welche Stories gibt es?")
        self.assertEqual((every.how, every.feature, every.entries), ("Übersicht aller Features", "", ()))
        self.assertEqual(
            every.outline[0],
            f"{CONNECT}: 4 Stories (AN-1: Dateitransfer für kleine Lieferanten bis AN-4: Überwachung der Einlieferungen)",
        )

    def test_nothing_found(self) -> None:
        nothing = self.select("Wie wird das Wetter morgen in Frankfurt am Main?")
        self.assertEqual((nothing.entries, nothing.outline, nothing.how), ((), (), "nichts gefunden"))

    def test_long_story_comes_whole_until_the_budget_is_full(self) -> None:
        index = Index(":memory:")
        self.addCleanup(index.close)
        text = "\n\n".join(f"Absatz {number}. " + "Wort " * 300 for number in range(12))
        pieces = search._pieces("story:x#LG-1", STORY, "LG-1: Lang (Groß)", text, key="lg-1", feature="Groß", position=1)
        index.update(pieces + [Passage("bemerkung:1", REMARK, "Bemerkung zu LG-1", "Kurz.", key="lg-1")])
        selection = ask.select(index, "Was ist mit LG-1?")
        self.assertGreater(len(pieces), len(selection.entries))
        self.assertLessEqual(sum(len(entry.text) for entry in selection.entries), ask.ENTRY_BUDGET)
        self.assertEqual(selection.entries[0].ref, "story:x#LG-1/1")
        self.assertEqual(selection.entries[-1].kind, REMARK)  # die kurze Bemerkung passt noch hinein
        self.assertEqual(selection.titles(), ["LG-1: Lang (Groß)", "Bemerkung zu LG-1"])


class TestAnswerCheck(AskCase):
    def deps(self, question: str = "Was ist mit QG-3?") -> ask.AskDeps:
        return ask.AskDeps(self.select(question), question, [], self.index.key_prefixes())

    def test_supported_answer_passes(self) -> None:
        good = "QG-3 hält abgewiesene Datensätze 30 Tage vor:\n1. Quarantäne\n2. erneut einspielen"
        self.assertEqual(ask.problems(good, self.deps()), [])

    def test_unknown_keys_and_numbers_are_named(self) -> None:
        deps = self.deps()
        self.assertEqual(
            ask.problems("QG-3 hängt an QG-9 und dauert 45 Tage.", deps),
            ["Story-Kürzel, die nicht in den Einträgen stehen: QG-9", "Zahlen, die nicht in den Einträgen stehen: 9, 45"],
        )
        # Was wie ein Kuerzel aussieht, aber keine Story-Vorsilbe hat, ist keines.
        self.assertEqual(ask.problems("Kodiert in UTF-8, siehe QG-3.", self.deps("Was ist mit QG-3 und UTF-8?")), [])
        self.assertEqual(ask.problems("  ", deps), ["die Antwort ist leer"])

    def test_model_sees_entries_outline_and_history(self) -> None:
        deps = ask.AskDeps(self.select("Was ist mit QG-3?"), "Was ist mit QG-3?", [("Hallo?", "Ja.")], set())
        text = ask.facts(deps)
        self.assertIn("[1] QG-3: Quarantäne für abgewiesene Lieferungen (Datenqualität im Wareneingang) (Story · datenqualitaet.md)", text)
        self.assertIn("[2] Bemerkung zu QG-3", text)
        self.assertIn(f"Gliederung des Features „{QUALITY}“ (4 Stories, in dieser Reihenfolge):\n1. QG-1", text)
        self.assertIn("Nutzer: Hallo?\nDu: Ja.", text)


class TestConversation(AskCase):
    def test_answer_names_its_basis_and_keeps_the_topic(self) -> None:
        seen: list = []
        talk = ask.Conversation(self.index, saying("QG-3 hält Datensätze 30 Tage vor.", "- 30 Tage Aufbewahrung", seen=seen))
        first = talk.ask("Was ist mit QG-3?")
        self.assertEqual((first.text, first.source), ("QG-3 hält Datensätze 30 Tage vor.", ask.MODEL))
        self.assertEqual(
            ask.answer_lines(first).splitlines()[-1],
            "Grundlage (Kürzel in der Frage: QG-3): QG-3: Quarantäne für abgewiesene Lieferungen; "
            "Bemerkung zu QG-3: Quarantäne für abgewiesene Lieferungen",
        )
        second = talk.ask("und die Akzeptanzkriterien?")
        self.assertEqual(second.selection.how, "bisheriges Thema")
        self.assertIn("Nutzer: Was ist mit QG-3?\nDu: QG-3 hält Datensätze 30 Tage vor.", seen[-1])
        self.assertIn("Abgewiesene Datensätze bleiben 30 Tage erhalten.", seen[-1])
        talk.reset()
        self.assertEqual((talk.focus, talk.history), ("", []))

    def test_unsupported_answer_is_sent_back_and_then_replaced_by_the_passages(self) -> None:
        talk = ask.Conversation(self.index, saying("QG-3 dauert 45 Tage.", "QG-3 hält Datensätze 30 Tage vor."))
        self.assertEqual(talk.ask("Was ist mit QG-3?").text, "QG-3 hält Datensätze 30 Tage vor.")
        stubborn = ask.Conversation(self.index, saying("QG-3 dauert 45 Tage."))
        answer = stubborn.ask("Was ist mit QG-3?")
        self.assertEqual(answer.source, ask.FALLBACK)
        self.assertTrue(answer.text.startswith("Das Modell hat keine belegte Antwort geliefert. Die Fundstellen dazu:\n- QG-3"))

    def test_nothing_found_is_said_without_the_model(self) -> None:
        talk = ask.Conversation(self.index, None)
        answer = talk.ask("Wie wird das Wetter morgen in Frankfurt am Main?")
        self.assertEqual((answer.text, answer.source), (ask.NOTHING_FOUND, ask.CODE))
        self.assertEqual(ask.answer_lines(answer), ask.NOTHING_FOUND)

    def test_protected_hyphens_and_spaces_are_normalised(self) -> None:
        talk = ask.Conversation(self.index, saying("QG\u20113 hält Datensätze 30\u202fTage vor."))
        self.assertEqual(talk.ask("Was ist mit QG-3?").text, "QG-3 hält Datensätze 30 Tage vor.")

    def test_story_found_by_its_place_must_be_named(self) -> None:
        seen: list = []
        talk = ask.Conversation(
            self.index,
            saying("Die Einträge enthalten keine Information zu QG-3.", "Danach kommt QG-4 mit Schwellenwerten.", seen=seen),
        )
        answer = talk.ask("Was kommt nach QG-3 und worum geht es dort?")
        self.assertEqual((answer.text, answer.selection.resolved), ("Danach kommt QG-4 mit Schwellenwerten.", "qg-4"))
        self.assertIn(
            f"Schon bestimmt: nächste Story im Feature „{QUALITY}“ (bezogen auf QG-3) ist QG-4. "
            "Beantworte die Frage für QG-4 und nenne dieses Kürzel in der Antwort.",
            seen[0],
        )
        # Bei einer genannten Story oder einer Anschlussfrage gilt die Pflicht nicht.
        self.assertEqual(self.select("Was ist mit QG-3?").resolved, "")
        self.assertEqual(self.select("und der Aufwand?", self.select("Was ist mit QG-3?").focus).resolved, "")

    def test_list_questions_about_asana_go_to_the_mirror_query(self) -> None:
        routed = {
            "Welche Aufgaben kommen aus Asana?": True,
            "Was ist diese Woche fällig?": True,
            "Wie viele offene Aufgaben habe ich?": True,
            "Wo warte ich auf andere?": True,
            "Was ist bei der Aufgabe Angebot prüfen offen?": False,  # Inhalt einer Aufgabe: Suche
            "Welche Stories gibt es?": False,
            "Welche Aufgaben hat die Story QG-3?": False,
            "Was ist mit QG-3?": False,
        }
        for question, expected in routed.items():
            self.assertEqual(ask.is_task_list_question(question), expected, question)

    def test_without_a_mirror_list_questions_stay_with_the_search(self) -> None:
        talk = ask.Conversation(self.index, saying("Dazu steht dort nichts."))
        self.assertNotEqual(talk.ask("Welche Aufgaben sind bei Lieferanten fällig?").source, ask.TASK_LIST)

    def test_chat_commands(self) -> None:
        talk = ask.Conversation(self.index, saying("QG-3 hält Datensätze vor."))
        typed = iter(["/quellen", "Was ist mit QG-3?", "/quellen", "/neu", ""])
        printed: list[str] = []
        ask.chat(talk, lambda prompt: next(typed), printed.append)
        self.assertEqual(printed[1], "Noch keine Einträge.")
        self.assertTrue(printed[2].startswith("\nQG-3 hält Datensätze vor.\n\nGrundlage (Kürzel in der Frage: QG-3)"))
        self.assertTrue(printed[3].startswith("[1] QG-3: Quarantäne"))
        self.assertEqual(printed[4], "Neues Thema.")


def slide(deck: str, path: str, number: int, heading: str, text: str) -> Passage:
    return Passage(
        f"dokument:{path}#{number}", search.DOCUMENT, f"{deck}: Folie {number}: {heading}", text,
        source=path, feature=deck, position=number,
    )


class TestNamedDocument(AskCase):
    """Nennt die Frage ein Dokument, bekommt das Modell dieses der Reihe nach statt einzelner Treffer."""

    DECK, RECAP, LIST = "Bestellwesen - Lieferketten", "Lieferketten: Vorgehen nach der Schulung", "Preisliste"

    def setUp(self) -> None:
        super().setUp()
        deck = [
            slide(self.DECK, "Ablage/Schulungen/Bestellwesen - Lieferketten.pptx", number, heading, text)
            for number, (heading, text) in enumerate(
                [
                    ("Lieferketten verstehen", "Agenda der Schulung."),
                    ("Vorgehen", "Die Schulung folgt dem Kanban-Verfahren mit Zugsteuerung."),
                    ("Rollen", "Disponent und Einkäufer teilen sich die Freigabe."),
                    ("Beschlüsse", "Am Ende stehen Beschlüsse mit Name und Datum."),
                ],
                start=1,
            )
        ]
        recap = [
            Passage(f"dokument:recap#{n}", search.DOCUMENT, f"{self.RECAP}: {h}", t, source="recap.md", feature=self.RECAP, position=n)
            for n, (h, t) in enumerate([("Nutzen", "Die Schulung kam gut an."), ("Offen", "Zwei Punkte sind offen.")], start=1)
        ]
        prices = [slide(self.LIST, "Ablage/Preisliste.xlsx", 1, "Blatt 1", "Schraube | 0,10")]
        self.index.update(
            search.story_passages("Beispiel", VAULT) + deck + recap + prices
        )

    def test_a_file_path_names_the_document_and_is_not_searched_for(self) -> None:
        question = "Welches Verfahren lehrt ('/Users/x/Ablage/Schulungen/Bestellwesen - Lieferketten.pptx')?"
        named, cleaned = ask.named_documents(question, self.index.document_sources())
        self.assertEqual(named, [self.DECK])
        self.assertEqual(cleaned, "Welches Verfahren lehrt ?")
        selection = self.select(question)
        self.assertEqual(selection.documents, (self.DECK,))
        self.assertEqual([entry.position for entry in selection.entries], [1, 2, 3, 4])
        self.assertEqual(selection.how, f"Dokument „{self.DECK}“")
        self.assertEqual(ask.source_line(selection), f"Grundlage (Dokument „{self.DECK}“)")

    def test_the_kind_of_file_decides_between_documents_with_the_same_word(self) -> None:
        sources = self.index.document_sources()
        self.assertEqual(ask.named_documents("Was lehrt die Präsentation Lieferketten?", sources)[0], [self.DECK])
        # Ohne die Art passt "Lieferketten" auf zwei Dokumente gleich gut, und ein Wort allein genügt nicht.
        self.assertEqual(ask.named_documents("Was ist mit den Lieferketten?", sources)[0], [])

    def test_two_documents_that_fit_equally_are_both_taken(self) -> None:
        # "Schulung" steht beim Deck im Ordner, beim Recap im Titel.
        selection = self.select("Welches Verfahren vermittelt die Schulung Lieferketten?")
        self.assertEqual(set(selection.documents), {self.DECK, self.RECAP})
        self.assertEqual(len(selection.entries), 6)
        self.assertEqual(selection.outline, ())

    def test_a_question_about_stories_is_not_taken_for_a_document(self) -> None:
        selection = self.select("Was passiert mit abgewiesenen Lieferungen?")
        self.assertEqual(selection.documents, ())
        self.assertEqual(ask.named_documents("Was kostet eine Schraube?", self.index.document_sources())[0], [])

    def test_a_document_too_large_keeps_the_best_sections_in_order_and_gets_an_outline(self) -> None:
        selection = ask.select(self.index, "Wer gibt laut der Präsentation Lieferketten frei, welche Rollen?", None, budget=80)
        self.assertEqual(selection.documents, (self.DECK,))
        positions = [entry.position for entry in selection.entries]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(3, positions)
        self.assertLess(len(positions), 4)
        self.assertEqual(selection.outline[0], "Folie 1: Lieferketten verstehen")
        self.assertEqual(len(selection.outline), 4)
        self.assertIn("von 4 Abschnitten", selection.how)
        self.assertIn("Gliederung des Dokuments", ask.outline_text(selection))

    def test_a_follow_up_stays_with_the_document(self) -> None:
        first = self.select("Was lehrt die Präsentation Lieferketten?")
        self.assertTrue(first.focus.startswith(ask.DOCUMENT_FOCUS))
        self.assertEqual(self.select("Und die Rollen?", first.focus).documents, (self.DECK,))
        self.assertEqual(self.select("Was ist in QG-3 geregelt?", first.focus).documents, ())

    def test_a_classification_is_marked_as_not_from_the_material(self) -> None:
        seen: list = []
        model = saying("Die Schulung folgt dem Kanban-Verfahren.\n**Einordnung:** Lean, erkennbar an der Zugsteuerung.", seen=seen)
        answer = ask.Conversation(self.index, model).ask("Welchen Ansatz lehrt die Präsentation Lieferketten?")
        self.assertEqual(
            answer.text.splitlines()[-1],
            "Einordnung (steht nicht in den Unterlagen): Lean, erkennbar an der Zugsteuerung.",
        )
        self.assertIn("Einordnung:", seen[0])
        # Auch die Einordnung darf keine Zahlen erfinden.
        model = saying("Kanban.\nEinordnung: Lean seit 1948.", "Kanban.")
        self.assertEqual(ask.Conversation(self.index, model).ask("Welchen Ansatz lehrt die Präsentation Lieferketten?").text, "Kanban.")

    def test_the_model_sees_the_whole_document(self) -> None:
        seen: list = []
        conversation = ask.Conversation(self.index, saying("Kanban-Verfahren mit Zugsteuerung.", seen=seen))
        answer = conversation.ask("Welches Verfahren lehrt die Präsentation Lieferketten?")
        self.assertEqual(answer.source, ask.MODEL)
        for text in ("Agenda der Schulung", "Kanban-Verfahren", "Beschlüsse mit Name und Datum"):
            self.assertIn(text, seen[0])


if __name__ == "__main__":
    unittest.main()

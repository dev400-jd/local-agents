"""Tests fuer die lokale Suche: Quellen einsammeln, Index abgleichen, Rangfolge."""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from local_agents import search
from local_agents.planner_agent import MOVE
from local_agents.profile import Context, Profile
from local_agents.search import BRIEF, MESSAGE, REMARK, SECTION, STORY, Changes, Index, Passage
from local_agents.store import DONE, NOTE_REMOVED, Store, StoredEvent

FEATURE = """# Datenqualität

Fehlerhafte Lieferdaten sollen früh auffallen.

## Ziel

Der Einkauf vertraut den Zahlen.

## QG-1: Prüfregeln festlegen

Als Datenverantwortliche möchte ich Prüfregeln für Lieferdaten festlegen.

### Akzeptanzkriterien

- Lieferung ohne Artikelnummer wird abgewiesen.

## QG-2: Wochenbericht über Verstöße

Als Leiterin Einkauf möchte ich einen Bericht über Verstöße gegen die Prüfregeln.
"""
PROFILE = Profile(contexts=(Context("intern", "intern", "Intern"), Context("kunde-acme", "kunde", "Kunde ACME")))


def entries() -> list[Passage]:
    return [
        Passage("story:1", STORY, "QG-1: Prüfregeln festlegen", "Prüfregeln für Lieferdaten.", key="qg-1"),
        Passage("story:2", STORY, "QG-2: Wochenbericht", "Bericht über Verstöße gegen die Prüfregeln."),
        Passage("bemerkung:1", REMARK, "Bemerkung zu QG-1", "Schnittstelle zum Lager ist offen.", key="qg-1"),
        Passage("abschnitt:1", SECTION, "Datenqualität: Ziel", "Der Einkauf vertraut den Zahlen."),
    ]


class TestQuery(unittest.TestCase):
    def test_question_becomes_stems_without_filler_words(self) -> None:
        self.assertEqual(
            search.fts_query("Wie steht es um die Schnittstellen zum Lager?"),
            '"schnittstell"* OR "lager"*',
        )
        self.assertEqual(search.fts_query("der die das"), "")
        self.assertEqual(search.fts_query("Verstoß am 20."), '"verstoß"* OR "20"')

    def test_story_keys_are_searched_separately(self) -> None:
        self.assertEqual(search.story_keys("Was ist mit QG-1 und an-12b?"), ["qg-1", "an-12b"])
        self.assertEqual(search.fts_query("Was ist mit QG-1?"), "")

    def test_short_words_keep_their_ending(self) -> None:
        self.assertEqual([search.stem(word) for word in ("tests", "regeln", "lieferungen", "plan")], ["test", "regel", "liefer", "plan"])


class TestIndex(unittest.TestCase):
    def setUp(self) -> None:
        self.index = Index(":memory:")
        self.addCleanup(self.index.close)
        self.index.update(entries())

    def refs(self, question: str, **options) -> list[str]:
        return [hit.passage.ref for hit in self.index.search(question, **options)]

    def test_words_endings_and_umlauts(self) -> None:
        self.assertEqual(self.refs("Wochenbericht")[0], "story:2")
        self.assertEqual(self.refs("Schnittstellen im Lager"), ["bemerkung:1"])
        self.assertEqual(self.refs("Verstoss"), [])  # ss statt ß ist ein anderes Wort
        self.assertEqual(self.refs("Verstoß"), ["story:2"])
        self.assertEqual(self.refs("prufregel festlegen")[0], "story:1")

    def test_title_counts_more_than_text(self) -> None:
        self.assertEqual(self.refs("Prüfregeln")[0], "story:1")

    def test_named_story_comes_first_with_its_remarks(self) -> None:
        self.assertEqual(self.refs("qg-1"), ["story:1", "bemerkung:1"])
        self.assertEqual(self.refs("Bericht zu QG-1"), ["story:1", "bemerkung:1", "story:2"])
        self.assertEqual(self.refs("qg-1", kinds=(REMARK,)), ["bemerkung:1"])

    def test_nothing_found(self) -> None:
        self.assertEqual(self.refs("Weihnachtsfeier"), [])
        self.assertEqual(self.refs("und oder"), [])
        self.assertEqual(self.refs('"; DROP TABLE passages; --'), [])

    def test_limit_and_snippet(self) -> None:
        self.assertEqual(len(self.index.search("Prüfregeln Einkauf Lager", limit=2)), 2)
        self.assertIn("Verstöße", self.index.search("Verstöße")[0].snippet)

    def test_update_writes_only_what_changed(self) -> None:
        self.assertEqual(self.index.update(entries()), Changes())
        changed = entries()[:3]
        changed[2] = Passage("bemerkung:1", REMARK, "Bemerkung zu QG-1", "Ist geklärt.", key="qg-1")
        self.assertEqual(self.index.update(changed), Changes(added=0, changed=1, removed=1))
        self.assertEqual(self.refs("Schnittstelle"), [])
        self.assertEqual(self.refs("geklärt"), ["bemerkung:1"])
        self.assertEqual(self.refs("Einkauf vertraut"), [])
        self.assertEqual(search.count_line(self.index.counts()), "Im Index: 2 Stories, 1 Bemerkung.")


class TestSources(unittest.TestCase):
    def test_feature_file_gives_stories_and_sections(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "qualitaet.md").write_text(FEATURE, encoding="utf-8")
            found = search.story_passages("Kunde ACME", Path(folder))
        self.assertEqual(
            [(passage.kind, passage.title, passage.key) for passage in found],
            [
                (SECTION, "Datenqualität", ""),
                (SECTION, "Datenqualität: Ziel", ""),
                (STORY, "QG-1: Prüfregeln festlegen (Datenqualität)", "qg-1"),
                (STORY, "QG-2: Wochenbericht über Verstöße (Datenqualität)", "qg-2"),
            ],
        )
        self.assertEqual({(passage.context, passage.source) for passage in found}, {("Kunde ACME", "qualitaet.md")})
        self.assertIn("Artikelnummer", found[2].text)
        self.assertEqual(search.story_passages("x", Path("/gibt/es/nicht")), [])

    def test_remarks_briefs_and_accepted_messages_from_the_database(self) -> None:
        store = Store(":memory:")
        self.addCleanup(store.close)
        store.add_story_note("QG-1: Prüfregeln festlegen", "Datenqualität", "Schwellenwerte fehlen.", date(2026, 11, 20))
        removed = store.add_story_note("", "", "Hat sich erledigt.")
        store.set_note_status(removed.id, NOTE_REMOVED)
        event = StoredEvent("e", "Jour fixe", datetime(2026, 11, 18, 10), datetime(2026, 11, 18, 11), "kunde-acme")
        store.upsert_event(event)
        store.upsert_task("e", "vorbereitung", "Vorbereitung: Jour fixe", "kunde-acme", date(2026, 11, 17), "planner")
        store.upsert_task("e", MOVE, "Nachricht an Erika: Jour fixe verschieben", "kunde-acme", date(2026, 11, 17), "planner")
        prep, move = sorted(store.tasks(), key=lambda task: task.id)
        store.save_brief(prep.id, "Jour fixe", "jour_fixe", "Titel nennt es.", ["Offene Punkte", "Entscheidungen"], "Architekt")
        found = {passage.ref: passage for passage in search.store_passages(store, PROFILE)}
        self.assertEqual(sorted(found), ["bemerkung:1", f"einordnung:{prep.id}"])  # Nachricht noch nicht uebernommen
        remark = found["bemerkung:1"]
        self.assertEqual(
            (remark.title, remark.key),
            ("Bemerkung zu QG-1: Prüfregeln festlegen für das Daily am 20.11.2026", "qg-1"),
        )
        brief = found[f"einordnung:{prep.id}"]
        self.assertEqual((brief.title, brief.context), ("Einordnung: Jour fixe", "Kunde ACME"))
        self.assertIn("- Offene Punkte", brief.text)
        store.set_status(move.id, DONE, "Hallo Erika, könntest du den Jour fixe verlegen? Viele Grüße Alex")
        message = {p.ref: p for p in search.store_passages(store, PROFILE)}[f"nachricht:{move.id}"]
        self.assertEqual((message.kind, message.title), (MESSAGE, "Nachricht an Erika: Jour fixe verschieben"))

    def test_long_text_is_split_at_paragraphs(self) -> None:
        text = "\n\n".join(f"Absatz {number}. " + "Wort " * 60 for number in range(20))
        parts = search.split_text(text, limit=1000)
        self.assertTrue(all(len(part) <= 1000 for part in parts))
        self.assertEqual(" ".join("".join(parts).split()), " ".join(text.replace("\n\n", "").split()))
        pieces = search._pieces("story:x", STORY, "Lang", "A" * 3000 + "\n\n" + "B" * 3000)
        self.assertEqual([piece.ref for piece in pieces][:2], ["story:x/1", "story:x/2"])
        self.assertTrue(pieces[0].title.startswith("Lang (Teil 1 von "))

    def test_output_lines(self) -> None:
        index = Index(":memory:")
        self.addCleanup(index.close)
        index.update([Passage("story:1", STORY, "QG-1: Prüfregeln", "Regeln für Lieferdaten.", "Kunde ACME", "qualitaet.md", "qg-1")])
        self.assertEqual(
            search.hit_lines(index.search("Lieferdaten")),
            ["1. QG-1: Prüfregeln", "   Story · Kunde ACME · qualitaet.md", "   Regeln für Lieferdaten."],
        )


if __name__ == "__main__":
    unittest.main()

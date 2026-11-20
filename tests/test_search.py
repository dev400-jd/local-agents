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
        # Auch Auftragsworte sind Fuellwoerter: Gesucht wird der Inhalt, nicht die Bitte.
        self.assertEqual(search.fts_query("Fasse bitte kurz zusammen, was das nächste Review braucht"), '"review"* OR "braucht"*')
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

    def test_word_part_is_found_if_no_word_starts_like_it(self) -> None:
        self.index.update(entries() + [Passage("story:3", STORY, "QG-3: Quarantäne", "Die Aufbewahrungsfrist beträgt 30 Tage.")])
        hit = self.index.search("Frist")[0]
        self.assertEqual((hit.passage.ref, hit.found_by), ("story:3", search.BY_PART))
        self.assertIn("Aufbewahrungsfrist", hit.snippet)
        self.assertEqual(self.refs("100%_"), [])  # Sonderzeichen sind keine Platzhalter
        self.assertEqual(self.refs("Rist"), [])  # zu kurz fuer die Suche nach Wortteilen

    def test_update_writes_only_what_changed(self) -> None:
        self.assertEqual(self.index.update(entries()), Changes())
        changed = entries()[:3]
        changed[2] = Passage("bemerkung:1", REMARK, "Bemerkung zu QG-1", "Ist geklärt.", key="qg-1")
        self.assertEqual(self.index.update(changed), Changes(added=0, changed=1, removed=1))
        self.assertEqual(self.refs("Schnittstelle"), [])
        self.assertEqual(self.refs("geklärt"), ["bemerkung:1"])
        self.assertEqual(self.refs("Einkauf vertraut"), [])
        self.assertEqual(search.count_line(self.index.counts()), "Im Index: 2 Stories, 1 Bemerkung.")


def fake_embedder(calls: list | None = None):
    """Bildet Texte auf vier Themen ab: Zugang, Bericht, Preise, alles andere."""
    topics = (("zugang", "einloggen", "anmeld"), ("bericht", "auswertung"), ("preis", "schwellenwert"))

    def embed(texts: list[str]) -> list[list[float]]:
        if calls is not None:
            calls.append(list(texts))
        vectors = []
        for text in texts:
            lower = text.lower()
            vector = [float(any(word in lower for word in words)) for words in topics]
            vectors.append(vector + [0.0 if any(vector) else 1.0])
        return vectors

    return embed


class TestMeaning(unittest.TestCase):
    def setUp(self) -> None:
        self.index = Index(":memory:")
        self.addCleanup(self.index.close)
        self.passages = [
            Passage("story:1", STORY, "AN-3: Zugangsverwaltung", "Zugänge für Lieferanten anlegen und sperren."),
            Passage("story:2", STORY, "QG-2: Wochenbericht", "Bericht über Verstöße, Abgleich der Stammdaten."),
            Passage("story:3", STORY, "QG-4: Schwellenwerte", "Ausreißer bei Preisen markieren."),
        ]
        self.index.update(self.passages)
        self.calls: list = []
        self.embedder = fake_embedder(self.calls)

    def test_vectors_are_computed_once_and_follow_changes(self) -> None:
        self.assertEqual(self.index.embed_missing(self.embedder, "modell-a"), 3)
        self.assertEqual(self.index.embed_missing(self.embedder, "modell-a"), 0)
        self.assertEqual(self.calls[0][0], "AN-3: Zugangsverwaltung\nZugänge für Lieferanten anlegen und sperren.")
        changed = [self.passages[0], Passage("story:2", STORY, "QG-2: Wochenbericht", "Neu gefasst."), self.passages[2]]
        self.index.update(changed)
        self.assertEqual((self.index.vector_count(), self.index.embed_missing(self.embedder, "modell-a")), (2, 1))
        # Ein anderes Modell rechnet anders: alle Vektoren neu.
        self.assertEqual(self.index.embed_missing(self.embedder, "modell-b"), 3)
        self.index.update(self.passages[:1])
        self.assertEqual(self.index.vector_count(), 1)

    def test_meaning_finds_what_no_word_matches(self) -> None:
        self.index.embed_missing(self.embedder, "m")
        self.assertEqual(self.index.search("Wer darf sich einloggen?"), [])
        hit = self.index.search("Wer darf sich einloggen?", embedder=self.embedder)[0]
        self.assertEqual((hit.passage.ref, hit.found_by, round(hit.similarity, 2)), ("story:1", search.BY_MEANING, 1.0))

    def test_both_rankings_are_merged_and_meaning_counts_double(self) -> None:
        self.index.embed_missing(self.embedder, "m")
        # Das Wort "Stammdaten" steht nur im Wochenbericht, gemeint ist der Zugang.
        hits = self.index.search("Stammdaten einloggen", embedder=self.embedder)
        self.assertEqual([(hit.passage.ref, hit.found_by) for hit in hits], [("story:1", search.BY_MEANING), ("story:2", search.BY_WORD)])
        both = self.index.search("Wochenbericht", embedder=self.embedder)[0]
        self.assertEqual((both.passage.ref, both.found_by), ("story:2", search.BY_BOTH))

    def test_unrelated_question_finds_nothing_and_key_stays_first(self) -> None:
        self.index.embed_missing(self.embedder, "m")
        self.assertEqual(self.index.search("Wie wird das Wetter?", embedder=self.embedder), [])
        self.index.update(self.passages + [Passage("story:4", STORY, "XY-1: Sonstiges", "Ganz anderes.", key="xy-1")])
        self.index.embed_missing(self.embedder, "m")
        hits = self.index.search("Bericht zu xy-1", embedder=self.embedder)
        self.assertEqual([hit.passage.ref for hit in hits][:2], ["story:4", "story:2"])
        self.assertEqual(hits[0].found_by, search.BY_KEY)

    def test_without_vectors_the_word_search_still_answers(self) -> None:
        hits = self.index.search("Wochenbericht", embedder=self.embedder)
        self.assertEqual([(hit.passage.ref, hit.found_by) for hit in hits], [("story:2", search.BY_WORD)])

    def test_profile_switches_meaning_on_and_failure_falls_back(self) -> None:
        notes: list[str] = []
        self.assertIsNone(search.prepare_meaning(self.index, Profile(), notes))
        self.assertEqual(notes, [])

        def broken(model: str):
            def embed(texts: list[str]) -> list[list[float]]:
                raise search.EmbeddingError("Ollama ist nicht erreichbar")

            return embed

        original = search.ollama_embedder
        search.ollama_embedder = broken
        self.addCleanup(setattr, search, "ollama_embedder", original)
        self.assertIsNone(search.prepare_meaning(self.index, Profile(search_embeddings="bge-m3"), notes))
        self.assertEqual(notes, ["Bedeutungssuche nicht verfügbar, nur Wortsuche: Ollama ist nicht erreichbar"])
        search.ollama_embedder = lambda model: self.embedder
        self.assertIsNotNone(search.prepare_meaning(self.index, Profile(search_embeddings="bge-m3"), notes))
        self.assertEqual(self.index.vector_count(), 3)


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

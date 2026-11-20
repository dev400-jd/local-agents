"""Lokale Suche ueber das eigene Material der Agenten.

Aufruf:
  python -m local_agents.search "Frage oder Stichworte"   die besten Treffer
  python -m local_agents.search -n 10 "Frage"             mehr Treffer (Standard 5)
  python -m local_agents.search index                     Index neu abgleichen, Bestand zeigen

Durchsucht werden:
- die Story-Dateien der Kontexte mit ``stories_dir`` (je Story und je sonstigem
  Abschnitt ein Eintrag),
- deine Bemerkungen aus dem Scrum Master (ohne die entfernten),
- die Einordnungen des Termin-Agenten,
- uebernommene Nachrichten des Nachrichten-Agenten.

Der Index ist eine eigene SQLite-Datei neben der Agenten-Datenbank. Er ist
abgeleitet: Loeschen schadet nicht, der naechste Aufruf baut ihn neu. Vor jeder
Suche wird er abgeglichen; geschrieben wird nur, was sich geaendert hat.

Gesucht wird nach Worten (Volltextsuche von SQLite, Rangfolge BM25), ohne
Modell. Steht im Profil unter ``[search]`` ein Embedding-Modell, kommt die
Bedeutungssuche dazu (siehe README). Was die Wortsuche fuer deutsche Texte tut:
- Gross- und Kleinschreibung und Umlaute spielen keine Rolle,
- Endungen zaehlen nicht ("Schnittstellen" findet "Schnittstelle"),
- Fuellwoerter der Frage werden uebergangen,
- ein Story-Kuerzel in der Frage ("QG-1") holt diese Story nach vorn.
Was sie nicht kann: Bedeutungen ("Auslastung" findet nicht "Kapazitaet") und
Wortteile mitten im Wort ("Schnittstelle" findet nicht "Datenschnittstelle").
"""

import hashlib
import os
import re
import sqlite3
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from local_agents import stories
from local_agents.meeting_types import BY_ID
from local_agents.profile import Profile, ProfileError, load_profile
from local_agents.planner_agent import MOVE
from local_agents.store import DONE, NOTE_REMOVED, Store, db_path

AGENT_NAME = "suche"
STORY, SECTION, REMARK, BRIEF, MESSAGE = "story", "abschnitt", "bemerkung", "einordnung", "nachricht"
KIND_LABELS = {
    STORY: "Story",
    SECTION: "Abschnitt",
    REMARK: "Bemerkung",
    BRIEF: "Einordnung",
    MESSAGE: "Nachricht",
}
DEFAULT_LIMIT = 5
# Laengere Eintraege werden an Absaetzen geteilt, damit ein Treffer in den
# Auftrag eines Agenten passt.
MAX_PASSAGE_CHARS = 2400
TITLE_WEIGHT, TEXT_WEIGHT = 4.0, 1.0
SNIPPET_TOKENS = 24
# Bedeutungssuche: Eintraege, deren Naehe zur Frage darunter liegt, gelten als
# unpassend. An den Messfaellen eingestellt (README, Abschnitt "Suche").
MIN_SIMILARITY = 0.50
EMBED_BATCH = 16
# So viele Kandidaten je Liste gehen in die gemeinsame Rangfolge ein.
CANDIDATES = 20
# Reciprocal Rank Fusion: Ein Eintrag bekommt je Liste 1 / (RRF_K + Rang).
RRF_K = 60
# Die Bedeutungssuche liest die ganze Frage, die Wortsuche belohnt schon ein
# einzelnes gemeinsames Wort. Deshalb zaehlt der Rang nach Bedeutung doppelt.
MEANING_WEIGHT = 2.0

STORY_KEY = re.compile(r"\b([A-Za-z]{1,8})-(\d+[a-z]?)\b")
WORD = re.compile(r"\w+", re.UNICODE)
# Endungen, die fuer die Suche nicht zaehlen; die laengste passende faellt weg.
ENDINGS = ("ungen", "ern", "en", "er", "es", "em", "e", "n", "s")
MIN_STEM = 4
STOPWORDS = frozenset(
    """aber alle als also am an auch auf aus bei bin bis bitte da damit dann das dass dem den der
    des die dies diese dieser doch du durch ein eine einem einen einer eines er es etwas fuer für
    gab gibt hat habe haben hatte ich ihr im in ist ja kann kein keine man mehr mein mich mir mit
    nach nicht noch nur ob oder sein sich sie sind so soll steht um und uns unter vom von vor war
    was welche welcher welches wenn wer werden wie wir wird wo wurde zu zum zur über ueber zwischen
    the of and to a is
    wann warum wieso weshalb womit wofür wozu wohin woher
    nächste nächsten nächster nächstes letzte letzten letzter letztes
    fasse fass zusammen erkläre erklär zeige zeig nenne nenn sage sag gib liste kurz bitte mal
    kannst könntest kann können muss müssen sollen sollte gibt geht machen tun""".split()
)

# Der Index ist abgeleitet: Aendert sich sein Aufbau, wird er verworfen und neu gebaut.
SCHEMA_VERSION = 2
DROP = """
DROP TRIGGER IF EXISTS passages_insert;
DROP TRIGGER IF EXISTS passages_delete;
DROP TRIGGER IF EXISTS vectors_delete;
DROP TABLE IF EXISTS passages_fts;
DROP TABLE IF EXISTS vectors;
DROP TABLE IF EXISTS passages;
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS passages (
    id      INTEGER PRIMARY KEY,
    ref     TEXT NOT NULL UNIQUE,   -- stabile Kennung der Quelle, z. B. "story:<Datei>#<Name>"
    kind    TEXT NOT NULL,
    title   TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    source  TEXT NOT NULL DEFAULT '',  -- woher: Dateiname, "Scrum Master 19.11.2026", ...
    key     TEXT NOT NULL DEFAULT '',  -- Story-Kuerzel, klein geschrieben
    feature TEXT NOT NULL DEFAULT '',  -- Titel des Features, zu dem der Eintrag gehoert
    position INTEGER NOT NULL DEFAULT 0,  -- Stelle der Story in ihrer Datei (1, 2, ...); 0 = keine Story
    grp     TEXT NOT NULL DEFAULT '',  -- gemeinsame Kennung der Teile eines geteilten Eintrags
    text    TEXT NOT NULL,
    digest  TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(
    title, text, content='passages', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);
CREATE TRIGGER IF NOT EXISTS passages_insert AFTER INSERT ON passages BEGIN
    INSERT INTO passages_fts (rowid, title, text) VALUES (new.id, new.title, new.text);
END;
CREATE TRIGGER IF NOT EXISTS passages_delete AFTER DELETE ON passages BEGIN
    INSERT INTO passages_fts (passages_fts, rowid, title, text) VALUES ('delete', old.id, old.title, old.text);
END;
CREATE TABLE IF NOT EXISTS vectors (
    passage_id INTEGER PRIMARY KEY,   -- geaenderte Eintraege bekommen eine neue id, ihr Vektor faellt weg
    model      TEXT NOT NULL,         -- Embedding-Modell, mit dem er berechnet wurde
    vector     BLOB NOT NULL          -- float32, auf Laenge 1 gebracht
);
CREATE TRIGGER IF NOT EXISTS vectors_delete AFTER DELETE ON passages BEGIN
    DELETE FROM vectors WHERE passage_id = old.id;
END;
"""


@dataclass(frozen=True)
class Passage:
    """Ein durchsuchbarer Eintrag: eine Story, ein Abschnitt, eine Bemerkung ..."""

    ref: str
    kind: str
    title: str
    text: str
    context: str = ""
    source: str = ""
    key: str = ""
    feature: str = ""
    position: int = 0  # Stelle der Story in ihrer Datei; 0 = keine Story
    group: str = ""  # gemeinsame Kennung der Teile eines geteilten Eintrags; leer = ref

    @property
    def digest(self) -> str:
        joined = "\x1f".join(
            (self.kind, self.title, self.text, self.context, self.source, self.key, self.feature, str(self.position), self.group)
        )
        return hashlib.sha256(joined.encode()).hexdigest()


# Wodurch ein Treffer gefunden wurde.
BY_KEY, BY_WORD, BY_MEANING, BY_BOTH = "Kürzel", "Wort", "Bedeutung", "Wort und Bedeutung"
BY_PART = "Wortteil"
# Ab so vielen Buchstaben wird ein Wort der Frage auch mitten in anderen Worten gesucht.
MIN_PART = 5


@dataclass(frozen=True)
class Hit:
    passage: Passage
    score: float  # hoeher ist besser
    snippet: str  # Ausschnitt um die Fundstelle
    found_by: str = BY_WORD
    similarity: float | None = None  # Naehe zur Frage (0 bis 1), wenn die Bedeutungssuche lief


# Texte -> je Text ein Vektor. Das Embedding-Modell laeuft lokal ueber Ollama.
Embedder = Callable[[list[str]], list[list[float]]]


class EmbeddingError(RuntimeError):
    """Das Embedding-Modell ist nicht erreichbar oder antwortet nicht wie erwartet."""


def ollama_embedder(model: str) -> Embedder:
    """Embeddings vom lokalen Ollama; baut noch keine Verbindung auf."""
    from local_agents.context_window import OllamaError, api

    def embed(texts: list[str]) -> list[list[float]]:
        try:
            vectors = api("/api/embed", {"model": model, "input": texts}).get("embeddings")
        except OllamaError as exc:
            raise EmbeddingError(str(exc)) from exc
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise EmbeddingError(f"{model} lieferte keine Embeddings.")
        return vectors

    return embed


def _unit(vector: list[float]) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(array))
    return array / norm if norm else array


@dataclass(frozen=True)
class Changes:
    added: int = 0
    changed: int = 0
    removed: int = 0

    def __bool__(self) -> bool:
        return bool(self.added or self.changed or self.removed)


# --- Quellen einsammeln


def split_text(text: str, limit: int = MAX_PASSAGE_CHARS) -> list[str]:
    """Teilt langen Text an Absaetzen in Stuecke von hoechstens ``limit`` Zeichen."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts, current = [], ""
    for paragraph in re.split(r"\n\s*\n", text):
        while len(paragraph) > limit:  # ein einzelner Absatz, der allein zu lang ist
            cut = paragraph.rfind("\n", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if current:
                parts.append(current)
                current = ""
            parts.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if current and len(current) + len(paragraph) + 2 > limit:
            parts.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}" if current else paragraph
    if current:
        parts.append(current)
    return parts


def _pieces(ref: str, kind: str, title: str, text: str, **extra) -> list[Passage]:
    parts = split_text(text)
    if len(parts) <= 1:
        return [Passage(ref, kind, title, parts[0] if parts else "", **extra)]
    return [
        Passage(f"{ref}/{number}", kind, f"{title} (Teil {number} von {len(parts)})", part, group=ref, **extra)
        for number, part in enumerate(parts, start=1)
    ]


def story_passages(label: str, folder: Path) -> list[Passage]:
    """Je Story und je sonstigem Abschnitt der Feature-Dateien ein Eintrag."""
    found: list[Passage] = []
    if not folder.expanduser().is_dir():
        return found
    for feature in stories.load_features(folder):  # ohne Modell: Regel und vorhandener Index
        try:
            name = str(feature.path.relative_to(folder.expanduser()))
        except ValueError:
            name = feature.path.name
        extra = {"context": label, "source": name, "feature": feature.title}
        if feature.intro.strip():
            found += _pieces(f"{SECTION}:{name}#", SECTION, feature.title, feature.intro, **extra)
        for heading, text in feature.sections.items():
            if text.strip():
                title = f"{feature.title}: {heading}"
                found += _pieces(f"{SECTION}:{name}#{heading}", SECTION, title, text, **extra)
        for position, story in enumerate(feature.stories, start=1):
            title = f"{story.name} ({feature.title})"
            found += _pieces(
                f"{STORY}:{name}#{story.name}",
                STORY,
                title,
                story.text,
                key=story.key.casefold(),
                position=position,
                **extra,
            )
    return found


def store_passages(store: Store, profile: Profile) -> list[Passage]:
    """Bemerkungen, Einordnungen und uebernommene Nachrichten aus der Agenten-Datenbank."""
    found: list[Passage] = []
    for note in store.story_notes():
        if note.status == NOTE_REMOVED:
            continue
        when = note.created_at[:10]
        title = f"Bemerkung zu {note.story}" if note.story else "Bemerkung ohne Story"
        if note.daily_date:
            title += f" für das Daily am {note.daily_date:%d.%m.%Y}"
        key = STORY_KEY.search(note.story)
        found.append(
            Passage(
                f"{REMARK}:{note.id}",
                REMARK,
                title,
                note.text,
                source=f"Scrum Master, {when}" if when else "Scrum Master",
                key=key.group(0).casefold() if key else "",
            )
        )
    for task in store.tasks():
        label = profile.label(task.context) if task.context in profile.context_ids() else ""
        brief = store.brief(task.id)
        if brief is not None:
            meeting_type = BY_ID.get(brief.meeting_type)
            lines = [f"Terminart: {meeting_type.label if meeting_type else brief.meeting_type}"]
            if brief.reason:
                lines.append(brief.reason)
            lines += [f"- {item}" for item in brief.deliverables]
            found.append(
                Passage(
                    f"{BRIEF}:{task.id}",
                    BRIEF,
                    f"Einordnung: {brief.title}",
                    "\n".join(lines),
                    context=label,
                    source=f"Termin-Agent, Aufgabe #{task.id}",
                )
            )
        if task.kind == MOVE and task.status == DONE and task.result.strip():
            found.append(
                Passage(
                    f"{MESSAGE}:{task.id}",
                    MESSAGE,
                    task.title,
                    task.result,
                    context=label,
                    source=f"Nachrichten-Agent, Aufgabe #{task.id}",
                )
            )
    return found


def collect(profile: Profile, store: Store) -> list[Passage]:
    """Alles, was durchsuchbar sein soll, aus den Quellen des Profils."""
    found: list[Passage] = []
    for label, folder in stories.story_folders(profile):
        found += story_passages(label, folder)
    return found + store_passages(store, profile)


# --- Index


def index_path() -> Path:
    return Path(os.environ.get("LOCAL_AGENTS_SEARCH_DB", db_path().with_name("search.db")))


class Index:
    """Der Suchindex. ``path`` darf ":memory:" sein."""

    def __init__(self, path: Path | str | None = None) -> None:
        path = index_path() if path is None else path
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        if self.db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            self.db.executescript(DROP)
            self.db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def update(self, passages: Iterable[Passage]) -> Changes:
        """Bringt den Index auf den Stand der Eintraege; schreibt nur Aenderungen."""
        wanted: dict[str, Passage] = {}
        for passage in passages:
            if passage.text.strip():
                wanted[passage.ref] = passage  # doppelte Kennung: der letzte gilt
        known = {row["ref"]: row["digest"] for row in self.db.execute("SELECT ref, digest FROM passages")}
        added = changed = 0
        with self.db:
            gone = [ref for ref in known if ref not in wanted]
            for ref in gone:
                self.db.execute("DELETE FROM passages WHERE ref = ?", (ref,))
            for ref, passage in wanted.items():
                digest = passage.digest
                if known.get(ref) == digest:
                    continue
                if ref in known:
                    self.db.execute("DELETE FROM passages WHERE ref = ?", (ref,))
                    changed += 1
                else:
                    added += 1
                self.db.execute(
                    "INSERT INTO passages (ref, kind, title, context, source, key, feature, position, grp, text, digest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ref,
                        passage.kind,
                        passage.title,
                        passage.context,
                        passage.source,
                        passage.key,
                        passage.feature,
                        passage.position,
                        passage.group or ref,
                        passage.text,
                        digest,
                    ),
                )
        return Changes(added, changed, len(gone))

    def counts(self) -> Counter:
        return Counter(
            {row["kind"]: row["n"] for row in self.db.execute("SELECT kind, count(*) AS n FROM passages GROUP BY kind")}
        )

    # --- Aufbau: Features und ihre Stories

    def features(self) -> list[str]:
        """Titel aller Features im Index."""
        rows = self.db.execute("SELECT DISTINCT feature FROM passages WHERE feature <> '' ORDER BY feature")
        return [row["feature"] for row in rows]

    def stories_of(self, feature: str) -> list[Passage]:
        """Die Stories eines Features in der Reihenfolge der Datei, je Story ihr erster Teil."""
        rows = self.db.execute(
            "SELECT * FROM passages WHERE feature = ? AND kind = ? ORDER BY position, length(ref), ref",
            (feature, STORY),
        )
        first: dict[str, Passage] = {}
        for row in rows:
            first.setdefault(row["grp"], _passage(row))
        return list(first.values())

    def parts(self, group: str) -> list[Passage]:
        """Alle Teile eines Eintrags in der Reihenfolge des Textes."""
        rows = self.db.execute("SELECT * FROM passages WHERE grp = ? ORDER BY length(ref), ref", (group,))
        return [_passage(row) for row in rows]

    def by_key(self, key: str, kinds: tuple[str, ...] = ()) -> list[Passage]:
        """Eintraege mit diesem Story-Kuerzel: die Story zuerst, dann ihre Bemerkungen."""
        kind_filter = f" AND kind IN ({', '.join('?' * len(kinds))})" if kinds else ""
        rows = self.db.execute(
            f"SELECT * FROM passages WHERE key = ?{kind_filter} ORDER BY kind = 'story' DESC, length(ref), ref",
            (key.casefold(), *kinds),
        )
        return [_passage(row) for row in rows]

    def key_prefixes(self) -> set[str]:
        """Die Vorsilben der Story-Kuerzel im Bestand, klein geschrieben ("qg", "an")."""
        rows = self.db.execute("SELECT DISTINCT key FROM passages WHERE key <> ''")
        return {row["key"].rsplit("-", 1)[0] for row in rows}

    # --- Bedeutungssuche

    def vector_count(self) -> int:
        return self.db.execute("SELECT count(*) FROM vectors").fetchone()[0]

    def embed_missing(self, embedder: Embedder, model: str) -> int:
        """Berechnet die Vektoren, die fuer ``model`` noch fehlen; gibt ihre Zahl zurueck."""
        with self.db:
            self.db.execute("DELETE FROM vectors WHERE model <> ?", (model,))
        rows = self.db.execute(
            "SELECT id, title, text FROM passages WHERE id NOT IN (SELECT passage_id FROM vectors) ORDER BY id"
        ).fetchall()
        for start in range(0, len(rows), EMBED_BATCH):
            batch = rows[start : start + EMBED_BATCH]
            vectors = embedder([f"{row['title']}\n{row['text']}" for row in batch])
            with self.db:
                self.db.executemany(
                    "INSERT INTO vectors (passage_id, model, vector) VALUES (?, ?, ?)",
                    [(row["id"], model, _unit(vector).tobytes()) for row, vector in zip(batch, vectors)],
                )
        return len(rows)

    def _by_meaning(self, question: str, embedder: Embedder, kinds: tuple[str, ...]) -> list[tuple[sqlite3.Row, float]]:
        """Eintraege nach Naehe zur Frage, die naechsten zuerst, ohne die unpassenden."""
        kind_filter = f" WHERE p.kind IN ({', '.join('?' * len(kinds))})" if kinds else ""
        rows = self.db.execute(
            f"SELECT p.*, v.vector FROM vectors v JOIN passages p ON p.id = v.passage_id{kind_filter} ORDER BY p.id",
            kinds,
        ).fetchall()
        if not rows:
            return []
        query = _unit(embedder([question])[0])
        matrix = np.frombuffer(b"".join(row["vector"] for row in rows), dtype=np.float32).reshape(len(rows), -1)
        if matrix.shape[1] != query.shape[0]:
            raise EmbeddingError("Die gespeicherten Vektoren passen nicht zum Embedding-Modell.")
        similarities = matrix @ query
        order = np.argsort(-similarities, kind="stable")
        return [
            (rows[index], float(similarities[index]))
            for index in order[:CANDIDATES]
            if similarities[index] >= MIN_SIMILARITY
        ]

    def _by_part(self, question: str, kinds: tuple[str, ...]) -> list[sqlite3.Row]:
        """Eintraege, in denen ein Wort der Frage mitten in einem anderen Wort steht; Titel zuerst."""
        stems = [stem(word) for word in content_words(question)]
        stems = [word for word in dict.fromkeys(stems) if len(word) >= MIN_PART and not word.isdigit()]
        if not stems:
            return []
        patterns = ["%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for word in stems]
        in_title = " OR ".join("p.title LIKE ? ESCAPE '\\'" for _ in patterns)
        in_text = " OR ".join("p.text LIKE ? ESCAPE '\\'" for _ in patterns)
        kind_filter = f" AND p.kind IN ({', '.join('?' * len(kinds))})" if kinds else ""
        rows = self.db.execute(
            f"SELECT p.*, 0.0 AS score, ({in_title}) AS titled FROM passages p "
            f"WHERE (({in_title}) OR ({in_text})){kind_filter} ORDER BY titled DESC, p.id LIMIT ?",
            (*patterns, *patterns, *patterns, *kinds, CANDIDATES),
        ).fetchall()
        return [_with_snippet(row, stems) for row in rows]

    # --- Suchen

    def search(
        self,
        question: str,
        limit: int = DEFAULT_LIMIT,
        kinds: tuple[str, ...] = (),
        embedder: Embedder | None = None,
        feature: str = "",
    ) -> list[Hit]:
        """Die besten Treffer zur Frage, die besten zuerst; leer, wenn nichts passt.

        Mit ``embedder`` kommt zur Wortsuche die Bedeutungssuche: Beide liefern
        eine Rangliste, und ein Eintrag steht umso weiter vorn, je weiter vorn
        er in beiden steht. Eine in der Frage genannte Story steht immer zuerst.
        ``feature`` beschraenkt die Suche auf die Eintraege eines Features.
        """
        hits = self._search(question, max(limit, CANDIDATES) if feature else limit, kinds, embedder)
        if feature:
            hits = [hit for hit in hits if hit.passage.feature == feature]
        return hits[:limit]

    def _search(self, question: str, limit: int, kinds: tuple[str, ...], embedder: Embedder | None) -> list[Hit]:
        keys = story_keys(question)
        query = fts_query(question)
        kind_filter = f" AND p.kind IN ({', '.join('?' * len(kinds))})" if kinds else ""
        hits: dict[str, Hit] = {}
        if keys:
            rows = self.db.execute(
                f"SELECT p.* FROM passages p WHERE p.key IN ({', '.join('?' * len(keys))}){kind_filter} "
                "ORDER BY p.kind = 'story' DESC, p.id",
                (*keys, *kinds),
            )
            for row in rows:
                hits[row["ref"]] = Hit(_passage(row), float("inf"), _lead(row["text"]), BY_KEY)
        words: list[sqlite3.Row] = []
        if query:
            words = self.db.execute(
                "SELECT p.*, -bm25(passages_fts, ?, ?) AS score, "
                "snippet(passages_fts, 1, '', '', ' … ', ?) AS snippet "
                f"FROM passages_fts JOIN passages p ON p.id = passages_fts.rowid "
                f"WHERE passages_fts MATCH ?{kind_filter} ORDER BY score DESC, p.id LIMIT ?",
                (TITLE_WEIGHT, TEXT_WEIGHT, SNIPPET_TOKENS, query, *kinds, max(CANDIDATES, limit)),
            ).fetchall()
        parts: set[str] = set()
        if query and not words:
            # Kein Wort beginnt so: als Wortteil suchen ("Frist" in "Aufbewahrungsfrist").
            words = self._by_part(question, kinds)
            parts = {row["ref"] for row in words}
        meaning = self._by_meaning(question, embedder, kinds) if embedder is not None else []
        similarity = {row["ref"]: value for row, value in meaning}
        scores: dict[str, float] = {}
        found: dict[str, tuple[sqlite3.Row, str]] = {}
        for rank, row in enumerate(words, start=1):
            scores[row["ref"]] = 1 / (RRF_K + rank)
            found[row["ref"]] = (row, " ".join(row["snippet"].split()))
        for rank, (row, _) in enumerate(meaning, start=1):
            scores[row["ref"]] = scores.get(row["ref"], 0.0) + MEANING_WEIGHT / (RRF_K + rank)
            found.setdefault(row["ref"], (row, _lead(row["text"])))
        word_refs = {row["ref"] for row in words}
        for ref in sorted(scores, key=lambda ref: (-scores[ref], found[ref][0]["id"])):
            if ref in hits:
                continue
            row, snippet = found[ref]
            if ref in parts:
                how = BY_PART
            elif ref in word_refs:
                how = BY_BOTH if ref in similarity else BY_WORD
            else:
                how = BY_MEANING
            score = scores[ref] if embedder is not None or ref in parts else row["score"]
            hits[ref] = Hit(_passage(row), score, snippet, how, similarity.get(ref))
        return list(hits.values())[:limit]


def _passage(row: sqlite3.Row) -> Passage:
    return Passage(
        row["ref"],
        row["kind"],
        row["title"],
        row["text"],
        row["context"],
        row["source"],
        row["key"],
        row["feature"],
        row["position"],
        row["grp"],
    )


class _Row(dict):
    """Eine Zeile mit nachgetragenem Ausschnitt; laesst sich lesen wie ``sqlite3.Row``."""


def _with_snippet(row: sqlite3.Row, stems: list[str]) -> _Row:
    flat = " ".join(row["text"].split())
    lower = flat.lower()
    at = min((lower.find(word) for word in stems if word in lower), default=0)
    start = max(0, at - 60)
    piece = flat[start : start + 160]
    snippet = ("… " if start else "") + piece + (" …" if start + 160 < len(flat) else "")
    return _Row({**{key: row[key] for key in row.keys()}, "snippet": snippet})


def _lead(text: str, chars: int = 160) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= chars else flat[:chars].rsplit(" ", 1)[0] + " …"


# --- Frage in eine Suchanfrage uebersetzen


def story_keys(question: str) -> list[str]:
    """Story-Kuerzel in der Frage, klein geschrieben ("qg-1")."""
    return [match.group(0).casefold() for match in STORY_KEY.finditer(question)]


def stem(word: str) -> str:
    """Wort ohne Endung, soweit mindestens ``MIN_STEM`` Buchstaben bleiben."""
    for ending in ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM:
            return word[: -len(ending)]
    return word


def content_words(question: str) -> list[str]:
    """Die Worte der Frage ohne Fuellwoerter und ohne Story-Kuerzel, klein geschrieben."""
    # Kuerzel sucht ``Index.search`` gezielt; ihre Teile ("qg", "1") wuerden nur stoeren.
    # lower() statt casefold(): Das "ß" bleibt, wie es auch im Index steht.
    return [
        word
        for word in WORD.findall(STORY_KEY.sub(" ", question).lower())
        if word not in STOPWORDS and (len(word) >= 2 or word.isdigit())
    ]


def fts_query(question: str) -> str:
    """Die Frage als Volltext-Anfrage: Wortstaemme mit beliebiger Endung, mit ODER verbunden.

    Je mehr und je seltenere Worte ein Eintrag trifft, desto weiter vorn steht
    er (BM25). Leer, wenn die Frage nur aus Fuellwoertern besteht.
    """
    terms: list[str] = []
    for word in content_words(question):
        term = f'"{word}"' if word.isdigit() else f'"{stem(word)}"*'
        if term not in terms:
            terms.append(term)
    return " OR ".join(terms)


# --- Bedienung


def refresh(index: Index, profile: Profile, store: Store) -> Changes:
    """Gleicht den Index mit den Quellen ab."""
    return index.update(collect(profile, store))


def prepare_meaning(index: Index, profile: Profile, notes: list[str] | None = None) -> Embedder | None:
    """Der Embedder aus dem Profil, nachdem fehlende Vektoren berechnet sind.

    None, wenn die Bedeutungssuche abgeschaltet ist oder das Modell nicht
    antwortet; dann gilt die Wortsuche, und ``notes`` nennt den Grund.
    """
    model = profile.search_embeddings
    if not model:
        return None
    embedder = ollama_embedder(model)
    try:
        index.embed_missing(embedder, model)
    except EmbeddingError as exc:
        if notes is not None:
            notes.append(f"Bedeutungssuche nicht verfügbar, nur Wortsuche: {exc}")
        return None
    return embedder


def hit_lines(hits: list[Hit]) -> list[str]:
    lines = []
    for number, hit in enumerate(hits, start=1):
        passage = hit.passage
        where = " · ".join(part for part in (KIND_LABELS[passage.kind], passage.context, passage.source) if part)
        lines.append(f"{number}. {passage.title}")
        if hit.similarity is not None:
            where += f" · gefunden über {hit.found_by}, Nähe {hit.similarity:.2f}"
        elif hit.found_by != BY_WORD:
            where += f" · gefunden über {hit.found_by}"
        lines.append(f"   {where}")
        lines.append(f"   {hit.snippet}")
    return lines


KIND_PLURALS = {
    STORY: "Stories",
    SECTION: "Abschnitte",
    REMARK: "Bemerkungen",
    BRIEF: "Einordnungen",
    MESSAGE: "Nachrichten",
}


def count_line(counts: Counter) -> str:
    parts = [
        f"{counts[kind]} {KIND_LABELS[kind] if counts[kind] == 1 else KIND_PLURALS[kind]}"
        for kind in KIND_LABELS
        if counts[kind]
    ]
    return "Im Index: " + (", ".join(parts) if parts else "nichts") + "."


def main(argv: list[str]) -> int:
    args = argv[1:]
    limit = DEFAULT_LIMIT
    if len(args) >= 2 and args[0] == "-n" and args[1].isdigit():
        limit, args = int(args[1]), args[2:]
    if len(args) != 1 or args[0].startswith("-") or limit < 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    store, index = Store(), Index()
    try:
        changes = refresh(index, profile, store)
        notes: list[str] = []
        embedder = prepare_meaning(index, profile, notes)
        if notes:
            print("\n".join(notes), file=sys.stderr)
        if args[0] == "index":
            print(f"Abgeglichen: {changes.added} neu, {changes.changed} geändert, {changes.removed} entfernt.")
            print(count_line(index.counts()))
            print(
                f"Bedeutungssuche: {profile.search_embeddings}, {index.vector_count()} Einträge mit Vektor."
                if embedder is not None
                else "Bedeutungssuche: aus (Profil: embeddings unter [search])."
            )
            return 0
        try:
            hits = index.search(args[0], limit, embedder=embedder)
        except EmbeddingError as exc:
            print(f"Bedeutungssuche nicht verfügbar, nur Wortsuche: {exc}", file=sys.stderr)
            hits = index.search(args[0], limit)
        if not hits:
            print("Keine Treffer. " + count_line(index.counts()))
            return 0
        print("\n".join(hit_lines(hits)))
        return 0
    finally:
        index.close()
        store.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))

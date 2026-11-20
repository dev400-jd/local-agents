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
Modell. Was die Wortsuche fuer deutsche Texte tut:
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
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

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
    the of and to a is""".split()
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS passages (
    id      INTEGER PRIMARY KEY,
    ref     TEXT NOT NULL UNIQUE,   -- stabile Kennung der Quelle, z. B. "story:<Datei>#<Name>"
    kind    TEXT NOT NULL,
    title   TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    source  TEXT NOT NULL DEFAULT '',  -- woher: Dateiname, "Scrum Master 19.11.2026", ...
    key     TEXT NOT NULL DEFAULT '',  -- Story-Kuerzel, klein geschrieben
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

    @property
    def digest(self) -> str:
        joined = "\x1f".join((self.kind, self.title, self.text, self.context, self.source, self.key))
        return hashlib.sha256(joined.encode()).hexdigest()


@dataclass(frozen=True)
class Hit:
    passage: Passage
    score: float  # hoeher ist besser
    snippet: str  # Ausschnitt um die Fundstelle


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
        Passage(f"{ref}/{number}", kind, f"{title} (Teil {number} von {len(parts)})", part, **extra)
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
        extra = {"context": label, "source": name}
        if feature.intro.strip():
            found += _pieces(f"{SECTION}:{name}#", SECTION, feature.title, feature.intro, **extra)
        for heading, text in feature.sections.items():
            if text.strip():
                title = f"{feature.title}: {heading}"
                found += _pieces(f"{SECTION}:{name}#{heading}", SECTION, title, text, **extra)
        for story in feature.stories:
            title = f"{story.name} ({feature.title})"
            found += _pieces(
                f"{STORY}:{name}#{story.name}", STORY, title, story.text, key=story.key.casefold(), **extra
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
                    "INSERT INTO passages (ref, kind, title, context, source, key, text, digest) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ref, passage.kind, passage.title, passage.context, passage.source, passage.key, passage.text, digest),
                )
        return Changes(added, changed, len(gone))

    def counts(self) -> Counter:
        return Counter(
            {row["kind"]: row["n"] for row in self.db.execute("SELECT kind, count(*) AS n FROM passages GROUP BY kind")}
        )

    def search(self, question: str, limit: int = DEFAULT_LIMIT, kinds: tuple[str, ...] = ()) -> list[Hit]:
        """Die besten Treffer zur Frage, die besten zuerst; leer, wenn nichts passt."""
        keys = story_keys(question)
        query = fts_query(question)
        hits: dict[str, Hit] = {}
        kind_filter = f" AND p.kind IN ({', '.join('?' * len(kinds))})" if kinds else ""
        if keys:
            # Eine genannte Story steht vorn, auch wenn sonst kein Wort passt.
            rows = self.db.execute(
                f"SELECT p.* FROM passages p WHERE p.key IN ({', '.join('?' * len(keys))}){kind_filter} "
                "ORDER BY p.kind = 'story' DESC, p.id",
                (*keys, *kinds),
            )
            for row in rows:
                hits[row["ref"]] = Hit(_passage(row), float("inf"), _lead(row["text"]))
        if query:
            rows = self.db.execute(
                "SELECT p.*, -bm25(passages_fts, ?, ?) AS score, "
                "snippet(passages_fts, 1, '', '', ' … ', ?) AS snippet "
                f"FROM passages_fts JOIN passages p ON p.id = passages_fts.rowid "
                f"WHERE passages_fts MATCH ?{kind_filter} ORDER BY score DESC, p.id LIMIT ?",
                (TITLE_WEIGHT, TEXT_WEIGHT, SNIPPET_TOKENS, query, *kinds, limit + len(hits)),
            )
            for row in rows:
                if row["ref"] not in hits:
                    hits[row["ref"]] = Hit(_passage(row), row["score"], " ".join(row["snippet"].split()))
        return list(hits.values())[:limit]


def _passage(row: sqlite3.Row) -> Passage:
    return Passage(row["ref"], row["kind"], row["title"], row["text"], row["context"], row["source"], row["key"])


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


def fts_query(question: str) -> str:
    """Die Frage als Volltext-Anfrage: Wortstaemme mit beliebiger Endung, mit ODER verbunden.

    Je mehr und je seltenere Worte ein Eintrag trifft, desto weiter vorn steht
    er (BM25). Leer, wenn die Frage nur aus Fuellwoertern besteht.
    """
    terms: list[str] = []
    # Kuerzel sucht ``Index.search`` gezielt; ihre Teile ("qg", "1") wuerden nur stoeren.
    # lower() statt casefold(): Das "ß" bleibt, wie es auch im Index steht.
    for word in WORD.findall(STORY_KEY.sub(" ", question).lower()):
        if word in STOPWORDS or (len(word) < 2 and not word.isdigit()):
            continue
        term = f'"{word}"' if word.isdigit() else f'"{stem(word)}"*'
        if term not in terms:
            terms.append(term)
    return " OR ".join(terms)


# --- Bedienung


def refresh(index: Index, profile: Profile, store: Store) -> Changes:
    """Gleicht den Index mit den Quellen ab."""
    return index.update(collect(profile, store))


def hit_lines(hits: list[Hit]) -> list[str]:
    lines = []
    for number, hit in enumerate(hits, start=1):
        passage = hit.passage
        where = " · ".join(part for part in (KIND_LABELS[passage.kind], passage.context, passage.source) if part)
        lines.append(f"{number}. {passage.title}")
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
        if args[0] == "index":
            print(f"Abgeglichen: {changes.added} neu, {changes.changed} geändert, {changes.removed} entfernt.")
            print(count_line(index.counts()))
            return 0
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

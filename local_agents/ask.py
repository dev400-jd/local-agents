"""Gespraechs-Agent: beantwortet Fragen zum Inhalt der Stories.

Aufruf:
  python -m local_agents.ask                  Gespraech im Terminal
  python -m local_agents.ask "Frage"          eine einzelne Frage

Im Gespraech: Leere Zeile oder /ende beendet, /neu beginnt ein neues Thema,
/quellen zeigt die Eintraege, aus denen die letzte Antwort stammt.

Der Ablauf je Frage:

1. Auswaehlen (Code): Welche Eintraege braucht die Antwort? Der Code liest die
   Frage nach Story-Kuerzeln ("QG-3"), nach einem genannten Feature und nach
   Worten fuer eine Stelle im Feature ("als Letztes", "zuerst", "nach QG-3").
   Sonst sucht er (search.py, mit Bedeutungssuche, wenn sie im Profil steht).
   Bei einer Anschlussfrage bleibt die Story der vorigen Antwort dabei.
2. Antworten (Modell): Das Modell bekommt nur diese Eintraege, die Gliederung
   des Features und die letzten Wortwechsel, und antwortet in der verlangten
   Form.
3. Pruefen (Code): Jedes Story-Kuerzel und jede Zahl der Antwort muss in dem
   stehen, was das Modell gesehen hat. Sonst geht die Antwort zurueck ans
   Modell; gelingt es nicht, zeigt der Agent die Fundstellen statt einer
   Antwort.

Findet der Code nichts, sagt er das selbst; das Modell wird dann nicht gefragt.
Der Agent liest nur. Er aendert weder Stories noch Bemerkungen.
"""

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior

from local_agents import search
from local_agents.agent import MODEL_SETTINGS, ollama_model
from local_agents.checks import invented_numbers
from local_agents.profile import ProfileError, load_profile
from local_agents.search import REMARK, STORY, Embedder, Index, Passage
from local_agents.store import Store

AGENT_NAME = "fragen"
MODEL, CODE, FALLBACK = "modell", "code", "fundstellen"
# So viele Zeichen an Eintraegen bekommt das Modell (rund 3000 Tokens). Mit
# Auftrag, Gliederung und Antwort passt das in ein Kontextfenster ab 8K.
ENTRY_BUDGET = 9000
SEARCH_HITS = 4
HISTORY_TURNS = 2
MAX_HISTORY_CHARS = 600
NOTHING_FOUND = "Dazu habe ich in den Stories und Bemerkungen nichts gefunden."

FIRST, LAST, NEXT, PREVIOUS = "erste", "letzte", "nächste", "vorige"
LAST_WORDS = re.compile(r"\b(letzte[nrs]?|zuletzt|am ende|am schluss|abschließend|abschluss|endet|hört)\b")
FIRST_WORDS = re.compile(r"\b(erste[nrs]?|zuerst|am anfang|zu beginn|als erstes|beginnt|fängt|startet)\b")
# Eine Anschlussfrage bezieht sich auf die Story der vorigen Antwort: Sie
# beginnt mit "und", verweist ("dazu", "dort", "sie") oder ist sehr kurz.
FOLLOW_UP = re.compile(
    r"^(und|aber|also|oder)\b|\b(dazu|davon|dafür|dabei|daran|darin|dort|da|diese[mnrs]?|dieselbe[n]?|sie|ihre?[mnrs]?|er|es)\b"
)
MAX_FOLLOW_UP_WORDS = 2
OVERVIEW_HITS = 2
OVERVIEW_WORDS = re.compile(
    r"\b(welche (user )?stories|alle stories|wie viele stories|überblick|übersicht|gliederung|reihenfolge)\b"
)
KEY = r"[a-z]{1,8}-\d+[a-z]?"
AFTER_KEY = re.compile(rf"\b(?:nach|hinter)\s+({KEY})\b")
BEFORE_KEY = re.compile(rf"\bvor\s+({KEY})\b")
# Worte, die in fast jedem Feature-Titel stehen und deshalb keines benennen.
GENERIC_TITLE_WORDS = frozenset(
    "feature features story stories user planung plan zielbild anforderungen konzept stand übersicht".split()
)
LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
PART_SUFFIX = re.compile(r" \(Teil \d+ von \d+\)$")


@dataclass(frozen=True)
class Selection:
    """Was der Code fuer eine Frage ausgewaehlt hat."""

    entries: tuple[Passage, ...] = ()  # in dieser Reihenfolge bekommt sie das Modell
    feature: str = ""  # das Feature, um das es geht; leer = keines erkannt
    outline: tuple[str, ...] = ()  # Stories des Features in der Reihenfolge der Datei
    how: str = ""  # in Worten, wie ausgewaehlt wurde
    focus: str = ""  # Gruppe der Story, bei der eine Anschlussfrage bleibt

    def titles(self) -> list[str]:
        """Titel der Eintraege ohne den Zusatz zu Teilen, jeder einmal."""
        return list(dict.fromkeys(PART_SUFFIX.sub("", entry.title) for entry in self.entries))


@dataclass(frozen=True)
class Answer:
    text: str
    source: str  # MODEL, CODE oder FALLBACK
    selection: Selection


# --- Auswaehlen


def title_words(title: str) -> set[str]:
    """Wortstaemme eines Feature-Titels, die es von anderen unterscheiden koennen."""
    return {
        search.stem(word)
        for word in search.content_words(title)
        if word not in GENERIC_TITLE_WORDS and len(word) >= 4 and not word.isdigit()
    }


def named_feature(question: str, features: list[str]) -> str:
    """Das Feature, das die Frage nennt; leer, wenn keines oder mehrere passen.

    Es zaehlen nur Worte, die im Titel genau eines Features stehen.
    """
    words = {feature: title_words(feature) for feature in features}
    asked = {search.stem(word) for word in search.content_words(question) if len(word) >= 4}
    scores = {}
    for feature, own in words.items():
        others = set().union(*(other for name, other in words.items() if name != feature))
        distinctive = own - others
        scores[feature] = sum(
            any(word.startswith(title_word) or title_word.startswith(word) for word in asked)
            for title_word in distinctive
        )
    best = max(scores.values(), default=0)
    winners = [feature for feature, score in scores.items() if score == best]
    return winners[0] if best and len(winners) == 1 else ""


def position_asked(question: str) -> tuple[str, str]:
    """(Stelle, Kuerzel): FIRST/LAST ohne Kuerzel, NEXT/PREVIOUS mit dem Kuerzel, auf das sie sich beziehen."""
    lower = question.lower()
    after, before = AFTER_KEY.search(lower), BEFORE_KEY.search(lower)
    if after:
        return NEXT, after.group(1)
    if before:
        return PREVIOUS, before.group(1)
    if LAST_WORDS.search(lower):
        return LAST, ""
    if FIRST_WORDS.search(lower):
        return FIRST, ""
    return "", ""


def _budgeted(passages: list[Passage]) -> tuple[Passage, ...]:
    """Die Eintraege der Reihe nach, bis das Budget voll ist; der erste immer."""
    taken: list[Passage] = []
    used = 0
    for passage in passages:
        if passage.ref in {entry.ref for entry in taken}:
            continue
        if taken and used + len(passage.text) > ENTRY_BUDGET:
            continue
        taken.append(passage)
        used += len(passage.text)
    return tuple(taken)


def _whole(index: Index, story: Passage) -> list[Passage]:
    """Die Story mit allen Teilen, danach die Bemerkungen zu ihr."""
    remarks = index.by_key(story.key, (REMARK,)) if story.key else []
    return index.parts(story.group or story.ref) + remarks


def _outline(stories: list[Passage]) -> tuple[str, ...]:
    return tuple(PART_SUFFIX.sub("", story.title).rsplit(" (", 1)[0] for story in stories)


def select(index: Index, question: str, embedder: Embedder | None = None, focus: str = "") -> Selection:
    """Waehlt die Eintraege zur Frage aus. ``focus`` ist die Story der vorigen Antwort."""
    features = index.features()
    feature = named_feature(question, features)
    keys = search.story_keys(question)
    position, anchor = position_asked(question)
    focused = index.parts(focus) if focus else []

    # 1. Stelle im Feature: "als Letztes", "zuerst", "nach QG-3".
    if position:
        if anchor:
            anchored = index.by_key(anchor, (STORY,))
            feature = anchored[0].feature if anchored else feature
        if not feature:
            # Ohne genanntes Feature: das des bisherigen Themas, sonst das einzige.
            feature = focused[0].feature if focused else (features[0] if len(features) == 1 else "")
        stories = index.stories_of(feature) if feature else []
        chosen = None
        if stories and position == LAST:
            chosen = stories[-1]
        elif stories and position == FIRST:
            chosen = stories[0]
        elif stories and anchor:
            at = next((number for number, story in enumerate(stories) if story.key == anchor), None)
            if at is not None:
                other = at + 1 if position == NEXT else at - 1
                chosen = stories[other] if 0 <= other < len(stories) else None
            if chosen is None and at is not None:
                # Es gibt keine Story davor oder danach: die genannte zeigen, die Gliederung klaert den Rest.
                return Selection(
                    _budgeted(_whole(index, stories[at])),
                    feature,
                    _outline(stories),
                    f"{anchor.upper()} ist die {FIRST if position == PREVIOUS else LAST} Story im Feature „{feature}“",
                    stories[at].group or stories[at].ref,
                )
        if chosen is not None:
            return Selection(
                _budgeted(_whole(index, chosen)),
                feature,
                _outline(stories),
                f"{position} Story im Feature „{feature}“" + (f" (bezogen auf {anchor.upper()})" if anchor else ""),
                chosen.group or chosen.ref,
            )

    # 2. Genannte Stories.
    if keys:
        found: list[Passage] = []
        first: Passage | None = None
        for key in dict.fromkeys(keys):
            for passage in index.by_key(key):
                if passage.kind == STORY:
                    first = first or passage
                    found += index.parts(passage.group or passage.ref)
                else:
                    found.append(passage)
        if found:
            feature = first.feature if first else feature
            return Selection(
                _budgeted(found),
                feature,
                _outline(index.stories_of(feature)) if feature else (),
                "Kürzel in der Frage: " + ", ".join(key.upper() for key in dict.fromkeys(keys)),
                (first.group or first.ref) if first else "",
            )

    # 3. Anschlussfrage: Es bleibt bei der Story der vorigen Antwort.
    overview = bool(OVERVIEW_WORDS.search(question.lower()))
    if focused and not feature and not overview and is_follow_up(question):
        feature = focused[0].feature
        return Selection(
            _budgeted(_whole(index, focused[0])),
            feature,
            _outline(index.stories_of(feature)) if feature else (),
            "bisheriges Thema",
            focus,
        )

    # 4. Suche, in einem genannten Feature zuerst dort.
    if not feature and overview:
        feature = focused[0].feature if focused else (features[0] if len(features) == 1 else "")
        if not feature and features:
            # Mehrere Features und keines genannt: die Features selbst sind die Gliederung.
            lines = []
            for name in features:
                names = _outline(index.stories_of(name))
                lines.append(f"{name}: {len(names)} Stories" + (f" ({names[0]} bis {names[-1]})" if names else ""))
            return Selection(outline=tuple(lines), how="Übersicht aller Features")
    limit = OVERVIEW_HITS if overview and feature else SEARCH_HITS
    hits = index.search(question, limit, embedder=embedder, feature=feature) if feature else []
    seen = {hit.passage.ref for hit in hits}
    hits += [hit for hit in index.search(question, limit, embedder=embedder) if hit.passage.ref not in seen]
    found = [hit.passage for hit in hits[:limit]]
    outline = _outline(index.stories_of(feature)) if feature else ()
    if not found and not (overview and outline):
        return Selection(how="nichts gefunden")
    how = "Suche" + (f" im Feature „{feature}“" if feature else "")
    if overview and outline:
        how = f"Gliederung des Features „{feature}“"
    top = next((passage for passage in found if passage.kind == STORY), None)
    return Selection(_budgeted(found), feature, outline, how, (top.group or top.ref) if top else "")


def is_follow_up(question: str) -> bool:
    """Ob die Frage ohne die vorige Antwort nicht zu verstehen ist."""
    lower = question.lower().strip()
    return bool(FOLLOW_UP.search(lower)) or len(search.content_words(lower)) <= MAX_FOLLOW_UP_WORDS


# --- Antworten


@dataclass
class AskDeps:
    selection: Selection
    question: str
    history: list[tuple[str, str]] = field(default_factory=list)  # (Frage, Antwort), aelteste zuerst
    prefixes: set[str] = field(default_factory=set)  # Vorsilben der Story-Kuerzel im Bestand


def entries_text(selection: Selection) -> str:
    """Die Eintraege, wie das Modell sie sieht: nummeriert, mit Titel und Quelle."""
    blocks = []
    for number, entry in enumerate(selection.entries, start=1):
        where = " · ".join(part for part in (search.KIND_LABELS[entry.kind], entry.source) if part)
        blocks.append(f"[{number}] {entry.title} ({where})\n{entry.text}")
    return "\n\n".join(blocks)


def outline_text(selection: Selection) -> str:
    if not selection.outline:
        return ""
    lines = [f"{number}. {name}" for number, name in enumerate(selection.outline, start=1)]
    if not selection.feature:
        return f"Features im Bestand ({len(lines)}):\n" + "\n".join(lines)
    return f"Gliederung des Features „{selection.feature}“ ({len(lines)} Stories, in dieser Reihenfolge):\n" + "\n".join(lines)


def history_text(history: list[tuple[str, str]]) -> str:
    lines = []
    for asked, answered in history[-HISTORY_TURNS:]:
        short = " ".join(answered.split())
        if len(short) > MAX_HISTORY_CHARS:
            short = short[:MAX_HISTORY_CHARS].rsplit(" ", 1)[0] + " …"
        lines += [f"Nutzer: {asked}", f"Du: {short}"]
    return "\n".join(lines)


def facts(deps: AskDeps) -> str:
    """Alles, was das Modell zu sehen bekommt; daran wird die Antwort geprueft."""
    parts = [entries_text(deps.selection), outline_text(deps.selection), history_text(deps.history), deps.question]
    return "\n\n".join(part for part in parts if part)


ask_agent = Agent(deps_type=AskDeps, model_settings=MODEL_SETTINGS, retries=3)


@ask_agent.instructions
def instructions(ctx: RunContext[AskDeps]) -> str:
    deps = ctx.deps
    text = (
        "Du beantwortest Fragen zu den User Stories eines Projekts. Regeln:\n"
        "- Antworte auf Deutsch und nur mit dem, was in den Einträgen und der Gliederung "
        "unten steht. Kein Wissen von außerhalb, keine Vermutungen.\n"
        "- Beantworten die Einträge die Frage nicht oder nur zum Teil, sage das in einem "
        "Satz und nenne, was dazu dort steht. Rate nicht.\n"
        "- Halte dich an die verlangte Form (etwa „in einem Satz“, „als Liste“). Ohne "
        "Vorgabe: höchstens fünf Sätze oder eine kurze Liste.\n"
        "- Nenne Stories mit ihrem Kürzel, wenn sie eines haben.\n"
        "- Erfinde keine Zahlen, Termine, Namen oder Kürzel. Übernimm Zahlen und "
        "Fachbegriffe wörtlich.\n"
        "- Keine Einleitung, keine Wiederholung der Frage, keine Rückfrage am Ende.\n"
    )
    outline = outline_text(deps.selection)
    if outline:
        text += f"\n{outline}\n"
    text += f"\nEinträge:\n{entries_text(deps.selection) or '(keine)'}\n"
    history = history_text(deps.history)
    if history:
        text += (
            "\nBisheriges Gespräch (nur zum Verständnis der Frage; die Antwort stützt sich "
            f"auf die Einträge):\n{history}\n"
        )
    return text


def problems(answer: str, deps: AskDeps) -> list[str]:
    """Was an der Antwort nicht belegt ist; leer, wenn sie so stehen bleiben kann."""
    found = []
    if not answer.strip():
        return ["die Antwort ist leer"]
    seen = facts(deps)
    known = seen.casefold()
    unknown = [
        key.upper()
        for key in dict.fromkeys(search.story_keys(answer))
        if key.rsplit("-", 1)[0] in deps.prefixes and key not in known
    ]
    if unknown:
        found.append(f"Story-Kürzel, die nicht in den Einträgen stehen: {', '.join(unknown)}")
    invented = invented_numbers(LIST_MARKER.sub("", answer), seen)
    if invented:
        found.append(f"Zahlen, die nicht in den Einträgen stehen: {', '.join(invented)}")
    return found


@ask_agent.output_validator
def checked(ctx: RunContext[AskDeps], answer: str) -> str:
    # Geschuetzte Bindestriche (U+2010, U+2011) schreibt das Modell gern in Kuerzel; dann
    # liessen sie sich weder pruefen noch kopieren.
    answer = answer.replace("\u2010", "-").replace("\u2011", "-")
    answer = "\n".join(line.rstrip() for line in answer.strip().splitlines())
    found = problems(answer, ctx.deps)
    if found:
        raise ModelRetry(
            "Die Antwort ist nicht belegt, bitte neu schreiben und nur verwenden, was in den "
            "Einträgen steht: " + "; ".join(found) + "."
        )
    return answer


def fallback_text(selection: Selection) -> str:
    lines = ["Das Modell hat keine belegte Antwort geliefert. Die Fundstellen dazu:"]
    lines += [f"- {entry.title}: {search._lead(entry.text)}" for entry in selection.entries]
    return "\n".join(lines)


def source_line(selection: Selection) -> str:
    """Eine Zeile fuer den Nutzer: woraus die Antwort stammt und wie ausgewaehlt wurde."""
    titles = selection.titles()
    if not titles and not selection.outline:
        return ""
    names = "; ".join(title.rsplit(" (", 1)[0] for title in titles) or "nur die Gliederung"
    return f"Grundlage ({selection.how}): {names}"


class Conversation:
    """Ein Gespraech: merkt sich die letzten Wortwechsel und die Story, um die es geht."""

    def __init__(self, index: Index, model, embedder: Embedder | None = None, store: Store | None = None) -> None:
        self.index = index
        self.model = model
        self.embedder = embedder
        self.store = store
        self.history: list[tuple[str, str]] = []
        self.focus = ""
        self.last: Answer | None = None

    def reset(self) -> None:
        self.history.clear()
        self.focus = ""
        self.last = None

    def ask(self, question: str) -> Answer:
        try:
            selection = select(self.index, question, self.embedder, self.focus)
        except search.EmbeddingError:
            selection = select(self.index, question, None, self.focus)  # ohne Bedeutungssuche weiter
        if not selection.entries and not selection.outline:
            answer = Answer(NOTHING_FOUND, CODE, selection)
        else:
            deps = AskDeps(selection, question, list(self.history), self.index.key_prefixes())
            run = self.store.start_run(AGENT_NAME) if self.store else None
            try:
                text = ask_agent.run_sync(question, deps=deps, model=self.model).output
                answer = Answer(text, MODEL, selection)
            except UnexpectedModelBehavior:
                answer = Answer(fallback_text(selection), FALLBACK, selection)
            if self.store and run is not None:
                self.store.finish_run(run, "ok" if answer.source == MODEL else "ohne Antwort", selection.how)
            self.focus = selection.focus
        self.history.append((question, answer.text))
        self.last = answer
        return answer


def answer_lines(answer: Answer) -> str:
    line = source_line(answer.selection)
    return answer.text + (f"\n\n{line}" if line else "")


def chat(conversation: Conversation, ask: Callable[[str], str] = input, out: Callable[[str], None] = print) -> None:
    """Das Gespraech im Terminal."""
    out("Frag mich etwas zu den Stories. Leere Zeile oder /ende beendet, /neu wechselt das Thema, /quellen zeigt die Einträge.")
    while True:
        try:
            question = ask("\nDu: ").strip()
        except (EOFError, KeyboardInterrupt):
            out("")
            return
        if not question or question == "/ende":
            return
        if question == "/neu":
            conversation.reset()
            out("Neues Thema.")
            continue
        if question == "/quellen":
            last = conversation.last
            out(entries_text(last.selection) if last and last.selection.entries else "Noch keine Einträge.")
            continue
        out("\n" + answer_lines(conversation.ask(question)))


def main(argv: list[str]) -> int:
    args = argv[1:]
    if len(args) > 1 or (args and args[0].startswith("-")):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    store, index = Store(), Index()
    try:
        search.refresh(index, profile, store)
        notes: list[str] = []
        embedder = search.prepare_meaning(index, profile, notes)
        if notes:
            print("\n".join(notes), file=sys.stderr)
        if not sum(index.counts().values()):
            print("Der Index ist leer: Im Profil hat kein Kontext ein stories_dir mit Story-Dateien.", file=sys.stderr)
            return 1
        conversation = Conversation(index, ollama_model(profile.model_for(AGENT_NAME)), embedder, store)
        if args:
            print(answer_lines(conversation.ask(args[0])))
        else:
            chat(conversation)
        return 0
    finally:
        index.close()
        store.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))

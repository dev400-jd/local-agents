"""User Stories aus Markdown-Dateien lesen, unabhaengig von deren Aufbau.

Aufruf:
  python -m local_agents.stories            alle Features und Stories im Ueberblick
  python -m local_agents.stories QG-1       eine Story vollstaendig (Kuerzel oder Teil des Namens)

Die Dateien liegen in einem Ordner ausserhalb des Repos (``stories_dir`` am
Kontext im Profil) und bleiben, wie sie geschrieben wurden. Gelesen wird in
drei Schichten:

1. Gliederung: Jede Datei wird in ihre Ueberschriften-Abschnitte zerlegt. Das
   geht mit jedem Markdown.
2. Welche Abschnitte sind Stories? Zuerst eine Regel: Ueberschriften, die mit
   einem Kuerzel wie ``QG-1`` beginnen. Findet sie nichts, markiert das lokale
   Modell die Stories anhand der Liste der Ueberschriften; das Ergebnis steht
   danach in ``stories-index.json`` im Ordner und laesst sich dort korrigieren.
3. Felder (Zugabe): Schaetzung, Akzeptanzkriterien, Aufgaben und andere werden
   erkannt, wo sie ueblich geschrieben sind. Verlassen kann man sich nur auf
   Name und Text einer Story.
"""

import hashlib
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior

from local_agents.agent import MODEL_SETTINGS, ollama_model, structured
from local_agents.profile import Profile, ProfileError, load_profile

AGENT_NAME = "stories"
INDEX_FILE = "stories-index.json"
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
FENCE = re.compile(r"^\s*(```|~~~)")
STORY_HEADING = re.compile(r"^([A-Za-z]{1,8}-\d+[a-z]?)(?::|\s)\s*(.+)$")
FIELD_LINE = re.compile(r"^\*\*([^*]+?):?\*\*:?\s*(.*)$")
TABLE_RULE = re.compile(r"^\|[\s:|-]+\|$")
META_SEPARATOR = re.compile(r"\s+[·|]\s+")
ESTIMATE = re.compile(r"^\d+(?:[.,]\d+)?\s*(?:PT|SP|Tage?|Std\.?|h)$", re.IGNORECASE)
STORY, ESTIMATE_FIELD, DETAILS = "Story", "Schätzung", "Angaben"
# Feldnamen, unter denen die Schaetzung stehen kann.
ESTIMATE_NAMES = (ESTIMATE_FIELD, "Aufwand", "Story Points", "Storypoints", "Estimate")
# Tabellen ohne Feldnamen: Stichwort im Tabellenkopf -> Feldname.
TABLE_NAMES = (
    ("kriterium", "Akzeptanzkriterien"),
    ("aufgabe", "Aufgaben"),
    ("voraussetzung", "Voraussetzungen"),
    ("frage", "Klärung"),
)
RULE, INDEX = "Regel", "Index"
# So viele Ueberschriften bekommt das Modell hoechstens zu sehen.
MAX_HEADINGS = 150
PREVIEW_CHARS = 90


@dataclass(frozen=True)
class Section:
    """Ein Ueberschriften-Abschnitt: der Text bis zur naechsten Ueberschrift."""

    level: int
    heading: str
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class Story:
    key: str  # Kuerzel, z. B. "QG-1"; leer, wenn die Ueberschrift keines hat
    title: str  # Ueberschrift ohne Kuerzel
    feature: str
    fields: dict[str, str] = field(default_factory=dict)  # Feldname -> Text (Zugabe)
    group: str = ""  # uebergeordneter Abschnitt, falls die Story darunter steht
    text: str = ""  # der ganze Abschnitt, unveraendert

    @property
    def name(self) -> str:
        """Voller Name, wie er in der Ueberschrift steht; Schluessel zum Projektwerkzeug des Kunden."""
        return f"{self.key}: {self.title}" if self.key else self.title

    @property
    def estimate(self) -> str:
        for name in ESTIMATE_NAMES:
            if self.fields.get(name):
                return self.fields[name]
        return ""

    def table(self, name: str) -> list[dict[str, str]]:
        """Die Tabelle eines Feldes als Zeilen (Spaltenname -> Zelle)."""
        return parse_table(self.fields.get(name, ""))


@dataclass(frozen=True)
class Feature:
    title: str
    path: Path
    intro: str  # Text vor dem ersten Abschnitt
    sections: dict[str, str]  # Abschnitte, die keine Story sind
    stories: tuple[Story, ...]
    found_by: str = RULE  # RULE oder INDEX: wie die Stories erkannt wurden


# --- Schicht 1: Gliederung


def outline(text: str) -> tuple[list[str], list[Section]]:
    """Zerlegt Markdown in den Vorspann und die Ueberschriften-Abschnitte.

    Zeilen in Codebloecken zaehlen nicht als Ueberschrift.
    """
    intro: list[str] = []
    sections: list[tuple[int, str, list[str]]] = []
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
        match = None if fenced else HEADING.match(line)
        if match:
            sections.append((len(match.group(1)), match.group(2).strip(), []))
        elif sections:
            sections[-1][2].append(line)
        else:
            intro.append(line)
    return intro, [Section(level, heading, tuple(lines)) for level, heading, lines in sections]


def _span(sections: list[Section], start: int) -> int:
    """Index hinter dem Abschnitt ``start`` samt seinen Unterabschnitten."""
    end = start + 1
    while end < len(sections) and sections[end].level > sections[start].level:
        end += 1
    return end


# --- Schicht 2: Stories erkennen


def rule_stories(sections: list[Section]) -> set[int]:
    """Abschnitte unterhalb des Titels, deren Ueberschrift mit einem Kuerzel beginnt."""
    return {
        index
        for index, section in enumerate(sections)
        if section.level >= 2 and STORY_HEADING.match(section.heading)
    }


class OutlineAnalysis(BaseModel):
    """Welche Ueberschriften einer Datei einzelne Stories sind."""

    stories: list[int] = Field(description="Nummern der Ueberschriften, die je eine Story sind")


@dataclass
class OutlineDeps:
    sections: list[Section]


outline_agent = Agent(
    deps_type=OutlineDeps, output_type=structured(OutlineAnalysis), model_settings=MODEL_SETTINGS, retries=2
)


def outline_listing(sections: list[Section]) -> str:
    """Die Gliederung als nummerierte Liste mit Ebene, Umfang und Textanfang."""
    lines = []
    for index, section in enumerate(sections[:MAX_HEADINGS]):
        body = [line.strip() for line in section.lines if line.strip() and line.strip() != "---"]
        preview = f" | beginnt: {body[0][:PREVIEW_CHARS]}" if body else ""
        lines.append(
            f"{index}. {'#' * section.level} {section.heading} | {len(body)} Zeilen{preview}"
        )
    return "\n".join(lines)


@outline_agent.instructions
def outline_instructions(ctx: RunContext[OutlineDeps]) -> str:
    return (
        "Unten steht die Gliederung eines Dokuments aus der Projektplanung: je Zeile "
        "Nummer, Ebene, Ueberschrift, Umfang und Textanfang. Nenne die Nummern der "
        "Ueberschriften, die je eine einzelne Story sind, also eine einzelne "
        "umzusetzende Anforderung (User Story, Arbeitspaket, Aufgabe, Ticket).\n"
        "Keine Stories sind: der Dokumenttitel, Einleitung, Ueberblick, Ziele, "
        "Abgrenzung, Glossar, Anhang, offene Punkte, Gruppen- oder Kapitelueberschriften "
        "ueber mehreren Stories und Unterabschnitte innerhalb einer Story (etwa "
        "Akzeptanzkriterien oder Aufgaben). Stories stehen meist auf derselben Ebene "
        "und sind aehnlich aufgebaut. Enthaelt das Dokument keine Stories, gib eine "
        "leere Liste zurueck.\n"
        f"{outline_listing(ctx.deps.sections)}"
    )


@outline_agent.output_validator
def known_headings_only(ctx: RunContext[OutlineDeps], analysis: OutlineAnalysis) -> OutlineAnalysis:
    limit = min(len(ctx.deps.sections), MAX_HEADINGS)
    unknown = [number for number in analysis.stories if not 0 <= number < limit]
    if unknown:
        raise ModelRetry(f"Diese Nummern gibt es nicht: {unknown}. Erlaubt sind 0 bis {limit - 1}.")
    return analysis


def model_stories(sections: list[Section], model) -> set[int]:
    """Laesst das Modell die Stories in der Gliederung markieren."""
    if not sections:
        return set()
    try:
        result = outline_agent.run_sync(
            "Welche Ueberschriften sind Stories?", deps=OutlineDeps(sections), model=model
        )
    except UnexpectedModelBehavior:
        return set()
    return set(result.output.stories)


def _without_groups(sections: list[Section], stories: set[int]) -> set[int]:
    """Entfernt Abschnitte, unter denen selbst Stories stehen: Das sind Gruppen."""
    return {
        index
        for index in stories
        if not any(other in stories for other in range(index + 1, _span(sections, index)))
    }


# --- Index: was das Modell (oder der Nutzer) je Datei festgelegt hat


def read_index(folder: Path) -> dict[str, dict]:
    path = folder / INDEX_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_index(folder: Path, index: dict[str, dict]) -> None:
    text = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True)
    (folder / INDEX_FILE).write_text(text + "\n", encoding="utf-8")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Schicht 3 und Zusammenbau


def parse_table(text: str) -> list[dict[str, str]]:
    """Liest die erste Markdown-Tabelle im Text."""
    rows = []
    for line in text.splitlines():
        if line.strip().startswith("|"):
            rows.append(line.strip())
        elif rows:
            break
    if len(rows) < 2 or not TABLE_RULE.match(rows[1]):
        return []
    header = _cells(rows[0])
    return [dict(zip(header, _cells(row), strict=False)) for row in rows[2:]]


def _cells(row: str) -> list[str]:
    # Ein senkrechter Strich in einer Zelle ist als \| geschrieben.
    parts = re.split(r"(?<!\\)\|", row.strip().strip("|"))
    return [part.strip().replace("\\|", "|") for part in parts]


def parse_feature(text: str, path: Path = Path(), story_headings: list[str] | None = None) -> Feature:
    """Zerlegt eine Datei in Feature-Abschnitte und Stories.

    Ohne ``story_headings`` gilt die Regel (Kuerzel in der Ueberschrift); mit
    der Liste sind genau die Abschnitte mit diesen Ueberschriften Stories.
    """
    intro, sections = outline(text)
    title = path.stem
    if sections and sections[0].level == 1 and not _text(intro):
        title = sections[0].heading.removeprefix("Feature:").strip()
        intro = list(sections[0].lines)
        sections = sections[1:]
    if story_headings is None:
        marked, found_by = rule_stories(sections), RULE
    else:
        wanted = set(story_headings)
        marked = {index for index, section in enumerate(sections) if section.heading in wanted}
        found_by = INDEX
    marked = _without_groups(sections, marked)

    stories, others = [], {}
    parents: list[Section] = []  # Abschnitte, unter denen der aktuelle steht
    index = 0
    while index < len(sections):
        section = sections[index]
        while parents and parents[-1].level >= section.level:
            parents.pop()
        if index not in marked:
            if _text(list(section.lines)):
                others[section.heading] = _text(list(section.lines))
            parents.append(section)
            index += 1
            continue
        end = _span(sections, index)
        lines = list(section.lines)
        for sub in sections[index + 1 : end]:
            lines.append(f"{'#' * sub.level} {sub.heading}")
            lines.extend(sub.lines)
        match = STORY_HEADING.match(section.heading)
        key, story_title = (_key(match.group(1)), match.group(2).strip()) if match else ("", section.heading)
        group = parents[-1].heading if parents else ""
        stories.append(Story(key, story_title, title, _fields(lines), group, _text(lines)))
        index = end
    return Feature(title, path, _text(intro), others, tuple(stories), found_by)


def _key(raw: str) -> str:
    """Kuerzel einheitlich gross, ein angehaengter Buchstabe bleibt klein: ZI-0a."""
    return raw[:-1].upper() + raw[-1] if raw[-1].isalpha() else raw.upper()


def _fields(lines: list[str]) -> dict[str, str]:
    """Die Felder einer Story: Feldname -> Text.

    Felder beginnen mit einem fett gesetzten Namen am Zeilenanfang oder einer
    Unterueberschrift. Davor stehender Text gilt als Story-Satz, eine
    Angabenzeile gleich nach der Ueberschrift wird zerlegt, und Tabellen ohne
    Feldnamen heissen nach ihrem Kopf.
    """
    fields: dict[str, list[str]] = {}
    current: str | None = None
    in_table = False
    seen_content = False
    fenced = False
    # Steht ein Feldname allein in seiner Zeile, gehoeren die folgenden Tabellen
    # zu ihm. Nach "**Name:** Text" in einer Zeile ist eine Tabelle ein eigenes Feld.
    owns_tables = False
    for line in lines:
        stripped = line.strip()
        if FENCE.match(line):
            fenced = not fenced
        if not stripped or stripped == "---":
            in_table = False
            if current is not None:
                fields[current].append("")
            continue
        heading = None if fenced else HEADING.match(stripped)
        field_match = None if fenced else FIELD_LINE.match(stripped)
        if heading or field_match:
            current = heading.group(2).strip() if heading else field_match.group(1).strip()
            inline = "" if heading else field_match.group(2)
            fields.setdefault(current, []).append(inline)
            owns_tables = not inline
            in_table = False
        elif stripped.startswith("|") and not fenced:
            if not in_table and not owns_tables:
                # Tabelle ohne eigenen Feldnamen: nach dem Kopf benennen.
                current = _table_name(stripped, fields)
                fields.setdefault(current, [])
            fields[current].append(stripped)
            in_table = True
        elif not seen_content and _is_meta(stripped):
            for name, value in _meta(stripped):
                fields.setdefault(name, []).append(value)
        else:
            if current is None:
                current = STORY
                fields.setdefault(current, [])
            fields[current].append(stripped.strip("*") if current == STORY else line)
            in_table = False
        seen_content = True
    return {name: text for name, body in fields.items() if (text := _text(body))}


def _is_meta(line: str) -> bool:
    """Ob die Zeile eine Angabenzeile ist, etwa "`kuerzel` · 3 PT · Sprint: 12"."""
    return len(line) < 250 and len(META_SEPARATOR.split(line.strip("`"))) >= 2


def _meta(line: str) -> list[tuple[str, str]]:
    """Zerlegt eine Angabenzeile in (Feldname, Wert); Unbenanntes sammelt "Angaben"."""
    pairs, rest = [], []
    for part in META_SEPARATOR.split(line.strip("`")):
        part = part.strip().strip("`").strip()
        name, colon, value = part.partition(":")
        if ESTIMATE.match(part):
            pairs.append((ESTIMATE_FIELD, part))
        elif colon and name.strip() and len(name) <= 40:
            if value.strip() not in ("", "–", "-"):
                pairs.append((name.strip(), value.strip()))
        elif part:
            rest.append(part)
    if rest:
        pairs.append((DETAILS, " · ".join(rest)))
    return pairs


def _table_name(header: str, fields: dict[str, list[str]]) -> str:
    lowered = header.casefold()
    for keyword, name in TABLE_NAMES:
        if keyword in lowered and name not in fields:
            return name
    number = 1 + sum(name.startswith("Tabelle ") for name in fields)
    return f"Tabelle {number}"


def _text(lines: list[str]) -> str:
    """Zeilen ohne Trennlinien, ohne Leerzeilen am Rand und ohne doppelte Leerzeilen."""
    text = "\n".join(line for line in lines if line.strip() != "---").strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def load_features(
    folder: Path,
    classify: Callable[[list[Section]], set[int]] | None = None,
    notes: list[str] | None = None,
) -> list[Feature]:
    """Alle Features eines Ordners (auch in Unterordnern), nach Titel sortiert.

    Findet die Regel in einer Datei keine Stories, gilt der Eintrag in
    ``stories-index.json``. Fehlt er oder hat sich die Datei geaendert, markiert
    ``classify`` (das Modell) die Stories und der Index wird fortgeschrieben;
    ein Eintrag mit ``"geprueft": true`` bleibt unangetastet. ``notes`` sammelt
    Hinweise fuer den Nutzer. Liegt ein Feature in mehreren Dateien vor (etwa
    mit Datum im Namen), gilt die Datei, deren Name alphabetisch zuletzt kommt.
    """
    folder = folder.expanduser()
    notes = [] if notes is None else notes
    index = read_index(folder)
    changed = False
    features: dict[str, Feature] = {}
    for path in sorted(folder.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        feature = parse_feature(text, path)
        if not feature.stories:
            name = str(path.relative_to(folder))
            entry = index.get(name)
            stale = entry is None or (entry.get("sha256") != _digest(text) and not entry.get("geprueft"))
            if stale and classify is not None:
                sections = outline(text)[1]
                marked = _without_groups(sections, classify(sections))
                entry = {
                    "sha256": _digest(text),
                    "stories": [sections[number].heading for number in sorted(marked)],
                    "quelle": "modell",
                    "geprueft": False,
                }
                index[name] = entry
                changed = True
                if marked:
                    notes.append(
                        f"{name}: {len(marked)} Stories vom Modell erkannt. Bitte prüfen; "
                        f"Korrekturen in {INDEX_FILE} (dort \"geprueft\": true setzen)."
                    )
            if entry and entry.get("stories"):
                feature = parse_feature(text, path, list(entry["stories"]))
            if not feature.stories:
                notes.append(f"{name}: keine Stories erkannt.")
                continue
        features[feature.title.casefold()] = feature
    if changed:
        write_index(folder, index)
    return sorted(features.values(), key=lambda feature: feature.title.casefold())


def story_folders(profile: Profile) -> list[tuple[str, Path]]:
    """(Kontext-Bezeichnung, Ordner) fuer jeden Kontext mit ``stories_dir``."""
    return [
        (context.label, Path(context.stories_dir).expanduser())
        for context in profile.contexts
        if context.stories_dir
    ]


def format_overview(features: list[Feature]) -> str:
    lines = []
    for feature in features:
        how = "" if feature.found_by == RULE else ", vom Modell erkannt"
        lines.append(
            f"Feature: {feature.title} ({len(feature.stories)} Stories, {feature.path.name}{how})"
        )
        group = ""
        for story in feature.stories:
            if story.group != group:
                group = story.group
                if group:
                    lines.append(f"  {group}")
            details = [story.estimate] if story.estimate else []
            for name in ("Akzeptanzkriterien", "Aufgaben"):
                rows = story.table(name)
                if rows:
                    details.append(f"{len(rows)} {name}")
            indent = "    " if story.group else "  "
            lines.append(f"{indent}{story.name}" + (f" [{', '.join(details)}]" if details else ""))
    return "\n".join(lines) or "Keine Stories gefunden."


def format_story(story: Story) -> str:
    return f"{story.name}\nFeature: {story.feature}\n\n{story.text}"


def find_stories(features: list[Feature], wanted: str) -> list[Story]:
    """Stories mit diesem Kuerzel, sonst alle, deren Name den Text enthaelt."""
    stories = [story for feature in features for story in feature.stories]
    by_key = [story for story in stories if story.key.casefold() == wanted.casefold()]
    return by_key or [story for story in stories if wanted.casefold() in story.name.casefold()]


def main(argv: list[str], profile: Profile | None = None, classify=None) -> int:
    if len(argv) > 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        profile = load_profile() if profile is None else profile
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    folders = story_folders(profile)
    if not folders:
        print("Kein Kontext im Profil hat stories_dir gesetzt. Siehe README.", file=sys.stderr)
        return 1
    if classify is None:
        model = ollama_model(profile.model_for(AGENT_NAME))

        def classify(sections: list[Section]) -> set[int]:
            return model_stories(sections, model)

    notes: list[str] = []
    features = [feature for _, folder in folders for feature in load_features(folder, classify, notes)]
    for note in notes:
        print(note, file=sys.stderr)
    if len(argv) == 2:
        found = find_stories(features, argv[1])
        if not found:
            print(f"Keine Story {argv[1]} gefunden.", file=sys.stderr)
            return 1
        print("\n\n".join(format_story(story) for story in found))
        return 0
    print(format_overview(features))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

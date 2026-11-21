"""Text aus Office- und PDF-Dateien lesen, in Abschnitten.

Kein eigener Aufruf: Die Suche (search.py) liest damit die Ordner, die im
Profil unter ``[documents]`` stehen, etwa eine lokal abgeglichene
SharePoint-Bibliothek.

Je Dateityp ein Abschnitt je natuerlicher Einheit:

- Word (.docx): je Ueberschrift, Tabellen als Zeilen mit " | ",
- PowerPoint (.pptx): je Folie, mit Titel, Text, Tabellen und Notizen,
- PDF: je Seite,
- Excel (.xlsx, .xlsm): je Tabellenblatt die ersten Zeilen, Zellen mit " | ",
- Markdown und Text: je Ueberschrift bzw. die ganze Datei.

Gelesen wird nur; keine Datei wird geoeffnet, um sie zu aendern. Dateien, die
OneDrive nur als Platzhalter fuehrt ("nur online"), werden nicht angefasst:
Sie zu lesen wuerde sie herunterladen.

Die Bibliotheken dazu sind eine Zugabe:
  pip install pypdf python-docx python-pptx openpyxl
"""

import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path

from local_agents import stories

SUFFIXES = (".docx", ".pptx", ".pdf", ".xlsx", ".xlsm", ".md", ".txt")
MAX_FILE_BYTES = 60 * 1024 * 1024
# Excel: so viele Zeilen und Zeichen je Blatt; danach ist es selten noch Text fuer die Suche.
MAX_ROWS, MAX_SHEET_CHARS, MAX_CELL_CHARS = 200, 6000, 200
MAX_SECTIONS = 400
# macOS: Datei ohne lokalen Inhalt (OneDrive "nur online").
SF_DATALESS = 0x40000000
HEADING_STYLES = ("heading", "überschrift", "uberschrift", "titel", "title")


@dataclass(frozen=True)
class Section:
    heading: str  # leer: Text vor der ersten Ueberschrift
    text: str


class Unreadable(ValueError):
    """Die Datei laesst sich nicht lesen; der Text sagt, warum."""


def is_online_only(path: Path) -> bool:
    """Ob OneDrive die Datei nur als Platzhalter fuehrt."""
    try:
        return bool(getattr(path.stat(), "st_flags", 0) & SF_DATALESS)
    except OSError:
        return False


def _need(module: str, package: str):
    try:
        return __import__(module)
    except ImportError as exc:
        raise Unreadable(f"die Bibliothek fehlt: pip install {package}") from exc


def _clean(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\x00", " ").splitlines()]
    kept: list[str] = []
    for line in lines:
        if line or (kept and kept[-1]):
            kept.append(line)
    return "\n".join(kept).strip()


def _docx(path: Path) -> list[Section]:
    docx = _need("docx", "python-docx")
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    sections: list[tuple[str, list[str]]] = [("", [])]
    for block in document.element.body.iterchildren():
        if block.tag.endswith("}p"):
            paragraph = Paragraph(block, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name if paragraph.style is not None else "").casefold()
            if style.startswith(HEADING_STYLES):
                sections.append((text, []))
            else:
                sections[-1][1].append(text)
        elif block.tag.endswith("}tbl"):
            for row in Table(block, document).rows:
                cells = [" ".join(cell.text.split()) for cell in row.cells]
                # Verbundene Zellen kommen mehrfach; aufeinanderfolgende Wiederholungen fallen weg.
                cells = [cell for number, cell in enumerate(cells) if number == 0 or cell != cells[number - 1]]
                if any(cells):
                    sections[-1][1].append(" | ".join(cells))
    return [Section(heading, "\n".join(lines)) for heading, lines in sections]


def _shape_texts(shapes) -> list[str]:
    found: list[str] = []
    for shape in shapes:
        if getattr(shape, "shape_type", None) == 6 and hasattr(shape, "shapes"):  # Gruppe
            found += _shape_texts(shape.shapes)
        if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
            found.append(shape.text_frame.text)
        if getattr(shape, "has_table", False):
            for row in shape.table.rows:
                cells = [" ".join(cell.text.split()) for cell in row.cells]
                if any(cells):
                    found.append(" | ".join(cells))
    return found


def _pptx(path: Path) -> list[Section]:
    pptx = _need("pptx", "python-pptx")
    presentation = pptx.Presentation(str(path))
    sections = []
    for number, slide in enumerate(presentation.slides, start=1):
        title = slide.shapes.title.text.strip() if slide.shapes.title is not None and slide.shapes.title.has_text_frame else ""
        texts = [text for text in _shape_texts(slide.shapes) if text.strip() != title]
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                texts.append(f"Notizen: {notes}")
        heading = f"Folie {number}" + (f": {' '.join(title.split())}" if title else "")
        sections.append(Section(heading, "\n".join(texts)))
    return sections


def _pdf(path: Path) -> list[Section]:
    pypdf = _need("pypdf", "pypdf")
    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            raise Unreadable("das PDF ist verschlüsselt")
        return [
            Section(f"Seite {number}", page.extract_text() or "") for number, page in enumerate(reader.pages, start=1)
        ]
    except Unreadable:
        raise
    except Exception as exc:  # pypdf wirft je nach Schaden sehr verschiedene Fehler
        raise Unreadable(f"das PDF ist nicht lesbar ({type(exc).__name__})") from exc


def _xlsx(path: Path) -> list[Section]:
    openpyxl = _need("openpyxl", "openpyxl")
    workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        sections = []
        for sheet in workbook.worksheets:
            lines: list[str] = []
            used = 0
            for number, row in enumerate(sheet.iter_rows(values_only=True)):
                if number >= MAX_ROWS or used >= MAX_SHEET_CHARS:
                    lines.append("…")
                    break
                cells = ["" if value is None else str(value)[:MAX_CELL_CHARS] for value in row]
                while cells and not cells[-1].strip():
                    cells.pop()
                if any(cell.strip() for cell in cells):
                    line = " | ".join(" ".join(cell.split()) for cell in cells)
                    lines.append(line)
                    used += len(line)
            sections.append(Section(f"Blatt {sheet.title}", "\n".join(lines)))
        return sections
    finally:
        workbook.close()


def _markdown(path: Path) -> list[Section]:
    intro, sections = stories.outline(path.read_text(encoding="utf-8", errors="replace"))
    return [Section("", "\n".join(intro))] + [Section(s.heading, "\n".join(s.lines)) for s in sections]


def _text(path: Path) -> list[Section]:
    return [Section("", path.read_text(encoding="utf-8", errors="replace"))]


READERS = {
    ".docx": _docx,
    ".pptx": _pptx,
    ".pdf": _pdf,
    ".xlsx": _xlsx,
    ".xlsm": _xlsx,
    ".md": _markdown,
    ".txt": _text,
}


def read_sections(path: Path) -> list[Section]:
    """Die Abschnitte der Datei mit Text; leere fallen weg. Wirft ``Unreadable``."""
    reader = READERS.get(path.suffix.lower())
    if reader is None:
        raise Unreadable(f"Dateityp {path.suffix or '(ohne Endung)'} wird nicht gelesen")
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise Unreadable(f"größer als {MAX_FILE_BYTES // (1024 * 1024)} MB")
        sections = reader(path)
    except Unreadable:
        raise
    except Exception as exc:  # beschaedigte oder ungewoehnliche Dateien: ueberspringen, nicht abstuerzen
        raise Unreadable(f"nicht lesbar ({type(exc).__name__}: {str(exc)[:80]})") from exc
    cleaned = [Section(" ".join(section.heading.split()), _clean(section.text)) for section in sections]
    return [section for section in cleaned if section.text][:MAX_SECTIONS]


@dataclass(frozen=True)
class Found:
    """Was ein Ordner hergibt: lesbare Dateien und, gezaehlt, was uebersprungen wird."""

    files: tuple[Path, ...]
    online_only: int = 0
    other_types: int = 0


def list_files(folder: Path, exclude: tuple[str, ...] = ()) -> Found:
    """Die lesbaren Dateien des Ordners samt Unterordnern, nach Pfad sortiert.

    Uebersprungen werden versteckte Dateien, Sperrdateien von Office ("~$..."),
    was auf ein Muster aus ``exclude`` passt (auf den Pfad relativ zum Ordner),
    andere Dateitypen und Dateien, die nur online vorliegen.
    """
    files: list[Path] = []
    online = other = 0
    folder = folder.expanduser()
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(name for name in dirs if not name.startswith("."))
        for name in sorted(names):
            path = Path(root) / name
            relative = str(path.relative_to(folder))
            if name.startswith((".", "~$")) or any(fnmatch.fnmatch(relative, pattern) for pattern in exclude):
                continue
            if path.suffix.lower() not in SUFFIXES:
                other += 1
            elif is_online_only(path):
                online += 1
            else:
                files.append(path)
    return Found(tuple(files), online, other)

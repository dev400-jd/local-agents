"""Tests fuer das Lesen von Office- und PDF-Dateien und ihre Aufnahme in die Suche."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ["LOCAL_AGENTS_ASANA_DB"] = "/gibt/es/nicht/asana.db"  # nie den echten Spiegel lesen

from local_agents import documents, search  # noqa: E402
from local_agents.documents import Section, Unreadable  # noqa: E402
from local_agents.profile import Context, Profile  # noqa: E402
from local_agents.search import DOCUMENT, Index  # noqa: E402
from local_agents.store import Store  # noqa: E402

try:
    import docx
    import openpyxl
    import pptx
    import pypdf  # noqa: F401

    LIBRARIES = True
except ImportError:
    LIBRARIES = False


def minimal_pdf(text: str) -> bytes:
    """Ein einseitiges PDF mit einer Textzeile, von Hand gebaut."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return out


def write_samples(folder: Path) -> None:
    document = docx.Document()
    document.add_paragraph("Stand November, erfundenes Beispiel.")
    document.add_heading("Zielbild", level=1)
    document.add_paragraph("Die Plattform liefert tägliche Auswertungen.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Phase", "Ergebnis"
    table.cell(1, 0).text, table.cell(1, 1).text = "1", "Rohdaten geladen"
    document.add_heading("Risiken", level=1)
    document.add_paragraph("Die Testumgebung ist knapp bemessen.")
    document.save(str(folder / "Konzept Plattform.docx"))

    deck = pptx.Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Überblick"
    slide.placeholders[1].text = "Drei Lieferanten sind angebunden."
    slide.notes_slide.notes_text_frame.text = "Zahlen noch bestätigen."
    slide = deck.slides.add_slide(deck.slide_layouts[5])
    slide.shapes.title.text = "Zeitplan"
    deck.save(str(folder / "Statusbericht.pptx"))

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Kosten"
    sheet.append(["Posten", "Betrag"])
    sheet.append(["Lizenzen", 1200])
    sheet.append([None, None])
    workbook.create_sheet("Leer")
    (folder / "Unterlagen").mkdir()
    workbook.save(str(folder / "Unterlagen" / "Budget.xlsx"))

    (folder / "Unterlagen" / "Vertrag.pdf").write_bytes(minimal_pdf("Laufzeit zwei Jahre"))
    (folder / "Notiz.txt").write_text("Rückruf am Montag.", encoding="utf-8")
    (folder / "bild.png").write_bytes(b"\x89PNG")
    (folder / "~$Konzept Plattform.docx").write_bytes(b"gesperrt")
    (folder / ".verborgen.txt").write_text("nicht lesen", encoding="utf-8")
    (folder / "Archiv").mkdir()
    (folder / "Archiv" / "alt.txt").write_text("veraltet", encoding="utf-8")
    (folder / "kaputt.docx").write_bytes(b"kein Word")


@unittest.skipUnless(LIBRARIES, "pypdf, python-docx, python-pptx und openpyxl sind nicht installiert")
class DocumentCase(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name) / "Kunde ACME Projektordner"
        self.folder.mkdir()
        write_samples(self.folder)
        self.profile = Profile(
            contexts=(Context("kunde-acme", "kunde", "Kunde ACME", keywords=("acme",)),),
            document_folders=(str(self.folder),),
            document_exclude=("Archiv/*",),
        )


class TestReading(DocumentCase):
    def test_word_by_heading_with_tables(self) -> None:
        self.assertEqual(
            documents.read_sections(self.folder / "Konzept Plattform.docx"),
            [
                Section("", "Stand November, erfundenes Beispiel."),
                Section("Zielbild", "Die Plattform liefert tägliche Auswertungen.\nPhase | Ergebnis\n1 | Rohdaten geladen"),
                Section("Risiken", "Die Testumgebung ist knapp bemessen."),
            ],
        )

    def test_powerpoint_by_slide_with_notes(self) -> None:
        self.assertEqual(
            documents.read_sections(self.folder / "Statusbericht.pptx"),
            [Section("Folie 1: Überblick", "Drei Lieferanten sind angebunden.\nNotizen: Zahlen noch bestätigen.")],
        )  # die zweite Folie hat nur einen Titel und faellt weg

    def test_excel_by_sheet_and_pdf_by_page(self) -> None:
        self.assertEqual(
            documents.read_sections(self.folder / "Unterlagen" / "Budget.xlsx"),
            [Section("Blatt Kosten", "Posten | Betrag\nLizenzen | 1200")],
        )
        self.assertEqual(
            documents.read_sections(self.folder / "Unterlagen" / "Vertrag.pdf"), [Section("Seite 1", "Laufzeit zwei Jahre")]
        )
        self.assertEqual(documents.read_sections(self.folder / "Notiz.txt"), [Section("", "Rückruf am Montag.")])

    def test_unreadable_files_say_why(self) -> None:
        with self.assertRaises(Unreadable) as broken:
            documents.read_sections(self.folder / "kaputt.docx")
        self.assertIn("nicht lesbar", str(broken.exception))
        with self.assertRaises(Unreadable) as image:
            documents.read_sections(self.folder / "bild.png")
        self.assertEqual(str(image.exception), "Dateityp .png wird nicht gelesen")

    def test_listing_skips_hidden_locked_excluded_and_other_types(self) -> None:
        listed = documents.list_files(self.folder, ("Archiv/*",))
        self.assertEqual(
            [str(path.relative_to(self.folder)) for path in listed.files],
            ["Konzept Plattform.docx", "Notiz.txt", "Statusbericht.pptx", "kaputt.docx", "Unterlagen/Budget.xlsx", "Unterlagen/Vertrag.pdf"],
        )
        self.assertEqual((listed.online_only, listed.other_types), (0, 1))
        original = documents.is_online_only
        documents.is_online_only = lambda path: path.suffix == ".pptx"
        self.addCleanup(setattr, documents, "is_online_only", original)
        self.assertEqual(documents.list_files(self.folder).online_only, 1)


class TestInTheSearch(DocumentCase):
    def test_each_section_becomes_a_document_passage(self) -> None:
        notes: list[str] = []
        found = search.folder_passages(self.profile, None, notes)
        self.assertEqual({passage.kind for passage in found}, {DOCUMENT})
        by_title = {passage.title: passage for passage in found}
        self.assertEqual(
            sorted(by_title),
            ["Budget: Blatt Kosten", "Konzept Plattform", "Konzept Plattform: Risiken", "Konzept Plattform: Zielbild", "Notiz", "Statusbericht: Folie 1: Überblick", "Vertrag: Seite 1"],
        )
        risk = by_title["Konzept Plattform: Risiken"]
        self.assertEqual(
            (risk.feature, risk.source, risk.context, risk.position),
            ("Konzept Plattform", "Kunde ACME Projektordner/Konzept Plattform.docx", "Kunde ACME", 3),
        )
        self.assertEqual(
            notes,
            [
                "Dokumentordner Kunde ACME Projektordner: 5 Dateien gelesen, 1 mit anderem Dateityp.",
                f"  nicht lesbar: kaputt.docx: {notes[1].split('kaputt.docx: ')[1]}",
            ],
        )

    def test_search_documents_and_description_material(self) -> None:
        store = Store(":memory:")
        self.addCleanup(store.close)
        index = Index(":memory:")
        self.addCleanup(index.close)
        search.refresh(index, self.profile, store)
        self.assertEqual(index.documents(), ["Budget", "Konzept Plattform", "Notiz", "Statusbericht", "Vertrag"])
        self.assertEqual(index.search("Testumgebung knapp")[0].passage.title, "Konzept Plattform: Risiken")
        self.assertEqual(index.search("Lizenzen")[0].passage.title, "Budget: Blatt Kosten")
        self.assertEqual([section.title for section in index.document("Konzept Plattform")], ["Konzept Plattform", "Konzept Plattform: Zielbild", "Konzept Plattform: Risiken"])

    def test_unchanged_files_are_not_read_again_and_removed_ones_are_forgotten(self) -> None:
        store = Store(":memory:")
        self.addCleanup(store.close)
        index = Index(":memory:")
        self.addCleanup(index.close)
        search.refresh(index, self.profile, store)
        original = documents.read_sections
        calls: list[str] = []

        def counting(path: Path):
            calls.append(path.name)
            return original(path)

        documents.read_sections = counting
        self.addCleanup(setattr, documents, "read_sections", original)
        self.assertFalse(search.refresh(index, self.profile, store))
        self.assertEqual(calls, ["kaputt.docx"])  # nur die unlesbare wird erneut versucht
        (self.folder / "Notiz.txt").write_text("Rückruf am Dienstag, nicht Montag.", encoding="utf-8")
        (self.folder / "Unterlagen" / "Vertrag.pdf").unlink()
        calls.clear()
        changes = search.refresh(index, self.profile, store)
        self.assertEqual((changes.changed, changes.removed, sorted(calls)), (1, 1, ["Notiz.txt", "kaputt.docx"]))
        self.assertEqual(index.search("Dienstag")[0].passage.title, "Notiz")
        self.assertEqual(index.db.execute("SELECT count(*) FROM extracted").fetchone()[0], 4)

    def test_missing_folder_and_no_folders(self) -> None:
        notes: list[str] = []
        self.assertEqual(search.folder_passages(Profile(document_folders=("/gibt/es/nicht/Ordner",)), None, notes), [])
        self.assertEqual(notes, ["Dokumentordner nicht gefunden: Ordner"])
        self.assertEqual(search.folder_passages(Profile(), None, notes), [])


if __name__ == "__main__":
    unittest.main()

"""Tests fuer Anhaenge: Dateien zur Aufgabe finden, SharePoint-Link bilden, nach Auswahl anhaengen, zuruecknehmen."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ["LOCAL_AGENTS_ASANA_DB"] = "/gibt/es/nicht/asana.db"  # nie den echten Spiegel lesen

from pydantic_ai import models  # noqa: E402

from local_agents import asana_actions as aa  # noqa: E402
from local_agents import asana_attach as at  # noqa: E402
from local_agents import search  # noqa: E402
from local_agents.asana_actions import Journal, Refused, Request  # noqa: E402
from local_agents.profile import Profile  # noqa: E402
from local_agents.search import Index, Passage  # noqa: E402
from tests.test_asana_actions import NOW, PROJECT, FakeAsana, wishing  # noqa: E402

models.ALLOW_MODEL_REQUESTS = False

SITE = "https://firma.sharepoint.com/Bereich/Kunde"
SCOPE = at.Scope(f"{SITE}/Shared Documents", "Projekte/2026 Aufträge/Ablage")


class AttachingAsana(FakeAsana):
    """Das nachgebaute Asana, dazu Anhaenge."""

    def __init__(self) -> None:
        super().__init__()
        self.attachments: dict[str, dict] = {}
        for task in self.tasks.values():
            task.setdefault("notes", "")

    def view(self, task: dict) -> dict:
        return {**super().view(task), "notes": task.get("notes", "")}

    def read(self, path: str, params: dict) -> dict:
        parts = path.strip("/").split("/")
        if parts[0] == "tasks" and parts[-1] == "attachments":
            found = [a for a in self.attachments.values() if a["parent"] == parts[1]]
            return {"data": [{"gid": a["gid"], "name": a["name"], "view_url": a["url"]} for a in found]}
        return super().read(path, params)

    def write(self, method: str, path: str, data: dict) -> dict:
        if method == "POST" and path == "/attachments":
            self.written.append((method, path, data))
            gid = f"a{self.next}"
            self.next += 1
            self.attachments[gid] = {"gid": gid, **data}
            return {"data": {"gid": gid}}
        if method == "DELETE" and path.startswith("/attachments/"):
            self.written.append((method, path, data))
            del self.attachments[path.rsplit("/", 1)[-1]]
            return {"data": {}}
        return super().write(method, path, data)


def section(folder: str, relative: str, number: int, heading: str, text: str) -> Passage:
    title = Path(relative).stem
    return Passage(
        f"dokument:{folder}/{relative}#{number}", search.DOCUMENT, f"{title}: {heading}", text,
        source=f"{folder}/{relative}", feature=title, position=number,
    )


class AttachCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())) / "Ablage"
        for relative in ("Schulungen/Folien Lieferketten.pptx", "Preisliste.xlsx", "Konzept Lager.docx"):
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.root / relative).write_bytes(b"x" * 2048)
        self.profile = Profile(asana_write_projects=(PROJECT,), document_folders=(str(self.root),))
        self.index = Index(":memory:")
        self.addCleanup(self.index.close)
        self.index.update(
            [
                section("Ablage", "Schulungen/Folien Lieferketten.pptx", 1, "Folie 1: Kanban", "Lieferketten steuern mit Kanban und Zugsteuerung."),
                section("Ablage", "Schulungen/Folien Lieferketten.pptx", 2, "Folie 2: Rollen", "Disponent und Einkäufer."),
                section("Ablage", "Preisliste.xlsx", 1, "Blatt Preise", "Schraube | 0,10 | Mutter | 0,05"),
                section("Ablage", "Konzept Lager.docx", 1, "Ziel", "Das Lager bekommt eine Zugsteuerung für Lieferketten."),
                # Liegt nicht in einem Dokumentordner: kein Link moeglich, wird nie angeboten.
                Passage("dokument:recap#1", search.DOCUMENT, "Recap: Lieferketten", "Lieferketten Kanban", source="recap.md", feature="Recap", position=1),
            ]
        )

    def find(self, task: str, notes: str = "", preferred: str = "") -> list[at.Candidate]:
        return at.find(self.index, self.profile, task, notes, None, preferred, scopes=[SCOPE])


class TestLinks(unittest.TestCase):
    def settings(self, added: str, namespace: str = "") -> Path:
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "0a1b2c3d-1111-2222-3333-444455556666.ini").write_text(
            'libraryScope = 0 abc 5 "x" "y" 2 "https://firma-my.sharepoint.com/personal/ich" "z"\n' + added + "\n", encoding="utf-8"
        )
        if namespace:
            (folder / "ClientPolicy_abc_def.ini").write_text(f"DavUrlNamespace = {namespace}\n", encoding="utf-8")
        return folder

    def test_scopes_come_from_the_sync_settings(self) -> None:
        added = f'AddedScope = 1 abc 4 "{SITE}" "name" i1 i2 i3 i4 "Projekte/2026 Aufträge/Ablage"'
        self.assertEqual(at.read_scopes(self.settings(added)), [SCOPE])
        # Heisst die Bibliothek anders, steht ihr Name in der ClientPolicy der Site.
        named = at.read_scopes(self.settings(added, f"{SITE}/Dokumente/"))
        self.assertEqual(named, [at.Scope(f"{SITE}/Dokumente", "Projekte/2026 Aufträge/Ablage")])

    def test_unreadable_settings_give_no_scopes(self) -> None:
        self.assertEqual(at.read_scopes(Path("/gibt/es/nicht")), [])
        self.assertEqual(at.read_scopes(self.settings('AddedScope = 1 abc "unvollständig')), [])
        self.assertEqual(at.read_scopes(self.settings('AddedScope = 1 abc 4 "http://unsicher" a b c d e "x"')), [])

    def test_link_is_quoted_and_opens_in_the_browser(self) -> None:
        self.assertEqual(
            at.link(SCOPE, "Schulungen/Folien Lieferketten.pptx"),
            "https://firma.sharepoint.com/Bereich/Kunde/Shared%20Documents/Projekte/2026%20Auftr%C3%A4ge/Ablage/"
            "Schulungen/Folien%20Lieferketten.pptx?web=1",
        )

    def test_the_local_folder_may_carry_the_site_name_in_front(self) -> None:
        other = at.Scope(f"{SITE}/Shared Documents", "Sonstiges/Archiv")
        self.assertEqual(at.scope_of(Path("/x/Ablage"), [other, SCOPE]), SCOPE)
        self.assertEqual(at.scope_of(Path("/x/KUNDE - Ablage"), [other, SCOPE]), SCOPE)
        self.assertIsNone(at.scope_of(Path("/x/Unbekannt"), [other, SCOPE]))
        self.assertEqual(at.scope_of(Path("/x/Umbenannt"), [SCOPE]), SCOPE)

    def test_only_sharepoint_links_are_allowed(self) -> None:
        self.assertTrue(at.is_allowed_url("https://firma.sharepoint.com/x/y.pptx?web=1"))
        for url in ("http://firma.sharepoint.com/x", "https://sharepoint.com.boese.example/x", "https://example.org/x", "file:///etc/passwd"):
            self.assertFalse(at.is_allowed_url(url), url)


class TestFind(AttachCase):
    def test_files_matching_name_and_description_come_first(self) -> None:
        found = self.find("Schulung Lieferketten vorbereiten", "Kanban und Zugsteuerung erklären.")
        self.assertEqual(found[0].name, "Folien Lieferketten.pptx")
        self.assertEqual(found[0].source, "Ablage/Schulungen/Folien Lieferketten.pptx")
        self.assertTrue(found[0].url.endswith("/Ablage/Schulungen/Folien%20Lieferketten.pptx?web=1"))
        self.assertEqual(found[0].size, 2048)
        self.assertEqual(found[0].reason, "Folie 1: Kanban")
        self.assertNotIn("Preisliste.xlsx", [candidate.name for candidate in found])
        # Jede Datei einmal, auch wenn mehrere Abschnitte passen; nichts ausserhalb der Ordner.
        self.assertEqual(len({candidate.source for candidate in found}), len(found))
        self.assertTrue(all(candidate.source.startswith("Ablage/") for candidate in found))

    def test_the_document_the_description_came_from_is_first(self) -> None:
        found = self.find("Schulung Lieferketten vorbereiten", "Kanban", preferred="Konzept Lager")
        self.assertEqual(found[0].name, "Konzept Lager.docx")
        self.assertEqual(found[0].reason, "daraus stammt die Beschreibung")

    def test_nothing_to_offer_is_said_plainly(self) -> None:
        with self.assertRaisesRegex(at.NoAttachments, "keine passende Datei"):
            self.find("Betriebsausflug planen")
        with self.assertRaisesRegex(at.NoAttachments, "keine SharePoint-Adresse"):
            at.find(self.index, self.profile, "Lieferketten", "", scopes=[])
        with self.assertRaisesRegex(at.NoAttachments, r"\[documents\]"):
            at.find(self.index, Profile(), "Lieferketten", "", scopes=[SCOPE])

    def test_a_file_that_is_gone_is_not_offered(self) -> None:
        (self.root / "Konzept Lager.docx").unlink()
        self.assertNotIn("Konzept Lager.docx", [c.name for c in self.find("Lieferketten Zugsteuerung")])

    def test_choice(self) -> None:
        self.assertEqual(at.chosen("1,3", 3), [0, 2])
        self.assertEqual(at.chosen("2 und 1", 3), [0, 1])
        self.assertEqual(at.chosen("alle", 2), [0, 1])
        self.assertEqual(at.chosen("n", 2), [])
        self.assertEqual(at.chosen("", 2), [])
        self.assertEqual(at.chosen("j", 1), [0])
        self.assertIsNone(at.chosen("j", 2))  # bei mehreren ist "ja" keine Auswahl
        self.assertIsNone(at.chosen("4", 3))
        self.assertIsNone(at.chosen("die erste", 3))


class TestAttachAction(AttachCase):
    def setUp(self) -> None:
        super().setUp()
        self.asana = AttachingAsana()
        self.asana.tasks["t2"].update(name="Schulung Lieferketten", notes="Kanban und Zugsteuerung erklären.")
        self.journal = Journal(":memory:")
        self.addCleanup(self.journal.close)
        self.printed: list[str] = []

    def act(self, answer: str | None, aufgabe: str = "Schulung Lieferketten") -> str:
        confirm = None if answer is None else (lambda prompt: answer)
        finder = lambda task, notes, preferred: self.find(task, notes, preferred)  # noqa: E731
        return aa.act(
            "Häng die passenden Dateien an", self.profile, wishing({"aktion": "anhaenge", "aufgabe": aufgabe}),
            self.asana.read, self.asana.write, self.journal, NOW, confirm, self.printed.append, finder=finder,
        )

    def test_chosen_files_are_attached_as_links_and_can_be_taken_back(self) -> None:
        self.assertEqual(self.act("1"), "ausgefuehrt")
        text = "\n".join(self.printed)
        self.assertIn("1. Ablage/Schulungen/Folien Lieferketten.pptx (2 KB) · passt auf: Folie 1: Kanban", text)
        self.assertIn("die Datei bleibt in SharePoint", text)
        self.assertEqual(len(self.asana.attachments), 1)
        attached = next(iter(self.asana.attachments.values()))
        self.assertEqual((attached["parent"], attached["resource_subtype"], attached["name"]), ("t2", "external", "Folien Lieferketten.pptx"))
        self.assertTrue(attached["url"].startswith("https://firma.sharepoint.com/"))
        self.assertEqual([entry[:2] for entry in self.asana.written], [("POST", "/attachments")])
        self.assertIn("Anhang: Folien Lieferketten.pptx", aa.journal_lines(self.journal.rows())[0])

        self.printed.clear()
        result = aa.undo_last(self.profile, self.asana.read, self.asana.write, self.journal, NOW, lambda prompt: "j", self.printed.append)
        self.assertEqual(result, "ausgefuehrt")
        self.assertIn("die Dateien in SharePoint bleiben", "\n".join(self.printed))
        self.assertEqual(self.asana.attachments, {})

    def test_nothing_is_attached_without_a_choice(self) -> None:
        for answer, result in (("n", "abgebrochen"), ("vielleicht", "abgebrochen"), (None, "nicht_freigegeben")):
            self.assertEqual(self.act(answer), result)
        self.assertEqual(self.asana.written, [])
        self.assertEqual(self.asana.attachments, {})

    def test_files_already_attached_are_not_offered_again(self) -> None:
        self.assertEqual(self.act("alle"), "ausgefuehrt")
        count = len(self.asana.attachments)
        self.printed.clear()
        self.assertEqual(self.act("alle"), "rueckfrage")
        self.assertIn("hängen schon an", self.printed[0])
        self.assertEqual(len(self.asana.attachments), count)

    def test_the_source_of_the_description_is_known_from_the_journal(self) -> None:
        state = aa.load_state(PROJECT, self.asana.read)
        plan = aa.Plan(aa.DESCRIBE, "Schulung Lieferketten", (("Beschreibung", "leer", "120 Zeichen aus „Konzept Lager“"),), (), (), "t2")
        self.journal.add(NOW, state, plan, aa.Outcome(True, [], "t2"))
        self.assertEqual(self.journal.described_from("t2"), "Konzept Lager")
        self.assertEqual(self.journal.described_from("t1"), "")
        self.act(None)
        self.assertIn("1. Ablage/Konzept Lager.docx (2 KB) · passt auf: daraus stammt die Beschreibung", "\n".join(self.printed))

    def test_without_the_index_the_action_asks_back(self) -> None:
        result = aa.act("Häng an", self.profile, wishing({"aktion": "anhaenge", "aufgabe": "Schulung Lieferketten"}), self.asana.read, self.asana.write, self.journal, NOW, lambda prompt: "j", self.printed.append)
        self.assertEqual(result, "rueckfrage")
        self.assertEqual(self.asana.written, [])

    def test_guard_allows_only_links_to_sharepoint_on_tasks_of_the_project(self) -> None:
        state = aa.load_state(PROJECT, self.asana.read)
        good = {"parent": "t2", "resource_subtype": "external", "name": "a.pptx", "url": "https://firma.sharepoint.com/a.pptx?web=1"}
        aa.check_allowed(Request("POST", "/attachments", good), state, set())
        for change in (
            {"parent": "k1"},  # Aufgabe eines anderen Projekts
            {"resource_subtype": "asana"},  # waere ein Hochladen
            {"url": "https://example.org/a.pptx"},
            {"url": "http://firma.sharepoint.com/a.pptx"},
            {"name": ""},
            {"file": "inhalt"},
        ):
            with self.assertRaises(Refused, msg=str(change)):
                aa.check_allowed(Request("POST", "/attachments", {**good, **change}), state, set())
        with self.assertRaises(Refused):  # fremde Anhaenge loescht der Agent nicht
            aa.check_allowed(Request("DELETE", "/attachments/a1", {}), state, set())
        aa.check_allowed(Request("DELETE", "/attachments/a1", {}), state, {"a1"})


class TestMeasurementHarness(unittest.TestCase):
    """Die Messung baut das Projekt nach; der Nachbau muss die Felder der Aufgaben richtig fuellen."""

    def run_case(self, wish: dict, **inputs):
        from datetime import date

        from local_agents import eval_suites

        tasks = [eval_suites.AktionAufgabe(name="Folien bauen"), eval_suites.AktionAufgabe(name="Angebot prüfen", abschnitt="In Arbeit")]
        given = eval_suites.AktionInputs(satz="Satz", heute=date(2026, 11, 20), aufgaben=tasks, **inputs)
        return eval_suites.aktion_task(wishing(wish))(given)

    def test_the_section_of_a_task_is_its_section_not_its_description(self) -> None:
        output = self.run_case({"aktion": "abschnitt", "aufgabe": "Folien bauen", "abschnitt": "In Arbeit"})
        self.assertIn("  Abschnitt: Offen → In Arbeit", output.plan)

    def test_attachments_are_measured_by_action_and_task(self) -> None:
        output = self.run_case({"aktion": "anhaenge", "aufgabe": "Angebot"})
        self.assertEqual((output.aktion, output.aufgabe, output.rueckfrage), ("anhaenge", "Angebot prüfen", ""))


if __name__ == "__main__":
    unittest.main()

"""Tests fuer den Asana-Spiegel gegen ein nachgebautes Asana, ohne Netz."""

import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ["LOCAL_AGENTS_ASANA_DB"] = "/gibt/es/nicht/asana.db"  # nie den echten Spiegel lesen

from local_agents import asana_mirror, search  # noqa: E402
from local_agents.asana_mirror import Mirror, sync  # noqa: E402
from local_agents.asana_source import AsanaAccessError  # noqa: E402
from local_agents.profile import Context, Profile  # noqa: E402
from local_agents.search import ASANA, Index  # noqa: E402
from local_agents.store import Store  # noqa: E402

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
PROFILE = Profile(
    contexts=(Context("intern", "intern", "Intern"), Context("kunde-acme", "kunde", "Kunde ACME", keywords=("datenplattform",)))
)


def task(gid: str, name: str, modified: str = "2026-11-19T08:00:00.000Z", **extra) -> dict:
    return {
        "gid": gid,
        "name": name,
        "notes": "",
        "completed": False,
        "due_on": None,
        "assignee": {"name": "Alex Beispiel"},
        "modified_at": modified,
        "created_at": "2026-11-01T08:00:00.000Z",
        "permalink_url": f"https://app.asana.com/0/0/{gid}",
        "memberships": [{"project": {"name": "Datenplattform"}, "section": {"name": "In Arbeit"}}],
        "custom_fields": [{"name": "Priorität", "display_value": "Hoch"}, {"name": "Leer", "display_value": None}],
        "num_subtasks": 0,
        **extra,
    }


def comment(gid: str, created: str, text: str, author: str = "Erika Muster", subtype: str = "comment_added") -> dict:
    return {"gid": gid, "created_at": created, "created_by": {"name": author}, "resource_subtype": subtype, "text": text}


class FakeAsana:
    """Antwortet wie die Asana-API auf die Pfade, die der Spiegel abfragt; kennt nur GET."""

    def __init__(self, mine: list[dict], followed: list[dict] | None = None, stories: dict | None = None, page: int = 100) -> None:
        self.mine, self.followed, self.stories, self.page = mine, followed, stories or {}, page
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, path: str, params: dict[str, str]) -> dict:
        self.calls.append((path, params))
        if path == "/users/me":
            return {"data": {"gid": "111", "workspaces": [{"gid": "ws"}]}}
        if path == "/tasks":
            since = params.get("modified_since", "")
            tasks = [t for t in self.mine if t["modified_at"] >= since.replace("Z", ".000Z")] if since else self.mine
            start = int(params.get("offset", "0"))
            more = {"offset": str(start + self.page)} if start + self.page < len(tasks) else None
            return {"data": tasks[start : start + self.page], "next_page": more}
        if path == "/workspaces/ws/tasks/search":
            if self.followed is None:
                raise AsanaAccessError("Die Asana-Suche gibt es nur in bezahlten Plaenen. (HTTP 402 bei /search)")
            return {"data": self.followed}
        if path.endswith("/stories"):
            return {"data": self.stories.get(path.split("/")[2], [])}
        raise AssertionError(path)

    def paths(self, ending: str) -> list[str]:
        return [path for path, _ in self.calls if path.endswith(ending)]


class MirrorCase(unittest.TestCase):
    def setUp(self) -> None:
        self.mirror = Mirror(":memory:")
        self.addCleanup(self.mirror.close)


class TestSync(MirrorCase):
    def test_first_sync_reads_tasks_fields_and_comments(self) -> None:
        asana = FakeAsana(
            [task("1", "Angebot prüfen", due_on="2026-11-24", notes="Bis Dienstag."), task("2", "Folien bauen", completed=True)],
            followed=[task("3", "Review vorbereiten", assignee={"name": "Erika Muster"})],
            stories={"1": [comment("c1", "2026-11-18T09:00:00.000Z", "Zahlen fehlen noch."), comment("s", "2026-11-18T10:00:00.000Z", "hat verschoben", subtype="due_date_changed")]},
        )
        result = sync(self.mirror, asana, NOW)
        self.assertEqual((result.full, result.new, result.changed, result.removed, result.notes), (True, 3, 0, 0, []))
        first, review, done = self.mirror.tasks()
        self.assertEqual(
            (first.name, first.due, first.assignee, first.mine, first.followed, first.projects, first.fields, first.notes),
            ("Angebot prüfen", "2026-11-24", "Alex Beispiel", True, False, (("Datenplattform", "In Arbeit"),), (("Priorität", "Hoch"),), "Bis Dienstag."),
        )
        self.assertEqual([(c.author, c.text) for c in first.comments], [("Erika Muster", "Zahlen fehlen noch.")])
        self.assertEqual((review.name, review.mine, review.followed, done.completed), ("Review vorbereiten", False, True, True))
        self.assertEqual(
            asana_mirror.status_line(self.mirror, NOW + timedelta(minutes=30)),
            "Asana-Spiegel: 3 Aufgaben (2 offen; 2 dir zugewiesen, 1 gefolgt) aus 1 Projekten, 1 Kommentare; vor 30 Min abgeglichen.",
        )
        own = next(params for path, params in asana.calls if path == "/tasks")
        self.assertEqual((own["assignee"], own["workspace"], own["completed_since"]), ("111", "ws", "2026-08-22T12:00:00Z"))
        self.assertNotIn("modified_since", own)

    def test_next_sync_asks_only_for_changes(self) -> None:
        tasks = [task("1", "Angebot prüfen"), task("2", "Folien bauen")]
        asana = FakeAsana(tasks, followed=[])
        sync(self.mirror, asana, NOW)
        tasks[0] = task("1", "Angebot prüfen und senden", modified="2026-11-20T13:00:00.000Z")
        asana.calls.clear()
        result = sync(self.mirror, asana, NOW + timedelta(hours=2))
        self.assertEqual((result.full, result.new, result.changed, result.unchanged), (False, 0, 1, 0))
        self.assertEqual(asana.paths("/stories"), ["/tasks/1/stories"])
        own = next(params for path, params in asana.calls if path == "/tasks")
        self.assertEqual(own["modified_since"], "2026-11-20T11:55:00Z")  # letzter Abgleich minus Ueberlappung
        self.assertEqual(sorted(t.name for t in self.mirror.tasks()), ["Angebot prüfen und senden", "Folien bauen"])

    def test_full_sync_removes_what_is_gone_and_rereads_open_comments(self) -> None:
        asana = FakeAsana([task("1", "Bleibt"), task("2", "Verschwindet"), task("3", "Erledigt", completed=True)], followed=[])
        sync(self.mirror, asana, NOW)
        asana.mine = [asana.mine[0], asana.mine[2]]
        asana.stories = {"1": [comment("c9", "2026-11-20T15:00:00.000Z", "Neuer Kommentar ohne neues Änderungsdatum.")]}
        asana.calls.clear()
        result = sync(self.mirror, asana, NOW + timedelta(hours=5), full=True)
        self.assertEqual((result.full, result.new, result.changed, result.unchanged, result.removed), (True, 0, 0, 2, 1))
        self.assertEqual(asana.paths("/stories"), ["/tasks/1/stories"])  # nur die offene
        self.assertEqual([t.name for t in self.mirror.tasks()], ["Bleibt", "Erledigt"])
        self.assertEqual(len(self.mirror.tasks()[0].comments), 1)
        self.assertEqual(asana_mirror.result_line(result), "Voller Abgleich: 0 neu, 0 geändert, 2 unverändert, 1 entfernt.")

    def test_after_a_week_the_sync_is_full_by_itself(self) -> None:
        asana = FakeAsana([task("1", "Bleibt"), task("2", "Verschwindet")], followed=[])
        sync(self.mirror, asana, NOW)
        asana.mine = asana.mine[:1]
        self.assertFalse(sync(self.mirror, asana, NOW + timedelta(days=6)).full)
        later = sync(self.mirror, asana, NOW + timedelta(days=7, minutes=1))
        self.assertEqual((later.full, later.removed), (True, 1))

    def test_paging_and_comment_limit(self) -> None:
        many = [comment(f"c{n:03}", f"2026-11-{1 + n // 10:02}T0{n % 10}:00:00.000Z", f"Kommentar {n}") for n in range(40)]
        asana = FakeAsana([task(str(n), f"Aufgabe {n}") for n in range(5)], followed=[], stories={"0": many}, page=2)
        self.assertEqual(sync(self.mirror, asana, NOW).new, 5)
        self.assertEqual(len(asana.paths("/tasks")), 3)
        comments = next(t for t in self.mirror.tasks() if t.gid == "0").comments
        self.assertEqual((len(comments), comments[0].text, comments[-1].text), (30, "Kommentar 10", "Kommentar 39"))

    def test_without_search_the_followed_tasks_are_skipped_with_a_note(self) -> None:
        result = sync(self.mirror, FakeAsana([task("1", "Angebot prüfen")]), NOW)
        self.assertEqual((result.new, result.notes), (1, ["Gefolgte Aufgaben fehlen: Die Asana-Suche gibt es nur in bezahlten Plänen."]))

    def test_other_errors_stop_the_sync_and_keep_the_mirror(self) -> None:
        sync(self.mirror, FakeAsana([task("1", "Angebot prüfen")], followed=[]), NOW)

        def broken(path: str, params: dict) -> dict:
            raise AsanaAccessError("Asana lehnt das Token ab (abgelaufen oder falsch). (HTTP 401 bei /users/me)")

        with self.assertRaises(AsanaAccessError):
            sync(self.mirror, broken, NOW + timedelta(hours=1), full=True)
        self.assertEqual(len(self.mirror.tasks()), 1)
        self.assertEqual(self.mirror.last_sync(), NOW)

    def test_only_reading_requests_exist(self) -> None:
        # Der Transport kennt nur Pfad und Parameter einer GET-Abfrage; schreiben kann dieses Modul nicht.
        asana = FakeAsana([task("1", "Angebot prüfen")], followed=[])
        sync(self.mirror, asana, NOW)
        self.assertEqual({path.split("/")[1] for path, _ in asana.calls}, {"users", "tasks", "workspaces"})

    def test_age(self) -> None:
        self.assertEqual(asana_mirror.age_text(None, NOW), "noch nie abgeglichen")
        self.assertEqual(asana_mirror.age_text(NOW - timedelta(hours=5), NOW), "vor 5 Std abgeglichen")
        self.assertEqual(asana_mirror.age_text(NOW - timedelta(days=3), NOW), "vor 3 Tagen abgeglichen")


class TestSearchOverTheMirror(MirrorCase):
    def setUp(self) -> None:
        super().setUp()
        asana = FakeAsana(
            [
                task("1", "Angebot prüfen", due_on="2026-11-24", notes="Rabattstaffel mit dem Vertrieb klären."),
                task("2", "Folien bauen", completed=True, memberships=[]),
            ],
            followed=[],
            stories={"1": [comment("c1", "2026-11-18T09:00:00.000Z", "Zahlen aus dem Controlling fehlen noch.")]},
        )
        sync(self.mirror, asana, NOW)

    def test_each_task_becomes_a_passage_with_state_and_comments(self) -> None:
        first, second = search.asana_passages(self.mirror, PROFILE)
        self.assertEqual((first.ref, first.kind, first.title, first.context, first.source), ("asana:1", ASANA, "Angebot prüfen", "Kunde ACME", "Asana, Datenplattform"))
        self.assertEqual(
            first.text,
            "Stand: offen, fällig 24.11.2026\n"
            "Zuständig: Alex Beispiel\n"
            "Projekt: Datenplattform, Abschnitt: In Arbeit\n"
            "Priorität: Hoch\n"
            "\n"
            "Rabattstaffel mit dem Vertrieb klären.\n"
            "\n"
            "Kommentare:\n"
            "- 18.11.2026 Erika Muster: Zahlen aus dem Controlling fehlen noch.",
        )
        self.assertTrue(second.text.startswith("Stand: erledigt\n"))
        self.assertEqual(second.source, "Asana")

    def test_every_part_of_a_long_task_carries_its_state(self) -> None:
        long = [comment(f"c{n:02}", f"2026-11-{n + 1:02}T09:00:00.000Z", "Sehr langer Kommentar. " * 40) for n in range(8)]
        mirror = Mirror(":memory:")
        self.addCleanup(mirror.close)
        sync(mirror, FakeAsana([task("7", "Großes Thema", completed=True)], followed=[], stories={"7": long}), NOW)
        parts = search.asana_passages(mirror, PROFILE)
        self.assertGreater(len(parts), 2)
        for part in parts:
            self.assertTrue(part.text.startswith("Stand: erledigt\nZuständig: Alex Beispiel\n"), part.title)
            self.assertLessEqual(len(part.text), search.MAX_PASSAGE_CHARS + 2)
        self.assertEqual(parts[1].title, f"Großes Thema (Teil 2 von {len(parts)})")

    def test_search_finds_task_text_and_comments(self) -> None:
        store = Store(":memory:")
        self.addCleanup(store.close)
        index = Index(":memory:")
        self.addCleanup(index.close)
        index.update(search.collect(PROFILE, store, self.mirror))
        self.assertEqual(search.count_line(index.counts()), "Im Index: 2 Asana-Aufgaben.")
        self.assertEqual([hit.passage.ref for hit in index.search("Controlling Zahlen")], ["asana:1"])
        self.assertEqual([hit.passage.ref for hit in index.search("Rabattstaffel")], ["asana:1"])
        lines = search.hit_lines(index.search("Folien"))
        self.assertEqual(lines[:2], ["1. Folien bauen", "   Asana-Aufgabe · Asana"])

    def test_without_a_mirror_file_nothing_is_added(self) -> None:
        store = Store(":memory:")
        self.addCleanup(store.close)
        self.assertEqual(search.collect(PROFILE, store), [])


if __name__ == "__main__":
    unittest.main()

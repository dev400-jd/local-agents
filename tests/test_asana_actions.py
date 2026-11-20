"""Tests fuer das Steuern von Asana gegen ein nachgebautes Asana: Planung, Grenzen, Freigabe, Protokoll."""

import os
import unittest
from datetime import date, datetime, timezone

os.environ["LOCAL_AGENTS_ASANA_DB"] = "/gibt/es/nicht/asana.db"  # nie den echten Spiegel lesen

from pydantic_ai import models  # noqa: E402
from pydantic_ai.messages import ModelMessage, ModelResponse  # noqa: E402
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402

from local_agents import asana_actions as aa  # noqa: E402
from local_agents.asana_actions import Clarify, Journal, Refused, Request, Wish  # noqa: E402
from local_agents.asana_source import AsanaAccessError  # noqa: E402
from local_agents.profile import Profile  # noqa: E402
from local_agents.testing import structured_reply  # noqa: E402

models.ALLOW_MODEL_REQUESTS = False

TODAY = date(2026, 11, 20)  # ein Freitag
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
PROJECT = "Übungsprojekt"
PROFILE = Profile(asana_write_projects=(PROJECT,))


class FakeAsana:
    """Ein Asana mit zwei Projekten. Liest wie die API und fuehrt die Schreibaufrufe des Katalogs aus."""

    def __init__(self) -> None:
        self.projects = {"p1": PROJECT, "p2": "Kundenprojekt"}
        self.sections = {"s1": ("p1", "Offen"), "s2": ("p1", "In Arbeit"), "s9": ("p2", "Backlog")}
        self.tasks: dict[str, dict] = {}
        self.next = 100
        self.written: list[tuple[str, str, dict]] = []
        self.add("t1", "Angebot prüfen", "p1", "s1", due_on="2026-11-24")
        self.add("t2", "Folien bauen", "p1", "s1")
        self.add("t3", "Folien drucken", "p1", "s2")
        self.add("t4", "Vertrag ablegen", "p1", "s2", completed=True)
        self.add("k1", "Kundentermin vorbereiten", "p2", "s9")

    def add(self, gid: str, name: str, project: str, section: str, **extra) -> None:
        self.tasks[gid] = {"gid": gid, "name": name, "completed": False, "due_on": None, "due_at": None, "project": project, "section": section, "parent": None, **extra}

    def view(self, task: dict) -> dict:
        section = task["section"]
        memberships = [{"project": {"gid": task["project"]}, "section": {"gid": section, "name": self.sections[section][1]}}] if section else []
        return {**{key: task[key] for key in ("gid", "name", "completed", "due_on", "due_at")}, "memberships": memberships, "parent": {"gid": task["parent"]} if task["parent"] else None}

    def read(self, path: str, params: dict) -> dict:
        parts = path.strip("/").split("/")
        if path == "/users/me":
            return {"data": {"gid": "111", "workspaces": [{"gid": "ws"}]}}
        if parts[-1] == "typeahead":
            return {"data": [{"gid": gid, "name": name} for gid, name in self.projects.items() if params["query"].casefold() in name.casefold()]}
        if parts[0] == "projects" and parts[2] == "sections":
            return {"data": [{"gid": gid, "name": name} for gid, (project, name) in self.sections.items() if project == parts[1]]}
        if parts[0] == "projects" and parts[2] == "tasks":
            return {"data": [self.view(t) for t in self.tasks.values() if t["project"] == parts[1] and not t["parent"]]}
        if parts[0] == "tasks" and len(parts) == 2:
            return {"data": self.view(self.tasks[parts[1]])}
        if parts[0] == "sections" and len(parts) == 2:
            return {"data": {"gid": parts[1], "name": self.sections[parts[1]][1]}}
        raise AssertionError(path)

    def write(self, method: str, path: str, data: dict) -> dict:
        self.written.append((method, path, data))
        parts = path.strip("/").split("/")
        if method == "POST" and path == "/tasks":
            gid = f"n{self.next}"
            self.next += 1
            self.add(gid, data["name"], data["projects"][0], "", due_on=data.get("due_on"))
            return {"data": {"gid": gid}}
        if method == "PUT":
            self.tasks[parts[1]].update(data)
            return {"data": {"gid": parts[1]}}
        if method == "POST" and parts[2] == "addTask":
            self.tasks[data["task"]]["section"] = parts[1]
            return {"data": {}}
        if method == "POST" and parts[2] == "subtasks":
            gid = f"n{self.next}"
            self.next += 1
            self.add(gid, data["name"], self.tasks[parts[1]]["project"], "", parent=parts[1])
            return {"data": {"gid": gid}}
        if method == "POST" and parts[0] == "projects" and parts[2] == "sections":
            gid = f"s{self.next}"
            self.next += 1
            self.sections[gid] = (parts[1], data["name"])
            return {"data": {"gid": gid}}
        if method == "DELETE" and parts[0] == "sections":
            del self.sections[parts[1]]
            return {"data": {}}
        if method == "DELETE":
            del self.tasks[parts[1]]
            return {"data": {}}
        raise AssertionError((method, path))


def wishing(*wishes: dict, seen: list | None = None) -> FunctionModel:
    replies = list(wishes)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append(info.instructions)
        return structured_reply(info, replies.pop(0) if len(replies) > 1 else replies[0])

    return FunctionModel(respond)


class ActionCase(unittest.TestCase):
    def setUp(self) -> None:
        self.asana = FakeAsana()
        self.journal = Journal(":memory:")
        self.addCleanup(self.journal.close)
        self.printed: list[str] = []

    def state(self) -> aa.ProjectState:
        return aa.load_state(PROJECT, self.asana.read)

    def plan(self, **wish) -> aa.Plan:
        return aa.make_plan(Wish(**wish), self.state(), TODAY)

    def act(self, wish: dict, answer: str | None = "j", profile: Profile = PROFILE, sentence: str = "Satz") -> str:
        confirm = None if answer is None else (lambda prompt: answer)
        return aa.act(sentence, profile, wishing(wish), self.asana.read, self.asana.write, self.journal, NOW, confirm, self.printed.append)


class TestState(ActionCase):
    def test_state_holds_only_the_allowed_project(self) -> None:
        state = self.state()
        self.assertEqual((state.gid, state.name, state.sections), ("p1", PROJECT, (("s1", "Offen"), ("s2", "In Arbeit"))))
        self.assertEqual([task.name for task in state.tasks], ["Angebot prüfen", "Folien bauen", "Folien drucken", "Vertrag ablegen"])
        self.assertEqual((state.tasks[0].due, state.tasks[0].section), ("2026-11-24", "Offen"))

    def test_project_must_exist_exactly_once(self) -> None:
        with self.assertRaises(Clarify):
            aa.load_state("Gibt es nicht", self.asana.read)
        self.asana.projects["p3"] = PROJECT
        with self.assertRaises(Clarify) as double:
            aa.load_state(PROJECT, self.asana.read)
        self.assertIn("gibt es mehrfach. Ich schreibe nichts.", str(double.exception))


class TestPlan(ActionCase):
    def test_create_with_due_date_and_section(self) -> None:
        plan = self.plan(aktion="anlegen", titel="„Protokoll schreiben“", faellig="Dienstag", abschnitt="in arbeit")
        self.assertEqual(
            plan.lines(PROJECT),
            [
                f"Aufgabe anlegen im Projekt „{PROJECT}“: Protokoll schreiben",
                "  Titel: Protokoll schreiben",
                "  Zuständig: du",
                "  Fällig: Di 24.11.2026",
                "  Abschnitt: In Arbeit",
            ],
        )
        self.assertEqual(plan.requests[0], Request("POST", "/tasks", {"name": "Protokoll schreiben", "projects": ["p1"], "assignee": "me", "due_on": "2026-11-24"}))
        self.assertEqual(plan.requests[1], Request("POST", "/sections/s2/addTask", {"task": "<neu>"}))

    def test_code_repairs_what_the_model_tends_to_get_wrong(self) -> None:
        # Zeitangabe im Titel, Faelligkeit leer oder faelschlich "keine".
        for faellig in ("", "keine"):
            plan = self.plan(aktion="anlegen", titel="Protokoll schreiben bis Freitag", faellig=faellig)
            self.assertEqual((plan.title, plan.requests[0].data["due_on"]), ("Protokoll schreiben", "2026-11-27"), faellig)
        kept = self.plan(aktion="anlegen", titel="Bericht bis Freitag", faellig="Montag")  # ausdrueckliche Angabe gilt
        self.assertEqual((kept.title, kept.requests[0].data["due_on"]), ("Bericht bis Freitag", "2026-11-23"))
        self.assertEqual(self.plan(aktion="anlegen", titel="Reise am 30.11.").requests[0].data["due_on"], "2026-11-30")
        self.assertNotIn("due_on", self.plan(aktion="anlegen", titel="Bis Freitag warten").requests[0].data)
        # Das Verb im Namen der Aufgabe.
        self.assertEqual(self.plan(aktion="umbenennen", aufgabe="Benenne Folien bauen", titel="Folien entwerfen").task_gid, "t2")

    def test_complete_reopen_rename_and_subtask(self) -> None:
        done = self.plan(aktion="abschliessen", aufgabe="angebot")
        self.assertEqual((done.title, done.changes, done.requests, done.undo), (
            "Angebot prüfen",
            (("Stand", "offen", "erledigt"),),
            (Request("PUT", "/tasks/t1", {"completed": True}),),
            (Request("PUT", "/tasks/t1", {"completed": False}),),
        ))
        self.assertEqual(self.plan(aktion="wieder_oeffnen", aufgabe="Vertrag").requests, (Request("PUT", "/tasks/t4", {"completed": False}),))
        renamed = self.plan(aktion="umbenennen", aufgabe="Folien bauen", titel="Folien entwerfen")
        self.assertEqual((renamed.changes, renamed.undo), ((("Titel", "Folien bauen", "Folien entwerfen"),), (Request("PUT", "/tasks/t2", {"name": "Folien bauen"}),)))
        sub = self.plan(aktion="unteraufgabe", aufgabe="Angebot prüfen", titel="Zahlen holen")
        self.assertEqual(sub.requests, (Request("POST", "/tasks/t1/subtasks", {"name": "Zahlen holen", "assignee": "me"}),))

    def test_due_date_is_set_changed_and_removed(self) -> None:
        moved = self.plan(aktion="faelligkeit", aufgabe="Angebot prüfen", faellig="Freitag")
        self.assertEqual(moved.changes, (("Fällig", "Di 24.11.2026", "Fr 27.11.2026"),))
        self.assertEqual((moved.requests, moved.undo), ((Request("PUT", "/tasks/t1", {"due_on": "2026-11-27"}),), (Request("PUT", "/tasks/t1", {"due_on": "2026-11-24"}),)))
        removed = self.plan(aktion="faelligkeit", aufgabe="Angebot prüfen", faellig="keine")
        self.assertEqual((removed.changes, removed.requests), ((("Fällig", "Di 24.11.2026", "kein Termin"),), (Request("PUT", "/tasks/t1", {"due_on": None}),)))
        first = self.plan(aktion="faelligkeit", aufgabe="Folien bauen", faellig="morgen")
        self.assertEqual((first.changes, first.undo), ((("Fällig", "kein Termin", "Sa 21.11.2026"),), (Request("PUT", "/tasks/t2", {"due_on": None}),)))

    def test_due_with_a_time_of_day_and_loosely_named_task(self) -> None:
        self.asana.add("t7", "Lagerhaus für Anfänger brainstormen", "p1", "s1")
        plan = self.plan(aktion="faelligkeit", aufgabe="Brainstorming Task", faellig="morgen 15 uhr")
        self.assertEqual((plan.task_gid, plan.changes), ("t7", (("Fällig", "kein Termin", "Sa 21.11.2026 15:00 Uhr"),)))
        stamp = plan.requests[0].data["due_at"]
        self.assertEqual(datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().strftime("%d.%m. %H:%M"), "21.11. 15:00")
        self.assertEqual(plan.undo, (Request("PUT", "/tasks/t7", {"due_on": None}),))
        self.assertEqual(aa.due_value("um 9:30", TODAY)[:4], "2026")
        self.assertIn("T", aa.due_value("Dienstag um 9.30 Uhr", TODAY))
        self.assertEqual(aa.due_value("am 24.11.", TODAY), "2026-11-24")  # ohne Uhrzeit bleibt es ein Tag
        with self.assertRaises(Clarify):
            aa.due_value("morgen 25 Uhr", TODAY)
        # Aus einem Zeitpunkt wird wieder "kein Termin": das gesetzte Feld wird geleert.
        self.asana.tasks["t7"]["due_at"] = stamp
        removed = self.plan(aktion="faelligkeit", aufgabe="brainstormen", faellig="keine")
        self.assertEqual((removed.requests[0].data, removed.undo[0].data), ({"due_at": None}, {"due_at": stamp}))
        # Aehnliche Worte ja, verschiedene nein.
        self.assertTrue(aa._similar("brainstorming", "brainstormen"))
        self.assertFalse(aa._similar("bauen", "drucken"))
        self.assertFalse(aa._similar("prüfen", "prüfung"))

    def test_section_move(self) -> None:
        plan = self.plan(aktion="abschnitt", aufgabe="Angebot prüfen", abschnitt="In Arbeit")
        self.assertEqual((plan.changes, plan.requests, plan.undo), (
            (("Abschnitt", "Offen", "In Arbeit"),),
            (Request("POST", "/sections/s2/addTask", {"task": "t1"}),),
            (Request("POST", "/sections/s1/addTask", {"task": "t1"}),),
        ))

    def test_questions_instead_of_guesses(self) -> None:
        asks = {
            "passt auf mehrere Aufgaben: Folien bauen; Folien drucken": dict(aktion="abschliessen", aufgabe="Folien"),
            "Eine Aufgabe „Reisekosten“ finde ich im Projekt nicht.": dict(aktion="abschliessen", aufgabe="Reisekosten"),
            "Eine Aufgabe „Kundentermin“ finde ich im Projekt nicht.": dict(aktion="abschliessen", aufgabe="Kundentermin"),  # anderes Projekt
            "Eine Aufgabe „Vertrag ablegen“ finde ich im Projekt nicht.": dict(aktion="abschliessen", aufgabe="Vertrag ablegen"),  # schon erledigt
            "Welche Aufgabe meinst du?": dict(aktion="abschliessen"),
            "Den Abschnitt „Review“ gibt es nicht. Abschnitte im Projekt: Offen; In Arbeit. Anlegen mit: „Leg den Abschnitt Review an“.": dict(aktion="abschnitt", aufgabe="Angebot", abschnitt="Review"),
            "Den Abschnitt „In Arbeit“ gibt es im Projekt schon.": dict(aktion="abschnitt_anlegen", abschnitt="in arbeit"),
            "steht schon im Abschnitt „Offen“": dict(aktion="abschnitt", aufgabe="Angebot", abschnitt="Offen"),
            "Die Zeitangabe „demnächst“ verstehe ich nicht.": dict(aktion="faelligkeit", aufgabe="Angebot", faellig="demnächst"),
            "steht schon auf Di 24.11.2026": dict(aktion="faelligkeit", aufgabe="Angebot", faellig="Dienstag"),
            "Auf wann soll „Angebot prüfen“ fällig sein?": dict(aktion="faelligkeit", aufgabe="Angebot"),
            "Eine offene Aufgabe „Folien bauen“ gibt es im Projekt schon.": dict(aktion="anlegen", titel="folien bauen"),
            "Wie soll die Aufgabe heißen?": dict(aktion="anlegen"),
            "Das kann ich nicht ausführen.": dict(aktion="unklar"),
        }
        for expected, wish in asks.items():
            with self.assertRaises(Clarify) as raised:
                self.plan(**wish)
            self.assertIn(expected, str(raised.exception), wish)


class TestLimits(ActionCase):
    def test_only_catalogue_requests_inside_the_project_pass(self) -> None:
        state = self.state()
        allowed = [
            Request("POST", "/tasks", {"name": "Neu", "projects": ["p1"], "assignee": "me"}),
            Request("PUT", "/tasks/t1", {"completed": True}),
            Request("POST", "/sections/s2/addTask", {"task": "t1"}),
            Request("POST", "/tasks/t1/subtasks", {"name": "Neu", "assignee": "me"}),
        ]
        for request in allowed:
            aa.check_allowed(request, state, set())
        refused = [
            Request("POST", "/tasks", {"name": "Neu", "projects": ["p2"], "assignee": "me"}),  # anderes Projekt
            Request("POST", "/tasks", {"name": "Neu", "projects": ["p1"], "assignee": "999"}),  # jemand anderem zuweisen
            Request("POST", "/tasks", {"name": "Neu", "projects": ["p1", "p2"], "assignee": "me"}),
            Request("PUT", "/tasks/k1", {"completed": True}),  # Aufgabe eines anderen Projekts
            Request("PUT", "/tasks/t1", {"assignee": "999"}),  # Feld ausserhalb des Katalogs
            Request("PUT", "/tasks/t1", {}),
            Request("POST", "/sections/s9/addTask", {"task": "t1"}),  # Abschnitt eines anderen Projekts
            Request("POST", "/sections/s2/addTask", {"task": "k1"}),
            Request("POST", "/tasks/t1/stories", {"text": "Kommentar"}),  # Kommentare gibt es nicht
            Request("DELETE", "/tasks/t1", {}),  # loeschen nur, was der Agent selbst angelegt hat
            Request("DELETE", "/projects/p1", {}),
            Request("PUT", "/projects/p1", {"name": "x"}),
        ]
        for request in refused:
            with self.assertRaises(Refused, msg=str(request)):
                aa.check_allowed(request, state, set())
        aa.check_allowed(Request("DELETE", "/tasks/n100", {}), state, {"n100"})

    def test_execute_refuses_before_anything_is_written(self) -> None:
        state = self.state()
        bad = aa.Plan("abschliessen", "Kundentermin", (), (Request("PUT", "/tasks/k1", {"completed": True}),), ())
        with self.assertRaises(Refused):
            aa.execute(bad, state, self.asana.write, self.asana.read)
        self.assertEqual(self.asana.written, [])


class TestAct(ActionCase):
    def test_approved_action_is_executed_checked_and_logged(self) -> None:
        seen: list = []
        result = aa.act("Schließe Angebot prüfen ab", PROFILE, wishing({"aktion": "abschliessen", "aufgabe": "Angebot prüfen"}, seen=seen), self.asana.read, self.asana.write, self.journal, NOW, lambda prompt: "j", self.printed.append)
        self.assertEqual(result, "ausgefuehrt")
        self.assertTrue(self.asana.tasks["t1"]["completed"])
        self.assertEqual(self.asana.written, [("PUT", "/tasks/t1", {"completed": True})])
        self.assertEqual(self.printed[0], f"Aufgabe abschließen im Projekt „{PROJECT}“: Angebot prüfen\n  Stand: offen → erledigt")
        self.assertEqual(self.printed[1], "Erledigt und kontrolliert.")
        self.assertEqual(aa.journal_lines(self.journal.rows()), ["2026-11-20 12:00 · Aufgabe abschließen: Angebot prüfen · Stand: offen → erledigt"])
        # Das Modell sieht den Satz und die Abschnitte, aber keine Aufgabe aus Asana.
        self.assertIn("wenn er dort steht: Offen; In Arbeit", seen[0])
        self.assertNotIn("Angebot", seen[0])
        self.assertNotIn("Folien", seen[0])

    def test_nothing_is_written_without_a_yes(self) -> None:
        for answer, expected in (("n", "abgebrochen"), ("", "abgebrochen"), ("vielleicht", "abgebrochen"), (None, "nicht_freigegeben")):
            self.assertEqual(self.act({"aktion": "abschliessen", "aufgabe": "Angebot prüfen"}, answer), expected)
        self.assertEqual((self.asana.written, self.journal.rows()), ([], []))
        self.assertIn("Nicht ausgeführt: Ohne Terminal gibt es keine Freigabe.", self.printed)

    def test_without_write_projects_nothing_is_done(self) -> None:
        self.assertEqual(self.act({"aktion": "abschliessen", "aufgabe": "Angebot prüfen"}, profile=Profile()), "rueckfrage")
        self.assertIn("Schreiben nach Asana ist nicht freigegeben.", self.printed[0])
        self.assertEqual(self.asana.written, [])

    def test_question_instead_of_action(self) -> None:
        self.assertEqual(self.act({"aktion": "abschliessen", "aufgabe": "Folien"}), "rueckfrage")
        self.assertIn("passt auf mehrere Aufgaben", self.printed[0])
        self.assertEqual(self.asana.written, [])

    def test_create_with_section_and_undo_deletes_only_the_new_task(self) -> None:
        self.assertEqual(self.act({"aktion": "anlegen", "titel": "Protokoll schreiben", "faellig": "Dienstag", "abschnitt": "In Arbeit"}), "ausgefuehrt")
        new = self.asana.tasks["n100"]
        self.assertEqual((new["name"], new["due_on"], new["section"], new["project"]), ("Protokoll schreiben", "2026-11-24", "s2", "p1"))
        printed: list[str] = []
        result = aa.undo_last(PROFILE, self.asana.read, self.asana.write, self.journal, NOW, lambda prompt: "j", printed.append)
        self.assertEqual(result, "ausgefuehrt")
        self.assertEqual(printed[0], "Zurücknehmen: Aufgabe anlegen: Protokoll schreiben\n  Die angelegte Aufgabe wird gelöscht.")
        self.assertNotIn("n100", self.asana.tasks)
        self.assertEqual(len(self.asana.tasks), 5)
        self.assertTrue(aa.journal_lines(self.journal.rows())[0].endswith("(zurückgenommen)"))
        self.assertEqual(aa.undo_last(PROFILE, self.asana.read, self.asana.write, self.journal, NOW, lambda prompt: "j", printed.append), "rueckfrage")

    def test_the_task_from_just_now_is_the_one_of_the_last_action(self) -> None:
        self.act({"aktion": "anlegen", "titel": "Workshop vorbereiten"})
        # Das Modell kann "den eben angelegten Task" nicht benennen; der Code setzt ihn ein.
        result = self.act({"aktion": "abschnitt", "aufgabe": "eben angelegten task", "abschnitt": "In Arbeit"}, sentence="pack den eben angelegten task in den Abschnitt In Arbeit")
        self.assertEqual((result, self.asana.tasks["n100"]["section"]), ("ausgefuehrt", "s2"))
        self.assertEqual(self.act({"aktion": "faelligkeit", "aufgabe": "", "faellig": "Montag"}, sentence="Setz die Aufgabe von eben auf Montag"), "ausgefuehrt")
        self.assertEqual(self.asana.tasks["n100"]["due_on"], "2026-11-23")
        # Ohne solchen Verweis gilt der Name; und gibt es die Aufgabe nicht mehr, wird nachgefragt.
        self.assertEqual(self.act({"aktion": "abschliessen", "aufgabe": "Angebot prüfen"}, sentence="Hak Angebot prüfen ab"), "ausgefuehrt")
        del self.asana.tasks["t1"]
        self.assertEqual(self.act({"aktion": "wieder_oeffnen", "aufgabe": "letzte Aufgabe"}, sentence="Öffne die letzte Aufgabe wieder"), "rueckfrage")

    def test_new_section_is_created_used_and_can_be_taken_back(self) -> None:
        self.assertEqual(self.act({"aktion": "abschnitt_anlegen", "abschnitt": "Kunde A"}), "ausgefuehrt")
        self.assertEqual(self.printed[0], f"Abschnitt anlegen im Projekt „{PROJECT}“: Kunde A\n  Abschnitt: Kunde A")
        self.assertEqual(self.asana.sections["s100"], ("p1", "Kunde A"))
        self.assertEqual(self.asana.written, [("POST", "/projects/p1/sections", {"name": "Kunde A"})])
        self.assertEqual(self.act({"aktion": "abschnitt", "aufgabe": "Angebot prüfen", "abschnitt": "Kunde A"}), "ausgefuehrt")
        self.assertEqual(self.asana.tasks["t1"]["section"], "s100")
        printed: list[str] = []
        aa.undo_last(PROFILE, self.asana.read, self.asana.write, self.journal, NOW, lambda prompt: "j", printed.append)  # die Verschiebung
        aa.undo_last(PROFILE, self.asana.read, self.asana.write, self.journal, NOW, lambda prompt: "j", printed.append)  # der Abschnitt
        self.assertIn("Zurücknehmen: Abschnitt anlegen: Kunde A\n  Der angelegte Abschnitt wird gelöscht.", printed)
        self.assertEqual((self.asana.tasks["t1"]["section"], "s100" in self.asana.sections), ("s1", False))
        # Abschnitte anderer Projekte und fremde Abschnitte bleiben gesperrt.
        state = self.state()
        for request in (Request("POST", "/projects/p2/sections", {"name": "x"}), Request("DELETE", "/sections/s1", {})):
            with self.assertRaises(Refused):
                aa.check_allowed(request, state, set())

    def test_loose_names_and_the_new_section(self) -> None:
        # "den Angebots-Task": Artikel und "Task" benennen nichts, das uebrige Wort genuegt.
        self.assertEqual(self.plan(aktion="abschliessen", aufgabe="den angebot task").task_gid, "t1")
        self.assertEqual(self.plan(aktion="abschnitt", aufgabe="die Aufgabe Angebot", abschnitt="den Abschnitt In Arbeit").requests[0].path, "/sections/s2/addTask")
        with self.assertRaises(Clarify):
            self.plan(aktion="abschliessen", aufgabe="den task")
        self.act({"aktion": "anlegen", "titel": "Workshop vorbereiten"})
        self.act({"aktion": "abschnitt_anlegen", "abschnitt": "Kunde A"})
        # Das Modell kennt den neuen Abschnitt nicht beim Namen; der Code setzt ihn ein.
        result = self.act({"aktion": "abschnitt", "aufgabe": "den angebot task", "abschnitt": "neuen abschnitt"}, sentence="tu den angebot task in den neuen abschnitt")
        self.assertEqual((result, self.asana.tasks["t1"]["section"]), ("ausgefuehrt", "s101"))
        # Beide Verweise in einem Satz: die letzte Aufgabe (nicht der Abschnitt) in den neuen Abschnitt.
        self.act({"aktion": "anlegen", "titel": "Protokoll schreiben"})
        result = self.act({"aktion": "abschnitt", "aufgabe": "eben angelegten task", "abschnitt": ""}, sentence="pack den eben angelegten task in den neuen Abschnitt")
        self.assertEqual((result, self.asana.tasks["n102"]["section"]), ("ausgefuehrt", "s101"))
        # Ein genannter Abschnitt bleibt, auch wenn "neu" im Satz steht.
        result = self.act({"aktion": "abschnitt", "aufgabe": "Folien bauen", "abschnitt": "In Arbeit"}, sentence="Schieb die neue Aufgabe Folien bauen nach In Arbeit")
        self.assertEqual((result, self.asana.tasks["t2"]["section"]), ("ausgefuehrt", "s2"))

    def test_undo_restores_the_previous_value_after_approval(self) -> None:
        self.act({"aktion": "faelligkeit", "aufgabe": "Angebot prüfen", "faellig": "Freitag"})
        self.assertEqual(self.asana.tasks["t1"]["due_on"], "2026-11-27")
        printed: list[str] = []
        self.assertEqual(aa.undo_last(PROFILE, self.asana.read, self.asana.write, self.journal, NOW, lambda prompt: "n", printed.append), "abgebrochen")
        self.assertEqual(self.asana.tasks["t1"]["due_on"], "2026-11-27")
        self.assertEqual(aa.undo_last(PROFILE, self.asana.read, self.asana.write, self.journal, NOW, lambda prompt: "j", printed.append), "ausgefuehrt")
        self.assertEqual(self.asana.tasks["t1"]["due_on"], "2026-11-24")

    def test_failed_check_is_reported_and_logged(self) -> None:
        original = self.asana.write
        self.asana.write = lambda method, path, data: {"data": {"gid": "t1"}}  # Asana nimmt an, aendert aber nichts
        self.assertEqual(self.act({"aktion": "abschliessen", "aufgabe": "Angebot prüfen"}), "fehler")
        self.assertIn("Ausgeführt, aber die Kontrolle stimmt nicht: completed ist False, erwartet True", self.printed[-1])
        self.assertEqual(len(self.journal.rows()), 1)
        self.asana.write = original

    def test_sentences_for_many_tasks_or_many_actions_never_reach_the_model(self) -> None:
        refused = {
            "Schließe alle Aufgaben ab": "Mehrere Aufgaben auf einmal",
            "Hak sämtliche offenen Tasks ab": "Mehrere Aufgaben auf einmal",
            "Setz beide Aufgaben auf Montag": "Mehrere Aufgaben auf einmal",
            "Lösch alles": "Mehrere Aufgaben auf einmal",
            "Schließe Folien bauen ab und leg eine Aufgabe Druck an": "mehrere Aktionen",
            "Hak Angebot prüfen ab, dann verschieb Folien bauen auf Montag": "mehrere Aktionen",
        }
        for sentence, expected in refused.items():
            self.printed.clear()
            # Ein Modell, das trotzdem eine Aktion liefern wuerde, wird gar nicht erst gefragt.
            result = self.act({"aktion": "abschliessen", "aufgabe": "Angebot prüfen"}, sentence=sentence)
            self.assertEqual(result, "rueckfrage", sentence)
            self.assertIn(expected, self.printed[0], sentence)
        self.assertEqual(self.asana.written, [])
        for fine in ("Leg die Aufgabe Angebot prüfen und versenden an", "Hak Angebot prüfen ab", "Leg die Aufgabe Alle Jahre wieder an"):
            aa.refuse_by_wording(fine)

    def test_creating_an_existing_task_with_a_section_means_moving_it(self) -> None:
        plan = self.plan(aktion="anlegen", titel="Folien bauen", abschnitt="In Arbeit")
        self.assertEqual((plan.action, plan.changes), ("abschnitt", (("Abschnitt", "Offen", "In Arbeit"),)))
        with self.assertRaises(Clarify):  # ohne Abschnitt bleibt es eine Rueckfrage
            self.plan(aktion="anlegen", titel="Folien bauen")

    def test_unusable_model_answer_counts_as_unclear(self) -> None:
        def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return structured_reply(info, {"aktion": "loeschen"})

        result = aa.act("Lösch die Aufgabe Folien bauen", PROFILE, FunctionModel(broken), self.asana.read, self.asana.write, self.journal, NOW, lambda prompt: "j", self.printed.append)
        self.assertEqual(result, "rueckfrage")
        self.assertIn("Das kann ich nicht ausführen.", self.printed[0])
        self.assertEqual(self.asana.written, [])

    def test_asana_error_while_writing_reaches_the_caller(self) -> None:
        def failing(method: str, path: str, data: dict) -> dict:
            raise AsanaAccessError("Das Token darf darauf nicht zugreifen. (HTTP 403 bei /tasks/t1)")

        with self.assertRaises(AsanaAccessError):
            aa.act("Satz", PROFILE, wishing({"aktion": "abschliessen", "aufgabe": "Angebot prüfen"}), self.asana.read, failing, self.journal, NOW, lambda prompt: "j", self.printed.append)
        self.assertEqual(self.journal.rows(), [])


if __name__ == "__main__":
    unittest.main()

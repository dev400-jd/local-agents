"""Tests fuer den Nachrichten-Agenten: Entwurf, Selbstpruefung, Freigabe, Vorgaben."""

import contextlib
import io
import unittest
from datetime import date, datetime

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents import message_agent as agent
from local_agents import orchestrator
from local_agents.calendar_events import Event
from local_agents.planner_agent import ANNOUNCE, ASK, DECLINE, move_title, parse_move_title
from local_agents.profile import Context, Profile
from local_agents.store import DONE, IN_PROGRESS, OPEN, REJECTED, Store, StoredEvent

models.ALLOW_MODEL_REQUESTS = False

PROFILE = Profile(name="Alex Beispiel", contexts=(Context("intern", "intern", "Intern"),))
START = datetime(2026, 11, 18, 10, 0)
EVENT = StoredEvent("e", "Jour fixe", START, datetime(2026, 11, 18, 11, 0), "intern", attendees=4, organizer="Muster, Erika")
TITLE = "Nachricht an Muster, Erika: Jour fixe (Mi 18.11. 10:00) verschieben, weil Kundentag Kunde ACME"
GOOD = (
    "Hallo Erika,\n\nden Jour fixe am Mi 18.11. um 10:00 Uhr schaffe ich leider nicht, "
    "ich bin an dem Tag beim Kunden vor Ort. Könntest du ihn auf einen anderen Tag legen?\n\n"
    "Viele Grüße\nAlex"
)


def writing(*texts: str, seen: list | None = None) -> FunctionModel:
    """Modell, das nacheinander die gegebenen Entwuerfe liefert (den letzten immer wieder)."""
    replies = list(texts)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append(info.instructions)
        return ModelResponse(parts=[TextPart(replies.pop(0) if len(replies) > 1 else replies[0])])

    return FunctionModel(respond)


class TestMoveTitles(unittest.TestCase):
    def test_every_title_the_planner_writes_can_be_read_back(self) -> None:
        def event(**extra) -> Event:
            return Event(START, START, "Jour fixe", **extra)

        what = "Jour fixe (Mi 18.11. 10:00)"
        cases = (
            (event(organizer="Erika Muster"), ASK),
            (event(organizer=""), ASK),
            (event(organizer_is_me=True), ANNOUNCE),
            (event(organizer="Kim Beispiel", attendee_domains=tuple(["beispiel.de"] * 12)), DECLINE),
        )
        for meeting, kind in cases:
            for reason in ("Kundentag Kunde ACME", "interner Block 09:00-12:00, Stresslevel hoch bei Kunde ACME"):
                title = move_title(meeting, what, reason)
                self.assertEqual(parse_move_title(title), (kind, reason), title)
        self.assertIsNone(parse_move_title("Vorbereitung: Jour fixe (Mi 18.11. 10:00)"))


class TestRequest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.store.upsert_event(EVENT)
        self.store.upsert_task("e", "verschieben", TITLE, "intern", date(2026, 11, 15), "planner")
        self.task = self.store.tasks()[0]

    def test_request_is_built_by_code_without_internal_details(self) -> None:
        request = agent.build_request(self.task, EVENT, PROFILE)
        self.assertEqual(
            (request.kind, request.recipient, request.sender, request.form),
            (ASK, "Erika", "Alex", "du"),
        )
        self.assertEqual(request.why, "Ich bin an diesem Tag beim Kunden vor Ort.")
        facts = agent.facts(request)
        self.assertIn("Termin: „Jour fixe“ am Mi 18.11. um 10:00 Uhr", facts)
        self.assertNotIn("ACME", facts)  # der Kundenname bleibt draussen

    def test_first_names_and_reasons(self) -> None:
        for display, expected in (
            ("Erika Muster", "Erika"),
            ("Muster, Erika", "Erika"),
            ("Organisator", ""),
            ("", ""),
            ("erika@beispiel.de", ""),
        ):
            self.assertEqual(agent.first_name(display), expected, display)
        self.assertIn("stark eingebunden", agent.reason_sentence("interner Block 09:00-12:00, Stresslevel hoch bei Kunde ACME"))
        self.assertEqual(agent.reason_sentence("ich bin im Urlaub"), "Ich bin im Urlaub.")

    def test_other_tasks_are_refused(self) -> None:
        self.store.upsert_task("e", "vorbereitung", "Vorbereitung: Jour fixe", "intern", date(2026, 11, 17), "planner")
        with self.assertRaises(agent.NoMessageTask):
            agent.build_request(self.store.tasks()[1], EVENT, PROFILE)
        with self.assertRaises(agent.NoMessageTask):
            agent.build_request(self.task, None, PROFILE)


class TestSelfCheck(unittest.TestCase):
    def setUp(self) -> None:
        self.request = agent.Request(ASK, "Erika", "Jour fixe", START, "Ich bin an diesem Tag beim Kunden vor Ort.", "Alex")

    def test_good_draft_has_no_problems(self) -> None:
        self.assertEqual(agent.problems(GOOD, self.request), [])

    def test_each_flaw_is_named(self) -> None:
        flaws = {
            GOOD.replace("Jour fixe", "Termin"): "der Titel des Termins fehlt",
            GOOD.replace("18.11.", "Mittwoch"): "das Datum des Termins fehlt",
            GOOD.replace("anderen Tag legen", "20.11. um 14:00 legen"): "Zahlen, die nicht in den Angaben stehen: 20, 14",
            GOOD.replace("Alex", "[Name]"): "Platzhalter im Text",
            GOOD.replace("Hallo Erika", "Hallo"): "die Anrede mit dem Vornamen fehlt",
            GOOD.replace("Könntest du ihn", "Könnten Sie ihn"): "förmliche Anrede",
            "Hallo Erika, Jour fixe am 18.11. um 10:00 geht nicht.": "Länge",
            GOOD.replace("Könntest du ihn auf einen anderen Tag legen?", "Ich verschiebe ihn auf einen anderen Tag."): "die Bitte fehlt",
        }
        for text, expected in flaws.items():
            found = agent.problems(text, self.request)
            self.assertTrue(any(expected in problem for problem in found), (expected, found))

    def test_formal_address_is_checked_the_other_way_round(self) -> None:
        formal = agent.Request(ASK, "Erika", "Jour fixe", START, self.request.why, "Alex", form="Sie")
        self.assertTrue(any("Anrede mit du" in p for p in agent.problems(GOOD, formal)))
        self.assertEqual(agent.problems(agent.template(formal), formal), [])

    def test_templates_pass_their_own_check(self) -> None:
        for kind, recipient in ((ASK, "Erika"), (ASK, ""), (DECLINE, "Kim"), (ANNOUNCE, "")):
            for sender in ("Alex", ""):
                request = agent.Request(kind, recipient, "Jour fixe", START, self.request.why, sender)
                self.assertEqual(agent.problems(agent.template(request), request), [], (kind, recipient, sender))


class MessageCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.store.upsert_event(EVENT)
        self.store.upsert_task("e", "verschieben", TITLE, "intern", date(2026, 11, 15), "planner")

    @property
    def task(self):
        return self.store.tasks()[0]


class TestDraft(MessageCase):
    def test_draft_is_stored_with_the_task_and_reused(self) -> None:
        seen: list = []
        result = agent.draft(self.task, EVENT, PROFILE, self.store, writing(GOOD, seen=seen))
        self.assertEqual((result.text, result.source, result.stored), (GOOD, agent.MODEL, False))
        self.assertEqual((self.task.status, self.task.result), (IN_PROGRESS, GOOD))
        self.assertIn("Empfänger: Erika (Anrede mit Vornamen)", seen[0])
        self.assertEqual(self.store.runs()[0]["agent"], "nachricht")
        again = agent.draft(self.task, EVENT, PROFILE, self.store, None)  # braucht kein Modell
        self.assertEqual((again.text, again.stored), (GOOD, True))

    def test_flawed_draft_is_sent_back_to_the_model(self) -> None:
        seen: list = []
        bad = GOOD.replace("anderen Tag legen", "20.11. legen")
        result = agent.draft(self.task, EVENT, PROFILE, self.store, writing(bad, GOOD, seen=seen))
        self.assertEqual((result.text, len(seen)), (GOOD, 2))

    def test_template_if_the_model_keeps_failing(self) -> None:
        result = agent.draft(self.task, EVENT, PROFILE, self.store, writing("Geht nicht."))
        self.assertEqual(result.source, agent.TEMPLATE)
        self.assertIn("Hallo Erika,", result.text)
        self.assertIn("„Jour fixe“ am Mi 18.11. um 10:00 Uhr", result.text)
        self.assertIn("Könntest du ihn auf einen anderen Tag verschieben?", result.text)
        self.assertTrue(result.text.endswith("Viele Grüße\nAlex"))
        self.assertEqual(self.task.status, IN_PROGRESS)

    def test_change_request_and_preferences_reach_the_model(self) -> None:
        self.store.add_preference("nachricht", "immer  ohne Ausrufezeichen")
        agent.draft(self.task, EVENT, PROFILE, self.store, writing(GOOD))
        seen: list = []
        shorter = GOOD.replace("leider ", "")
        result = agent.draft(self.task, EVENT, PROFILE, self.store, writing(shorter, seen=seen), change="ohne leider")
        self.assertEqual(self.task.result, shorter)
        self.assertEqual(result.stored, False)
        self.assertIn("- immer ohne Ausrufezeichen", seen[0])
        self.assertIn("Der Absender möchte ihn geändert haben: ohne leider", seen[0])
        self.assertIn("ich bin an dem Tag beim Kunden vor Ort", seen[0])  # der bisherige Entwurf


class TestReview(MessageCase):
    def review(self, *answers: str, redo=None) -> tuple[str, list[str], list[str]]:
        result = agent.draft(self.task, EVENT, PROFILE, self.store, writing(GOOD))
        replies, printed, copied = list(answers), [], []
        decision = agent.review(
            self.task,
            result,
            self.store,
            redo or (lambda change: agent.Draft(GOOD + f" ({change})", agent.MODEL)),
            ask=lambda question: replies.pop(0),
            out=printed.append,
            copy=lambda text: copied.append(text) or True,
        )
        return decision, printed, copied

    def test_accept_copies_the_text_and_finishes_the_task(self) -> None:
        decision, printed, copied = self.review("")
        self.assertEqual((decision, copied), ("uebernommen", [GOOD]))
        self.assertEqual((self.task.status, self.task.result), (DONE, GOOD))
        self.assertIn("Zwischenablage", printed[-1])

    def test_drop_keeps_the_task_open_without_draft(self) -> None:
        decision, _, copied = self.review("n")
        self.assertEqual((decision, copied, self.task.status, self.task.result), ("verworfen", [], OPEN, ""))

    def test_refuse_takes_the_task_out(self) -> None:
        decision, _, _ = self.review("a")
        self.assertEqual((decision, self.task.status), ("abgelehnt", REJECTED))

    def test_change_is_applied_and_remembered_only_on_request(self) -> None:
        decision, printed, copied = self.review("bitte kürzer", "ohne Gruß", "j", "j", "n")
        self.assertEqual(decision, "uebernommen")
        self.assertEqual(copied, [GOOD + " (ohne Gruß)"])
        self.assertEqual(self.store.preferences("nachricht"), ["bitte kürzer"])
        self.assertIn("Gemerkt.", printed)

    def test_end_of_input_leaves_the_draft_stored(self) -> None:
        def closed(question: str) -> str:
            raise EOFError

        result = agent.draft(self.task, EVENT, PROFILE, self.store, writing(GOOD))
        decision = agent.review(self.task, result, self.store, lambda change: result, ask=closed, out=lambda text: None)
        self.assertEqual((decision, self.task.status, self.task.result), ("offen", IN_PROGRESS, GOOD))


class TestCommandAndChain(MessageCase):
    def call(self, *args: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = agent.main(["message_agent", *args], store=self.store)
        return code, buffer.getvalue()

    def test_preferences_can_be_listed_and_removed(self) -> None:
        self.assertEqual(self.call("vorgaben"), (0, "Keine Vorgaben gemerkt.\n"))
        number = self.store.add_preference("nachricht", "kürzer")
        self.assertEqual(self.call("vorgaben"), (0, f"{number}: kürzer\n"))
        self.assertEqual(self.call("vergiss", str(number))[0], 0)
        self.assertEqual(self.call("vergiss", "99")[0], 1)
        self.assertEqual(self.store.preferences("nachricht"), [])
        self.assertEqual(self.call("unsinn")[0], 2)

    def test_orchestrator_hands_move_tasks_to_the_message_agent(self) -> None:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            status = "Status-Agent" in (info.instructions or "")
            return ModelResponse(parts=[TextPart("Als Nächstes die Nachricht an Erika." if status else GOOD)])

        output = orchestrator.run(datetime(2026, 11, 15, 19, 0), self.store, PROFILE, lambda name: FunctionModel(respond))
        self.assertIn("**Entwurf der Nachricht zu Aufgabe #1 (neu)**\nHallo Erika,", output)
        self.assertTrue(output.endswith("Freigeben oder ändern: python -m local_agents.message_agent 1"), output)
        self.assertEqual(self.task.status, IN_PROGRESS)

    def test_next_move_task_skips_other_kinds(self) -> None:
        before = datetime(2026, 11, 15, 9, 0)
        self.store.upsert_task("e", "vorbereitung", "Vorbereitung: Jour fixe", "intern", date(2026, 11, 14), "planner")
        self.assertEqual(agent.next_move_task(self.store, before).kind, "verschieben")
        self.store.set_status(1, DONE)
        self.assertIsNone(agent.next_move_task(self.store, before))

    def test_no_message_once_the_meeting_has_started(self) -> None:
        after = datetime(2026, 11, 19, 9, 0)  # der Termin war am 18.11.
        self.assertIsNone(agent.next_move_task(self.store, after))
        with self.assertRaises(agent.MeetingStarted) as raised:
            agent.draft(self.task, EVENT, PROFILE, self.store, None, now=after)
        self.assertIn("hat schon begonnen (Mi 18.11. um 10:00 Uhr)", str(raised.exception))
        self.assertEqual(self.task.status, OPEN)

    def test_command_discards_move_tasks_of_meetings_that_have_started(self) -> None:
        later = StoredEvent("f", "Teamrunde", datetime(2026, 11, 25, 9), datetime(2026, 11, 25, 10), "intern")
        self.store.upsert_event(later)
        title = "Nachricht an Kim Beispiel: Teamrunde (Mi 25.11. 09:00) verschieben, weil Kundentag Kunde ACME"
        self.store.upsert_task("f", "verschieben", title, "intern", date(2026, 11, 22), "planner")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = agent.main(["message_agent", "vorgaben"], store=self.store, now=datetime(2026, 11, 19, 9, 0))
        self.assertEqual(code, 0)
        self.assertEqual(self.task.status, OPEN)  # "vorgaben" raeumt nicht auf
        started = agent.discard_started(self.store, datetime(2026, 11, 19, 9, 0))
        self.assertEqual([task.id for task in started], [1])
        statuses = {task.id: task.status for task in self.store.tasks()}
        self.assertEqual(statuses, {1: "verworfen", 2: OPEN})
        self.assertEqual(agent.next_move_task(self.store, datetime(2026, 11, 19, 9, 0)).id, 2)


if __name__ == "__main__":
    unittest.main()

"""Tests fuer die Rueckmeldung des Nutzers auf den Plan."""

import unittest
from datetime import date

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents.testing import structured_reply
from local_agents.feedback import is_named, resolve_due, Feedback, NewTask, apply, handle, is_approval
from local_agents.profile import Context, Profile
from local_agents.store import OPEN, REJECTED, Store, StoredEvent
from datetime import datetime

models.ALLOW_MODEL_REQUESTS = False

PROFILE = Profile(
    contexts=(
        Context("intern", "intern", "Intern"),
        Context("kunde-acme", "kunde", "Kunde ACME", keywords=("acme",)),
    )
)
TODAY = date(2026, 11, 15)


def answering(*outputs: dict, seen: list | None = None) -> FunctionModel:
    """Modell, das nacheinander die gegebenen Feedback-Ausgaben liefert."""
    replies = list(outputs)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append((info.instructions, messages[0].parts[-1].content))
        return structured_reply(info, replies.pop(0))

    return FunctionModel(respond)


class TestApproval(unittest.TestCase):
    def test_plain_agreement_needs_no_model(self) -> None:
        for answer in ("", "ja", "Ja, passt.", "ok passt so", "Passt soweit, danke!", "alles klar"):
            self.assertTrue(is_approval(answer), answer)

    def test_anything_else_is_a_change_request(self) -> None:
        for answer in ("ja, aber das Daily raus", "nein", "die Absage nicht", "passt nicht"):
            self.assertFalse(is_approval(answer), answer)


class TestFeedback(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.store.upsert_event(
            StoredEvent("d", "Daily", datetime(2026, 11, 16, 9), datetime(2026, 11, 16, 9, 30), "kunde-acme")
        )
        self.store.upsert_task("d", "vorbereitung", "Vorbereitung: Daily", "kunde-acme", TODAY, "planner")
        self.store.upsert_task("d", "verschieben", "Nachricht an Erika Muster: Daily verschieben", "intern", TODAY, "planner")

    def handle(self, answer: str, model) -> list[str]:
        outcome = handle(answer, self.store, PROFILE, TODAY, model)
        return [*outcome.lines, outcome.spoken]

    def statuses(self) -> dict[int, str]:
        return {task.id: task.status for task in self.store.tasks()}

    def test_approval_changes_nothing_and_logs_no_run(self) -> None:
        self.assertEqual(self.handle("ja passt", None), ["Alles klar, der Plan bleibt so."])
        self.assertEqual((self.statuses(), self.store.runs()), ({1: OPEN, 2: OPEN}, []))

    def test_removes_and_adds_tasks(self) -> None:
        seen: list = []
        model = answering(
            {"remove": [2], "add": [{"title": "Folien  fuer ACME bauen", "due": "2026-11-17"}]}, seen=seen
        )
        self.assertEqual(
            self.handle("Die Nachricht an Erika raus, dazu Folien bis Dienstag", model),
            [
                "- raus: #2 Nachricht an Erika Muster: Daily verschieben",
                "- neu: #3 Folien fuer ACME bauen, fertig bis Di 17.11.2026",
                "Alles klar, ich habe eine Aufgabe herausgenommen und eine Aufgabe ergänzt.",
            ],
        )
        self.assertEqual(self.statuses(), {1: OPEN, 2: REJECTED, 3: OPEN})
        new = self.store.tasks()[-1]
        self.assertEqual((new.kind, new.context, new.event_id), ("manuell", "kunde-acme", None))
        instructions, prompt = seen[0]
        self.assertIn("#2 Nachricht an Erika Muster: Daily verschieben (fertig bis So 2026-11-15)", instructions)
        self.assertIn("Heute ist So 2026-11-15. Die naechsten Tage: So 2026-11-15, Mo 2026-11-16", instructions)
        self.assertEqual(prompt, "Die Nachricht an Erika raus, dazu Folien bis Dienstag")
        self.assertEqual(self.store.runs()[0]["summary"], "1 herausgenommen, 1 ergänzt")

    def test_unknown_task_number_is_sent_back_to_the_model(self) -> None:
        model = answering({"remove": [99]}, {"remove": [1]})
        self.assertEqual(self.handle("das Daily raus", model)[0], "- raus: #1 Vorbereitung: Daily")

    def test_unclear_answer_changes_nothing(self) -> None:
        model = answering({"unclear": "Welcher Termin ist gemeint?"})
        self.assertEqual(
            self.handle("das eine da weg", model),
            [
                "- nicht verstanden: Welcher Termin ist gemeint?",
                "Ich habe nichts geändert. Einen Teil habe ich nicht verstanden, er steht im Terminal.",
            ],
        )
        self.assertEqual(self.statuses(), {1: OPEN, 2: OPEN})

    def test_guessed_removal_is_not_carried_out_but_asked_back(self) -> None:
        model = answering({"remove": [2]})
        self.assertEqual(
            self.handle("das mit dem Dings am Nachmittag passt mir nicht", model),
            [
                "- nicht verstanden: Meinst du #2 Nachricht an Erika Muster: Daily verschieben? "
                "Das habe ich nicht herausgenommen; nenne die Nummer oder den Termin.",
                "Ich habe nichts geändert. Einen Teil habe ich nicht verstanden, er steht im Terminal.",
            ],
        )
        self.assertEqual(self.statuses(), {1: OPEN, 2: OPEN})

    def test_task_counts_as_named_by_number_day_word_or_all(self) -> None:
        task = self.store.tasks()[1]  # #2 Nachricht an Erika Muster: Daily verschieben, fällig So 15.11.
        for answer in (
            "nimm Aufgabe 2 raus",
            "die 2 brauche ich nicht",
            "die Nachrichten bitte nicht",
            "das Daily raus",
            "alles für heute raus",
            "alle raus",
        ):
            self.assertTrue(is_named(task, answer, TODAY), answer)
        for answer in (
            "das mit dem Dings passt nicht",
            "bis zum 2. die Folien",
            "um 12:00 passt es nicht",
            "das am Donnerstag raus",
        ):
            self.assertFalse(is_named(task, answer, TODAY), answer)

    def test_due_date_comes_from_the_users_words_not_the_models_arithmetic(self) -> None:
        def due(answer: str, removing: bool = False, **new) -> date | None:
            return resolve_due(NewTask(title="Folien", **new), answer, TODAY, removing)

        tuesday = date(2026, 11, 17)
        # Das Modell verrechnet sich oder laesst das Datum weg: die Worte gelten.
        self.assertEqual(due("bis Dienstag die Folien", when="bis Dienstag", due=date(2026, 11, 24)), tuesday)
        self.assertEqual(due("bis Dienstag die Folien", when="bis Dienstag"), tuesday)
        self.assertEqual(due("bis Dienstag die Folien"), tuesday)
        self.assertEqual(due("bis zum 25. die Folien", when="bis zum 25."), date(2026, 11, 25))
        # Kein Tag genannt: ein erfundenes Datum zaehlt nicht.
        self.assertIsNone(due("noch die Folien", due=date(2026, 11, 20)))
        # Der einzige Tag gehoert zur herausgenommenen Aufgabe, nicht zur neuen.
        self.assertIsNone(due("das Daily am Montag raus, dazu Folien", removing=True))
        # Zwei Tage: das Datum des Modells gilt, wenn die Antwort es nennt.
        both = "das Daily am Montag raus, dazu bis Dienstag Folien"
        self.assertEqual(due(both, removing=True, due=tuesday), tuesday)
        self.assertEqual(due(both, removing=True, when="bis Dienstag", due=date(2026, 11, 16)), tuesday)

    def test_new_task_without_or_with_past_date_is_due_today(self) -> None:
        feedback = Feedback(add=[NewTask(title="A"), NewTask(title="B", due=date(2026, 11, 1)), NewTask(title=" ")])
        outcome = apply(feedback, [], self.store, PROFILE, TODAY)
        self.assertEqual(outcome.added, 2)
        self.assertEqual({task.due_date for task in self.store.tasks() if task.kind == "manuell"}, {TODAY})


if __name__ == "__main__":
    unittest.main()

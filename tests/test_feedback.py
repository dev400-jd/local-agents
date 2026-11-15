"""Tests fuer die Rueckmeldung des Nutzers auf den Plan."""

import unittest
from datetime import date

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents.feedback import Feedback, NewTask, apply, handle, is_approval
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
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, replies.pop(0))])

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

    def test_new_task_without_or_with_past_date_is_due_today(self) -> None:
        feedback = Feedback(add=[NewTask(title="A"), NewTask(title="B", due=date(2026, 11, 1)), NewTask(title=" ")])
        outcome = apply(feedback, [], self.store, PROFILE, TODAY)
        self.assertEqual(outcome.added, 2)
        self.assertEqual({task.due_date for task in self.store.tasks() if task.kind == "manuell"}, {TODAY})


if __name__ == "__main__":
    unittest.main()

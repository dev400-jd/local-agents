"""Tests fuer den Scrum-Master-Agenten."""

import contextlib
import io
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents.testing import structured_reply
from local_agents import scrum_master
from local_agents.profile import Context, Profile
from local_agents.scrum_master import (
    DialogChannel,
    ScrumMaster,
    all_stories,
    find_story,
    is_nothing,
    is_removal,
    mentioned_dates,
    mentioned_stories,
    next_daily,
)
from local_agents.store import Store, StoredEvent

models.ALLOW_MODEL_REQUESTS = False

EXAMPLE = Path(__file__).parent / "data" / "feature_beispiel.md"
NOW = datetime(2026, 11, 16, 15, 0)  # Montag


class FakeChannel:
    name = "test"

    def __init__(self, *answers: str | None) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []
        self.told: list[str] = []

    def ask(self, question: str) -> str | None:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else None

    def tell(self, text: str) -> None:
        self.told.append(text)


def answering(*outputs: dict, seen: list | None = None) -> FunctionModel:
    replies = list(outputs)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append((info.instructions, messages[0].parts[-1].content))
        return structured_reply(info, replies.pop(0))

    return FunctionModel(respond)


class ScrumCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "feature.md").write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        self.profile = Profile(
            state="HE",
            contexts=(Context("kunde-acme", "kunde", "Kunde ACME", stories_dir=str(folder)),),
        )
        self.stories = all_stories(self.profile)

    def agent(self, model) -> ScrumMaster:
        return ScrumMaster(self.store, self.profile, model, NOW, self.stories)


class TestHelpers(ScrumCase):
    def test_nothing_new(self) -> None:
        for answer in ("", "nein", "Nichts Neues.", "nee, im Moment nichts", "das war's, danke"):
            self.assertTrue(is_nothing(answer), answer)
        for answer in ("zu QG-1 gibt es was", "nein, aber TR-2 hakt", "ja"):
            self.assertFalse(is_nothing(answer), answer)

    def test_dates_in_the_wording(self) -> None:
        today = date(2026, 11, 16)  # Montag
        cases = {
            "fürs Daily am Dienstag": [date(2026, 11, 17)],
            "morgen ansprechen": [date(2026, 11, 17)],
            "übermorgen": [date(2026, 11, 18)],
            "am Montag": [date(2026, 11, 23)],
            "am 20.11. und am 3.12.2026": [date(2026, 11, 20), date(2026, 12, 3)],
            "am 20. besprechen": [date(2026, 11, 20)],
            "am 5. besprechen": [date(2026, 12, 5)],
            "Version 2. steht": [],
            "nichts mit Datum, 3 Punkte": [],
        }
        for text, expected in cases.items():
            self.assertEqual(mentioned_dates(text, today), expected, text)

    def test_find_story_by_key_name_or_unique_part(self) -> None:
        stories = [story for _, story in self.stories]
        self.assertEqual(find_story("qg-1", stories), 0)
        self.assertEqual(find_story("TR-2: Bericht bereitstellen", stories), 1)
        self.assertEqual(find_story("Bericht", stories), 1)
        self.assertIsNone(find_story("e", stories))  # mehrdeutig
        self.assertIsNone(find_story("XX-9", stories))
        self.assertEqual(mentioned_stories("zu qg-1 und TR-2, nicht TR-22", stories), [0, 1])

    def test_stories_come_from_the_profile_folders(self) -> None:
        self.assertEqual(
            [(context, story.key) for context, story in self.stories],
            [("kunde-acme", "QG-1"), ("kunde-acme", "TR-2")],
        )

    def test_next_daily_from_planned_meetings_else_next_workday(self) -> None:
        self.assertEqual(next_daily(self.store, NOW), date(2026, 11, 17))
        friday = datetime(2026, 11, 20, 15, 0)
        self.assertEqual(next_daily(self.store, friday), date(2026, 11, 23))
        # Christi Himmelfahrt (Do 14.05.2026) ist kein Werktag.
        self.assertEqual(next_daily(self.store, datetime(2026, 5, 13, 9), state="HE"), date(2026, 5, 15))
        for event_id, title, day, context in (
            ("a", "Team - Daily", 16, "kunde-acme"),  # heute, schon vorbei
            ("b", "Jour fixe", 17, "kunde-acme"),
            ("c", "Anderes Daily", 18, "intern"),
            ("d", "Team - Daily", 19, "kunde-acme"),
        ):
            start = datetime(2026, 11, day, 9, 0)
            self.store.upsert_event(StoredEvent(event_id, title, start, start.replace(minute=15), context))
        self.assertEqual(next_daily(self.store, NOW, "kunde-acme"), date(2026, 11, 19))
        self.assertEqual(next_daily(self.store, NOW), date(2026, 11, 18))


class TestCheckIn(ScrumCase):
    def test_remark_is_stored_with_story_date_and_wording(self) -> None:
        seen: list = []
        model = answering(
            {"remarks": [{"story": "QG-1", "text": "Abstimmung  steht noch aus", "daily": "2026-11-18"}]},
            seen=seen,
        )
        channel = FakeChannel("zu QG-1: Abstimmung steht noch aus, fürs Daily am Mittwoch", "nein danke")
        (note,) = self.agent(model).check_in(channel)
        self.assertEqual(
            (note.story, note.feature, note.text, note.daily_date, note.status, note.source),
            (
                "QG-1: Prüfregeln für `bestellung_roh`",
                "Bestellimport mit Qualitätsprüfung",
                "Abstimmung steht noch aus",
                date(2026, 11, 18),
                "offen",
                "test",
            ),
        )
        self.assertEqual(note.raw, "zu QG-1: Abstimmung steht noch aus, fürs Daily am Mittwoch")
        self.assertEqual(self.store.story_notes(), [note])
        self.assertEqual(channel.questions, ["Gibt es etwas Neues zu einer User Story?", "Noch etwas zu einer anderen Story?"])
        self.assertEqual(
            channel.told,
            ["Vermerkt:\n- #1 QG-1: Prüfregeln für `bestellung_roh`: Abstimmung steht noch aus [Daily Mi 18.11.2026]"],
        )
        instructions, prompt = seen[0]
        self.assertIn("- QG-1: Prüfregeln für `bestellung_roh` (Feature: Bestellimport mit Qualitätsprüfung)", instructions)
        self.assertIn("Heute ist Mo 2026-11-16.", instructions)
        self.assertEqual(prompt, channel_answer := "zu QG-1: Abstimmung steht noch aus, fürs Daily am Mittwoch")
        (logged,) = self.store.runs()
        self.assertEqual((logged["agent"], logged["summary"]), ("scrum_master", "1 Bemerkungen über test"))
        del channel_answer

    def test_note_without_daily_keeps_its_dates_in_the_text(self) -> None:
        # Der gescheiterte erste Handtest: zwei Stories, ein Datum als Inhalt, kein Daily.
        answer = "schreib bitte die Bemerkung rein, dass QG-1 und TR-2 am 30.10.2026 abgeschlossen wurden"
        model = answering(
            {"remarks": [{"story": "QG-1", "text": "QG-1 abgeschlossen"}, {"story": "TR-2", "text": "TR-2 abgeschlossen"}]},
            {"remarks": [
                {"story": "QG-1", "text": "QG-1 wurde am 30.10.2026 abgeschlossen", "daily": "2026-11-17"},
                {"story": "TR-2", "text": "TR-2 wurde am 30.10.2026 abgeschlossen"},
            ]},
        )
        channel = FakeChannel(answer)
        first, second = self.agent(model).check_in(channel)
        self.assertEqual((first.daily_date, second.daily_date), (None, None))
        self.assertEqual(first.text, "QG-1 wurde am 30.10.2026 abgeschlossen")
        self.assertEqual(second.story, "TR-2: Bericht bereitstellen")
        self.assertEqual(
            channel.told[0].splitlines()[1],
            "- #1 QG-1: Prüfregeln für `bestellung_roh`: QG-1 wurde am 30.10.2026 abgeschlossen",
        )

    def test_daily_cue_without_date_means_the_next_daily(self) -> None:
        model = answering({"remarks": [{"story": "QG-1", "text": "A"}, {"story": "Bericht bereitstellen", "text": "B", "daily": "2026-11-01"}]})
        notes = self.agent(model).check_in(FakeChannel("das eine A und das andere B, bitte im Daily ansprechen"))
        self.assertEqual([note.daily_date for note in notes], [date(2026, 11, 17)] * 2)
        self.assertEqual([note.story.split(":")[0] for note in notes], ["QG-1", "TR-2"])

    def test_model_failure_keeps_one_note_per_mentioned_key(self) -> None:
        bad = {"remarks": [{"story": "XX-1", "text": "x"}]}
        notes = self.agent(answering(bad, bad, bad)).check_in(FakeChannel("QG-1 und TR-2 sind durch"))
        self.assertEqual([(note.story[:4], note.text) for note in notes], [("QG-1", "QG-1 und TR-2 sind durch"), ("TR-2", "QG-1 und TR-2 sind durch")])

    def test_remove_the_last_notes_or_a_numbered_one(self) -> None:
        model = answering(
            {"remarks": [{"story": "QG-1", "text": "A"}, {"story": "TR-2", "text": "B"}]},
            {"remarks": [{"story": "QG-1", "text": "C"}]},
        )
        channel = FakeChannel("QG-1 A, TR-2 B", "Bitte entferne diesen Vermerk wieder", "QG-1 C", "lösch Bemerkung 3", "streich das")
        saved = self.agent(model).check_in(channel)
        self.assertEqual(channel.told[1], "Entfernt: #1, #2")
        self.assertEqual(channel.told[3], "Entfernt: #3")
        self.assertEqual(channel.told[4], "Es gibt keinen Vermerk, den ich entfernen könnte.")
        self.assertEqual(saved, [])
        self.assertEqual(self.store.story_notes(("offen",)), [])
        self.assertEqual(len(self.store.story_notes(("entfernt",))), 3)

    def test_removal_wording(self) -> None:
        for answer in ("Bitte entferne diesen Vermerk wieder", "lösch #2", "das bitte streichen", "mach das rückgängig", "nimm den letzten wieder raus"):
            self.assertTrue(is_removal(answer), answer)
        for answer in ("QG-1 ist fertig", "der Zugang fehlt noch"):
            self.assertFalse(is_removal(answer), answer)

    def test_unmatched_or_failed_answers_keep_the_wording(self) -> None:
        model = answering({"remarks": [{"story": None, "text": "irgendwas hakt"}]}, {"remarks": []})
        channel = FakeChannel("irgendwas hakt", "noch was Unklares")
        first, second = self.agent(model).check_in(channel)
        self.assertEqual((first.story, first.text), ("", "irgendwas hakt"))
        self.assertEqual((second.story, second.text, second.raw), ("", "noch was Unklares", "noch was Unklares"))
        self.assertIn("ohne Story (nicht zugeordnet): irgendwas hakt", channel.told[0])

    def test_key_and_weekday_in_the_wording_beat_the_model(self) -> None:
        # Das Modell irrt bei Story und Datum; beides steht woertlich in der Antwort.
        model = answering({"remarks": [{"story": "QG-1", "text": "bei TR-2 fehlt der Zugang", "daily": "2026-11-17"}]})
        (note,) = self.agent(model).check_in(FakeChannel("bei TR-2 fehlt der Zugang, bitte am Mittwoch ansprechen"))
        self.assertEqual((note.story, note.daily_date), ("TR-2: Bericht bereitstellen", date(2026, 11, 18)))

    def test_single_key_does_not_capture_a_second_remark(self) -> None:
        model = answering({"remarks": [{"story": "QG-1", "text": "die Prüfregeln warten"}, {"story": "TR-2", "text": "TR-2 ist fertig"}]})
        first, second = self.agent(model).check_in(FakeChannel("die Prüfregeln warten, und TR-2 ist fertig"))
        self.assertEqual((first.story[:4], second.story[:4]), ("QG-1", "TR-2"))

    def test_model_date_counts_only_if_the_wording_names_it(self) -> None:
        model = answering({"remarks": [{"story": "QG-1", "text": "A", "daily": "2026-11-19"}, {"story": "TR-2", "text": "B", "daily": "2026-11-25"}]})
        first, second = self.agent(model).check_in(FakeChannel("fürs Daily: QG-1 A am Donnerstag, TR-2 B am Freitag"))
        self.assertEqual((first.daily_date, second.daily_date), (date(2026, 11, 19), date(2026, 11, 17)))

    def test_unknown_story_number_is_sent_back(self) -> None:
        model = answering({"remarks": [{"story": "XX-7", "text": "x"}]}, {"remarks": [{"story": "tr-2", "text": "x"}]})
        (note,) = self.agent(model).check_in(FakeChannel("der Bericht x"))
        self.assertEqual(note.story, "TR-2: Bericht bereitstellen")

    def test_nothing_new_or_no_answer_stores_nothing(self) -> None:
        self.assertEqual(self.agent(None).check_in(FakeChannel("nichts Neues")), [])
        self.assertEqual(self.agent(None).check_in(FakeChannel(None)), [])
        self.assertEqual(self.store.story_notes(), [])
        self.assertEqual(len(self.store.runs()), 2)

    def test_rounds_are_limited(self) -> None:
        outputs = [{"remarks": [{"story": "QG-1", "text": f"Punkt {n}"}]} for n in range(9)]
        notes = self.agent(answering(*outputs)).check_in(FakeChannel(*[f"QG-1 Punkt {n}" for n in range(9)]))
        self.assertEqual(len(notes), 5)


class TestNotesCommand(ScrumCase):
    def call(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = scrum_master.main(["scrum_master", *args], store=self.store)
        return code, out.getvalue()

    def test_list_and_tick_off(self) -> None:
        self.assertEqual(self.call("notizen"), (0, "Keine Bemerkungen.\n"))
        self.store.add_story_note("QG-1: Regeln", "F", "Abstimmung offen", date(2026, 11, 17))
        self.store.add_story_note("", "", "Allgemeines", date(2026, 11, 18))
        self.store.add_story_note("TR-2: Bericht", "F", "am 30.10. abgeschlossen")
        self.assertEqual(
            self.call("notizen")[1],
            "Vermerke ohne Daily-Bezug\n  #3 TR-2: Bericht: am 30.10. abgeschlossen\n"
            "Daily Di 17.11.2026\n  #1 QG-1: Regeln: Abstimmung offen\n"
            "Daily Mi 18.11.2026\n  #2 ohne Story (nicht zugeordnet): Allgemeines\n",
        )
        self.assertEqual(self.call("entfernt", "3"), (0, "Bemerkung #3: entfernt.\n"))
        self.assertNotIn("#3", self.call("notizen")[1])
        self.assertEqual(self.call("offen", "3")[0], 0)
        self.assertIn("#3", self.call("notizen")[1])
        self.assertEqual(self.call("besprochen", "1"), (0, "Bemerkung #1: besprochen.\n"))
        self.assertNotIn("#1", self.call("notizen")[1])
        self.assertIn("#1 QG-1: Regeln: Abstimmung offen [besprochen]", self.call("notizen", "alle")[1])
        self.assertEqual(self.call("besprochen", "9")[0], 1)
        self.assertEqual(self.call("unsinn")[0], 2)
        self.assertEqual(self.store.story_notes(daily_date=date(2026, 11, 18))[0].text, "Allgemeines")


class TestDialogChannel(unittest.TestCase):
    def test_question_goes_to_osascript_as_argument(self) -> None:
        calls = []

        def runner(command: list[str]) -> tuple[int, str]:
            calls.append(command)
            return 0, "zu QG-1 gibt es was\n"

        channel = DialogChannel(runner)
        self.assertEqual(channel.ask('Frage mit "Anführung"'), "zu QG-1 gibt es was")
        self.assertEqual((calls[0][0], calls[0][-1]), ("osascript", 'Frage mit "Anführung"'))
        self.assertIn("display dialog (item 1 of argv)", " ".join(calls[0]))

    def test_cancel_or_missing_osascript_means_no_answer(self) -> None:
        self.assertIsNone(DialogChannel(lambda command: (1, "")).ask("Frage"))

        def missing(command: list[str]) -> tuple[int, str]:
            raise FileNotFoundError("osascript")

        channel = DialogChannel(missing)
        self.assertIsNone(channel.ask("Frage"))
        channel.tell("Hinweis")  # darf nicht scheitern


if __name__ == "__main__":
    unittest.main()

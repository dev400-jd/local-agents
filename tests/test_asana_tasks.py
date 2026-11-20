"""Tests fuer die Abfragen auf dem Asana-Spiegel: Zeitangaben, Filter, Ausgabe, Agent."""

import os
import unittest
from datetime import date, datetime, timezone

os.environ["LOCAL_AGENTS_ASANA_DB"] = "/gibt/es/nicht/asana.db"  # nie den echten Spiegel lesen

from pydantic_ai import models  # noqa: E402
from pydantic_ai.messages import ModelMessage, ModelResponse  # noqa: E402
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402

from local_agents import asana_tasks as at  # noqa: E402
from local_agents.asana_mirror import Comment, Mirror, MirroredTask  # noqa: E402
from local_agents.asana_tasks import NotUnderstood, Period, TaskFilter, period  # noqa: E402
from local_agents.testing import structured_reply  # noqa: E402

models.ALLOW_MODEL_REQUESTS = False

TODAY = date(2026, 11, 20)  # ein Freitag
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def task(name: str, due: str = "", mine: bool = True, **extra) -> MirroredTask:
    defaults = dict(
        assignee="Alex Beispiel" if mine else "Erika Muster",
        mine=mine,
        followed=not mine,
        projects=(("Datenplattform", "In Arbeit"),),
        url=f"https://app.asana.com/0/0/{abs(hash(name)) % 1000}",
        modified_at="2026-11-10T08:00:00.000Z",
    )
    return MirroredTask(name, name, due=due, **{**defaults, **extra})


TASKS = [
    task("Angebot prüfen", "2026-11-24"),
    task("Folien bauen", "2026-11-18"),
    task("Vertrag ablegen", "2026-11-12", completed=True),
    task("Ohne Termin"),
    task("Schnittstelle abstimmen", "2026-11-27", mine=False, projects=(("Vertrieb Nord", ""),)),
    task(
        "Review vorbereiten",
        "2026-12-02T09:00:00.000Z",
        mine=False,
        modified_at="2026-11-19T15:00:00.000Z",
        comments=(Comment("Erika Muster", datetime(2026, 11, 19, 15, 0, tzinfo=timezone.utc), "Bitte bis Mittwoch die Zahlen liefern."),),
    ),
]


def names(result: at.QueryResult) -> list[str]:
    return [found.name for found in result.tasks]


class TestPeriod(unittest.TestCase):
    def test_due_periods(self) -> None:
        cases = {
            "heute": (TODAY, TODAY),
            "morgen": (date(2026, 11, 21), date(2026, 11, 21)),
            "diese Woche": (date(2026, 11, 16), date(2026, 11, 22)),
            "nächste Woche": (date(2026, 11, 23), date(2026, 11, 29)),
            "in der kommenden Woche": (date(2026, 11, 23), date(2026, 11, 29)),
            "diesen Monat": (date(2026, 11, 1), date(2026, 11, 30)),
            "nächsten Monat": (date(2026, 12, 1), date(2026, 12, 31)),
            "bis Dienstag": (None, date(2026, 11, 24)),
            "bis zum 30.11.": (None, date(2026, 11, 30)),
            "ab Montag": (date(2026, 11, 23), None),
            "am Mittwoch": (date(2026, 11, 25), date(2026, 11, 25)),
            "überfällig": (None, date(2026, 11, 19)),
            "nächsten 3 Tage": (TODAY, date(2026, 11, 23)),
        }
        for words, expected in cases.items():
            found = period(words, TODAY)
            self.assertEqual((found.start, found.end), expected, words)

    def test_changes_look_back(self) -> None:
        cases = {
            "seit Montag": (date(2026, 11, 16), None),
            "seit Freitag": (TODAY, None),
            "seit gestern": (date(2026, 11, 19), None),
            "seit dem 1.11.": (date(2026, 11, 1), None),
            "seit 15.12.": (date(2025, 12, 15), None),
            "am Mittwoch": (date(2026, 11, 18), date(2026, 11, 18)),
            "letzte Woche": (date(2026, 11, 9), date(2026, 11, 15)),
            "letzten 3 Tage": (date(2026, 11, 17), TODAY),
            "letzte 2 Wochen": (date(2026, 11, 6), TODAY),
            "heute": (TODAY, TODAY),
        }
        for words, expected in cases.items():
            found = period(words, TODAY, past=True)
            self.assertEqual((found.start, found.end), expected, words)

    def test_labels_empty_and_unknown(self) -> None:
        self.assertIsNone(period("  ", TODAY))
        self.assertEqual(period("diese Woche", TODAY), Period(date(2026, 11, 16), date(2026, 11, 22), "16.11.–22.11.2026"))
        self.assertEqual(period("bis Dienstag", TODAY).label, "bis Di 24.11.")
        with self.assertRaises(NotUnderstood):
            period("demnächst irgendwann", TODAY)


class TestQuery(unittest.TestCase):
    def test_default_is_everything_open_sorted_by_due_date(self) -> None:
        result = at.run(TaskFilter(), TASKS, TODAY)
        self.assertEqual(
            names(result),
            ["Folien bauen", "Angebot prüfen", "Schnittstelle abstimmen", "Review vorbereiten", "Ohne Termin"],
        )
        self.assertEqual(result.description, "offene Aufgaben")

    def test_mine_due_and_overdue(self) -> None:
        week = at.run(TaskFilter(wem="mir", faellig="nächste Woche"), TASKS, TODAY)
        self.assertEqual((names(week), week.description), (["Angebot prüfen"], "offene Aufgaben, dir zugewiesen, fällig 23.11.–29.11.2026"))
        late = at.run(TaskFilter(faellig="überfällig"), TASKS, TODAY)
        self.assertEqual((names(late), late.description), (["Folien bauen"], "offene Aufgaben, überfällig"))
        self.assertEqual(names(at.run(TaskFilter(stand="erledigt"), TASKS, TODAY)), ["Vertrag ablegen"])
        self.assertEqual(len(at.run(TaskFilter(stand="egal"), TASKS, TODAY).tasks), 6)

    def test_waiting_for_others_person_and_words(self) -> None:
        others = at.run(TaskFilter(wem="anderen"), TASKS, TODAY)
        self.assertEqual(names(others), ["Schnittstelle abstimmen", "Review vorbereiten"])
        self.assertEqual(names(at.run(TaskFilter(person="erika"), TASKS, TODAY)), ["Schnittstelle abstimmen", "Review vorbereiten"])
        self.assertEqual(names(at.run(TaskFilter(stichworte="Angebote"), TASKS, TODAY)), [])
        self.assertEqual(names(at.run(TaskFilter(stichworte="Angebot prüf"), TASKS, TODAY)), ["Angebot prüfen"])

    def test_project_is_resolved_or_the_choices_are_named(self) -> None:
        self.assertEqual(names(at.run(TaskFilter(projekt="vertrieb"), TASKS, TODAY)), ["Schnittstelle abstimmen"])
        self.assertEqual(at.resolve_project("Projekt Datenplattform", ["Datenplattform", "Vertrieb Nord"]), "Datenplattform")
        with self.assertRaises(NotUnderstood) as missing:
            at.run(TaskFilter(projekt="Marketing"), TASKS, TODAY)
        self.assertEqual(str(missing.exception), "Das Projekt „Marketing“ gibt es im Spiegel nicht. Zur Wahl: Datenplattform; Vertrieb Nord.")
        with self.assertRaises(NotUnderstood) as double:
            at.resolve_project("Vertrieb", ["Vertrieb Nord", "Vertrieb Süd", "Einkauf"])
        self.assertIn("passt auf mehrere Projekte. Zur Wahl: Vertrieb Nord; Vertrieb Süd.", str(double.exception))

    def test_changes_are_sorted_newest_first_and_count_comments(self) -> None:
        result = at.run(TaskFilter(stand="egal", geaendert="seit Montag"), TASKS, TODAY)
        self.assertEqual((names(result), result.changed), (["Review vorbereiten"], True))
        self.assertEqual(result.description, "alle Aufgaben, geändert seit Mo 16.11.")
        # Ein Kommentar zaehlt als Aenderung, auch wenn das Aenderungsdatum der Aufgabe aelter ist.
        old = task("Alt", modified_at="2026-10-01T08:00:00.000Z", comments=(Comment("Kim", datetime(2026, 11, 19, 9, tzinfo=timezone.utc), "Neu."),))
        self.assertEqual(at.modified_day(old), date(2026, 11, 19))


class TestOutput(unittest.TestCase):
    def test_lines_name_due_date_project_and_owner(self) -> None:
        self.assertEqual(
            at.task_lines(TASKS[1], TODAY)[0],
            "- Folien bauen · fällig Mi 18.11., überfällig seit 2 Tagen · Datenplattform / In Arbeit",
        )
        lines = at.task_lines(TASKS[5], TODAY, changed=True)
        self.assertEqual(lines[0], "- Review vorbereiten · fällig Mi 02.12. · Datenplattform / In Arbeit · zuständig: Erika Muster")
        self.assertEqual(lines[1], "  letzter Kommentar 19.11. Erika Muster: Bitte bis Mittwoch die Zahlen liefern.")
        self.assertTrue(lines[2].startswith("  https://app.asana.com/"))
        self.assertEqual(at.task_lines(TASKS[2], TODAY)[0], "- Vertrag ablegen (erledigt) · fällig Do 12.11. · Datenplattform / In Arbeit")

    def test_result_has_heading_list_and_age_of_the_mirror(self) -> None:
        result = at.run(TaskFilter(wem="mir", faellig="überfällig"), TASKS, TODAY)
        lines = at.result_lines(result, TODAY, NOW, NOW)
        self.assertEqual(lines[0], "1 Aufgabe (offene Aufgaben, dir zugewiesen, überfällig).")
        self.assertEqual(lines[-1], "Stand: Asana-Spiegel, vor 0 Min abgeglichen.")
        count = at.run(TaskFilter(nur_anzahl=True), TASKS, TODAY)
        stale = at.result_lines(count, TODAY, datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc), NOW)
        self.assertEqual(
            stale,
            [
                "5 Aufgaben (offene Aufgaben).",
                "Stand: Asana-Spiegel, vor 2 Tagen abgeglichen. Auffrischen: python -m local_agents.asana_mirror",
            ],
        )

    def test_long_lists_are_cut(self) -> None:
        many = [task(f"Aufgabe {number:02}") for number in range(45)]
        lines = at.result_lines(at.run(TaskFilter(), many, TODAY), TODAY, NOW, NOW)
        self.assertEqual((lines[0], lines[-2]), ("45 Aufgaben (offene Aufgaben).", "… und 5 weitere."))


def filtering(*filters: dict, seen: list | None = None) -> FunctionModel:
    replies = list(filters)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if seen is not None:
            seen.append(info.instructions)
        return structured_reply(info, replies.pop(0) if len(replies) > 1 else replies[0])

    return FunctionModel(respond)


class TestAgent(unittest.TestCase):
    def setUp(self) -> None:
        self.mirror = Mirror(":memory:")
        self.addCleanup(self.mirror.close)
        self.mirror.tasks = lambda: TASKS  # der Bestand des Spiegels, ohne den Umweg ueber die API
        self.mirror.set("last_sync", NOW.isoformat())

    def test_question_becomes_a_filter_and_the_code_writes_the_list(self) -> None:
        seen: list = []
        text = at.answer("Was ist bei mir überfällig?", self.mirror, filtering({"wem": "mir", "faellig": "überfällig"}, seen=seen), NOW)
        self.assertEqual(
            text.splitlines()[:2],
            [
                "1 Aufgabe (offene Aufgaben, dir zugewiesen, überfällig).",
                "- Folien bauen · fällig Mi 18.11., überfällig seit 2 Tagen · Datenplattform / In Arbeit",
            ],
        )
        self.assertIn("nimm den Namen von dort: Datenplattform; Vertrieb Nord", seen[0])
        self.assertNotIn("Folien", seen[0])  # das Modell sieht die Aufgaben nicht

    def test_unresolvable_filter_goes_back_to_the_model(self) -> None:
        model = filtering({"faellig": "irgendwann"}, {"projekt": "Vertrieb Nord", "faellig": "nächste Woche"})
        text = at.answer("Was ist im Vertrieb demnächst fällig?", self.mirror, model, NOW)
        self.assertEqual(text.splitlines()[0], "1 Aufgabe (offene Aufgaben, Projekt „Vertrieb Nord“, fällig 23.11.–29.11.2026).")
        stubborn = at.answer("Was ist irgendwann fällig?", self.mirror, filtering({"faellig": "irgendwann"}), NOW)
        self.assertTrue(stubborn.startswith("Die Frage konnte ich nicht in einen Filter übersetzen."))

    def test_code_adds_what_the_question_says_literally(self) -> None:
        mine = at.adjusted("Was muss ich bis Dienstag erledigen?", TaskFilter(faellig="bis Dienstag"))
        self.assertEqual((mine.wem, mine.faellig), ("mir", "bis Dienstag"))
        self.assertEqual(at.adjusted("Was ist bei mir fällig?", TaskFilter()).wem, "mir")
        # Nicht bei "ich warte auf andere", bei einer genannten Person oder wenn das Modell schon entschieden hat.
        self.assertEqual(at.adjusted("Wo warte ich auf andere?", TaskFilter(wem="anderen")).wem, "anderen")
        self.assertEqual(at.adjusted("Was soll ich Erika abnehmen?", TaskFilter(person="Erika")).wem, "egal")
        self.assertEqual(at.adjusted("Welche Aufgaben sind offen?", TaskFilter()).wem, "egal")
        named = at.adjusted("Was ist im Projekt Marketing offen?", TaskFilter())
        self.assertEqual(named.projekt, "Marketing")
        self.assertEqual(at.adjusted("Was ist im Projekt Vertrieb Nord offen?", TaskFilter()).projekt, "Vertrieb Nord")
        self.assertEqual(at.adjusted("Was ist im Projekt Marketing offen?", TaskFilter(projekt="Vertrieb Nord")).projekt, "Vertrieb Nord")
        text = at.answer("Was ist im Projekt Marketing offen?", self.mirror, filtering({}), NOW)
        self.assertEqual(text, "Das Projekt „Marketing“ gibt es im Spiegel nicht. Zur Wahl: Datenplattform; Vertrieb Nord.")

    def test_unknown_project_is_reported_instead_of_dropped(self) -> None:
        text = at.answer("Was ist im Marketing offen?", self.mirror, filtering({"projekt": "Marketing"}), NOW)
        self.assertEqual(text, "Das Projekt „Marketing“ gibt es im Spiegel nicht. Zur Wahl: Datenplattform; Vertrieb Nord.")

    def test_conversation_hands_list_questions_to_the_mirror(self) -> None:
        from local_agents import ask
        from local_agents.search import Index

        index = Index(":memory:")
        self.addCleanup(index.close)
        talk = ask.Conversation(index, filtering({"wem": "mir", "faellig": "überfällig"}), mirror=self.mirror, now=lambda: NOW)
        answer = talk.ask("Welche Aufgaben sind bei mir überfällig?")
        self.assertEqual((answer.source, answer.selection.how), (ask.TASK_LIST, "Abfrage im Asana-Spiegel"))
        self.assertEqual(ask.answer_lines(answer).splitlines()[0], "1 Aufgabe (offene Aufgaben, dir zugewiesen, überfällig).")
        self.assertEqual(talk.history, [("Welche Aufgaben sind bei mir überfällig?", "1 Aufgabe (offene Aufgaben, dir zugewiesen, überfällig).")])

    def test_empty_mirror(self) -> None:
        self.mirror.tasks = lambda: []
        self.assertTrue(at.answer("Was ist fällig?", self.mirror, None, NOW).startswith("Der Asana-Spiegel ist leer."))


if __name__ == "__main__":
    unittest.main()

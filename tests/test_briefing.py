"""Tests fuer die gesprochene Kurzfassung und die Sprachausgabe."""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from local_agents.briefing import spoken_summary
from local_agents.store import StoredEvent, Task
from local_agents.voice import speak, speakable

TODAY = date(2026, 11, 15)
EVENTS = [
    StoredEvent("d", "Daily", datetime(2026, 11, 16, 9), datetime(2026, 11, 16, 9, 30), "kunde-acme"),
    StoredEvent("j", "Jour fixe", datetime(2026, 11, 18, 10), datetime(2026, 11, 18, 11), "intern", organizer="Erika Muster"),
]


def task(number: int, event: str, kind: str, title: str, due: int, context: str = "intern") -> Task:
    return Task(number, event, kind, title, context, date(2026, 11, due), "offen", "", "")


TASKS = [
    task(1, "d", "vorbereitung", "Vorbereitung: Daily (Mo 16.11. 09:00)", 15, "kunde-acme"),
    task(2, "j", "verschieben", "Nachricht an Erika Muster: Jour fixe (Mi 18.11. 10:00) verschieben, weil Kundentag Kunde ACME", 15),
    task(3, "j", "vorbereitung", "Vorbereitung: Jour fixe (Mi 18.11. 10:00)", 17),
]


class TestSpokenSummary(unittest.TestCase):
    def test_overview_names_messages_and_counts_the_rest(self) -> None:
        self.assertEqual(
            spoken_summary(TASKS, EVENTS, TODAY),
            "Für heute ist eine Nachricht eingeplant, an Erika Muster, damit der interne Termin "
            "am Mittwoch verschoben wird. Hinzu kommt eine Vorbereitung für den Termin am Montag. "
            "In den Tagen danach folgt eine weitere Aufgabe. "
            "Der vollste Tag ist Montag mit einem geplanten Termin. Passt das so für dich?",
        )

    def test_plural_declines_own_meeting_and_many_recipients(self) -> None:
        tasks = [
            task(n, "j", "verschieben", f"Nachricht an {name}: Jour fixe verschieben, weil Kundentag", 15)
            for n, name in enumerate(["A", "B", "C", "Organisator", "A"])
        ] + [
            task(7, "j", "verschieben", "Absage an Erika Muster: Jour fixe, weil Kundentag (große Runde)", 14),
            task(8, "d", "verschieben", "Selbst verschieben: Daily (Mo 16.11. 09:00), weil Kundentag", 15),
            task(9, "d", "vorbereitung", "Vorbereitung: Daily", 16),
            task(10, "j", "vorbereitung", "Vorbereitung: Jour fixe", 17),
            task(11, "j", "vorbereitung", "Vorbereitung: Jour fixe", 17),
        ]
        self.assertEqual(
            spoken_summary(tasks, EVENTS, TODAY),
            "Für heute sind fünf Nachrichten eingeplant, an A, B und zwei weitere, damit die "
            "internen Termine am Mittwoch verschoben werden. Dazu kommt eine Absage an Erika Muster "
            "für die große Runde am Mittwoch. Außerdem ist eingeplant, dass dein eigener Termin "
            "„Daily“ am Montag verschoben wird. Morgen folgt eine weitere Aufgabe, danach noch zwei. "
            "Der vollste Tag ist Montag mit einem geplanten Termin. Passt das so für dich?",
        )

    def test_only_preparations_or_nothing_today(self) -> None:
        only = [TASKS[0], task(4, "j", "vorbereitung", "Vorbereitung: Jour fixe", 15)]
        self.assertTrue(
            spoken_summary(only, EVENTS, TODAY).startswith(
                "Für heute sind zwei Vorbereitungen für die Termine am Montag und Mittwoch eingeplant. Der vollste"
            )
        )
        self.assertTrue(
            spoken_summary([TASKS[2]], EVENTS, TODAY).startswith(
                "Für heute ist nichts eingeplant. In den Tagen danach folgt eine weitere Aufgabe."
            )
        )

    def test_no_open_tasks(self) -> None:
        self.assertEqual(spoken_summary([], [], TODAY), "Es sind keine Aufgaben offen.")


class TestVoice(unittest.TestCase):
    def test_speakable_strips_markdown(self) -> None:
        self.assertEqual(speakable("**Plan**\n- eins\n- `zwei`  #3"), "Plan eins zwei 3")

    def test_speaks_with_voice_and_text_on_stdin(self) -> None:
        calls = []
        spoke = speak("Hallo *du*", "Anna", runner=lambda cmd, text: calls.append((cmd, text)) or 0)
        self.assertEqual(spoke, "say")
        self.assertEqual(calls, [(["say", "-v", "Anna"], "Hallo du")])

    def piper_folder(self) -> Path:
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "de_DE-test-high.onnx").write_bytes(b"x")
        self.enterContext(mock.patch("importlib.util.find_spec", return_value=object()))
        return folder

    def test_piper_synthesizes_then_plays(self) -> None:
        folder = self.piper_folder()
        calls = []
        spoke = speak("Hallo", "Anna", "de_DE-test-high", lambda cmd, text: calls.append((cmd, text)) or 0, folder)
        self.assertEqual(spoke, "piper")
        (piper, text), (play, _) = calls
        self.assertEqual(piper[1:7], ["-m", "piper", "-m", "de_DE-test-high", "--data-dir", str(folder)])
        self.assertEqual((text, play[0], play[1]), ("Hallo", "afplay", piper[-1]))

    def test_speed_and_speaker_reach_piper(self) -> None:
        folder = self.piper_folder()
        calls = []
        speak("Hallo", "Anna", "de_DE-test-high", lambda cmd, text: calls.append(cmd) or 0, folder, speed=1.25, speaker=3)
        self.assertEqual(calls[0][-4:], ["--length-scale", "0.80", "--speaker", "3"])

    def test_missing_voice_is_downloaded_once(self) -> None:
        folder = self.piper_folder()
        calls = []

        def runner(cmd: list[str], text: str) -> int:
            calls.append(cmd[:4])
            if "piper.download_voices" in cmd:
                (folder / "de_DE-neu-low.onnx").write_bytes(b"x")
            return 0

        with mock.patch("sys.stderr"):
            self.assertEqual(speak("Hallo", "Anna", "de_DE-neu-low", runner, folder), "piper")
        self.assertEqual(calls[0][1:], ["-m", "piper.download_voices", "de_DE-neu-low"])
        self.assertEqual(len(calls), 3)

    def test_falls_back_to_say_if_piper_fails_or_is_missing(self) -> None:
        folder = self.piper_folder()
        spoke = speak("Hallo", "Anna", "de_DE-test-high", lambda cmd, text: 0 if cmd[0] == "say" else 1, folder)
        self.assertEqual(spoke, "say")
        calls = []
        speak("Hallo", "Anna", "kein Name", lambda cmd, text: calls.append(cmd) or 0, folder)
        self.assertEqual(calls, [["say", "-v", "Anna"]])

    def test_falls_back_to_system_voice_and_reports_failure(self) -> None:
        calls = []

        def runner(cmd: list[str], text: str) -> int:
            calls.append(cmd)
            return 1 if "-v" in cmd else 0

        self.assertEqual(speak("Hallo", "Fehlt", runner=runner), "say")
        self.assertEqual(calls, [["say", "-v", "Fehlt"], ["say"]])

        def missing(cmd: list[str], text: str) -> int:
            raise FileNotFoundError("say")

        self.assertEqual(speak("Hallo", "Anna", runner=missing), "")
        self.assertEqual(speak(" ", "Anna", runner=runner), "")


if __name__ == "__main__":
    unittest.main()

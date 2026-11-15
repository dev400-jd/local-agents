"""Tests fuer die Spracheingabe (ohne Mikrofon und ohne Whisper)."""

import contextlib
import io
import unittest

from local_agents.listen import Endpointer, ListenError, clean, listen
from local_agents.planner_agent import hear
from local_agents.profile import Profile

QUIET, LOUD = 0.002, 0.2


def blocks_until_end(levels: list[float], **settings) -> int | None:
    endpointer = Endpointer(**settings)
    for index, level in enumerate(levels, start=1):
        if endpointer.feed(level):
            return index
    return None


class TestEndpointer(unittest.TestCase):
    def test_stops_after_silence_following_speech(self) -> None:
        levels = [QUIET] * 5 + [LOUD] * 10 + [QUIET] * 30
        self.assertEqual(blocks_until_end(levels, silence=1.5), 5 + 10 + 15)

    def test_short_pause_does_not_end_the_recording(self) -> None:
        levels = [QUIET] * 3 + [LOUD] * 5 + [QUIET] * 10 + [LOUD] * 5 + [QUIET] * 15
        self.assertEqual(blocks_until_end(levels, silence=1.5), len(levels))

    def test_gives_up_if_nobody_speaks(self) -> None:
        endpointer = Endpointer(wait=2.0)
        ended = [endpointer.feed(QUIET) for _ in range(20)]
        self.assertEqual((ended.index(True), endpointer.spoke), (19, False))

    def test_single_click_is_not_speech(self) -> None:
        endpointer = Endpointer(wait=1.0)
        for level in [QUIET] * 3 + [LOUD] + [QUIET] * 6:
            endpointer.feed(level)
        self.assertFalse(endpointer.spoke)

    def test_threshold_follows_room_noise(self) -> None:
        noisy = Endpointer()
        for _ in range(3):
            noisy.feed(0.05)
        self.assertAlmostEqual(noisy.threshold, 0.15)
        self.assertEqual(Endpointer().threshold, 0.01)

    def test_hard_limit(self) -> None:
        self.assertEqual(blocks_until_end([QUIET] * 3 + [LOUD] * 100, limit=5.0), 50)


class TestListen(unittest.TestCase):
    def test_transcribes_the_recording(self) -> None:
        calls = []

        def transcriber(audio, model, language):
            calls.append((audio, model, language))
            return "ja passt"

        self.assertEqual(listen("small", "de", lambda: "AUDIO", transcriber), "ja passt")
        self.assertEqual(calls, [("AUDIO", "small", "de")])

    def test_silence_is_not_transcribed(self) -> None:
        self.assertEqual(listen(recorder=lambda: None, transcriber=self.fail), "")

    def test_clean_joins_segments(self) -> None:
        self.assertEqual(clean([" Ja,  passt. ", " ", "Danke."]), "Ja, passt. Danke.")


class TestHear(unittest.TestCase):
    def hear(self, listener) -> tuple[str, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            answer = hear(Profile(listen_model="small"), listener)
        return answer, out.getvalue()

    def test_shows_what_was_understood(self) -> None:
        answer, text = self.hear(lambda model, language: f"Daily raus ({model}, {language})")
        self.assertEqual(answer, "Daily raus (small, de)")
        self.assertIn("Ich höre zu", text)
        self.assertIn("Verstanden: Daily raus (small, de)\n", text)

    def test_nothing_heard_or_broken_microphone_leads_to_typing(self) -> None:
        self.assertEqual(self.hear(lambda model, language: ""), ("", "\nIch höre zu … (Stille beendet die Aufnahme)\nIch habe nichts gehört.\n"))

        def broken(model: str, language: str) -> str:
            raise ListenError("kein Mikrofon")

        answer, text = self.hear(broken)
        self.assertEqual(answer, "")
        self.assertIn("(Spracheingabe nicht möglich: kein Mikrofon)", text)


if __name__ == "__main__":
    unittest.main()

"""Tests fuer den Telegram-Kanal (ohne Netz: die Bot-API ist nachgestellt)."""

import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents.testing import structured_reply
from local_agents import telegram_bot
from local_agents.profile import Context, Profile, ProfileError, load_profile
from local_agents.store import Store
from local_agents.telegram_bot import TelegramApi, TelegramService, save_chat_id, setup

models.ALLOW_MODEL_REQUESTS = False

EXAMPLE = Path(__file__).parent / "data" / "feature_beispiel.md"
ME = 4711


class FakeTelegram:
    """Stellt die Bot-API nach: geplante Updates rein, gesendete Nachrichten raus."""

    def __init__(self, *batches: list[tuple[int, str]]) -> None:
        self.batches = [list(batch) for batch in batches]
        self.sent: list[tuple[int, str]] = []
        self.calls: list[tuple[str, dict]] = []
        self.next_id = 100

    def __call__(self, method: str, params: dict) -> object:
        self.calls.append((method, dict(params)))
        if method == "getMe":
            return {"username": "test_bot"}
        if method == "sendMessage":
            self.sent.append((params["chat_id"], params["text"]))
            return {}
        updates = []
        for chat_id, text in self.batches.pop(0) if self.batches else []:
            updates.append({"update_id": self.next_id, "message": {"chat": {"id": chat_id}, "text": text}})
            self.next_id += 1
        return updates


def answering(*outputs: dict) -> FunctionModel:
    replies = list(outputs)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return structured_reply(info, replies.pop(0))

    return FunctionModel(respond)


class TelegramCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "feature.md").write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        self.profile = Profile(
            state="HE",
            contexts=(Context("kunde-acme", "kunde", "Kunde ACME", stories_dir=str(folder)),),
            telegram_enabled=True,
            telegram_chat_id=ME,
            telegram_ask_at=("09:00", "16:30"),
        )

    def service(self, telegram: FakeTelegram, model=None) -> TelegramService:
        return TelegramService(TelegramApi(telegram), ME, self.store, self.profile, model)


class TestService(TelegramCase):
    def test_message_becomes_a_note_and_reply_names_only_the_key(self) -> None:
        telegram = FakeTelegram([(ME, "QG-1 wartet auf die Abnahme")])
        model = answering({"remarks": [{"story": "QG-1", "text": "QG-1 wartet auf die Abnahme"}]})
        handled = self.service(telegram, model).poll_once(datetime(2026, 11, 16, 8, 0))
        self.assertEqual(handled, 1)
        self.assertEqual(telegram.sent, [(ME, "Vermerkt:\n- #1 QG-1: QG-1 wartet auf die Abnahme")])
        (note,) = self.store.story_notes()
        self.assertEqual((note.story, note.source), ("QG-1: Prüfregeln für `bestellung_roh`", "telegram"))

    def test_removal_refers_to_the_previous_message(self) -> None:
        telegram = FakeTelegram([(ME, "QG-1 fertig")], [(ME, "entferne das wieder")], [(ME, "nein danke")])
        service = self.service(telegram, answering({"remarks": [{"story": "QG-1", "text": "QG-1 fertig"}]}))
        for _ in range(3):
            service.poll_once(datetime(2026, 11, 16, 8, 0))
        self.assertEqual([text for _, text in telegram.sent[1:]], ["Entfernt: #1", "Alles klar."])
        self.assertEqual(self.store.story_notes(("offen",)), [])

    def test_other_chats_are_ignored(self) -> None:
        telegram = FakeTelegram([(999, "QG-1 fertig"), (999, "/notizen")])
        self.assertEqual(self.service(telegram, self.fail).poll_once(datetime(2026, 11, 16, 8, 0)), 0)
        self.assertEqual((telegram.sent, self.store.story_notes()), ([], []))

    def test_commands_need_no_model(self) -> None:
        self.store.add_story_note("QG-1: Prüfregeln", "F", "wartet", date(2026, 11, 17))
        telegram = FakeTelegram([(ME, "/start"), (ME, "/notizen")])
        self.service(telegram).poll_once(datetime(2026, 11, 16, 8, 0))
        self.assertEqual(telegram.sent[0][1], telegram_bot.HELP)
        self.assertEqual(telegram.sent[1][1], "Daily Di 17.11.2026\n  #1 QG-1: wartet")

    def test_offset_acknowledges_updates(self) -> None:
        telegram = FakeTelegram([(ME, "/start")], [])
        service = self.service(telegram)
        service.poll_once(datetime(2026, 11, 16, 8, 0))
        service.poll_once(datetime(2026, 11, 16, 8, 0))
        polls = [params for method, params in telegram.calls if method == "getUpdates"]
        self.assertNotIn("offset", polls[0])
        self.assertEqual(polls[1]["offset"], 101)

    def test_asks_once_per_time_on_workdays_only(self) -> None:
        telegram = FakeTelegram()
        service = self.service(telegram)
        service.skip_past(datetime(2026, 11, 16, 10, 0))  # Start um 10: die 9-Uhr-Frage entfaellt
        service.poll_once(datetime(2026, 11, 16, 10, 5))
        self.assertEqual(telegram.sent, [])
        service.poll_once(datetime(2026, 11, 16, 16, 30))
        service.poll_once(datetime(2026, 11, 16, 16, 45))
        self.assertEqual(telegram.sent, [(ME, "Gibt es etwas Neues zu einer User Story?")])
        service.poll_once(datetime(2026, 11, 21, 17, 0))  # Samstag
        service.poll_once(datetime(2026, 12, 25, 17, 0))  # Feiertag
        self.assertEqual(len(telegram.sent), 1)
        service.poll_once(datetime(2026, 11, 17, 9, 1))
        self.assertEqual(len(telegram.sent), 2)


class TestSetupAndProfile(unittest.TestCase):
    def test_setup_returns_the_chat_of_the_first_message(self) -> None:
        lines: list[str] = []
        chat = setup(TelegramApi(FakeTelegram([], [(ME, "hallo")])), out=lines.append)
        self.assertEqual(chat, ME)
        self.assertEqual(lines[0], "Bot gefunden: @test_bot")
        ticks = iter(range(0, 1000, 60))
        self.assertIsNone(setup(TelegramApi(FakeTelegram()), 120, lambda text: None, lambda: next(ticks)))

    def test_chat_id_is_written_to_the_profile_once(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.toml"
            path.write_text('[person]\nname = "Erika"\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {"LOCAL_AGENTS_PROFILE": str(path)}):
                self.assertIn("Im Profil eingetragen", save_chat_id(ME))
                profile = load_profile()
                self.assertEqual((profile.name, profile.telegram_enabled, profile.telegram_chat_id), ("Erika", True, ME))
                self.assertIn("steht schon ein [telegram]-Block", save_chat_id(5))
                self.assertEqual(load_profile().telegram_chat_id, ME)

    def test_telegram_is_off_by_default_and_settings_are_checked(self) -> None:
        self.assertEqual((Profile().telegram_enabled, Profile().telegram_chat_id), (False, None))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.toml"
            path.write_text('[telegram]\nenabled = true\nchat_id = 5\nask_at = ["9:00", "16:30"]\n', encoding="utf-8")
            self.assertEqual(load_profile(path).telegram_ask_at, ("09:00", "16:30"))
            for bad in ('chat_id = "abc"', 'ask_at = ["halb fünf"]', 'ask_at = ["25:00"]'):
                path.write_text(f"[telegram]\n{bad}\n", encoding="utf-8")
                with self.assertRaises(ProfileError):
                    load_profile(path)

    def test_missing_token_explains_the_setup(self) -> None:
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}), mock.patch(
            "subprocess.run", side_effect=FileNotFoundError
        ):
            with self.assertRaises(telegram_bot.TelegramError) as caught:
                telegram_bot.telegram_token()
        self.assertIn("security add-generic-password -s local-agents-telegram", str(caught.exception))
        with mock.patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": " abc "}):
            self.assertEqual(telegram_bot.telegram_token(), "abc")


if __name__ == "__main__":
    unittest.main()

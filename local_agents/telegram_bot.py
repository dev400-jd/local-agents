"""Telegram-Kanal fuer den Scrum Master: Bemerkungen vom Handy aus.

Aufruf:
  python -m local_agents.telegram_bot einrichten   Token pruefen, eigenen Chat festlegen
  python -m local_agents.telegram_bot              Dienst starten (laeuft, bis Strg+C)

ACHTUNG, Datenschutz: Anders als der Rest dieses Projekts verlaesst hier etwas
den Rechner. Was du dem Bot schreibst und was er antwortet, laeuft ueber die
Server von Telegram und ist dort nicht Ende-zu-Ende-verschluesselt. Der Kanal
ist deshalb abgeschaltet, bis du ihn im Profil einschaltest
(``[telegram] enabled = true``). Der Bot antwortet knapp (Kuerzel statt
Story-Namen) und nur dem einen Chat aus dem Profil; alle anderen ignoriert er.

Der Dienst nimmt jede Nachricht als Antwort an den Scrum Master (Bemerkung,
"entferne das wieder", ...) und fragt zu den Uhrzeiten aus ``ask_at`` an
Werktagen selbst nach. Befehle im Chat: /notizen zeigt die offenen Bemerkungen.
Die Auswertung macht weiterhin das lokale Modell; der Rechner muss laufen.

Das Bot-Token (von @BotFather) liegt im macOS-Schluesselbund:
  security add-generic-password -s local-agents-telegram -a "$USER" -w
oder in der Umgebungsvariable TELEGRAM_BOT_TOKEN.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime

from local_agents.agent import ollama_model
from local_agents.holidays import is_public_holiday
from local_agents.profile import Profile, ProfileError, load_profile, profile_path
from local_agents.scrum_master import (
    AGENT_NAME,
    QUESTION,
    ScrumMaster,
    all_stories,
    format_notes,
)
from local_agents.store import NOTE_OPEN, Store

API_URL = "https://api.telegram.org"
TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
KEYCHAIN_SERVICE = "local-agents-telegram"
CHANNEL_NAME = "telegram"
POLL_SECONDS = 25
MAX_MESSAGE_CHARS = 4000
HELP = (
    "Schreib mir, was es Neues zu einer User Story gibt, z. B. „QG-3 wartet auf die "
    "Abnahme, fürs Daily am Dienstag“. „Entferne das wieder“ nimmt den letzten Vermerk "
    "zurück, /notizen zeigt die offenen."
)

# Ruft eine Methode der Bot-API auf: Methode und Parameter rein, "result" raus.
Transport = Callable[[str, dict], object]


class TelegramError(RuntimeError):
    """Telegram ist nicht erreichbar oder nicht eingerichtet."""


def telegram_token() -> str:
    """Liest das Bot-Token aus Umgebung oder Schluesselbund."""
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    raise TelegramError(
        "Kein Telegram-Token gefunden. Bot bei @BotFather anlegen und das Token ablegen mit:\n"
        f"  security add-generic-password -s {KEYCHAIN_SERVICE} -a \"$USER\" -w\n"
        f"oder die Umgebungsvariable {TOKEN_ENV} setzen. Einrichtung: README."
    )


def http_transport(token: str) -> Transport:
    """Transport, der die Bot-API mit dem Token aufruft."""

    def call(method: str, params: dict) -> object:
        data = urllib.parse.urlencode(params).encode()
        request = urllib.request.Request(f"{API_URL}/bot{token}/{method}", data=data)
        try:
            with urllib.request.urlopen(request, timeout=POLL_SECONDS + 15) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            reasons = {401: "Telegram lehnt das Token ab.", 409: "Ein anderer Dienst fragt denselben Bot ab."}
            raise TelegramError(f"{reasons.get(exc.code, 'Telegram meldet einen Fehler.')} (HTTP {exc.code} bei {method})") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TelegramError(f"Telegram nicht erreichbar: {exc}") from exc
        if not body.get("ok"):
            raise TelegramError(f"Telegram meldet einen Fehler bei {method}.")
        return body["result"]

    return call


@dataclass(frozen=True)
class Message:
    update_id: int
    chat_id: int
    text: str


class TelegramApi:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.offset: int | None = None

    def bot_name(self) -> str:
        me = self.transport("getMe", {})
        return str(me.get("username") or me.get("first_name") or "")

    def updates(self, timeout: int = POLL_SECONDS) -> list[Message]:
        """Neue Textnachrichten; wartet bis zu ``timeout`` Sekunden auf die naechste."""
        params: dict = {"timeout": timeout, "allowed_updates": json.dumps(["message"])}
        if self.offset is not None:
            params["offset"] = self.offset
        messages = []
        for update in self.transport("getUpdates", params):
            self.offset = update["update_id"] + 1
            message = update.get("message") or {}
            text = message.get("text")
            if text and "chat" in message:
                messages.append(Message(update["update_id"], message["chat"]["id"], text))
        return messages

    def send(self, chat_id: int, text: str) -> None:
        self.transport("sendMessage", {"chat_id": chat_id, "text": text[:MAX_MESSAGE_CHARS]})


@dataclass
class TelegramService:
    """Verbindet den Bot mit dem Scrum Master: Nachrichten annehmen, zu festen Zeiten fragen."""

    api: TelegramApi
    chat_id: int
    store: Store
    profile: Profile
    model: object
    asked: set[tuple[date, str]] = field(default_factory=set)
    agent: ScrumMaster | None = None

    def _agent(self, now: datetime) -> ScrumMaster:
        # Stories bei jeder Nachricht neu lesen; was zuletzt vermerkt wurde, bleibt bekannt.
        stories = all_stories(self.profile)
        if self.agent is None:
            self.agent = ScrumMaster(self.store, self.profile, self.model, now, stories)
        self.agent.now, self.agent.stories = now, stories
        return self.agent

    def handle(self, text: str, now: datetime) -> str:
        """Antwort auf eine Nachricht aus dem zugelassenen Chat."""
        command = text.strip().split()[0].casefold() if text.strip() else ""
        if command in ("/start", "/hilfe", "/help"):
            return HELP
        if command == "/notizen":
            return format_notes(self.store.story_notes((NOTE_OPEN,)), short=True)
        run_id = self.store.start_run(AGENT_NAME)
        try:
            reply = self._agent(now).respond(text, CHANNEL_NAME, short=True)
        except BaseException:
            self.store.finish_run(run_id, "fehler")
            raise
        self.store.finish_run(run_id, "ok", f"Nachricht über {CHANNEL_NAME}")
        return reply or "Alles klar."

    def due(self, now: datetime) -> bool:
        """Ob jetzt eine geplante Nachfrage faellig ist (an Werktagen, je Uhrzeit einmal)."""
        today = now.date()
        if today.weekday() >= 5 or (self.profile.state and is_public_holiday(today, self.profile.state)):
            return False
        for clock in self.profile.telegram_ask_at:
            if (today, clock) not in self.asked and now.strftime("%H:%M") >= clock:
                self.asked.add((today, clock))
                return True
        return False

    def skip_past(self, now: datetime) -> None:
        """Beim Start: Uhrzeiten, die heute schon vorbei sind, nicht nachholen."""
        for clock in self.profile.telegram_ask_at:
            if now.strftime("%H:%M") >= clock:
                self.asked.add((now.date(), clock))

    def poll_once(self, now: datetime, timeout: int = POLL_SECONDS) -> int:
        """Eine Runde: faellige Nachfrage senden, neue Nachrichten beantworten."""
        if self.due(now):
            self.api.send(self.chat_id, QUESTION)
        handled = 0
        for message in self.api.updates(timeout):
            if message.chat_id != self.chat_id:
                continue  # fremde Chats bekommen keine Antwort
            self.api.send(self.chat_id, self.handle(message.text, now))
            handled += 1
        return handled


def setup(api: TelegramApi, wait_seconds: int = 120, out=print, clock=time.monotonic) -> int | None:
    """Wartet auf die erste Nachricht an den Bot und gibt deren Chat-Nummer zurueck."""
    out(f"Bot gefunden: @{api.bot_name()}")
    out(f"Schreib dem Bot jetzt in Telegram eine beliebige Nachricht (ich warte {wait_seconds} s) ...")
    deadline = clock() + wait_seconds
    while clock() < deadline:
        for message in api.updates(timeout=10):
            return message.chat_id
    return None


def save_chat_id(chat_id: int) -> str:
    """Traegt den Chat ins Profil ein, falls dort noch kein [telegram]-Block steht."""
    path = profile_path()
    block = f"\n[telegram]\nenabled = true\nchat_id = {chat_id}\nask_at = []\n"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if "[telegram]" in text:
        return f"Im Profil steht schon ein [telegram]-Block. Dort eintragen: chat_id = {chat_id}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")
    return f"Im Profil eingetragen ({path}): [telegram] enabled = true, chat_id = {chat_id}"


def main(argv: list[str]) -> int:
    args = argv[1:]
    if args not in ([], ["einrichten"]):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        profile = load_profile()
        api = TelegramApi(http_transport(telegram_token()))
        if args:
            chat_id = setup(api)
            if chat_id is None:
                print("Keine Nachricht erhalten. Bot in Telegram öffnen, etwas schreiben, erneut versuchen.", file=sys.stderr)
                return 1
            api.send(chat_id, "Verbunden. " + HELP)
            print(save_chat_id(chat_id))
            return 0
        if not profile.telegram_enabled or profile.telegram_chat_id is None:
            print(
                "Telegram ist nicht eingeschaltet. Erst: python -m local_agents.telegram_bot einrichten\n"
                "Hinweis: Nachrichten laufen dann über die Server von Telegram. Siehe README.",
                file=sys.stderr,
            )
            return 1
        service = TelegramService(
            api, profile.telegram_chat_id, Store(), profile, ollama_model(profile.model_for(AGENT_NAME))
        )
        service.skip_past(datetime.now())
        api.updates(timeout=0)  # Nachrichten aus der Zeit vor dem Start nicht nachholen
        print(f"Telegram-Dienst läuft (@{api.bot_name()}). Beenden mit Strg+C.", flush=True)
        while True:
            try:
                service.poll_once(datetime.now())
            except TelegramError as exc:
                print(f"{exc} Neuer Versuch in 30 s.", file=sys.stderr, flush=True)
                time.sleep(30)
    except (ProfileError, TelegramError) as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nBeendet.")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Kalenderwache: gleicht die Datenbank ab, sobald sich der Kalender aendert.

Aufruf:
  python -m local_agents.watch              im Vordergrund laufen (Strg+C beendet)
  python -m local_agents.watch einrichten   als Hintergrunddienst einrichten und starten
  python -m local_agents.watch entfernen    Hintergrunddienst stoppen und entfernen
  python -m local_agents.watch status       laeuft der Dienst? letzte Zeilen des Protokolls

macOS meldet jede Aenderung am Kalender (EKEventStoreChangedNotification). Die
Wache wartet danach einige Sekunden, bis Ruhe ist, und ruft den Abgleich aus
``sync.py`` auf. Zusaetzlich gleicht sie beim Start und alle 30 Minuten ab,
falls eine Meldung verloren ging (etwa im Ruhezustand). Ein Modell braucht sie
nicht.

Hat sich etwas geaendert, erscheint eine macOS-Mitteilung; mit ``speak = true``
unter ``[watch]`` im Profil wird sie auch vorgelesen. Alles bleibt auf dem Rechner.

Der Hintergrunddienst ist ein LaunchAgent des angemeldeten Nutzers: Er startet
mit der Anmeldung und wird neu gestartet, falls er abstuerzt.
"""

import os
import plistlib
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from local_agents import sync, voice
from local_agents.calendar_events import CalendarAccessError, pump, watch_changes
from local_agents.profile import Profile, ProfileError, load_profile
from local_agents.store import Store

LABEL = "local-agents.watch"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library" / "Logs" / "local-agents" / "watch.log"
# Eine Aenderung loest meist mehrere Meldungen aus; erst nach so vielen
# Sekunden Ruhe wird abgeglichen.
QUIET_SECONDS = 5.0
# Abgleich ohne Meldung, als Netz fuer verlorene Meldungen.
FALLBACK_SECONDS = 30 * 60.0
# Durchgereichte Umgebungsvariablen (abweichende Pfade fuer Profil, Datenbank, Stimmen).
ENVIRONMENT = ("LOCAL_AGENTS_PROFILE", "LOCAL_AGENTS_DB", "LOCAL_AGENTS_VOICES")


class Watcher:
    """Entscheidet, wann abgeglichen wird. ``run`` fuehrt einen Abgleich aus."""

    def __init__(
        self,
        run: Callable[[], None],
        clock: Callable[[], float] = time.monotonic,
        quiet: float = QUIET_SECONDS,
        fallback: float = FALLBACK_SECONDS,
    ) -> None:
        self.run = run
        self.clock = clock
        self.quiet = quiet
        self.fallback = fallback
        self.changed_at: float | None = None
        self.synced_at: float | None = None

    def changed(self) -> None:
        """Der Kalender hat eine Aenderung gemeldet."""
        self.changed_at = self.clock()

    def tick(self) -> bool:
        """Gleicht ab, wenn es an der Zeit ist; True, wenn abgeglichen wurde."""
        now = self.clock()
        first = self.synced_at is None
        settled = self.changed_at is not None and now - self.changed_at >= self.quiet
        stale = self.synced_at is not None and now - self.synced_at >= self.fallback
        # Solange noch Meldungen eintreffen, wartet auch der Abgleich nach Zeit.
        if not (first or settled or (stale and self.changed_at is None)):
            return False
        self.changed_at = None
        self.synced_at = now
        self.run()
        return True


def notify(text: str, title: str = "Kalender abgeglichen") -> None:
    """Zeigt eine macOS-Mitteilung; Fehler dabei sind unwichtig."""
    script = (
        "on run argv",
        "display notification (item 1 of argv) with title (item 2 of argv)",
        "end run",
    )
    command = ["osascript"]
    for line in script:
        command += ["-e", line]
    try:
        subprocess.run([*command, text, title], check=False, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def spoken(lines: list[str]) -> str:
    """Die Meldung als Satz zum Vorlesen."""
    text = lines[0].replace("Kalender abgeglichen: ", "Dein Kalender hat sich geändert: ")
    details = (line.strip() for line in lines[1:])
    return " ".join([text, *(line[:1].upper() + line[1:] + "." for line in details)])


class Reporter:
    """Fuehrt den Abgleich aus und meldet das Ergebnis."""

    def __init__(
        self,
        profile: Profile,
        open_store: Callable[[], Store] = Store,
        fetch=None,
        notify: Callable[[str, str], None] = notify,
        speak: Callable[[str, Profile], object] = voice.speak_with_profile,
        log: Callable[[str], None] = print,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.profile = profile
        self.open_store = open_store
        self.fetch = fetch
        self.notify = notify
        self.speak = speak
        self.log = log
        self.now = now
        self.failing = False

    def __call__(self) -> None:
        lines: list[str] = []
        try:
            store = self.open_store()
            try:
                extra = {} if self.fetch is None else {"fetch": self.fetch}
                sync.refresh(
                    store,
                    self.profile,
                    self.now(),
                    out=lambda text: lines.extend(text.splitlines()),
                    **extra,
                )
            finally:
                store.close()
        except sqlite3.Error as exc:
            # Etwa wenn gerade ein anderer Agent schreibt; der naechste Abgleich holt es nach.
            self.log(f"{self.now():%d.%m. %H:%M} Abgleich übersprungen: {exc}")
            return
        failed = bool(lines) and lines[0].startswith("Kalender nicht abgeglichen")
        for line in lines:
            self.log(f"{self.now():%d.%m. %H:%M} {line}")
        if failed:
            # Nur beim ersten Mal melden, nicht bei jedem weiteren Versuch.
            if not self.failing and self.profile.watch_notify:
                self.notify(lines[0], "Kalenderwache")
            self.failing = True
            return
        self.failing = False
        if not lines:
            return
        if self.profile.watch_notify:
            body = lines[0].removeprefix("Kalender abgeglichen: ")
            self.notify("\n".join([body, *(line.strip() for line in lines[1:])]), "Kalender abgeglichen")
        if self.profile.watch_speak:
            try:
                self.speak(spoken(lines), self.profile)
            except Exception as exc:  # Vorlesen darf die Wache nicht beenden
                self.log(f"Vorlesen fehlgeschlagen: {exc}")


def serve(profile: Profile) -> int:
    """Laeuft, bis der Prozess beendet wird.

    Fehlt die Kalenderfreigabe oder ist der Abgleich abgeschaltet, endet die
    Wache mit 0, damit launchd sie nicht in Dauerschleife neu startet.
    """

    def log(text: str) -> None:
        print(text, flush=True)

    if not profile.sync_enabled:
        log("Der Abgleich ist im Profil abgeschaltet (sync = false unter [planning]). Wache beendet.")
        return 0
    reporter = Reporter(profile, log=log)

    def run() -> None:
        try:  # Aenderungen am Profil gelten ohne Neustart
            reporter.profile = load_profile()
        except ProfileError as exc:
            log(f"Profil nicht lesbar, es gilt der letzte Stand: {exc}")
        reporter()

    watcher = Watcher(run)
    try:
        handle = watch_changes(watcher.changed)
    except CalendarAccessError as exc:
        log(f"{exc} Wache beendet.")
        notify(str(exc), "Kalenderwache")
        return 0
    print(f"{datetime.now():%d.%m. %H:%M} Kalenderwache läuft.", flush=True)
    try:
        while True:
            pump(1.0)
            watcher.tick()
    except KeyboardInterrupt:
        return 0
    finally:
        del handle


# --- Hintergrunddienst (LaunchAgent)


def agent_plist(python: str, folder: Path, environ: dict[str, str]) -> dict:
    """Beschreibung des Dienstes fuer launchd."""
    plist = {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "local_agents.watch"],
        "WorkingDirectory": str(folder),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},  # nach Absturz neu starten, nach sauberem Ende nicht
        "ThrottleInterval": 60,
        "ProcessType": "Background",
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }
    passed = {name: environ[name] for name in ENVIRONMENT if environ.get(name)}
    if passed:
        plist["EnvironmentVariables"] = passed
    return plist


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def _target() -> str:
    return f"gui/{os.getuid()}"


def install() -> int:
    folder = Path(__file__).resolve().parent.parent
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(plistlib.dumps(agent_plist(sys.executable, folder, dict(os.environ))))
    _launchctl("bootout", f"{_target()}/{LABEL}")  # falls er schon laeuft
    result = _launchctl("bootstrap", _target(), str(PLIST_PATH))
    if result.returncode != 0:
        print(f"launchd hat den Dienst nicht angenommen: {result.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"Kalenderwache eingerichtet und gestartet.\n  Dienst:    {PLIST_PATH}\n  Protokoll: {LOG_PATH}")
    print(
        "Fragt macOS nach dem Kalenderzugriff für Python, bitte erlauben. "
        "Prüfen: python -m local_agents.watch status"
    )
    return 0


def uninstall() -> int:
    result = _launchctl("bootout", f"{_target()}/{LABEL}")
    existed = PLIST_PATH.exists()
    PLIST_PATH.unlink(missing_ok=True)
    if result.returncode != 0 and not existed:
        print("Die Kalenderwache war nicht eingerichtet.")
        return 0
    print("Kalenderwache gestoppt und entfernt.")
    return 0


def status() -> int:
    result = _launchctl("print", f"{_target()}/{LABEL}")
    if result.returncode != 0:
        print("Die Kalenderwache ist nicht eingerichtet." if not PLIST_PATH.exists() else "Die Kalenderwache ist eingerichtet, aber nicht geladen.")
        return 1
    running = any(line.strip() == "state = running" for line in result.stdout.splitlines())
    print("Die Kalenderwache läuft." if running else "Die Kalenderwache ist geladen, läuft aber gerade nicht.")
    if LOG_PATH.exists():
        tail = LOG_PATH.read_text(errors="replace").splitlines()[-8:]
        print(f"Protokoll ({LOG_PATH}):" + "".join(f"\n  {line}" for line in tail))
    return 0 if running else 1


def main(argv: list[str]) -> int:
    args = argv[1:]
    commands = {"einrichten": install, "entfernen": uninstall, "status": status}
    if len(args) == 1 and args[0] in commands:
        return commands[args[0]]()
    if args:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    return serve(profile)


if __name__ == "__main__":
    sys.exit(main(sys.argv))

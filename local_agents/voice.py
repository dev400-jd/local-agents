"""Sprachausgabe; der Text verlaesst den Rechner nicht.

Es spricht ein neuronales Sprachmodell (Piper, lokal). Die Stimme wird beim
ersten Gebrauch einmal heruntergeladen und liegt dann im Ordner
``DEFAULT_VOICE_DIR``. Scheitert Piper, spricht ersatzweise das macOS-Kommando
``say``.

Stimme ausprobieren: python -m local_agents.voice ["Text"]
"""

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

DEFAULT_VOICE = "Anna"  # Stimme fuer ``say``
DEFAULT_MODEL = "de_DE-thorsten-high"  # Stimme fuer Piper
VOICE_NAME = re.compile(r"[a-z]{2}_[A-Z]{2}-\w+-\w+")
DEFAULT_VOICE_DIR = Path.home() / ".local" / "share" / "local-agents" / "voices"

# Fuehrt das Kommando mit dem Text auf der Standardeingabe aus, gibt den Exit-Code zurueck.
Runner = Callable[[list[str], str], int]


def voice_dir() -> Path:
    return Path(os.environ.get("LOCAL_AGENTS_VOICES", DEFAULT_VOICE_DIR))


def speakable(text: str) -> str:
    """Text ohne Markdown-Zeichen und Zeilenumbrueche, wie er vorgelesen werden soll."""
    text = re.sub(r"[*_#`|]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.MULTILINE)
    return " ".join(text.split())


def piper_ready(model: str, folder: Path | None = None, runner: Runner | None = None) -> bool:
    """Ob Piper die Stimme sprechen kann; laedt sie beim ersten Mal herunter."""
    folder = folder or voice_dir()
    # Nur Namen aus dem Piper-Katalog, etwa de_DE-thorsten-high.
    if not VOICE_NAME.fullmatch(model) or importlib.util.find_spec("piper") is None:
        return False
    if (folder / f"{model}.onnx").is_file():
        return True
    print(f"Lade die Stimme {model} einmalig herunter ...", file=sys.stderr)
    folder.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "piper.download_voices", model, "--data-dir", str(folder)]
    return (runner or _run)(command, "") == 0 and (folder / f"{model}.onnx").is_file()


def speak(
    text: str,
    voice: str = DEFAULT_VOICE,
    model: str = "",
    runner: Runner | None = None,
    folder: Path | None = None,
    speed: float = 1.0,
    speaker: int | None = None,
) -> str:
    """Liest den Text vor und wartet, bis er gesprochen ist.

    Gibt zurueck, wer gesprochen hat: "piper", "say" oder "" (nichts ging).
    Ein ``model`` waehlt Piper, sofern es bereitsteht; sonst spricht ``say``.
    ``speed`` ueber 1 spricht schneller; ``speaker`` waehlt bei Piper-Stimmen
    mit mehreren Sprechern einen aus.
    """
    text = speakable(text)
    if not text:
        return ""
    runner = runner or _run
    folder = folder or voice_dir()
    try:
        if piper_ready(model, folder, runner) and _speak_piper(
            text, model, folder, runner, speed, speaker
        ):
            return "piper"
        if voice and runner(["say", "-v", voice], text) == 0:
            return "say"
        # Stimme nicht installiert: mit der Systemstimme versuchen.
        return "say" if runner(["say"], text) == 0 else ""
    except OSError:  # kein macOS
        return ""


def _speak_piper(
    text: str, model: str, folder: Path, runner: Runner, speed: float, speaker: int | None
) -> bool:
    """Erzeugt mit Piper eine WAV-Datei und spielt sie ab."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = str(Path(tmp) / "kurzfassung.wav")
        command = [sys.executable, "-m", "piper", "-m", model, "--data-dir", str(folder), "-f", wav]
        if speed != 1.0:
            # Piper dehnt die Laute: kleiner als 1 heisst schneller.
            command += ["--length-scale", f"{1 / speed:.2f}"]
        if speaker is not None:
            command += ["--speaker", str(speaker)]
        return runner(command, text) == 0 and runner(["afplay", wav], "") == 0


def _run(command: list[str], text: str) -> int:
    return subprocess.run(
        command, input=text, text=True, check=False, stdout=subprocess.DEVNULL
    ).returncode


def speak_with_profile(text: str, profile) -> str:
    """Liest den Text mit den Einstellungen aus ``[voice]`` im Profil vor."""
    return speak(
        text,
        profile.voice,
        profile.voice_model,
        speed=profile.voice_speed,
        speaker=profile.voice_speaker,
    )


def main(argv: list[str]) -> int:
    from local_agents.profile import ProfileError, load_profile

    text = " ".join(argv[1:]) or "Hallo, so klingt die eingestellte Stimme. Passt das so für dich?"
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    spoke = speak_with_profile(text, profile)
    names = {"piper": f"Piper-Stimme {profile.voice_model}", "say": f"macOS-Stimme {profile.voice}"}
    print(f"Gesprochen mit: {names.get(spoke, 'nichts, Sprachausgabe nicht verfügbar')}")
    return 0 if spoke else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

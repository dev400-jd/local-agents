"""Spracheingabe: eine gesprochene Antwort aufnehmen und lokal in Text umsetzen.

Das Mikrofon liest ``sounddevice``; die Aufnahme endet von selbst, sobald nach
dem Sprechen eine Weile Stille ist. Den Text erkennt Whisper (``pywhispercpp``,
laeuft auf dem Rechner). Das Modell wird beim ersten Gebrauch einmal
heruntergeladen und liegt dann in ``DEFAULT_MODEL_DIR``. Ton und Text verlassen
den Rechner nicht.

Ausprobieren: python -m local_agents.listen
"""

import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

DEFAULT_MODEL = "large-v3-turbo"
DEFAULT_MODEL_DIR = Path.home() / ".local" / "share" / "local-agents" / "whisper"
SAMPLE_RATE = 16000  # erwartet Whisper
BLOCK_SECONDS = 0.1
# Die ersten Bloecke messen das Grundrauschen des Raums.
CALIBRATION_BLOCKS = 3
# Sprache ist deutlich lauter als das Grundrauschen, mindestens aber so laut.
NOISE_FACTOR = 3.0
MIN_THRESHOLD = 0.01
# So viele laute Bloecke am Stueck gelten als Sprechbeginn (kein Tastenklick).
START_BLOCKS = 2


class ListenError(RuntimeError):
    """Mikrofon oder Spracherkennung stehen nicht zur Verfuegung."""


class Endpointer:
    """Entscheidet aus der Lautstaerke je Block, wann die Aufnahme endet.

    ``feed`` bekommt die Lautstaerke (RMS) eines Blocks und gibt True zurueck,
    sobald Schluss ist: nach ``silence`` Sekunden Stille im Anschluss an Sprache,
    nach ``wait`` Sekunden ohne Sprechbeginn oder nach ``limit`` Sekunden.
    """

    def __init__(self, silence: float = 1.5, wait: float = 8.0, limit: float = 45.0) -> None:
        self.silence_blocks = round(silence / BLOCK_SECONDS)
        self.wait_blocks = round(wait / BLOCK_SECONDS)
        self.limit_blocks = round(limit / BLOCK_SECONDS)
        self.blocks = 0
        self.noise: list[float] = []
        self.loud_run = 0
        self.quiet_run = 0
        self.spoke = False

    @property
    def threshold(self) -> float:
        noise = sum(self.noise) / len(self.noise) if self.noise else 0.0
        return max(noise * NOISE_FACTOR, MIN_THRESHOLD)

    def feed(self, level: float) -> bool:
        self.blocks += 1
        if len(self.noise) < CALIBRATION_BLOCKS:
            self.noise.append(level)
            return False
        if level > self.threshold:
            self.loud_run += 1
            self.quiet_run = 0
            self.spoke = self.spoke or self.loud_run >= START_BLOCKS
        else:
            self.loud_run = 0
            self.quiet_run += 1
        if self.spoke and self.quiet_run >= self.silence_blocks:
            return True
        if not self.spoke and self.blocks >= self.wait_blocks:
            return True
        return self.blocks >= self.limit_blocks


def model_dir() -> Path:
    return Path(os.environ.get("LOCAL_AGENTS_WHISPER", DEFAULT_MODEL_DIR))


def record(endpointer: Endpointer | None = None):
    """Nimmt vom Standardmikrofon auf, bis der Sprecher fertig ist.

    Gibt die Aufnahme als float32-Array (16 kHz, mono) zurueck oder None, wenn
    niemand gesprochen hat.
    """
    try:
        import numpy as np
        import sounddevice as sd
    except (ImportError, OSError) as exc:
        raise ListenError(f"Mikrofonaufnahme nicht installiert ({exc}). Siehe README.") from exc
    endpointer = endpointer or Endpointer()
    block = int(SAMPLE_RATE * BLOCK_SECONDS)
    chunks = []
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=block) as stream:
            while True:
                data, _overflowed = stream.read(block)
                chunks.append(data.copy())
                if endpointer.feed(float(np.sqrt(np.mean(np.square(data))))):
                    break
    except Exception as exc:  # PortAudio meldet eigene Fehlerklassen
        raise ListenError(
            f"Kein Zugriff auf das Mikrofon ({exc}). Freigabe: Systemeinstellungen -> "
            "Datenschutz & Sicherheit -> Mikrofon, fuer das Terminal."
        ) from exc
    if not endpointer.spoke:
        return None
    return np.concatenate(chunks).reshape(-1)


def transcribe(audio, model: str = DEFAULT_MODEL, language: str = "de") -> str:
    """Setzt die Aufnahme mit Whisper in Text um."""
    try:
        from pywhispercpp.model import Model
    except ImportError as exc:
        raise ListenError(f"Spracherkennung nicht installiert ({exc}). Siehe README.") from exc
    folder = model_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if not any(folder.glob(f"*{model}*")):
        print(f"Lade das Spracherkennungsmodell {model} einmalig herunter ...", file=sys.stderr)
    try:
        whisper = Model(model, models_dir=str(folder), redirect_whispercpp_logs_to=None)
        segments = whisper.transcribe(audio, language=language, print_progress=False)
    except Exception as exc:
        raise ListenError(f"Spracherkennung gescheitert: {exc}") from exc
    return clean(segment.text for segment in segments)


def clean(parts: Iterable[str]) -> str:
    """Fuegt die erkannten Stuecke zu einer Zeile zusammen."""
    return " ".join(" ".join(part.split()) for part in parts if part.strip())


def listen(
    model: str = DEFAULT_MODEL,
    language: str = "de",
    recorder: Callable[[], object] = record,
    transcriber: Callable[..., str] = transcribe,
) -> str:
    """Nimmt eine gesprochene Antwort auf und gibt sie als Text zurueck.

    Leer, wenn niemand gesprochen hat. ``ListenError``, wenn Mikrofon oder
    Spracherkennung nicht funktionieren.
    """
    audio = recorder()
    if audio is None:
        return ""
    return transcriber(audio, model, language)


def main(argv: list[str]) -> int:
    from local_agents.profile import ProfileError, load_profile

    try:
        profile = load_profile()
        print("Ich höre zu … (Stille beendet die Aufnahme)", flush=True)
        text = listen(profile.listen_model, profile.listen_language)
    except (ProfileError, ListenError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Verstanden: {text}" if text else "Ich habe nichts gehört.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

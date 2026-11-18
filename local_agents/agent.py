"""Minimaler Agent: beantwortet Fragen zu Dateien eines Verzeichnisses.

Das Modell laeuft lokal ueber Ollama. Der Agent hat genau ein Werkzeug,
``read_file``, das auf das uebergebene Wurzelverzeichnis beschraenkt ist.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent, ModelRetry, NativeOutput, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider

MODEL_NAME = os.environ.get("LOCAL_AGENTS_MODEL", "gpt-oss:20b")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
# Kontextbudget ist 16K: Dateiinhalte werden gekuerzt, Antworten begrenzt.
MAX_FILE_CHARS = 8000
MAX_OUTPUT_TOKENS = 2048
# Niedriger Reasoning-Aufwand: Bei gpt-oss landen die englischen
# Zwischenueberlegungen sonst gelegentlich in der Antwort, und die Laeufe
# dauern zwei- bis dreimal so lange.
MODEL_SETTINGS = {"max_tokens": MAX_OUTPUT_TOKENS, "openai_reasoning_effort": "low"}
# Wie Agenten mit festem Antwortschema ihre Antwort abgeben:
# "tool"   = das Modell ruft ein Ausgabe-Werkzeug auf (Standard von Pydantic AI),
# "native" = Ollama erzwingt das Schema beim Erzeugen der Antwort.
# Gemessen mit gpt-oss:20b (README, Abschnitt "Stand"): kein Unterschied, der
# ueber die Schwankung von Lauf zu Lauf hinausgeht. Deshalb bleibt der Standard.
OUTPUT_MODE = os.environ.get("LOCAL_AGENTS_OUTPUT", "tool")

INSTRUCTIONS = (
    "Du beantwortest Fragen zu Dateien in einem Projektverzeichnis. "
    "Lies eine Datei mit read_file, bevor du etwas ueber ihren Inhalt sagst. "
    "Antworte knapp auf Deutsch."
)


@dataclass
class Deps:
    """Laufzeitabhaengigkeiten: das Verzeichnis, das der Agent lesen darf."""

    root: Path


def ollama_model(name: str = "") -> OpenAIChatModel:
    """Erzeugt das lokale Modell; baut noch keine Verbindung auf.

    Ohne ``name`` ist es das Standardmodell; einzelne Agenten koennen ein
    anderes bekommen (``[models]`` im Profil).
    """
    return OpenAIChatModel(name or MODEL_NAME, provider=OllamaProvider(base_url=OLLAMA_BASE_URL))


def structured(output_type):
    """Das Antwortschema eines Agenten in der eingestellten Ausgabeart (``OUTPUT_MODE``)."""
    if OUTPUT_MODE not in ("tool", "native"):
        raise ValueError(f"LOCAL_AGENTS_OUTPUT={OUTPUT_MODE!r} unbekannt. Erlaubt: tool, native.")
    return NativeOutput(output_type) if OUTPUT_MODE == "native" else output_type


def answer_with_notes(agent: Agent, question: str, deps, model) -> str:
    """Antwort des Modells, gefolgt von den Abschnitten in ``deps.notes``.

    ``deps.notes`` (Ueberschrift -> Zeilen) fuellen die Werkzeuge mit Text, den
    der Code formuliert. Er geht am Modell vorbei, weil kleine Modelle Listen
    beim Wiedergeben veraendern.
    """
    output = agent.run_sync(question, model=model, deps=deps).output.rstrip()
    return output + format_notes(deps.notes)


def format_notes(notes: dict[str, list[str]]) -> str:
    """Die Abschnitte als Text, jeweils mit Leerzeile davor und fetter Ueberschrift."""
    return "".join(
        f"\n\n**{heading}**\n" + "\n".join(lines) for heading, lines in notes.items() if lines
    )


agent = Agent(
    deps_type=Deps,
    instructions=INSTRUCTIONS,
    model_settings=MODEL_SETTINGS,
    retries=2,
)


@agent.tool
def read_file(ctx: RunContext[Deps], path: str) -> str:
    """Liest eine Textdatei relativ zum Projektverzeichnis.

    Args:
        path: Relativer Pfad, zum Beispiel "README.md".
    """
    root = ctx.deps.root.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ModelRetry(f"{path} liegt ausserhalb des Projektverzeichnisses.")
    if not target.is_file():
        raise ModelRetry(f"{path} ist keine vorhandene Datei.")
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > MAX_FILE_CHARS:
        return text[:MAX_FILE_CHARS] + f"\n[gekuerzt: {len(text)} Zeichen insgesamt]"
    return text

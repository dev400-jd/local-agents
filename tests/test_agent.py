"""Tests ohne Ollama: ein geskriptetes Modell ersetzt das lokale Modell."""

import tempfile
import unittest
from pathlib import Path

from pydantic_ai import models
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from local_agents.agent import MAX_FILE_CHARS, Deps, agent

# Kein Test darf versehentlich ein echtes Modell aufrufen.
models.ALLOW_MODEL_REQUESTS = False


def scripted_model(path: str) -> FunctionModel:
    """Modell, das zuerst read_file(path) aufruft und dann das Ergebnis wiedergibt."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for part in messages[-1].parts:
            if part.part_kind == "tool-return":
                # Ein ModelRetry des Werkzeugs kommt als Rueckgabe mit outcome="retried" an.
                label = "FEHLER" if part.outcome == "retried" else "INHALT"
                return ModelResponse(parts=[TextPart(f"{label}: {part.content}")])
        return ModelResponse(parts=[ToolCallPart("read_file", {"path": path})])

    return FunctionModel(respond)


class TestReadFileAgent(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "projekt"
        self.root.mkdir()

    def run_agent(self, path: str) -> str:
        result = agent.run_sync("Frage", model=scripted_model(path), deps=Deps(root=self.root))
        return result.output

    def test_reads_file_inside_root(self) -> None:
        (self.root / "notiz.txt").write_text("Hallo Agent", encoding="utf-8")
        self.assertEqual(self.run_agent("notiz.txt"), "INHALT: Hallo Agent")

    def test_rejects_path_outside_root(self) -> None:
        (self.base / "geheim.txt").write_text("GEHEIM", encoding="utf-8")
        output = self.run_agent("../geheim.txt")
        self.assertTrue(output.startswith("FEHLER:"), output)
        self.assertIn("ausserhalb", output)
        self.assertNotIn("GEHEIM", output)

    def test_reports_missing_file(self) -> None:
        output = self.run_agent("fehlt.txt")
        self.assertTrue(output.startswith("FEHLER:"), output)
        self.assertIn("keine vorhandene Datei", output)

    def test_truncates_large_file(self) -> None:
        (self.root / "gross.txt").write_text("x" * (MAX_FILE_CHARS + 500), encoding="utf-8")
        output = self.run_agent("gross.txt")
        self.assertIn(f"[gekuerzt: {MAX_FILE_CHARS + 500} Zeichen insgesamt]", output)
        self.assertEqual(output.count("x"), MAX_FILE_CHARS)


if __name__ == "__main__":
    unittest.main()

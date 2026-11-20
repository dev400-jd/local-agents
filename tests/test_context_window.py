"""Tests fuer die Pruefung des Kontextfensters; Ollama wird nicht angesprochen."""

import unittest

from local_agents import context_window as cw
from local_agents.profile import Profile


class FakeOllama:
    """Antwortet wie die Schnittstelle von Ollama und merkt sich angelegte Varianten."""

    def __init__(self, models: dict[str, str], loaded: dict[str, int] | None = None) -> None:
        self.models = dict(models)  # Name -> Text der Parameter
        self.loaded = loaded or {}
        self.created: list[dict] = []

    def __call__(self, path: str, payload: dict | None) -> dict:
        if path == "/api/tags":
            return {"models": [{"name": name} for name in self.models]}
        if path == "/api/ps":
            return {"models": [{"name": n, "context_length": c} for n, c in self.loaded.items()]}
        if path == "/api/show":
            return {"parameters": self.models[payload["model"]]}
        if path == "/api/create":
            self.created.append(payload)
            self.models[payload["model"]] = f"temperature 1\nnum_ctx {payload['parameters']['num_ctx']}"
            return {"status": "success"}
        raise AssertionError(path)


class TestContextWindow(unittest.TestCase):
    def test_fixed_context_is_read_from_the_parameters(self) -> None:
        ollama = FakeOllama({"a": "temperature    1\nnum_ctx    16384", "b": "temperature 1", "c": ""})
        self.assertEqual([cw.fixed_context(name, ollama) for name in "abc"], [16384, None, None])

    def test_states_name_who_uses_which_model(self) -> None:
        profile = Profile(models={"stories": "gross:27b", "scrum_master": "gross:27b", "status": "fehlt:1b"})
        ollama = FakeOllama(
            {cw.MODEL_NAME: "temperature 1", "gross:27b": "num_ctx 4096"}, loaded={cw.MODEL_NAME: 8167}
        )
        self.assertEqual(
            cw.state_lines(cw.states(profile, ollama)),
            [
                f"! {cw.MODEL_NAME} (Standard): Kontext nicht festgelegt, Ollama wählt selbst "
                "(weniger als 16384 gemessen); gerade geladen mit 8167",
                "! gross:27b (scrum_master, stories): Kontext fest auf 4096 Tokens, die Agenten rechnen mit 16384",
                "! fehlt:1b (status): nicht installiert",
            ],
        )

    def test_variant_is_created_with_16k_and_then_passes(self) -> None:
        ollama = FakeOllama({"klein:20b": "temperature 1"})
        name = cw.create_variant("klein:20b", ollama)
        self.assertEqual(name, "klein:20b-ctx16k")
        self.assertEqual(
            ollama.created,
            [{"model": name, "from": "klein:20b", "parameters": {"num_ctx": 16384}, "stream": False}],
        )
        self.assertEqual(cw.variant_name(name), name)  # keine Variante der Variante
        profile = Profile(models={"default": name})
        self.assertEqual(
            cw.state_lines(cw.states(profile, ollama)),
            ["✓ klein:20b-ctx16k (Standard): Kontext fest auf 16384 Tokens"],
        )

    def test_hint_names_the_profile_lines_to_change(self) -> None:
        profile = Profile(models={"stories": "gross:27b", "status": cw.MODEL_NAME})
        self.assertEqual(
            cw.profile_hint(cw.MODEL_NAME, cw.MODEL_NAME + "-ctx16k", profile),
            [
                "Damit die Agenten sie benutzen, im Profil unter [models] eintragen:",
                f'  default = "{cw.MODEL_NAME}-ctx16k"',
                f'  status = "{cw.MODEL_NAME}-ctx16k"',
            ],
        )
        self.assertIn("nutzt kein Agent", cw.profile_hint("anderes:1b", "anderes:1b-ctx16k", profile)[0])


if __name__ == "__main__":
    unittest.main()

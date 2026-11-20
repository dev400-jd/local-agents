"""Kontextfenster der lokalen Modelle pruefen und auf 16K festlegen.

Aufruf:
  python -m local_agents.context_window                       Stand je Modell
  python -m local_agents.context_window einrichten            Variante des Standardmodells anlegen
  python -m local_agents.context_window einrichten <modell>   Variante eines bestimmten Modells
  python -m local_agents.context_window entfernen <variante>  Variante wieder loeschen

Ollama laedt ein Modell mit einem Kontextfenster, das es selbst aus dem freien
Speicher ableitet (gemessen: 4096 bis rund 8000 Tokens). Was darueber
hinausgeht, schneidet es stillschweigend ab. Die Agenten rechnen mit 16K.

Ueber die OpenAI-kompatible Schnittstelle, die die Agenten nutzen, laesst sich
das Fenster nicht je Anfrage setzen (geprueft mit Ollama 0.40). Deshalb legt
``einrichten`` eine Variante des Modells an, in der das Fenster feststeht, etwa
``gpt-oss:20b-ctx16k``. Sie teilt sich die Gewichte mit dem Original und belegt
keinen weiteren Plattenplatz. Benutzt wird sie, sobald sie im Profil unter
``[models]`` steht.
"""

import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from local_agents.agent import MODEL_NAME, OLLAMA_BASE_URL
from local_agents.profile import Profile, ProfileError, load_profile

CONTEXT_TOKENS = 16384
SUFFIX = "-ctx16k"

# (Pfad, Nutzlast oder None fuer GET) -> Antwort als dict
Call = Callable[[str, dict | None], dict]


class OllamaError(RuntimeError):
    """Ollama ist nicht erreichbar oder lehnt die Anfrage ab."""


def api(path: str, payload: dict | None = None, method: str = "") -> dict:
    """Ruft die eigene Schnittstelle von Ollama auf (nicht die OpenAI-kompatible)."""
    url = OLLAMA_BASE_URL.rstrip("/").removesuffix("/v1") + path
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method=method or None)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as exc:
        raise OllamaError(f"Ollama meldet {exc.code}: {exc.read().decode()[:200]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise OllamaError(f"Ollama ist nicht erreichbar: {exc}") from exc
    return json.loads(body) if body.strip() else {}


def variant_name(model: str) -> str:
    """Name der Variante mit festem Kontextfenster."""
    return model if model.endswith(SUFFIX) else model + SUFFIX


def fixed_context(model: str, call: Call = api) -> int | None:
    """Das im Modell festgelegte Kontextfenster; None, wenn Ollama es selbst waehlt."""
    for line in call("/api/show", {"model": model}).get("parameters", "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "num_ctx" and parts[1].isdigit():
            return int(parts[1])
    return None


def loaded_contexts(call: Call = api) -> dict[str, int]:
    """Gerade geladene Modelle mit dem Kontextfenster, das sie tatsaechlich haben."""
    return {
        model["name"]: model["context_length"]
        for model in call("/api/ps", None).get("models", [])
        if model.get("context_length")
    }


def installed(call: Call = api) -> set[str]:
    return {model["name"] for model in call("/api/tags", None).get("models", [])}


def create_variant(model: str, call: Call = api) -> str:
    """Legt die Variante mit 16K an und gibt ihren Namen zurueck."""
    name = variant_name(model)
    call(
        "/api/create",
        {"model": name, "from": model, "parameters": {"num_ctx": CONTEXT_TOKENS}, "stream": False},
    )
    return name


@dataclass(frozen=True)
class ModelState:
    name: str
    agents: tuple[str, ...]  # wer es laut Profil benutzt; "Standard" fuer alle uebrigen
    installed: bool
    fixed: int | None
    loaded: int | None

    @property
    def enough(self) -> bool:
        return (self.fixed or 0) >= CONTEXT_TOKENS


def states(profile: Profile, call: Call = api) -> list[ModelState]:
    """Je Modell aus dem Profil: wer es nutzt und welches Kontextfenster es hat."""
    users: dict[str, list[str]] = {}
    users.setdefault(profile.models.get("default") or MODEL_NAME, []).append("Standard")
    for agent, name in sorted(profile.models.items()):
        if agent != "default" and name:
            users.setdefault(name, []).append(agent)
    present, loaded = installed(call), loaded_contexts(call)
    return [
        ModelState(
            name,
            tuple(agents),
            name in present,
            fixed_context(name, call) if name in present else None,
            loaded.get(name),
        )
        for name, agents in users.items()
    ]


def state_lines(found: list[ModelState]) -> list[str]:
    lines = []
    for state in found:
        if not state.installed:
            verdict = "nicht installiert"
        elif state.enough:
            verdict = f"Kontext fest auf {state.fixed} Tokens"
        elif state.fixed:
            verdict = f"Kontext fest auf {state.fixed} Tokens, die Agenten rechnen mit {CONTEXT_TOKENS}"
        else:
            verdict = f"Kontext nicht festgelegt, Ollama wählt selbst (weniger als {CONTEXT_TOKENS} gemessen)"
        if state.loaded:
            verdict += f"; gerade geladen mit {state.loaded}"
        mark = "✓" if state.enough else "!"
        lines.append(f"{mark} {state.name} ({', '.join(state.agents)}): {verdict}")
    return lines


def profile_hint(original: str, variant: str, profile: Profile) -> list[str]:
    """Welche Zeilen im Profil auf die Variante zeigen muessen."""
    keys = [agent for agent, name in sorted(profile.models.items()) if name == original and agent != "default"]
    if (profile.models.get("default") or MODEL_NAME) == original:
        keys.insert(0, "default")
    if not keys:
        return [f"Im Profil nutzt kein Agent {original}; trage die Variante dort ein, wo du sie willst."]
    return ["Damit die Agenten sie benutzen, im Profil unter [models] eintragen:"] + [
        f'  {key} = "{variant}"' for key in keys
    ]


def main(argv: list[str]) -> int:
    args = argv[1:]
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        if not args:
            found = states(profile)
            print("\n".join(state_lines(found)))
            if not all(state.enough for state in found):
                print("\nFestlegen: python -m local_agents.context_window einrichten [modell]")
            return 0
        if args[0] == "einrichten" and len(args) <= 2:
            original = args[1] if len(args) == 2 else profile.models.get("default") or MODEL_NAME
            if original not in installed():
                print(f"{original} ist in Ollama nicht installiert.", file=sys.stderr)
                return 1
            variant = create_variant(original)
            print(f"Angelegt: {variant} mit {fixed_context(variant)} Tokens Kontext.")
            print("\n".join(profile_hint(original, variant, profile)))
            return 0
        if args[0] == "entfernen" and len(args) == 2 and args[1].endswith(SUFFIX):
            api("/api/delete", {"model": args[1]}, method="DELETE")
            print(f"Entfernt: {args[1]}. Steht sie noch im Profil, dort wieder das Original eintragen.")
            return 0
    except OllamaError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

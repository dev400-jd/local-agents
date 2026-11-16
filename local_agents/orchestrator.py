"""Orchestrierung: reicht die naechste Aufgabe von Agent zu Agent weiter.

Aufruf: python -m local_agents.orchestrator [--neu]

Die Reihenfolge legt der Code fest, kein Modell:

1. Status-Agent: welche Aufgabe ist jetzt als Naechstes dran?
2. Termin-Agent: Um welche Art von Termin geht es, und welche Ergebnisse
   helfen dem Nutzer dafuer? (nur fuer Vorbereitungsaufgaben mit Termin)

Weitere Schritte (Agenten, die die Ergebnisse erarbeiten) folgen hier.
``--neu`` laesst den Termin-Agenten neu einordnen, auch wenn schon eine
Einordnung gespeichert ist.
"""

import sys
from datetime import datetime

from local_agents import meeting_agent, status_agent, sync
from local_agents.agent import format_notes, ollama_model
from local_agents.profile import Profile, ProfileError, load_profile
from local_agents.store import Store

PREPARATION = "vorbereitung"


def run(now: datetime, store: Store, profile: Profile, model_for, fresh: bool = False) -> str:
    """Fuehrt die Kette einmal aus und gibt den Text fuers Terminal zurueck.

    ``model_for`` liefert zu einem Agentennamen das Modell.
    """
    deps = status_agent.StatusDeps(now=now, store=store, profile=profile)
    output = status_agent.answer(
        status_agent.DEFAULT_QUESTION, deps, model_for(status_agent.AGENT_NAME)
    )
    item = deps.item
    if item is None:
        return output
    # Uebergabe: Aufgabe und Termin gehen an den Agenten, der zur Art der Aufgabe passt.
    if item.task.kind != PREPARATION or item.event is None:
        return output + (
            f"\n\nFür Aufgaben der Art „{item.task.kind}“ gibt es noch keinen Agenten, "
            "der sie weiterbearbeitet."
        )
    brief = meeting_agent.analyse(
        item.task, item.event, profile, store, model_for(meeting_agent.AGENT_NAME), fresh
    )
    return output + format_notes(meeting_agent.brief_notes(brief, profile, item.event.context))


def main(argv: list[str]) -> int:
    args = [arg for arg in argv[1:] if arg != "--neu"]
    if args:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    now, store = datetime.now(), Store()
    sync.refresh(store, profile, now)
    print(
        run(
            now,
            store,
            profile,
            lambda agent: ollama_model(profile.model_for(agent)),
            fresh="--neu" in argv,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

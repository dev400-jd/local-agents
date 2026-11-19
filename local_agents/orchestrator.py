"""Orchestrierung: reicht die naechste Aufgabe von Agent zu Agent weiter.

Aufruf: python -m local_agents.orchestrator [--neu]

Die Reihenfolge legt der Code fest, kein Modell:

1. Status-Agent: welche Aufgabe ist jetzt als Naechstes dran?
2. Je nach Art der Aufgabe:
   - Vorbereitung: Der Termin-Agent sagt, um welche Art von Termin es geht und
     welche Ergebnisse dem Nutzer dafuer helfen.
   - Verschieben: Der Nachrichten-Agent schreibt den Entwurf der Nachricht.
     Freigegeben wird er mit ``python -m local_agents.message_agent``.

``--neu`` laesst den zweiten Agenten neu arbeiten, auch wenn schon ein Ergebnis
gespeichert ist.
"""

import sys
from datetime import datetime

from local_agents import meeting_agent, message_agent, status_agent, sync
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
    if item.task.kind == message_agent.MOVE and item.event is not None:
        try:
            result = message_agent.draft(
                item.task, item.event, profile, store, model_for(message_agent.AGENT_NAME), fresh, now=now
            )
        except message_agent.NoMessageTask:
            result = None
        hint = f"\n\nFreigeben oder ändern: python -m local_agents.message_agent {item.task.id}"
        if result is not None:
            return output + format_notes(message_agent.draft_notes(item.task, result)) + hint
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

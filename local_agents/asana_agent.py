"""Asana-Agent: zeigt, wo der Nutzer in Asana erwaehnt wurde.

Aufruf: python -m local_agents.asana_agent ["Frage"]

Ohne Frage geht es um die letzten 14 Tage. Das Modell fasst zusammen; die
vollstaendige Liste mit Links druckt der Code unveraendert darunter.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic_ai import Agent, ModelRetry, RunContext

from local_agents.agent import MODEL_SETTINGS, answer_with_notes, ollama_model
from local_agents.asana_source import (
    AsanaAccessError,
    Mention,
    asana_token,
    fetch_mentions,
    http_transport,
)
from local_agents.profile import EXTERNAL_UNKNOWN, OTHER, Profile, ProfileError, load_profile

DEFAULT_DAYS = 14
MAX_DAYS = 60
DEFAULT_QUESTION = f"Wo wurde ich in den letzten {DEFAULT_DAYS} Tagen in Asana erwaehnt?"
# Kontextbudget ist 16K: Die Liste fuer das Modell wird begrenzt.
MAX_MENTION_CHARS = 6000


@dataclass
class AsanaDeps:
    """Laufzeitabhaengigkeiten: Zeitpunkt, Profil und die Quelle der Erwaehnungen."""

    now: datetime
    fetch: Callable[[datetime], list[Mention]]
    profile: Profile = field(default_factory=Profile)
    # Vom Code formulierte Abschnitte (Ueberschrift -> Zeilen) fuer die Ausgabe.
    notes: dict[str, list[str]] = field(default_factory=dict)


asana_agent = Agent(deps_type=AsanaDeps, model_settings=MODEL_SETTINGS, retries=2)


@asana_agent.instructions
def instructions(ctx: RunContext[AsanaDeps]) -> str:
    profile = ctx.deps.profile.describe()
    return (
        "Du bist ein Asana-Assistent. Hole die Erwaehnungen des Nutzers mit "
        "list_mentions, bevor du etwas darueber sagst, und nenne nur, was im "
        f"Ergebnis steht. Heute ist {ctx.deps.now:%Y-%m-%d}. Fasse knapp auf Deutsch "
        "zusammen: Wer will was vom Nutzer, gruppiert nach Aufgabe, das Neueste "
        "zuerst. Hebe hervor, was nach einer Antwort oder Entscheidung verlangt. "
        "Die vollstaendige Liste mit Links wird automatisch angehaengt; gib keine "
        "Links aus." + (f"\n{profile}" if profile else "")
    )


@asana_agent.tool
def list_mentions(ctx: RunContext[AsanaDeps], days: int = DEFAULT_DAYS) -> str:
    """Listet Asana-Kommentare und -Beschreibungen, in denen der Nutzer erwaehnt wurde.

    Args:
        days: Wie viele Tage zurueck, hoechstens 60.
    """
    if not 1 <= days <= MAX_DAYS:
        raise ModelRetry(f"days muss zwischen 1 und {MAX_DAYS} liegen.")
    mentions = ctx.deps.fetch(ctx.deps.now - timedelta(days=days))
    profile = ctx.deps.profile

    def tag(mention: Mention) -> str:
        context = profile.classify(" ".join((mention.task, *mention.projects)))
        return "" if context in (OTHER, EXTERNAL_UNKNOWN) else f" [{profile.label(context)}]"

    heading = f"Erwähnungen der letzten {days} Tage ({len(mentions)})"
    ctx.deps.notes[heading] = [line for m in mentions for line in _note_lines(m, tag(m))]
    if not mentions:
        return f"Keine Erwaehnungen in den letzten {days} Tagen."

    by_task: dict[tuple[str, str], list[Mention]] = {}
    for mention in mentions:
        by_task.setdefault((mention.task, mention.url), []).append(mention)
    lines = []
    for (task, _), group in by_task.items():
        first = group[0]
        projects = f" (Projekte: {', '.join(first.projects)})" if first.projects else ""
        done = " (erledigt)" if first.completed else ""
        lines.append(f"Aufgabe: {task}{tag(first)}{projects}{done}")
        lines.extend(
            f"  {_local(m.when):%d.%m. %H:%M} {m.author} ({m.kind}): {m.excerpt}" for m in group
        )
    text = "\n".join(lines)
    if len(text) > MAX_MENTION_CHARS:
        return text[:MAX_MENTION_CHARS] + f"\n[gekuerzt: {len(mentions)} Erwaehnungen insgesamt]"
    return text


def _note_lines(mention: Mention, tag: str) -> list[str]:
    done = ", erledigt" if mention.completed else ""
    lines = [
        f"- {_local(mention.when):%d.%m. %H:%M} · {mention.author} in „{mention.task}“{tag} "
        f"({mention.kind}{done}): {mention.excerpt}"
    ]
    if mention.url:
        lines.append(f"  {mention.url}")
    return lines


def _local(moment: datetime) -> datetime:
    return moment.astimezone()


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print(__doc__, file=sys.stderr)
        return 2
    question = argv[1] if len(argv) == 2 else DEFAULT_QUESTION
    try:
        transport = http_transport(asana_token())
        deps = AsanaDeps(
            now=datetime.now().astimezone(),
            fetch=lambda since: fetch_mentions(since, transport),
            profile=load_profile(),
        )
        output = answer_with_notes(asana_agent, question, deps, ollama_model(deps.profile.model_for("asana")))
    except (AsanaAccessError, ProfileError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

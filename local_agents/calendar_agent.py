"""Kalender-Agent: fasst Termine aus dem macOS-Kalender zusammen.

Aufruf: python -m local_agents.calendar_agent ["Frage"]

Ohne Frage fasst der Agent die kommende Woche zusammen.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from pydantic_ai import Agent, ModelRetry, RunContext

from local_agents.agent import MAX_OUTPUT_TOKENS, ollama_model
from local_agents.calendar_events import (
    WEEKDAYS,
    CalendarAccessError,
    Event,
    fetch_events,
    format_events,
    next_week,
)

DEFAULT_QUESTION = "Fasse meine naechste Woche zusammen."
# Kontextbudget ist 16K: Terminliste und Zeitraum werden begrenzt.
MAX_EVENT_CHARS = 6000
MAX_RANGE_DAYS = 31


@dataclass
class CalendarDeps:
    """Laufzeitabhaengigkeiten: heutiges Datum und die Terminquelle."""

    today: date
    fetch: Callable[[date, date], list[Event]] = fetch_events


calendar_agent = Agent(
    deps_type=CalendarDeps,
    model_settings={"max_tokens": MAX_OUTPUT_TOKENS},
    retries=2,
)


@calendar_agent.instructions
def instructions(ctx: RunContext[CalendarDeps]) -> str:
    today = ctx.deps.today
    monday, sunday = next_week(today)
    return (
        "Du bist ein Kalender-Assistent. Hole Termine mit list_events, bevor du "
        "etwas ueber den Kalender sagst, und nenne nur Termine aus dem Ergebnis. "
        f"Heute ist {WEEKDAYS[today.weekday()]}, {today.isoformat()}. "
        f"Die naechste Woche reicht von {monday.isoformat()} bis {sunday.isoformat()}. "
        "Fasse knapp auf Deutsch zusammen: Tag fuer Tag, mit Uhrzeiten, und weise "
        "auf Ueberschneidungen und besonders volle Tage hin."
    )


@calendar_agent.tool
def list_events(ctx: RunContext[CalendarDeps], start: date, end: date) -> str:
    """Listet die Kalendertermine eines Zeitraums auf.

    Args:
        start: Erster Tag im Format JJJJ-MM-TT.
        end: Letzter Tag (einschliesslich) im Format JJJJ-MM-TT.
    """
    if end < start:
        raise ModelRetry("end liegt vor start.")
    if (end - start).days >= MAX_RANGE_DAYS:
        raise ModelRetry(f"Zeitraum zu lang, hoechstens {MAX_RANGE_DAYS} Tage.")
    return format_events(ctx.deps.fetch(start, end), MAX_EVENT_CHARS)


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print(__doc__, file=sys.stderr)
        return 2
    question = argv[1] if len(argv) == 2 else DEFAULT_QUESTION
    try:
        result = calendar_agent.run_sync(
            question, model=ollama_model(), deps=CalendarDeps(today=date.today())
        )
    except CalendarAccessError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(result.output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

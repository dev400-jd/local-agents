"""Kalender-Agent: fasst Termine aus dem macOS-Kalender zusammen.

Aufruf: python -m local_agents.calendar_agent ["Frage"]

Ohne Frage fasst der Agent die kommende Woche zusammen. Das Profil
(local_agents/profile.py) ordnet jeden Termin einem Arbeitskontext zu.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from pydantic_ai import Agent, ModelRetry, RunContext

from local_agents.agent import MODEL_SETTINGS, answer_with_notes, ollama_model
from local_agents.calendar_events import (
    CalendarAccessError,
    Event,
    fetch_events,
    format_events,
    next_week,
)
from local_agents.holidays import STATES, WEEKDAYS, holiday_note, is_public_holiday
from local_agents.planning import move_suggestions
from local_agents.profile import INTERNAL, Profile, ProfileError, load_profile

DEFAULT_QUESTION = "Fasse meine naechste Woche zusammen."
# Kontextbudget ist 16K: Terminliste und Zeitraum werden begrenzt.
MAX_EVENT_CHARS = 6000
MAX_RANGE_DAYS = 31
ALL_CONTEXTS = "alle"
HOLIDAY_CALENDAR_WORDS = ("feiertag", "holiday")


@dataclass
class CalendarDeps:
    """Laufzeitabhaengigkeiten: heutiges Datum, Profil und die Terminquelle."""

    today: date
    profile: Profile = field(default_factory=Profile)
    fetch: Callable[[date, date], list[Event]] = fetch_events
    # Vom Code formulierte Abschnitte (Ueberschrift -> Zeilen), die unveraendert
    # unter die Antwort des Modells gehoeren. list_events fuellt sie.
    notes: dict[str, list[str]] = field(default_factory=dict)


calendar_agent = Agent(
    deps_type=CalendarDeps,
    model_settings=MODEL_SETTINGS,
    retries=2,
)


@calendar_agent.instructions
def instructions(ctx: RunContext[CalendarDeps]) -> str:
    today = ctx.deps.today
    monday, sunday = next_week(today)
    profile = ctx.deps.profile.describe()
    contexts = (
        f"\n{profile}\n"
        "Jeder Termin traegt seinen Kontext in eckigen Klammern. Geht es nur um "
        "einen Kontext (z. B. nur Kundentermine), uebergib dessen id als context. "
        "Gliedere Zusammenfassungen nach Tag und nenne den Kontext je Termin."
        if profile
        else ""
    )
    return (
        "Du bist ein Kalender-Assistent. Hole Termine mit list_events, bevor du "
        "etwas ueber den Kalender sagst, und nenne nur Termine aus dem Ergebnis. "
        f"Heute ist {WEEKDAYS[today.weekday()]}, {today.isoformat()}. "
        f"Die naechste Woche reicht von {monday.isoformat()} bis {sunday.isoformat()}. "
        "Fasse knapp auf Deutsch zusammen: Tag fuer Tag, mit Uhrzeiten, und weise "
        "auf Ueberschneidungen und besonders volle Tage hin." + contexts
    )


@calendar_agent.tool
def list_events(
    ctx: RunContext[CalendarDeps], start: date, end: date, context: str = ALL_CONTEXTS
) -> str:
    """Listet die Kalendertermine eines Zeitraums auf, je Termin mit Kontext.

    Args:
        start: Erster Tag im Format JJJJ-MM-TT.
        end: Letzter Tag (einschliesslich) im Format JJJJ-MM-TT.
        context: "alle" oder die id eines Arbeitskontexts, um nur dessen Termine zu sehen.
    """
    profile = ctx.deps.profile
    if context != ALL_CONTEXTS and context not in profile.context_ids():
        valid = ", ".join([ALL_CONTEXTS, *profile.context_ids()])
        raise ModelRetry(f"Unbekannter context {context!r}. Erlaubt: {valid}.")
    if end < start:
        raise ModelRetry("end liegt vor start.")
    if (end - start).days >= MAX_RANGE_DAYS:
        raise ModelRetry(f"Zeitraum zu lang, hoechstens {MAX_RANGE_DAYS} Tage.")

    def context_of(event: Event) -> str:
        return profile.classify(event.title, event.organizer_domain, event.attendee_domains)

    events = relevant_events(ctx.deps.fetch(start, end), profile.state)
    # Vorschlaege betreffen interne Termine; bei einem Kundenfilter entfallen sie.
    internal_ids = {c.id for c in profile.contexts if c.kind == INTERNAL}
    suggestions = (
        move_suggestions(events, profile, context_of)
        if context == ALL_CONTEXTS or context in internal_ids
        else []
    )
    if context != ALL_CONTEXTS:
        events = [event for event in events if context_of(event) == context]

    def tag(event: Event) -> str:
        # Eintraege aus Feiertagskalendern gelten oft nur in einzelnen Bundeslaendern.
        if is_holiday_entry(event):
            return holiday_note(event.start.date(), profile.state)
        # Ohne Kontexte im Profil gibt es nichts zu unterscheiden.
        return profile.label(context_of(event)) if profile.contexts else ""

    # Hinweise gehen am Modell vorbei: Es gab sie sonst veraendert wieder.
    for heading, lines in (
        ("Feiertagshinweise", holiday_hints(events, profile.state)),
        ("Verschiebevorschläge", suggestions),
    ):
        known = ctx.deps.notes.setdefault(heading, [])
        known.extend(line for line in lines if line not in known)
    return format_events(events, MAX_EVENT_CHARS, tag=tag)


def answer(question: str, deps: CalendarDeps, model) -> str:
    """Antwort des Modells, gefolgt von den Abschnitten, die der Code formuliert."""
    return answer_with_notes(calendar_agent, question, deps, model)


def holiday_hints(events: list[Event], state: str) -> list[str]:
    """Fertig formulierte Hinweise auf Termine an arbeitsfreien Feiertagen."""
    hints = []
    for event in events:
        day = event.start.date()
        if not (state and is_holiday_entry(event) and is_public_holiday(day, state)):
            continue
        count = sum(1 for other in events if other.start.date() == day and not other.all_day)
        if count:
            meetings = "1 Termin" if count == 1 else f"{count} Termine"
            hints.append(
                f"- {WEEKDAYS[day.weekday()]} {day:%d.%m.}: {event.title} ist in "
                f"{STATES[state]} gesetzlicher Feiertag (arbeitsfrei), trotzdem "
                f"{meetings} eingetragen."
            )
    return hints


def relevant_events(events: list[Event], state: str) -> list[Event]:
    """Entfernt Feiertagseintraege, die den Nutzer nicht betreffen.

    Mit bekanntem Arbeitsort bleiben nur dort geltende gesetzliche Feiertage;
    Feiertage anderer Bundeslaender und Gedenktage entfallen. Ausserdem bleibt
    je Tag nur der Eintrag eines Feiertagskalenders, denn Outlook und macOS
    bringen je einen mit.
    """
    calendar_of_day: dict[date, str] = {}
    kept = []
    for event in events:
        if is_holiday_entry(event):
            day = event.start.date()
            if state and not is_public_holiday(day, state):
                continue
            if calendar_of_day.setdefault(day, event.calendar) != event.calendar:
                continue
        kept.append(event)
    return kept


def is_holiday_entry(event: Event) -> bool:
    """Ganztaegiger Eintrag aus einem Feiertagskalender (am Kalendernamen erkannt)."""
    name = event.calendar.casefold()
    return event.all_day and any(word in name for word in HOLIDAY_CALENDAR_WORDS)


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print(__doc__, file=sys.stderr)
        return 2
    question = argv[1] if len(argv) == 2 else DEFAULT_QUESTION
    try:
        deps = CalendarDeps(today=date.today(), profile=load_profile())
        output = answer(question, deps, ollama_model())
    except (CalendarAccessError, ProfileError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Verschiebevorschlaege: welche internen Termine dem Kundenprojekt weichen koennten.

Zwei Regeln, beide aus dem Profil gesteuert:

1. Kundentag (``onsite_days``): Alle internen Termine an einem Tag, an dem der
   Nutzer beim Kunden sein moechte, sind Kandidaten.
2. Stresslevel (``stress``): An den uebrigen Tagen sind interne Bloecke
   Kandidaten, also interne Termine am Stueck. Je hoeher das Level, desto
   kuerzere Bloecke zaehlen (siehe ``BLOCK_RULES``).

Der Code formuliert die Vorschlaege fertig; sie werden unveraendert unter die
Antwort des Modells gedruckt.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from local_agents.calendar_events import Event
from local_agents.holidays import WEEKDAYS
from local_agents.profile import CUSTOMER, INTERNAL, STRESS_LEVELS, Profile

# Stresslevel -> (Mindestdauer des Blocks in Minuten, Mindestzahl der Termine).
# "niedrig" fehlt: Dann gibt es nur die Vorschlaege fuer Kundentage.
BLOCK_RULES = {"mittel": (120, 2), "hoch": (60, 1)}
# Termine mit hoechstens dieser Pause dazwischen gelten als "am Stueck".
MAX_GAP = timedelta(minutes=30)
# Ab so vielen Eingeladenen kann der Nutzer den Termin kaum selbst verschieben.
LARGE_ROUND = 10


@dataclass(frozen=True)
class MoveGroup:
    """Interne Termine eines Kundentags oder Blocks, die weichen koennten."""

    headline: str  # Listenpunkt fuer die Uebersicht, ohne die einzelnen Termine
    reason: str  # kurze Begruendung fuer eine Aufgabe je Termin
    meetings: list[Event]


def move_suggestions(
    events: list[Event], profile: Profile, context_of: Callable[[Event], str]
) -> list[str]:
    """Fertig formulierte Vorschlaege: je Tag oder Block ein Listenpunkt mit Unterpunkten."""
    return [
        group.headline + _items(group.meetings)
        for group in move_groups(events, profile, context_of)
    ]


def is_large_round(event: Event) -> bool:
    """Ob der Nutzer den Termin wegen der vielen Eingeladenen kaum verschieben kann."""
    return len(event.attendee_domains) > LARGE_ROUND


def move_groups(
    events: list[Event], profile: Profile, context_of: Callable[[Event], str]
) -> list[MoveGroup]:
    """Die Termine hinter den Vorschlaegen, je Kundentag oder Block eine Gruppe."""
    customers = [c for c in profile.contexts if c.kind == CUSTOMER]
    internal_ids = {c.id for c in profile.contexts if c.kind == INTERNAL}
    if not customers or not internal_ids:
        return []

    by_day: dict[date, list[Event]] = {}
    seen = set()
    for event in sorted(events, key=lambda e: (e.start, e.title)):
        key = (event.start, event.end, event.title)
        # Doppelt eingetragene Termine nur einmal nennen.
        if event.all_day or key in seen or context_of(event) not in internal_ids:
            continue
        seen.add(key)
        by_day.setdefault(event.start.date(), []).append(event)

    stressed = max(customers, key=lambda c: STRESS_LEVELS.index(c.stress or STRESS_LEVELS[0]))
    rule = BLOCK_RULES.get(stressed.stress)

    groups = []
    for day, meetings in by_day.items():
        when = f"{WEEKDAYS[day.weekday()]} {day:%d.%m.}"
        onsite = [c.label for c in customers if day.weekday() in c.onsite_days]
        if onsite:
            groups.append(
                MoveGroup(
                    f"- {when} ist Kundentag ({', '.join(onsite)}), {_count(len(meetings))} "
                    "verschieben:",
                    f"Kundentag {', '.join(onsite)}",
                    meetings,
                )
            )
        elif rule:
            min_minutes, min_meetings = rule
            for block in _blocks(meetings):
                start, end = block[0].start, max(m.end for m in block)
                minutes = int((end - start).total_seconds() // 60)
                if minutes >= min_minutes and len(block) >= min_meetings:
                    groups.append(
                        MoveGroup(
                            f"- {when}: interner Block {start:%H:%M}-{end:%H:%M} "
                            f"({_count(len(block))}, {minutes} Min) verschieben, weil das "
                            f"Stresslevel bei {stressed.label} {stressed.stress} ist:",
                            f"interner Block {start:%H:%M}-{end:%H:%M}, "
                            f"Stresslevel {stressed.stress} bei {stressed.label}",
                            block,
                        )
                    )
    return groups


def _blocks(meetings: list[Event]) -> list[list[Event]]:
    """Teilt die Termine eines Tages in Bloecke ohne groessere Pause."""
    blocks: list[list[Event]] = []
    block_end = None
    for meeting in meetings:
        if block_end is None or meeting.start - block_end > MAX_GAP:
            blocks.append([])
            block_end = meeting.end
        blocks[-1].append(meeting)
        block_end = max(block_end, meeting.end)
    return blocks


def _count(n: int) -> str:
    return "1 interner Termin" if n == 1 else f"{n} interne Termine"


def _items(meetings: list[Event]) -> str:
    parts = []
    for meeting in meetings:
        text = f"\n  - {meeting.start:%H:%M}-{meeting.end:%H:%M} {meeting.title.strip()}"
        if is_large_round(meeting):
            text += f" (große Runde mit {len(meeting.attendee_domains)} Eingeladenen, eher aussetzen)"
        parts.append(text)
    return "".join(parts)

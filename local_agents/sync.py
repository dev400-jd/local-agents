"""Kalenderabgleich: bringt die Agenten-Datenbank auf den Stand des Kalenders.

Aufruf: python -m local_agents.sync

Der Abgleich laeuft ohne Modell und dauert Sekundenbruchteile. Status-Agent,
Orchestrierung und der Aufgaben-Befehl rufen ihn auf, bevor sie Aufgaben lesen
(abschaltbar mit ``sync = false`` unter ``[planning]`` im Profil).

Was er tut, fuer Termine ab heute:

- verschobene Termine: Die Aufgabe bleibt mit Status und Einordnung erhalten,
  Titel und Fertig-bis-Datum ziehen nach.
- abgesagte Termine: Offene Aufgaben werden verworfen.
- begonnene Termine, auch aus frueheren Tagen: Unerledigte Aufgaben werden
  verworfen, denn Vorbereitung oder Nachricht kaemen zu spaet.
- neue Termine an schon geplanten Tagen: Sie bekommen Aufgaben wie beim Planen.
  Ungeplante Wochen bleiben dem Planungs-Agenten ueberlassen.
"""

import sys
from dataclasses import replace
from collections.abc import Callable
from datetime import date, datetime, timedelta

from local_agents.calendar_events import CalendarAccessError, Event, fetch_events
from local_agents.holidays import WEEKDAYS
from local_agents.planner_agent import Plan, plan_events
from local_agents.profile import Profile, ProfileError, load_profile
from local_agents.store import CHANGED, NEW, Store

AGENT_NAME = "abgleich"
# So weit hinter dem letzten bekannten Termin sucht der Abgleich nach
# Terminen, die dorthin verschoben wurden.
LOOKAHEAD = timedelta(days=28)


class SyncSkipped(RuntimeError):
    """Der Kalender lieferte nichts Verwertbares; die Datenbank bleibt, wie sie ist."""


def sync(
    store: Store,
    profile: Profile,
    now: datetime,
    fetch: Callable[[date, date], list[Event]] = fetch_events,
) -> Plan | None:
    """Gleicht Termine und Aufgaben ab heute ab; None, wenn noch nichts geplant ist."""
    today = now.date()
    planned = store.planned_days(today)
    known = store.events(today, date.max)
    if not planned and not known:
        store.discard_started(now)
        return None
    end = max([*planned, *(event.start.date() for event in known)])
    events = fetch(today, end + LOOKAHEAD)
    if known and not events:
        # Ein leerer Kalender heisst fast immer, dass das Konto gerade nicht
        # geladen ist. Dann nicht alle Aufgaben verwerfen.
        raise SyncSkipped("Der Kalender liefert keine Termine.")
    plan = plan_events(
        events, profile, store, today, end, today, now=now, plannable=planned.__contains__
    )
    # Erst jetzt, mit den Beginnzeiten aus dem Kalender: Ein Termin, der auf
    # spaeter verlegt wurde, hat noch nicht begonnen.
    return replace(plan, discarded=plan.discarded + store.discard_started(now))


def _when(moment: datetime) -> str:
    return f"{WEEKDAYS[moment.weekday()]} {moment:%d.%m. %H:%M}"


def sync_lines(plan: Plan | None) -> list[str]:
    """Was der Abgleich geaendert hat; leer, wenn alles schon stimmte."""
    if plan is None:
        return []
    parts = []
    if plan.moves:
        parts.append(f"{len(plan.moves)} Termin{'e' if len(plan.moves) != 1 else ''} verschoben")
    for count, word in (
        (plan.counts[NEW], "neu"),
        (plan.counts[CHANGED], "angepasst"),
        (plan.discarded, "verworfen"),
    ):
        if count:
            parts.append(f"{count} Aufgabe{'n' if count != 1 else ''} {word}")
    if not parts:
        return []
    lines = [f"Kalender abgeglichen: {', '.join(parts)}."]
    lines += [
        f"  verschoben: {move.title} von {_when(move.old_start)} auf {_when(move.new_start)}"
        for move in plan.moves
    ]
    return lines


def refresh(
    store: Store,
    profile: Profile,
    now: datetime,
    fetch: Callable[[date, date], list[Event]] = fetch_events,
    out: Callable[[str], None] = print,
) -> Plan | None:
    """Abgleich vor dem Lesen: meldet Aenderungen und scheitert nie.

    Ist der Kalender nicht lesbar, arbeiten die Agenten mit dem gespeicherten Stand.
    """
    if not profile.sync_enabled:
        return None
    try:
        plan = sync(store, profile, now, fetch)
    except (CalendarAccessError, SyncSkipped) as exc:
        out(f"Kalender nicht abgeglichen, Aufgaben auf dem gespeicherten Stand: {exc}")
        return None
    lines = sync_lines(plan)
    if lines:
        run = store.start_run(AGENT_NAME)
        store.finish_run(run, "ok", lines[0])
        out("\n".join(lines) + "\n")
    return plan


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        profile = load_profile()
    except ProfileError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        plan = sync(Store(), profile, datetime.now())
    except (CalendarAccessError, SyncSkipped) as exc:
        print(exc, file=sys.stderr)
        return 1
    if plan is None:
        print("Noch nichts geplant, also nichts abzugleichen.")
    else:
        print("\n".join(sync_lines(plan)) or "Kalender abgeglichen: keine Änderungen.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Termine aus dem macOS-Kalender (EventKit) lesen.

Das Exchange-Konto muss in Systemeinstellungen -> Internetaccounts mit
aktiviertem Kalender eingerichtet sein. Die Termine verlassen den Rechner nicht.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

MAX_LOCATION_CHARS = 60
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


class CalendarAccessError(RuntimeError):
    """Der Kalender ist nicht lesbar (fehlende Freigabe oder kein macOS)."""


@dataclass(frozen=True)
class Event:
    """Ein Termin, reduziert auf das, was die Zusammenfassung braucht."""

    start: datetime
    end: datetime
    title: str
    all_day: bool = False
    location: str = ""
    calendar: str = ""
    # Mail-Domains des Organisators und der Teilnehmer (je Person einmal),
    # ohne Namen oder Adressen.
    organizer_domain: str = ""
    attendee_domains: tuple[str, ...] = ()


def next_week(today: date) -> tuple[date, date]:
    """Montag und Sonntag der Woche nach ``today``."""
    monday = today + timedelta(days=7 - today.weekday())
    return monday, monday + timedelta(days=6)


def format_events(
    events: list[Event], max_chars: int, tag: Callable[[Event], str] | None = None
) -> str:
    """Kompakte Textliste, nach Tagen gruppiert, fuer das Kontextbudget gekuerzt.

    ``tag`` liefert optional eine Markierung je Termin, etwa den Arbeitskontext.
    """
    if not events:
        return "Keine Termine im Zeitraum."
    lines: list[str] = []
    current_day: date | None = None
    for event in sorted(events, key=lambda e: (e.start, e.title)):
        day = event.start.date()
        if day != current_day:
            current_day = day
            lines.append(f"{WEEKDAYS[day.weekday()]} {day:%d.%m.%Y}")
        when = "ganztaegig" if event.all_day else f"{event.start:%H:%M}-{event.end:%H:%M}"
        label = tag(event) if tag else ""
        marker = f"[{label}] " if label else ""
        line = f"  {when} {marker}{event.title}"
        if event.location:
            line += f" (Ort: {event.location[:MAX_LOCATION_CHARS]})"
        lines.append(line)
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[:max_chars] + f"\n[gekuerzt: {len(events)} Termine insgesamt]"
    return text


def fetch_events(start: date, end: date) -> list[Event]:
    """Liest alle Termine von ``start`` bis einschliesslich ``end`` aus EventKit."""
    try:
        import EventKit
        from Foundation import NSDate
    except ImportError as exc:
        raise CalendarAccessError(
            "EventKit fehlt. Installation: pip install -e '.[calendar]' (nur macOS)."
        ) from exc

    store = EventKit.EKEventStore.alloc().init()
    _request_access(store)

    def ns_date(day: date, at: time) -> "NSDate":
        return NSDate.dateWithTimeIntervalSince1970_(datetime.combine(day, at).timestamp())

    predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
        ns_date(start, time.min), ns_date(end, time.max), None
    )

    def local(ns: "NSDate") -> datetime:
        return datetime.fromtimestamp(ns.timeIntervalSince1970())

    return [
        Event(
            start=local(item.startDate()),
            end=local(item.endDate()),
            title=str(item.title() or "(ohne Titel)"),
            all_day=bool(item.isAllDay()),
            location=str(item.location() or ""),
            calendar=str(item.calendar().title() or ""),
            organizer_domain=_domain(item.organizer()),
            attendee_domains=tuple(
                domain for domain in map(_domain, item.attendees() or []) if domain
            ),
        )
        for item in store.eventsMatchingPredicate_(predicate) or []
        if item.status() != EventKit.EKEventStatusCanceled
    ]


def _domain(participant) -> str:
    """Mail-Domain eines EventKit-Teilnehmers, leer ohne Mailadresse."""
    url = "" if participant is None else str(participant.URL() or "")
    return url.rsplit("@", 1)[1].lower() if "@" in url else ""


def _request_access(store) -> None:
    """Fragt einmalig die Kalenderfreigabe an; macOS zeigt dazu einen Dialog."""
    done = threading.Event()
    result: dict[str, object] = {}

    def handler(granted: bool, error) -> None:
        result["granted"] = granted
        result["error"] = error
        done.set()

    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(handler)
    else:  # vor macOS 14
        store.requestAccessToEntityType_completion_(0, handler)
    if not done.wait(timeout=120):
        raise CalendarAccessError("Keine Antwort auf die Kalenderfreigabe.")
    if not result.get("granted"):
        raise CalendarAccessError(
            "Kein Zugriff auf den Kalender. Freigabe erteilen unter "
            "Systemeinstellungen -> Datenschutz & Sicherheit -> Kalender "
            "(fuer das Terminal, aus dem der Agent gestartet wird)."
        )

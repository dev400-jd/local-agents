"""Gesprochene Kurzfassung: was von den offenen Aufgaben als Naechstes ansteht.

Die vollstaendige Liste steht im Terminal; vorgelesen wird nur ein Ueberblick
in wenigen Saetzen. Der Code formuliert ihn aus festen Satzbausteinen. Ein
Versuch, den Text vom lokalen Modell schreiben zu lassen, ergab schiefe
Grammatik und einmal eine erfundene Uhrzeit (gpt-oss:20b, 2026-11-15).

Die Saetze beschreiben, was eingeplant ist, nicht was der Nutzer tun muss: Die
Aufgaben sollen spaeter Agenten erledigen. Am Ende steht eine Rueckfrage.
"""

from collections import Counter
from datetime import date, timedelta

from local_agents.store import StoredEvent, Task

DAY_NAMES = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
NUMBERS = (
    "null", "eine", "zwei", "drei", "vier", "fünf", "sechs",
    "sieben", "acht", "neun", "zehn", "elf", "zwölf",
)  # fmt: skip
MOVE = "verschieben"
# Mehr Namen nennt ein Satz nicht einzeln.
MAX_NAMES = 3
CLOSING_QUESTION = "Passt das so für dich?"


def spoken_summary(tasks: list[Task], events: list[StoredEvent], today: date) -> str:
    """Wenige Saetze zum Vorlesen ueber die offenen Aufgaben ``tasks``."""
    if not tasks:
        return "Es sind keine Aufgaben offen."
    by_id = {event.id: event for event in events}
    tomorrow = today + timedelta(days=1)
    now = [task for task in tasks if task.due_date <= today]

    def of_kind(prefix: str) -> list[Task]:
        return [task for task in now if task.kind == MOVE and task.title.startswith(prefix)]

    # Die Art der Verschiebe-Aufgabe steht am Anfang ihres Titels (planner_agent.move_title).
    messages, declines, own = of_kind("Nachricht an"), of_kind("Absage an"), of_kind("Selbst")
    preparations = [task for task in now if task.kind != MOVE]

    sentences = []
    if messages:
        one = len(messages) == 1
        sentences.append(
            f"Für heute {'ist' if one else 'sind'} "
            f"{_count(len(messages), 'Nachricht', 'Nachrichten')} eingeplant, "
            f"{_recipients(messages)}, damit {'der interne Termin' if one else 'die internen Termine'} "
            f"{_days(messages, by_id)} verschoben {'wird' if one else 'werden'}."
        )
    if declines:
        one = len(declines) == 1
        count = _count(len(declines), "Absage", "Absagen")
        target = (
            f"{_recipients(declines)} für {'die große Runde' if one else 'die großen Runden'} "
            f"{_days(declines, by_id)}"
        )
        if sentences:
            sentences.append(f"Dazu {'kommt' if one else 'kommen'} {count} {target}.")
        else:
            sentences.append(f"Für heute {'ist' if one else 'sind'} {count} eingeplant, {target}.")
    if own:
        event = by_id.get(own[0].event_id or "")
        if len(own) == 1 and event:
            what = f"dein eigener Termin „{event.title}“ {_days(own, by_id)} verschoben wird"
        elif len(own) == 1:
            what = "ein eigener Termin von dir verschoben wird"
        else:
            what = f"{_number(len(own))} eigene Termine von dir {_days(own, by_id)} verschoben werden"
        sentences.append(f"{'Außerdem' if sentences else 'Für heute'} ist eingeplant, dass {what}.")
    if preparations:
        one = len(preparations) == 1
        count = _count(len(preparations), "Vorbereitung", "Vorbereitungen")
        target = f"für {'den Termin' if one else 'die Termine'} {_days(preparations, by_id)}"
        if sentences:
            sentences.append(f"Hinzu {'kommt' if one else 'kommen'} {count} {target}.")
        else:
            sentences.append(f"Für heute {'ist' if one else 'sind'} {count} {target} eingeplant.")
    if not sentences:
        sentences.append("Für heute ist nichts eingeplant.")

    next_day = sum(task.due_date == tomorrow for task in tasks)
    later = sum(task.due_date > tomorrow for task in tasks)
    if next_day and later:
        verb = "folgt" if next_day == 1 else "folgen"
        sentences.append(
            f"Morgen {verb} {_count(next_day, 'weitere Aufgabe', 'weitere Aufgaben')}, "
            f"danach noch {_number(later)}."
        )
    elif next_day or later:
        count = next_day or later
        verb = "folgt" if count == 1 else "folgen"
        when = "Morgen" if next_day else "In den Tagen danach"
        sentences.append(f"{when} {verb} {_count(count, 'weitere Aufgabe', 'weitere Aufgaben')}.")

    per_day = Counter(event.start.date() for event in events if event.start.date() >= today)
    if len(per_day) > 1:
        day, count = max(sorted(per_day.items()), key=lambda item: item[1])
        sentences.append(
            f"Der vollste Tag ist {DAY_NAMES[day.weekday()]} mit "
            f"{'einem geplanten Termin' if count == 1 else f'{_number(count)} geplanten Terminen'}."
        )
    sentences.append(CLOSING_QUESTION)
    return " ".join(sentences)


def _number(n: int) -> str:
    return NUMBERS[n] if n < len(NUMBERS) else str(n)


def _count(n: int, singular: str, plural: str) -> str:
    return f"eine {singular}" if n == 1 else f"{_number(n)} {plural}"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} und {items[-1]}"


def _recipients(tasks: list[Task]) -> str:
    """"an Erika Muster und Max Beispiel", aus den Titeln "Nachricht an <Name>: ..."."""
    names = []
    for task in tasks:
        name = task.title.split(":", 1)[0].split(" an ", 1)[-1].strip()
        name = "den Organisator" if name == "Organisator" else name
        if name not in names:
            names.append(name)
    if len(names) > MAX_NAMES:
        rest = len(names) - (MAX_NAMES - 1)
        names = names[: MAX_NAMES - 1] + [f"{_number(rest)} weitere"]
    return f"an {_join(names)}"


def _days(tasks: list[Task], events: dict[str, StoredEvent]) -> str:
    """"am Mittwoch und Donnerstag": die Wochentage der zugehoerigen Termine."""
    days = sorted({events[task.event_id].start.date() for task in tasks if task.event_id in events})
    names = list(dict.fromkeys(DAY_NAMES[day.weekday()] for day in days))
    return f"am {_join(names)}" if names else "in dieser Woche"

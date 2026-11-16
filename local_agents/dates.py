"""Datumsangaben in deutschem Freitext erkennen.

Kleine Modelle rechnen Wochentage unzuverlaessig in ein Datum um. Deshalb liest
der Code die Angaben selbst; das Modell muss nur die Worte des Nutzers wiedergeben.
"""

import re
from datetime import date, timedelta

DAY_WORDS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")


def mentioned_dates(text: str, today: date) -> list[date]:
    """Tage, die der Text nennt: heute, morgen, Wochentage, "20.11.", "am 20." oder "bis zum 20."."""
    lowered = text.casefold()
    found = []
    for word, days in (("übermorgen", 2), ("morgen", 1), ("heute", 0)):
        if re.search(rf"(?<!\w){word}(?!\w)", lowered):
            found.append(today + timedelta(days=days))
            lowered = lowered.replace(word, " ")
    for number, name in enumerate(DAY_WORDS):
        if re.search(rf"(?<!\w){name}s?(?!\w)", lowered):
            # Der naechste solche Tag; der heutige Wochentag meint die kommende Woche.
            found.append(today + timedelta(days=(number - today.weekday()) % 7 or 7))
    for match in re.finditer(r"(?<!\d)(\d{1,2})\.(?:\s?(\d{1,2})\.(?:(\d{4}))?)?(?!\d)", lowered):
        day, month, year = match.group(1), match.group(2), match.group(3)
        if month is None and not re.search(r"\b(am|zum|den)\s*$", lowered[: match.start()]):
            continue  # eine nackte Zahl mit Punkt ist kein Datum
        try:
            candidate = date(int(year or today.year), int(month or today.month), int(day))
        except ValueError:
            continue
        if candidate < today and year is None:
            # Ohne Jahr ist der naechste solche Tag gemeint.
            if month is None:
                next_month = today.replace(day=28) + timedelta(days=4)
                try:
                    candidate = date(next_month.year, next_month.month, int(day))
                except ValueError:
                    continue
            else:
                candidate = candidate.replace(year=today.year + 1)
        found.append(candidate)
    return sorted(set(found))

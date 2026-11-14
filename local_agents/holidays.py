"""Gesetzliche Feiertage in Deutschland je Bundesland, ohne Netz berechnet.

Feiertagskalender (Outlook, macOS) zeigen alle deutschen Feiertage, auch die,
die nur in einzelnen Bundeslaendern gelten. Dieses Modul beantwortet, ob ein
Datum am Standort des Nutzers tatsaechlich arbeitsfrei ist.

Vereinfachung: Feiertage, die nur in einzelnen Gemeinden gelten (Mariae
Himmelfahrt in Teilen Bayerns, Fronleichnam in Teilen Sachsens und Thueringens,
Augsburger Friedensfest), zaehlen fuer das ganze Land bzw. gar nicht.
"""

from datetime import date, timedelta
from functools import lru_cache

STATES = {
    "BW": "Baden-Wuerttemberg",
    "BY": "Bayern",
    "BE": "Berlin",
    "BB": "Brandenburg",
    "HB": "Bremen",
    "HH": "Hamburg",
    "HE": "Hessen",
    "MV": "Mecklenburg-Vorpommern",
    "NI": "Niedersachsen",
    "NW": "Nordrhein-Westfalen",
    "RP": "Rheinland-Pfalz",
    "SL": "Saarland",
    "SN": "Sachsen",
    "ST": "Sachsen-Anhalt",
    "SH": "Schleswig-Holstein",
    "TH": "Thueringen",
}
ALL_STATES = frozenset(STATES)
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


def easter_sunday(year: int) -> date:
    """Ostersonntag nach der gregorianischen Osterformel (Meeus/Jones/Butcher)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    month = (h + l - 7 * m + 90) // 25
    day = (h + l - 7 * m + 33 * month + 19) % 32
    return date(year, month, day)


@lru_cache(maxsize=16)
def public_holidays(year: int) -> dict[date, tuple[str, frozenset[str]]]:
    """Alle gesetzlichen Feiertage eines Jahres: Datum -> (Name, Bundeslaender)."""
    easter = easter_sunday(year)
    november_22 = date(year, 11, 22)
    # Buss- und Bettag: der Mittwoch vor dem 23. November.
    repentance_day = november_22 - timedelta(days=(november_22.weekday() - 2) % 7)

    def states(*codes: str) -> frozenset[str]:
        return frozenset(codes)

    return {
        date(year, 1, 1): ("Neujahr", ALL_STATES),
        date(year, 1, 6): ("Heilige Drei Koenige", states("BW", "BY", "ST")),
        date(year, 3, 8): ("Internationaler Frauentag", states("BE", "MV")),
        easter - timedelta(days=2): ("Karfreitag", ALL_STATES),
        easter: ("Ostersonntag", states("BB")),
        easter + timedelta(days=1): ("Ostermontag", ALL_STATES),
        date(year, 5, 1): ("Tag der Arbeit", ALL_STATES),
        easter + timedelta(days=39): ("Christi Himmelfahrt", ALL_STATES),
        easter + timedelta(days=49): ("Pfingstsonntag", states("BB")),
        easter + timedelta(days=50): ("Pfingstmontag", ALL_STATES),
        easter + timedelta(days=60): ("Fronleichnam", states("BW", "BY", "HE", "NW", "RP", "SL")),
        date(year, 8, 15): ("Mariae Himmelfahrt", states("BY", "SL")),
        date(year, 9, 20): ("Weltkindertag", states("TH")),
        date(year, 10, 3): ("Tag der Deutschen Einheit", ALL_STATES),
        date(year, 10, 31): (
            "Reformationstag",
            states("BB", "HB", "HH", "MV", "NI", "SN", "ST", "SH", "TH"),
        ),
        date(year, 11, 1): ("Allerheiligen", states("BW", "BY", "NW", "RP", "SL")),
        repentance_day: ("Buss- und Bettag", states("SN")),
        date(year, 12, 25): ("1. Weihnachtstag", ALL_STATES),
        date(year, 12, 26): ("2. Weihnachtstag", ALL_STATES),
    }


def is_public_holiday(day: date, state: str) -> bool:
    """Ob ``day`` im Bundesland ``state`` (Kuerzel) gesetzlicher Feiertag ist."""
    holiday = public_holidays(day.year).get(day)
    return holiday is not None and state in holiday[1]


def holiday_note(day: date, state: str = "") -> str:
    """Kurzer Hinweis, ob ``day`` gesetzlicher Feiertag ist, bezogen auf ``state``.

    Ohne ``state`` nennt der Hinweis nur, wo der Tag Feiertag ist.
    """
    holiday = public_holidays(day.year).get(day)
    weekend = day.weekday() >= 5
    if holiday is None:
        return "kein gesetzlicher Feiertag" + (", Wochenende" if weekend else "")
    _, where = holiday
    if state in where:
        return f"gesetzlicher Feiertag in {STATES[state]}, arbeitsfrei"
    if where == ALL_STATES:
        return "bundesweiter gesetzlicher Feiertag"
    only = ", ".join(STATES[code] for code in sorted(where, key=STATES.get))
    if state:
        kind = "Wochenende" if weekend else "normaler Arbeitstag"
        return f"KEIN Feiertag in {STATES[state]}, {kind}; gilt nur in {only}"
    return f"gesetzlicher Feiertag nur in {only}"

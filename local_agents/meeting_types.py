"""Katalog der Terminarten: Wozu ein Termin dient und was man dort beitraegt.

Das ist das Fachwissen des Termin-Agenten ueber uebliche Besprechungsformate,
auch aus Scrum. Der Katalog ist fest; das Modell ordnet einen Termin nur einer
Art zu. Neue Arten hier ergaenzen.
"""

from dataclasses import dataclass

OTHER_TYPE = "sonstiges"


@dataclass(frozen=True)
class MeetingType:
    id: str
    label: str
    purpose: str  # wozu der Termin dient
    contributions: tuple[str, ...]  # was ein Teilnehmer ueblicherweise mitbringt
    keywords: tuple[str, ...] = ()  # Hinweise im Titel, klein geschrieben
    framework: str = ""  # z. B. "Scrum"


MEETING_TYPES = (
    MeetingType(
        "daily",
        "Daily / Stand-up",
        "Das Team stimmt sich in etwa 15 Minuten ab: Fortschritt zum Sprintziel, Plan bis zum "
        "nächsten Daily, Hindernisse. Kein Ort für Detaildiskussionen.",
        (
            "Was seit dem letzten Daily erreicht wurde",
            "Was bis zum nächsten Daily ansteht",
            "Hindernisse und offene Punkte, bei denen andere helfen oder entscheiden müssen",
        ),
        ("daily", "stand-up", "standup", "stand up"),
        "Scrum",
    ),
    MeetingType(
        "sprint_planning",
        "Sprint Planning",
        "Das Team legt Sprintziel und Umfang des nächsten Sprints fest.",
        (
            "Einschätzung, was im Sprint machbar ist (Kapazität, Abhängigkeiten)",
            "Vorschläge für das Sprintziel und die wichtigsten Einträge",
            "Offene fachliche oder technische Fragen zu den Einträgen",
        ),
        ("planning", "sprintplanung", "sprint-planung"),
        "Scrum",
    ),
    MeetingType(
        "sprint_review",
        "Sprint Review / Demo",
        "Das Team zeigt den Stakeholdern, was im Sprint fertig wurde, und holt Rückmeldung ein.",
        (
            "Vorführbare Ergebnisse des Sprints",
            "Was nicht fertig wurde und warum",
            "Fragen an die Stakeholder für die nächsten Schritte",
        ),
        ("review", "demo"),
        "Scrum",
    ),
    MeetingType(
        "retrospektive",
        "Retrospektive",
        "Das Team blickt auf die Zusammenarbeit zurück und vereinbart Verbesserungen.",
        (
            "Was gut lief und beibehalten werden soll",
            "Was gestört hat, mit konkretem Beispiel",
            "Ein Vorschlag für eine Verbesserung",
        ),
        ("retro",),
        "Scrum",
    ),
    MeetingType(
        "refinement",
        "Backlog Refinement",
        "Das Team klärt und schätzt kommende Einträge, damit sie planbar werden.",
        (
            "Technische Einschätzung der Einträge: Aufwand, Risiken, Abhängigkeiten",
            "Offene Fragen an den Product Owner",
            "Vorschläge, große Einträge zu teilen",
        ),
        ("refinement", "grooming", "backlog"),
        "Scrum",
    ),
    MeetingType(
        "jour_fixe",
        "Jour fixe / regelmäßige Abstimmung",
        "Wiederkehrender Termin, um Stand, anstehende Themen und Entscheidungen abzugleichen.",
        (
            "Kurzer Stand der eigenen Themen seit dem letzten Termin",
            "Punkte, die eine Entscheidung oder Abstimmung brauchen",
            "Ausblick auf die nächsten Schritte und Termine",
        ),
        ("jour fixe", "jourfixe", "jf ", "sync", "weekly", "austausch", "abstimmung", "regeltermin"),
    ),
    MeetingType(
        "einzelgespraech",
        "Einzelgespräch (1:1)",
        "Zwei Personen besprechen Zusammenarbeit, Entwicklung und aktuelle Anliegen.",
        (
            "Eigene Themen und Anliegen",
            "Rückmeldung an die andere Person",
            "Stand der zuletzt vereinbarten Punkte",
        ),
        ("1:1", "1on1", "one on one", "einzelgespräch", "mitarbeitergespräch", "feedbackgespräch"),
    ),
    MeetingType(
        "workshop",
        "Workshop / Arbeitstermin",
        "Die Runde erarbeitet gemeinsam ein Ergebnis, etwa ein Konzept, eine Lösung oder einen Plan.",
        (
            "Vorbereitete Ausgangslage: bekannte Fakten, Rahmenbedingungen, Ziel des Termins",
            "Eigene Vorschläge oder ein Entwurf als Diskussionsgrundlage",
            "Offene Fragen, die im Termin geklärt werden sollen",
        ),
        ("workshop", "brainstorming", "arbeitstermin", "working session", "konzept", "design session"),
    ),
    MeetingType(
        "problemklaerung",
        "Problemklärung / Eskalation",
        "Die Beteiligten klären, was schiefläuft, und vereinbaren Maßnahmen.",
        (
            "Sachliche Darstellung des Problems mit Auswirkungen und bisherigen Schritten",
            "Eigene Einschätzung der Ursachen",
            "Konkrete Lösungsvorschläge und was dafür von wem gebraucht wird",
        ),
        ("eskalation", "going wrong", "problem", "krise", "störung", "incident", "post mortem", "postmortem"),
    ),
    MeetingType(
        "steuerung",
        "Statusbericht / Lenkungsausschuss",
        "Auftraggeber und Leitung erhalten den Projektstand und treffen Entscheidungen.",
        (
            "Stand zu Terminen, Umfang, Budget und Risiken, knapp und belegbar",
            "Entscheidungsvorlagen mit Optionen und Empfehlung",
            "Bedarf an Unterstützung durch die Leitung",
        ),
        ("lenkungsausschuss", "steering", "statusbericht", "status meeting", "statusmeeting", "steuerkreis", "projektstatus"),
    ),
    MeetingType(
        "kickoff",
        "Kick-off",
        "Start eines Vorhabens: Ziele, Rollen, Vorgehen und nächste Schritte werden geklärt.",
        (
            "Eigene Rolle und Verantwortung im Vorhaben",
            "Erwartungen, Rahmenbedingungen und bekannte Risiken",
            "Vorschlag für die ersten Schritte",
        ),
        ("kick-off", "kickoff", "auftakt"),
    ),
    MeetingType(
        "praesentation",
        "Präsentation / Vorstellung",
        "Jemand stellt ein Ergebnis, ein Angebot oder ein Thema vor; die Runde fragt nach und bewertet.",
        (
            "Als Vortragender: Kernaussagen, Unterlagen und erwartete Rückfragen",
            "Als Zuhörer: eigene Fragen und die Entscheidung, die danach ansteht",
        ),
        ("präsentation", "vorstellung", "pitch"),
    ),
    MeetingType(
        "grosse_runde",
        "Große Runde / Informationstermin",
        "Viele Teilnehmer werden informiert; eigene Beiträge sind die Ausnahme.",
        (
            "In der Regel nichts; höchstens eine Frage oder ein eigener kurzer Beitrag, falls angefragt",
        ),
        ("company call", "all hands", "all-hands", "town hall", "townhall", "team meeting", "get together", "betriebsversammlung"),
    ),
    MeetingType(
        OTHER_TYPE,
        "Sonstiger Termin",
        "Keine der bekannten Terminarten passt eindeutig.",
        ("Ziel des Termins klären und was von dir erwartet wird",),
    ),
)

BY_ID = {meeting_type.id: meeting_type for meeting_type in MEETING_TYPES}


def keyword_hint(title: str) -> MeetingType | None:
    """Die erste Terminart, deren Stichwort im Titel vorkommt."""
    lowered = title.casefold()
    for meeting_type in MEETING_TYPES:
        if any(keyword in lowered for keyword in meeting_type.keywords):
            return meeting_type
    return None

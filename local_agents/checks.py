"""Pruefungen an Texten, die ein Modell geschrieben hat."""

import re

NUMBER = re.compile(r"\d+")


def invented_numbers(answer: str, facts: str) -> list[str]:
    """Zahlen der Antwort, die in den Fakten nicht vorkommen (auch Teile von Datum und Uhrzeit).

    Ein kleines Modell erfindet gern Angaben wie "in 6 Minuten". Jede Ziffernfolge
    der Antwort muss deshalb auch in dem stehen, was das Modell zu sehen bekam.
    """
    known = set(NUMBER.findall(facts))
    return [number for number in dict.fromkeys(NUMBER.findall(answer)) if number not in known]

# Feature: Bestellimport mit Qualitätsprüfung

Version 1.0.0 · Stand 01.11.2026

**Ziel:** Bestellungen aus dem Shop landen geprüft in der Datenplattform.

**Abgrenzung:** Keine Rechnungsdaten.

---

## Überblick

| Thema | Inhalt |
|---|---|
| Quelle | Shop-Export |

---

## QG-1: Prüfregeln für `bestellung_roh`

**Story**

Als Datenverantwortliche möchte ich fehlerhafte Bestellungen erkennen, damit Berichte stimmen.

**Schätzung:** 3 PT

**Fachlicher Inhalt**

| Thema | Inhalt |
|---|---|
| Pflichtfelder | Kunde, Datum |

**Voraussetzungen**

| Nr. | Art | Voraussetzung |
|---|---|---|
| 1 | Daten | Shop-Export liegt vor |

**Akzeptanzkriterien**

| Nr. | Kriterium | Nachweis |
|---|---|---|
| 1 | Leere Pflichtfelder werden gemeldet | Testlauf |
| 2 | Bericht nennt Anzahl a \| b | Protokoll |

**Aufgaben**

| Nr. | Aufgabe | Aufwand | Rolle |
|---|---|---|---|
| 1 | Regeln festlegen | 1 PT | Solution Architect |
| 2 | Regeln umsetzen | 2 PT | Entwicklung |

---

## tr-2: Bericht bereitstellen

**Story**

Als Teamleitung möchte ich einen Bericht sehen.

**Schätzung:** 1 PT

---

## Offene Punkte

Noch zu klären: Aufbewahrungsfrist.

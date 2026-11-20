# Anbindung der Lieferanten

Stand November 2026. Erfundenes Beispiel für die Messung der Suche.

## Ziel

Lieferanten sollen ihre Daten ohne manuelle Schritte einliefern können.

## Architekturentscheidung

Dateien kommen über einen gesicherten Dateitransfer, große Lieferanten zusätzlich
über eine Programmierschnittstelle. Beide Wege enden in derselben Eingangszone.

## AN-1: Dateitransfer für kleine Lieferanten

**Aufwand:** 5 PT

Als kleiner Lieferant möchte ich meine Lieferscheine als CSV-Datei hochladen,
damit ich keine eigene Software brauche.

### Akzeptanzkriterien

- Jeder Lieferant hat ein eigenes Verzeichnis und eigene Zugangsdaten.
- Nach dem Hochladen kommt eine Empfangsbestätigung.

## AN-2: Schnittstelle für große Lieferanten

**Aufwand:** 13 PT

Als großer Lieferant möchte ich Lieferungen über eine Schnittstelle melden,
damit meine Warenwirtschaft das automatisch erledigt.

### Akzeptanzkriterien

- Die Schnittstelle nimmt einzelne Lieferungen und Stapel an.
- Fehler kommen mit einer verständlichen Meldung zurück.

### Abhängigkeiten

Braucht die Prüfregeln aus QG-1 und die Zugangsverwaltung aus AN-3.

## AN-3: Zugangsverwaltung und Berechtigungen

**Aufwand:** 8 PT

Als Administrator möchte ich Zugänge für Lieferanten anlegen und sperren, damit
nur berechtigte Partner Daten einliefern.

### Akzeptanzkriterien

- Zugänge laufen nach 12 Monaten ohne Nutzung ab.
- Jede Anmeldung wird protokolliert.

## AN-4: Überwachung der Einlieferungen

**Aufwand:** 5 PT

Als Betriebsverantwortlicher möchte ich sehen, welcher Lieferant heute noch
nichts geliefert hat, damit ich rechtzeitig nachfragen kann.

### Akzeptanzkriterien

- Eine Übersicht zeigt je Lieferant die letzte Einlieferung.
- Bleibt eine erwartete Lieferung bis 10 Uhr aus, gibt es eine Warnung.

# Datenqualität im Wareneingang

Stand November 2026. Erfundenes Beispiel für die Messung der Suche.

## Ziel

Fehlerhafte Lieferdaten sollen auffallen, bevor sie in die Auswertungen gelangen.
Verantwortlich ist das Team Datenplattform.

## Offene Fragen

- Wer pflegt die Schwellenwerte, wenn sich das Sortiment ändert?
- Reicht ein täglicher Lauf oder braucht der Einkauf stündliche Zahlen?

## QG-1: Prüfregeln für Lieferdaten festlegen

**Aufwand:** 5 PT

Als Datenverantwortliche möchte ich Prüfregeln für eingehende Lieferdaten
festlegen, damit unvollständige Datensätze gar nicht erst geladen werden.

### Akzeptanzkriterien

- Pflichtfelder sind je Lieferant hinterlegt.
- Eine Lieferung ohne Artikelnummer wird abgewiesen.
- Regeln lassen sich ohne Entwickler ändern.

## QG-2: Wochenbericht über Regelverstöße

**Aufwand:** 3 PT

Als Leiterin Einkauf möchte ich jeden Montag einen Bericht über alle Verstöße
gegen die Prüfregeln der Vorwoche, damit ich Lieferanten gezielt ansprechen kann.

### Akzeptanzkriterien

- Der Bericht nennt Lieferant, Regel und Anzahl der Verstöße.
- Er kommt als PDF per Mail.

## QG-3: Quarantäne für abgewiesene Lieferungen

**Aufwand:** 8 PT

Als Lagerleitung möchte ich abgewiesene Lieferungen in einem eigenen Bereich
sehen, damit nichts verloren geht und wir nacharbeiten können.

### Akzeptanzkriterien

- Abgewiesene Datensätze bleiben 30 Tage erhalten.
- Nach der Korrektur lassen sie sich erneut einspielen.

### Technische Hinweise

Die Quarantäne liegt in einem eigenen Schema; die Aufbewahrungsfrist ist ein
Parameter des nächtlichen Aufräumlaufs.

## QG-4: Schwellenwerte für Ausreißer

**Aufwand:** 5 PT

Als Analyst möchte ich Schwellenwerte für ungewöhnliche Mengen und Preise
hinterlegen, damit Ausreißer markiert statt abgewiesen werden.

### Akzeptanzkriterien

- Markierte Datensätze erscheinen in den Auswertungen mit einem Hinweis.
- Die Schwellenwerte gelten je Warengruppe.

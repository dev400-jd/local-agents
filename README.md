# local-agents

Eigene Agenten mit [Pydantic AI](https://ai.pydantic.dev/) und einem lokalen
Ollama-Modell (`gpt-oss:20b`, 16K Kontext). Ollama-Einrichtung, Speichergrenzen
und Modellerfahrungen stehen im Schwesterprojekt `opencode-ollama-agents`
(`docs/setup-macos.md`, `docs/performance.md`).

| Baustein | Datei | Zweck |
|---|---|---|
| Datei-Agent | `local_agents/agent.py` | beantwortet Fragen zu Dateien eines Verzeichnisses |
| Kalender-Agent | `local_agents/calendar_agent.py` | fasst Termine aus dem macOS-Kalender zusammen |
| Terminquelle | `local_agents/calendar_events.py` | liest Termine über EventKit |
| Profil | `local_agents/profile.py` | gemeinsames Gedächtnis: Person, Arbeitsort, Arbeitskontexte |
| Feiertage | `local_agents/holidays.py` | gesetzliche Feiertage je Bundesland |
| Planung | `local_agents/planning.py` | Verschiebevorschläge für interne Termine |

## Einrichtung

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Tests

Laufen ohne Ollama; ein geskriptetes Modell ersetzt das lokale Modell.

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Agent ausführen

Ollama muss mit 16K Kontext laufen, sonst gelten stillschweigend 4096 Tokens:

```bash
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
```

Dann in einem zweiten Terminal:

```bash
.venv/bin/python -m local_agents "Was steht in der README.md?" .
```

Der Agent (`local_agents/agent.py`) hat ein Werkzeug, `read_file`. Es liest nur
Dateien innerhalb des angegebenen Verzeichnisses und kürzt Inhalte auf 8000
Zeichen, damit das Kontextbudget hält.

Anderes Modell: `LOCAL_AGENTS_MODEL=<name>` setzen. Alle Agenten sollten
dasselbe Modell nutzen und nacheinander laufen; zwei geladene Modelle passen
nicht in 24 GB.

## Kalender-Agent

Fasst Termine aus dem macOS-Kalender zusammen, ohne Frage die kommende Woche:

```bash
.venv/bin/pip install -e '.[calendar]'
.venv/bin/python -m local_agents.calendar_agent
.venv/bin/python -m local_agents.calendar_agent "Was habe ich am Dienstag?"
```

Der Agent (`local_agents/calendar_agent.py`) hat ein Werkzeug, `list_events`,
für höchstens 31 Tage. Abgesagte Termine werden ausgelassen, die Terminliste
wird auf 6000 Zeichen gekürzt. Termine gehen nur an das lokale Modell.

Outlook selbst wird nicht angesprochen: Das neue Outlook gibt per AppleScript
keine Termine heraus. Der Agent liest stattdessen den macOS-Kalender, in den
das Exchange-Konto eingebunden wird.

### Kalender in den Systemeinstellungen freigeben

Zwei Dinge müssen einmalig eingerichtet sein: das Konto und die Freigabe.

**1. Exchange-Konto in macOS einbinden**

1. Systemeinstellungen → Internetaccounts → „Account hinzufügen …" →
   „Microsoft Exchange".
2. Name und dienstliche E-Mail-Adresse eingeben, „Anmelden" wählen und im
   Microsoft-Fenster wie gewohnt anmelden (inklusive MFA).
3. In der App-Auswahl nur „Kalender" aktivieren; Mail, Kontakte, Erinnerungen
   und Notizen braucht der Agent nicht.
4. Prüfen: In der App „Kalender" erscheint links das Exchange-Konto, und die
   Termine sind sichtbar. Die erste Synchronisierung kann einige Minuten dauern.

Ist das Konto schon vorhanden, genügt es, unter Internetaccounts → Konto den
Schalter „Kalender" einzuschalten. Lässt sich das Konto nicht hinzufügen,
sperrt vermutlich eine Richtlinie der IT den Zugriff.

**2. Dem Terminal den Kalenderzugriff erlauben**

Die Freigabe gilt für das Programm, aus dem der Agent gestartet wird (Terminal,
iTerm, VS Code …), nicht für Python.

1. Agent einmal starten. macOS fragt: „… möchte auf deinen Kalender zugreifen".
   „Vollzugriff erlauben" wählen.
2. Kam kein Dialog oder wurde er abgelehnt: Systemeinstellungen → Datenschutz &
   Sicherheit → Kalender, beim Terminal-Programm „Vollzugriff" einstellen
   („Nur Termine hinzufügen" reicht nicht, der Agent muss lesen).
3. Terminal-Programm beenden und neu starten, dann den Agenten erneut ausführen.

Das Terminal-Programm taucht in der Liste erst auf, nachdem der Agent einmal
gestartet wurde. Fehlt es trotzdem oder ist die Freigabe verklemmt, setzt
`tccutil reset Calendar` alle Kalenderfreigaben zurück; der nächste Lauf fragt
dann neu.

**Fehlerbilder**

- „Kein Zugriff auf den Kalender": Schritt 2 fehlt.
- „Keine Termine im Zeitraum", obwohl Termine existieren: Schritt 1 fehlt oder
  die Synchronisierung läuft noch; in der App „Kalender" nachsehen.

## Profil: gemeinsames Gedächtnis der Agenten

`local_agents/profile.py` lädt ein Profil, das beschreibt, wer der Nutzer ist
und in welchen Arbeitskontexten er sich bewegt (intern, je Kunde ein Kontext).
Es liegt außerhalb des Repos, weil es Kundennamen enthält:

```bash
mkdir -p ~/.config/local-agents
cp profile.example.toml ~/.config/local-agents/profile.toml
```

`.config` ist ein versteckter Ordner: im Terminal mit
`open -e ~/.config/local-agents/profile.toml` öffnen, im Finder mit
`Cmd+Shift+G` den Pfad eingeben. Anderer Pfad: `LOCAL_AGENTS_PROFILE=<datei>`
setzen. Ohne Profil laufen die Agenten wie bisher, nur ohne Kontexte.

Felder der Datei:

| Feld | Bedeutung |
|---|---|
| `ignored_domains` | technische Domains, die bei der Zuordnung nicht zählen (z. B. Teams-Kanäle) |
| `[person]` `name`, `employer`, `role` | wer der Nutzer ist; steht in den Instruktionen jedes Agenten |
| `[person]` `state` | Bundesland des Arbeitsorts als Kürzel (`HE`, `BY`, `NW` …), siehe Feiertage |
| `[[contexts]]` `id` | Kennung, nach der gefiltert wird; `extern` und `sonstig` sind reserviert |
| `[[contexts]]` `kind` | `intern` (Arbeitgeber) oder `kunde` (je Kunde ein Block) |
| `[[contexts]]` `label`, `description` | Anzeigename und Erläuterung, z. B. das Projekt |
| `[[contexts]]` `domains` | Mail-Domains, die zu diesem Kontext gehören |
| `[[contexts]]` `keywords` | Stichwörter im Titel, Groß-/Kleinschreibung egal |
| `[[contexts]]` `stress` | nur Kunden: Stresslevel des Projekts, `niedrig`, `mittel` oder `hoch` |
| `[[contexts]]` `onsite_days` | nur Kunden: Tage beim Kunden, z. B. `["Mi"]` oder `["Mittwoch", "Fr"]` |

Eine fehlerhafte Datei (unbekanntes Bundesland, falsches `kind`, doppelte `id`)
bricht den Lauf mit einer Meldung ab. Das gilt auch für ein unbekanntes
Stresslevel oder einen unbekannten Wochentag.

Jeder Agent kann das Profil auf zwei Arten nutzen:

- `Profile.describe()` liefert den Text für die Instruktionen, damit das Modell
  Rolle und Kontexte kennt.
- `Profile.classify(text, organizer, attendees)` ordnet etwas einem Kontext
  zu, anhand von Titel, Domain des Einladenden und Domains der Teilnehmer. Das
  macht der Code, nicht das Modell, damit die Zuordnung wiederholbar ist. Für
  Mails passt dieselbe Funktion: Absender statt Organisator.

Regeln der Zuordnung, in dieser Reihenfolge:

1. **Kunde:** Der Titel enthält ein Stichwort des Kunden, oder der Kunde lädt
   ein (Domain des Organisators).
2. **Intern:** Der Titel enthält ein internes Stichwort.
3. **`sonstig`:** keine Beteiligten (Blocker, Privates).
4. **Mehrheit der Teilnehmer:** Bei interner Einladung gilt der Termin nur als
   Kundentermin, wenn mindestens so viele Kunden- wie interne Adressen
   eingeladen sind. Einzelne Kundenadressen in einer internen Runde (etwa
   Kundenpostfächer von Kollegen im Company Call) ändern nichts.
5. **`extern`:** ein fremder Organisator oder überwiegend fremde Domains, die
   noch keinem Kontext zugeordnet sind.

Der Kalender-Agent markiert damit jeden Termin, etwa `[Kunde ACME]` oder
`[Intern (Beispiel GmbH)]`, und kann über den Parameter `context` von `list_events`
filtern:

```bash
.venv/bin/python -m local_agents.calendar_agent "Welche Kundentermine habe ich nächste Woche?"
.venv/bin/python -m local_agents.calendar_agent "Was steht intern an?"
```

### Standort und Feiertage

Feiertagskalender zeigen alle deutschen Feiertage, auch solche, die nur in
einzelnen Bundesländern gelten. Mit `state = "HE"` unter `[person]` (Kürzel des
Bundeslands) blendet der Kalender-Agent alle Einträge aus Feiertagskalendern
aus, die am Arbeitsort kein gesetzlicher Feiertag sind: Der Buß- und Bettag
(nur Sachsen) und Gedenktage wie der Totensonntag tauchen für Hessen gar nicht
erst auf. Als Feiertagskalender gilt jeder Kalender, dessen Name „Feiertag"
oder „Holiday" enthält.

Feiertage, die am Arbeitsort gelten, bleiben stehen und sind als
`gesetzlicher Feiertag in Hessen, arbeitsfrei` markiert. Liegen an so einem Tag
Termine, steht unter der Zusammenfassung ein Feiertagshinweis: „Do 04.06.:
Fronleichnam ist in Hessen gesetzlicher Feiertag (arbeitsfrei), trotzdem 2
Termine eingetragen."

`local_agents/holidays.py` berechnet die Feiertage je Bundesland ohne Netz.
Feiertage, die nur in einzelnen Gemeinden gelten, sind vereinfacht: Mariä
Himmelfahrt zählt für ganz Bayern, Fronleichnam nicht für Sachsen und
Thüringen, das Augsburger Friedensfest gar nicht. Führen zwei
Feiertagskalender denselben Tag, erscheint der Feiertag nur einmal. Ohne
`state` bleiben alle Einträge sichtbar, mit dem Vermerk, wo sie gelten.

### Stresslevel und Kundentag: Verschiebevorschläge

Unter der Zusammenfassung nennt der Kalender-Agent interne Termine, die dem
Kundenprojekt weichen könnten. Zwei Angaben im Kundenkontext steuern das:

```toml
[[contexts]]
id = "kunde-acme"
kind = "kunde"
stress = "mittel"       # niedrig, mittel oder hoch
onsite_days = ["Mi"]    # Tage beim Kunden
```

- **Kundentag (`onsite_days`):** Alle internen Termine an diesem Wochentag
  werden zum Verschieben vorgeschlagen, unabhängig vom Stresslevel.
- **Stresslevel (`stress`):** An den übrigen Tagen zählen interne Blöcke, also
  interne Termine am Stück mit höchstens 30 Minuten Pause dazwischen.

| `stress` | Vorschlag an Tagen ohne Kundentag |
|---|---|
| `niedrig` oder leer | keiner |
| `mittel` | Blöcke aus mindestens 2 Terminen, die zusammen mindestens 2 Stunden belegen |
| `hoch` | jeder Block ab 1 Stunde, auch ein einzelner Termin |

Bei mehreren Kunden gilt das höchste Stresslevel. Termine mit mehr als 10
Eingeladenen sind als „große Runde, eher aussetzen" markiert, weil man sie kaum
selbst verschieben kann. Doppelt eingetragene Termine erscheinen einmal.
Kundentermine werden nie vorgeschlagen, und bei einer Frage nur nach
Kundenterminen entfällt der Abschnitt. Beispiel:

```text
**Verschiebevorschläge**
- Mi 18.11. ist Kundentag (Kunde ACME), 2 interne Termine verschieben:
  - 10:15-11:00 Brainstorming
  - 12:45-14:00 Firmenrunde (große Runde mit 80 Eingeladenen, eher aussetzen)
- Do 19.11.: interner Block 09:00-11:00 (2 interne Termine, 120 Min) verschieben, weil das Stresslevel bei Kunde ACME mittel ist:
  - 09:00-10:00 Team
  - 10:15-11:00 Planung
```

Die Schwellen stehen in `BLOCK_RULES`, `MAX_GAP` und `LARGE_ROUND` in
`local_agents/planning.py`.

Von den Beteiligten liest der Agent nur die Mail-Domains, keine Namen. Ein
neuer Kunde ist ein weiterer `[[contexts]]`-Block im Profil.

## Modell-Einstellungen

Beide Agenten laufen mit niedrigem Reasoning-Aufwand (`MODEL_SETTINGS` in
`local_agents/agent.py`). Mit der Standardeinstellung schrieb `gpt-oss:20b` in
1 von 3 Läufen englische Zwischenüberlegungen in die Antwort und brauchte
31–61 s statt rund 18 s.

Was verlässlich sein muss, entscheidet der Code und nicht das Modell:
Zeitraum der nächsten Woche, Zuordnung zu Kontexten und Auswahl der Feiertage.
Feiertagshinweise und Verschiebevorschläge formuliert der Code und druckt sie
unverändert unter die Antwort; das Modell bekommt sie gar nicht zu sehen, weil
es sie beim Wiedergeben veränderte (z. B. „eher aussetzen" an einem falschen
Termin). Das Modell formuliert nur die Zusammenfassung; deren Aufbau (Liste
oder Tabelle) schwankt von Lauf zu Lauf.

## Stand

- Tests mit geskriptetem Modell: bestanden.
- Lauf gegen `gpt-oss:20b`: durchgeführt (2026-11-14). Der Agent ruft
  `read_file` auf und fasst die README im Kern richtig zusammen; Details
  gibt er teils ungenau wieder (z. B. „16 KB" statt 16K Tokens).
- Kalender-Agent gegen `gpt-oss:20b`: läuft durch (2026-11-14, 20 Termine,
  ca. 25 s). Zusammenfassung manuell geprüft: funktioniert gut.
- Profil und Kontextfilter gegen `gpt-oss:20b`: laufen durch (2026-11-14),
  Ausgabe manuell geprüft. Die Zuordnung wurde danach verfeinert (Organisator
  und Mehrheit der Teilnehmer), weil einzelne Kundenadressen interne Termine
  zu Kundenterminen machten.
- Feiertage je Bundesland gegen `gpt-oss:20b`: Für Hessen erscheinen Buß- und
  Bettag und Totensonntag nicht mehr (2026-11-14, 4 Läufe).
- Verschiebevorschläge gegen `gpt-oss:20b`: erscheinen wortgleich unter der
  Zusammenfassung (2026-11-14, 2 Läufe). Ob die Schwellen im Alltag passen, ist
  noch nicht erprobt.

# local-agents

Eigene Agenten mit [Pydantic AI](https://ai.pydantic.dev/) und einem lokalen
Ollama-Modell (`gpt-oss:20b`, 16K Kontext). Ollama-Einrichtung, Speichergrenzen
und Modellerfahrungen stehen im Schwesterprojekt `opencode-ollama-agents`
(`docs/setup-macos.md`, `docs/performance.md`).

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

## Stand

- Tests mit geskriptetem Modell: bestanden.
- Lauf gegen `gpt-oss:20b`: durchgeführt (2026-11-14). Der Agent ruft
  `read_file` auf und fasst die README im Kern richtig zusammen; Details
  gibt er teils ungenau wieder (z. B. „16 KB" statt 16K Tokens).
- Kalender-Agent gegen `gpt-oss:20b`: läuft durch (2026-11-14, 20 Termine,
  ca. 25 s). Zusammenfassung manuell geprüft: funktioniert gut.

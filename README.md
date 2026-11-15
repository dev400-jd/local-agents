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
| Asana-Agent | `local_agents/asana_agent.py` | zeigt, wo der Nutzer in Asana erwähnt wurde |
| Asana-Quelle | `local_agents/asana_source.py` | liest Erwähnungen über die Asana-API |
| Planungs-Agent | `local_agents/planner_agent.py` | leitet aus Terminen Aufgaben mit Fertig-bis-Datum ab |
| Agenten-Datenbank | `local_agents/store.py` | SQLite-Datei mit Terminen, Aufgaben und Läufen |
| Aufgaben-Befehl | `local_agents/tasks.py` | Aufgaben ansehen und abhaken |
| Kurzfassung | `local_agents/briefing.py` | Überblick über die offenen Aufgaben in wenigen Sätzen, zum Vorlesen |
| Sprachausgabe | `local_agents/voice.py` | liest Text mit dem lokalen Sprachmodell Piper vor |
| Spracheingabe | `local_agents/listen.py` | nimmt eine gesprochene Antwort auf und erkennt sie lokal mit Whisper |
| Rückmeldung | `local_agents/feedback.py` | setzt die Antwort auf den Plan um: Aufgaben herausnehmen oder ergänzen |

## Einrichtung

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Das installiert auch die lokale Sprachausgabe (Piper), die Mikrofonaufnahme
(`sounddevice`) und die Spracherkennung (Whisper über `pywhispercpp`) für den
Planungs-Agenten ([Stimme anpassen](#stimme-anpassen),
[Gesprochene Antwort](#gesprochene-antwort)). Nach einem `git pull` mit neuen
Abhängigkeiten denselben Befehl noch einmal ausführen.

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

## Asana-Agent

Zeigt alle Asana-Kommentare und -Beschreibungen, in denen der Nutzer erwähnt
wurde, ohne Frage für die letzten 14 Tage:

```bash
.venv/bin/python -m local_agents.asana_agent
.venv/bin/python -m local_agents.asana_agent "Wo wurde ich in den letzten 3 Tagen erwähnt?"
```

Das Modell fasst zusammen, wer was will. Darunter druckt der Code die
vollständige Liste mit Datum, Autor, Aufgabe, Textauszug und Link, höchstens
60 Tage zurück. Enthält Aufgaben- oder Projektname ein Stichwort aus dem
Profil, steht der Kontext dabei, z. B. `[Kunde ACME]`.

### Asana-Token einrichten

Der Agent braucht ein Personal Access Token. Es gilt für das eigene Konto und
gehört weder ins Repo noch ins Profil, sondern in den macOS-Schlüsselbund. Der
Agent liest nur; er schreibt nichts nach Asana.

**1. Token in Asana erstellen**

1. In Asana: Profilbild → „Einstellungen" → „Apps" → „Entwicklerkonsole", oder
   direkt <https://app.asana.com/0/my-apps>.
2. „Token erstellen" wählen und einen Namen vergeben, z. B. `local-agents`. Der
   Name dient nur der Übersicht in Asana; für den Agenten spielt er keine Rolle.
3. Das Token in die Zwischenablage kopieren. Asana zeigt es nur einmal.

**2. Token im Schlüsselbund ablegen (Terminal)**

1. Diesen Befehl ausführen:

   ```bash
   security add-generic-password -s local-agents-asana -a "$USER" -w
   ```

2. Bei `password data for new item:` das Token mit `Cmd+V` einfügen und `Enter`
   drücken. Die Eingabe bleibt unsichtbar, es erscheinen keine Zeichen.
3. Bei `retype password for new item:` das Token noch einmal einfügen und
   `Enter` drücken. Ohne Fehlermeldung ist es gespeichert.
4. Prüfen (zeigt nur die Länge, nicht das Token; eine Zahl um 50–70 heißt, das
   Token ist da):

   ```bash
   security find-generic-password -s local-agents-asana -w | wc -c
   ```

Entscheidend ist der Name des Eintrags, `local-agents-asana`: Daran findet der
Agent das Token.

**Alternative: App „Schlüsselbundverwaltung"**

1. Mit `Cmd+Leertaste` nach „Schlüsselbundverwaltung" suchen und öffnen.
2. Links den Schlüsselbund „Anmeldung" wählen, dann „Ablage" → „Neues
   Passwort …".
3. Name des Schlüsselbundobjekts: `local-agents-asana`, Accountname: der eigene
   macOS-Benutzername, Passwort: das Token. „Hinzufügen" klicken.
4. Beim ersten Start des Agenten fragt macOS, ob `security` auf den Eintrag
   zugreifen darf: Mac-Passwort eingeben und „Immer erlauben" wählen.

**Alternative: Umgebungsvariable**

`ASANA_ACCESS_TOKEN=<token>` hat Vorrang vor dem Schlüsselbund. Das ist für
einmalige Tests gedacht; das Token steht dann in der Shell-Historie.

**Fehlerbilder**

- „Kein Asana-Token gefunden": Der Eintrag fehlt oder heißt anders.
- „The specified item already exists in the keychain": Es gibt schon einen
  Eintrag. Mit `security delete-generic-password -s local-agents-asana`
  löschen und neu anlegen. So ersetzt man auch ein Token.
- „Asana lehnt das Token ab": Das Token ist falsch, abgeschnitten oder in Asana
  gelöscht worden. Eintrag löschen und mit einem gültigen Token neu anlegen.
- „Die Asana-Suche gibt es nur in bezahlten Plänen": siehe nächster Abschnitt.

### Wie die Erwähnungen gefunden werden

Asana hat keinen Filter „wo wurde ich erwähnt". Der Agent sucht deshalb alle
Aufgaben, denen der Nutzer folgt und die im Zeitraum geändert wurden, und prüft
je Aufgabe die Kommentare und die Beschreibung auf einen Erwähnungs-Link auf
das eigene Konto. Daraus folgen Grenzen:

- Die Suche gibt es nur in bezahlten Asana-Plänen; sonst meldet der Agent das.
- Gefunden wird nur, was in Aufgaben steht, denen der Nutzer folgt. In der
  Asana-Oberfläche wird man durch eine Erwähnung Mitbearbeiter und folgt damit;
  wer sich danach austrägt, sieht die Erwähnung hier nicht.
- Eine Erwähnung in der Beschreibung hat kein eigenes Datum. Sie zählt, wenn
  die Aufgabe im Zeitraum angelegt wurde.
- Erwähnungen in Projektbeschreibungen, Statusberichten und Nachrichten fehlen.
- Je Aufgabe ein Abruf, höchstens 500 Aufgaben je Arbeitsbereich.

Die Textauszüge gehen an das lokale Modell, sonst nirgendwohin.

## Planungs-Agent und Agenten-Datenbank

Der Planungs-Agent liest die Termine der kommenden Woche und legt je Termin
eine Aufgabe „Vorbereitung" in der Agenten-Datenbank an, fertig am Vortag des
Termins: Das Daily am Montag, 16.11., ergibt eine Aufgabe, die am Sonntag,
15.11., fertig sein muss.

Interne Termine, die laut den [Verschiebevorschlägen](#verschiebevorschläge)
weichen sollten (Kundentag, interner Block bei Stress), bekommen zusätzlich
eine Aufgabe mit mehr Vorlauf, damit die Bitte rechtzeitig rausgeht. Ein Jour
fixe am Mittwoch, 18.11., ergibt bei drei Tagen Puffer eine Aufgabe für
Sonntag, 15.11.:

```text
Nachricht an Erika Muster: Jour fixe (Mi 18.11. 10:00) verschieben, weil Kundentag Kunde ACME
```

Der Name ist der Organisator aus der Einladung. Hast du selbst eingeladen,
heißt die Aufgabe „Selbst verschieben: …", bei großen Runden (mehr als 10
Eingeladene) „Absage an …". Liegt das Datum schon in der Vergangenheit, ist die
Aufgabe heute fällig. Trifft die Regel nicht mehr zu, verwirft der nächste Lauf
die offene Aufgabe.

```bash
.venv/bin/python -m local_agents.planner_agent
.venv/bin/python -m local_agents.planner_agent "Plane die Woche ab dem 23.11.2026"
```

Der Agent kann beliebig oft laufen. Ein weiterer Lauf legt nichts doppelt an,
sondern gleicht ab:

- neuer Termin → neue Aufgabe,
- umbenannter Termin → Aufgabe wird angepasst, Status und Ergebnis bleiben,
- abgesagter oder verschobener Termin → offene Aufgabe wird verworfen (ein
  verschobener Termin bekommt eine neue Aufgabe mit neuem Datum).

Keine Aufgabe bekommen ganztägige Einträge, Feiertage und Termine, die im
Profil ausgenommen sind. Doppelt eingetragene Termine ergeben eine Aufgabe,
jeder Termin einer Serie eine eigene. Einstellungen im Profil:

```toml
[planning]
lead_days = 1               # Vorbereitung: Tage vor dem Termin; 1 = am Vortag, auch am Wochenende
move_lead_days = 3          # Nachricht zum Verschieben: Tage vor dem Termin
skip = ["Company Call"]     # Stichwörter im Titel: keine Aufgabe
```

### Gesprochene Kurzfassung

Der ganze Plan steht im Terminal. Danach liest der Agent einen Überblick in
wenigen Sätzen vor und schließt mit einer Rückfrage:

```text
Für heute sind zwei Nachrichten eingeplant, an Erika Muster und Max Beispiel,
damit die internen Termine am Mittwoch verschoben werden. Hinzu kommen drei
Vorbereitungen für die Termine am Montag. Morgen folgen drei weitere Aufgaben,
danach noch acht. Der vollste Tag ist Montag mit drei geplanten Terminen.
Passt das so für dich?
```

Die Sätze beschreiben, was eingeplant ist, nicht was du tun musst: Die Aufgaben
sollen später Agenten erledigen. Den Text baut der Code aus festen
Satzbausteinen (`local_agents/briefing.py`). Vom lokalen Modell geschrieben
hatte er Grammatikfehler und einmal eine erfundene Uhrzeit. Ein lokales
Sprachmodell (Piper) spricht ihn (`local_agents/voice.py`), und er steht zum
Mitlesen unter „Kurzfassung (wird vorgelesen)". Nichts davon verlässt den
Rechner.

```bash
.venv/bin/python -m local_agents.planner_agent --stumm   # Kurzfassung nur als Text, ohne Ton
```

### Rückmeldung auf den Plan

Nach der Rückfrage wartet der Agent auf deine Antwort. Mit Ton sprichst du sie
([Gesprochene Antwort](#gesprochene-antwort)), mit `--stumm` tippst du sie:

```text
Deine Antwort (Enter = passt so): die Nachricht an Erika raus, dazu bis Dienstag Folien bauen
- raus: #18 Nachricht an Erika Muster: Jour fixe (Mi 18.11. 10:00) verschieben, weil Kundentag Kunde ACME
- neu: #21 Folien bauen, fertig bis Di 17.11.2026
Alles klar, ich habe eine Aufgabe herausgenommen und eine Aufgabe ergänzt.
```

- Zustimmung („ja", „passt so", Enter) lässt den Plan, wie er ist. Das erkennt
  der Code, ohne das Modell zu fragen.
- Alles andere übersetzt das lokale Modell in Änderungen: welche Aufgaben raus
  sollen und welche dazukommen, mit Datum, wenn du eines nennst („bis
  Dienstag", „morgen"); ohne Datum ist die neue Aufgabe heute fällig.
- Der Code führt nur aus, was zu einer vorhandenen Aufgabe passt, und nennt
  jede Änderung einzeln, damit ein Missverständnis auffällt. Was das Modell
  nicht zuordnen kann, steht unter „nicht verstanden" und ändert nichts.
- Herausgenommene Aufgaben bekommen den Status `abgelehnt`; der Planungs-Agent
  legt sie beim nächsten Lauf nicht wieder an. Das gilt für diesen einen
  Termin, nicht für die ganze Serie; dauerhaft ausnehmen geht mit `skip` im
  Profil. Zurücknehmen: `python -m local_agents.tasks offen 18`.
- Ergänzte Aufgaben haben die Art `manuell` und hängen an keinem Termin.

Läuft der Agent ohne Terminal (etwa später als geplanter Lauf), entfällt die
Rückfrage.

### Gesprochene Antwort

Nach der vorgelesenen Rückfrage hört der Agent zu:

```text
Ich höre zu … (Stille beendet die Aufnahme)
Verstanden: Die Nachricht an Erika raus, dazu bis Dienstag Folien bauen.
- raus: #18 Nachricht an Erika Muster: …
```

- Die Aufnahme beginnt sofort und endet nach etwa anderthalb Sekunden Stille
  im Anschluss an das Gesprochene, spätestens nach 45 Sekunden. Sagst du acht
  Sekunden lang nichts, bricht sie ab.
- Hört der Agent nichts oder scheitert die Spracheingabe, erscheint die
  Eingabezeile zum Tippen.
- Der erkannte Text steht unter „Verstanden:", bevor er ausgewertet wird.
- Beim ersten Mal fragt macOS nach dem Mikrofonzugriff für das Terminal
  (später: Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon).
- Das Whisper-Modell wird beim ersten Gebrauch einmalig heruntergeladen, nach
  `~/.local/share/local-agents/whisper` (anderer Ordner:
  `LOCAL_AGENTS_WHISPER`). Ton und Text verlassen den Rechner nicht.

```toml
[listen]
enabled = true             # false: Antwort immer tippen
model = "large-v3-turbo"   # Whisper-Modell
language = "de"
```

| `model` | Größe | Anmerkung |
|---|---|---|
| `large-v3-turbo` | ca. 1,6 GB | Standard, erkennt Namen und englische Fachwörter am besten |
| `medium` | ca. 1,5 GB | |
| `small` | ca. 470 MB | schneller, mehr Fehler bei Namen |
| `base`, `tiny` | 140 MB, 75 MB | nur zum Ausprobieren |

Nur die Spracheingabe testen (nimmt auf und zeigt den erkannten Text):

```bash
.venv/bin/python -m local_agents.listen
```

Was die Spracheingabe zusätzlich mitbringt:

| Zusatz | Wofür | Woher |
|---|---|---|
| `sounddevice` | Mikrofonaufnahme (bringt PortAudio mit) | Abhängigkeit, kommt mit `pip install -e .` |
| `pywhispercpp` | Spracherkennung mit Whisper (whisper.cpp) | Abhängigkeit, kommt mit `pip install -e .` |
| `numpy` | Lautstärke je Block messen, Aufnahme an Whisper übergeben | Abhängigkeit |
| Whisper-Modell | die eigentliche Erkennung | einmaliger Download beim ersten Gebrauch |
| Mikrofonfreigabe | macOS erlaubt dem Terminal die Aufnahme | Dialog beim ersten Gebrauch |

Wann die Aufnahme endet, entscheidet `Endpointer` in `local_agents/listen.py`
aus der Lautstärke: Die ersten 0,3 Sekunden messen das Grundrauschen; als
Sprache gilt, was dreimal so laut ist (mindestens ein fester Sockel) und zwei
Blöcke von je 0,1 Sekunden anhält. Bricht die Aufnahme bei dir zu früh ab oder
endet sie in lauter Umgebung nicht, sind das die Stellschrauben: `silence`,
`wait` und `limit` am `Endpointer` sowie `NOISE_FACTOR` und `MIN_THRESHOLD`.

Aufgaben, die du beim Ausprobieren herausgenommen hast, holst du so zurück:

```bash
.venv/bin/python -m local_agents.tasks alle        # herausgenommene tragen den Zusatz „abgelehnt"
.venv/bin/python -m local_agents.tasks offen 17    # wieder aufnehmen
```

### Stimme anpassen

Piper wird mit dem Repo installiert. Die Stimme selbst (eine Datei von 20 bis
110 MB) lädt der Agent beim ersten Vorlesen einmalig herunter, nach
`~/.local/share/local-agents/voices` (anderer Ordner: `LOCAL_AGENTS_VOICES`).
Eingestellt wird alles im Profil:

```toml
[voice]
enabled = true                  # false: nie vorlesen
model = "de_DE-thorsten-high"   # Piper-Stimme
speed = 1.0                     # Tempo von 0.5 bis 2; 1.2 = etwas schneller
speaker = 0                     # nur bei Stimmen mit mehreren Sprechern
voice = "Anna"                  # macOS-Ersatzstimme
```

Andere Stimme wählen: Namen bei `model` eintragen und ausprobieren. Der Befehl
spricht einen Testsatz (oder den angegebenen Text) mit den Einstellungen aus
dem Profil und lädt eine neue Stimme bei Bedarf herunter:

```bash
.venv/bin/python -m local_agents.voice
.venv/bin/python -m local_agents.voice "Guten Morgen, die Woche ist geplant."
```

Deutsche Stimmen im Piper-Katalog (Stand 2026-11-15), Hörproben unter
<https://rhasspy.github.io/piper-samples/>:

| `model` | Qualität |
|---|---|
| `de_DE-thorsten-high` | hoch (Standard) |
| `de_DE-thorsten-medium`, `de_DE-thorsten-low` | mittel, niedrig; schneller berechnet |
| `de_DE-thorsten_emotional-medium` | mittel, mehrere Sprechstile über `speaker` |
| `de_DE-kerstin-medium`, `de_DE-kerstin-low` | mittel, niedrig |
| `de_DE-mls-medium` | mittel, viele Sprecher über `speaker` |
| `de_DE-ramona-low`, `de_DE-karlsson-low`, `de_DE-pavoque-low` | niedrig |
| `de_DE-eva_k-x_low` | sehr niedrig |

Die aktuelle Liste aller Sprachen zeigt
`.venv/bin/python -m piper.download_voices`. Scheitert Piper oder steht
`model = ""` im Profil, spricht die macOS-Stimme aus `voice` (Auswahl:
`say -v '?' | grep de_DE`); sie klingt deutlich künstlicher.

Was in der Datenbank landet, entscheidet der Code. Das Modell wählt nur den
Zeitraum und kommentiert das Ergebnis; ruft es das Werkzeug nicht auf, plant
der Agent trotzdem die kommende Woche.

### Aufgaben ansehen und abhaken

```bash
.venv/bin/python -m local_agents.tasks               # offene Aufgaben
.venv/bin/python -m local_agents.tasks alle          # auch erledigte und verworfene
.venv/bin/python -m local_agents.tasks erledigt 12   # Aufgabe #12 abhaken
.venv/bin/python -m local_agents.tasks offen 12      # wieder öffnen
.venv/bin/python -m local_agents.tasks abgelehnt 12  # herausnehmen, wird nicht neu angelegt
```

### Die Datenbank

Eine SQLite-Datei unter `~/.local/share/local-agents/agents.db`, außerhalb des
Repos, weil Termintitel und Kundennamen darin stehen. Anderer Pfad:
`LOCAL_AGENTS_DB=<datei>`. Sie entsteht beim ersten Lauf von selbst.

| Tabelle | Inhalt |
|---|---|
| `events` | Termine, wie der Planungs-Agent sie gesehen hat: Titel, Beginn, Ende, Kontext, Ort, Zahl der Eingeladenen, Name des Organisators |
| `tasks` | Aufgaben: Art (`kind`), Titel, Kontext, Fertig-bis-Datum, Status, zuständiger Agent, Ergebnis, zugehöriger Termin |
| `runs` | Protokoll: welcher Agent wann lief, mit Status und Kurzfassung |

Status einer Aufgabe: `offen`, `in_arbeit`, `erledigt`, `verworfen` (Termin
entfallen) und `abgelehnt` (von dir herausgenommen). Die
Spalten `agent` und `result` sind für Agenten gedacht, die Aufgaben später
selbst erledigen; bisher füllt sie niemand. Je Termin und Art gibt es höchstens
eine Aufgabe, daran erkennt der Planungs-Agent, was schon da ist.

Andere Agenten greifen über `local_agents/store.py` zu (`Store().tasks(...)`,
`set_status`, `start_run`). Direkt hineinsehen geht mit
`sqlite3 ~/.local/share/local-agents/agents.db "select * from tasks"`. Zum
Zurücksetzen die Datei löschen. Die Agenten laufen nacheinander; gleichzeitige
Schreiber sind nicht vorgesehen.

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
| `[planning]` `lead_days` | Planungs-Agent: Aufgaben sind so viele Tage vor dem Termin fertig (Standard 1) |
| `[planning]` `move_lead_days` | Planungs-Agent: Vorlauf der Aufgaben „Nachricht an …, Termin verschieben" (Standard 3) |
| `[voice]` `enabled`, `model`, `speed`, `speaker`, `voice` | Sprachausgabe, siehe [Stimme anpassen](#stimme-anpassen) |
| `[listen]` `enabled`, `model`, `language` | Spracheingabe, siehe [Gesprochene Antwort](#gesprochene-antwort) |
| `[planning]` `skip` | Planungs-Agent: Stichwörter im Titel, für die keine Aufgabe entsteht |

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

Alle Agenten laufen mit niedrigem Reasoning-Aufwand (`MODEL_SETTINGS` in
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
- Asana-Agent gegen das echte Asana und `gpt-oss:20b`: läuft durch
  (2026-11-14). Die Suche fand in 14 Tagen 9 gefolgte Aufgaben und darin 1
  Erwähnung; der Posteingang der Asana-API (16 Benachrichtigungen) enthielt im
  selben Zeitraum ebenfalls genau 1 Erwähnung.
- Planungs-Agent gegen echten Kalender und `gpt-oss:20b`: legt für die Woche
  16.–22.11.2026 16 Aufgaben an; ein zweiter Lauf meldet 16 unverändert
  (2026-11-14).
- Rückmeldung auf den Plan mit `gpt-oss:20b` gegen eine Kopie der echten
  Datenbank (2026-11-15): sieben verschiedene getippte Antworten, darunter
  „alle Vorbereitungen für Montag raus" und eine absichtlich unklare, je 1 bis
  10 Sekunden. Sechs wurden auf Anhieb richtig umgesetzt; bei „bis Dienstag"
  fehlte zuerst das Datum, nach geschärften Vorgaben stimmte es. Der Dialog im
  Terminal (getippte Antwort) ist vom Nutzer von Hand erprobt.
- Spracheingabe (`listen.py`): vom Nutzer mit Mikrofon und Whisper
  (`large-v3-turbo`) erprobt, die Erkennung funktioniert (2026-11-15). Die
  Schwellen für Sprechbeginn und Stille sind gewählt, nicht in lauter Umgebung
  erprobt; die automatischen Tests stellen die Aufnahme nach.

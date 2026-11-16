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
| Status-Agent | `local_agents/status_agent.py` | kennt Datum und Uhrzeit, nennt die eine Aufgabe, die als Nächstes dran ist |
| Termin-Agent | `local_agents/meeting_agent.py` | erkennt die Art des Termins und welche Ergebnisse dir dafür helfen |
| Terminarten | `local_agents/meeting_types.py` | Katalog: Zweck und übliche Beiträge je Terminart, auch Scrum |
| Orchestrierung | `local_agents/orchestrator.py` | reicht die nächste Aufgabe von Agent zu Agent weiter |
| User Stories | `local_agents/stories.py` | liest Features und Stories aus Markdown-Dateien, Wissensbasis für den Daily-Agenten |
| Scrum Master | `local_agents/scrum_master.py` | fragt nach Neuigkeiten zu User Stories und vermerkt sie, auf Wunsch für ein Daily |
| Telegram-Kanal | `local_agents/telegram_bot.py` | Bemerkungen an den Scrum Master vom Handy aus; freiwillig, verlässt den Rechner |

## Ablauf im Überblick

Die Bausteine greifen so ineinander; alles läuft auf dem Rechner, auch Modell,
Stimme und Spracherkennung. Einzige Ausnahme ist der freiwillige
[Telegram-Kanal](#telegram-freiwillig) des Scrum Masters:

1. **Planen** (`planner_agent`): liest die Termine der kommenden Woche und
   legt Aufgaben in der Agenten-Datenbank an, je Termin eine Vorbereitung und
   für interne Termine, die dem Kundenprojekt weichen sollten, eine Nachricht
   an den Organisator mit einigen Tagen Vorlauf.
2. **Kurzfassung:** Der ganze Plan steht im Terminal; ein Überblick in wenigen
   Sätzen wird vorgelesen und endet mit „Passt das so für dich?".
3. **Absegnen:** Du antwortest gesprochen (oder getippt mit `--stumm`). Ein Ja
   lässt den Plan stehen, sonst nimmt der Agent Aufgaben heraus oder ergänzt
   welche. Danach gilt der Plan als abgesegnet.
4. **Abarbeiten** (`orchestrator`): Der Status-Agent nennt zu Datum und
   Uhrzeit die eine Aufgabe, die als Nächstes dran ist, und übergibt sie dem
   Termin-Agenten. Der erkennt die Art des Termins und nennt die Ergebnisse,
   die dir in deiner Rolle dafür helfen. Agenten, die diese Ergebnisse
   erarbeiten, gibt es noch nicht.

Zwischen Planen und Abarbeiten ändert sich der Kalender. Deshalb gleicht der
Code die Datenbank vor jedem Lesen der Aufgaben mit dem Kalender ab, siehe
[Kalenderabgleich](#kalenderabgleich).

```bash
.venv/bin/python -m local_agents.planner_agent   # Schritte 1 bis 3
.venv/bin/python -m local_agents.orchestrator    # Schritt 4
.venv/bin/python -m local_agents.tasks           # der ganze Plan
```

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

### Kalenderabgleich

Der Planungs-Agent sieht den Kalender nur, wenn er läuft. Damit der
Status-Agent keine Aufgabe zu einem Termin nennt, der inzwischen verschoben
oder abgesagt wurde, gleicht `sync.py` die Datenbank mit dem Kalender ab, bevor
`status_agent`, `orchestrator` und `tasks` (beim Auflisten) Aufgaben lesen. Der
Abgleich braucht kein Modell und dauert Sekundenbruchteile. Hat sich etwas
geändert, steht es vor der Ausgabe:

```text
Kalender abgeglichen: 1 Termin verschoben, 1 Aufgabe angepasst, 1 Aufgabe verworfen.
  verschoben: Review von Di 17.11. 10:00 auf Do 19.11. 14:00
```

Für Termine ab heute gilt:

- **Verschoben:** Die Aufgabe bleibt mit Status, Ablehnung und Einordnung des
  Termin-Agenten erhalten; Titel und Fertig-bis-Datum ziehen nach. Muss der
  Termin danach nicht mehr weichen, wird seine Aufgabe „Nachricht an
  <Organisator>" verworfen.
- **Abgesagt:** Offene Aufgaben werden verworfen, erledigte bleiben stehen.
- **Neu:** Der Termin bekommt Aufgaben wie beim Planen, aber nur an Tagen, die
  schon geplant wurden, und nur, wenn er noch nicht begonnen hat. Ungeplante
  Wochen bleiben dem Planungs-Agenten überlassen.

Einen verschobenen Termin erkennt der Abgleich so wieder:

| Fall | Erkennungsmerkmal |
|---|---|
| Einzeltermin verschoben | gleiche Kalender-Kennung, genau ein alter und ein neuer Eintrag |
| einzelnes Vorkommen einer Serie verschoben | Der Kalender führt es als geändertes Vorkommen mit eigener Kennung; der ursprüngliche Beginn und der Titel stimmen mit dem verschwundenen Eintrag überein |
| ganze Serie verlegt | gleiche Kennung und im Zeitraum gleich viele alte wie neue Vorkommen |

Passt keiner der Fälle, gilt der alte Termin als entfallen und der neue als
neu. Das trifft zum Beispiel zu, wenn ein Vorkommen einer Serie verschoben und
zugleich umbenannt wurde. Der Abgleich sucht bis vier Wochen hinter dem letzten
bekannten Termin; weiter weg verschobene Termine gelten als entfallen.

Ist der Kalender nicht lesbar oder liefert er gar keine Termine (Konto gerade
nicht geladen), bleibt die Datenbank unverändert und eine Zeile weist darauf
hin. Von Hand: `.venv/bin/python -m local_agents.sync`. Abschalten: `sync =
false` unter `[planning]` im Profil; dann gleicht nur der Planungs-Agent ab.

Der Abgleich läuft bisher nur bei einem Aufruf. Einen Hintergrunddienst, der
auf Kalenderänderungen reagiert, gibt es noch nicht.

### Die Datenbank

Eine SQLite-Datei unter `~/.local/share/local-agents/agents.db`, außerhalb des
Repos, weil Termintitel und Kundennamen darin stehen. Anderer Pfad:
`LOCAL_AGENTS_DB=<datei>`. Sie entsteht beim ersten Lauf von selbst.

| Tabelle | Inhalt |
|---|---|
| `events` | Termine, wie der Planungs-Agent sie gesehen hat: Titel, Beginn, Ende, Kontext, Ort, Zahl der Eingeladenen, Name des Organisators |
| `planned_days` | Tage, die schon geplant wurden; nur dort legt der Kalenderabgleich Aufgaben für neue Termine an |
| `tasks` | Aufgaben: Art (`kind`), Titel, Kontext, Fertig-bis-Datum, Status, zuständiger Agent, Ergebnis, zugehöriger Termin |
| `briefs` | Einordnung des Termins hinter einer Aufgabe: Terminart, Begründung, Ergebnisse für den Nutzer, Rolle |
| `story_notes` | Bemerkungen zu User Stories: Story, Feature, Text, Daily-Datum, Status, Kanal, Wortlaut |
| `runs` | Protokoll: welcher Agent wann lief, mit Status und Kurzfassung. Einträge: `planner` (Planungslauf), `rueckmeldung` (Antwort mit Änderungen ausgewertet), `freigabe` (Plan abgesegnet), `termin` (Termin eingeordnet), `scrum_master` (Nachfrage-Runde), `abgleich` (Kalenderabgleich, nur wenn sich etwas geändert hat) |

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

## Status-Agent

Der erste Baustein für das Abarbeiten des abgesegneten Plans. Er kennt nur
Datum, Uhrzeit und die offenen Aufgaben und nennt die eine Aufgabe, die jetzt
als Allernächstes dran ist, nicht den ganzen Plan:

```bash
.venv/bin/python -m local_agents.status_agent
```

```text
Als Nächstes steht die Vorbereitung für das Daily an, der Termin beginnt in 14 Stunden.

**Stand So 15.11.2026, 18:53 Uhr (Plan abgesegnet am 15.11. 18:45)**
Als Nächstes: #1 Vorbereitung: Daily (Mo 16.11. 09:00) [Kunde ACME; heute fällig; fertig bis So 15.11.; Termin in 14 Std 7 Min]
Danach noch 14 weitere Aufgaben: 4 heute fällig, 2 morgen fällig, 8 später fällig.
```

Welche Aufgabe das ist, rechnet der Code aus Fertig-bis-Datum und
Terminbeginn. Die Reihenfolge der Dringlichkeit:

| Stufe | Bedeutung |
|---|---|
| `überfällig` | Fertig-bis-Datum liegt vor heute |
| `heute fällig`, `morgen fällig` | heute bzw. morgen fällig |
| `später fällig` | alles danach |
| `hinfällig` | der zugehörige Termin hat schon begonnen; solche Aufgaben werden nie als Nächstes genannt |

Innerhalb einer Stufe kommt zuerst, was zum früheren Termin gehört. Mit der
Uhrzeit rückt die Antwort weiter: Hat ein Termin begonnen, ist seine
Vorbereitung hinfällig und die nächste Aufgabe dran. Das Modell formuliert nur
die ein, zwei Sätze darüber. Den ganzen Plan zeigt
`python -m local_agents.tasks`.

Als abgesegnet gilt der Plan, sobald du im Planungs-Agenten auf die Rückfrage
geantwortet hast (mit Ja oder mit Änderungen); der Zeitpunkt steht in der
Überschrift. Ohne Freigabe läuft der Status-Agent trotzdem und vermerkt „Plan
noch nicht abgesegnet".

Andere Agenten brauchen dafür kein Modell: `next_task(Store(), datetime.now())`
aus `local_agents/status_agent.py` liefert die dringendste offene Aufgabe mit
Stufe und Zeit bis zum Termin, `next_tasks(...)` alle in dieser Reihenfolge.

## Termin-Agent und Orchestrierung

```bash
.venv/bin/python -m local_agents.orchestrator         # Status-Agent, dann Termin-Agent
.venv/bin/python -m local_agents.orchestrator --neu   # Termin neu einordnen lassen
```

Die Orchestrierung (`local_agents/orchestrator.py`) ist Code, kein Modell: Sie
lässt den Status-Agenten laufen und übergibt dessen nächste Aufgabe samt
Termin an den Agenten, der zur Art der Aufgabe passt. Bisher gibt es einen:
Vorbereitungsaufgaben gehen an den Termin-Agenten. Für andere Arten (etwa
„Nachricht an …") endet die Kette nach dem Status mit einem Hinweis.

```text
**Stand So 15.11.2026, 20:02 Uhr (Plan abgesegnet am 15.11. 18:45)**
Als Nächstes: #1 Vorbereitung: Plattform - Daily (Mo 16.11. 09:00) [Kunde ACME; heute fällig; …]

**Terminart: Daily / Stand-up, Scrum**
Woran erkannt: Der Titel enthält das Stichwort Daily und der Termin dient einer kurzen Abstimmung
Zweck: Das Team stimmt sich in etwa 15 Minuten ab: Fortschritt zum Sprintziel, …
Was man dort üblicherweise beiträgt:
- Was seit dem letzten Daily erreicht wurde
- Was bis zum nächsten Daily ansteht
- Hindernisse und offene Punkte, bei denen andere helfen oder entscheiden müssen

**Ergebnisse, die dir für diesen Termin helfen (als Solution Architect, Kunde ACME)**
- Liste der seit letztem Daily erledigten Architekturaufgaben im Projekt
- …
```

Der Termin-Agent arbeitet in zwei Teilen:

- **Terminart.** Das Modell ordnet den Termin einer Art aus dem Katalog zu
  (`local_agents/meeting_types.py`): Daily, Sprint Planning, Sprint Review,
  Retrospektive, Backlog Refinement, Jour fixe, Einzelgespräch, Workshop,
  Problemklärung, Statusbericht/Lenkungsausschuss, Kick-off, Präsentation,
  große Runde, Sonstiges. Anhaltspunkte sind Titel, Dauer, Zahl der
  Eingeladenen und Kontext. Zweck und übliche Beiträge der Art kommen
  unverändert aus dem Katalog. Neue Arten trägst du dort ein.
- **Ergebnisse für dich.** Aus deiner Rolle im Kontext des Termins leitet das
  Modell ein bis fünf Ergebnisse ab, die du vorbereitet haben solltest. Die
  Rolle steht im Profil je Kontext (`role = "Solution Architect"`); fehlt sie,
  gilt die allgemeine Rolle aus `[person]`.

Die Einordnung wird zur Aufgabe gespeichert (Tabelle `briefs`) und beim
nächsten Lauf wiederverwendet; sie ist der Auftrag für Agenten, die den Termin
später inhaltlich vorbereiten. Liefert das Modell nichts Brauchbares, gilt das
Stichwort im Titel oder „Sonstiges" mit den Beiträgen aus dem Katalog.

Grenzen: Die Ergebnisse formuliert das Modell, und es kennt nur Titel, Rolle
und Projektnamen, nicht den Inhalt des Projekts. Sie sind mal mehr, mal
weniger auf die Rolle bezogen und können Vermutetes enthalten. Mehrdeutige
Titel (nur der Projektname) ordnet es nach Dauer und Teilnehmerzahl ein, das
ist geraten. Der Termin-Agent läuft mit mittlerem Reasoning-Aufwand: Mit
niedrigem übernahm `gpt-oss:20b` die Katalog-Beiträge wörtlich oder erfand
Projektdetails.

## User Stories als Markdown

Die Wissensbasis für die inhaltliche Vorbereitung (zuerst das Daily) sind die
User Stories des Projekts als Markdown, eine Datei je Feature. Sie liegen als
normale Dateien in einem Ordner; kein Programm muss dafür laufen. Wer mag,
öffnet denselben Ordner in Obsidian zum Lesen und Suchen, am Aufbau ändert das
nichts.

```toml
[[contexts]]
id = "kunde-acme"
stories_dir = "~/local-agents-vault/kunde-acme"
```

Der Ordner gehört nicht ins Repo und nicht in einen Cloud-Ordner (iCloud,
OneDrive): Er enthält Kundendaten. Dateien bleiben, wie sie geschrieben
wurden; der Code liest sie bei jedem Aufruf neu, es gibt keine zweite Kopie.

```bash
.venv/bin/python -m local_agents.stories        # Features und Stories im Überblick
.venv/bin/python -m local_agents.stories QG-1   # eine Story vollständig
```

```text
Feature: Bestellimport mit Qualitätsprüfung (2 Stories, Feature_Bestellimport_2026-11-11.md)
  QG-1: Prüfregeln für `bestellung_roh` [3 PT, 2 Akzeptanzkriterien, 2 Aufgaben]
  TR-2: Bericht bereitstellen [1 PT]
```

Der Leser hängt nicht an einem bestimmten Aufbau. Verlangt ist nur: Eine Story
ist ein Überschriften-Abschnitt mit einem Namen. Gelesen wird in drei
Schichten:

1. **Gliederung.** Jede Datei wird in ihre Überschriften-Abschnitte zerlegt
   (Zeilen in Codeblöcken zählen nicht). Das geht mit jedem Markdown.
2. **Welche Abschnitte sind Stories?**
   - *Regel:* Überschriften unterhalb des Titels, die mit einem Kürzel
     beginnen: `## QG-1: Titel`, `### CL-1 Titel`, `## ZI-0a Titel`.
   - *Modell, wenn die Regel nichts findet:* Das lokale Modell bekommt nur
     die Liste der Überschriften (mit Ebene, Umfang und Textanfang) und
     markiert die Stories. Es schreibt keinen Inhalt ab, kann also nichts
     daran verändern.
   - Das Ergebnis des Modells steht danach in `stories-index.json` im
     Ordner. Dort korrigierst du es bei Bedarf (Liste der Überschriften) und
     setzt `"geprueft": true`; dann bleibt der Eintrag auch nach Änderungen
     an der Datei bestehen. Ohne diesen Haken wird eine geänderte Datei neu
     markiert.
   - Stehen unter einem markierten Abschnitt selbst Stories, gilt er als
     Gruppe, nicht als Story.
3. **Felder (Zugabe).** Wo üblich geschrieben, erkennt der Code Schätzung,
   Akzeptanzkriterien, Aufgaben und weitere Felder. Agenten dürfen sich nur
   auf Name und Text einer Story verlassen.

Was Schicht 3 versteht (Beispiele: `tests/data/feature_beispiel.md` und
`tests/data/feature_varianten.md`):

| Teil | Erkennung |
|---|---|
| Feature | erste Überschrift `# …` (ein vorangestelltes „Feature:" entfällt), sonst der Dateiname |
| Gruppe | der Abschnitt, unter dem eine Story steht |
| Angabenzeile | Zeile gleich unter der Überschrift mit `·` oder `\|` als Trenner, z. B. `` `kürzel` · 3 SP · Sprint: 12 ``; daraus kommen Schätzung und benannte Angaben |
| Felder | fett gesetzter Name am Zeilenanfang (`**Story**`, `**Schätzung:** 3 SP`) oder eine Unterüberschrift (`#### Akzeptanzkriterien`); der Text reicht bis zum nächsten Feld |
| Story-Satz ohne Feldnamen | freier oder kursiver Text vor dem ersten Feld gilt als Feld „Story" |
| Tabellen ohne Feldnamen | heißen nach ihrem Kopf: „Kriterium" → Akzeptanzkriterien, „Aufgabe" → Aufgaben, „Voraussetzung" → Voraussetzungen, „Frage" → Klärung |
| Schätzung | Feld „Schätzung", „Aufwand", „Story Points" oder „Estimate", oder eine Angabe wie `3 SP` |

Prüfen, ob eine neue Datei verstanden wird: `python -m local_agents.stories`
zeigt je Story Schätzung und Zahl der Akzeptanzkriterien und Aufgaben, und bei
Features den Zusatz „vom Modell erkannt", wenn die Regel nicht griff. Nur
dieser Befehl fragt das Modell; andere Agenten lesen den Index.

- Der volle Name einer Story (`QG-1: Titel`) ist der Schlüssel, über den
  später der Stand aus dem Projektwerkzeug des Kunden dazukommt.
- Liegt ein Feature in mehreren Dateien vor (Datum im Dateinamen), gilt die
  Datei, deren Name alphabetisch zuletzt kommt.
- Dateien ohne Story-Abschnitte werden übergangen.
- Für andere Agenten: `load_features(ordner)` liefert `Feature`-Objekte mit
  `stories`; je Story verlässlich `name` und `text` (der ganze Abschnitt),
  als Zugabe `key`, `group`, `estimate`, `fields` und `table("Aufgaben")`.

## Scrum-Master-Agent

Er sammelt, was der Daily-Agent später braucht: Er fragt dich, ob es zu einer
User Story etwas Neues gibt, und vermerkt deine Bemerkung an der Story. Einem
Daily ordnet er sie nur zu, wenn du das sagst.

```bash
.venv/bin/python -m local_agents.scrum_master               # nachfragen, im Terminal
.venv/bin/python -m local_agents.scrum_master --dialog      # nachfragen, als macOS-Dialogfenster
.venv/bin/python -m local_agents.scrum_master notizen       # offene Bemerkungen
.venv/bin/python -m local_agents.scrum_master notizen alle  # auch besprochene und entfernte
.venv/bin/python -m local_agents.scrum_master besprochen 3  # Bemerkung #3 abhaken
.venv/bin/python -m local_agents.scrum_master entfernt 3    # Bemerkung #3 entfernen
.venv/bin/python -m local_agents.scrum_master offen 3       # wiederherstellen
```

```text
Gibt es etwas Neues zu einer User Story?
> schreib bitte rein, dass QG-1 und TR-2 am 30.10.2026 abgeschlossen wurden
Vermerkt:
- #1 QG-1: Prüfregeln für `bestellung_roh`: QG-1 wurde am 30.10.2026 abgeschlossen
- #2 TR-2: Bericht bereitstellen: TR-2 wurde am 30.10.2026 abgeschlossen
Noch etwas zu einer anderen Story?
> bei QG-1 steht die Abstimmung mit dem Fachbereich noch aus, fürs Daily am Mittwoch
Vermerkt:
- #3 QG-1: Prüfregeln für `bestellung_roh`: bei QG-1 steht die Abstimmung mit dem Fachbereich noch aus [Daily Mi 18.11.2026]
Noch etwas zu einer anderen Story?
> entferne den letzten Vermerk wieder
Entfernt: #3
```

Was eine Antwort bewirkt:

| Antwort | Wirkung |
|---|---|
| Bemerkung zu einer oder mehreren Stories | je Story ein Vermerk ohne Daily-Bezug; genannte Tage und Zahlen bleiben im Text |
| … mit „fürs Daily", „im Daily", „ansprechen", „besprechen" | der Vermerk gehört zu einem Daily: zum genannten Tag, sonst zum nächsten Daily |
| „entferne/lösche/streiche den Vermerk", „mach das rückgängig" | nimmt die zuletzt vermerkten Bemerkungen zurück; mit Nummer (`#3`, „Bemerkung 3") eine bestimmte |
| „nein", „nichts Neues", Enter | beendet die Runde |

Architektur:

- **Ablage in der Datenbank** (Tabelle `story_notes`): Story, Feature, Text,
  Daily-Datum (leer = ohne Daily-Bezug), Status (`offen`, `besprochen`,
  `entfernt`), Kanal und dein unveränderter Wortlaut. Nicht in den
  Story-Dateien, denn die werden neu erzeugt. Der Daily-Agent liest später
  `Store().story_notes(daily_date=…)`. Entfernte Vermerke bleiben in der
  Datenbank und lassen sich wiederherstellen.
- **Zuordnung in zwei Stufen.** Was wörtlich in deiner Antwort steht, liest
  der Code selbst: ein Kürzel wie `QG-3`, den Daily-Bezug und den Tag dazu
  („am Mittwoch", „morgen", „am 20.11."). Nur wo nichts Eindeutiges steht,
  entscheidet das lokale Modell anhand der Liste der Story-Namen, etwa bei
  „die Story mit dem SSO". Der Code prüft, dass es die Story gibt und dass
  keine Datums- oder Zeitangabe aus deiner Antwort im Text fehlt.
- **Nächstes Daily:** der nächste geplante Termin mit „Daily" im Titel im
  Kontext der Story, sonst der nächste Werktag.
- **Nichts geht verloren.** Passt keine Story oder liefert das Modell nichts
  Brauchbares, wird dein Wortlaut vermerkt, je genanntem Kürzel einmal oder
  „ohne Story".
- **Kanal getrennt vom Agenten.** Fragen und Antworten laufen über einen
  Kanal mit zwei Funktionen, `ask` und `tell`. Es gibt `TerminalChannel` und
  `DialogChannel` (macOS-Dialogfenster, braucht kein Terminal). Ein weiterer
  Kanal, etwa ein Messenger, ist eine Klasse mit diesen zwei Funktionen.

Grenzen: Im Terminal und als Dialogfenster läuft der Agent, wenn du ihn
startest; von selbst fragt bisher nur der Telegram-Dienst nach. Nennst du
mehrere Stories in einem Satz, steht der ganze Satz oft an jeder dieser
Stories. Bei Stichworten statt Kürzeln rät das Modell. Bearbeiten lässt sich
ein Vermerk nicht; entferne ihn und sag ihn neu.

### Telegram (freiwillig)

> **Datenschutz:** Das ist die einzige Stelle im Projekt, an der Inhalte den
> Rechner verlassen. Was du dem Bot schreibst und was er antwortet, läuft über
> die Server von Telegram und ist dort nicht Ende-zu-Ende-verschlüsselt. Der
> Kanal ist abgeschaltet, bis du ihn einrichtest. Kläre vorher, ob du
> Projektinhalte eines Kunden über einen privaten Messenger schicken darfst.

Was die Auswirkung begrenzt: Der Bot antwortet nur dem einen Chat aus dem
Profil und ignoriert alle anderen. Seine Antworten nennen das Kürzel einer
Story, nicht ihren Namen. Was du selbst schreibst, liegt bei dir. Die
Auswertung macht weiter das lokale Modell auf deinem Rechner; Telegram
überträgt nur die Nachrichten.

Einrichtung:

1. In Telegram `@BotFather` öffnen, `/newbot` senden, Namen vergeben. Du
   bekommst ein Token.
2. Token im Schlüsselbund ablegen (wird beim Tippen nicht angezeigt):
   ```bash
   security add-generic-password -s local-agents-telegram -a "$USER" -w
   ```
3. Eigenen Chat festlegen. Der Befehl wartet, bis du dem Bot eine Nachricht
   schreibst, und trägt `[telegram]` mit deiner `chat_id` ins Profil ein:
   ```bash
   .venv/bin/python -m local_agents.telegram_bot einrichten
   ```
4. Dienst starten; er läuft, bis du ihn mit Strg+C beendest:
   ```bash
   .venv/bin/python -m local_agents.telegram_bot
   ```

```toml
[telegram]
enabled = true          # false: Dienst startet nicht
chat_id = 123456789     # nur dieser Chat wird beantwortet
ask_at = ["16:30"]      # der Bot fragt an Werktagen zu diesen Uhrzeiten nach; [] = nie
```

Im Chat gilt dasselbe wie im Terminal: Jede Nachricht ist eine Antwort an den
Scrum Master (Bemerkung, „fürs Daily am Dienstag", „entferne das wieder").
`/notizen` zeigt die offenen Bemerkungen, `/start` eine kurze Hilfe.

Grenzen: Der Dienst läuft nur, solange der Rechner an ist und der Befehl
läuft; ein automatischer Start beim Anmelden ist nicht eingerichtet.
Nachrichten aus der Zeit, in der er nicht lief, holt er nicht nach.
Sprachnachrichten versteht er nicht. Token zurückziehen: bei `@BotFather` mit
`/revoke`; im Schlüsselbund löschen mit
`security delete-generic-password -s local-agents-telegram`.

### Modell je Agent

Jeder Agent kann ein eigenes Ollama-Modell bekommen, über den Agentennamen im
Profil; ohne Eintrag gilt das Standardmodell:

```toml
[models]
status = "qwen3.5:9b-q8_0"   # Agentennamen: status, termin, stories, scrum_master
```

Gemessen am Status-Agenten mit 15 offenen Aufgaben (2026-11-15, je 2 Läufe):

| Modell | Dauer | Antwort |
|---|---|---|
| `gpt-oss:20b`, Reasoning niedrig (Standard) | 3–8 s | sachlich richtig |
| `qwen3.5:9b-q8_0` mit Nachdenken | 141–147 s | sachlich richtig, gutes Deutsch |
| `qwen3.5:9b-q8_0` ohne Nachdenken | 3–6 s | erfundene Angabe („in weniger als 6 Minuten"), Grammatikfehler |

Deshalb läuft der Status-Agent vorerst mit dem Standardmodell. Die Einstellung
„Reasoning niedrig" verkürzt das Nachdenken bei Qwen nicht; abschalten lässt es
sich nur ganz, und das ist im Code noch nicht je Agent einstellbar.

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
| `[[contexts]]` `role` | deine Rolle in diesem Kontext, falls sie von `[person]` `role` abweicht; nutzt der Termin-Agent |
| `[[contexts]]` `stories_dir` | Ordner mit den User Stories des Kontexts als Markdown, siehe [User Stories als Markdown](#user-stories-als-markdown) |
| `[[contexts]]` `domains` | Mail-Domains, die zu diesem Kontext gehören |
| `[[contexts]]` `keywords` | Stichwörter im Titel, Groß-/Kleinschreibung egal |
| `[[contexts]]` `stress` | nur Kunden: Stresslevel des Projekts, `niedrig`, `mittel` oder `hoch` |
| `[[contexts]]` `onsite_days` | nur Kunden: Tage beim Kunden, z. B. `["Mi"]` oder `["Mittwoch", "Fr"]` |
| `[planning]` `lead_days` | Planungs-Agent: Aufgaben sind so viele Tage vor dem Termin fertig (Standard 1) |
| `[planning]` `move_lead_days` | Planungs-Agent: Vorlauf der Aufgaben „Nachricht an …, Termin verschieben" (Standard 3) |
| `[voice]` `enabled`, `model`, `speed`, `speaker`, `voice` | Sprachausgabe, siehe [Stimme anpassen](#stimme-anpassen) |
| `[listen]` `enabled`, `model`, `language` | Spracheingabe, siehe [Gesprochene Antwort](#gesprochene-antwort) |
| `[telegram]` `enabled`, `chat_id`, `ask_at` | Telegram-Kanal des Scrum Masters, siehe [Telegram (freiwillig)](#telegram-freiwillig); ohne Eintrag aus |
| `[models]` `<agent>` | Ollama-Modell für einen Agenten, siehe [Modell je Agent](#modell-je-agent) |
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

Dasselbe gilt für die neueren Bausteine:

| Aufgabe | Wer entscheidet |
|---|---|
| Welche Aufgaben entstehen, mit welchem Datum | Code |
| Text der gesprochenen Kurzfassung | Code (Satzbausteine); vom Modell geschrieben hatte er Grammatikfehler und eine erfundene Uhrzeit |
| Ob eine Antwort Zustimmung ist | Code |
| Welche Aufgaben eine Antwort meint, welche neu dazukommen | Modell; der Code prüft die Nummern und nennt jede Änderung |
| Welche Aufgabe als Nächstes dran ist | Code |
| Ein, zwei Sätze des Status-Agenten dazu | Modell; es sieht nur diese eine Aufgabe |
| Welcher Agent die nächste Aufgabe bekommt | Code (Orchestrierung) |
| Art eines Termins | Modell, aus einem festen Katalog; der Code prüft die Auswahl |
| Zweck und übliche Beiträge der Terminart | Code (Katalog) |
| Ergebnisse, die dem Nutzer für den Termin helfen | Modell; nicht geprüft, als Vorschlag zu lesen |
| Welche Abschnitte einer Datei Stories sind | Code (Kürzel), sonst Modell auf der Überschriftenliste; korrigierbar im Index |
| Zu welcher Story eine Bemerkung gehört, für welches Daily | Code, wenn Kürzel und Tag wörtlich genannt sind; sonst Modell, vom Code geprüft |

Der Name des Organisators eines Termins geht an das lokale Modell, wenn es
eine Antwort auswertet oder der Status-Agent eine Nachricht-Aufgabe nennt.

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
- Status-Agent gegen die echte Datenbank und `gpt-oss:20b` (2026-11-15):
  nennt in 3–8 s die dringendste Aufgabe; zwei Läufe hintereinander nannten
  dieselbe. Die Sätze des Modells stimmten inhaltlich, einmal schrieb es
  „Naechste" ohne Umlaut. Modellvergleich mit Qwen: Abschnitt
  [Modell je Agent](#modell-je-agent).
- Kalenderabgleich gegen den echten Kalender und eine Kopie der echten
  Datenbank (2026-11-16): erkannte einen von Mittwoch auf den folgenden Montag
  verschobenen Termin (Vorbereitung blieb, die Nachricht zum Verschieben wurde
  verworfen) und einen abgesagten (beide Aufgaben verworfen); ein zweiter Lauf
  änderte nichts. Verschobene Vorkommen von Serien und verlegte Serien sind
  nur in den automatischen Tests nachgestellt.
- Noch offen: Agenten, die Aufgaben selbst erledigen; vom Nutzer ergänzte
  Aufgaben (`manuell`) erscheinen in `tasks` und beim Status-Agenten, aber
  nicht in der Planliste und der Kurzfassung des Planungs-Agenten.
- Termin-Agent mit `gpt-oss:20b` gegen eine Kopie der echten Datenbank
  (2026-11-15, 10 verschiedene Termine): Terminart bei den eindeutigen Titeln
  richtig (Daily, Sync, Brainstorming, Statusmeeting, großes Team-Meeting);
  bei mehrdeutigen schwankend (ein Zweiertermin einmal Jour fixe, einmal
  Einzelgespräch). Ergebnisse mit mittlerem Reasoning-Aufwand in 9–20 s und
  meist auf die Rolle bezogen, vereinzelt mit Vermutetem. Kette
  Status-Agent → Termin-Agent auf der echten Datenbank: rund 6 s, mit
  gespeicherter Einordnung rund 2 s.
- User Stories aus Markdown (`stories.py`, 2026-11-15): vier echte Dateien in
  vier Schreibweisen werden gelesen, zusammen 58 Stories, alle mit Story-Text
  und Schätzung, 57 mit Tabellen für Akzeptanzkriterien und Aufgaben (eine
  Story hat in der Datei keine). Geprüft über Zählungen, nicht über den
  Inhalt.
- Stories vom Modell erkennen lassen (`gpt-oss:20b`, 2026-11-15, dieselben
  vier Dateien, verglichen mit der Regel): mit Kürzeln in den Überschriften in
  zwei Läufen je Datei alle 58 Stories richtig, nichts zu viel. Mit entfernten
  Kürzeln, wie in einer Datei ohne Kürzel, 57 von 58 richtig, nichts zu viel,
  eine übersehen. Je Datei 1 bis 6 Sekunden. Dateien ganz anderer Machart
  (andere Autoren, andere Sprache) sind nicht erprobt.
- Noch nicht angebunden: Daily-Agent und Stand aus dem Projektwerkzeug.
- Scrum-Master-Agent mit `gpt-oss:20b` gegen die 58 echten Stories und eine
  Kopie der Datenbank (2026-11-15): Der erste Handtest des Nutzers
  scheiterte (Vermerk ungefragt einem Daily zugeordnet, Datum aus dem Text
  gestrichen, „entferne den Vermerk" selbst als Bemerkung gespeichert).
  Nach der Korrektur lief derselbe Ablauf mit drei weiteren Antworten zweimal
  richtig durch, je Antwort 1–5 s. Zuordnung über Stichworte ohne Kürzel
  (drei Fälle in einem früheren Lauf) plausibel, aber nicht gegen den Inhalt
  geprüft. Das Dialogfenster ist noch nicht von Hand erprobt; ein Zeitplan im
  Hintergrund fehlt.
- Telegram-Kanal (`telegram_bot.py`, 2026-11-15): nur mit nachgestellter
  Bot-API getestet. Gegen das echte Telegram noch nicht gelaufen, weil kein
  Bot-Token eingerichtet ist.

# local-agents

Eigene Agenten mit [Pydantic AI](https://ai.pydantic.dev/) und einem lokalen
Ollama-Modell (`gpt-oss:20b`, 16K Kontext). Ollama-Einrichtung, Speichergrenzen
und Modellerfahrungen stehen im Schwesterprojekt `opencode-ollama-agents`
(`docs/setup-macos.md`, `docs/performance.md`).

Was die Agenten heute können, wo ihre Grenzen liegen und woran als Nächstes
zu arbeiten ist, steht in [docs/stand-und-fahrplan.md](docs/stand-und-fahrplan.md).
Was die Messung der Agenten ergeben hat, steht in
[docs/messung-erkenntnisse.md](docs/messung-erkenntnisse.md).

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
| Kalenderabgleich | `local_agents/sync.py` | bringt die Datenbank vor jedem Lesen auf den Stand des Kalenders |
| Kalenderwache | `local_agents/watch.py` | Hintergrunddienst: gleicht ab, sobald sich der Kalender ändert |
| Messung | `local_agents/evals.py` | lässt Agenten gegen feste Fälle mit bekannter richtiger Antwort laufen |
| Messung je Agent | `local_agents/eval_suites.py` | Eingaben, Ausführung und Prüfer je Agent |
| Fälle festhalten | `local_agents/eval_capture.py` | legt echte Fälle aus dem Alltag für die Messung ab |
| Datumsangaben | `local_agents/dates.py` | erkennt „morgen", Wochentage und Daten in deutschem Freitext |
| Testhilfen | `local_agents/testing.py` | Antworten des geskripteten Modells für beide Ausgabearten |
| Kurzfassung | `local_agents/briefing.py` | Überblick über die offenen Aufgaben in wenigen Sätzen, zum Vorlesen |
| Sprachausgabe | `local_agents/voice.py` | liest Text mit dem lokalen Sprachmodell Piper vor |
| Spracheingabe | `local_agents/listen.py` | nimmt eine gesprochene Antwort auf und erkennt sie lokal mit Whisper |
| Rückmeldung | `local_agents/feedback.py` | setzt die Antwort auf den Plan um: Aufgaben herausnehmen oder ergänzen |
| Status-Agent | `local_agents/status_agent.py` | kennt Datum und Uhrzeit, nennt die eine Aufgabe, die als Nächstes dran ist |
| Termin-Agent | `local_agents/meeting_agent.py` | erkennt die Art des Termins und welche Ergebnisse dir dafür helfen |
| Terminarten | `local_agents/meeting_types.py` | Katalog: Zweck und übliche Beiträge je Terminart, auch Scrum |
| Orchestrierung | `local_agents/orchestrator.py` | reicht die nächste Aufgabe von Agent zu Agent weiter |
| Nachrichten-Agent | `local_agents/message_agent.py` | schreibt den Entwurf der Nachricht zu einer Verschiebe-Aufgabe und legt ihn zur Freigabe vor |
| Textprüfungen | `local_agents/checks.py` | prüft Texte des Modells, etwa auf Zahlen, die nicht in der Eingabe stehen |
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
   Uhrzeit die eine Aufgabe, die als Nächstes dran ist, und übergibt sie je
   nach Art weiter. Bei einer Vorbereitung erkennt der Termin-Agent die Art
   des Termins und nennt die Ergebnisse, die dir in deiner Rolle dafür helfen;
   Agenten, die diese Ergebnisse erarbeiten, gibt es noch nicht. Bei einer
   Verschiebe-Aufgabe schreibt der Nachrichten-Agent den Entwurf der Nachricht.
5. **Freigeben** (`message_agent`): Du übernimmst den Entwurf, änderst ihn
   oder verwirfst ihn. Das ist der erste Schritt, in dem ein Agent eine
   Aufgabe erledigt.

Zwischen Planen und Abarbeiten ändert sich der Kalender. Deshalb gleicht der
Code die Datenbank vor jedem Lesen der Aufgaben mit dem Kalender ab, siehe
[Kalenderabgleich](#kalenderabgleich).

```bash
.venv/bin/python -m local_agents.planner_agent   # Schritte 1 bis 3
.venv/bin/python -m local_agents.orchestrator    # Schritt 4
.venv/bin/python -m local_agents.message_agent   # Schritt 5
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

Ollama muss das Modell mit 16K Kontext laden, siehe
[Kontextfenster](#kontextfenster). Dann:

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
- Das Fertig-bis-Datum rechnet der Code aus deinen Worten aus, nicht das
  Modell, weil es sich bei Wochentagen verrechnet. Erkannt werden „heute",
  „morgen", Wochentage, „20.11.", „am 20." und „bis zum 20.".
- Der Code führt nur aus, was zu einer vorhandenen Aufgabe passt, und nennt
  jede Änderung einzeln, damit ein Missverständnis auffällt. Was das Modell
  nicht zuordnen kann, steht unter „nicht verstanden" und ändert nichts.
- Eine Aufgabe wird nur herausgenommen, wenn deine Antwort sie erkennbar
  nennt: über ihre Nummer, ihren Tag, ein Wort aus dem Titel oder „alle".
  Rät das Modell bei einer unklaren Antwort, bleibt die Aufgabe stehen und
  der Agent fragt nach („Meinst du #6 …?").
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

Dazu räumt der Abgleich auf, auch für frühere Tage: Hat ein Termin begonnen,
werden seine unerledigten Aufgaben verworfen, denn Vorbereitung oder Nachricht
kämen zu spät. Erledigte und abgelehnte Aufgaben bleiben stehen. Die Stufe
`hinfällig` des Status-Agenten greift damit nur noch, wenn der Abgleich
abgeschaltet oder der Kalender nicht lesbar ist.

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

### Kalenderwache: Abgleich im Hintergrund

Die Kalenderwache (`watch.py`) gleicht ab, sobald sich der Kalender ändert,
ohne dass du etwas aufrufst. macOS meldet jede Änderung
(`EKEventStoreChangedNotification`); die Wache wartet fünf Sekunden Ruhe ab und
startet dann denselben Abgleich. Zusätzlich gleicht sie beim Start und alle 30
Minuten ab, falls eine Meldung verloren ging. Ein Modell braucht sie nicht.

```bash
.venv/bin/python -m local_agents.watch einrichten   # als Hintergrunddienst einrichten und starten
.venv/bin/python -m local_agents.watch status       # läuft er? letzte Zeilen des Protokolls
.venv/bin/python -m local_agents.watch entfernen    # stoppen und entfernen
.venv/bin/python -m local_agents.watch              # im Vordergrund, zum Ausprobieren
```

`einrichten` legt einen LaunchAgent an
(`~/Library/LaunchAgents/local-agents.watch.plist`). Er startet mit der
Anmeldung und wird nach einem Absturz neu gestartet. Das Protokoll steht in
`~/Library/Logs/local-agents/watch.log`.

Hat sich etwas geändert, zeigt die Wache eine macOS-Mitteilung mit denselben
Zeilen wie der Abgleich im Terminal. Einstellungen im Profil, sie gelten ohne
Neustart:

```toml
[watch]
notify = true    # macOS-Mitteilung
speak = false    # zusätzlich vorlesen (Stimme aus [voice])
```

Was dabei zu beachten ist:

- **Kalenderfreigabe:** Der Dienst läuft nicht im Terminal, deshalb braucht
  Python eine eigene Freigabe. macOS fragt beim ersten Start danach; zu sehen
  unter Systemeinstellungen → Datenschutz & Sicherheit → Kalender. Fehlt sie,
  meldet die Wache das einmal und beendet sich.
- **Verzögerung:** Die Meldung kommt erst, wenn macOS die Änderung vom
  Exchange-Konto geholt hat, je nach Konto Sekunden bis Minuten.
- **Mitteilungen:** Sie laufen über `osascript`; sind Mitteilungen dafür in
  den Systemeinstellungen abgeschaltet, bleibt nur das Protokoll.
- **Gleichzeitige Zugriffe:** Schreibt gerade ein anderer Agent in die
  Datenbank, überspringt die Wache den Abgleich und holt ihn beim nächsten
  Anlass nach.
- **Nach einem Umzug des Repos** oder einer neuen `.venv` einmal neu
  `einrichten`, weil der Dienst den Pfad zu Python enthält.

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
| `preferences` | Vorgaben des Nutzers für einzelne Agenten, aus seinen Korrekturen (bisher: Nachrichten-Agent) |
| `runs` | Protokoll: welcher Agent wann lief, mit Status und Kurzfassung. Einträge: `planner` (Planungslauf), `rueckmeldung` (Antwort mit Änderungen ausgewertet), `freigabe` (Plan abgesegnet), `termin` (Termin eingeordnet), `scrum_master` (Nachfrage-Runde), `abgleich` (Kalenderabgleich, nur wenn sich etwas geändert hat), `nachricht` (Entwurf geschrieben) |

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

## Nachrichten-Agent

Der erste Agent, der eine Aufgabe erledigt statt sie nur zu nennen. Für eine
Verschiebe-Aufgabe („Nachricht an <Organisator>: Termin verschieben")
schreibt er den Entwurf der Nachricht und legt ihn dir zur Freigabe vor.
Gesendet wird nichts: „Übernehmen" legt den Text in die Zwischenablage und
hakt die Aufgabe ab; das Abschicken in Teams oder Outlook bleibt bei dir.

```bash
.venv/bin/python -m local_agents.message_agent             # nächste offene Verschiebe-Aufgabe
.venv/bin/python -m local_agents.message_agent 18          # Aufgabe #18
.venv/bin/python -m local_agents.message_agent --neu 18    # neuen Entwurf schreiben
.venv/bin/python -m local_agents.message_agent vorgaben    # gemerkte Vorgaben zeigen
.venv/bin/python -m local_agents.message_agent vergiss 3   # Vorgabe 3 entfernen
```

```text
#18 Nachricht an Erika Muster: Jour fixe (Mi 18.11. 10:00) verschieben, weil Kundentag Kunde ACME

Hallo Erika,

ich muss den Termin „Jour fixe" am Mi 18.11. um 10:00 Uhr verschieben, da ich an
diesem Tag beim Kunden vor Ort bin. Bitte lass mich wissen, wann ein anderer Tag
passen würde.

Viele Grüße
Alex

Übernehmen (Enter), verwerfen (n), Aufgabe ablehnen (a) oder Änderungswunsch eintippen: kürzer, und ohne "muss"
```

Der Ablauf in vier Schritten:

1. **Ausführen:** Das Modell schreibt die Nachricht aus Angaben, die der Code
   zusammenstellt: Art der Nachricht, Empfänger, Termin, Grund, Absender,
   Anrede.
2. **Selbst prüfen:** Der Code prüft den Entwurf und schickt ihn bei Mängeln
   ans Modell zurück (bis zu dreimal). Geprüft wird: Titel und Datum des
   Termins genannt, keine Zahl, die nicht in den Angaben steht (also kein
   erfundener Ersatztermin), kein Platzhalter in Klammern, Anrede mit dem
   Vornamen, du oder Sie durchgehend, Grußformel am Schluss vor dem Namen,
   60 bis 700 Zeichen; bei einer Bitte an
   den Organisator außerdem, dass die Nachricht ihn wirklich bittet, statt zu
   behaupten, du würdest den Termin selbst verschieben. Gelingt es dem
   Modell nicht, gilt eine feste Vorlage; die Ausgabe weist darauf hin.
3. **Freigeben:** Enter übernimmt, `n` verwirft den Entwurf (die Aufgabe
   bleibt offen), `a` lehnt die Aufgabe ab. Alles andere gilt als
   Änderungswunsch in deinen Worten; der Agent schreibt den Entwurf damit neu.
4. **Lernen:** Nach dem Übernehmen fragt der Agent zu jedem Änderungswunsch,
   ob er ihn für künftige Nachrichten merken soll. Gemerkte Vorgaben stehen
   bei jedem neuen Entwurf im Auftrag an das Modell (die letzten fünf).

Drei Arten von Nachrichten, je nach Verschiebe-Aufgabe:

| Aufgabe beginnt mit | Nachricht |
|---|---|
| „Nachricht an …" | Bitte an den Organisator, den Termin auf einen anderen Tag zu legen |
| „Absage an …" (große Runde) | Absage; der Termin findet ohne dich statt |
| „Selbst verschieben: …" | Ankündigung an die Eingeladenen, dass du den Termin verschiebst |

Was der Code dem Modell abnimmt:

- **Der Grund:** Aus „Kundentag Kunde ACME" wird „Ich bin an diesem Tag beim
  Kunden vor Ort.", aus dem internen Block bei hohem Stresslevel „Ich bin im
  Kundenprojekt zurzeit stark eingebunden …". Kundenname und Stresslevel
  stehen nicht in den Angaben für das Modell.
- **Der Vorname:** aus dem Anzeigenamen des Organisators („Erika Muster" oder
  „Muster, Erika"); ist er unbekannt, grüßt die Nachricht ohne Namen.
- **Kein Ersatztermin:** Der Agent kennt deine freien Zeiten nicht. Er bittet
  um einen anderen Tag, schlägt aber keinen vor.

Der Entwurf steht in der Spalte `result` der Aufgabe, die solange den Status
`in_arbeit` hat; ein zweiter Aufruf zeigt denselben Entwurf wieder. Ohne
Terminal (etwa aus der Orchestrierung) wird der Entwurf nur geschrieben und
gezeigt, nicht abgefragt. Die Anrede steht im Profil:

```toml
[messages]
form = "du"    # oder "Sie"
```

Hat der Termin schon begonnen, schreibt der Agent keinen Entwurf mehr: Eine
Nachricht dazu käme zu spät. Der Befehl verwirft solche Verschiebe-Aufgaben
beim Start und nennt sie.

Grenzen: Der Agent sendet nichts und schlägt keinen Ersatztermin vor. Wird
der Termin nach dem Entwurf umbenannt oder verschoben, bleibt der alte Entwurf
gespeichert; `--neu` schreibt ihn neu. Eine gemerkte Vorgabe gilt für alle
Nachrichten, auch wenn sie nur für einen Empfänger gemeint war.

## Suche

Eine lokale Suche über das eigene Material, ohne Sprachmodell. Sie ist der Unterbau
für Agenten, die Inhalte brauchen: Der Code holt die passenden Einträge, das
Modell bekommt nur diese.

```bash
.venv/bin/python -m local_agents.search "Aufbewahrungsfrist Quarantäne"
.venv/bin/python -m local_agents.search -n 10 "Was ist mit QG-3?"   # mehr Treffer (Standard 5)
.venv/bin/python -m local_agents.search index                        # Index abgleichen, Bestand zeigen
```

```text
1. Bemerkung zu QG-3: Quarantäne für abgewiesene Lieferungen
   Bemerkung · Scrum Master, 2026-11-19
   Aufbewahrungsfrist ist mit dem Datenschutz noch nicht geklärt.
2. QG-3: Quarantäne für abgewiesene Lieferungen (Datenqualität im Wareneingang)
   Story · Kunde ACME · datenqualitaet.md
   … der Korrektur lassen sie sich erneut einspielen. ### Technische Hinweise Die Quarantäne liegt in einem eigenen Schema; die Aufbewahrungsfrist ist ein Parameter des nächtlichen Aufräumlaufs.
```

Durchsucht werden:

| Quelle | Ein Eintrag je |
|---|---|
| Story-Dateien der Kontexte mit `stories_dir` | Story und sonstigem Abschnitt (Ziel, Architektur …) |
| Bemerkungen aus dem Scrum Master | Bemerkung, ohne die entfernten |
| Einordnungen des Termin-Agenten | Termin |
| Nachrichten-Agent | übernommener Nachricht |

Einträge über 2400 Zeichen werden an Absätzen geteilt („Teil 1 von 3"), damit
ein Treffer in den Auftrag eines Agenten passt. Asana und der Kalender sind
nicht im Index.

Der Index ist eine eigene Datei neben der Agenten-Datenbank
(`~/.local/share/local-agents/search.db`, anderer Ort:
`LOCAL_AGENTS_SEARCH_DB`). Er ist abgeleitet: Löschen schadet nicht, der
nächste Aufruf baut ihn neu. Vor jeder Suche wird er abgeglichen, und
geschrieben wird nur, was sich geändert hat. Er enthält die Texte der Stories
im Klartext und gehört wie die Datenbank nicht ins Repo.

### Wortsuche

Die Grundlage ist eine Suche nach Worten (Volltextsuche von SQLite, Rangfolge
BM25; ein Treffer im Titel zählt vierfach). Sie braucht kein Modell:

- Groß- und Kleinschreibung und Umlaute spielen keine Rolle („Uberwachung"
  findet „Überwachung"; „Ueberwachung" nicht).
- Endungen zählen nicht: „Schnittstellen" findet „Schnittstelle".
- Füllwörter der Frage werden übergangen, auch Auftragsworte wie „fasse
  zusammen" oder „erkläre". Die übrigen Worte sind mit ODER verbunden; wer
  mehr und seltenere trifft, steht weiter vorn.
- Ein Story-Kürzel in der Frage („QG-3") holt diese Story und ihre
  Bemerkungen nach vorn, auch wenn sonst kein Wort passt.
- Beginnt kein Wort im Bestand wie ein Wort der Frage, wird es als Wortteil
  gesucht: „Frist" findet „Aufbewahrungsfrist" (ab fünf Buchstaben).

### Bedeutungssuche

Mit einem Embedding-Modell findet die Suche auch, was anders formuliert ist:
„Wer darf sich einloggen?" findet die Story „Zugangsverwaltung", obwohl kein
Wort übereinstimmt. Einschalten:

```bash
ollama pull bge-m3
```

```toml
# im Profil
[search]
embeddings = "bge-m3"
```

Das Modell (rund 1,2 GB, mehrsprachig) läuft lokal über Ollama und passt neben
das Sprachmodell. Es übersetzt jeden Eintrag einmal in einen Vektor; beim
ersten Aufruf dauert das für 125 Einträge rund zehn Sekunden, danach wird nur
neu berechnet, was sich geändert hat. Die Vektoren liegen im Index. Ein
Wechsel des Modells berechnet alle neu.

Bei einer Frage entstehen zwei Ranglisten, eine nach Worten und eine nach
Bedeutung, und ein Eintrag steht umso weiter vorn, je weiter vorn er in beiden
steht (Reciprocal Rank Fusion). Der Rang nach Bedeutung zählt doppelt: Das
Embedding liest die ganze Frage, während die Wortsuche schon ein einzelnes
gemeinsames Wort belohnt. Einträge, deren Nähe zur Frage unter 0,50 liegt,
gelten nach Bedeutung als unpassend. Die Ausgabe nennt zu jedem Treffer, wodurch
er gefunden wurde, und die Nähe:

```text
1. AN-3: Zugangsverwaltung und Berechtigungen (Anbindung der Lieferanten)
   Story · Kunde ACME · anbindung.md · gefunden über Bedeutung, Nähe 0.54
```

Antwortet das Embedding-Modell nicht, gilt die Wortsuche, und die Ausgabe sagt
das.

Grenzen:

- **Die Schwelle ist an 25 erfundenen Fragen eingestellt.** Passende Einträge
  lagen dort bei 0,54 bis 0,75, unpassende bei höchstens 0,49 (Fragen, die
  nur aus einem Kürzel oder einem Wortteil bestehen, nicht mitgezählt). Der Abstand ist
  klein; mit echten Texten kann er anders liegen. Zeigt die Ausgabe passende
  Treffer knapp über 0,50 oder fehlen welche, ist das ein Fall für
  `evals merke suche`.
- **Schwelle, Gewicht und Füllwörter habe ich an denselben Fällen
  eingestellt, an denen gemessen wird.** Dass alle bestehen, belegt deshalb
  wenig; echte Fälle fehlen.
- **Fragen nach dem Aufbau** („was kommt als Letztes im Feature?") beantwortet
  keine der beiden Suchen.
- **Ein Allerweltswort genügt der Wortsuche für einen Treffer.** Die
  Bedeutungssuche nimmt solche Treffer nicht wieder heraus, sie stuft sie nur
  herab.

## Gesprächs-Agent

Mit ihm unterhältst du dich über den Inhalt der Stories. Er liest nur; Stories
und Bemerkungen ändert er nicht.

```bash
.venv/bin/python -m local_agents.ask            # Gespräch im Terminal
.venv/bin/python -m local_agents.ask "Frage"    # eine einzelne Frage
```

```text
Du: Fasse in einem Satz zusammen, was im Feature Anbindung als Letztes zu tun ist

Im Feature „Anbindung der Lieferanten“ ist zuletzt die Story AN-4 – Überwachung der Einlieferungen zu erledigen.

Grundlage (letzte Story im Feature „Anbindung der Lieferanten“): AN-4: Überwachung der Einlieferungen

Du: Und welche Akzeptanzkriterien hat sie?

- Eine Übersicht zeigt je Lieferant die letzte Einlieferung.
- Bleibt eine erwartete Lieferung bis 10 Uhr aus, gibt es eine Warnung.

Grundlage (bisheriges Thema): AN-4: Überwachung der Einlieferungen
```

Im Gespräch beendet eine leere Zeile oder `/ende`, `/neu` wechselt das Thema,
und `/quellen` zeigt die Einträge, aus denen die letzte Antwort stammt.

Je Frage läuft dasselbe Muster wie beim Nachrichten-Agenten:

1. **Auswählen (Code):** Der Code entscheidet, welche Einträge das Modell
   bekommt, in dieser Reihenfolge:

   | Die Frage … | Ausgewählt wird |
   |---|---|
   | nennt eine Stelle: „als Letztes", „zuerst", „womit beginnt", „nach QG-3", „vor QG-3" | die Story an dieser Stelle im Feature, mit allen Teilen und ihren Bemerkungen |
   | nennt ein Kürzel: „QG-3" | diese Story mit ihren Bemerkungen |
   | schließt an die vorige Antwort an: beginnt mit „und", verweist („dazu", „dort", „sie") oder hat höchstens zwei Inhaltsworte | die Story der vorigen Antwort |
   | fragt nach dem Überblick: „welche Stories", „Übersicht", „Reihenfolge" | die Gliederung des Features, ohne genanntes Feature die Liste aller Features |
   | sonst | die besten vier Treffer der [Suche](#suche), in einem genannten Feature zuerst dort |

   Ein Feature gilt als genannt, wenn die Frage ein Wort enthält, das nur in
   seinem Titel steht. Zur Auswahl kommt immer die Gliederung des Features:
   die Namen seiner Stories in der Reihenfolge der Datei.
2. **Antworten (Modell):** Das Modell bekommt nur diese Einträge (höchstens
   rund 9000 Zeichen), die Gliederung und die letzten zwei Wortwechsel, und
   antwortet in der verlangten Form.
3. **Prüfen (Code):** Jedes Story-Kürzel und jede Zahl der Antwort muss in dem
   stehen, was das Modell gesehen hat. Hat der Code die Story über ihre Stelle
   bestimmt („als Letztes", „nach QG-3"), muss die Antwort ihr Kürzel nennen,
   denn du kennst es noch nicht; das Modell erfährt dazu, welche Story gemeint
   ist. Sonst geht die Antwort zurück ans
   Modell (bis zu dreimal); gelingt es nicht, zeigt der Agent die Fundstellen
   statt einer Antwort.

Unter jeder Antwort steht die Grundlage: wie ausgewählt wurde und welche
Einträge es waren. Findet der Code nichts, sagt er das selbst, und das Modell
wird nicht gefragt.

Grenzen:

- **„Als Letztes" heißt: zuletzt in der Datei.** Ob diese Story auch zeitlich
  zuletzt dran ist, weiß der Code nicht.
- **Geprüft werden Kürzel und Zahlen, nicht der Sinn.** Eine Antwort kann die
  Einträge falsch wiedergeben, ohne dass die Prüfung es merkt. Die Zeile
  „Grundlage" und `/quellen` sind dafür da, nachzusehen.
- **Die Regeln lesen Worte, nicht Absichten.** „Das letzte Mal, als wir über
  Zugänge sprachen" gilt als Frage nach der letzten Story. Ein neues Thema in
  zwei Worten („Zugänge sperren?") gilt als Anschlussfrage; `/neu` oder ein
  ganzer Satz hilft.
- **Lange Stories werden nach rund 9000 Zeichen abgeschnitten;** die Auswahl
  nimmt die Teile vom Anfang.
- **Das Modell braucht mindestens 8K Kontext.** Mit `qwen3.8:27b-mlx`, das hier
  mit 4096 Tokens lädt, wird der Auftrag abgeschnitten; siehe
  [Kontextfenster](#kontextfenster).
- Fragen über mehrere Features hinweg („wo überall kommt X vor?") beantwortet
  er nur so weit, wie die vier besten Treffer reichen.

Anderes Modell: `fragen = "…"` unter `[models]` im Profil.

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

Das Modell steht im Profil. `default` gilt für alle Agenten, ein Agentenname
nur für diesen einen; ohne Eintrag gilt `gpt-oss:20b` (oder die
Umgebungsvariable `LOCAL_AGENTS_MODEL`):

```toml
[models]
default = "qwen3.8:27b-mlx"
# status = "gpt-oss:20b"   # Agentennamen: planner, rueckmeldung, status, termin,
#                          # nachricht, stories, scrum_master, kalender, asana
```

Die Rückmeldung auf den Plan läuft im Planungs-Agenten und nutzt dessen
Modell (`planner`). Zwei große Modelle passen nicht gleichzeitig in den
Speicher: Bekommen Agenten, die nacheinander laufen, verschiedene Modelle,
lädt Ollama bei jedem Wechsel neu.

Gemessen auf einem Rechner mit 24 GB RAM (2026-11-18), Fälle aus `evals/`, je
fünf Läufe:

| | `gpt-oss:20b` | `qwen3.8:27b-mlx` |
|---|---|---|
| Scrum Master | 73 bis 79 von 80 Läufen, 1 bis 3 s je Lauf | 80 von 80, 5 bis 20 s |
| Rückmeldung auf den Plan | 56 bis 57 von 60, 1 bis 3 s | 60 von 60, 6 bis 17 s |
| Status-Agent | 45 von 45, 1 bis 2 s | 45 von 45, 8 bis 14 s |
| Terminart (je zwei Läufe) | 24 von 26, 3 bis 18 s | Abbruch nach 10 bestandenen Läufen (rund 50 s je Lauf): Speichermangel |
| Story-Erkennung | 19 von 25, unter 1 s | 25 von 25, 8 bis 21 s |
| Status- und Termin-Agent (`orchestrator --neu`), ein Lauf | nicht gemessen | 98 s |
| Speicher geladen | 12 GB | 21 bis 23 GB |

Die Zahlen für den Status-Agenten gelten nach der Korrektur seines Auftrags
(vorher mit Qwen 39 von 45). Bei der Terminart scheiterte `gpt-oss:20b` nur am
Fall mit irreführendem Stichwort; dessen Erwartung war strenger als nötig und
ist seitdem gelockert.

Was daraus folgt:

- Scrum Master und Story-Erkennung: Qwen ist klar besser. Es bestand auch die
  Fälle, an denen `gpt-oss:20b` regelmäßig scheitert (Bitte im Text, zwei
  Stories in einem Satz); bei der Story-Erkennung hält `gpt-oss:20b` die
  Abschnitte eines Protokolls für Stories und übersieht eine Story mit
  unauffälligem Titel.
- Status-Agent und Terminart: `gpt-oss:20b` ist gleich gut und fünf- bis
  zehnmal schneller; beim Termin-Agenten kommt Qwen an die Speichergrenze.
- Rückmeldung auf den Plan: Qwen ist etwas besser (drei Läufe von 60).

Der Preis von Qwen ist die Dauer und der Speicher: Bei 24 GB RAM bleibt neben dem
Modell kaum Platz. Ein erster Messversuch mit weiteren offenen Programmen
blieb stecken (55 Aufrufe in 45 Minuten, Auslagerungsspeicher voll); mit
geschlossenen Programmen lief die Messung in 25 Minuten durch.

Ältere Messung am Status-Agenten mit 15 offenen Aufgaben (2026-11-15, je 2
Läufe):

| Modell | Dauer | Antwort |
|---|---|---|
| `gpt-oss:20b`, Reasoning niedrig | 3–8 s | sachlich richtig |
| `qwen3.5:9b-q8_0` mit Nachdenken | 141–147 s | sachlich richtig, gutes Deutsch |
| `qwen3.5:9b-q8_0` ohne Nachdenken | 3–6 s | erfundene Angabe („in weniger als 6 Minuten"), Grammatikfehler |

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

## Kontextfenster

Die Agenten rechnen mit 16K Tokens Kontext. Ollama lädt ein Modell aber mit
einem Fenster, das es selbst aus dem freien Speicher ableitet; gemessen wurden
4096 und rund 8000 Tokens. Was darüber hinausgeht, schneidet Ollama
stillschweigend ab: Der Agent antwortet dann auf einen Auftrag, dem das Ende
fehlt, ohne dass ein Fehler erscheint.

```bash
.venv/bin/python -m local_agents.context_window                      # Stand je Modell aus dem Profil
.venv/bin/python -m local_agents.context_window einrichten           # Variante des Standardmodells mit 16K
.venv/bin/python -m local_agents.context_window einrichten <modell>  # Variante eines bestimmten Modells
.venv/bin/python -m local_agents.context_window entfernen <variante>
```

```text
! gpt-oss:20b (Standard): Kontext nicht festgelegt, Ollama wählt selbst (weniger als 16384 gemessen)
✓ gpt-oss:20b-ctx16k (Standard): Kontext fest auf 16384 Tokens
```

Es gibt zwei Wege zu 16K:

- **Je Modell (empfohlen):** `einrichten` legt eine Variante an, in der das
  Fenster feststeht, etwa `gpt-oss:20b-ctx16k`. Sie teilt sich die Gewichte
  mit dem Original und belegt keinen weiteren Plattenplatz. Benutzt wird sie,
  sobald sie im Profil unter `[models]` steht; der Befehl nennt die Zeilen.
  So bekommt nur das Modell das größere Fenster, das es verträgt.
- **Für alle Modelle:** Ollama mit `OLLAMA_CONTEXT_LENGTH=16384` starten. Das
  gilt dann auch für große Modelle, die schon ohne das an der Speichergrenze
  laufen.

Je Anfrage lässt sich das Fenster nicht setzen: Die OpenAI-kompatible
Schnittstelle, über die die Agenten mit Ollama sprechen, übergeht die Angabe
(geprüft mit Ollama 0.40), und ein Modell, das über die andere Schnittstelle
mit 16K geladen wurde, lädt Ollama bei der nächsten Anfrage wieder mit dem
eigenen Wert.

Ein größeres Fenster braucht mehr Speicher. Bei `gpt-oss:20b` blieb die
Anzeige von `ollama ps` bei 12 GB; für `qwen3.8:27b-mlx`, das auf einem Rechner
mit 24 GB schon zweimal am Speicher gescheitert ist, ist es nicht erprobt.

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

## Messen

Die Tests unter `tests/` prüfen den Code mit einem geskripteten Modell. Ob das
echte lokale Modell richtig antwortet, misst `evals.py`: Es lässt einen Agenten
gegen feste Fälle laufen, bei denen die richtige Antwort bekannt ist. Weil das
Modell nicht jedes Mal gleich antwortet, läuft jeder Fall mehrfach, und das
Ergebnis ist eine Quote.

Das Programm baut auf [pydantic-evals](https://ai.pydantic.dev/evals/) auf
(Dateiformat der Fälle, wiederholtes Ausführen, Fortschrittsanzeige). Es läuft
ohne Cloud-Dienst. Installation: `.venv/bin/pip install -e '.[evals]'`.

```bash
.venv/bin/python -m local_agents.evals                    # alle Agenten mit Fällen
.venv/bin/python -m local_agents.evals scrum_master       # nur dieser Agent
.venv/bin/python -m local_agents.evals status termin      # mehrere Agenten
.venv/bin/python -m local_agents.evals --laeufe 10        # jeden Fall zehnmal (Standard 5)
.venv/bin/python -m local_agents.evals --modell qwen3.5:9b-q8_0
.venv/bin/python -m local_agents.evals --fall Daily       # nur Fälle mit "Daily" im Namen
.venv/bin/python -m local_agents.evals --liste            # Fälle zeigen, nichts ausführen
.venv/bin/python -m local_agents.evals --schema           # Schema-Dateien neu schreiben
.venv/bin/python -m local_agents.evals merke status       # einen echten Fall festhalten, siehe unten
```

Ollama muss laufen; im Terminal zeigt ein Balken den Fortschritt. Scrum Master
und Rückmeldung zusammen (28 Fälle, je fünfmal) dauern mit `gpt-oss:20b` knapp
fünf Minuten, mit `qwen3.8:27b-mlx` 25 Minuten. Für Status-Agent, Termin-Agent
und Story-Erkennung gibt es noch keine gemessene Dauer; der Termin-Agent
denkt länger nach und braucht je Lauf am meisten. Eine volle Messung aller
Agenten mit einem großen Modell lohnt deshalb je Agent einzeln oder mit
`--laeufe 2`. Antwortet der Modellserver in einem Lauf nicht (abgestürzt, nicht
erreichbar, Zeitlimit), meldet das Programm die Messung als unvollständig und
hält sie nicht fest. Ein Modellaufruf, der länger als zwei Minuten hängt (Termin-Agent:
fünf), zählt als gescheiterter Lauf. Das Programm endet mit 1, wenn ein Fall
nicht in jedem Lauf besteht.

```text
scrum_master · gpt-oss:20b · Ausgabe tool · 5 Läufe je Fall · Stand 96a63e7
  ✓ 5/5    2.0 s  Kürzel und schlichter Text
  ~ 3/5    3.4 s  Bitte weglassen, Inhalt behalten
        Lauf 2: IM-1: „schreib bitte“ gehört nicht in den Text
          Modell: {"remarks":[{"text":"Schreib bitte rein, dass IM-1 ...","story":"IM-1","daily":null}]}
Fälle immer bestanden: 14 von 16 · Läufe bestanden: 75 von 80 · Dauer 3:04 min
Gegenüber dem letzten Lauf (18.11.2026 10:42, Stand 96a63e7):
  schlechter: Bitte weglassen, Inhalt behalten (4/5 → 3/5)
```

`✓` heißt in jedem Lauf bestanden, `~` manchmal, `✗` nie. Bei einem
gescheiterten Lauf steht unter „Modell" wörtlich, was das Modell geantwortet
hat. Daran ist zu sehen, ob das Modell oder der Code danach den Fehler macht.

Gemessen wird der ganze Weg einer Eingabe, also Modell und Code zusammen.
Geprüft wird mit Code am Endzustand, nicht mit einem zweiten Modell:

| Agent | Prüfer | Geprüft wird |
|---|---|---|
| `scrum_master` | `Bemerkungen` | Zahl der Bemerkungen, zugeordnete Story, Daily-Datum, Angaben im Text, zurückgenommene Vermerke |
| `rueckmeldung` | `Aufgaben` | herausgenommene Aufgaben (genau diese), neue Aufgaben mit Fälligkeit, Rückfrage bei Unklarem |
| `status` | `Status` | die Sätze des Modells nennen die Aufgabe, die dran ist, und keine andere; keine Zahl, die nicht in der Eingabe steht; kein „du musst" |
| `termin` | `Terminart` | die erkannte Terminart; das Modell hat selbst geantwortet (kein Rückfall auf den Katalog); Zahl der Ergebnisse |
| `stories` | `Stories` | welche Überschriften eines Dokuments das Modell als Stories erkennt, ohne Hilfe durch Kürzel |
| `nachricht` | `Nachricht` | das Modell schreibt den Entwurf selbst (kein Rückfall auf die Vorlage); die Selbstprüfung findet nichts; verlangte und verbotene Wendungen (kein Kundenname, Absage klingt wie eine Absage, gemerkte Vorgabe eingehalten) |

Nicht gemessen werden die Ergebnisliste des Termin-Agenten (es gibt keine
eindeutig richtige Antwort; geprüft wird nur ihre Länge), die freien Texte von
Kalender- und Asana-Agent, der Planungs-Agent (dort entscheidet der Code) und
die Spracheingabe.

### Schwankung beachten

Zwei Messläufe mit demselben Modell und demselben Code unterscheiden sich um
einige Läufe (beim Scrum Master gemessen: 75 und 79 von 80). Ein Unterschied
in dieser Größe zwischen zwei Einstellungen sagt deshalb nichts. Wer zwei
Modelle oder Einstellungen vergleichen will, misst jede mehrmals oder mit
`--laeufe 10` und mehr.

### Fälle

Fälle stehen als YAML-Dateien im Format von pydantic-evals an zwei Orten.
Beide werden gelesen; der Dateiname beginnt mit dem Namen des Agenten
(`scrum_master.yaml`, `scrum_master-alltag.yaml`):

- `evals/` im Repo: erfundene Fälle.
- `~/.local/share/local-agents/evals/` (anderer Ort: `LOCAL_AGENTS_EVALS`):
  echte Fälle aus dem Alltag. Sie enthalten Story-Inhalte und Termintitel und
  bleiben deshalb außerhalb des Repos.

Die Suche (`suche.yaml`) wird ohne Sprachmodell gemessen, also mit einem Lauf
je Fall und in Sekundenbruchteilen. Die Beispieldokumente dazu liegen in
`evals/suche_vault/`.

```bash
.venv/bin/python -m local_agents.evals suche                   # nur Wortsuche; läuft auch in den Tests mit
.venv/bin/python -m local_agents.evals suche --modell bge-m3   # mit Bedeutungssuche
```

Drei Fälle, deren Name mit „Grenze der Wortsuche" beginnt, scheitern ohne
Embedding-Modell mit Absicht. Stand 2026-11-20: Wortsuche 18 von 21,
mit `bge-m3` 21 von 21.

Geht im Alltag etwas schief, wird daraus zuerst ein Fall und dann die
Korrektur. So wächst die Sammlung an den Stellen, an denen das Modell wirklich
Fehler macht.

Die Umgebung eines Falls (das „heute" und die Stories oder Aufgaben) steht
beim ersten Fall einer Datei und wird mit einem YAML-Anker wiederverwendet.
Für den Scrum Master:

```yaml
# yaml-language-server: $schema=scrum_master_schema.json
name: scrum_master
cases:
- name: "Datum bleibt im Text, kein Daily erfunden"
  inputs:
    umgebung: &umgebung
      heute: 2026-11-16            # Wochentage und "morgen" beziehen sich darauf
      stories:                     # die Stories, die der Agent kennt
      - {kuerzel: "QG-3", titel: "Freigabe der Schnittstelle", feature: "Bestellimport"}
      # statt stories geht auch ein Ordner: stories_dir: "~/pfad/zu/stories"
    vorher: []                     # frühere Antworten derselben Runde, ungeprüft
    eingabe: "Bei QG-3 warten wir bis 24.11. auf die Freigabe"
  evaluators:
  - Bemerkungen:
      entfernt: 0                  # so viele Vermerke sind danach zurückgenommen
      erwartet:                    # je erwarteter Bemerkung ein Eintrag
      - story: "QG-3"              # Kürzel, sonst Name; "" = keiner Story zugeordnet
        daily: false               # false = ohne Daily-Bezug, sonst ein Datum
        enthaelt: ["24.11."]       # muss im Text stehen
        enthaelt_nicht: []         # darf nicht im Text stehen
- name: "Nichts Neues vermerkt nichts"
  inputs:
    umgebung: *umgebung            # dieselbe Umgebung wie oben
    eingabe: "nein, nichts Neues"
  evaluators:
  - Bemerkungen                    # ohne Angaben: keine Bemerkung, nichts entfernt
```

Für die Rückmeldung auf den Plan:

```yaml
# yaml-language-server: $schema=rueckmeldung_schema.json
name: rueckmeldung
cases:
- name: "Herausnehmen und ergänzen in einer Antwort"
  inputs:
    umgebung: &umgebung
      heute: 2026-11-20
      aufgaben:                    # bekommen die Nummern 1, 2, 3 ... in dieser Reihenfolge
      - {titel: "Vorbereitung: Daily (Mo 23.11. 09:00)", faellig: 2026-11-22}
    eingabe: "die Vorbereitung fürs Daily raus, dafür bis zum 25. die Demo aufsetzen"
  evaluators:
  - Aufgaben:
      raus: [1]                    # genau diese Nummern sind danach abgelehnt
      unklar: false                # ob der Agent etwas nicht verstanden meldet
      neu:                         # je erwarteter neuer Aufgabe ein Eintrag
      - {titel_enthaelt: "Demo", faellig: 2026-11-25}
```

Für die drei weiteren Agenten, je ein Fall:

```yaml
# status.yaml: Welche Aufgabe dran ist, bestimmt der Code; geprüft werden die Sätze des Modells.
- name: "Überfällige Aufgabe geht vor"
  inputs:
    umgebung:
      jetzt: 2026-11-16T09:00:00
      aufgaben:                    # Nummern 1, 2, 3 ... in dieser Reihenfolge
      - {titel: "Vorbereitung: Jour fixe (Di 17.11. 14:00)", faellig: 2026-11-16, termin: 2026-11-17T14:00:00}
      - {titel: "Vorbereitung: Kundenworkshop (Mo 16.11. 13:00)", faellig: 2026-11-15, termin: 2026-11-16T13:00:00}
    # eingabe: "Womit soll ich anfangen?"   (ohne Angabe die Standardfrage)
  evaluators:
  - Status:
      naechste: 2                  # Nummer der Aufgabe, die dran ist; "keine" = nichts steht an
      nennt: ["Kundenworkshop"]    # muss in der Antwort stehen
      nennt_nicht: ["Jour fixe"]   # darf nicht in der Antwort stehen

# termin.yaml: ids der Terminarten stehen in local_agents/meeting_types.py
- name: "Bericht an die Leitung ohne Stichwort"
  inputs:
    umgebung: {rolle_intern: "Teamleiter", rolle_kunde: "Solution Architect"}
    titel: "Quartalsbericht an die Geschäftsführung"
    beginn: 2026-11-24T14:00:00
    minuten: 60
    eingeladene: 9
    kontext: intern                # intern oder kunde
  evaluators:
  - Terminart: {art: [steuerung, praesentation]}   # eine id oder mehrere vertretbare

# stories.yaml: ein Dokument als Text (markdown: |) oder als Datei
- name: "Datei mit Kürzeln, dem Modell ohne Kürzel gezeigt"
  inputs: {datei: "tests/data/feature_beispiel.md", ohne_kuerzel: true}
  evaluators:
  - Stories: {wie_regel: true}     # erwartet, was die Regel an den Kürzeln erkennt;
                                   # sonst erwartet: ["Überschrift A", "Überschrift B"]
```

Angaben, die fehlen, werden nicht geprüft; die Zahl der Bemerkungen, `raus` und
die Zahl neuer Aufgaben werden immer geprüft. Unbekannte Schlüssel (etwa ein
Tippfehler wie `dayli`) sind ein Fehler beim Laden. Die Schema-Dateien
`evals/*_schema.json` lassen den Editor die Falldateien prüfen; nach einer
Änderung an Eingaben oder Prüfern schreibt `--schema` sie neu (ein Test
erinnert daran).

### Echte Fälle festhalten

Die Fälle im Repo sind erfunden. Aussagekräftig wird die Messung mit Fällen
aus dem Alltag, vor allem mit solchen, bei denen ein Agent danebenlag. `merke`
legt einen solchen Fall im privaten Ordner ab, in `<agent>-alltag.yaml`:

```bash
.venv/bin/python -m local_agents.evals merke scrum_master              # die letzte Antwort an den Scrum Master
.venv/bin/python -m local_agents.evals merke scrum_master "Wortlaut"   # eine bestimmte Antwort
.venv/bin/python -m local_agents.evals merke rueckmeldung "Wortlaut"   # eine Antwort auf den Plan, mit den jetzt offenen Aufgaben
.venv/bin/python -m local_agents.evals merke status                    # die jetzt offenen Aufgaben zur jetzigen Uhrzeit
.venv/bin/python -m local_agents.evals merke termin 12                 # der Termin hinter Aufgabe #12
.venv/bin/python -m local_agents.evals merke stories ~/pfad/feature.md # eine Datei mit Stories
.venv/bin/python -m local_agents.evals merke nachricht 18              # die Verschiebe-Aufgabe #18 mit ihrem Termin
.venv/bin/python -m local_agents.evals merke suche "Frage"             # eine Suchanfrage gegen deinen echten Bestand
.venv/bin/python -m local_agents.evals merke fragen "Frage"            # eine Frage an den Gesprächs-Agenten, echter Bestand
```

Als Erwartung steht im Fall zunächst, was der Agent tatsächlich getan hat
(vermerkte Bemerkungen, erkannte Terminart). War das falsch, trägst du in der
Datei ein, was richtig gewesen wäre; erst dann misst der Fall etwas. Bei
`rueckmeldung` und `status` ist die Erwartung leer bzw. nur die Aufgabennummer
und wird von Hand ergänzt (`raus`, `neu`, `nennt`). Die Dateien enthalten
Termintitel und Story-Inhalte und bleiben außerhalb des Repos.

### Ergebnisse und Vergleich

Jeder vollständige Messlauf wird als Zeile in
`~/.local/share/local-agents/evals/ergebnisse.jsonl` festgehalten: Zeit, Agent,
Modell, Ausgabeart, Code-Stand (Commit, mit `+` bei ungespeicherten Änderungen)
und je Fall die bestandenen Läufe. Die Ausgabe vergleicht mit dem letzten Lauf
desselben Agenten mit demselben Modell und derselben Ausgabeart. Läufe mit
`--fall` werden nicht festgehalten, weil sie nur einen Teil der Fälle messen.
Den Verlauf führt das Programm selbst; pydantic-evals vergleicht Berichte nur
innerhalb eines Programmlaufs.

### Ausgabeart

Agenten mit festem Antwortschema (Scrum Master, Rückmeldung, Termin-Agent,
Story-Erkennung) können ihre Antwort auf zwei Arten abgeben:

| `LOCAL_AGENTS_OUTPUT` | Bedeutung |
|---|---|
| `tool` (Standard) | Das Modell ruft ein Ausgabe-Werkzeug auf. |
| `native` | Ollama erzwingt das Schema beim Erzeugen der Antwort. |

Gemessen mit `gpt-oss:20b` gibt es keinen Unterschied, der über die Schwankung
hinausgeht (Zahlen unter „Stand"). Zum Nachmessen:
`LOCAL_AGENTS_OUTPUT=native .venv/bin/python -m local_agents.evals`.

Grenzen: Mit einem guten Dutzend Fällen je Agent zeigt die Messung Rückschritte
und grobe Unterschiede zwischen Modellen, keine feinen. Die Fälle für
Terminarten ohne Stichwort im Titel sind noch nicht gegen ein echtes Modell
gelaufen (Abbruch durch Speichermangel).

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
- Kalenderwache (2026-11-16): Start im Vordergrund gegen eine Kopie der
  Datenbank geprüft; ein von `launchd` gestarteter Python-Prozess bekam die
  Kalenderfreigabe. Der eingerichtete Hintergrunddienst ist vom Nutzer von
  Hand erprobt (Termin anlegen, verschieben, löschen) und hat funktioniert.
  Verhalten nach Ruhezustand und Neuanmeldung ist nicht erprobt.
- Erste Messung mit `gpt-oss:20b`, erfundene Fälle, je drei Läufe (2026-11-16):
  - Scrum Master: 15 von 16 Fällen immer bestanden, 47 von 48 Läufen. Einmal
    blieb die Bitte („schreib bitte rein, dass") im Text der Bemerkung.
  - Rückmeldung auf den Plan: 9 von 12 Fällen immer bestanden, 30 von 36
    Läufen. Zwei Schwächen, beide noch nicht behoben: Bei „bis Dienstag" und
    „bis zum 25." stimmt die Fälligkeit der neuen Aufgabe nicht immer (einmal
    heute, einmal eine Woche zu spät), und eine unklare Antwort („das mit dem
    Dings passt mir nicht") führte in allen drei Läufen dazu, dass eine
    Aufgabe herausgenommen wurde, statt nachzufragen.
- Zweite Messung nach der Korrektur der Rückmeldung, je fünf Läufe
  (2026-11-16, `gpt-oss:20b`):
  - Rückmeldung auf den Plan: 57 von 60 Läufen. Das Fertig-bis-Datum rechnet
    jetzt der Code aus den Worten des Nutzers, und eine Aufgabe fliegt nur
    raus, wenn die Antwort sie erkennbar nennt. Die unklare Antwort führte in
    5 von 5 Läufen zur Rückfrage (vorher 0 von 3). Je ein Lauf scheiterte
    noch bei „bis Dienstag" (keine Aufgabe angelegt), bei „raus, dafür bis zum
    25." (heute statt 25.) und bei „beide Aufgaben zur Teamrunde" (nur eine
    herausgenommen).
  - Scrum Master, unverändert: 73 von 80 Läufen. Mit fünf Läufen zeigen sich
    Schwankungen, die drei Läufe verdeckt hatten: Die Bitte „schreib bitte
    rein, dass" blieb in 4 von 5 Läufen im Text, die Zuordnung über ein
    Stichwort aus dem Namen gelang in 3 von 5, der Satz mit zwei Stories in
    4 von 5. Noch nicht behoben.
- Ausgabeart `tool` gegen `native` mit `gpt-oss:20b`, je fünf Läufe
  (2026-11-18): Scrum Master 73 von 80 (`tool`) gegen 79 und 75 von 80
  (`native`, zwei Messläufe); Rückmeldung 57 von 60 gegen 57 und 56 von 60.
  Der Abstand zwischen den beiden `native`-Läufen ist so groß wie der zu
  `tool`, also kein belegter Unterschied. Der Standard bleibt `tool`.
- `qwen3.8:27b-mlx` (2026-11-18): besteht alle 28 Fälle in allen fünf Läufen
  (140 von 140), braucht dafür 25 statt knapp 5 Minuten. Zahlen und
  Speicherbedarf: Abschnitt [Modell je Agent](#modell-je-agent).
- Offene Schwächen mit `gpt-oss:20b` laut Messung (2026-11-18): Der Scrum Master lässt die Bitte
  („schreib bitte rein, dass") oft im Text und teilt einen Satz zu zwei
  Stories nicht immer auf; die Rückmeldung nimmt bei „beide Aufgaben zur
  Teamrunde" öfter nur eine heraus.
- Messung von Status-Agent, Terminart und Story-Erkennung mit beiden Modellen
  (2026-11-18, vom Nutzer ausgeführt); Zahlen im Abschnitt
  [Modell je Agent](#modell-je-agent). Was die Messung gefunden hat:
  - Status-Agent: Qwen rechnete die Zeit bis zum Termin selbst um und lag
    daneben („knapp 24 Stunden" bei 25) und leitete aus „Plan noch nicht
    abgesegnet" eine Behauptung ab. Seit der Auftrag Zeitangaben wörtlich
    verlangt und die Freigabe nicht mehr enthält: 45 von 45 statt 39 von 45.
  - Terminart mit Qwen: nach zehn bestandenen Läufen stürzte der Modellserver
    mit „Insufficient Memory" ab. Solche Messungen hält das Programm nicht
    mehr fest.
  - Story-Erkennung mit `gpt-oss:20b`: 19 von 25; die Abschnitte eines
    Protokolls gelten in 3 von 5 Läufen als Stories. Ohne Folgen, solange die
    Story-Dateien Kürzel haben, denn dann entscheidet die Regel.
- Der Befehl `merke` (2026-11-18): mit geskriptetem Modell getestet und gegen
  eine Kopie der echten Datenbank ausgeführt.
- Nachrichten-Agent (2026-11-19): gebaut und mit geskriptetem Modell
  getestet. Ein einzelner Lauf mit einem erfundenen Termin gegen
  `gpt-oss:20b` lieferte in 8 s einen Entwurf, an dem die Selbstprüfung nichts
  fand. Die neun Messfälle sind noch nicht gegen ein Modell gelaufen. Erster
  Lauf des Nutzers gegen die echte Datenbank: Der Agent schrieb einen Entwurf
  zu einem Termin vom Vortag und formulierte „ich möchte den Termin
  verschieben", obwohl der Empfänger eingeladen hatte. Beides ist behoben
  (kein Entwurf nach Terminbeginn; die Bitte an den Organisator wird geprüft),
  aber noch nicht erneut von Hand erprobt.
- Noch offen: weitere Agenten, die Aufgaben selbst erledigen; vom Nutzer ergänzte
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

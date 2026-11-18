# Stand und Fahrplan

Stand: 2026-11-18. Diese Seite beschreibt, was die Agenten heute können, wo
ihre Grenzen liegen und woran als Nächstes zu arbeiten ist. Wie die Bausteine
bedient werden, steht in der [README](../README.md). Was die Messung der
Agenten ergeben hat, steht in [messung-erkenntnisse.md](messung-erkenntnisse.md).

Die Seite ist eine Momentaufnahme. Wer einen Baustein ändert, der hier
beschrieben ist, zieht die betroffene Zeile nach und setzt das Datum oben neu.

## Bestand

Elf Agenten und Bausteine lesen Kalender, Asana und Story-Dateien, machen
daraus Aufgaben und Notizen und sprechen mit dem Nutzer. Keiner davon erledigt
bisher selbst eine Aufgabe.

| Baustein | Modul | Kann | Grenzen |
|---|---|---|---|
| Kalender-Agent | `calendar_agent.py` | Fasst die Woche zusammen, ordnet Termine nach Kontext (intern/Kunde), kennt Feiertage, schlägt Verschiebungen vor | Liest nur Titel, Ort und Mail-Domains, keine Beschreibungen oder Anhänge. Schreibt nichts in den Kalender. |
| Asana-Agent | `asana_agent.py` | Findet Erwähnungen und gefolgte Aufgaben der letzten Tage | Nur lesend, nur Kommentar-Auszüge. Nicht mit der Aufgaben-Datenbank verbunden. |
| Planungs-Agent | `planner_agent.py` | Legt je Termin Vorbereitungs- und Verschiebe-Aufgaben mit Fälligkeit an, liest eine Kurzfassung vor, nimmt Rückmeldung an | Regeln sind fest im Code. Manuell ergänzte Aufgaben fehlen in Planliste und Kurzfassung. Plant nur auf Aufruf. |
| Kalenderabgleich und Kalenderwache | `sync.py`, `watch.py` | Halten die Datenbank im Hintergrund aktuell, erkennen verschobene, abgesagte und neue Termine | Serienfälle sind nur in Tests nachgestellt. Verhalten nach Ruhezustand und Neuanmeldung ist nicht erprobt. |
| Status-Agent | `status_agent.py` | Nennt die eine Aufgabe, die jetzt dran ist | Reihenfolge nur nach Fälligkeit, ohne Aufwand, Wichtigkeit oder freie Zeit im Kalender. |
| Termin-Agent und Orchestrierung | `meeting_agent.py`, `orchestrator.py` | Erkennt die Terminart (14 Typen) und nennt Ergebnisse, die dem Nutzer in seiner Rolle helfen | Rät aus Titel und Organisator. Die Ergebnisse schwanken zwischen Läufen, und niemand erarbeitet sie. |
| Story-Leser | `stories.py` | Liest User Stories aus Markdown unabhängig vom Aufbau der Dateien | Nur Markdown im Ordner, kein Anschluss an das Projektwerkzeug. Erkennt keine Änderungen zwischen zwei Ständen. |
| Scrum Master | `scrum_master.py`, `telegram_bot.py` | Vermerkt Bemerkungen je Story, auf Wunsch fürs nächste Daily, per Terminal, Dialog oder Telegram | Beantwortet keine Fragen. Ein Satz zu mehreren Stories landet ganz bei jeder. Der Daily-Bezug hängt an Stichwörtern. |
| Sprache | `voice.py`, `listen.py` | Vorlesen (Piper) und Zuhören (Whisper), beides lokal | Kein durchgehendes Gespräch, nur Frage und Antwort an einer Stelle im Planungs-Agenten. |
| Messung | `evals.py`, `eval_suites.py`, `eval_capture.py` | Lässt fünf Agenten gegen feste Fälle laufen (auf Basis von pydantic-evals): Scrum Master, Rückmeldung, Status-Agent, Terminart, Story-Erkennung. Mehrfach, mit Quote, Mitschnitt der Modellantwort und Vergleich zum letzten Lauf. `merke` hält echte Fälle fest. | 55 erfundene Fälle, noch keine echten. Nicht gemessen: Ergebnisliste des Termin-Agenten, freie Texte von Kalender- und Asana-Agent, Spracheingabe. |
| Profil und Datenbank | `profile.py`, `store.py` | Gemeinsames Gedächtnis: Kontexte, Rolle, Stresslevel, Aufgaben, Notizen, Laufprotokoll | Kein Gedächtnis über Gespräche, keine gelernten Vorlieben. |

## Fahrplan je Kategorie

Der Maßstab sind die Agentensysteme der großen Labs, mit der Einschränkung,
dass hier ein kleines lokales Modell läuft.

### 1. Ausführen statt auflisten

Die größte Lücke. Die Systeme der großen Labs erledigen Aufgaben in einer
Schleife aus Werkzeugaufruf, Prüfung und Korrektur. Hier endet die Kette bei
„das würde dir helfen". Der nächste Schritt sind Agenten, die ein Ergebnis
wirklich erstellen und in der Spalte `result` der Aufgabe ablegen:

- Entwurf einer Agenda für einen Termin
- Entwurf der Nachricht, mit der ein Termin verschoben wird
- Zusammenfassung einer Story fürs Refinement

### 2. Wissen heranholen

Ein kleines Modell weiß wenig und hat 16K Kontext. Es braucht eine lokale Suche
über Stories, Notizen, frühere Ergebnisse und später Dokumente, die nur die
passenden Abschnitte in den Kontext legt. Das ist auch die Voraussetzung dafür,
dass der Scrum Master Fragen zu einem Ticket beantworten kann.

### 3. Messen

Die Labs verbessern Agenten über feste Testfälle mit Bewertung, nicht über
Eindrücke. Hier gibt es viele Code-Tests, aber kaum Messungen der
Modellantworten. Nötig ist je Agent ein kleiner Satz echter Fälle mit
erwarteter Antwort, gegen den jedes Modell und jede Prompt-Änderung läuft.

Stand 2026-11-16: Das Messprogramm steht (`evals.py`, README-Abschnitt
„Messen") mit erfundenen Fällen für Scrum Master und Rückmeldung. Die erste
Messung fand zwei Schwächen der Rückmeldung (Fälligkeit bei Wochentagen,
unklare Antworten nehmen eine Aufgabe heraus). Beide sind behoben und
nachgemessen (57 von 60 Läufen statt 30 von 36). Als Nächstes:

- Scrum Master: die Bitte („schreib bitte rein, dass") bleibt oft im Text,
  die Zuordnung über ein Stichwort aus dem Namen schwankt
- Rückmeldung: bei „beide Aufgaben zu einem Termin" wird öfter nur eine
  herausgenommen
- echte Fälle aus dem Alltag sammeln (`evals merke`)
- Regel: Jeder neue Agent bekommt seine Fälle gleich mit
- Unterschiede zwischen Einstellungen nur mit mehr Läufen beurteilen: Zwei
  Messläufe desselben Stands unterscheiden sich um einige Läufe

### 4. Verlässlichkeit des kleinen Modells

Das bisherige Muster trägt und bleibt Regel: Der Code entscheidet und rechnet,
das Modell formuliert und ordnet zu. Dazu kommen:

- strukturierte Ausgaben mit Prüfung
- eine zweite Prüfrunde bei wichtigen Antworten
- ein eigenes Modell je Aufgabe (`[models]` im Profil)

### 5. Gedächtnis und Lernen

Korrekturen des Nutzers sollen künftige Läufe ändern, zum Beispiel abgelehnte
Aufgaben („für diese Serie nie eine Vorbereitung"). Heute merkt sich das System
nur den Einzelfall.

### 6. Eigeninitiative

Die Kalenderwache ist der erste Baustein, der von selbst läuft. Planung am
Freitag, Status am Morgen und Nachfrage des Scrum Masters laufen noch auf
Zuruf.

### 7. Gespräch

Es fehlt ein Einstieg, über den der Nutzer frei spricht oder tippt und der an
den passenden Agenten weiterleitet, mit Gesprächsverlauf.

## Einordnung

Das Niveau der großen Labs kommt zum größten Teil aus dem Modell selbst. Ein
Modell mit 20 Milliarden Parametern wird bei offenen, mehrstufigen Aufgaben
nicht gleichziehen, egal wie gut das Drumherum ist. Erreichbar ist dagegen,
dass es in eng umrissenen Abläufen ähnlich verlässlich wirkt, weil Code, Suche
und Prüfungen ihm die schweren Teile abnehmen.

## Empfohlene Reihenfolge

1. Messen (Kategorie 3)
2. Wissen heranholen (Kategorie 2)
3. Der erste ausführende Agent (Kategorie 1)

Ohne Messung ist nicht zu erkennen, ob die anderen Schritte etwas verbessern.

## Entscheidungen

- 2026-11-16: Ein eigener Daily-Agent ist zurückgestellt. Der Scrum Master
  deckt die Vorbereitung des Dailys vorerst ab.
- 2026-11-18: Die Ausgabeart bleibt `tool`. Die Messung zeigte für `native`
  keinen Unterschied, der über die Schwankung hinausgeht.
- 2026-11-18: Das Modell wird im Profil gewählt (`[models] default`). Der
  Code-Standard bleibt `gpt-oss:20b`. `qwen3.8:27b-mlx` besteht alle Messfälle
  (140 von 140 Läufen gegen 130 bis 136), ist aber etwa fünfmal langsamer und
  belegt 21 bis 23 GB von 24 GB RAM.
- 2026-11-18: Modell je Agent nach den Messwerten: `qwen3.8:27b-mlx` für Scrum
  Master und Story-Erkennung, wo es klar besser ist; `gpt-oss:20b` für alles
  andere, weil es bei Status-Agent und Terminart gleich gut und fünf- bis
  zehnmal schneller ist. Eingestellt wird das im Profil, nicht im Repo.

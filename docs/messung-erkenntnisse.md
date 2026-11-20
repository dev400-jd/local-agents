# Messung der Agenten: Erkenntnisse

Stand: 2026-11-18. Diese Seite hält fest, was die ersten Messungen der Agenten
gegen echte lokale Modelle ergeben haben: Ergebnisse je Agent und Modell,
gefundene Schwächen und ihre Korrekturen, und was wir über das Messen selbst
gelernt haben. Wie die Messung bedient wird, steht in der
[README](../README.md#messen); der Fahrplan steht in
[stand-und-fahrplan.md](stand-und-fahrplan.md).

Gemessen wurde vom 16. bis 18.11.2026 auf einem Rechner mit 24 GB RAM, mit 55
erfundenen Fällen. Echte Fälle aus dem Alltag gibt es noch nicht.

## Kurzfassung

- Die Messung hat vier Schwächen gefunden, die vorher niemandem aufgefallen
  waren. Alle vier sind behoben und nachgemessen.
- `qwen3.8:27b-mlx` ist bei Scrum Master und Story-Erkennung klar besser als
  `gpt-oss:20b`. Bei Status-Agent und Terminart ist `gpt-oss:20b` gleich gut und
  fünf- bis zehnmal schneller.
- Qwen belegt 21 bis 23 GB von 24 GB RAM und ist zweimal am Speicher
  gescheitert. Es läuft deshalb nur für die Agenten, bei denen es messbar
  besser ist.
- Zwei Messläufe desselben Stands unterscheiden sich um einige Läufe. Zwei
  vorschnelle Urteile aus je einem Messlauf mussten wir zurücknehmen.

## Ergebnisse je Agent und Modell

Bestandene Läufe von allen Läufen; je Fall fünf Läufe, bei der Terminart zwei.

| Agent | Fälle | `gpt-oss:20b` | `qwen3.8:27b-mlx` |
|---|---|---|---|
| Scrum Master | 16 | 73 bis 79 von 80, 1 bis 3 s je Lauf | 80 von 80, 5 bis 20 s |
| Rückmeldung auf den Plan | 12 | 56 bis 57 von 60, 1 bis 3 s | 60 von 60, 6 bis 17 s |
| Status-Agent | 9 | 45 von 45, 1 bis 2 s | 45 von 45, 8 bis 14 s |
| Terminart | 13 | 24 von 26, 3 bis 18 s | Abbruch nach 10 bestandenen Läufen (rund 50 s je Lauf): Speichermangel |
| Story-Erkennung | 5 | 19 von 25, unter 1 s | 25 von 25, 8 bis 21 s |
| Gesprächs-Agent | 12 | 47, nach Korrekturen 60 von 60, 1 bis 2 s | nicht gemessen |
| Nachrichten-Agent | 10 | 49 von 50, 2 bis 4 s | 42 von 45, 24 bis 70 s (9 Fälle, vor der Lockerung) |

Die Zahlen gelten nach den Korrekturen aus dem nächsten Abschnitt. Beim
Nachrichten-Agenten (gemessen am 19.11.) lagen im ersten Messlauf alle fünf
gescheiterten Läufe an zu strengen Erwartungen der Fälle („Hi Erika" statt
„Hallo Erika", „verschoben" statt „verschieben"); nach der Lockerung und mit
der Prüfung auf eine Grußformel bestand `gpt-oss:20b` 49 von 50 Läufen. Der
eine gescheiterte Lauf mischte bei förmlicher Anrede Sie und du („Sie Erika,
kannst du"); seitdem gibt der Code die Begrüßungszeile wörtlich vor. Das ist
nicht nachgemessen, ebenso wenig Qwen mit den gelockerten Fällen. Die Spannen
bei `gpt-oss:20b` stammen aus mehreren Messläufen desselben Stands.

## Gefundene Schwächen und was daraus wurde

| Agent | Befund | Korrektur | Nachgemessen |
|---|---|---|---|
| Rückmeldung | Bei „bis Dienstag" stimmte das Fertig-bis-Datum der neuen Aufgabe nicht (einmal heute, einmal eine Woche zu spät). | Das Datum rechnet der Code aus den Worten des Nutzers; das Modell gibt nur die Worte wieder. | 1 von 3 → 4 bis 5 von 5 |
| Rückmeldung | Auf eine unklare Antwort („das mit dem Dings passt mir nicht") nahm der Agent eine Aufgabe heraus, statt nachzufragen. | Eine Aufgabe fliegt nur raus, wenn die Antwort sie erkennbar nennt (Nummer, Tag, Wort aus dem Titel); sonst Rückfrage. | 0 von 3 → 5 von 5 |
| Status-Agent | Qwen rechnete die Zeit bis zum Termin selbst um und lag daneben („knapp 24 Stunden" bei 25). | Der Auftrag verlangt, Zeitangaben wörtlich zu übernehmen. | 39 von 45 → 45 von 45 |
| Status-Agent | Aus „Plan noch nicht abgesegnet" leitete Qwen Behauptungen ab („erst nach Freigabe"). | Der Stand der Freigabe steht nur noch in der Überschrift für den Nutzer, nicht mehr im Auftrag. | in den Mitschnitten nicht mehr aufgetreten; kein Prüfer dafür |

Dazu am 20.11. beim Gesprächs-Agenten (erster Messlauf 47 von 60, danach 60
von 60):

| Befund | Korrektur |
|---|---|
| Der Code löste „nach QG-3" richtig zu QG-4 auf, sagte es dem Modell aber nicht; das Modell meldete, zu QG-3 stehe nichts da. | Der Auftrag nennt die gemeinte Story. |
| Bei „als Letztes" beschrieb die Antwort die Story, ohne ihr Kürzel zu nennen. | Die Selbstprüfung verlangt das Kürzel, wenn der Code die Story über ihre Stelle bestimmt hat. |
| „30 Tage" stand in der Antwort und galt doch als fehlend: geschütztes Leerzeichen zwischen Zahl und Einheit. | Der Agent ersetzt solche Leerzeichen. |

Den ersten Befund hatte ein Probelauf von Hand mit fünf Fragen nicht gezeigt.

Weiter bestehende Schwächen, alle mit `gpt-oss:20b`:

- **Scrum Master:** Die Bitte („schreib bitte rein, dass") bleibt oft im Text
  der Bemerkung. Ein Satz zu zwei Stories wird nicht immer aufgeteilt; der
  Mitschnitt zeigt, dass das Modell dann nur eine Bemerkung liefert. Mit Qwen
  tritt beides nicht auf.
- **Rückmeldung:** Bei „beide Aufgaben zur Teamrunde raus" wird öfter nur eine
  herausgenommen (zuletzt 2 von 5 richtig).
- **Story-Erkennung:** Die Abschnitte eines Protokolls gelten in 3 von 5 Läufen
  als Stories, und eine Story mit unauffälligem Titel („Offene Punkte klären")
  wird übersehen. Ohne Folgen, solange die Story-Dateien Kürzel haben, denn
  dann entscheidet die Regel und nicht das Modell.

## Was wir über das Messen gelernt haben

**Ein Messlauf ist kein Beleg.** Derselbe Stand ergab beim Scrum Master 73, 75
und 79 von 80 Läufen. Zwei Urteile aus je einem Lauf mussten wir zurücknehmen:

- Die native Ausgabe schien nach einem Lauf besser (79 gegen 73 von 80) und war
  schon als Standard eingestellt. Der zweite Lauf ergab 75. Der Standard bleibt
  `tool`.
- Qwen galt nach dem ersten Versuch als „nicht brauchbar". Der Versuch war am
  vollen Speicher gescheitert, nicht am Modell; mit geschlossenen Programmen
  bestand es alle Fälle.

Wer zwei Einstellungen vergleicht, misst jede mehrmals oder mit `--laeufe 10`
und mehr. Drei Läufe je Fall verdecken Schwankungen, die fünf zeigen.

**Auch die Fälle haben Fehler.** Drei Erwartungen waren strenger als nötig:
„Folien" (das Modell schrieb „Folie"), „Abstimmung Schnittstelle" (das Modell
schrieb „Schnittstellen-Abstimmung") und der Termin mit irreführendem
„Review", bei dem nur „nicht Sprint Review" zu prüfen war. Der Mitschnitt der
Modellantwort zeigt in Sekunden, ob das Modell oder der Fall danebenliegt.

**Ein Ausfall ist kein Ergebnis.** Hängt oder stürzt der Modellserver ab,
scheitern alle folgenden Läufe sofort. Das Programm meldet solche Messungen
jetzt als unvollständig und hält sie nicht fest; ein Lauf, der länger als zwei
Minuten hängt, zählt als gescheitert (Termin-Agent: fünf).

**Der Code rechnet, das Modell gibt Worte wieder.** Das Muster hat sich zweimal
bestätigt: beim Datum („bis Dienstag") und bei Zeitspannen („in 1 Tag 1 Std").
Beide Male rechnete das Modell falsch, und beide Male half es, ihm das Rechnen
abzunehmen statt es besser zu erklären.

**Prüfer fangen nicht alles.** Die erfundene Behauptung zur Freigabe stand nur
im Mitschnitt; kein Prüfer hat sie bemängelt. Beim ersten Messlauf eines
Agenten lohnt es sich, die Antworten zu lesen und nicht nur die Quote.

**Kein Unterschied durch die Ausgabeart.** Ob das Modell ein Ausgabe-Werkzeug
aufruft (`tool`) oder Ollama das Schema erzwingt (`native`), änderte mit
`gpt-oss:20b` nichts, was über die Schwankung hinausgeht.

## Modelle auf einem Rechner mit 24 GB RAM

| | `gpt-oss:20b` | `qwen3.8:27b-mlx` |
|---|---|---|
| Speicher geladen | 12 GB | 21 bis 23 GB |
| Dauer je Modellaufruf | 1 bis 3 s, Termin-Agent bis 18 s | 5 bis 20 s, Termin-Agent rund 50 s |
| Status- und Termin-Agent zusammen, ein Lauf | nicht gemessen | 98 s |

Zwei Speichervorfälle mit Qwen:

- Erster Messversuch mit weiteren offenen Programmen: 55 Modellaufrufe in 45
  Minuten, Pausen von 10 und 16 Minuten, Auslagerungsspeicher voll. Abgebrochen.
- Messung der Terminart: Nach zehn Läufen stürzte der Modellserver mit
  „Insufficient Memory" ab. Der Termin-Agent denkt mit mittlerem Aufwand nach
  und braucht am meisten Speicher.

Zwei große Modelle passen nicht gleichzeitig in den Speicher. Bekommen Agenten,
die nacheinander laufen, verschiedene Modelle, lädt Ollama bei jedem Wechsel neu.

Offen ist das Kontextfenster: Ollama lud die Modelle während der Messungen mit
4096 bzw. rund 8000 Tokens, README und Code gehen von 16K aus. Für die kurzen
Eingaben der Messung reicht das; bei langen Story-Texten oder dem Termin-Agenten
kann Eingabe unbemerkt abgeschnitten werden.

## Entscheidungen

- **Modell je Agent:** Qwen für Scrum Master und Story-Erkennung,
  `gpt-oss:20b` für alles andere. Eingestellt im Profil (`[models]`), nicht im
  Repo; der Code-Standard bleibt `gpt-oss:20b`.
- **Rückmeldung auf den Plan bleibt bei `gpt-oss:20b`,** obwohl Qwen dort etwas
  besser ist (drei Läufe von 60): Sie läuft im gesprochenen Dialog, wo 10 bis 17
  Sekunden Wartezeit je Antwort auffallen.
- **Ausgabeart bleibt `tool`.**
- **Fünf Läufe je Fall** sind der Standard der Messung.
- **Jeder neue Agent bekommt seine Fälle gleich mit.**

## Offen

- Echte Fälle aus dem Alltag sammeln (`evals merke`). Alle bisherigen Zahlen
  beruhen auf erfundenen Fällen und sagen wenig darüber, wie oft die Agenten im
  Alltag danebenliegen.
- Der gelockerte Termin-Fall („Review im Titel") ist nicht nachgemessen.
- Die drei weiter bestehenden Schwächen mit `gpt-oss:20b` (oben).
- Nicht gemessen werden die Ergebnisliste des Termin-Agenten (nur ihre Länge),
  die freien Texte von Kalender- und Asana-Agent, der Planungs-Agent und die
  Spracheingabe.
- Kontextfenster: Der Befehl `context_window` zeigt den Stand und legt eine
  Variante mit 16K an (README, Abschnitt „Kontextfenster"). Die Messungen oben
  liefen noch mit dem kleineren Fenster.

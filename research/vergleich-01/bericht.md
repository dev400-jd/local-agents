# Modellvergleich

Referenz **gpt-oss:20b**, Kandidat **deepseek-r1:14b**. 120/120 Versuche erfasst. Beginn 2026-11-28T15:10:31.813230+00:00.

Quellstand `35e2051+`, Ollama 0.40.15, Ausgabeart `tool`, Stichproben-Seed 20261128.

- gpt-oss:20b · 20.9B · MXFP4 · Digest `f38aa0c53da5f8c49d08c43a99df24ff53167fe68e24664a7777288e7656fdfe`
- deepseek-r1:14b · 14.8B · Q4_K_M · Digest `c333b7232bdb521236694ffbb5f5a6b11cc45d98e9142c73123b670fca400b09`

| Disziplin | Fälle | gpt-oss:20b | deepseek-r1:14b | Δ Prozentpunkte | Median Sekunden¹ (Ref. / Kand.) |
|---|---:|---:|---:|---:|---:|
| asana_aktion | 3 | 6/6 | 3/6 | -50.0 | 2.4 / 44.6 |
| asana_aufgaben | 3 | 6/6 | 2/6 | -66.7 | 3.3 / 37.8 |
| asana_beschreibung | 3 | 6/6 | 4/6 | -33.3 | 2.2 / 38.7 |
| fragen | 3 | 6/6 | 6/6 | +0.0 | 2.8 / 17.6 |
| nachricht | 3 | 6/6 | 6/6 | +0.0 | 2.5 / 18.1 |
| rueckmeldung | 3 | 6/6 | 6/6 | +0.0 | 2.1 / 51.9 |
| scrum_master | 3 | 5/6 | 2/6 | -50.0 | 2.4 / 51.2 |
| status | 3 | 6/6 | 5/6 | -16.7 | 1.8 / 20.2 |
| stories | 3 | 6/6 | 1/6 | -83.3 | 1.5 / 65.7 |
| termin | 3 | 5/6 | 0/6 | -83.3 | 12.4 / 116.7 |

**Primärmaß** · Differenz der Erfolgsquoten bei gleichem Gewicht je Disziplin und je Fall innerhalb der Disziplin (Kandidat minus Referenz) **-38.3 Prozentpunkte**.
Disziplinmittel gpt-oss:20b 96.7 %, deepseek-r1:14b 58.3 %.
Gepaarte Versuche: beide erfolgreich 35, nur Referenz 23, nur Kandidat 0, beide gescheitert 2.
Exploratives 95-%-Perzentilintervall [-46.7, -30.0] Prozentpunkte. 10.000 gepaarte Bootstrap-Stichproben ziehen ganze Fälle innerhalb jeder Disziplin. Ähnliche Fallvarianten und kleine Stichproben begrenzen die Aussagekraft.

### Wiederholbarkeit und Ausfälle

- gpt-oss:20b bestand 28/30 Fälle in allen Wiederholungen. fail 2, pass 58.
- deepseek-r1:14b bestand 14/30 Fälle in allen Wiederholungen. fail 23, pass 35, timeout 2.

¹ Gemessen wird die Zeit für Aufgabe und Bewertung einschließlich Modellladezeit, sofern diese anfällt. Ein Zeitlimit geht mit seiner vollen Dauer ein. Sekundenwerte enthalten bei regulärem Abschluss keinen Prozessstart.

Alle vorgesehenen Versuche zählen im Nenner. Nur bestandene Fälle zählen im Zähler. Ausführungs- und Prüferfehler belasten damit die gemessene Systemleistung. Die Fallprüfer definieren Erfolg am beobachteten Ergebnis. Alle Aussagen gelten für diese Modellartefakte, Agenten, Budgets und synthetischen Fälle.

### Fälle mit unterschiedlicher Erfolgsquote

| Disziplin | Fall | gpt-oss:20b | deepseek-r1:14b |
|---|---|---:|---:|
| asana_aktion | Anderen zuweisen gibt es nicht | 2/2 | 1/2 |
| asana_aktion | In einen Abschnitt verschieben | 2/2 | 1/2 |
| asana_aktion | Verweis auf die eben angelegte Aufgabe | 2/2 | 1/2 |
| asana_aufgaben | Alles Offene ohne weitere Angabe | 2/2 | 0/2 |
| asana_aufgaben | Anzahl in einem Projekt | 2/2 | 1/2 |
| asana_aufgaben | Änderungen seit Montag, auch erledigte | 2/2 | 1/2 |
| asana_beschreibung | Ohne Hinweis: Dokument über den Titel der Aufgabe | 2/2 | 0/2 |
| scrum_master | Story über ein Stichwort aus dem Namen | 1/2 | 0/2 |
| scrum_master | Zwei Stories in einem Satz | 2/2 | 0/2 |
| status | Andere Frage, gleiche Aufgabe | 2/2 | 1/2 |
| stories | Beispieldatei mit Kürzeln, dem Modell ohne Kürzel gezeigt | 2/2 | 0/2 |
| stories | Stories auf einer Ebene, ohne Kürzel | 2/2 | 1/2 |
| stories | Stories unter Gruppenüberschriften | 2/2 | 0/2 |
| termin | Daily | 2/2 | 0/2 |
| termin | Infoveranstaltung ohne Stichwort | 2/2 | 0/2 |
| termin | Review im Titel, aber kein Sprint Review | 1/2 | 0/2 |

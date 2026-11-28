# Modellvergleich

Referenz **gpt-oss:20b**, Kandidat **qwen3.8:27b-mlx**. 116/116 Versuche erfasst. Beginn 2026-11-28T16:34:46.559356+00:00.

Quellstand `5009a57+`, Ollama 0.40.15, Ausgabeart `tool`, Stichproben-Seed 20261128.

- gpt-oss:20b · 20.9B · MXFP4 · Digest `f38aa0c53da5f8c49d08c43a99df24ff53167fe68e24664a7777288e7656fdfe`
- qwen3.8:27b-mlx ·  · nvfp4 · Digest `5642e97495e1a088883805981563dcdc4a040c2f53388b7a41d1f24d3622cf7e`

| Disziplin | Fälle | gpt-oss:20b | qwen3.8:27b-mlx | Δ Prozentpunkte | Median Sekunden¹ (Ref. / Kand.) |
|---|---:|---:|---:|---:|---:|
| asana_aktion | 3 | 6/6 | 6/6 | +0.0 | 2.1 / 19.3 |
| asana_aufgaben | 3 | 6/6 | 6/6 | +0.0 | 3.2 / 28.6 |
| asana_beschreibung | 3 | 6/6 | 4/6 | -33.3 | 6.2 / 45.9 |
| fragen | 3 | 6/6 | 6/6 | +0.0 | 1.8 / 17.6 |
| nachricht | 3 | 6/6 | 6/6 | +0.0 | 2.6 / 49.9 |
| rueckmeldung | 3 | 4/6 | 6/6 | +33.3 | 2.3 / 33.8 |
| scrum_master | 3 | 6/6 | 6/6 | +0.0 | 2.4 / 20.8 |
| status | 3 | 6/6 | 6/6 | +0.0 | 2.2 / 21.0 |
| stories | 2 | 1/4 | 4/4 | +75.0 | 1.6 / 33.1 |
| termin | 3 | 6/6 | 4/6 | -33.3 | 13.9 / 104.5 |

**Primärmaß** · Differenz der Erfolgsquoten bei gleichem Gewicht je Disziplin und je Fall innerhalb der Disziplin (Kandidat minus Referenz) **+4.2 Prozentpunkte**.
Disziplinmittel gpt-oss:20b 89.2 %, qwen3.8:27b-mlx 93.3 %.
Gepaarte Versuche: beide erfolgreich 49, nur Referenz 4, nur Kandidat 5, beide gescheitert 0.
Exploratives 95-%-Perzentilintervall [-3.3, +11.7] Prozentpunkte. 10.000 gepaarte Bootstrap-Stichproben ziehen ganze Fälle innerhalb jeder Disziplin. Ähnliche Fallvarianten und kleine Stichproben begrenzen die Aussagekraft.

### Wiederholbarkeit und Ausfälle

- gpt-oss:20b bestand 25/29 Fälle in allen Wiederholungen. fail 5, pass 53.
- qwen3.8:27b-mlx bestand 26/29 Fälle in allen Wiederholungen. execution_error 2, pass 54, timeout 2.

¹ Gemessen wird die Zeit für Aufgabe und Bewertung einschließlich Modellladezeit, sofern diese anfällt. Ein Zeitlimit geht mit seiner vollen Dauer ein. Sekundenwerte enthalten bei regulärem Abschluss keinen Prozessstart.

Alle vorgesehenen Versuche zählen im Nenner. Nur bestandene Fälle zählen im Zähler. Ausführungs- und Prüferfehler belasten damit die gemessene Systemleistung. Die Fallprüfer definieren Erfolg am beobachteten Ergebnis. Alle Aussagen gelten für diese Modellartefakte, Agenten, Budgets und synthetischen Fälle.

### Fälle mit unterschiedlicher Erfolgsquote

| Disziplin | Fall | gpt-oss:20b | qwen3.8:27b-mlx |
|---|---|---:|---:|
| asana_beschreibung | Zahlen wörtlich | 2/2 | 0/2 |
| rueckmeldung | Beide Aufgaben zu einem Termin | 1/2 | 2/2 |
| rueckmeldung | Herausnehmen und ergänzen in einer Antwort | 1/2 | 2/2 |
| stories | Beispieldatei mit Gruppen, dem Modell ohne Kürzel gezeigt | 0/2 | 2/2 |
| stories | Dokument ohne Stories | 1/2 | 2/2 |
| termin | Retrospektive | 2/2 | 1/2 |
| termin | Wöchentlicher Termin ohne Stichwort | 2/2 | 1/2 |

# Wie der Modellvergleich zu lesen ist

Die praktische Frage lautet, wie oft ein Agent mit einem bestimmten Ollama-Modell eine Aufgabe im Repository korrekt erledigt und wie lange ein Versuch dauert. Die vorhandenen ausführbaren Prüfer lesen beispielsweise gespeicherte Story-Bemerkungen, den geplanten Asana-Aufruf oder Belege in einer Antwort. Ein erfolgreicher Versuch erfordert, dass alle Assertions des Falls bestehen. Auch deterministische Vorverarbeitung, Wiederholungslogik und Rückfallpfade der Agenten beeinflussen das Ergebnis.

## Versuchsdesign

- **Fallpopulation.** Der erste Korpus enthält 127 versionierte synthetische Fälle in zehn Disziplinen. Er deckt Status, Terminart, Story-Erkennung, Scrum-Bemerkungen, Planänderungen, Nachrichtenentwürfe, belegte Antworten und drei Asana-Aufgaben ab. Die eigenständige Suchsuite misst Wort- bzw. Embedding-Suche. Sie ist vom Sprachmodellvergleich ausgenommen. Private Falldateien werden beim Laden übergangen.
- **Paarung.** Der Runner zieht je Disziplin ohne Zurücklegen aus den nach Namen sortierten Fällen. Beide Modelle erhalten dieselben Fälle und innerhalb einer Wiederholung die gleiche Fallreihenfolge. Pro Wiederholung wechseln die Modellblöcke ihre Reihenfolge. Das reduziert häufiges Umladen und balanciert bei einer geraden Wiederholungszahl die Position. Der Seed steuert den Versuchsplan. Ollamas Generierung verwendet die jeweiligen Agenteneinstellungen.
- **Vorab festgelegte Hauptkennzahl.** Jeder Versuch liefert 1 für Erfolg oder 0. Zuerst mitteln wir Wiederholungen je Modell und Fall, anschließend gepaarte Differenzen innerhalb jeder Disziplin und schließlich die zehn Disziplinmittel mit gleichem Gewicht. Ein Pluszeichen spricht für das zweite Modell. Diese Gewichtung gibt beispielsweise den fünf Story-Fällen dieselbe Bedeutung wie den 28 Asana-Aktionsfällen. Für einen konkreten Arbeitsalltag können andere Gewichte passen.
- **Unsicherheit.** Das Intervall resampelt ganze Fallpaare innerhalb der Disziplin. Wiederholungen desselben Falls bleiben zusammen. Das Intervall beschreibt die Empfindlichkeit gegenüber der Fallzusammensetzung unter der Annahme austauschbarer Fälle je Disziplin. Die erste Stichprobe mit drei Fällen je Disziplin erlaubt nur eine explorative Einordnung. Verwandte Varianten und Schwankungen über Rechnerstarts oder Tage können die Unsicherheit vergrößern. Die Disziplintabelle ist deskriptiv. Ein Vergleich vieler Tabellenzeilen ist kein Signifikanztest.
- **Zeit und Zuverlässigkeit.** Der Median enthält auch gescheiterte Versuche. Bei Überschreiten des Prozesszeitlimits wird der Versuch mit der Frist erfasst. Das Terminbudget beträgt 300 Sekunden, die übrigen Budgets je 120 Sekunden. Reguläre Laufzeiten umfassen die gesamte Evaluation einschließlich Agenten-Retries. Gemischte Warm- und Kaltstarts sowie Hintergrundlast beeinflussen die Zeitwerte. „Alle Wiederholungen bestanden“ misst beobachtete Beständigkeit bei der angegebenen Wiederholungszahl.
- **Kontrollen.** Vor dem ersten Versuch speichert der Runner den vollständigen Ablauf, Modell-Digests, Varianten, Softwareversionen, Ausgabeart, Zeitbudgets und einen Hash von Agentencode, Falldateien und Testdokumenten. Die produktiven Agenteneinstellungen gelten für beide Modelle, einschließlich der abweichenden Einstellungen des Termin-Agenten. Bei einem Neustart prüft der Runner das Protokoll. Jeder abgeschlossene Versuch wird einzeln in SQLite festgeschrieben. Ein geänderter Software- oder Datenstand erfordert ein neues Verzeichnis.

## Vom Bericht zum Einzelfall

Die folgenden Befehle laufen im Repository mit der installierten Eval-Umgebung. Der erste schreibt einen Markdown-Bericht aus dem gespeicherten Lauf. Der zweite zeigt für Fehler den Modelltag, die Disziplin, den Fallnamen sowie den tatsächlichen Endzustand mit Prüferbegründung oder Fehlerstatus.

```sh
.venv/bin/python -m local_agents.research_report research/vergleich-01 > research/vergleich-01/bericht.md
sqlite3 -readonly research/vergleich-01/run.sqlite <<'SQL'
  SELECT json_extract(s.value, '$[1]'), json_extract(s.value, '$[2]'),
         json_extract(s.value, '$[3]'), t.result
  FROM protocol AS p, json_each(p.config, '$.schedule') AS s
  JOIN trials AS t ON t.id = s.key
  WHERE json_extract(t.result, '$.status') <> 'pass'
  ORDER BY t.id;
SQL
```

Bei einem großen Abstand lohnt sich die Prüfung konkreter Fälle. Ein Prüfer kann eine sachlich korrekte Paraphrase verwerfen oder eine formal passende, inhaltlich schwache Antwort akzeptieren. Versuche mit `execution_error` oder `grader_error` erfordern vor einer Modellauswahl Ursachenprüfung. Nach Anpassung von Prompts oder Prüfern einen neuen Vergleich anlegen. Für eine Entscheidung über den produktiven Einsatz vorab eine praktisch relevante Mindestdifferenz festlegen und auf zusätzlichen, bis dahin ungenutzten Aufgaben prüfen.

Die Orientierung an ausführbaren Endzustandsprüfern, wiederholten Versuchen und der Überprüfung der Prüfer folgt den öffentlich beschriebenen Verfahren von [Anthropic](https://www.anthropic.com/research/demystifying-evals-for-ai-agents) und [OpenAI](https://openai.com/index/introducing-swe-bench-verified/).

Für die Arbeit in einem Notebook exportiert der Bericht die Versuchsdaten als JSON Lines. Jede Zeile enthält die Fallkennung (Disziplin und Fallname), Wiederholung, Modell-Digest, Status und bei abgeschlossenen Versuchen die verfügbaren Prüfergebnisse und die Laufzeit. Eine ausstehende Zeile trägt den Status `pending`.

```sh
.venv/bin/python -m local_agents.research_report research/vergleich-01 --jsonl > /tmp/vergleich-01.jsonl
```

## Einen Stand reproduzieren

Jeder neue Vergleich legt den gemessenen Python-Code, die Prüfer und die Fall-Fixtures gemeinsam in `sources.zip` ab. Die Worker verwenden eine temporäre Arbeitskopie dieser Quellen. Das schützt längere Läufe vor parallelen Änderungen am Checkout. Der gespeicherte SHA-256-Wert identifiziert den Inhalt. Zum Nachstellen einer historischen Messung das Quellenarchiv in ein neues leeres Verzeichnis entpacken und dort den Messbefehl mit einem **neuen** Ausgabepfad ausführen. Dazu dieselbe virtuelle Umgebung oder die im Protokoll dokumentierten Paketversionen und dieselben Ollama-Digests verwenden. Der erste archivierte Messlauf verwendete die Repo-Dateien direkt. Sein archivierter Quellinhalt wurde gegen den zu Beginn gespeicherten Hash geprüft.

Neue Läufe speichern im JSONL-Export außerdem `wall_seconds` einschließlich Prozessstart sowie `calls` für regulär abgeschlossene Aufgaben. Jeder Eintrag in `calls` steht für einen Agentenaufruf und enthält Antwortteiltypen, die Dauer und bei einer Antwort die von Ollama übermittelten Eingabe- und Ausgabetokens. Eine leere Liste kennzeichnet einen rein lokalen Codepfad. Abgebrochene Worker können ihre Aufrufdaten verlieren. Der erste archivierte Lauf enthält die Endzustände, Prüfergebnisse und Latenzen. Die Aufruftelemetrie kam danach hinzu.

Für einen Vergleich nach Prompt-Tuning kann `--exclude-run research/vergleich-01` die bereits verwendeten Fälle ausschließen. Die Auswahl berücksichtigt alle im älteren Lauf geplanten Fallnamen je Disziplin, auch bei einem abgebrochenen Lauf. Bei diesem Korpus verbleiben 97 der 127 Fälle. Inhaltlich ähnliche Aufgaben mit anderen Namen können weiterhin vorkommen. Für eine langfristige Zeitreihe lässt man den Ausschluss weg und verwendet einen festen Korpus und Seed.

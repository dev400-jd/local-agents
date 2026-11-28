# Versuchsbedingungen im November 2026

Die Messung läuft auf einem Apple M4 Pro mit 24 GiB Unified Memory. Ollama 0.40.15 führt beide Modelle nacheinander auf demselben Rechner aus. Verglichen werden die installierten Quantisierungen und die produktiven Agenten-Prompts im Modus `tool`. Der reguläre Ausgaberahmen umfasst 2.048 Tokens bei Reasoning-Einstellung `low`, beim Termin-Agenten 4.096 Tokens bei `medium`. Wie die Modellvorlage eine solche Einstellung umsetzt, ist modellspezifisch. Für beide Modelle meldete Ollama während ihres jeweiligen Messblocks den Runner `llamacpp` mit 8.167 Kontext-Tokens.

Die vor dem ersten Versuch gespeicherte Reihenfolge umfasst 30 verschiedene Aufgaben, drei aus jeder der zehn Disziplinen. Pro Modell und Fall gibt es zwei Versuche. Der Aufwand des einzelnen Versuchs ist auf 120 Sekunden begrenzt, bei der Terminart auf 300 Sekunden. Ein separater DeepSeek-Probeaufruf vor Studienbeginn gehört nicht zur Auswertung. Die erste Modellreihenfolge war GPT-OSS, DeepSeek. Im zweiten Durchgang ist sie umgekehrt.

[Versuchsplan und Einzelversuche in SQLite](run.sqlite) · [Messverfahren](../../docs/modellvergleich.md)

## Einordnung der Ergebnisse

GPT-OSS bestand 58 von 60 Versuchen und benötigte im Median 2,5 Sekunden. DeepSeek bestand 35 von 60 bei 36,7 Sekunden. 28 der 30 Aufgaben gelangen GPT-OSS beide Male. Bei DeepSeek waren es 14. Den größten Abstand gab es bei Story-Erkennung und Terminart. Bei belegten Antworten, Nachrichtenentwürfen und Planrückmeldungen bestanden beide Modelle sämtliche sechs Versuche der jeweiligen Disziplin.

Eine explorative Aufteilung nach der implementierten Ausgabeschnittstelle hilft bei der Fehleranalyse. In den sechs Agenten mit strukturiertem Rückgabetyp bestand GPT-OSS 34/36 Versuche, DeepSeek 14/36. In den vier anderen Agenten waren es 24/24 und 21/24. Die Gruppen bearbeiten unterschiedliche Aufgaben. Ob das Ausgabeformat den Abstand verursacht, lässt sich erst durch einen Vergleich mehrerer Ausgabearten am selben Fall prüfen.

Zwei Beispiele sind in der Datenbank anhand der nullbasierten Versuchs-ID nachvollziehbar. In Versuch 36 erkannte DeepSeek das Daily im Freitext. Der Termin-Agent griff auf den Katalog zurück, weil die erwartete strukturierte Antwort ausblieb. In Versuch 5 lieferte GPT-OSS eine verarbeitbare Antwort, ordnete eine Reisekostenprüfung jedoch als Sprint Review ein. Ein Formatfehler und eine fachliche Fehlentscheidung verlangen unterschiedliche Änderungen am Agenten.

[Ergebnisse je Disziplin und Fall](bericht.md) · [Eingefrorene Quellen und Fixtures](sources.zip)

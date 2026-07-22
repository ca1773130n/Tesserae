# Automatische Konsolidierung — der Schlafzyklus der Engine

<!-- translations:start -->
<p align="center"><a href="../engine-consolidation.md">English</a> · <a href="engine-consolidation.ko.md">한국어</a> · <a href="engine-consolidation.zh.md">中文</a> · <a href="engine-consolidation.ja.md">日本語</a> · <a href="engine-consolidation.ru.md">Русский</a> · <a href="engine-consolidation.es.md">Español</a> · <a href="engine-consolidation.fr.md">Français</a></p>
<!-- translations:end -->

Das Gehirn konsolidiert Erinnerungen während der Ruhe. Während du schläfst, wird die rohe Erfahrung des Tages reorganisiert, verdichtet und integriert — das Jüngste, Lauteste wird in stabile Struktur gefaltet. Tesseraes Motor macht dasselbe. Wenn ein Projekt **untätig** wird, hört der ständig laufende Daemon auf, auf die nächste Bearbeitung zu warten, und verbringt die ruhige Zeit damit, das Bekannte zu reorganisieren: Es führt einen Destillationspass durch, der jede Agent-Speicher reorganisiert, verdichtet und sicher vergisst.

Bis jetzt lief dieser Pass nur auf Anfrage — `tesserae refresh` unter Speicherdruck oder ein explizites `tesserae distill`. Die Engine kompilierte bei jeder Datei und jedem Sitzungsereignis neu, konsolidierte sich aber nie automatisch. Der **Schlafzyklus** schließt diese Lücke: Lassen Sie `tesserae engine` laufen, und die Konsolidierung erfolgt während der Ruhe, ohne dass Sie einen Befehl speichern müssen.

Wie alles im System der [geschichteten Speicher](agent-memory.md), ist dies ein **no-op, wenn du nicht einwilligst** — der Daemon konsolidiert bei Untätigkeit, aber die Destillation darunter funktioniert nur, wenn `TESSERAE_AGENT_DISTILL` gesetzt ist.

## Wann es auslöst

Ein dedizierter Konsolidierungs-Thread wacht bei einem festen **Überprüfungsintervall** (Standard 30 Sekunden) auf und bewertet zwei unabhängige Auslöser gegen eine monotone Aktivitätsuhr:

- **Untätigkeitsauslöser.** Das Projekt hat mindestens `--consolidate-idle` Sekunden lang kein Auslöseereignis oder keine Pipeline-Ausführung gesehen (Standard **300s = 5 min**). Dies ist der Fall "Konsolidierung während der Ruhe" — die Engine bemerkte, dass du aufgehört hast zu arbeiten, und nutzte die Pause. Ein **Boden** seit der letzten Konsolidierung verhindert Zittern, daher wird ein geschäftiges Projekt, das gerade ruhig geworden ist, nicht bei empfindlichem Auslöser konsolidiert. - **Deckenauslöser.** Mindestens `--consolidate-every` Sekunden sind seit der letzten Konsolidierung vergangen, **unabhängig von Aktivität** (Standard **21600s = 6h**). Dies stellt sicher, dass sich ein durchgehend beschäftigtes Projekt trotzdem regelmäßig konsolidiert, anstatt nie einen ruhigen Moment zu bekommen. Wenn du es auf `0` setzt, wird die Decke deaktiviert — dann ist Untätigkeit der einzige Auslöser.

Jede Bearbeitung, jeder Sitzungszug oder jede Neukompilierung erhöht die Aktivitätsuhr, daher verstreicht das Untätigkeitsfenster nur während echter Ruhe. Beide Uhren sind **monoton**, niemals Wanduhr, und werden nie in Artefakten beibehalten — Konsolidierungszeitpunkt kann nie das byte-deterministische Diagramm stören.

## Was ausgeführt wird

Jeder Auslöser lädt das kompilierte Diagramm aus `.tesserae/graph.json` (wenn die Datei fehlt, wird der Pass übersprungen) und ruft denselben `maybe_distill_on_refresh`-Einstiegspunkt auf, den `tesserae refresh` verwendet. Diese Funktion ist **dreifach überprüft** intern und wirft niemals einen Fehler pro Agent auf:

1. **Zustimmungtor** — `TESSERAE_AGENT_DISTILL=1` (oder `{"agent_distill": {"enabled": true}}` in `config.json`). Standardmäßig deaktiviert; der gesamte Zyklus ist ein sicheres no-op, bis du ihn setzt.
2. **Pro-Agent-Wasserzeichen** — ein Agent, dessen Erkenntnisse sich seit seiner letzten Destillation nicht geändert haben, wird übersprungen.
3. **Pro-Agent-Speicherdruck** — nur Agenten, deren undestillierten Erkenntnisse nicht mehr in die halbe Kontextlesart passen, werden konsolidiert (MemGPT-Stil-Auslöser).

Auch wenn die Konsolidierung auf einem Zeitplan *auslöst*, *funktioniert* sie nur für Agenten, die zugestimmt haben und tatsächlich genug neuen Speicher angesammelt haben, um dies zu rechtfertigen. Siehe [Geschichteter Agent-Speicher](agent-memory.md) für das, was Destillation produziert.

## Sicherheit und Determinismus

- **Läuft unter dem Kompiliertor.** Konsolidierung erwirbt denselben Lock wie Neukompilierung, wird daher mit Kompilierungen serialisiert und **überlappt sich nie mit einer**. Ein ausstehender Kompilierung wartet auf eine laufende Konsolidierung und umgekehrt — das Diagramm wird nie beim Schreiben gelesen.
- **Löst niemals in der Daemon-Schleife aus.** Der gesamte Pass ist umhüllt; Fehler werden protokolliert und der Thread wiederholt die Schleife. Eine fehlgeschlagene Konsolidierung beendet die Engine nie.
- **No-op, wenn das Tor aus ist.** Mit `TESSERAE_AGENT_DISTILL` nicht gesetzt, lädt der Pass nichts Teures und gibt sofort zurück, daher kostet das Laufen des Schlafzyklus im Wesentlichen nichts.
- **Deterministische Artefakte, unverändert.** Destillierte Artefakte bleiben angesichts ihrer Eingaben deterministisch; der Schlafzyklus ändert nur *wann* Destillation läuft, nie *was* sie produziert. Untätigkeitszeitpunkt lässt sich niemals in `graph.json` oder eine destillierte Ebene ein.
- **Sauberer Shutdown.** Der Konsolidierungsthread beobachtet das Stop-Event des Daemons und beendet sich ordnungsgemäß bei `Ctrl-C` / Shutdown. Es ist nur eine Langzeitmodus-Funktion: `tesserae engine ... --once` startet es niemals.

## CLI-Flags

| Flag | Standard | Wirkung |
|---|---|---|
| `--consolidate` / `--no-consolidate` | on | Aktivieren oder deaktivieren Sie den Schlafzyklus vollständig. Standardmäßig aktiviert (no-op, wenn das Destillationstor nicht gesetzt ist). |
| `--consolidate-idle SECONDS` | `300` | Ruhebereich: Konsolidieren Sie nach dieser vielen Sekunden Inaktivität. |
| `--consolidate-every SECONDS` | `21600` | Decke: Konsolidieren Sie mindestens so häufig unabhängig von Aktivität. `0` deaktiviert die Decke. |
| `--consolidate-check SECONDS` | `30` | Wie oft der Konsolidierungsthread aufwacht, um die Auslöser neu zu bewerten. |

## Flottenverhalten(`--all`)

`tesserae engine --all` hält jedes registrierte Projekt in einem einzigen Prozess frisch. Jede Projekteinheit erhält ihren eigenen Konsolidierungsthread mit denselben Reglern, und alle Einheiten teilen sich ein flottenweit Kompiliertor — daher wird eine Konsolidierung in einem Projekt gegen Kompilierungen in der gesamten Flotte serialisiert und überlappt sich nie.

## Durchgearbeitetes Beispiel

Schalten Sie Destillation ein und führen Sie die Engine mit einem schnelleren Schlafzyklus für eine Demo aus — konsolidieren Sie nach 60 Sekunden Untätigkeit und mindestens alle 30 Minuten unabhängig:

```bash
export TESSERAE_AGENT_DISTILL=1
tesserae engine \
  --consolidate-idle 60 \
  --consolidate-every 1800 \
  --consolidate-check 15
```

Arbeite wie gewohnt in deinem Editor und mit Agenten; die Engine beobachtet, entprallt und kompiliert jede Änderung neu. Stoppe eine Minute und der Untätigkeitsauslöser feuert: Der Konsolidierungsthread erwirbt das Kompiliertor und destilliert jeden Agent unter Speicherdruck — reorganisiert, verdichtet, vergisst sicher — und schläft dann wieder. Arbeite über die halbe Stunde hinaus ohne jemals zu pausieren, und die Decke wird auch auslösen, daher konsolidiert sich ein rücksichtslose Projekt trotzdem.

Um die Engine am Laufen zu halten, aber Konsolidierung für manuelle `tesserae distill`-Läufe zu hinterlassen, übergeben Sie `--no-consolidate`. Um es bei Untätigkeit auszuführen, aber nie auf einem festen Zeitplan, übergeben Sie `--consolidate-every 0`.

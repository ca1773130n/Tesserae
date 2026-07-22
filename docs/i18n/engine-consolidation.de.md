# Automatische Konsolidierung — der Schlafzyklus der Engine

<!-- translations:start -->
<p align="center"><a href="../engine-consolidation.md">English</a> · <a href="engine-consolidation.ko.md">한국어</a> · <a href="engine-consolidation.zh.md">中文</a> · <a href="engine-consolidation.ja.md">日本語</a> · <a href="engine-consolidation.ru.md">Русский</a> · <a href="engine-consolidation.es.md">Español</a> · <a href="engine-consolidation.fr.md">Français</a> · <a href="engine-consolidation.de.md">Deutsch</a></p>
<!-- translations:end -->

Das Gehirn konsolidiert Erinnerungen während des Schlafs. Während Sie schlafen, wird die rohe Erfahrung des Tages umorganisiert, komprimiert und integriert — die jüngsten, lauten Dinge werden in eine dauerhafte Struktur gefaltet. Die Tesserae-Engine macht das Gleiche. Wenn ein Projekt **inaktiv** wird, stoppt der immer aktive Daemon, auf die nächste Bearbeitung zu warten, und verbringt die ruhige Zeit damit, das zu reorganisieren, was er bereits weiß: er **komprimiert und vergisst** laute aktuelle Erinnerungen, lässt Wissen, das niemand abgerufen hat, **durch Nichtverwendung verblassen** und **entdeckt neue Verbindungen** zwischen dem, was überlebt.

Bislang wurde dieser Durchgang nur ausgeführt, wenn Sie ihn anforderten — `tesserae refresh` unter Speicherdruck oder ein explizites `tesserae distill`. Die Engine recompilierte bei jeder Datei und jedem Sessionsereignis, konsolidierte sich aber nie automatisch. Der **Schlafzyklus** schließt diese Lücke: Lassen Sie `tesserae engine` laufen und die Konsolidierung findet während der Ruhe statt, ohne dass Sie sich einen Befehl merken müssen.

Wie alles im System der [geschichteten Erinnerungen](agent-memory.md) ist dies **ein Noop, wenn Sie nicht teilnehmen** — der Daemon konsolidiert im Leerlauf, aber die Destillation darunter funktioniert nur, wenn `TESSERAE_AGENT_DISTILL` eingestellt ist.

## Wann es auslöst

Ein dedizierter Konsolidierungs-Thread wacht in einem festen **Prüfintervall** (Standard 30 Sekunden) auf und bewertet zwei unabhängige Auslöser gegen eine monotone Aktivitätsuhr:

- **Leerlauf-Auslöser.** Das Projekt hat mindestens `--consolidate-idle` Sekunden lang kein Triggerereignis und keine Pipeline-Ausführung gesehen (Standard **300 Sekunden = 5 Minuten**). Dies ist der Fall der "Konsolidierung während der Ruhe" — die Engine bemerkte, dass Sie aufgehört haben zu arbeiten, und nutzte die Pause. Ein **Boden** seit der letzten Konsolidierung verhindert Flattern, daher konsolidiert sich ein geschäftiges Projekt, das gerade ruhig geworden ist, nicht beim empfindlichen Auslöser.
- **Obergrenze-Auslöser.** Mindestens `--consolidate-every` Sekunden sind seit der letzten Konsolidierung verstrichen, **unabhängig von der Aktivität** (Standard **21600 Sekunden = 6 Stunden**). Dies garantiert, dass ein ständig beschäftigtes Projekt sich immer noch regelmäßig konsolidiert, anstatt nie einen ruhigen Moment zu haben. Das Setzen auf `0` deaktiviert die Obergrenze — dann ist Leerlauf der einzige Auslöser.

Jede Bearbeitung, jeder Sessionsdurchgang oder jede Neukompilierung erhöht die Aktivitätsuhr, daher verstreicht das Leerlauf-Fenster nur während echter Ruhe. Beide Uhren sind **monoton**, nie Wanduhr, und werden niemals in einem Artefakt beibehalten — Konsolidierungszeitpunkt kann niemals das Byte-deterministische Diagramm stören.

## Was ausgeführt wird — drei Operationen

Jede Auslösung lädt das kompilierte Diagramm aus `.tesserae/graph.json` (wenn die Datei fehlt, wird der Durchgang übersprungen) und führt drei Konsolidierungsvorgänge in der richtigen Reihenfolge aus. Zusammen spiegeln sie das wider, was ein ruhender Gehirn macht: die letzten lauten Dinge komprimieren, das nie Besuchteste verblassen lassen und neue Assoziationen zwischen dem, was überlebt, verkabeln.

### 1. Komprimieren / vergessen — Destillation

Ruft denselben `maybe_distill_on_refresh`-Einstiegspunkt auf, den `tesserae refresh` verwendet, um die Erinnerung jedes Agenten zu reorganisieren, zu komprimieren und sicher zu vergessen. Diese Funktion ist intern **dreifach gesperrt** und wird nie für einen Fehler pro Agent angehoben:

1. **Opt-in-Tor** — `TESSERAE_AGENT_DISTILL=1` (oder `{"agent_distill": {"enabled": true}}` in `config.json`). Standardmäßig deaktiviert; der gesamte Zyklus ist ein sicherer Noop, bis Sie ihn einschalten.
2. **Pro-Agent-Wasserstand** — ein Agent, dessen Ergebnisse sich seit der letzten Destillation nicht geändert haben, wird übersprungen.
3. **Pro-Agent-Speicherdruck** — nur Agenten, deren undestillierte Ergebnisse nicht mehr in die Hälfte einer Kontextlesung passen, werden konsolidiert (MemGPT-Stil-Auslöser).

Also auch wenn die Konsolidierung nach einem Zeitplan **auslöst**, **funktioniert** sie nur für Agenten, die sich angemeldet haben und tatsächlich genug neue Erinnerungen angesammelt haben, um sie zu rechtfertigen. Siehe [Geschichtete Agent-Erinnerung](agent-memory.md) für das, was Destillation produziert.

### 2. Durch Nichtverwendung vergessen — LRU-Abbau beim Abrufen, nicht nur nach Alter

Der Abbau der Destillation wird nicht mehr nur durch das Erstellungsalter vorangetrieben. Jede Lesefläche zeichnet den Zugriff auf die Ergebnisse, die sie zurückgibt, auf — `last_accessed_at` und `access_count` — in einem **`node_memory`-Sidecar**, niemals in `graph.json`. Bevor der Destillationsdurchgang den Abbau berechnet, fusioniert er diesen Live-Zugriffszustand in seine Arbeitsansicht, sodass ein Ergebnis, das seit seiner Erstellung nicht abgerufen wurde, abbaut und zur Absorption oder Herabstufung in Frage kommt, während eines, das kürzlich gelesen wurde, unabhängig von seinem Alter frisch bleibt. Dies ist **Abruf-Aktualität**, die LRU-Intuition (Least Recently Used) auf Erinnerungen angewendet: Wissen, das Sie weiterhin abrufen, bleibt erhalten; Wissen, das niemand anfordert, verblasst zuerst. Ein leerer Sidecar reproduziert das alte Verhalten nur nach Alter exakt, daher ist es vollständig rückwärtskompatibel.

### 3. Zuordnen — neue Verbindungen entdecken

Der letzte Vorgang sucht nach *neuen* Beziehungen zwischen dem, was überlebt hat. Er bettet destillierte Notizen ein und verknüpft Paare, deren Bedeutungen nahe beieinander liegen — **Einbettungs-Gating**, daher wird er nur ausgeführt, wenn ein echtes Einbettungs-Backend konfiguriert ist (der Hash-Stub wird übersprungen, nie rauschhafte Links produzierend). Die Entdeckung läuft innerhalb des Projekts und **übergreifend** und die Verbindungen, die sie findet, werden als `shares_concept_with`-Kanten mit einem `federation_semantic`-Marker geprägt.

Entscheidend ist, dass diese erkannten Kanten in eine **Sidecar-Überlagerung** unter `.tesserae` geschrieben werden, *niemals* in `graph.json`. Die Überlagerung **sammelt sich über Zyklen an** — jeder Zuordnungsdurchgang dedupliciert und erweitert das, was frühere Durchgänge gefunden haben. Bei der Lesezeit (Abfrage, PPR-Expansion, Föderations-Ansichten) wird die Überlagerung **nur im Speicher** in das Diagramm zusammengeführt, genau wie die Pro-Agent-Ansichtsüberlagerung — daher wird das Byte-deterministische `graph.json` niemals berührt. Der gesamte Vorgang wird eingewickelt und wird nie in die Daemon-Schleife angehoben.

## Sicherheit und Determinismus

- **Läuft unter dem Kompilierungs-Tor.** Konsolidierung erwirbt das gleiche Sperren wie eine Neukompilierung, daher **serialisiert** sie sich mit Kompilierungen und **überlappt sich niemals**. Eine ausstehende Kompilierung wartet auf eine laufende Konsolidierung und umgekehrt — das Diagramm wird niemals während des Schreibens gelesen.
- **Wird niemals in die Daemon-Schleife angehoben.** Der gesamte Durchgang wird eingewickelt; jeder Fehler wird protokolliert und der Thread schleift weiter. Eine fehlgeschlagene Konsolidierung bringt die Engine nie zum Abstürz.
- **Noop, wenn das Tor aus ist.** Mit ungesetztem `TESSERAE_AGENT_DISTILL` lädt der Durchgang nichts Teures und kehrt sofort zurück, daher kostet die Beibehaltung des Schlafzyklus im Wesentlichen nichts.
- **Deterministische Artefakte, unverändert.** Destillierte Artefakte bleiben deterministische bei ihren Eingaben; der Schlafzyklus ändert nur *wann* Destillation läuft, niemals *was* sie produziert. Leerlauf-Zeit leckt niemals in `graph.json` oder irgendeine destillierte Schicht.
- **`graph.json` bleibt Byte-idempotent.** Keine neue Operation schreibt es. Der Zugriffszustand lebt im `node_memory`-Sidecar und erkannte Verbindungen in einer kumulativen Überlagerung — beide unter `.tesserae`, beide nur im Speicher bei Lesezeit zusammengeführt. Die maßgeblichen Diagramm-Bytes sind von Abrufverlauf oder erkannten Links unberührt.
- **Sauberes Herunterfahren.** Der Konsolidierungs-Thread beobachtet das Stopperereignis des Daemon und wird bei `Ctrl-C` / Herunterfahren rasch beendet. Es ist nur eine Funktion im Langzeitlaufmodus: `tesserae engine ... --once` startet ihn niemals.

## CLI-Flaggen

| Flagge | Standard | Effekt |
|---|---|---|
| `--consolidate` / `--no-consolidate` | on | Schlafzyklus vollständig aktivieren oder deaktivieren. Standardmäßig aktiviert (Noop, wenn Destillations-Tor nicht eingestellt). |
| `--consolidate-idle SECONDS` | `300` | Ruhefenster: Konsolidierung nach dieser Anzahl von Sekunden ohne Aktivität. |
| `--consolidate-every SECONDS` | `21600` | Obergrenze: Konsolidierung mindestens so häufig unabhängig von Aktivität. `0` deaktiviert die Obergrenze. |
| `--consolidate-check SECONDS` | `30` | Wie oft der Konsolidierungs-Thread aufwacht, um die Auslöser neu zu bewerten. |

## Flotten-Verhalten(`--all`)

`tesserae engine --all` hält jedes registrierte Projekt in einem Prozess frisch. Jede Projekteinheit erhält ihren eigenen Konsolidierungs-Thread mit den gleichen Steuerelementen, und alle Einheiten teilen sich ein flottenweites Kompilierungs-Tor — daher serialisiert sich eine Konsolidierung in einem Projekt gegen Kompilierungen in der gesamten Flotte, überlappt sich mit keiner.

## Ausgearbeitetes Beispiel

Aktivieren Sie Destillation, führen Sie dann die Engine mit einem schnelleren Schlafzyklus zu einer Demo aus — Konsolidierung nach 60 Sekunden Leerlauf und mindestens alle 30 Minuten unabhängig:

```bash
export TESSERAE_AGENT_DISTILL=1
tesserae engine \
  --consolidate-idle 60 \
  --consolidate-every 1800 \
  --consolidate-check 15
```

Arbeiten Sie wie gewohnt in Ihrem Editor und in Ihren Agenten; die Engine beobachtet, puffert und recompiliert jede Änderung. Stoppen Sie eine Minute lang und der Leerlauf-Auslöser tritt auf: der Konsolidierungs-Thread erwirbt das Kompilierungs-Tor und destilliert jeden Agent unter Speicherdruck — reorganisiert, komprimiert und vergisst sicher — dann schläft wieder ein. Arbeiten Sie nach der 30-Minuten-Marke weiter, ohne jemals anzuhalten, und auch die Obergrenze tritt auf, daher konsolidiert sich ein unerbittliches Projekt immer noch.

Um die Engine laufen zu lassen, aber Konsolidierung manuellen `tesserae distill`-Läufen zu überlassen, übergeben Sie `--no-consolidate`. Um sie im Leerlauf laufen zu lassen, aber nie nach einem festen Zeitplan, übergeben Sie `--consolidate-every 0`.

# Automatische Konsolidierung — der Schlafzyklus der Engine

<!-- translations:start -->
<p align="center"><a href="../engine-consolidation.md">English</a> · <a href="engine-consolidation.ko.md">한국어</a> · <a href="engine-consolidation.zh.md">中文</a> · <a href="engine-consolidation.ja.md">日本語</a> · <a href="engine-consolidation.ru.md">Русский</a> · <a href="engine-consolidation.es.md">Español</a> · <a href="engine-consolidation.fr.md">Français</a> · <a href="engine-consolidation.de.md">Deutsch</a></p>
<!-- translations:end -->

Das Gehirn konsolidiert Erinnerungen während des Schlafs. Während Sie schlafen, wird die rohe Erfahrung des Tages umorganisiert, komprimiert und integriert — die jüngsten, lauten Dinge werden in eine dauerhafte Struktur gefaltet. Die Tesserae-Engine macht das Gleiche. Wenn ein Projekt **inaktiv** wird, stoppt der immer aktive Daemon, auf die nächste Bearbeitung zu warten, und verbringt die ruhige Zeit damit, das zu reorganisieren, was er bereits weiß: er **komprimiert und vergisst** laute aktuelle Erinnerungen, lässt Wissen, das niemand abgerufen hat, **durch Nichtverwendung verblassen** und **entdeckt neue Verbindungen** zwischen dem, was überlebt.

Bislang wurde dieser Durchgang nur ausgeführt, wenn Sie ihn anforderten — `tesserae refresh` unter Speicherdruck oder ein explizites `tesserae distill`. Die Engine recompilierte bei jeder Datei und jedem Sessionsereignis, konsolidierte sich aber nie automatisch. Der **Schlafzyklus** schließt diese Lücke: Lassen Sie `tesserae engine` laufen und die Konsolidierung findet während der Ruhe statt, ohne dass Sie sich einen Befehl merken müssen.

Wie alles im System der [geschichteten Erinnerungen](agent-memory.de.md) ist dies **ein Noop, wenn Sie nicht teilnehmen** — der Daemon konsolidiert im Leerlauf, aber die Destillation darunter funktioniert nur, wenn `TESSERAE_AGENT_DISTILL` eingestellt ist.

## Wann es auslöst

Ein dedizierter Konsolidierungs-Thread wacht in einem festen **Prüfintervall** (Standard 30 Sekunden) auf und bewertet zwei unabhängige Auslöser gegen eine monotone Aktivitätsuhr:

- **Leerlauf-Auslöser.** Das Projekt hat mindestens `--consolidate-idle` Sekunden lang kein Triggerereignis und keine Pipeline-Ausführung gesehen (Standard **300 Sekunden = 5 Minuten**). Dies ist der Fall der "Konsolidierung während der Ruhe" — die Engine bemerkte, dass Sie aufgehört haben zu arbeiten, und nutzte die Pause. Ein **Boden** seit der letzten Konsolidierung verhindert Flattern, daher konsolidiert sich ein geschäftiges Projekt, das gerade ruhig geworden ist, nicht beim empfindlichen Auslöser.
- **Obergrenze-Auslöser.** Mindestens `--consolidate-every` Sekunden sind seit der letzten Konsolidierung verstrichen, **unabhängig von der Aktivität** (Standard **21600 Sekunden = 6 Stunden**). Dies garantiert, dass ein ständig beschäftigtes Projekt sich immer noch regelmäßig konsolidiert, anstatt nie einen ruhigen Moment zu haben. Das Setzen auf `0` deaktiviert die Obergrenze — dann ist Leerlauf der einzige Auslöser.

Jede Bearbeitung, jeder Sessionsdurchgang oder jede Neukompilierung erhöht die Aktivitätsuhr, daher verstreicht das Leerlauf-Fenster nur während echter Ruhe. Beide Uhren sind **monoton**, nie Wanduhr, und werden niemals in einem Artefakt beibehalten — Konsolidierungszeitpunkt kann niemals das Byte-deterministische Diagramm stören.

## Was ausgeführt wird — fünf Operationen

Jede Auslösung lädt das kompilierte Diagramm aus `.tesserae/graph.json` (wenn die Datei fehlt, wird der Durchgang übersprungen) und führt fünf Konsolidierungsvorgänge in der richtigen Reihenfolge aus. Zusammen spiegeln sie das wider, was ein ruhender Gehirn macht: die letzten lauten Dinge komprimieren, das nie Besuchteste verblassen lassen, neue Assoziationen zwischen dem, was überlebt, verkabeln und proben — es wird jetzt, während niemand wartet, ein wenig Aufwand in die Beschreibungen investiert, die ein Leser als Nächstes haben möchte.

### 1. Komprimieren / vergessen — Destillation

Ruft denselben `maybe_distill_on_refresh`-Einstiegspunkt auf, den `tesserae refresh` verwendet, um die Erinnerung jedes Agenten zu reorganisieren, zu komprimieren und sicher zu vergessen. Diese Funktion ist intern **dreifach gesperrt** und wird nie für einen Fehler pro Agent angehoben:

1. **Opt-in-Tor** — `TESSERAE_AGENT_DISTILL=1` (oder `{"agent_distill": {"enabled": true}}` in `config.json`). Standardmäßig deaktiviert; der gesamte Zyklus ist ein sicherer Noop, bis Sie ihn einschalten.
2. **Pro-Agent-Wasserstand** — ein Agent, dessen Ergebnisse sich seit der letzten Destillation nicht geändert haben, wird übersprungen.
3. **Pro-Agent-Speicherdruck** — nur Agenten, deren undestillierte Ergebnisse nicht mehr in die Hälfte einer Kontextlesung passen, werden konsolidiert (MemGPT-Stil-Auslöser).

Also auch wenn die Konsolidierung nach einem Zeitplan **auslöst**, **funktioniert** sie nur für Agenten, die sich angemeldet haben und tatsächlich genug neue Erinnerungen angesammelt haben, um sie zu rechtfertigen. Siehe [Geschichtete Agent-Erinnerung](agent-memory.de.md) für das, was Destillation produziert.

### 2. Durch Nichtverwendung vergessen — LRU-Abbau beim Abrufen, nicht nur nach Alter

Der Abbau der Destillation wird nicht mehr nur durch das Erstellungsalter vorangetrieben. Jede Lesefläche zeichnet den Zugriff auf die Ergebnisse, die sie zurückgibt, auf — `last_accessed_at` und `access_count` — in einem **`node_memory`-Sidecar**, niemals in `graph.json`. Bevor der Destillationsdurchgang den Abbau berechnet, fusioniert er diesen Live-Zugriffszustand in seine Arbeitsansicht, sodass ein Ergebnis, das seit seiner Erstellung nicht abgerufen wurde, abbaut und zur Absorption oder Herabstufung in Frage kommt, während eines, das kürzlich gelesen wurde, unabhängig von seinem Alter frisch bleibt. Dies ist **Abruf-Aktualität**, die LRU-Intuition (Least Recently Used) auf Erinnerungen angewendet: Wissen, das Sie weiterhin abrufen, bleibt erhalten; Wissen, das niemand anfordert, verblasst zuerst. Ein leerer Sidecar reproduziert das alte Verhalten nur nach Alter exakt, daher ist es vollständig rückwärtskompatibel.

### 3. Zuordnen — neue Verbindungen entdecken

Der letzte Vorgang sucht nach *neuen* Beziehungen zwischen dem, was überlebt hat. Er bettet destillierte Notizen ein und verknüpft Paare, deren Bedeutungen nahe beieinander liegen — **Einbettungs-Gating**, daher wird er nur ausgeführt, wenn ein echtes Einbettungs-Backend konfiguriert ist (der Hash-Stub wird übersprungen, nie rauschhafte Links produzierend). Die Entdeckung läuft innerhalb des Projekts und **übergreifend** und die Verbindungen, die sie findet, werden als `shares_concept_with`-Kanten mit einem `federation_semantic`-Marker geprägt.

Entscheidend ist, dass diese erkannten Kanten in eine **Sidecar-Überlagerung** unter `.tesserae` geschrieben werden, *niemals* in `graph.json`. Die Überlagerung **sammelt sich über Zyklen an** — jeder Zuordnungsdurchgang dedupliciert und erweitert das, was frühere Durchgänge gefunden haben. Bei der Lesezeit (Abfrage, PPR-Expansion, Föderations-Ansichten) wird die Überlagerung **nur im Speicher** in das Diagramm zusammengeführt, genau wie die Pro-Agent-Ansichtsüberlagerung — daher wird das Byte-deterministische `graph.json` niemals berührt. Der gesamte Vorgang wird eingewickelt und wird nie in die Daemon-Schleife angehoben.

### 4. Zusammenfassen — die Community-Caches vorwärmen, in die Agenten hinabsteigen

`graph_map` serviert eine Karte pro Bereich. Ein Bereich, dessen Zusammenfassungs-Cache kalt ist, erhält eine deterministische *strukturelle* Karte — eine Mitgliederzahl und eine Liste der Top-Mitglieder — und der erste Agent, der ihn besucht, benötigt einen synchronen LLM-Aufruf für die Prosa. Diese Operation verlagert diese Kosten aus dem Lesepfad: innerhalb eines Pro-Tick-Budgets (`--summarize-budget`, Standard 25; `0` deaktiviert es) materialisiert sie Zusammenfassungen für die Bereiche, die am ehesten als nächstes besucht werden, damit der Besuch einen warmen Cache findet.

Kandidaten werden nach **Anforderung** eingestuft — die Zugriffszählungen des Bereichs von `graph_map`-Durchläufen plus die Zugriffszähler seiner Mitglieder — dann nach Größe, Grad und Ebene in einer Gesamtreihenfolge, sodass zwei Ticks über identischem Zustand dieselben Bereiche auswählen. Ein Cache, der bereits warm und noch Digest-gültig ist, kostet kein Budget; nur eine kalte Materialisierung kostet. Ohne einen LLM-Client ist der gesamte Vorgang ein No-Op.

### 5. Brief — die Charter-Domain-Briefe vorwärmen

Die gleiche Form, nur auf einer anderen Achse: Die Kandidaten sind die aktiven Domains von [der Charter](../README.md) statt der Dendrogramm-Communities. Eine kalte Domain wird überall, wo sie erscheint, als strukturelle Karte gerendert — in `graph_map`, in `charter_route`s Scoring-Corpus und in Lint's `CHARTER_FALLBACK`-Zensus — daher ist dieser Durchgang das, was der Charter Prosa verleiht.

Das Budget ist seine eigene Stellschraube (`--brief-budget`, Standard 8; `0` deaktiviert es), absichtlich getrennt von `--summarize-budget`, damit kein Betrieb den anderen aushungert, und absichtlich kleiner: Die **Abteilungen** der Charter sind das, was `graph_map` als seinen Root-Kartensatz ausliefert, und es gibt nur eine Handvoll von ihnen, daher wärmt 8 den Einstiegspunkt im ersten Idle-Tick auf und tiefere Ebenen folgen dahinter mit 8 pro Tick.

Die Reihenfolge ist **Breitensuche**, nicht eine Anforderungsreihenfolge. Eines Domains Mitgliedermenge enthält seinen gesamten Teilbaum, daher dominiert die Anforderung eines übergeordneten immer die seiner Untergeordneten und keine Domain wird vor ihren Vorfahren vorgewärmt. Das ist absichtlich: Agenten steigen von der Wurzel ab, daher ist die grobe Karte die, die zuerst gelesen wird und für die sich Prosa zu haben lohnt. Zugriffszähler ordnen Domains, die sich nicht gegenseitig enthalten, und die aktiven **Abteilungen** — Domains ohne aktives Eltern-Element, dieselbe Regel wie die `graph_map`-Wurzel, nicht `tier == 1` — sortieren vor allem anderen.

Einige Domains kosten nie einen Budget-Platz, da ein Platz für einen LLM-Aufruf reserviert ist: ausrangierte Domains, der `intake`-Zensus (der kein Thema hat, daher würde ein Brief, geschrieben aus 25 seiner Tausenden von Mitgliedern, nur einen Bruchteil eines Prozents mit Sicherheit beschreiben), eine Domain, deren Mitglieder das Diagramm verlassen haben, und alles bereits Warme. Und eine Domain, deren Materialisierung **fehlschlägt** — meist weil ihre Prosa keines ihrer untergeordneten Elemente zitierte und abgelehnt wurde — wird für eine verdoppelte Tick-Anzahl zurückgestellt, statt immer wieder versucht zu werden, daher kann eine ständig unwärmbare Domain keinen Platz blockieren, den eine wärmbare brauchen könnte.

### Was dies pro Stunde kostet

Beide Budgets sind pro **Tick**, und ein Tick schießt höchstens einmal pro `--consolidate-idle` Fenster. Bei den Defaults:

| | pro Tick | Ticks/Stunde bei `--consolidate-idle 300` | Obergrenze |
|---|---|---|---|
| Zusammenfassen | 25 | 12 | 300 LLM-Aufrufe/Stunde |
| Brief | 8 | 12 | 96 LLM-Aufrufe/Stunde |
| **Gesamt** | **33** | **12** | **396 LLM-Aufrufe/Stunde** |

Das ist eine **Obergrenze, die nur erreicht wird, während Caches kalt sind**, und sie fällt auf **null**: Ein warmer, Digest-gültiger Cache kostet keinen Aufruf und keinen Platz, daher verbringt der Schlafzyklus, sobald die Bereiche und Domains eines Projekts zusammengefasst werden, nichts, bis sich das Diagramm ändert. Stellen Sie eines der Budgets auf `0` ein, um seinen Betrieb auszuschalten, oder erhöhen Sie `--consolidate-idle`, um Ticks seltener zu machen.

**Ein Budget ist eine Obergrenze, keine Quote.** Beide Budgets werden *sequenziell* innerhalb eines Ticks ausgegeben, und der Tick hält das Kompilierungs-Tor für den ganzen Durchgang — daher könnte ein Tick bei den Standardeinstellungen das Tor über 33 aufeinanderfolgende LLM-Aufrufe hinweg belegen. Ein Datei-Speichern, das mitten im Tick auftritt, musste jeden verbleibenden Aufruf abwarten, bevor seine Pipeline-Ausführung starten konnte, was bei einem CLI-Provider Minuten sind. Beide Vorwärmschleifen prüfen nun oben in jeder Iteration, ob eine Pipeline-Ausführung am Tor blockiert ist, und **geben ihr verbleibendes Budget auf**, falls ja:

- die Prüfung erfolgt *zwischen* Aufrufen, niemals mitten in einem Aufruf, daher findet der bereits laufende Durchgang immer statt und die Pipeline wartet auf höchstens diesen einen Aufruf;
- das vorzeitige Stoppen ist verlustlos. Wärmen ist idempotent, daher ist ein Bereich oder eine Domain, den/die der Tick nicht erreicht hat, auf dem nächsten einfach noch immer kalt, in der gleichen Reihenfolge — nichts geht verloren, wird beschädigt oder zweimal bezahlt;
- eine aufgegebene Domain erhält **keinen Back-off-Treffer**. Treffer sind für eine Domain, deren Wärmungsversuch einen Aufruf verbrauchte und fehlschlug; eine aufgegebene wurde nie versucht, daher würde ihre Belastung eine wärmbare Domain in der Warteschlange nach unten drücken, weil eine nicht zugehörige Datei gespeichert wurde;
- es wird berichtet, nicht stillschweigend. Das Zusammenfassungs-Dict des Ticks erhält `abandoned` und `unspent` (wie viele Budget-Plätze ungenutzt blieben), daher unterscheidet das Daemon-Protokoll „zurückgetreten für eine Pipeline" von „es gab nichts zum Aufwärmen".

**Warum hier und nicht im Compile.** Ein Brief kostet einen LLM-Aufruf. Sie während des Kompilierens zu prägen würde einen Aufruf pro Domain bei jedem Kompilierungsvorgang bedeuten, und Kompilierung ist der Pfad, den dieses Projekt deterministisch und günstig hält. Sie faul beim Lesen zu prägen würde bedeuten, dass ein `graph_map`-Aufruf auf einem Modell blockieren könnte. Der Idle-Schlafzyklus ist der einzige Ort, der bleibt, der einen Aufruf verbringen kann, auf den niemand wartet.

## Sicherheit und Determinismus

- **Läuft unter dem Kompilierungs-Tor, für den ganzen Durchgang.** Konsolidierung erwirbt das gleiche Sperren wie eine Neukompilierung, daher **serialisiert** sie sich mit Kompilierungen und **überlappt sich niemals**. Eine ausstehende Kompilierung wartet auf eine laufende Konsolidierung und umgekehrt — das Diagramm wird niemals während des Schreibens gelesen. Das Tor wird absichtlich **nicht** zwischen LLM-Aufrufen freigegeben: Jeder Betrieb in einem Tick liest die eine `graph.json`, die der Tick geladen hat, daher würde das Zurückgeben des Tors während des Durchgangs eine Neukompilierung ermöglichen, das Diagramm darunter umzuschreiben sodass die früh im Durchgang geschriebenen Briefe ein anderes Diagramm beschreiben würden als die spät geschriebenen. Deshalb gibt ein Tick, der auf eine Pipeline wartet, sein verbleibendes Budget **auf**, anstatt das Tor freizugeben — es tauscht spekulative Wärmung gegen Latenz, niemals Konsistenz gegen Latenz.
- **Wird niemals in die Daemon-Schleife angehoben.** Der gesamte Durchgang wird eingewickelt; jeder Fehler wird protokolliert und der Thread schleift weiter. Eine fehlgeschlagene Konsolidierung bringt die Engine nie zum Abstürz.
- **Noop, wenn das Tor aus ist.** Mit ungesetztem `TESSERAE_AGENT_DISTILL` lädt der Durchgang nichts Teures und kehrt sofort zurück, daher kostet die Beibehaltung des Schlafzyklus im Wesentlichen nichts.
- **Deterministische Artefakte, unverändert.** Destillierte Artefakte bleiben deterministische bei ihren Eingaben; der Schlafzyklus ändert nur *wann* Destillation läuft, niemals *was* sie produziert. Leerlauf-Zeit leckt niemals in `graph.json` oder irgendeine destillierte Schicht.
- **`graph.json` bleibt Byte-idempotent.** Keine Operation schreibt es. Der Zugriffszustand lebt im `node_memory`-Sidecar, erkannte Verbindungen in einer kumulativen Überlagerung und sowohl Zusammenfassungen als auch Domain-Briefe im `community_summaries`-Cache — alle unter `.tesserae`, alle nur im Speicher bei Lesezeit zusammengeführt. Die maßgeblichen Diagramm-Bytes sind von Abrufverlauf, erkannten Links oder vorgewärmter Prosa unberührt. Zusammenfassungen und Briefe sind **Caches, nicht Wissen**: Das Löschen des Cache-Verzeichnisses kostet den nächsten Leser nur eine strukturelle Karte und nichts anderes.
- **Sauberes Herunterfahren.** Der Konsolidierungs-Thread beobachtet das Stopperereignis des Daemon und wird bei `Ctrl-C` / Herunterfahren rasch beendet. Es ist nur eine Funktion im Langzeitlaufmodus: `tesserae engine ... --once` startet ihn niemals.

## CLI-Flaggen

| Flagge | Standard | Effekt |
|---|---|---|
| `--consolidate` / `--no-consolidate` | on | Schlafzyklus vollständig aktivieren oder deaktivieren. Standardmäßig aktiviert (Noop, wenn Destillations-Tor nicht eingestellt). |
| `--consolidate-idle SECONDS` | `300` | Ruhefenster: Konsolidierung nach dieser Anzahl von Sekunden ohne Aktivität. |
| `--consolidate-every SECONDS` | `21600` | Obergrenze: Konsolidierung mindestens so häufig unabhängig von Aktivität. `0` deaktiviert die Obergrenze. |
| `--consolidate-check SECONDS` | `30` | Wie oft der Konsolidierungs-Thread aufwacht, um die Auslöser neu zu bewerten. |
| `--summarize-budget N` | `25` | Max LLM-Aufrufe pro Tick für die Vorwärmung von Community-Zusammenfassungen. `0` deaktiviert den Zusammenfassungs-Betrieb. |
| `--brief-budget N` | `8` | Max LLM-Aufrufe pro Tick für die Vorwärmung von Charter-Domain-Briefen. `0` deaktiviert den Brief-Betrieb. |

## Flotten-Verhalten (`--all`)

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

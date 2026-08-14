# `.tesserae/` — was darin liegt und was Löschen kostet

<!-- translations:start -->
<p align="center"><a href="../sidecars.md">English</a> · <a href="sidecars.ko.md">한국어</a> · <a href="sidecars.zh.md">中文</a> · <a href="sidecars.ja.md">日本語</a> · <a href="sidecars.ru.md">Русский</a> · <a href="sidecars.es.md">Español</a> · <a href="sidecars.fr.md">Français</a> · <a href="sidecars.de.md">Deutsch</a></p>
<!-- translations:end -->
Ein gereiftes Projekt sammelt rund sechzig Einträge unter `.tesserae/` an, und
ein Verzeichnislisting verrät nichts darüber, welche davon eine Kompilierung
kostenlos wiederherstellt, welche einen LLM-Durchlauf kosten und welche Arbeit
tragen, die nichts rekonstruieren kann. `compile.lock` und eine verwaiste
Null-Byte-tmp-Datei sehen genauso aus wie `candidate-same-as.json`, das
menschliche Urteile trägt.

Diese Seite ist diese Antwort, geordnet nach Konsequenz. Die Klassifikation
selbst liegt in `tesserae/sidecars.py` — ein Registry-Eintrag pro Datei, jeder
mit Besitzer, Art und dem, was ein Löschen kostet. Die Registry ist die Quelle
der Wahrheit; diese Seite ist ihre lesbare Projektion, und `tesserae doctor`
druckt die reale aus.

Jeder Eintrag trägt zwei voneinander unabhängige Felder:

| Art | Woher die Bytes kommen |
|---|---|
| `derived` | wird von einer Kompilierung aus den Quellen neu veröffentlicht |
| `accumulated` | wächst über die Zeit an; keine Kompilierung leitet es neu ab |
| `cache` | eine gespeicherte Antwort auf eine Frage, die erneut gestellt werden kann |
| `scratch` | Prozess-Buchhaltung: Locks, Pidfiles, tmp-Reste |

Die Art sagt, woher die Bytes kommen. Sie sagt **nicht**, ob Löschen sicher ist
— `safe_to_delete` ist ein eigenes Feld, und beide weichen oft genug
voneinander ab, dass es zählt: Ein `cache`, dessen Antwort von einem Modell kam,
ist nicht sicher löschbar, und eine `derived`-Datei kann menschliche
Freigaben tragen. Die Abschnitte unten sind nach diesem zweiten Feld geordnet,
weil es das ist, was Sie eigentlich wissen wollen.

## Bedenkenlos freigebbar — eine Kompilierung baut das neu

Löschen Sie irgendetwas davon, und die nächste Kompilierung legt es Byte für
Byte zurück, ohne einen einzigen Modellaufruf:

<!-- sidecars:safe-list -->
`agent_harness` · `code-graph.json` · `combined-graph.json` ·
`competitive_report.md` · `diverged-fields.md` · `doctor-report.json` ·
`doctor-report.md` · `graph.json` · `graph.kuzu` · `graphiti_episodes.jsonl` ·
`hierarchy.json` · `lint-report.json` · `lint-report.md` · `log.md` ·
`markdown_projection` · `merge-ledger.json` · `okf` ·
`okf-imported.graph.json` · `output-snapshot.json` · `report.md` · `research` ·
`schema-drift.md` · `site` · `summaries` · `temporal_facts.jsonl` · `wiki` ·
`.watch-cache.json` · `arxiv-cache.json` · `code-graph-cache.json` · `external`

`graph.json` steht mit Absicht auf dieser Liste. Der kompilierte Graph ist eine
reine Funktion der Quellen plus der angesammelten Sidecars weiter unten — genau
deshalb sind *jene* die schützenswerten, und genau deshalb ist der Reflex „ich
lösche einfach `.tesserae/` und kompiliere neu" falsch, obwohl die sichtbarste
Datei darin tatsächlich wegwerfbar ist.

## Kostet einen Modelldurchlauf — und ändert die Bytes von `graph.json`

Das sind gespeicherte Antworten eines LLM. Ein Neuaufbau kostet einen Durchlauf,
und das Modell liefert dieselben Worte nie zweimal — also ändern sich auch die
Bytes von allem, was daran hängt.

| Eintrag | Art | Was ein Neuaufbau kostet |
|---|---|---|
| `session_findings` | `cache` | der schärfste Fall: Diese Findings werden zu **Knoten** im Graphen, ein verworfener Cache lässt also einen nichtdeterministischen Extraktor erneut laufen und die nächste `graph.json` unterscheidet sich in den Bytes — der Bruch der Byte-Idempotenz, den dieses Repository bereits viermal kassiert hat |
| `community_summaries` | `cache` | LLM-geschriebene Community-Zusammenfassungen, verschlüsselt über den Mitglieder-Hash |
| `distill_cache` | `cache` | Ergebnisse der Agenten-Destillation |
| `distillation_cache` | `cache` | Destillationsergebnisse |
| `extraction_guidance_cache` | `cache` | ein LLM-formulierter Punkt je Feedback-Cluster |
| `schema_drift_cache` | `cache` | LLM-Subtyp-Vorschläge je Host-Typ |
| `supersede_cache` | `cache` | LLM-Schiedsspruch zur Ablösung (supersede) |
| `schema-drift-proposals.json` | `derived` | abgeleitete Bytes, nicht ableitbarer Inhalt: Derselbe Datensatz trägt das menschliche `approved`-Gate und einen editierbaren `proposed_type`, ein Neuaufbau kostet also einen Durchlauf **und** verwirft die Freigaben |

## Unwiederbringlich — nichts baut das neu

Nichts hiervon leitet eine Kompilierung erneut ab. Eines davon zu löschen ist
Datenverlust, keine Verzögerung.

| Eintrag | Art | Was verloren geht |
|---|---|---|
| `candidate-same-as.json` | `accumulated` | menschliche same-as-Urteile. Eine Kompilierung, die es nicht findet, schlägt nicht fehl — sie stellt stillschweigend eine Frage neu, die ein Mensch bereits beantwortet hat, und ein abgelehntes Paar kommt unabgelehnt zurück |
| `sqlite.db` | `accumulated` | gemischt; siehe unten |
| `agent-writes.jsonl` | `accumulated` | das von Agenten geschriebene Overlay, bei jeder Kompilierung als fünfter Produzent wieder eingespielt; Löschen tilgt jeden Agentenschreibvorgang |
| `vault_snapshot.json` | `accumulated` | die Basislinie, gegen die `vault_pull` diffed. Mitten in einer Bearbeitung gelöscht, kann die nächste Kompilierung Ihre Änderung nicht von ihrer eigenen früheren Projektion unterscheiden — der gesamte Override-Mechanismus des Vaults |
| `obsidian_vault` | `accumulated` | bidirektional und dem Nutzer gehörend: Ihre Änderungen hier werden in den Graphen zurückgezogen, das ist also keine Projektion, die man einfach neu zeichnet |
| `config.json` | `accumulated` | Projektkonfiguration, einschließlich `obsidian.vault_path` — Nutzereingabe, wird nie neu erzeugt |
| `charter` | `accumulated` | die Projekt-Charter wird verfasst, nicht extrahiert |
| `agents` | `accumulated` | die `registry.json` je Agent und die handgeschriebene `purpose.md` |
| `discovered_links.json` | `accumulated` | das Assoziations-Overlay sammelt bewertete Verknüpfungen über Läufe hinweg; ein einzelner Lauf rekonstruiert es nicht |
| `extraction-feedback.jsonl` | `accumulated` | menschliche Korrekturen aus Vault-Overlay und review-apply |
| `extraction-guidance.md` | `accumulated` | handgepflegte Guidance, in die ein evolve-Durchlauf hineinmergt |
| `harness_sessions` | `accumulated` | importierter Sitzungszustand |
| `harness_sessions.db` | `accumulated` | importierte Agentensitzungen, deren Ursprungstranskripte wegrotieren — ein Re-Import stellt sie nicht wieder her |
| `session_chunks.db` | `accumulated` | normalisierte Turns, live vom Tailer des Daemons geschrieben, aus Transkripten, die nicht verfügbar bleiben |
| `manifest.json` | `accumulated` | Ingest-Zustand je Quelle; ohne ihn ingestiert der nächste Batch alles erneut und lässt die Extraktion über bereits gelesene Quellen nochmals laufen |
| `.build-history.jsonl` | `accumulated` | eine Zeile je Build mit dem `git_head`, auf dem kompiliert wurde; ohne sie bleibt die Veraltung des Graphen dauerhaft unbekannt |

### `sqlite.db` ist gemischt und wird nach seiner wertvollsten Tabelle eingeordnet

Der Graph-Spiegel darin ist abgeleitet und `node_vectors` ein verwerfbarer
Vektor-Cache — dieselbe Datei hält aber auch `node_memory` (Decay,
Zugriffszähler, verstärkte Konfidenz), `fact_observed` (Transaktionszeit, eine
echte Uhr, die nur vorwärts läuft) und `read_audit`, und nichts davon ist
wiederherstellbar. Die Datei zu löschen, um den Vektor-Cache freizugeben, setzt
das „wann wir das erfahren haben" jedes Fakts auf jetzt zurück. Holen Sie sich
Platz mit `tesserae doctor --fix` zurück, das ein Vacuum ausführt, statt die
Datenbank zu löschen.

## Locks, Pidfiles und Reste

| Eintrag | Art | Vor dem Entfernen |
|---|---|---|
| `compile.lock` | `scratch` | der Kompilierungs-Mutex. Wird von **keinem** automatischen Pfad entfernt — der dokumentierte Fehlerfall sind aufgestaute SessionEnd-Kompilierungen, und doctors `compile_lock`-Check ist aus demselben Grund nur meldend |
| `.recompile.lock.d` | `scratch` | mkdir-basierter Hook-Mutex; einen gehaltenen zu entfernen lässt zwei Rekompilierungen kollidieren |
| `session_chunks.lock` | `scratch` | der „überspringen, wenn gehalten"-flock des Backfills; einen gehaltenen zu entfernen lässt zwei Backfills denselben Tag schreiben |
| `daemon*.pid` | `scratch` | Engine-Pidfile, host-bezogen als `daemon.<host>.pid`. Doctor entfernt eines erst, nachdem der eingetragene Besitzer **auf dieser Maschine** als tot bestätigt ist |
| `graph.json.bak-*` | `scratch` | kein Tesserae-Codepfad schreibt sie. Es sind handgemachte Kopien aus einer Wiederherstellungssitzung — gemeldet, nie entfernt, weil ein Mensch sie angelegt hat |
| `*.tmp*` | `scratch` | die verwaiste Hälfte eines tmp+replace-Schreibvorgangs, benannt `<target>.tmp.<pid>.<hex>`. Erst entfernbar, wenn die besitzende pid weg ist: Ein lebender Schreiber steckt mitten im rename |
| `.*-hook.log*` | `scratch` | Diagnosen der Shell-Hooks; doctor rotiert die zu groß gewordenen |

## `~/.tesserae/` — maschinenweit, gleicher Verzeichnisname

Das Nutzer-Verzeichnis heißt wie das Projekt-Verzeichnis und bedeutet etwas
anderes. `config.json` existiert in beiden: im Projekt ist es die
Projektkonfiguration, hier ist es die LLM-Konfiguration für jedes Projekt auf
der Maschine.

| Eintrag | Art | Was verloren geht |
|---|---|---|
| `registry.json` | `accumulated` | die Projekt-Registry. Sie zu löschen deregistriert jedes Projekt auf dieser Maschine |
| `config.json` | `accumulated` | maschinenweite LLM-Konfiguration; Nutzereingabe |
| `host_id` | `accumulated` | die Identität dieser Maschine. Neu erzeugt, wirkt jedes host-bezogene Pidfile und jeder Sitzungseintrag auf geteiltem Speicher fremd |
| `harness_sessions` | `accumulated` | maschinenweiter Zustand des Sitzungsimports |
| `llm_cache` | `cache` | zwischengespeicherte LLM-Antworten; ein Neuaufbau ruft Modelle und reproduziert sie nicht |
| `federation` | `cache` | projektübergreifende Link- und Vektor-Caches — gefahrlos löschbar |
| `wiki` | `derived` | maschinenweiter serve-Scratch — gefahrlos löschbar |
| `engine.pid` | `scratch` | Flotten-Pidfile; ein veraltetes hielt einmal eine seit sechs Tagen tote pid, weshalb pidlock validiert statt zu vertrauen |
| `engine.pid.lock` | `scratch` | Mutex des Flotten-Pidfiles; einen gehaltenen zu entfernen lässt zwei Flotten starten |
| `*.bak*` | `scratch` | Kopien von `registry.json` und `config.json` von vor einer Migration. Kein Codepfad schreibt sie, sie existieren also, weil jemand sie behalten wollte |

## Die reale Klassifikation ansehen

```bash
tesserae doctor          # the `sidecars` check, under hygiene
tesserae doctor --fix    # removes orphaned tmp halves, nothing else
```

Der `sidecars`-Check liest Ihr tatsächliches `.tesserae/` gegen die Registry und
meldet drei Populationen getrennt: verwaiste tmp-Hälften, handgemachte
`graph.json.bak-*`-Kopien und Einträge, die kein Registry-Eintrag beansprucht.
`--fix` entfernt nur die ersten, und nur wenn die pid des Schreibers tot und die
Datei älter als 24 Stunden ist — weil ein lebender Schreiber zwischen
`write_text` und `replace` steht und `os.kill(pid, 0)` nur über die lokale
Prozesstabelle Auskunft gibt, während mehrere Hosts ein `.tesserae/` mounten
können.

**Nicht klassifizierte Einträge werden gemeldet und nie angefasst.** Ein Eintrag,
den die Registry nicht beansprucht, ist eher die Datei von jemand anderem — Ihre
Notizen, der Cache eines anderen Werkzeugs — als ein Tesserae-Bug; die Antwort
auf einen Fund ist also, ihn zu benennen, nicht ihn zu entfernen. Auf demselben
Weg wird auch ein neuer Tesserae-Sidecar sichtbar, den niemand registriert hat.

Tesserae liefert kein pauschales `reset`-Verb aus. Die Klassifikation ist das,
was ein solches Kommando möglich machen würde; sie aufzuschreiben und im selben
Zug ein destruktives Kommando dagegen auszuliefern, ist die falsche Reihenfolge.

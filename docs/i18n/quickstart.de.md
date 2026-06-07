# Quickstart

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a></p>
<!-- translations:end -->
Diese Seite zeigt den kürzesten Pfad von einem existierenden Projektverzeichnis zu einem browserbaren Tesserae.

## 1. Setup-Wizard ausführen

Aus dem Projekt, das du indexieren willst:

```bash
cd /path/to/my-project
tesserae project setup
```

Der Wizard erkennt gängige Quellen wie `README.md`, `docs`, `src`, `lib`, `app`, `packages` und `data`, und schreibt dann `.tesserae/config.json`. Er konfiguriert außerdem das Default-Cognee-Backend, damit `project ask` Cognee versuchen und auf die kompilierte Wiki-Suche zurückfallen kann.

Für ein vollautomatisiertes Setup mit aktiviertem Understand Anything und Cognee-Runtime-Memory:

```bash
tesserae project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
  --run-cognee \
  --install-cognee
```

Was das tut:

| Flag | Effekt |
|---|---|
| `--with-understand-anything` | Fügt die UA-Graph-Projektion als Quelle hinzu. |
| `--install-understand-anything` | Installiert/aktualisiert die UA-Companion-Skills. |
| `--understand-anything-platform codex` | Nutzt Codex, um den verwalteten UA-Refresh-Wrapper von Tesserae auszuführen. |
| `--with-raganything` | Aktiviert multimodale Ingestion via RAG-Anything. |
| `--install-raganything` | Installiert raganything[all] beim Setup. |
| `--raganything-parser` | Parser-Wahl: mineru (default), docling, paddleocr. |
| `--run-raganything` | Aktualisiert RAG-Anything automatisch bei jedem Compile. |
| `--run-cognee` | Führt einen Best-Effort-Cognee-Runtime-Cognify beim Compile aus. |
| `--install-cognee` | Installiert Cognee mit dem aktuellen Python, falls fehlend. |

Nutzer müssen den UA-Installationspfad nicht kennen oder `/understand` tippen; `project compile` ruft `project refresh-understand-anything` auf, wenn der UA-Graph fehlt oder veraltet ist.

## 2. Graph und Projektionen kompilieren

```bash
tesserae project compile
```

`project compile` schreibt die langlebigen Artefakte:

```text
.tesserae/
  config.json
  graph.json
  manifest.json
  sqlite.db
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  competitive_report.md
  markdown_projection/
  obsidian_vault/
  agent_harness/
  harness_sessions/
  site/
  cognee_bundle/
```

Nach dem ersten Lauf `--changed-only` nutzen, um unveränderte Markdown-Dateien zu überspringen, während der vorherige Graph erhalten bleibt, wenn keine Dateien geändert wurden. Ist Understand Anything aktiviert, refresht/materialisiert der Compile zuerst `.tesserae/external/understand-anything.md`; ist Cognee-Runtime aktiviert, aktualisiert er außerdem Cognee Best-Effort, nachdem `.tesserae/cognee_bundle/` geschrieben wurde.

> **One-Shot-Pipeline.** `tesserae project refresh` führt die gesamte Schleife in-process aus – es importiert neue Agent-Sessions, compiliert und synchronisiert den Vault in einem einzigen Befehl. Übergib `--changed-only` für den optionalen inkrementellen Compile und `--skip-sessions`, um den langsameren Harness-Session-Discovery-Scan zu überspringen.

## 3. Statisches Frontend bauen und servieren

```bash
tesserae project build-site
tesserae project serve --port 8765
```

Öffne:

```text
http://127.0.0.1:8765/
```

<!-- BEGIN: subagent-r-watch -->
### Auto-Rebuild beim Speichern

Paare den Dev-Server mit einem Polling-Watcher, sodass Edits unter `data/` und `docs/` einen inkrementellen Re-Compile auslösen:

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae project watch
```

`project watch` pollt alle 2 s, debounct 1 s und führt `compile --changed-only` aus. Nutze `--once` für Cron-Style-Rebuilds (Snapshots vs `.tesserae/.watch-cache.json`), `--paths <dir>` für zusätzliche Watch-Verzeichnisse und `--interval` / `--debounce`, um die Kadenz zu tunen.
<!-- END: subagent-r-watch -->

### Den Refresh-Daemon starten

Wenn du eine dauerhaft laufende Engine willst, die deine Quellen selbstständig überwacht, Bearbeitungs-Bursts zusammenfasst und automatisch neu compiliert, um die Wissensbasis aktuell zu halten, starte den überwachten Daemon:

```bash
tesserae project engine
```

`project engine` (Alias `project daemon`) ist der langlaufende Supervisor: Er pollt alle 2 s und wartet vor jedem Rebuild ein Ruhefenster von 1 s ab. Stelle die Kadenz mit `--interval` und `--debounce` ein, richte ihn mit `--project` auf ein anderes Projekt aus, oder übergib `--once`, um einen einzelnen deterministischen Drain-Zyklus auszuführen und zu beenden (praktisch für Cron oder CI). Das ist das unbeaufsichtigte Gegenstück zu `project watch`: Lass ihn laufen, und Graph, Vault und Site bleiben aktuell, während du und deine Agents arbeiten.

Für eine annotierte Tour durch jede sichtbare Route — Home, Sources, Concepts, Entities, Papers, Repos, Topics, Syntheses, Questions, Timeline, Graph plus die AI-Siblings — siehe [`docs/frontend-redesign.md`](frontend-redesign.de.md).

Das Frontend ist dependency-arm und schreibt:

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. Lokale Agent-Session-Historie importieren

Der Session-History-Import ist explizit: ein normaler Compile/Build liest bereits normalisierte Sessions, scannt aber von sich aus keine privaten Claude-Code- oder Codex-Transcript-Stores.

```bash
# Preview matching Claude Code/Codex sessions for this project:
tesserae project sessions discover

# Normalize and store them under .tesserae/harness_sessions/:
tesserae project sessions discover --import

# Confirm the imported set:
tesserae project sessions list

# Rebuild so sessions/index.html and session detail pages are emitted:
tesserae project build-site
```

Importierte Sessions erscheinen im globalen Sessions-Bereich, in der Site-Search und in den Home-Browse-Cards. Session-Detailseiten rendern User-/Assistant-Turns als lesbares Markdown, hängen Tool-Use-Blöcke unter den vorhergehenden Assistant-Turn und exponieren eine Left-Turn-Rail für `#turn-N`-Navigation. Siehe [`docs/session-history.md`](session-history.de.md) für Privacy-Hinweise, Import-Formate und die aktuelle Transcript-Typografie-Map.

## 5. Wiki linten

```bash
tesserae project lint
```

Läuft den kompilierten Graph + Wiki + die Site ab und markiert Orphan-Papers, veraltete Zitate, Drift zwischen Graph und wiki/, Ghost-Synthese-Inputs und mehr. Schreibt `.tesserae/lint-report.md` und `.tesserae/lint-report.json`. Übergib `--fix-trivial`, um sichere Auto-Fixes anzuwenden (fehlende `implemented_in`-Kanten, Ghost-Input-Pruning), und `--severity error`, um den Exit-Code nur bei Errors fail zu setzen.

## 6. Wiki abfragen

```bash
tesserae project query "What is Gaussian Splatting?"
```

Standardmäßig nur Search — BM25 über `.tesserae/site/search-index.json`, mit einem 200-Zeichen-Excerpt aus der passenden `wiki/<kind>/<slug>.md`. Übergib `--kind papers` (oder `concepts`, `repos` etc.) zum Eingrenzen, `--top-k N` zum Verbreitern und `--json` für strukturierten Output. Füge `--llm` hinzu (oder setze `TESSERAE_QUERY_LLM=1`), um Claude um eine synthetisierte Antwort mit `[node_id]`-Zitaten zu bitten; `--interactive` öffnet ein Readline-REPL — leere Zeile oder EOF beendet. `TESSERAE_QUERY_DRY_RUN=1` exerziert den Prompt ohne API-Call.

## 7. Agent-tauglichen Context bei Bedarf compilieren

Das Highlight von v0.5.0 ist der On-Demand Context Compiler: Fordere vom compilierten Graph ein einzelnes, zitiertes Context-Dokument an, das auf eine Anfrage zugeschnitten und auf das Context-Window eines Agents dimensioniert ist.

```bash
tesserae project context "Wie funktioniert session import?"
```

Er nutzt die zur Anfrage passenden Knoten als Seed für Personalized PageRank (mit `--seeds <node_id>` explizit seeden), erweitert die Nachbarschaft (`--depth`, Standard 2) und stellt ein zitiertes Dokument zusammen, das durch ein Zeichen-`--budget` begrenzt ist (Standard 32000; `<= 0` für unbegrenzt). Füge `--synthesize` für eine zusätzlich vom LLM verfasste Zusammenfassung hinzu (erfordert ein LLM-Backend) und `-o/--output <file>`, um das Dokument in eine Datei statt auf stdout zu schreiben.

Derselbe Compiler ist Agents über MCP als Tool `compile_context` zugänglich, sodass ein Coding-Agent mitten im Gespräch genau den passenden, budgetbegrenzten Projekt-Context ziehen kann – ganz ohne manuellen Export.

## 8. Agent-Harness-Dateien exportieren

```bash
tesserae project export-agent-harness
```

Unterstützte Ziele:

- Claude Code
- Codex
- Gemini
- Kiro
- Cursor
- OpenCode

Beispiel-Subset:

```bash
tesserae project export-agent-harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. Obsidian-Vault exportieren

```bash
tesserae project export-obsidian
```

Oder in ein existierendes Vault schreiben:

```bash
tesserae project export-obsidian --vault "$OBSIDIAN_VAULT_PATH"
```

Das Vault enthält Markdown-Projektionen, `.obsidian`-Defaults, Graph-Coloring, `raw/assets/` und ein Dataview-Dashboard.

## 10. MCP konfigurieren

```bash
tesserae project mcp-config --server-name my_project_wiki
```

Füge die Ausgabe unter `mcp_servers` in `~/.hermes/config.yaml` ein und starte Hermes/Gateway neu.

## 11. Graphiti-Export / -Sync

Dependency-freier Episode-Export:

```bash
tesserae project export-graphiti
```

Dry-Run-Sync-Smoke ohne installiertes Graphiti:

```bash
tesserae project sync-graphiti --dry-run
```

Live-Sync benötigt `graphiti_core` und ein erreichbares Neo4j-Backend:

```bash
tesserae project sync-graphiti \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. Auf GitHub Pages deployen

Pusht die kompilierte Site unter `.tesserae/site/` auf den `gh-pages`-Branch des git-Origins des Projekts:

```bash
tesserae project deploy --build --enable-pages
```

`--build` führt zuerst `project compile` aus, damit die Site frisch ist. `--enable-pages` schaltet Pages via `gh`-CLI ein (idempotent; mit einem Hinweis übersprungen, falls `gh` fehlt). Nutze `--dry-run`, um zu stagen und committen ohne zu pushen, `--branch` / `--remote`, um Defaults zu überschreiben, und `--force`, um Deploys mit einem dirty Working-Tree zu erlauben.

Die Site wird unter `https://<owner>.github.io/<repo>/` erreichbar.

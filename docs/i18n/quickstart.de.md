# Quickstart

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a></p>
<!-- translations:end -->
Diese Seite zeigt den kürzesten Pfad von einem existierenden Projektverzeichnis zu einem browserbaren Tesserae.

## Befehlsübersicht

Die CLI ist gruppiert: eine Handvoll alltäglicher Verben auf oberster Ebene, plus
Gruppen (`sessions`, `vault`, `export`, `code`, `config`, `projects`,
`integrations`, `lab`) für den Rest. Führe `tesserae --help` aus, um den ganzen Baum zu sehen:

```text
usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  context       Compile agent-ready context for a query
  ask           Ask the project memory a question
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query

ANALYSIS
  query         Raw retrieval over the graph (top-k, kind filters)
  lint          Graph lint report (--fix-trivial, --severity, --json)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  config        llm | show — machine-wide defaults (~/.tesserae/config.json)
  projects      register | list | activate | unregister | mcp-config — registry
  integrations  refresh raganything|understand-anything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve | schema-drift — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

Um die Flags eines einzelnen Befehls zu sehen, führe `tesserae <command> --help` aus (z. B. `tesserae compile --help`).

## 1. Den Setup-Wizard ausführen

Im Projekt, das du indexieren möchtest:

```bash
cd /path/to/my-project
tesserae init
```

Der Wizard erkennt gängige source wie `README.md`, `docs`, `src`, `lib`, `app`, `packages` und `data` und schreibt dann `.tesserae/config.json`. Er konfiguriert außerdem das Standard-Cognee-backend, damit `tesserae ask` Cognee versuchen und auf die kompilierte wiki-Suche zurückfallen kann.

Für ein nicht-interaktives Setup (CI, Skripte) übergib `--yes`, um die erkannten Standardwerte ohne Nachfrage zu übernehmen:

```bash
tesserae init --yes
```

Für ein vollständig automatisches Setup mit aktiviertem Understand Anything und Cognee runtime memory:

```bash
tesserae init \
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

Was das bewirkt:

| Flag | Effect |
|---|---|
| `--with-understand-anything` | Fügt die UA graph projection als source hinzu. |
| `--install-understand-anything` | Installiert/aktualisiert die UA companion skills. |
| `--understand-anything-platform codex` | Nutzt Codex, um Tesserae's verwalteten UA refresh wrapper auszuführen. |
| `--with-raganything` | Aktiviert multimodales ingestion über RAG-Anything. |
| `--install-raganything` | Installiert raganything[all] während des Setups. |
| `--raganything-parser` | Parser-Wahl: mineru (Standard), docling, paddleocr. |
| `--run-raganything` | Aktualisiert RAG-Anything automatisch bei jedem compile. |
| `--run-cognee` | Führt während des compile einen best-effort Cognee runtime cognify aus. |
| `--install-cognee` | Installiert Cognee mit dem aktuellen Python, falls es fehlt. |

Benutzer müssen weder den UA-Installationspfad kennen noch `/understand` tippen; wenn der UA graph fehlt oder veraltet ist, führt `tesserae compile` `tesserae integrations refresh understand-anything` aus.

> **Den Wizard überspringen.** `tesserae init --bare` schreibt eine minimale `.tesserae/config.json` ohne source-Erkennung oder backend-Prüfung — praktisch, wenn du das config vor dem ersten compile von Hand bearbeiten willst.

## 2. Den Graph und die Projektionen kompilieren

```bash
tesserae compile
```

`compile` schreibt die dauerhaften Artefakte:

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

Nach dem ersten Lauf verwende `--changed-only`, um unveränderte markdown-Dateien zu überspringen und den vorherigen Graph zu erhalten, wenn sich keine Datei geändert hat. Ist Understand Anything aktiviert, refresh/materialize compile zuerst `.tesserae/external/understand-anything.md`; ist Cognee runtime aktiviert, aktualisiert es nach dem Schreiben von `.tesserae/cognee_bundle/` außerdem Cognee als best-effort.

Um zusätzliche Pfade ad-hoc zu ingesten, ohne die konfigurierten source anzufassen, übergib sie positional: `tesserae compile path/to/extra.md docs/`.

### Integrationsschalter leben jetzt im config

`tesserae compile` ist bewusst auf die alltäglichen Flags beschränkt (positionale
paths plus `--project`, `--changed-only`, `--limit`, `--refresh-integrations`,
`--sessions`/`--no-sessions` und die drei LLM-Flags). Alle anderen früheren
compile-Flags sind in einen `compile_options`-Block in `.tesserae/config.json`
gewandert; der alte argparse-Standard bleibt der fallback. Setze dort einen Schlüssel,
um das Verhalten zu ändern:

| `compile_options`-Schlüssel | Altes Flag | Standard | Was es tut |
|---|---|---|---|
| `source_kind` | `--source-kind` | (keiner) | Überschreibt den konfigurierten source kind. |
| `trends` | `--trends` | `false` | Fügt Trend-Knoten auf Korpusebene hinzu. |
| `min_trend_sources` | `--min-trend-sources` | `2` | Minimale source-Zahl für einen Trend-Knoten. |
| `exclude_data` | `--exclude-data` | `false` | Überspringt die implizite Auto-Einbindung von `project_root/data`. |
| `no_vault_pull` | `--no-vault-pull` | `false` | Holt bestehende vault-Bearbeitungen vor dem compile nicht erneut per pull. |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | Speist frühere extraction-Ergebnisse wieder in den Lauf ein. |
| `sessions_llm` | `--sessions-llm` | (auto) | LLM-Session-Extraktionsmodus (`auto`/`true`/`false`). |
| `sessions_model` | `--sessions-model` | (keiner) | Überschreibt das LLM-Modell für die Session-Extraktion. |
| `cognee_add` | `--cognee-add` | `false` | Fügt das Cognee bundle dem dataset hinzu (kein cognify). |
| `cognee_cognify` | `--cognee-cognify` | `false` | Fügt das bundle hinzu und führt Cognee cognify aus. |
| `cognee_codex_cognify` | `--cognee-codex-cognify` | `false` | Führt cognify mit dem auf Codex gepatchten LLM client von Cognee aus. |
| `cognee_codex_model` | `--cognee-codex-model` | `gpt-5.4` | Codex-CLI-Modell für `cognee_codex_cognify`. |
| `cognee_codex_timeout` | `--cognee-codex-timeout` | `300` | Timeout pro Codex-CLI-Aufruf (Sekunden). |
| `cognee_dataset` | `--cognee-dataset` | `tesserae_research_graph` | Name des Cognee dataset. |
| `cognee_embedding_provider` | `--cognee-embedding-provider` | `deterministic` | Embedding provider für die Cognee-lane. |
| `cognee_ollama_embedding_model` | `--cognee-ollama-embedding-model` | `qwen3-embedding:0.6b` | Ollama-Embedding-Modell. |
| `cognee_ollama_embedding_endpoint` | `--cognee-ollama-embedding-endpoint` | `http://127.0.0.1:11434/api/embed` | Ollama-`/api/embed`-Endpoint. |
| `cognee_ollama_embedding_timeout` | `--cognee-ollama-embedding-timeout` | `120` | Timeout der Ollama-Embedding-Anfrage (Sekunden). |
| `cognee_local_embedding_dimensions` | `--cognee-local-embedding-dimensions` | `128` | Dimensionalität des lokalen Embeddings. |
| `cognee_system_root` | `--cognee-system-root` | (keiner) | Isoliertes Cognee-system-root-Verzeichnis. |
| `cognee_data_root` | `--cognee-data-root` | (keiner) | Isoliertes Cognee-data-root-Verzeichnis. |

> **Pipeline in einem Zug.** `tesserae refresh` führt die ganze Schleife in-process aus — es importiert alle neuen agent-Sessions, kompiliert und synchronisiert das vault in einem einzigen Befehl. Übergib `--changed-only` für den optionalen inkrementellen compile.

## 3. Das statische Frontend bauen und bereitstellen

`serve` baut das site automatisch, wenn es fehlt, sodass ein einziger Befehl dir ein browserbares Tesserae liefert:

```bash
tesserae serve --port 8765
```

Öffne:

```text
http://127.0.0.1:8765/
```

Um das site explizit zu bauen (z. B. für ein deploy ohne Bereitstellung), nutze `export site`; übergib `--no-build` an `serve`, wenn du ein zuvor gebautes site durchstöbern willst, ohne es neu zu bauen:

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### Automatischer Neubau beim Speichern

Kopple den Dev-Server mit dem eingebauten watcher, damit Bearbeitungen unter `data/` und `docs/` einen inkrementellen recompile auslösen:

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` pollt alle 2 s, debounct 1 s und führt `compile --changed-only` aus. Verwende `--once` für cron-artige Neubauten (Snapshots gegen `.tesserae/.watch-cache.json`), `--paths <dir>`, um eigene Überwachungsverzeichnisse hinzuzufügen, und `--interval` / `--debounce`, um die Taktung anzupassen.
<!-- END: subagent-r-watch -->

### Den refresh-Daemon ausführen

Wenn du eine dauerhaft laufende Engine willst, die die Wissensbasis von selbst frisch hält — deine source überwacht, Bearbeitungs-Bursts zusammenfasst und automatisch recompile — starte den überwachten Daemon:

```bash
tesserae engine
```

`engine` ist der langlebige supervisor: Er pollt alle 2 s und wartet vor jedem Neubau ein Ruhefenster von 1 s ab. Stelle die Taktung mit `--interval` und `--debounce` ein, richte ihn mit `--project` auf ein anderes Projekt, oder übergib `--once`, um einen einzigen deterministischen drain-Zyklus auszuführen und zu beenden (nützlich für cron oder CI). Das ist das händefreie Gegenstück zu `export site --watch`: Lass ihn laufen, und Graph, vault und site bleiben aktuell, während du und deine Agenten arbeiten.

Für eine kommentierte Tour durch jede sichtbare Route — home, sources, concepts, entities, papers, repos, topics, syntheses, questions, timeline, graph sowie die AI siblings — siehe [`docs/frontend-redesign.md`](frontend-redesign.de.md).

Das Frontend ist abhängigkeitsarm und schreibt:

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. Lokale agent-Session-Historie importieren

Der Import der Session-Historie ist explizit: normales compile/build liest bereits normalisierte Sessions, scannt aber nicht von selbst die privaten Transkript-Stores von Claude Code oder Codex.

```bash
# Preview matching Claude Code/Codex sessions for this project:
tesserae sessions discover

# Normalize and store them under .tesserae/harness_sessions/:
tesserae sessions discover --import

# Confirm the imported set:
tesserae sessions list

# Rebuild so sessions/index.html and session detail pages are emitted:
tesserae export site
```

Importierte Sessions erscheinen im globalen Sessions-Bereich, in der site-Suche und in den Browse-Karten der Startseite. Session-Detailseiten rendern user/assistant-Turns als lesbares markdown, hängen tool-use-Blöcke unter den vorhergehenden assistant-Turn und bieten eine linke Turn-Leiste für die `#turn-N`-Navigation. Für Datenschutzhinweise, Importformate und die aktuelle Transkript-Typografie-Karte siehe [`docs/session-history.md`](session-history.de.md).

## 5. Das wiki linten

```bash
tesserae lint
```

Durchläuft den kompilierten Graph + wiki + site und markiert orphan papers, stale citations, drift zwischen Graph und wiki/, ghost synthesis inputs und mehr. Schreibt `.tesserae/lint-report.md` und `.tesserae/lint-report.json`. Übergib `--fix-trivial`, um sichere Auto-Korrekturen anzuwenden (fehlende `implemented_in`-edges, ghost-input-Beschneidung), und `--severity error`, damit der Exit-Code nur bei Fehlern fehlschlägt.

## 6. Das wiki abfragen

```bash
tesserae query "What is Gaussian Splatting?"
```

Standardmäßig nur Suche — BM25 über `.tesserae/site/search-index.json`, mit einem 200-Zeichen-Auszug aus dem passenden `wiki/<kind>/<slug>.md`. Übergib `--kind papers` (oder `concepts`, `repos` usw.) zum Einschränken, `--top-k N` zum Erweitern und `--json` für strukturierte Ausgabe. Füge `--llm` hinzu (oder setze `TESSERAE_QUERY_LLM=1`), um Claude um eine synthetisierte Antwort mit `[node_id]`-Zitaten zu bitten; `--interactive` öffnet eine readline-REPL — leere Zeile oder EOF beendet. `TESSERAE_QUERY_DRY_RUN=1` übt den Prompt ohne API-Aufruf.

## 7. Agentenfertigen context auf Abruf kompilieren

Das Highlight von v0.5.0 ist der On-Demand Context Compiler: Bitte den kompilierten Graph um ein einziges zitiertes context-Dokument, das auf eine Anfrage zugeschnitten und auf das Fenster eines Agenten dimensioniert ist.

```bash
tesserae context "How does session import work?"
```

Es seedet Personalized PageRank von den Knoten, die zu deiner Anfrage passen (verwende `--seeds <node_id>`, um explizit zu seeden), erweitert die Nachbarschaft (`--depth`, Standard 2) und stellt ein zitiertes Dokument zusammen, das durch ein Zeichen-`--budget` begrenzt ist (Standard 32000; übergib `<= 0` für unbegrenzt). Füge `--synthesize` für eine LLM-geschriebene Zusammenfassung obendrauf hinzu (erfordert ein LLM backend) und `-o/--output <file>`, um das Dokument auf Platte statt nach stdout zu schreiben.

Derselbe compiler wird Agenten über MCP als das Tool `compile_context` zur Verfügung gestellt, sodass ein Coding-Agent mitten im Gespräch genau so viel budget-begrenzten Projekt-context ziehen kann, wie nötig — ohne manuellen export.

## 8. Agent-harness-Dateien exportieren

```bash
tesserae export harness
```

Unterstützte Targets:

- Claude Code
- Codex
- Gemini
- Kiro
- Cursor
- OpenCode

Beispiel für eine Teilmenge:

```bash
tesserae export harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. Ein Obsidian-vault exportieren

```bash
tesserae vault export
```

Oder in ein bestehendes vault schreiben:

```bash
tesserae vault export --vault "$OBSIDIAN_VAULT_PATH"
```

Das vault enthält markdown projections, `.obsidian` defaults, Graph-Färbung, `raw/assets/` und ein Dataview dashboard. Verwende `tesserae vault sync`, um ein bestehendes vault mit dem letzten compile abzugleichen (füge `--prune` hinzu, um verwaiste Notizen zu entfernen).

## 10. MCP konfigurieren

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

Füge die Ausgabe unter `mcp_servers` in `~/.hermes/config.yaml` ein und starte dann Hermes/gateway neu.

## 11. Graphiti-export / sync

Abhängigkeitsfreier Episoden-export:

```bash
tesserae export graphiti
```

Dry-run-sync-Smoke ohne installiertes Graphiti:

```bash
tesserae export graphiti --sync --dry-run
```

Live-sync erfordert `graphiti_core` und ein erreichbares Neo4j backend:

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. Auf GitHub Pages deployen

Pushe das kompilierte site in `.tesserae/site/` in den `gh-pages`-Branch des git origin des Projekts:

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` führt zuerst `compile` aus, damit das site frisch ist. `--enable-pages` schaltet Pages über die `gh`-CLI ein (idempotent; wird mit einem Hinweis übersprungen, wenn `gh` fehlt). Verwende `--dry-run`, um zu stagen und zu committen ohne zu pushen, `--branch` / `--remote`, um Standardwerte zu überschreiben, und `--force`, um ein deploy mit unsauberem Arbeitsbaum zu erlauben.

Das Site wird unter `https://<owner>.github.io/<repo>/` erreichbar.

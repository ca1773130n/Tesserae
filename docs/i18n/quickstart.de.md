# Quickstart

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a></p>
<!-- translations:end -->
Diese Seite zeigt den kürzesten Pfad von einem existierenden Projektverzeichnis zu einem browsbaren Tesserae.

## Befehlsübersicht

Die CLI ist gruppiert: eine Handvoll alltäglicher Verben auf oberster Ebene, plus Gruppen
(`sessions`, `vault`, `export`, `code`, `config`, `projects`, `agents`, `domains`, `integrations`,
`lab`) für den Rest. Führe `tesserae --help` aus, um den ganzen Baum zu sehen:

```text
tesserae 0.30.0 — a context engine

usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  ingest        Ingest a document file or URL into the knowledge base
  context       Compile agent-ready context for a query
  ask           LLM answer over the knowledge graph (planned retrieval)
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles, idle 'sleep' consolidation
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query
  distill       Per-agent L1 expertise artifacts (opt-in: TESSERAE_AGENT_DISTILL)

ANALYSIS
  query         raw retrieval: BM25/semantic + explicit backends
  graph-map     Budgeted Descent navigation (the graph_map tool as a CLI verb; JSON out)
  verify-claim  Does the graph license this triple? Deterministic verdict, JSON out
  lint          Graph lint report (--fix-trivial, --severity, --json)
  doctor        Health checks: init/graph/registry/staleness/locks (--fix = safe repairs only)
  summary       Daily/weekly activity digest (sessions, findings, commits, PRs, docs)
  decisions     Decisions across projects + time (human AskUserQuestion + agent)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  setup         Machine-wide setup: LLM defaults + optional deps (interactive by default)
  config        llm | deps | show | status | clip-token — LLM backend defaults + resolved view & liveness ping
  projects      register | list | unregister | mcp-config — registry
  agents        init | list | tree | show | drill | set-parent | rename — role-grade agent org registry
  domains       status — chartered domain tree (divisions/departments/teams)
  sources       add | list | remove — manage compile source dirs (local & global)
  federation    status | explain — inspect cross-project federation
  integrations  refresh raganything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve | schema-drift — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

Führe `tesserae <command> --help` aus (z. B. `tesserae compile --help`), um die Flags
eines einzelnen Befehls zu sehen.

## 1. Den Setup-Wizard ausführen

Aus dem Projekt, das du indexieren willst:

```bash
cd /path/to/my-project
tesserae init
```

`tesserae init` ist der einzige Onboarding-Schritt. Der Wizard erkennt gängige Quellen wie `README.md`, `docs`, `src`, `lib`, `app`, `packages` und `data`, prüft, welche LLM-CLIs installiert **und eingeloggt** sind, lässt dich den LLM-Provider wählen und schreibt `.tesserae/config.json`. Das optionale RAG-Anything-Memory-Backend ist **standardmäßig aus**; aktiviere es später unter `memory_backends` in der Config und frage es explizit mit `tesserae query --backend raganything` ab.

Für ein nicht-interaktives Setup (CI, Skripte) übergib `--yes`, um die erkannten
Defaults ohne Nachfragen zu akzeptieren (alle optionalen Integrationen AUS):

```bash
tesserae init --yes
```

### LLM-Provider-Konfiguration

Die Provider-Wahl des Wizards (oder die äquivalenten Flags) persistiert diese Config-Keys:

| Config-Key | Flag | Was es ist |
|---|---|---|
| `llm_provider` | `--llm-provider {claude,codex,anthropic,custom}` | Backend für den LLM-Client: `claude`/`codex` nutzen die eingeloggte CLI über OAuth; `anthropic` nutzt die API direkt; `custom` zielt auf einen beliebigen claude-kompatiblen Endpunkt. |
| `llm_model` | `--llm-model` | Modell für den Synthesis-/Insights-LLM-Client. |
| `llm_base_url` | `--llm-base-url` | Endpunkt-Basis-URL für `anthropic`/`custom`. |
| `llm_api_key` | `--llm-api-key` | API-Key für `anthropic`/`custom`. |

> **Klartext-Warnung.** `llm_api_key` wird in **Klartext** in
> `.tesserae/config.json` gespeichert. Bevorzuge stattdessen die Umgebungsvariablen:
> `ANTHROPIC_API_KEY` (Key), `ANTHROPIC_BASE_URL` (Endpunkt) und
> `TESSERAE_LLM_MODEL` (Modell). Auflösungsreihenfolge ist env → Projekt-Config →
> maschinenweite Config (`~/.tesserae/config.json`, geschrieben von `tesserae setup`)
> → eingebauter Default.

Ein erneutes `init` auf einem existierenden Projekt **merged** — deine konfigurierten `sources`
und `memory_backends` bleiben erhalten und werden nicht überschrieben.

Beispiele für nicht-interaktive Provider-Setups:

```bash
tesserae init --yes --llm-provider codex
tesserae init --yes --llm-provider custom \
  --llm-base-url https://llm.internal.example/v1 \
  --llm-model my-model            # key via ANTHROPIC_API_KEY
```

> **Den Wizard überspringen.** `tesserae init --bare` schreibt eine minimale `.tesserae/config.json`
> ohne Quellenerkennung oder Backend-Probing — praktisch, wenn du die Config vor dem
> ersten Compile von Hand editieren willst.

## 2. Graph und Projektionen kompilieren

```bash
tesserae compile
```

`compile` schreibt die langlebigen Artefakte:

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
```

Nutze `--changed-only` nach dem ersten Lauf, um unveränderte Markdown-Dateien zu überspringen und dabei den vorherigen Graph zu bewahren, wenn sich keine Dateien geändert haben.

Um zusätzliche Pfade ad-hoc zu ingesten, ohne die konfigurierten Quellen anzufassen,
übergib sie positional: `tesserae compile path/to/extra.md docs/`.

### Integrations-Stellschrauben leben jetzt in der Config

`tesserae compile` ist bewusst auf die Alltags-Flags begrenzt (Pfade positional
plus `--project`, `--changed-only`, `--limit`, `--refresh-integrations`,
`--sessions`/`--no-sessions` und die drei LLM-Flags). Jedes andere frühere Compile-Flag
ist in einen `compile_options`-Block in `.tesserae/config.json` gewandert; der alte
argparse-Default bleibt der Fallback. Setze dort einen Key, um das Verhalten zu ändern:

| `compile_options`-Key | Altes Flag | Default | Was er tut |
|---|---|---|---|
| `source_kind` | `--source-kind` | (keiner) | Überschreibt den konfigurierten Source-Kind. |
| `trends` | `--trends` | `false` | Fügt korpusweite Trend-Knoten hinzu. |
| `min_trend_sources` | `--min-trend-sources` | `2` | Minimale Quellenzahl für einen Trend-Knoten. |
| `exclude_data` | `--exclude-data` | `false` | Überspringt das implizite `project_root/data`-Auto-Include. |
| `no_vault_pull` | `--no-vault-pull` | `false` | Zieht existierende Vault-Edits vor dem Compile nicht zurück. |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | Speist frühere Extraktionsergebnisse in den Lauf zurück. |
| `sessions_llm` | `--sessions-llm` | (auto) | LLM-Session-Extraktionsmodus (`auto`/`true`/`false`). |
| `sessions_model` | `--sessions-model` | (keiner) | Überschreibt das für die Session-Extraktion genutzte LLM-Modell. |

> **Cognee wurde in 0.19 entfernt.** Das cognee-Backend wurde in 0.18 zurückgestuft
> und hat den Graph nie gespeist. Configs, die noch eine `memory_backends.cognee`-Sektion
> (oder `cognee_*`-Compile-Optionen) tragen, laden weiterhin — die Sektion wird
> mit einem einzeiligen Hinweis ignoriert.

> **One-Shot-Pipeline.** `tesserae refresh` führt die ganze Schleife in-process aus — es importiert neue Agent-Sessions, kompiliert und synchronisiert den Vault in einem einzigen Befehl. Übergib `--changed-only` für den Opt-in-inkrementellen Compile.

## 3. Das statische Frontend bauen und serven

`serve` baut die Site automatisch, wenn sie fehlt, sodass ein einziger Befehl dir ein
browsbares Tesserae liefert. **Nacktes `serve` served jedes registrierte Projekt**
unter einem Server — eine Projects-Landing unter `/`, jedes Projekt unter `/<alias>/`
und einen Projects-Switcher im Header, um zwischen ihnen zu springen. Das In-Page-**Ask-Widget
funktioniert live in beiden Modi**, geroutet auf das Projekt der Seite, auf der du bist:

```bash
tesserae serve --port 8765                 # all registered projects
tesserae serve --project . --port 8765     # just this one
```

Öffne:

```text
http://127.0.0.1:8765/
```

Um die Site explizit zu bauen (z. B. für ein Deploy ohne Serving), nutze `export site`;
übergib `--no-build` an `serve`, wenn du eine zuvor gebaute Site browsen willst, ohne
sie neu zu bauen:

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### Auto-Rebuild beim Speichern

Kombiniere den Dev-Server mit dem eingebauten Watcher, damit Edits unter `data/` und `docs/` einen inkrementellen Recompile auslösen:

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` pollt alle 2 s, debounced 1 s und führt `compile --changed-only` aus. Nutze `--once` für Cron-artige Rebuilds (Snapshots vs. `.tesserae/.watch-cache.json`), `--paths <dir>` für zusätzliche Watch-Verzeichnisse und `--interval` / `--debounce` zum Tunen der Kadenz.
<!-- END: subagent-r-watch -->

### Den Refresh-Daemon ausführen

Für eine dauerhaft laufende Engine, die die Wissensbasis eigenständig frisch hält — deine Quellen beobachtet, Edit-Bursts zusammenfasst und automatisch rekompiliert — starte den überwachten Daemon:

```bash
tesserae engine
```

`engine` ist der langlaufende Supervisor: er pollt alle 2 s und wartet vor jedem Rebuild ein 1-s-Ruhefenster ab. Tune die Kadenz mit `--interval` und `--debounce`, richte ihn mit `--project` auf ein anderes Projekt oder übergib `--once` für einen einzelnen deterministischen Drain-Zyklus mit anschließendem Exit (nützlich für Cron oder CI). Das ist das Hands-off-Gegenstück zu `export site --watch`: lass ihn laufen und Graph, Vault und Site bleiben aktuell, während du und deine Agenten arbeiten.

Für eine kommentierte Tour über jede sichtbare Route — Home, Sources, Concepts, Entities, Papers, Repos, Topics, Syntheses, Questions, Timeline, Graph, plus die KI-Geschwister — siehe [`docs/frontend-redesign.md`](frontend-redesign.de.md).

Das Frontend ist abhängigkeitsarm und schreibt:

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. Lokale Agent-Session-Historie importieren

Der Import von Session-Historie ist explizit: normales compile/build liest bereits normalisierte Sessions, scannt aber nicht von sich aus private Claude-Code- oder Codex-Transkript-Stores.

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

Importierte Sessions erscheinen im globalen Sessions-Bereich, in der Site-Suche und in den Home-Browse-Karten. Session-Detailseiten rendern User-/Assistant-Turns als lesbares Markdown, hängen Tool-Use-Blöcke unter den vorangehenden Assistant-Turn und bieten eine linke Turn-Leiste für `#turn-N`-Navigation. Siehe [`docs/session-history.md`](session-history.de.md) für Privacy-Hinweise, Import-Formate und die aktuelle Transkript-Typografie-Map.

## 5. Das Wiki linten

```bash
tesserae lint
```

Läuft über den kompilierten Graph + Wiki + Site und markiert verwaiste Paper, veraltete Zitate, Drift zwischen Graph und wiki/, Geister-Synthesis-Inputs und mehr. Schreibt `.tesserae/lint-report.md` und `.tesserae/lint-report.json`. Übergib `--fix-trivial`, um sichere Auto-Fixes anzuwenden (fehlende `implemented_in`-Kanten, Geister-Input-Pruning), und `--severity error`, um den Exit-Code nur bei Fehlern fehlschlagen zu lassen.

Für Workspace-Gesundheit jenseits des Graphen selbst — Registry-Konsistenz, Staleness, Locks, LLM-Login, Hygiene — führe `tesserae doctor` aus (`--fix` wendet nur die sicheren Reparaturen an). Siehe [`docs/doctor.md`](doctor.de.md).

## 6. Das Wiki fragen und abfragen

```bash
# LLM-planned, cited answer over the compiled graph (the default):
tesserae ask "What is Gaussian Splatting?"

# Raw retrieval — ranked search hits, no LLM:
tesserae query "What is Gaussian Splatting?"
```

`ask` ist die Antwort-Oberfläche: Das Modell plant Retrieval über den kompilierten Graph und synthetisiert dann eine zitierte Antwort. Es funktioniert mit einer eingeloggten `claude`/`codex`-CLI (OAuth) oder `ANTHROPIC_API_KEY`; übergib `--no-llm` für nur gerankte Suchtreffer (dieses Force-off schlägt `TESSERAE_QUERY_LLM=1`). `TESSERAE_QUERY_DRY_RUN=1` exerziert den Prompt ohne API-Aufruf.

`query` ist die Retrieval-Oberfläche: BM25-/semantische Suche über `.tesserae/site/search-index.json`, mit einem 200-Zeichen-Exzerpt aus der passenden `wiki/<kind>/<slug>.md`. Übergib `--kind papers` (oder `concepts`, `repos` etc.) zum Eingrenzen, `--top-k N` zum Verbreitern und `--json` für strukturierte Ausgabe; `--interactive` öffnet eine Readline-REPL — Leerzeile oder EOF beendet. Das explizite Memory-Backend lebt ebenfalls hier: `--backend raganything` schaltet direkt auf dieses Backend durch und zeigt dessen Fehler an. Es gibt keine LLM-Synthese auf `query` — dafür ist `ask` da.

## 7. Agent-fertigen Kontext auf Abruf kompilieren

Das Aushängeschild von v0.5.0 ist der On-Demand Context Compiler: Frag den kompilierten Graph nach einem einzelnen, zitierten Kontextdokument, das auf eine Query zugeschnitten und auf das Fenster eines Agenten dimensioniert ist.

```bash
tesserae context "How does session import work?"
```

Er seedet Personalized PageRank aus den zu deiner Query passenden Knoten (nutze `--seeds <node_id>` für explizites Seeding), expandiert die Nachbarschaft (`--depth`, Default 2) und assembliert ein zitiertes Dokument, begrenzt auf ein Zeichen-`--budget` (Default 32000; übergib `<= 0` für unbegrenzt). Füge `--llm` für eine LLM-geschriebene Zusammenfassung obendrauf hinzu (erfordert ein LLM-Backend) und `-o/--output <file>`, um das Dokument auf Platte statt auf stdout zu schreiben.

Derselbe Compiler ist Agenten über MCP als `compile_context`-Tool zugänglich, sodass ein Coding-Agent mitten im Gespräch genau-genug, budget-begrenzten Projektkontext ziehen kann, ohne manuellen Export.

## 8. Agent-Harness-Dateien exportieren

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

Beispiel-Teilmenge:

```bash
tesserae export harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. Einen Obsidian-Vault exportieren

```bash
tesserae vault export
```

Oder in einen existierenden Vault schreiben:

```bash
tesserae vault export --output "$OBSIDIAN_VAULT_PATH"
```

Der Vault enthält Markdown-Projektionen, `.obsidian`-Defaults, Graph-Färbung, `raw/assets/` und ein Dataview-Dashboard. Nutze `tesserae vault sync`, um einen existierenden Vault mit dem letzten Compile abzugleichen (füge `--prune` hinzu, um verwaiste Notizen zu entfernen).

## 10. MCP konfigurieren

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

Füge die Ausgabe unter `mcp_servers` in `~/.hermes/config.yaml` ein und starte dann Hermes/Gateway neu.

## 11. Graphiti-Export / -Sync

Abhängigkeitsfreier Episoden-Export:

```bash
tesserae export graphiti
```

Dry-Run-Sync-Smoke ohne installiertes Graphiti:

```bash
tesserae export graphiti --sync --dry-run
```

Live-Sync erfordert `graphiti_core` und ein erreichbares Neo4j-Backend:

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. Auf GitHub Pages deployen

Pushe die kompilierte Site unter `.tesserae/site/` auf den `gh-pages`-Branch des Git-Origins des Projekts:

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` führt zuerst `compile` aus, damit die Site frisch ist. `--enable-pages` schaltet Pages über die `gh`-CLI ein (idempotent; wird mit einem Hinweis übersprungen, wenn `gh` fehlt). Nutze `--dry-run`, um zu stagen und zu committen ohne zu pushen, `--branch` / `--remote`, um Defaults zu überschreiben, und `--force`, um ein Deploy mit unsauberem Arbeitsbaum zu erlauben.

Die Site wird unter `https://<owner>.github.io/<repo>/` erreichbar.

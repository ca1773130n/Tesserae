# Quickstart

<!-- translations:start -->
<p align="center"><a href="i18n/quickstart.ko.md">한국어</a> · <a href="i18n/quickstart.zh.md">中文</a> · <a href="i18n/quickstart.ja.md">日本語</a> · <a href="i18n/quickstart.ru.md">Русский</a> · <a href="i18n/quickstart.es.md">Español</a> · <a href="i18n/quickstart.fr.md">Français</a> · <a href="../i18n/quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
This page shows the shortest path from an existing project directory to a browsable Tesserae.

## Command overview

The CLI is grouped: a handful of everyday verbs at the top level, plus groups
(`sessions`, `vault`, `export`, `code`, `config`, `projects`, `integrations`,
`lab`) for the rest. Run `tesserae --help` to see the whole tree:

```text
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
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query

ANALYSIS
  query         raw retrieval: BM25/semantic + explicit backends
  lint          Graph lint report (--fix-trivial, --severity, --json)
  doctor        Health checks: init/graph/registry/staleness/locks (--fix = safe repairs only)
  summary       Daily/weekly activity digest (sessions, findings, commits, PRs, docs)
  decisions     Decisions across projects + time (human AskUserQuestion + agent)

GROUPS
  sessions      import | discover | list | chunk-backfill — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  setup         Machine-wide setup: LLM defaults + optional deps (interactive by default)
  config        llm | deps | show | status | clip-token — LLM backend defaults + resolved view & liveness ping
  projects      register | list | unregister | mcp-config — registry
  sources       add | list | remove — manage compile source dirs (local & global)
  federation    status | explain — inspect cross-project federation
  integrations  refresh raganything|understand-anything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve | schema-drift — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

Run `tesserae <command> --help` (e.g. `tesserae compile --help`) for the flags on
any single command.

## 1. Run the setup wizard

From the project you want to index:

```bash
cd /path/to/my-project
tesserae init
```

`tesserae init` is the single onboarding step. The wizard detects common sources such as `README.md`, `docs`, `src`, `lib`, `app`, `packages`, and `data`, probes which LLM CLIs are installed **and logged in**, lets you pick the LLM provider, and writes `.tesserae/config.json`. Optional memory backends (RAG-Anything, Cognee) are **off by default**; enable them later in `memory_backends` in the config, and query them explicitly with `tesserae query --backend …`.

For a non-interactive setup (CI, scripts), pass `--yes` to accept the detected
defaults without prompting (all optional integrations OFF):

```bash
tesserae init --yes
```

### LLM provider configuration

The wizard's provider pick (or the equivalent flags) persists these config keys:

| Config key | Flag | What it is |
|---|---|---|
| `llm_provider` | `--llm-provider {claude,codex,anthropic,custom}` | Backend for the LLM client: `claude`/`codex` use the logged-in CLI over OAuth; `anthropic` uses the API directly; `custom` targets any claude-compatible endpoint. |
| `llm_model` | `--llm-model` | Model for the synthesis/insights LLM client. |
| `llm_base_url` | `--llm-base-url` | Endpoint base URL for `anthropic`/`custom`. |
| `llm_api_key` | `--llm-api-key` | API key for `anthropic`/`custom`. |

> **Plaintext warning.** `llm_api_key` is stored in **plaintext** in
> `.tesserae/config.json`. Prefer the environment variables instead:
> `ANTHROPIC_API_KEY` (key), `ANTHROPIC_BASE_URL` (endpoint), and
> `TESSERAE_LLM_MODEL` (model). Resolution order is env → project config →
> machine-wide config (`~/.tesserae/config.json`, written by `tesserae setup`)
> → built-in default.

Re-running `init` on an existing project **merges** — your configured `sources`
and `memory_backends` are preserved, not clobbered.

Example non-interactive provider setups:

```bash
tesserae init --yes --llm-provider codex
tesserae init --yes --llm-provider custom \
  --llm-base-url https://llm.internal.example/v1 \
  --llm-model my-model            # key via ANTHROPIC_API_KEY
```

If you enable Understand Anything with `auto_refresh: true` in its `external_tools` entry (off by default — its refresh runs a remote install script), `tesserae compile` runs `tesserae integrations refresh understand-anything` when the UA graph is missing or stale; otherwise run that command yourself.

> **Skip the wizard.** `tesserae init --bare` writes a minimal `.tesserae/config.json`
> without source detection or backend probing — handy when you want to hand-edit
> the config before the first compile.

## 2. Compile the graph and projections

```bash
tesserae compile
```

`compile` writes the durable artifacts:

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

Use `--changed-only` after the first run to skip unchanged markdown files while preserving the previous graph when no files changed. If Understand Anything is enabled, compile first refreshes/materializes `.tesserae/external/understand-anything.md`; if Cognee runtime is enabled, it also updates Cognee best-effort after writing `.tesserae/cognee_bundle/`.

To ingest extra paths ad-hoc without touching the configured sources, pass them
positionally: `tesserae compile path/to/extra.md docs/`.

### Integration knobs live in config now

`tesserae compile` is deliberately capped at the everyday flags (paths positional
plus `--project`, `--changed-only`, `--limit`, `--refresh-integrations`,
`--sessions`/`--no-sessions`, and the three LLM flags). Every other former compile
flag moved into a `compile_options` block in `.tesserae/config.json`; the old
argparse default is still the fallback. Set a key there to change behavior:

| `compile_options` key | Old flag | Default | What it does |
|---|---|---|---|
| `source_kind` | `--source-kind` | (none) | Override the configured source kind. |
| `trends` | `--trends` | `false` | Add corpus-level Trend nodes. |
| `min_trend_sources` | `--min-trend-sources` | `2` | Minimum sources needed for a Trend node. |
| `exclude_data` | `--exclude-data` | `false` | Skip the implicit `project_root/data` auto-include. |
| `no_vault_pull` | `--no-vault-pull` | `false` | Don't pull existing vault edits back before compile. |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | Feed prior extraction results back into the run. |
| `sessions_llm` | `--sessions-llm` | (auto) | LLM session-extraction mode (`auto`/`true`/`false`). |
| `sessions_model` | `--sessions-model` | (none) | Override the LLM model used for session extraction. |
| `cognee_add` | `--cognee-add` | `false` | Add the Cognee bundle to the dataset (no cognify). |
| `cognee_cognify` | `--cognee-cognify` | `false` | Add the bundle and run Cognee cognify. |
| `cognee_dataset` | `--cognee-dataset` | `tesserae_research_graph` | Cognee dataset name. |
| `cognee_embedding_provider` | `--cognee-embedding-provider` | `deterministic` | Embedding provider for the Cognee lane. |
| `cognee_ollama_embedding_model` | `--cognee-ollama-embedding-model` | `qwen3-embedding:0.6b` | Ollama embedding model. |
| `cognee_ollama_embedding_endpoint` | `--cognee-ollama-embedding-endpoint` | `http://127.0.0.1:11434/api/embed` | Ollama `/api/embed` endpoint. |
| `cognee_ollama_embedding_timeout` | `--cognee-ollama-embedding-timeout` | `120` | Ollama embedding request timeout (seconds). |
| `cognee_local_embedding_dimensions` | `--cognee-local-embedding-dimensions` | `128` | Local embedding dimensionality. |
| `cognee_system_root` | `--cognee-system-root` | (none) | Isolated Cognee system root directory. |
| `cognee_data_root` | `--cognee-data-root` | (none) | Isolated Cognee data root directory. |

> **Cognee is opt-in.** The Cognee backend is disabled by default: install it
> with `pip install tesserae[cognee]` and set `memory_backends.cognee.enabled: true`
> to use it (queried explicitly via `tesserae query --backend cognee`). The
> legacy Codex-patched cognify mode (`cognee_codex_cognify` /
> `cognee_codex_model` / `cognee_codex_timeout`) was removed — configs still
> carrying those keys are inert.

> **One-shot pipeline.** `tesserae refresh` runs the whole loop in-process — it imports any new agent sessions, compiles, and syncs the vault in a single command. Pass `--changed-only` for the opt-in incremental compile.

## 3. Build and serve the static frontend

`serve` auto-builds the site if it is missing, so a single command gets you a
browsable Tesserae. **Bare `serve` serves every registered project** under one
server — a projects landing at `/`, each project at `/<alias>/`, and a Projects
switcher in the header to jump between them. The in-page **ask widget works live
in either mode**, routed to the project of the page you're on:

```bash
tesserae serve --port 8765                 # all registered projects
tesserae serve --project . --port 8765     # just this one
```

Open:

```text
http://127.0.0.1:8765/
```

To build the site explicitly (e.g. for deploy without serving) use `export site`;
pass `--no-build` to `serve` when you want to browse a previously built site
without rebuilding it:

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### Auto-rebuild on save

Pair the dev server with the built-in watcher so edits under `data/` and `docs/` trigger an incremental recompile:

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` polls every 2 s, debounces 1 s, and runs `compile --changed-only`. Use `--once` for cron-style rebuilds (snapshots vs `.tesserae/.watch-cache.json`), `--paths <dir>` to add custom watch dirs, and `--interval` / `--debounce` to tune cadence.
<!-- END: subagent-r-watch -->

### Run the refresh daemon

For an always-on engine that keeps the knowledge base fresh on its own — watching your sources, coalescing bursts of edits, and auto-recompiling — start the supervised daemon:

```bash
tesserae engine
```

`engine` is the long-running supervisor: it polls every 2 s and waits out a 1 s quiet window before each rebuild. Tune the cadence with `--interval` and `--debounce`, point it at another project with `--project`, or pass `--once` to run a single deterministic drain cycle and exit (useful for cron or CI). This is the hands-off counterpart to `export site --watch`: leave it running and the graph, vault, and site stay current as you and your agents work.

For an annotated tour of every visible route — home, sources, concepts, entities, papers, repos, topics, syntheses, questions, timeline, graph, plus the AI siblings — see [`docs/frontend-redesign.md`](frontend-redesign.md).

The frontend is dependency-light and writes:

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. Import local agent session history

Session history import is explicit: normal compile/build reads already-normalized sessions but does not scan private Claude Code or Codex transcript stores on its own.

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

Imported sessions appear in the global Sessions section, site search, and the home Browse cards. Session detail pages render user/assistant turns as readable markdown, attach tool-use blocks under the preceding assistant turn, and expose a left turn rail for `#turn-N` navigation. See [`docs/session-history.md`](session-history.md) for privacy notes, import formats, and the current transcript typography map.

## 5. Lint the wiki

```bash
tesserae lint
```

Walks the compiled graph + wiki + site and flags orphan papers, stale citations, drift between graph and wiki/, ghost synthesis inputs, and more. Writes `.tesserae/lint-report.md` and `.tesserae/lint-report.json`. Pass `--fix-trivial` to apply safe auto-fixes (missing `implemented_in` edges, ghost-input pruning) and `--severity error` to only fail the exit code on errors.

For workspace health beyond the graph itself — registry consistency, staleness, locks, LLM login, hygiene — run `tesserae doctor` (`--fix` applies the safe repairs only). See [`docs/doctor.md`](doctor.md).

## 6. Ask and query the wiki

```bash
# LLM-planned, cited answer over the compiled graph (the default):
tesserae ask "What is Gaussian Splatting?"

# Raw retrieval — ranked search hits, no LLM:
tesserae query "What is Gaussian Splatting?"
```

`ask` is the answer surface: the model plans retrieval over the compiled graph, then synthesizes a cited answer. It works with a logged-in `claude`/`codex` CLI (OAuth) or `ANTHROPIC_API_KEY`; pass `--no-llm` for ranked search hits only (this force-off beats `TESSERAE_QUERY_LLM=1`). `TESSERAE_QUERY_DRY_RUN=1` exercises the prompt without an API call.

`query` is the retrieval surface: BM25/semantic search over `.tesserae/site/search-index.json`, with a 200-char excerpt pulled from the matching `wiki/<kind>/<slug>.md`. Pass `--kind papers` (or `concepts`, `repos`, etc.) to narrow, `--top-k N` to widen, and `--json` for structured output; `--interactive` opens a readline REPL — blank line or EOF exits. Explicit memory backends live here too: `--backend raganything|cognee` short-circuits to that backend and surfaces its errors (with `--cognee-search-type` / `--cognee-dataset` for the Cognee lane). There is no LLM synthesis on `query` — that's `ask`.

## 7. Compile agent-ready context on demand

The headline of v0.5.0 is the On-Demand Context Compiler: ask the compiled graph for a single, cited context document scoped to a query, sized to fit an agent's window.

```bash
tesserae context "How does session import work?"
```

It seeds Personalized PageRank from the nodes matching your query (use `--seeds <node_id>` to seed explicitly), expands the neighbourhood (`--depth`, default 2), and assembles a cited doc capped at a character `--budget` (default 32000; pass `<= 0` for uncapped). Add `--llm` for an LLM-written summary on top (requires an LLM backend) and `-o/--output <file>` to write the doc to disk instead of stdout.

The same compiler is exposed to agents over MCP as the `compile_context` tool, so a coding agent can pull just-enough, budget-bounded project context mid-conversation without a manual export.

## 8. Export agent harness files

```bash
tesserae export harness
```

Supported targets:

- Claude Code
- Codex
- Gemini
- Kiro
- Cursor
- OpenCode

Example subset:

```bash
tesserae export harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. Export an Obsidian vault

```bash
tesserae vault export
```

Or write into an existing vault:

```bash
tesserae vault export --output "$OBSIDIAN_VAULT_PATH"
```

The vault includes markdown projections, `.obsidian` defaults, graph coloring, `raw/assets/`, and a Dataview dashboard. Use `tesserae vault sync` to reconcile an existing vault with the latest compile (add `--prune` to drop orphaned notes).

## 10. Configure MCP

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

Paste the output under `mcp_servers` in `~/.hermes/config.yaml`, then restart Hermes/gateway.

## 11. Graphiti export / sync

Dependency-free episode export:

```bash
tesserae export graphiti
```

Dry-run sync smoke without Graphiti installed:

```bash
tesserae export graphiti --sync --dry-run
```

Live sync requires `graphiti_core` and a reachable Neo4j backend:

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. Deploy to GitHub Pages

Push the compiled site at `.tesserae/site/` to the `gh-pages` branch of the project's git origin:

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` runs `compile` first so the site is fresh. `--enable-pages` turns Pages on via the `gh` CLI (idempotent; skipped with a hint if `gh` is missing). Use `--dry-run` to stage and commit without pushing, `--branch` / `--remote` to override defaults, and `--force` to allow deploying with a dirty working tree.

The site becomes reachable at `https://<owner>.github.io/<repo>/`.

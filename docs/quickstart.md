# Quickstart

<!-- translations:start -->
<p align="center"><a href="i18n/quickstart.ko.md">한국어</a> · <a href="i18n/quickstart.zh.md">中文</a> · <a href="i18n/quickstart.ja.md">日本語</a> · <a href="i18n/quickstart.ru.md">Русский</a> · <a href="i18n/quickstart.es.md">Español</a> · <a href="i18n/quickstart.fr.md">Français</a></p>
<!-- translations:end -->
This page shows the shortest path from an existing project directory to a browsable LLM-Wiki.

## 1. Run the setup wizard

From the project you want to index:

```bash
cd /path/to/my-project
llm_wiki project setup
```

The wizard detects common sources such as `README.md`, `docs`, `src`, `lib`, `app`, `packages`, and `data`, then writes `.llm-wiki/config.json`. It also configures the default Cognee backend so `project ask` can try Cognee and fall back to compiled wiki search.

For a fully automated setup with Understand Anything and Cognee runtime memory enabled:

```bash
llm_wiki project setup \
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

What that does:

| Flag | Effect |
|---|---|
| `--with-understand-anything` | Adds the UA graph projection as a source. |
| `--install-understand-anything` | Installs/updates the UA companion skills. |
| `--understand-anything-platform codex` | Uses Codex to run LLM-Wiki's managed UA refresh wrapper. |
| `--with-raganything` | Enable multimodal ingestion via RAG-Anything. |
| `--install-raganything` | Install raganything[all] during setup. |
| `--raganything-parser` | Parser choice: mineru (default), docling, paddleocr. |
| `--run-raganything` | Auto-refresh RAG-Anything on every compile. |
| `--run-cognee` | Runs best-effort Cognee runtime cognify during compile. |
| `--install-cognee` | Installs Cognee with the current Python if missing. |

Users do not need to know the UA install path or type `/understand`; `project compile` runs `project refresh-understand-anything` when the UA graph is missing or stale.

## 2. Compile the graph and projections

```bash
llm_wiki project compile
```

`project compile` writes the durable artifacts:

```text
.llm-wiki/
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

Use `--changed-only` after the first run to skip unchanged markdown files while preserving the previous graph when no files changed. If Understand Anything is enabled, compile first refreshes/materializes `.llm-wiki/external/understand-anything.md`; if Cognee runtime is enabled, it also updates Cognee best-effort after writing `.llm-wiki/cognee_bundle/`.

## 3. Build and serve the static frontend

```bash
llm_wiki project build-site
llm_wiki project serve --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

<!-- BEGIN: subagent-r-watch -->
### Auto-rebuild on save

Pair the dev server with a polling watcher so edits under `data/` and `docs/` trigger an incremental recompile:

```bash
# terminal 1
python3 -m http.server 56821 --directory .llm-wiki/site

# terminal 2
llm_wiki project watch
```

`project watch` polls every 2 s, debounces 1 s, and runs `compile --changed-only`. Use `--once` for cron-style rebuilds (snapshots vs `.llm-wiki/.watch-cache.json`), `--paths <dir>` to add custom watch dirs, and `--interval` / `--debounce` to tune cadence.
<!-- END: subagent-r-watch -->

For an annotated tour of every visible route — home, sources, concepts, entities, papers, repos, topics, syntheses, questions, timeline, graph, plus the AI siblings — see [`docs/frontend-redesign.md`](frontend-redesign.md).

The frontend is dependency-light and writes:

```text
.llm-wiki/site/index.html
.llm-wiki/site/sessions/index.html
.llm-wiki/site/graph.json
.llm-wiki/site/search-index.json
.llm-wiki/site/llms.txt
```

## 4. Import local agent session history

Session history import is explicit: normal compile/build reads already-normalized sessions but does not scan private Claude Code or Codex transcript stores on its own.

```bash
# Preview matching Claude Code/Codex sessions for this project:
llm_wiki project sessions discover

# Normalize and store them under .llm-wiki/harness_sessions/:
llm_wiki project sessions discover --import

# Confirm the imported set:
llm_wiki project sessions list

# Rebuild so sessions/index.html and session detail pages are emitted:
llm_wiki project build-site
```

Imported sessions appear in the global Sessions section, site search, and the home Browse cards. Session detail pages render user/assistant turns as readable markdown, attach tool-use blocks under the preceding assistant turn, and expose a left turn rail for `#turn-N` navigation. See [`docs/session-history.md`](session-history.md) for privacy notes, import formats, and the current transcript typography map.

## 5. Lint the wiki

```bash
llm_wiki project lint
```

Walks the compiled graph + wiki + site and flags orphan papers, stale citations, drift between graph and wiki/, ghost synthesis inputs, and more. Writes `.llm-wiki/lint-report.md` and `.llm-wiki/lint-report.json`. Pass `--fix-trivial` to apply safe auto-fixes (missing `implemented_in` edges, ghost-input pruning) and `--severity error` to only fail the exit code on errors.

## 6. Query the wiki

```bash
llm_wiki project query "What is Gaussian Splatting?"
```

Search-only by default — BM25 over `.llm-wiki/site/search-index.json`, with a 200-char excerpt pulled from the matching `wiki/<kind>/<slug>.md`. Pass `--kind papers` (or `concepts`, `repos`, etc.) to narrow, `--top-k N` to widen, and `--json` for structured output. Add `--llm` (or set `LLM_WIKI_QUERY_LLM=1`) to ask Claude for a synthesized answer with `[node_id]` citations; `--interactive` opens a readline REPL — blank line or EOF exits. `LLM_WIKI_QUERY_DRY_RUN=1` exercises the prompt without an API call.

## 7. Export agent harness files

```bash
llm_wiki project export-agent-harness
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
llm_wiki project export-agent-harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 8. Export an Obsidian vault

```bash
llm_wiki project export-obsidian
```

Or write into an existing vault:

```bash
llm_wiki project export-obsidian --vault "$OBSIDIAN_VAULT_PATH"
```

The vault includes markdown projections, `.obsidian` defaults, graph coloring, `raw/assets/`, and a Dataview dashboard.

## 9. Configure MCP

```bash
llm_wiki project mcp-config --server-name my_project_wiki
```

Paste the output under `mcp_servers` in `~/.hermes/config.yaml`, then restart Hermes/gateway.

## 10. Graphiti export / sync

Dependency-free episode export:

```bash
llm_wiki project export-graphiti
```

Dry-run sync smoke without Graphiti installed:

```bash
llm_wiki project sync-graphiti --dry-run
```

Live sync requires `graphiti_core` and a reachable Neo4j backend:

```bash
llm_wiki project sync-graphiti \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 11. Deploy to GitHub Pages

Push the compiled site at `.llm-wiki/site/` to the `gh-pages` branch of the project's git origin:

```bash
llm_wiki project deploy --build --enable-pages
```

`--build` runs `project compile` first so the site is fresh. `--enable-pages` turns Pages on via the `gh` CLI (idempotent; skipped with a hint if `gh` is missing). Use `--dry-run` to stage and commit without pushing, `--branch` / `--remote` to override defaults, and `--force` to allow deploying with a dirty working tree.

The site becomes reachable at `https://<owner>.github.io/<repo>/`.

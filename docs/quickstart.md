# Quickstart

This page shows the shortest path from an existing project directory to a browsable LLM-Wiki.

## 1. Initialize a project wiki

From the project you want to index:

```bash
cd /path/to/my-project
llm_wiki project init \
  --name my_project_wiki \
  --source-kind Repository \
  --source README.md \
  --source docs \
  --source src \
  --source tests
```

This creates `.llm-wiki/config.json` and records the default sources that future `compile` runs should use.

For code-heavy projects, `Repository` or `CodeProject` is preferred. For paper/note corpora, use `Paper` or `SourceDocument`.

## 2. Compile the graph and projections

```bash
llm_wiki project compile --changed-only
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
  site/
  cognee_bundle/
```

The `--changed-only` flag uses `.llm-wiki/manifest.json` content hashes to skip unchanged markdown files while preserving the previous graph when no files changed.

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
.llm-wiki/site/graph.json
.llm-wiki/site/search-index.json
.llm-wiki/site/llms.txt
```

## 4. Export agent harness files

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

## 5. Export an Obsidian vault

```bash
llm_wiki project export-obsidian
```

Or write into an existing vault:

```bash
llm_wiki project export-obsidian --vault "$OBSIDIAN_VAULT_PATH"
```

The vault includes markdown projections, `.obsidian` defaults, graph coloring, `raw/assets/`, and a Dataview dashboard.

## 6. Configure MCP

```bash
llm_wiki project mcp-config --server-name my_project_wiki
```

Paste the output under `mcp_servers` in `~/.hermes/config.yaml`, then restart Hermes/gateway.

## 7. Graphiti export / sync

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

## 8. Deploy to GitHub Pages

Push the compiled site at `.llm-wiki/site/` to the `gh-pages` branch of the project's git origin:

```bash
llm_wiki project deploy --build --enable-pages
```

`--build` runs `project compile` first so the site is fresh. `--enable-pages` turns Pages on via the `gh` CLI (idempotent; skipped with a hint if `gh` is missing). Use `--dry-run` to stage and commit without pushing, `--branch` / `--remote` to override defaults, and `--force` to allow deploying with a dirty working tree.

The site becomes reachable at `https://<owner>.github.io/<repo>/`.

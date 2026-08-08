# MCP — wire Tesserae into Claude Code, Codex, Cursor

<!-- translations:start -->
<p align="center"><a href="../i18n/integrations/mcp.ko.md">한국어</a> · <a href="../i18n/integrations/mcp.zh.md">中文</a> · <a href="../i18n/integrations/mcp.ja.md">日本語</a> · <a href="../i18n/integrations/mcp.ru.md">Русский</a> · <a href="../i18n/integrations/mcp.es.md">Español</a> · <a href="../i18n/integrations/mcp.fr.md">Français</a> · <a href="../i18n/integrations/mcp.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae ships a [Model Context Protocol](https://modelcontextprotocol.io) stdio server that exposes the compiled typed graph to any MCP-aware client: Claude Code, Codex CLI, Cursor, Claude Desktop, and others. The server advertises three full MCP surfaces — **tools**, **resources**, and **prompts** — so clients can both query the graph on demand and seed context cheaply from canonical URIs.

## Prerequisites

The server reads from `.tesserae/graph.json`, so a one-time compile is required:

```bash
cd /path/to/your-project
tesserae init    # interactive; or --yes for non-interactive
tesserae compile  # deterministic, no LLM calls, no API keys
```

Recompile any time your sources change. The server will pick up the new graph on the next tool call without needing to restart.

## 1) Generate the client config

```bash
tesserae projects mcp-config
```

Prints a JSON snippet roughly like:

```json
{
  "mcpServers": {
    "tesserae": {
      "command": "python3",
      "args": [
        "-m", "tesserae.mcp_server",
        "--graph", "/path/to/your-project/.tesserae/graph.json"
      ]
    }
  }
}
```

The exact path is filled in from the current project. Pass `--name <alias>` if you want a different server entry name than `tesserae`.

## 2) Paste it into your MCP client

| Client | Config location |
|---|---|
| Claude Code | `~/.claude/mcp-servers.json` (or `~/.config/claude-code/mcp-servers.json`) |
| Claude Desktop | macOS: `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Codex CLI | `~/.config/codex/mcp-servers.json` |
| Cursor | Settings → MCP Servers → paste JSON |
| Hermes | `~/.hermes/config.toml` (use the TOML-equivalent block printed by `mcp-config --format hermes`) |

Restart the client after editing. The next session will connect and discover the Tesserae surface.

## 3) What the client sees

Every tool accepts an optional `graph_path` or `project` (registry alias) so a single server can resolve any registered vault per call. With no explicit project, read tools resolve the project you're in (the nearest registered project root above the server's cwd), then the server's `--graph`. There is no "active project" — all registered projects are equal.

**Graph query & retrieval**

| Tool | Purpose |
|---|---|
| `schema` | Controlled node, edge, and wiki-kind vocabulary |
| `graph_summary` | Node + edge counts and type distributions for the resolved project |
| `search_nodes` | Filter public graph nodes by `query`, `type`/`types`, `kind`, `limit`, hybrid `mode`/`weights`; `include_superseded` to surface retired nodes |
| `node_context` | A node + its incident edges + neighbouring nodes. `use_ppr` ranks neighbours by personalized PageRank instead of a 1-hop walk; `include_superseded` and `limit` bound the result |
| `embedding_status` | Report the active embedding backend powering hybrid search |
| `search_facts` | Temporal facts projected from the graph (Graphiti-style); `current_only` filters to live facts, `as_of` answers as of a past date. The two are refused together — they express different clocks — and `undated_included` reports how many of the rows you got carry no date |
| `timeline` | Facts ordered by `valid_from` for a longitudinal view |
| `graph_ppr` | Personalized PageRank seeded at one or more `seed_node_id`s; returns the top-K most relevant nodes with tunable `alpha`, `directed`, `edge_type_weights` |
| `wiki_page` | The compiled markdown page body for a node, plus the internal links it references |
| `raw_source` | The original source markdown (capped at 16 KB) |
| `lint_report` | The latest compile-time lint findings (capped at 64 KB) |

**On-demand context compiler** (Phase 7)

| Tool | Purpose |
|---|---|
| `compile_context` | Compile a tailored, **cited** context doc for a `query` or explicit `seeds`. Walks a depth-bounded subgraph (`depth`, 1–10, default 2), ranks with PPR, and fills a character `budget` (default 32000; pass `0` for uncapped). Deterministic by default; set `synthesize: true` for an LLM-written narrative "topic" slice. Returns `body`, `citations`, `selected_node_ids`, and `char_budget_used`. Set `preview: N` to return a bounded preview + a `handle` instead of the full body (memex-style read-discipline) |
| `get_handle` | Page a large payload previously returned as a `handle` (e.g. `compile_context` with `preview`) in slices (`offset`, `limit`) — fetch more on demand instead of dumping it all into context |
| `list_communities` | List `COMMUNITY_SUMMARY` nodes minted by the post-compile pass, ranked by member count (`min_size`, `limit`); walk `summarizes` edges back to members via `node_context` |
| `fresh_insights` | Session findings ranked by Ebbinghaus-style decay score (newest + most-accessed first); filters out superseded near-duplicates. Optional `kind`, `limit`, `include_superseded` |

**Session memory** (see [sessions.md](sessions.md))

| Tool | Purpose |
|---|---|
| `list_sessions` | Session envelopes (id, started_at, title, files_touched, finding counts) for the resolved project; `since`, `limit` |
| `find_session_findings` | Every Session-derived finding linked to `node_id` via `discussed_in` / `references`, optionally filtered to `kinds` (insight / decision / question / todo / hypothesis / takeaway) |
| `find_code_symbol_mentions` | Expand a session finding into the `CodeFunction`/`CodeClass`/`CodeMethod` symbols it mentions, via `discusses` edges from the opt-in insight↔symbol link pass. The code layer is opt-in: with no `external_tools` entry for `codegraph`, this returns nothing |

**Q&A & registry**

| Tool | Purpose |
|---|---|
| `ask` | Natural-language Q&A. Omit `scope` and a smart router picks the target across your registered projects (federated fallback) and reroutes across consecutive questions (pass `conversation_id` to isolate a thread). Explicit `scope`: `current` (one project), `all-registered` (one answer per project), `federated` (ONE merged, cross-referenced answer; `semantic` on by default). Plus `backend`, `top_k`, `scope_aliases`, `claude_config_dir` |
| `list_projects` / `register_project` / `unregister_project` | Multi-project registry control (no privileged "active" project) |

**Guided setup**

| Tool | Purpose |
|---|---|
| `tesserae_setup_plan` | Detect the environment and propose a setup plan as JSON. Read-only — never touches `.tesserae/` |
| `tesserae_setup_apply` | Apply a (possibly edited) plan: write `.tesserae/config.json` and run gated install/run actions. Gated behind `confirm_install_actions` / `confirm_run_actions` |

### Resources — auto-loaded into the model's context

URIs the client can pull in via its resource picker without burning a tool turn:

- `tesserae://graph/schema` — same payload as the `schema` tool, ready as static context
- `tesserae://graph/summary` — summary of the resolved (cwd) project
- `tesserae://lint-report` — the latest lint report as markdown

Plus URI templates the client can construct on demand:

- `tesserae://wiki/{kind}/{slug}` — any compiled wiki page body
- `tesserae://raw/{source_path}` — any raw source markdown

### Prompts — one-click research templates

These appear in the client's slash menu (e.g. Claude Code's `/` palette):

| Prompt | Arguments | What it does |
|---|---|---|
| `summarize-paper` | `slug` (required) | Calls `node_context` + `wiki_page` + optional `raw_source`, then returns a structured summary: contribution, method sketch, headline results, limitations, related nodes |
| `find-related-work` | `topic` (required), `limit` | Chains `search_nodes` + `node_context` for the top-K related items with relevance justifications |
| `compare-approaches` | `a`, `b` (both required) | Pulls `node_context` for both + `search_facts` for performance claims; returns side-by-side comparison with synthesis |
| `gap-analysis` | `topic` (optional) | Surfaces unresolved open questions, missing benchmarks, under-evidenced claims |
| `triage-open-questions` | _none_ | Lists every `OpenQuestion` node, groups by topic, proposes a priority order |

Each prompt renders to a single user message that tells the model exactly which Tesserae tools to chain, so the model doesn't have to rediscover the surface every time.

## Multi-project: register several vaults under one server

A persistent registry at `~/.tesserae/registry.json` lets the same MCP server resolve any registered project by name:

```bash
tesserae projects register /path/to/research --name research
tesserae projects register /path/to/notes    --name notes
```

After this, every tool that accepts `project` or `graph_path` will resolve `project: "research"` against the registry instead of needing a full path. The server even validates that the registered `graph_path` still exists and returns a clear error if a recompile is needed.

### Querying across projects

With no `scope`, the `ask` tool **routes the question for you**: it picks a single project when the question clearly targets one, and **federates** (one merged, cross-referenced answer) when it's comparative or general. The two explicit cross-project scopes:

- **`federated`** — assemble ONE graph from the named projects (identity-merged + embedding-backed semantic links) and return a single cross-referenced answer. Defaults to ALL registered projects; narrow with `scope_aliases`. `semantic` is on by default.
- **`all-registered`** — fan out and return one independent answer per project (`by_project`).

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "What did I decide about caching, and does any clipped paper support it?",
    "scope": "federated"
  }
}
```

Restrict either scope to a subset with `scope_aliases: ["research", "notes"]`. Inspect a federation from the CLI with `tesserae federation status` / `tesserae federation explain <node>`.

## Multi-account Claude CLI

If your `ask` tool routes through the Claude CLI and you have multiple accounts (e.g. `~/.claude` and `~/.claude-personal2`), pass `claude_config_dir` per call:

```jsonc
{
  "name": "ask",
  "arguments": {
    "question": "...",
    "claude_config_dir": "/Users/you/.claude-personal2"
  }
}
```

The server exports `CLAUDE_CONFIG_DIR` for the duration of that call only and restores the previous value afterwards. No leakage between calls.

## Verification

After restarting your MCP client, confirm the connection:

- Claude Code: `/mcp` should list `tesserae` with the tool count.
- Cursor: the MCP icon in the chat bar should show `tesserae: connected` with tool/resource/prompt counts.
- Codex / Hermes: invoke any tool by name (e.g. `schema`) and check the response.

If nothing appears, double-check that `--graph` points at an existing `.tesserae/graph.json` — the server now validates this on startup and on every tool call, so you'll see a clear error message instead of a silent 500.

## Where this fits

The MCP server is the **read interface** to the typed graph. For the **write path** (ingesting sources, recompiling, refreshing companion tools like RAG-Anything) use the CLI directly. The two are decoupled: the CLI updates `.tesserae/`, the MCP server reads whatever's there on the next tool call.

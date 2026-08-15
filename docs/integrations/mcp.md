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
| `graph_map` | **Start here.** Budgeted map of the graph hierarchy — the Descent entry point. No scope returns the root card set (counts, top hubs, one card per coarsest community); `scope='<card scope_id>'` descends a dendrogram level; `org:root` walks the agent org tree. Orients an agent without it having to guess search terms |
| `schema` | Controlled node, edge, and wiki-kind vocabulary |
| `graph_summary` | Node + edge counts and type distributions for the resolved project |
| `search_nodes` | Filter public graph nodes by `query`, `type`/`types`, `kind`, `limit`, hybrid `mode`/`weights`; `include_superseded` to surface retired nodes; `explain` adds a retrieval `profile` (see below) |
| `node_context` | A node + its incident edges + neighbouring nodes. `use_ppr` ranks neighbours by personalized PageRank instead of a 1-hop walk; `include_superseded` and `limit` bound the result. A `node_id` that lost a merge in a later compile is **not** a miss: it resolves through the merge ledger to the node that absorbed it, and the response gains `status: "merged"` with `merged_from` / `merged_into` so you learn the id to hold from now on. The ledger is consulted only after the graph misses, so a live id can never be redirected |
| `embedding_status` | Report the active embedding backend powering hybrid search, plus its persisted vector cache — `vectors_cached` for this backend/dim key, and process-wide `cache_hits` / `cache_misses` / `cache_errors`, so a cold or unwritable cache cannot be mistaken for a fast path. Accepts `graph_path` / `project` to pick the project whose sidecar is reported |
| `search_facts` | Temporal facts projected from the graph (Graphiti-style), ranked over fact CONTENT — subject, predicate, object, evidence — never the serialized fact, so an id or a metadata fragment is not a match; `dated` (`any`, `dated`, `undated`) selects by whether a fact carries a usable `valid_from`; `current_only` filters to live facts, `as_of` answers as of a past date. The two are refused together — they express different clocks — and `undated_included` reports how many of the rows you got carry no date. `observed_as_of` is the second axis (see below) |
| `timeline` | Facts ordered by PARSED `valid_from` for a longitudinal view, with undated facts bucketed after every dated one and counted back as `undated_events` rather than interleaved; `dated` (`any`, `dated`, `undated`) selects by whether a fact carries a usable `valid_from`; `as_of` answers as of a past date — a point pivot over validity intervals, not a range bound — and `undated_included` reports how many of the rows you got carry no date. An undated fact is kept by `as_of`, so that count is what tells a thin answer from a complete one. Takes `observed_as_of` too. `total_events` counts every fact that MATCHED, not the page you were handed — the whole match set is date-sorted before the page is sliced, so the earliest events are the ones a timeline actually returns, and `total_events > len(events)` is how you tell a full page from a complete answer |
| `graph_ppr` | Personalized PageRank seeded at one or more `seed_node_id`s; returns the top-K most relevant nodes with tunable `alpha`, `directed`, `edge_type_weights` |
| `wiki_page` | The compiled markdown page body for a node, plus the internal links it references. A stale `node_id` follows the same merge-ledger redirect, silently — the absorbed node's name is an alias on the survivor, so the survivor's page *is* the page you asked for |
| `raw_source` | The original source markdown (capped at 16 KB). Never returns bytes: for an `Artifact` node it points you at `drill_down`, which reports the asset's path and site address instead |
| `verify_claim` | Verify ONE triple against the graph — exact lookup, no LLM, no fuzzy matching, no ranked results. Returns `{verdict, reason, triple, citation, provenance, advisory}`; `verdict` is `SUPPORTED` (the edge exists **and** its evidence is a verbatim document span), `PRESENT_UNEVIDENCED`, or a refusal. Chain `search_nodes` → `verify_claim` when you only have prose |
| `lint_report` | The latest compile-time lint findings (capped at 64 KB) |
| `doctor_run` | Run the health checks and return the report as JSON (`findings`, `exit_code` 0/1/2). **Always read-only** — fixes never run over MCP; use `tesserae doctor --fix` on the CLI |
| `doctor_report` | The contents of `.tesserae/doctor-report.md` (capped at 64 KB); empty until `tesserae doctor` has run |
| `charter_route` | Place ONE task in the chartered domain tree in one call — the alternative to paging `graph_map`'s cards when none is choosable by name. Ranks every live domain (slug, anchor name, and its brief where one is cached) and walks beam-1 down to the domain whose subtree carries the best evidence; returns `{routed, path, brief, parent, siblings, route_quality}`, and a domain slug is a scope that survives ingest where a community id does not. `altitude` (`auto`/`division`/`department`/`team`) caps how deep the walk may go. **Best-effort, and it says so**: `charter.json`'s bytes are idempotent, this ranking is not — the embedding lane varies with the machine's backend, and a domain's row carries its brief once one has been written. **No compile writes briefs yet**, so today every row is cold and `warm_rows` is `0`; `route_quality` reports `{backend, semantic, corpus_rows, warm_rows, evidenced_rows}` rather than leaving you to guess which, and every card carries `evidence` — `lexical` (a term match, which survives a backend change), `semantic` (embedding similarity alone, which does not) or `none` (walked through). A task it cannot place comes back `routed: false` naming **no** domain at all: there is no low-confidence candidate to read a guess out of. Needs `.tesserae/charter/charter.json`, written by `tesserae compile` |

**Two clocks on the fact tools.** `search_facts` and `timeline` take two
independent pivots, and they answer different questions:

- **`as_of` — valid time, "what was TRUE then."** Read off each fact's own
  `valid_from` / `valid_to`, which come from the sources' timestamps. It ships
  in `graph.json` and `temporal_facts.jsonl` because it is a pure function of
  them.
- **`observed_as_of` — transaction time, "what had we LEARNED by then."** Read
  off the `fact_observed` sidecar, stamped once per compile from the wall
  clock. It lives only in `.tesserae/sqlite.db` — a wall clock inside an
  artifact would make the same sources compile to different bytes tomorrow.

They **compose**: `as_of: "2026-03-01"` with `observed_as_of: "2026-05-01"`
means *what did we believe held on 1 March, as we knew it on 1 May*. Each
reports its own coverage over the rows you actually received —
`undated_included` for facts with no usable `valid_from`, `unobserved_included`
for facts the ledger cannot date. `observed_as_of` needs a compiled project:
with no ledger it errors rather than handing back the whole corpus under an
"as we knew it" label.

**Profiling a retrieval.** `search_nodes` and `compile_context` take
`explain: true` and answer with a `profile` — for each of the `bm25`, `lexical`
and `embedding` lanes its weight, `candidates_in`, how many it scored,
`embed_calls` / `cache_hits` / `cache_misses`, whether it ran `vectorized`, and
its wall time, plus the total
`candidates_in` / `admitted` / `returned` and which lanes actually contributed
each of the nodes it counts. `returned` and that per-node lane attribution are
**pre-budget**: the fusion fixes both over its own top-`k` slice, and a binding
`budget_chars` trims that slice afterwards, in the MCP layer, without rewriting
the profile. So under a tight budget `returned` describes the slice the
retriever produced rather than the rows in the response, and the `continuation`
line is what reports the difference. `search_nodes` returns one profile;
`compile_context` returns a list, one per seed search it ran.

Off by default, and off is not a formality: measuring costs time, so this is a
diagnostic rather than something to leave on. It cannot move a ranking — every
number is read off score and rank tables the fusion had already produced — and
with the flag unset the response carries exactly the keys it always had. The
`cache_hits` / `cache_misses` counters are how you tell a warm vector cache
from a cold one on a live query rather than by inspecting `embedding_status`
after the fact. `vectorized` is the same kind of signal for the embedding
lane's arithmetic: with `numpy` installed the cosine runs as one matrix
product, and without it the lane falls back to a per-document Python loop that
costs roughly 5x as much on a corpus-sized candidate set — `vectorized: false`
is what tells those apart instead of leaving the lane looking inexplicably
slow.

**On-demand context compiler** (Phase 7)

| Tool | Purpose |
|---|---|
| `compile_context` | Compile a tailored, **cited** context doc for a `query` or explicit `seeds`. Walks a depth-bounded subgraph (`depth`, 1–10, default 2), ranks with PPR, and fills a character `budget` (default 32000; pass `0` for uncapped). Deterministic by default; set `synthesize: true` for an LLM-written narrative "topic" slice. Returns `body`, `citations`, `selected_node_ids`, and `char_budget_used`. Set `preview: N` to return a bounded preview + a `handle` instead of the full body (memex-style read-discipline). `view` restricts the walk to a named edge partition — `semantic`, `temporal`, `causal` or `entity`; pass an array of names to run one walk per view and fuse them (weighted RRF). Whenever a view is requested — one name or several — each citation carries `via_views` (the views whose walk reached it). `explain` adds `profile`, one per seed search |
| `get_handle` | Page a large payload previously returned as a `handle` (e.g. `compile_context` with `preview`) in slices (`offset`, `limit`) — fetch more on demand instead of dumping it all into context |
| `list_communities` | List `COMMUNITY_SUMMARY` nodes minted by the post-compile pass, ranked by member count (`min_size`, `limit`); walk `summarizes` edges back to members via `node_context` |
| `fresh_insights` | Session findings ranked by Ebbinghaus-style decay score (newest + most-accessed first); filters out superseded near-duplicates. Optional `kind`, `limit`, `include_superseded` |

**Session memory** (see [sessions.md](sessions.md))

| Tool | Purpose |
|---|---|
| `list_sessions` | Session envelopes (id, started_at, title, files_touched, finding counts) for the resolved project; `since`, `limit` |
| `find_session_findings` | Every Session-derived finding linked to `node_id` via `discussed_in` / `references`, optionally filtered to `kinds` (insight / decision / question / todo / hypothesis / takeaway) |
| `find_code_symbol_mentions` | Expand a session finding into the `CodeFunction`/`CodeClass`/`CodeMethod` symbols it mentions, via `discusses` edges from the opt-in insight↔symbol link pass. The code layer is opt-in: with no `external_tools` entry for `codegraph`, this returns nothing |
| `activity_summary` | Daily/weekly digest across registered projects — sessions, findings, git commits, PRs, ingested docs — each windowed by **its own** timestamp, never a session's `started_at`. Deterministic markdown, with an LLM narrative prepended unless disabled |
| `query_decisions` | Decisions across registered projects in a time range: explicit **human** choices parsed deterministically from Claude Code's `AskUserQuestion` (the question and the option chosen), plus agent decisions mined from the conversation |

**Agent memory & writeback** (see [agent-memory.md](../agent-memory.md))

| Tool | Purpose |
|---|---|
| `agent_view_explain` | Explain an agent-scoped view *without loading it*: resolution mode (worker / manager / org), member agents, each L1 artifact's path, node count, and the `distilled_through` staleness watermark |
| `drill_down` | Resolve a distillate `member_ref` back to its raw L0 node — the manager's explicit, audit-logged escalation past distilled visibility. Returns status `alive` / `changed` / `absorbed` / `gone`; every call is logged to the sidecar. Drilling a **figure** `Artifact` whose asset resolved inside the project adds three keys no other node carries: `asset_path` (where the bytes live on disk), `asset_sha256` (the digest of those bytes, which with the kind seeds the node id) and `asset_site_path` (the content-addressed address under a built site's `raw-assets/`). Table and equation Artifacts have no asset at all — their content *is* their description — and a figure resolved outside the project root never stored a path; both drill back with the ordinary keys. A malformed declared hash drops `asset_site_path` rather than inventing an address |
| `read_audit` | Who has been reading the graph: recorded read events (`tool`, `actor`, `node_ids`, `at`, `tesserae_version`) newest first, plus a per-actor tally, so the access counts driving forgetting-by-disuse can be attributed to a reader. **Opt-in** — nothing is recorded unless `TESSERAE_READ_AUDIT=1` is set on the server process, because an always-on audit makes every read a write. Rows stay readable after the flag is turned off; `enabled` reports the current setting. Filter by `actor`, `tool`, `node_id` |
| `graph_write` | Write typed nodes + edges into the graph directly — no markdown, no extraction pass. Appended to an append-only overlay and replayed as a compile producer, so the write **survives recompilation**. Strict: unknown types, an edge without evidence, or an endpoint that is neither in the payload nor an existing node id are all refused. **To retract** something simply wrong, without inventing a replacement: point a `retracts` edge at the wrong node **by id** — the target drops out of discovery (`search_nodes`, `fresh_insights`), out of context selection (`compile_context`), and out of every neighbour list and incident edge `node_context` returns. What it does *not* do is hide the node from someone who names it: an exact `node_context` lookup by id or name still returns the node itself, flagged `"retracted": true`, because the caller asked for that one. `include_superseded: true` puts it back in the discovery surfaces, and nothing is deleted |

**Q&A & registry**

| Tool | Purpose |
|---|---|
| `ask` | Natural-language Q&A. Omit `scope` and a smart router picks the target across your registered projects (federated fallback) and reroutes across consecutive questions (pass `conversation_id` to isolate a thread). Explicit `scope`: `current` (one project), `all-registered` (one answer per project), `federated` (ONE merged, cross-referenced answer; `semantic` on by default). Plus `backend`, `top_k`, `scope_aliases`, `claude_config_dir`. On a graph-routed question the envelope carries `plan` (the planner's reasoning, the steps it chose, and `executed` — what actually ran), and may carry `proposed_write`: nodes and edges the planner thinks are worth recording, grounded only in what the *question* asserted. It is a **suggestion, never a write** — its provenance is always null, so `graph_write` refuses it until a caller with an agent key and an outside anchor supplies one. A mutation is never a side effect of a query |
| `query` | Raw retrieval, no LLM — mirrors `tesserae query`. `backend='wiki'` (default) is deterministic BM25/semantic search over the compiled wiki, returning ranked hits with excerpts; `backend='raganything'` queries the optional multimodal RAG index when the project has it enabled. Use `ask` for a synthesized, cited answer |
| `ingest` | Ingest raw web/text content (e.g. a browser clip) into the resolved project's knowledge graph |
| `list_projects` | List the registered projects |
| `register_project` | Add a project to the registry |
| `unregister_project` | Remove a project from the registry (no privileged "active" project exists) |

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

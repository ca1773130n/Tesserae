# Activity Summary + Citation-Name Fix — Design

- **Date:** 2026-07-04
- **Status:** Draft (awaiting review)
- **Mission fit:** Tesserae is a context engine that hands agents realtime, evolving
  knowledge. A daily/weekly activity digest is an *on-demand doc* projection over the
  same knowledge base — "what happened on day/week X" as agent-ready context. The
  citation fix makes every knowledge answer legible instead of leaking `node-<hash>`.

Two deliverables. **Part A ships first** (small, unblocks clean citations everywhere,
including the summary's own narrative). **Part B** is the summary feature.

---

## Part A — `node-<hash>` citation fix

### Root cause
Not a data defect. The LLM answer/synthesis paths instruct the model to cite sources
as raw `[node_id]` and return the body verbatim, never rewriting `[node_id] → title`.
The `{node_id: title}` map is *already assembled* per request (the `<source title=…
node_id=…>` block; `QueryHit.title`). A secondary fallback surfaces the id even when a
name is wanted.

### Fix sites
1. **`tesserae/query.py` `WikiQueryEngine.answer`** — after synthesis, rewrite the body:
   for every `_NODE_CITATION_RE` (`query.py:196`) match, replace `[node_id]` with the
   node's title from a `{hit.node_id: hit.title}` map built over `hits`. Unresolved ids
   are left untouched (never crash on a stray id).
2. **`tesserae/query.py` `_answer_via_cli`** — same rewrite on the CLI synthesis path.
3. **`tesserae/query.py:388`** — `title = raw.get('title') or raw.get('id')` currently
   yields the raw id for untitled session/source nodes. Resolve a readable label instead
   (`_session_display_name` / `kind:slug`) so the map itself never carries an id.
4. **`tesserae/llm_synthesis.py`** — `compile_context(synthesize=True)` and `wiki_page`
   emit `[node_id]` citations parsed by `_extract_citations` (`:325`) and never mapped.
   Apply the same rewrite using the id→name map it already holds.

### Shared helper
One function, reused by both files:

```python
def rewrite_citations(body: str, id_to_name: Mapping[str, str]) -> str:
    """Replace [node_id] citation markers with the node's display name.
    Unknown ids are left verbatim."""
```

Visible citation becomes the **title**; the machine-resolvable id stays available in the
source list, so nothing loses navigability.

### Test
Unit test: feed a body containing `[Type:slug:hash]` + an id→name map, assert the marker
renders as the title; assert an id absent from the map is left unchanged.

---

## Part B — Activity Summary

### Surfaces (one engine, three entry points)
- **CLI:** `tesserae summary --day YYYY-MM-DD [--week] [--project <name>] [--no-llm]`
  → new `_handle_summary` in `cli.py`.
- **MCP tool:** `activity_summary(day? | week? | since?/until?, project?, synthesize?)`
  in `mcp_server.py` — lets agents pull a digest as context.
- **Slash:** `/summary` thin wrapper over the CLI.

### Scope
Default = **all registered projects** (`ProjectRegistry.iter_registered_projects`).
`--project <name>` (CLI) / `project=` (MCP) opts into a single project.

### Window semantics — the core rule
**Never use session `started_at`.** Sessions are long-running and span days, so
session-level timestamps are not a valid window key. Everything is windowed at the
level of the individual artifact's own timestamp:

| Source | Window key |
|---|---|
| Messages (chat / response / tool) | the **turn's own `timestamp`** ∈ `[start, end)` |
| Findings / new knowledge | the **source turn's `timestamp`** (via `turn_ids` → turn index) ∈ window; if a finding has no resolvable turn timestamp it is **undated → excluded** (no `started_at` fallback) |
| Ingested / synthesized docs | the **document's own `updated_at` / `created`** (`raganything_refresh.py:304`) or source-file mtime ∈ window |
| Git commits | `git log --since --until` (author date) |
| PRs | created-in-window and merged/closed-in-window (`gh pr list`) |

`--day X` → local `[00:00, +24h)`. `--week [X]` → 7 consecutive daily buckets ending at
X (default: last 7 days). `--since/--until` is an escape hatch for arbitrary ranges.

### Gather (per project, reusing existing seams)
1. **Messages** — enumerate sessions via `HarnessSessionsDB.list_for_project()`. Prune
   cheaply to transcripts whose file **mtime ≥ window_start** (a file untouched since
   before the window can hold no in-window turns — and this uses mtime, not `started_at`).
   Re-parse each surviving raw transcript with `_claude_turns`/`_codex_turns` at a
   **raised `limit`** (avoid the 300-turn truncation), keep turns whose `timestamp` ∈
   window. Classify role: user=chat, assistant=response, tool=tooling (carries `name`).
2. **Findings / new knowledge** — from the compiled graph, for each session-finding node
   resolve its time via `turn_ids` → the session's turn `timestamp`; keep those ∈ window.
   Types: `SessionInsight/Decision/Question/TODO/Hypothesis/Takeaway`.
3. **Ingested docs** — docs whose `updated_at`/`created`/source mtime ∈ window.
4. **Git** — `git -C <root> log --since=<start> --until=<end> --pretty=…` via the
   `_git_head` subprocess pattern (`raganything_refresh.py:34`). Skip gracefully if the
   root is not a git repo.
5. **PRs** — derive `owner/repo` from `git -C <root> remote get-url origin`, then
   `gh pr list` (created + merged/closed windows) via subprocess. Skip gracefully if `gh`
   is absent/unauthenticated (digest still renders without the PR section).

### Render
- **Deterministic markdown**, bucketed by day: per day → Sessions (grouped by
  project/harness) · Files touched · Decisions & Insights · Commits · PRs opened/merged ·
  Docs ingested. This is the reproducible spine (mirrors the report renderers in
  `temporal.py` / `review_workflow.py`).
- **LLM narrative** — feed the deterministic markdown to the existing provider path
  (`ask_project` / `WikiQueryEngine`, i.e. the configured codex backend) to write the
  prose "what happened." `--no-llm` returns the deterministic digest alone.
- **Weekly** = gather+render each of the 7 days deterministically, then one synth pass
  over the 7 daily sections into a weekly narrative.

### Output
Write to `.tesserae/summaries/<project>/daily-YYYY-MM-DD.md` (and
`weekly-YYYY-Www.md`); return the path(s) + inline body. For `--all`, one file per
project plus a combined top-level digest.

### Module layout
- New `tesserae/activity_summary.py` — window resolution, the 5 gatherers, the
  deterministic renderer, and the narrative synth call. One clear purpose; testable
  without the CLI/MCP shells.
- `cli.py` `_handle_summary` and `mcp_server.py` `activity_summary` are thin adapters
  that parse args → call the engine → format output.

### Reuse (not rebuild)
`HarnessSessionsDB` · `ProjectRegistry.iter_registered_projects` · `HarnessSession`
aggregates (files_touched/tools_used/commands_run/decisions/errors/tokens) ·
`_claude_turns`/`_codex_turns` · `turn_ids` finding→turn resolution · the `_git_head`
subprocess pattern · the existing LLM provider path · existing markdown-report renderers.

### Explicitly skipped (YAGNI — add when the trigger fires)
- No new SQLite time-index/columns — load-and-filter is fine at current volume. *Add
  when* per-project session counts make the scan slow.
- No persisted git/PR ingest — read at summary time. *Add when* you re-run weekly
  rollups often enough that re-querying `gh` is a bottleneck.
- No daemon-scheduled auto-digest, no static-site/HTML projection of summaries (markdown
  only). *Add when* you want summaries browsable on the compiled site.

### Tests
1. `rewrite_citations` (Part A) — marker → title; unknown id untouched.
2. Window edge inclusion — a turn/finding/doc exactly at `start` is included, one at
   `end` is excluded; a long session contributes only its in-window turns.
3. Deterministic e2e (`--no-llm`) on a fixture project: two fake sessions with turns on
   two different days + a small git repo with commits on those days → assert the
   day-X digest contains only day-X messages/commits and excludes day-Y.

### Open items / risks
- **Finding→turn timestamp:** confirmed `turn_ids` are integer indices into the turns
  list and `session_event.py` already reads `turn['timestamp']`; verify the index base
  (compile-time turn list vs. re-parsed list) during implementation so the two line up.
- **Ingested-doc timestamp field:** `updated_at` exists in the RAG refresh manifest;
  confirm the exact per-doc field / whether a `created` is also recorded, else fall back
  to source-file mtime.
- **`gh` availability** at summary time is environmental — treated as an optional
  section, never a hard failure.

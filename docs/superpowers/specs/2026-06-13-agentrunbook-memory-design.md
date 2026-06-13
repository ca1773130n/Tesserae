# AgentRunbook-style multi-granularity memory for Tesserae

**Date:** 2026-06-13
**Status:** Approved (brainstorming complete)
**Source of method:** LongMemEval-V2 `memory_modules/agentrunbook_r.py` (the
benchmark's SOTA reference). We port the *core idea*, not the harness.

## Goal

Give Tesserae's memory system AgentRunbook's **multi-granularity, LLM-distilled
memory**: from coding/agent sessions, distill three layers above raw
session-finding nodes, and retrieve across them with query decomposition. This
deepens the "session monitoring → agent-ready context" pillar.

Their layers → ours (all approved):

| AgentRunbook | Ability targeted | Tesserae node kind |
|---|---|---|
| raw state pool | static state recall | *(existing session-finding nodes — reused)* |
| event pool (per-transition) | dynamic state tracking | **`Event`** (new) |
| procedure note (runbook) | workflow knowledge | **`Runbook`** (new) |
| hint note | env gotchas + premise awareness | **`Gotcha`** (new) |

Query-time: decompose question → retrieve across pools → budget-merge.

## Scope (locked with user)

- IN: all three layers (Event + Runbook + Gotcha); distillation **integrated
  into session-finding extraction**; **configurable via CLI + config.json + MCP**
  (not env-only). Multi-pool / query-decomposed retrieval.
- OUT: no evaluation/leaderboard module, no real LME-V2 run, no GPU models.

## Principles (follow existing Tesserae conventions)

1. **LLM-optional, degrade-never-raise.** Every layer works deterministically
   with no LLM; an `LLMJsonClient` only *enriches* (mirrors `supersede`,
   `community_summaries`). Content-keyed disk cache for LLM verdicts.
2. **Byte-idempotent.** This sits squarely in the project's known
   byte-idempotence blind spot: all minted node ids / bodies / `first_seen_at`
   are **content-derived; no `datetime.now()` / RNG; stable ordering**. A rerun
   test asserts identical graph bytes.
3. **Opt-in, additive.** Default compile is unchanged. Enabling only *adds*
   nodes/edges; never mutates or deletes existing ones.

## Components (disjoint, testable units)

### 1. `tesserae/session_event.py` — Event layer (per-transition)
- `extract_events(session, *, json_client=None) -> (nodes, edges)`.
- Deterministic over `session_graph._normalised_turns(session)`: each
  significant transition (tool call / action turn) → one `Event` node capturing
  `{turn_id, actor, action, brief state-change}`. Node id = content hash of
  `(session_id, turn_id, action)`. Consecutive events linked `precedes`.
- Integrated in session extraction: events are minted into the **session slice**
  alongside findings, and each session-finding node links to the Event(s) at its
  `turn_ids` via a `derived_from` edge (findings already carry `turn_ids`). This
  is the "integrated into session-finding nodes" requirement.
- LLM optional: when a client is present, enrich each event's one-line
  state-change description (cached); else a deterministic template from the turn.

### 2. `tesserae/memory/distill.py` — Runbook + Gotcha (cross-session)
- `distillation_enabled(cfg) -> bool` — `cfg["distillation"]["enabled"]` OR env
  `TESSERAE_RUNBOOK_DISTILLATION` truthy; explicit config `false` / env-falsy
  disables (mirror `community_summaries.is_enabled_via_env`).
- `set_distillation_test_client` / `_get_distillation_test_client` — test
  injection, mirroring community summaries.
- `run_distillation_pass(graph, *, json_client=None, cache_dir, layers, min_cluster_size=2) -> ResearchGraph`:
  1. Cluster session-finding nodes by shared topic — reuse `supersede.jaccard`
     + the `reinforce` union-find (deterministic, id-ordered).
  2. Classify each cluster: *procedure-ish* (how/steps/decisions) → `Runbook`;
     *pitfall-ish* (fail/error/gotcha/avoid/wrong) → `Gotcha`. Deterministic
     keyword heuristic; LLM refines + writes the distilled title/body when a
     client is present (content-keyed cache).
  3. Mint one `Runbook`/`Gotcha` node per qualifying cluster (≥ `min_cluster_size`)
     + `derived_from` edges to its member findings (and any events they link).
     Deterministic id = content hash of sorted member ids. `first_seen_at` =
     earliest member's timestamp.
- Pure/deterministic fallback when no client: title from the cluster's dominant
  finding name, body = bulleted member names.

### 3. `tesserae/retrieval/query_decompose.py` + `context_compiler.py` multi-pool
- `decompose_query(query, *, json_client=None, max_subqueries=5) -> list[str]`:
  LLM splits into sub-queries; deterministic fallback = `[query]` (or sentence
  split). Bounded; degrade-never-raise.
- `compile_context(..., multi_pool=False)`: when true — run `hybrid_search` per
  sub-query, union seeds; after PPR ranking, **reserve budget slots** for the
  top in-neighborhood `Runbook` / `Gotcha` / `Event` nodes (pool quotas) so
  distilled memory always surfaces when relevant. Default path byte-identical.

### 4. `tesserae/research_graph.py` — kinds + registration
- Add `EVENT="Event"`, `RUNBOOK="Runbook"`, `GOTCHA="Gotcha"` after
  `COMMUNITY_SUMMARY`. Add `DISTILLED_MEMORY_TYPES = {RUNBOOK, GOTCHA}` and treat
  `EVENT` as a session-scoped type. Register in the wiki-kind / projector / public
  maps so `Runbook`/`Gotcha` get vault pages (like `CommunitySummary`); `Event`
  is public but low-salience. Add `derived_from` / `precedes` edge types if absent.

### 5. Wiring + configuration
- `project.py`: a `_merge_distillation(graph, cfg)` step (after supersede /
  community summaries) gated by `distillation_enabled(cfg)`; event extraction
  invoked inside the session-extraction step. Caches under
  `self.paths.root / "distillation_cache"`.
- `cli.py`: `tesserae compile --distill / --no-distill` (mutually-exclusive
  group, like `--sessions`); `tesserae context --multi-pool`.
- `mcp_server.py`: `compile_context` tool gains a `multi_pool` boolean; the
  engine-driven compile honors `distillation.enabled` from config so MCP/engine
  users configure it once in `config.json`.

## Data flow

```
session transcript
  └─ session extraction ── findings (existing) ─┐
                        └─ extract_events ─ Event nodes ─ derived_from ┘
typed graph (post-merge)
  └─ run_distillation_pass ─ cluster findings ─ Runbook / Gotcha nodes
                                              └ derived_from → findings/events
compile_context(multi_pool=True, query)
  └─ decompose_query → per-pool retrieval → budget-merge → cited bundle
```

## Error handling
- Any LLM/SDK failure in any layer → deterministic fallback, never raise.
- Disabled (default) → zero behavior change; passes are skipped wholesale.
- Empty session / no findings → no nodes minted (early return).

## Testing
- `tests/test_session_event.py` — deterministic event extraction; finding↔event
  links; LLM-enrichment via stub; rerun byte-idempotence.
- `tests/test_distillation.py` — clustering determinism; Runbook vs Gotcha
  classification; node + `derived_from` minting; deterministic fallback (no
  client); stub-LLM path; **byte-idempotent rerun**; disabled = no-op.
- `tests/test_multipool_retrieval.py` — `decompose_query` fallback + stub;
  pool-reserved selection surfaces a Runbook/Gotcha/Event; default path
  unchanged.
- `tests/test_docs_i18n.py` stays green (spec lives under i18n-excluded
  `docs/superpowers/`).

## Build sequence
1. `research_graph.py` kinds + maps (+ schema test).
2. `session_event.py` + integrate into session extraction (+ test).
3. `memory/distill.py` (+ test).
4. `query_decompose.py` + `context_compiler.py` multi-pool (+ test).
5. `project.py` wiring + config; `cli.py` flags; `mcp_server.py` param.
6. Full suite green + byte-idempotence rerun check; docstring docs.

## Out-of-scope / YAGNI
No embedding-model swap, no rerank stage, no async controller, no event LLM by
default (deterministic events are enough; LLM only enriches).

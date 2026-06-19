# Memex / OKF research — decision document

Date: 2026-06-20
Scope: four candidate features evaluated against Tesserae's mission (context engine: session monitoring, proactive ingestion, on-demand docs). Bias: laziest thing that works. Reject speculative flexibility.

---

## Feature 1 — OKF (Open Knowledge Format) import/export

**What it actually is (verified):** Real. Google Cloud's Open Knowledge Format v0.1 (Draft), published ~2026-06-12, Apache-2.0, at `GoogleCloudPlatform/knowledge-catalog/okf/SPEC.md`. It is *just a convention*: a directory tree of Markdown files, each with YAML frontmatter whose only required field is a non-empty `type` string. No runtime, no SDK, no binary format, no JSON schema. Relationships are plain relative Markdown links between Concept IDs (= file path minus `.md`). Reserved files: `index.md`, `log.md`. Consumers must tolerate unknown types/keys/broken links.

**Caveats / blunt flags:**
- v0.1 is an explicitly unstable Draft. Pin to a commit. Do not treat as a stable contract.
- This is *almost exactly what Tesserae's vault projection already emits* (one MD file + YAML frontmatter + links per node). The only real work is link-format translation (Obsidian wikilinks → relative MD links) and `type` mapping.
- "Google standard" framing is overblown — it's one team's draft convention, not a Google-wide or industry standard.

**Recommendation: build-minimal (export only). Skip import for now.**
Justification: export is a thin reprojection of an artifact we already produce; import requires a merge/dedup entrypoint that doesn't exist and has lossy type-mapping — speculative until someone actually hands us an OKF bundle.

**Smallest implementation (export):**
- New `tesserae/okf.py`: `write_okf_bundle(graph: ResearchGraph, out_dir)`. Reuse `GraphMarkdownProjector` helpers from `tesserae/markdown_projection.py` (`directory_for_node`, `slugify`, `render_node_page`). Differences from the vault projector: (a) frontmatter `type` from the node's ontology label, drop `node_id`/dataview/bridge keys (or namespace them under one `x_tesserae:` key to keep round-trip without polluting bundles); (b) translate wikilinks → relative MD links to Concept IDs; (c) emit `index.md` per directory (reuse `render_index`) and one top-level `log.md` from the session/compile timeline.
- Wire CLI: add `_handle_export_okf` beside `harness`/`graphiti`/`site` in `cli.py` `_build_export_parser` (~line 2404).
- Keep volatile/wall-clock data OUT (byte-idempotence blind spot) — `log.md` is the one allowed time-stamped file; isolate it.

**New dependency:** none. PyYAML/markdown already in tree.

**Effort:** S–M (export only ~1–1.5 days). Import would add M and an open design question — defer.

---

## Feature 2 — nicosuave/memex (fast local transcript search)

**What it actually is (verified):** Real. Rust CLI (v0.3.1, MIT) for BM25 + optional local-embedding search over Claude Code / Codex / OpenCode JSONL transcripts. Ships a TUI and a background index-service. Embeddings via ONNX (`ort` 2.0-rc) + fastembed default on.

**Caveats / blunt flags:**
- Heavy: pulls a full ONNX runtime + Rust/Cargo toolchain + a downloaded embedding model. Shipping cross-platform inside Tesserae (a Python tool) is a real packaging burden.
- It maintains a **second index** over the same logs, with **memex-internal** session_id/doc_id needing a path/timestamp join back to Tesserae's session records.
- The research note's own caveat is decisive: **if we only need fast lexical transcript search, SQLite FTS5 (already used in Tesserae) covers it without any of this.**

**Recommendation: skip the dependency; build the FTS5 version ourselves (build-minimal, in-house).**
Justification: an external Rust binary + ONNX stack to get lexical search we can get from FTS5 over data we already store is the textbook over-engineering case. Reuse, don't adopt.

**Smallest implementation (in-house):**
- Transcript turns currently live as an opaque `session_json` blob in `harness_sessions_db.py` (~line 248) — no per-turn rows, no FTS.
- Add one FTS5 virtual table over turns, populated on upsert in `harness_sessions_db.py`. Reuse existing `site/search.py` tokenize/BM25 for parity.
- Add `GET /api/transcript-search` to `serve.py` `build_ask_aware_handler` (~line 92); follow the existing `_clip_origin` origin-gating pattern. Hydrate a clicked result via the full `session_json`.
- UI: add a debounced fetch on the session-history page (`site/sessions.py` `session_search_entries`, ~line 285).

**New dependency:** none (FTS5 ships with stdlib sqlite3). Revisit memex only if users demand *semantic* transcript search — and even then, prefer Tesserae's existing embedding infra.

**Effort:** M (FTS5 migration + populate-on-upsert + endpoint + UI fetch ~2–3 days).

---

## Feature 3 — Jonasb8/memex (decision-knowledge extraction ideas)

**What it actually is (verified):** Real. PyPI `memex-oss`, **AGPL-3.0**, ~8 stars. Narrow tool: extracts architectural decisions from merged GitHub PRs/ADRs into reviewable bot-PR markdown decision records. Typed schema, three-gate extraction (regex prefilter → LLM → confidence cutoff), self-reported confidence-as-rationale-completeness, `revisit_signals`.

**Caveats / blunt flags:**
- **AGPL-3.0 — do NOT copy any source.** Reimplement ideas independently only.
- It's single-purpose (GitHub PR capture). Its delivery mechanism (GitHub Action + bot PR) fights Tesserae's regenerate-projections model. Distill ideas, not the workflow.
- Confidence is LLM-self-graded — a flag, never a truth guarantee.

**Recommendation: distill-a-subset (3 cheap, high-leverage ideas). Skip the rest.**
Justification: Tesserae already has the heavy machinery (typed graph, distill pipeline, embeddings, MCP). Only the schema/prompt-discipline ideas are worth lifting.

**The subset worth building (smallest, ordered by value):**
1. **Confidence + rationale on extracted facts.** Add `confidence: float` + `confidence_rationale: str` to the session/fact extraction schema; surface in `search_facts`/`fresh_insights`/`compile_context` so low-rationale items are flagged not trusted. Strict anti-hallucination prompt line ("do not infer motivation not stated") directly hardens the known extraction-quality blind spot. Touch: extraction prompt/schema in `tesserae/ingest`, `memory/distill.py`, MCP output formatting in `mcp_server.py`.
2. **Cheap regex prefilter before the LLM.** A `is_low_signal` gate (skip trivial/chore inputs) to cut LLM cost — re-tune for session inputs, don't copy their PR-tuned patterns verbatim. Touch: `tesserae/ingest` extraction entrypoint.
3. **`revisit_signals` → node property + re-surfacing hook.** Store captured "revisit when X" phrases as a node property feeding `fresh_insights`/`timeline` staleness. Serves pillar 2. Touch: node property in `research_graph.py`, surfacing in `memory/decay.py` / `fresh_insights`.

Skip: structured-embed-text (nice but lowest-value; only if retrieval quality is measured as a problem), bot-PR review workflow (wrong model for Tesserae).

**New dependency:** none. (They use `instructor`; Tesserae already has its own LLM path — don't add it.)

**Effort:** M for all three; #1 alone is S and the highest leverage — do it first, independently of the others.

---

## Feature 4 — MemEx (Databricks) "programmable scratchpad"

**What it actually is (verified from blog only):** A *within-session agent runtime* (code-as-action: each turn the agent runs Python in a persistent typed kernel; only `print()`ed output becomes tokens). **No public code repo exists** — everything is inferred from one blog post, so any "spec" is interpretive.

**Caveats / blunt flags:**
- Category mismatch: this is a caller-side runtime, Tesserae is a build/retrieval engine. We **cannot** force what stays out of the agent's context — we only control what our MCP tools *return*. Copying the kernel idea is scope creep and duplicates Claude Code / `ctx_execute`.
- The only portable, in-scope idea is the **read discipline**: return handles + a small preview instead of full text dumps.

**Recommendation: build-minimal (read-discipline only) — and gate it behind a measured need.**
Justification: the portable 20% (preview + handle) is a genuine context-budget win on big `compile_context`/`search_nodes` payloads; the kernel maximal version is not Tesserae's job.

**Smallest implementation:**
- Add an optional `budget`/`limit` to `compile_context` so it emits a bounded projection instead of the whole subgraph (this alone captures most of the win with zero new state).
- *Only if that's insufficient:* add result-handle IDs to `search_nodes`/`search_facts` payloads + a `get_handle(id, slice=...)` MCP tool. This adds server statefulness (handle lifetime, invalidation on recompile) — real complexity, so don't build it speculatively.
- Touch: `tesserae/mcp_server.py` (tool payloads), `retrieval/hybrid.py`.

**New dependency:** none.

**Effort:** S for the `budget` bound; +M and statefulness for full handles (defer until proven needed).

---

## Recommended order & open questions for the user

**Suggested build order (cheapest, highest-leverage first):**
1. Feature 3 #1 — confidence + rationale on extractions (S, hardens a known blind spot, no deps).
2. Feature 4 — `compile_context` budget bound (S, pure win, no deps).
3. Feature 2 — in-house FTS5 transcript search (M, no deps, real user feature).
4. Feature 1 — OKF export (M, no deps, low-friction reprojection).
5. (Optional) Feature 3 #2/#3 — prefilter + revisit_signals.

**Decisions needed before writing code:**

1. **OKF: export-only, or do you actually need import?** I recommend export-only — import has lossy type-mapping and needs a merge/dedup entrypoint that doesn't exist. Confirm no inbound OKF bundle is imminent, or this scope grows.
2. **OKF frontmatter for Tesserae-specific data:** drop our `node_id`/dataview/bridge keys (clean bundles, lossy re-import) vs. namespace them under one `x_tesserae:` key (round-trippable, slightly non-idiomatic)? I lean namespaced. Your call.
3. **Transcript search: confirm lexical-only is enough.** I'm recommending FTS5 and explicitly NOT adopting nicosuave/memex (Rust + ONNX + second index). Only override if you specifically want *semantic* transcript search now.
4. **Scratchpad (Feature 4): how far?** I'm proposing the cheap read-discipline (`budget` bound) and explicitly *deferring* the stateful `get_handle` API until measured as needed. Confirm you don't want the full programmable-kernel direction — that's out of scope for a build engine.

**Non-negotiables flagged for any of these:**
- AGPL-3.0 on Jonasb8/memex — reimplement, never copy.
- Keep all wall-clock/mutable/confidence/revisit state in sidecars (`node_memory` pattern), never in byte-idempotent `graph.json`. OKF `log.md` is the one isolated exception.

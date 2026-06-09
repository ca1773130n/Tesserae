# `tesserae ingest` — single source → KB, incremental + reconciled

- **Status:** Approved design (brainstorming complete) — ready for implementation plan
- **Date:** 2026-06-10
- **Topic:** First-class `ingest <file|url>` command that merges one document or URL
  into the knowledge base without a manual full recompile, while guaranteeing the
  result is identical to a full compile.

---

## 1. Problem

Today there is no ergonomic way to point Tesserae at a *single* document file or
URL and have it merged into the KB. The closest paths are:

- `tesserae compile <path>` — ad-hoc ingest-only of explicit paths (`_handle_compile_paths_ingest`,
  `cli.py`). It *merges* (`BatchIngestRunner` "merges, never prunes"), but defaults to
  `changed_only=False`, i.e. a **true full recompile** that re-extracts the whole tracked
  corpus — exactly the "refresh all nodes/edges" cost we want to avoid.
- `tesserae ingest` — already taken: it is the **code-graph** extractor (Python AST), not documents.
- No URL ingestion exists. The only `SourceLoader` is `FilesystemSourceLoader` (markdown on disk).

The truly-incremental "prior graph + delta" merge already exists as the
`incremental_compile` path in `project.py`. It is **byte-for-byte parity-tested**
against full compiles in `tests/test_incremental_parity.py` (additive K=1/5/21,
content-reduction, file-deletion, rename, alias-identity, both-endpoints-move) and
carries an automatic over-cap → full-recompile safety valve (`incremental_reextract_cap`,
default 50). It is gated **off by default** pending a few residual edge-case gaps and has
**no ergonomic front door**.

**This feature is therefore mostly wrapping + guaranteeing existing, tested machinery —
not building incremental merge from scratch.**

## 2. Goals / Non-goals

**Goals (v1 / "Approach B"):**
- `tesserae ingest <input>...` accepting **local file paths and `http(s)` URLs**.
- Merge a new source into the KB via the **incremental** compile path in the common
  case (no full re-extraction of unchanged docs).
- **Correctness contract:** the resulting `graph.json` / vault / site are byte-identical
  to a full `compile(corpus ∪ X)`, whichever internal path runs.
- Fetched URLs become **first-class tracked sources** (re-ingest is idempotent; a future
  full compile reproduces them identically).

**Non-goals (v1):**
- Instant-write / async reconcile (that is Approach C / v2 — see §10).
- New approximate merge algorithm (we ride the parity-tested incremental path).
- Globally flipping `incremental_compile` on for the `compile` command (stays off).
- Streaming/remote source loaders beyond persist-then-ingest.

## 3. Decisions captured during brainstorming

1. **Speed vs correctness:** *Correct & reconciled* — result must equal a full compile.
   (Resolved via incremental-where-proven + automatic full-recompile fallback.)
2. **Source types in v1:** *Local file **and** URL.*
3. **Phasing:** *Approach B now, Approach C (instant write + background reconcile) next.*
4. **URL handling model:** *Persist-then-ingest* (fetch → markdown file in the tracked
   corpus → run normal pipeline) rather than a bespoke streaming loader.
5. **No naming collision (verified):** the existing code-graph ingest is the *namespaced*
   `tesserae code ingest` (a subcommand under `code`, built by `_build_code_parser`).
   The **top-level `tesserae ingest` is free** — `_NEW_DISPATCH` has no `ingest` key — so the
   new document command is added as a new top-level route with **no breaking change** and
   nothing renamed.

## 4. Architecture overview

```
tesserae ingest <input>
 ├─ resolve input
 │    ├─ local path → use as-is
 │    └─ http(s) URL → fetch_to_source() → data/ingested/<slug>.md   ([ingest-url] extra)
 ├─ register as a tracked source (data/ is already a default source root)
 ├─ CORRECTNESS GUARD: pure-additive, no winner-change, under cap?
 │    ├─ yes → incremental compile (changed_only=True)
 │    │         strip generated layer · re-extract ONLY the new doc · merge ·
 │    │         re-run ALL global passes · write artifacts
 │    └─ no  → full recompile (changed_only=False)   ← existing safe path
 └─ report: nodes/edges merged · path taken (incremental│fallback) · source path
```

The only genuinely new code is **one fetch module + one thin orchestrator + CLI wiring +
one optional dependency extra.** Everything load-bearing (incremental merge, reconcile
passes, over-cap fallback, artifact writing) is existing, parity-tested machinery.

## 5. Command surface

```
tesserae ingest <input>...        # input = local path OR http(s) URL (one or more)
  --title TEXT        # name override (useful for URLs / bare files)
  --source-kind KIND  # override classification (default: auto)
  --exact             # force full recompile, skip the fast path
  --dry-run           # fetch + extract + show the merge delta; write nothing
```

Output reports: nodes/edges merged, **which path ran** (`incremental` vs
`full-recompile fallback`), and the persisted source path.

**No collision (verified in `tesserae/cli.py`):** the Python-AST code-graph ingest is
`tesserae code ingest` (registered inside `_build_code_parser`, `_handler="_handle_code_ingest"`).
The top-level dispatch table `_NEW_DISPATCH` has **no `ingest` key**, so the new document
command is registered as a new top-level route (`"ingest": _route_ingest`) — purely additive,
non-breaking. `tesserae code ingest` (code) and `tesserae ingest` (documents) coexist in
distinct namespaces; nothing is renamed.

## 6. Components

**New (small):**
- `tesserae/ingest/fetch.py` — `fetch_to_source(url, dest_dir) -> Path`: HTTP GET,
  HTML→markdown conversion, **arxiv special-case** (reuse existing `arxiv_id` awareness in
  `lint.py` / extraction), write file + provenance front-matter. **Behind a new
  `[ingest-url]` optional extra**; import-guarded with an actionable
  `pip install tesserae[ingest-url]` error when missing. Network is never touched in tests
  (always mocked).
- `tesserae/ingest/orchestrator.py` — the ingest flow: resolve inputs → (URL?
  `fetch_to_source`) → ensure tracked → run the guard → drive compile → report.
  **This is the B→C seam:** v1 calls compile synchronously; v2 splits it into "fast
  approximate write now" + "enqueue reconcile."
- CLI: new top-level `ingest` route (`_route_ingest` + `_build_ingest_parser`) added to
  `_NEW_DISPATCH`, wired to a `_handle_ingest`-style handler that calls the orchestrator.
  No rename of the existing `tesserae code ingest`.

**Reused as-is:**
- `FilesystemSourceLoader`; `Project.compile` (incremental path, `changed_only=True`);
  `BatchIngestRunner` + manifest (sha256 changed-only); `_strip_generated_layer`;
  provenance sidecar + `_provenance_ready`; `incremental_reextract_cap` over-cap fallback;
  `merge_graphs`; `_write_artifacts` (graph.json + vault + site + sqlite); existing
  extractors (`ResearchGraphExtractor` deterministic; `llm_extractor` / `selective_extractor`
  optional).

### Persisted source convention

`data/ingested/<slug>.md` (the `data/` tree is already an auto-discovered source root),
with provenance front-matter:

```markdown
---
source_url: https://arxiv.org/abs/2401.12345
fetched_at: 2026-06-10T12:34:56Z
content_sha256: <sha256 of fetched body>
arxiv_id: 2401.12345        # when detected
---
<converted markdown body>
```

## 7. Correctness guard (the heart of B)

**Contract:** `ingest(X)` output is byte-identical to a full `compile(corpus ∪ X)`.

Incremental is parity-proven for pure-additive cases. Residual gaps and their relevance to
ingest (which is **purely additive** — never edits or deletes existing sources):

| Residual gap | Triggerable by a new-doc ingest? | Handling |
|---|---|---|
| producer-layer removal | No (ingest removes nothing) | n/a |
| over-cap (> `incremental_reextract_cap`) | Only if the new doc touches a heavily co-owned entity | **existing** over-cap → full-recompile fallback |
| multi-owner winner-change | **Yes** — a new doc can become the winning source of an existing entity | **new pre-flight guard** → fall back |
| untracked post-pass edges | Audit required | covered by golden-parity test; fall back on failure |

**Guard logic:** extract the new doc; if its entities would change any existing node's
winning source / payload, or exceed the cap, **fall back to full recompile**. Otherwise take
the fast incremental path.

**Conservative-by-default:** v1 ships the guard erring toward fallback on *any* ambiguity,
so correctness holds from day one. The fast path is taken only for provably-safe additive
ingests. *Correct first, faster over time* as the gap-audit narrows the unsafe set.

**Scoping of the experimental flag:** `ingest` opts **its own invocation** into the
incremental path under this guard; the global `incremental_compile` flag stays **off** for
the `compile` command.

**Ship gate:** until the golden-parity test (§9) is green, `ingest` defaults to `--exact`
(full recompile). This is the honest correctness gate — no fast-path claims before the proof.

## 8. Error handling

- **Fetch:** non-2xx / unsupported content-type / timeout → clear error, nothing written,
  non-zero exit. Sane default timeout.
- **`[ingest-url]` missing:** actionable `pip install tesserae[ingest-url]` hint.
- **HTML→md conversion failure:** store the raw text body + warning (the deterministic
  extractor tolerates plain markdown) rather than aborting.
- **Re-ingest of unchanged URL/content** (sha256 match in manifest): friendly
  "already ingested, unchanged" no-op.
- **Compile error:** the existing pipeline guarantees **no partial `graph.json`**. A
  persisted-but-not-yet-compiled file is tracked and recovered on the next ingest/compile.
- **Atomicity:** persist is idempotent and tracked; a failure after fetch but before compile
  leaves a recoverable tracked file (no corruption).

## 9. Testing (TDD)

- **Golden parity (the contract):** `ingest(X)` `graph.json` + vault + site byte-identical
  to a full `compile(corpus ∪ X)`, across an input matrix: disjoint-add, colliding-add
  (no winner change), winner-change, over-cap, URL-fetched. Extends the
  `tests/test_incremental_parity.py` methodology to the **ingest** entry point.
- **Guard unit tests:** each gap condition → asserts the correct path decision (fast vs
  fallback).
- **`fetch_to_source` unit tests:** network **always mocked** — arxiv, generic HTML, PDF,
  error/content-type cases, provenance front-matter, slug generation.
- **CLI tests:** arg parsing; `ingest` / `ingest-code` rename (+ optional deprecated alias)
  back-compat; `--exact` / `--dry-run` / `--title`.
- **i18n:** the new `ingest` command documentation must ship in all 7 languages
  (ko/zh/ja/ru/es/fr/de) — project invariant.

## 10. Phasing

- **v1 — Approach B (this spec):** synchronous. Orchestrator's `compile()` call is the seam.
- **v2 — Approach C (future):** orchestrator gains `--async`: (1) instant approximate write
  (append the new doc's extracted subgraph, skip passes) → queryable in <1s; (2) enqueue
  the B reconcile to converge. Requires a graph "dirty" marker, a background runner (reuse
  the daemon `_run_pipeline`?), and read-time pre-reconcile awareness. **Additive to B, not a
  rewrite** — the clean orchestrator seam is what makes this cheap.

## 11. Risks & open questions

- **R1 — incremental correctness for ingest:** the central risk. Mitigated by the
  conservative guard + over-cap fallback + the golden-parity ship gate. The plan's first
  task is the **gap-audit**: prove (with tests) that pure-additive ingest only reaches the
  parity-proven path or the fallback — never a silent divergence.
- **R2 — `[ingest-url]` dependency choice:** pick an HTTP client + HTML→markdown converter
  that keeps the base install lean (base is currently only pydantic/networkx/rich). Decide
  in the plan (candidates to evaluate: stdlib `urllib` + a minimal HTML→md, vs `httpx` +
  `markdownify`/`trafilatura`). PDF handling may be deferred or behind the same extra.
- **R3 — resolved (no rename):** top-level `ingest` is free (`_NEW_DISPATCH` has no `ingest`
  key; the code-graph ingest is the namespaced `tesserae code ingest`). The new command is
  purely additive — no rename, no deprecation alias, no blast radius.
- **Q1 — multiple inputs:** confirm `ingest a.md b.md https://…` semantics (treat as a
  batch add → single incremental compile over all new sources).
- **Q2 — slug collisions:** `data/ingested/<slug>.md` collision policy (suffix with short
  content hash).

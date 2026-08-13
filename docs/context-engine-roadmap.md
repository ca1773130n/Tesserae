# Tesserae → Context Engine — Phased Roadmap

<!-- translations:start -->
<p align="center"><a href="i18n/context-engine-roadmap.ko.md">한국어</a> · <a href="i18n/context-engine-roadmap.zh.md">中文</a> · <a href="i18n/context-engine-roadmap.ja.md">日本語</a> · <a href="i18n/context-engine-roadmap.ru.md">Русский</a> · <a href="i18n/context-engine-roadmap.es.md">Español</a> · <a href="i18n/context-engine-roadmap.fr.md">Français</a> · <a href="../i18n/context-engine-roadmap.de.md">Deutsch</a></p>
<!-- translations:end -->
Derived from [`context-engine-audit.md`](./context-engine-audit.md). Turns the
7-step build order into sequenced phases with dependencies, concrete scope,
and acceptance criteria.

**North star:** a continuously-running engine that monitors sessions, ingests
knowledge autonomously, self-improves its base, and serves on-demand,
agent-ready context — replacing today's manual batch-compile CLI.

> **Status as of v0.5.0 (2026-06-06):** Phases 0–6 have **shipped**. The engine
> spine (P0 pipeline orchestrator, P1 supervisor daemon, P2 live session
> monitor) is in `tesserae/engine/`; P3 incremental-compile infrastructure
> landed but stays **flag-OFF / experimental**; P4 self-improvement persists via
> the `node_memory` sidecar (numeric recurrence confidence, supersede
> default-on); P5 real default embeddings shipped (Track B); and **P6 — the
> On-Demand Context Compiler — is the v0.5.0 headline feature**. Phase 7 (unify
> serve + watch + deploy + lifecycle tests) remains **open**. Per-phase status
> is noted inline below. See the [v0.5.0 release notes](./release-notes/v0.5.0.md).

## Dependency shape

```
P0 Pipeline orchestrator (extract refresh-chain into code)
        │
P1 Supervisor daemon (the loop) ──────────────┐
        │                                      │
   ┌────┴─────────────── Track A ──────┐   ┌── Track B (parallelizable) ──┐
   P2 Live session monitor             │   P5 Real default embeddings
   P3 Incremental/streaming compile    │   P6 On-demand context compiler
   P4 Self-improvement persistence ────┘   (P6 depends on P5)
        │                                      │
        └──────────────► P7 Unify serve+watch+deploy + lifecycle tests ◄──┘
```

Track A (realtime ingestion) and Track B (agent-facing output) can run in
parallel once P1 lands. P7 converges them.

---

## Phase 0 — Pipeline orchestrator (de-risking foundation) ✅ Shipped v0.5.0

**Goal:** Move the refresh pipeline out of slash-command markdown into a
first-class in-process orchestrator that the daemon, CLI, and MCP all call.

> **Shipped in v0.5.0** as `tesserae/engine/pipeline.py` (part of the engine
> spine, merged in Phases 1–3 of the engine work).

- **Why now:** Every later phase needs one shared code path for
  `ingest → compile → project → publish`. Today that sequence only exists as
  prose in a skill. Nothing else can be automated until it's callable.
- **Scope:** New `tesserae/engine/pipeline.py` (a `Pipeline` object wrapping the
  current `sessions discover --import → compile → obsidian-sync` chain). Route
  `cli.py` subcommands through it. Begin decomposing the ~2000-line
  `project_main` god-dispatcher into a command table (mechanical, no behavior
  change).
- **Deliverables:** `Pipeline.run(steps, changed_only=…)`; CLI delegates to it;
  unit tests for step sequencing + failure propagation.
- **Acceptance:** `tesserae refresh` exists as code (not a skill) and
  reproduces the markdown chain byte-for-byte on the demo corpus.
- **Risk:** Low. Pure refactor; existing tests guard behavior.
- **Audit findings closed:** "refresh lives in a skill," "cli god-dispatcher."

## Phase 1 — Supervisor daemon (the engine loop) ✅ Shipped v0.5.0

**Goal:** A supervised long-running process that owns one event loop and drives
`Pipeline` on triggers, with real lifecycle handling.

> **Shipped in v0.5.0** as `tesserae/engine/daemon.py` (engine spine, Phases 1–3).

- **Why now:** This is the spine. The audit's single biggest gap. Everything
  "continuous/autonomous" hangs off it.
- **Scope:** New `tesserae/engine/daemon.py` — event loop, a trigger queue,
  debounce/coalescing, `SIGTERM`/`SIGINT` graceful shutdown, pidfile, structured
  logging. `tesserae engine` / `tesserae daemon` CLI entry. Replace the bare
  `KeyboardInterrupt` death in `watch.py`/`vault_watch.py` by folding them in as
  *trigger sources* feeding the queue.
- **Deliverables:** Daemon that runs indefinitely, coalesces bursts into one
  pipeline run, shuts down cleanly; launchd/systemd example unit.
- **Acceptance:** Edit a source file → daemon coalesces and runs one
  `compile(changed_only)` within the debounce window; `SIGTERM` exits 0 with no
  orphaned threads; survives a pipeline exception without dying.
- **Risk:** Med — concurrency/shutdown correctness. Mitigate with a
  single-threaded asyncio core + explicit task supervision.
- **Audit findings closed:** "no daemon," "continuous = sleep poller," watcher
  `KeyboardInterrupt` death, no signal handling.

## Phase 2 — Live session monitor (Pillar 1) ✅ Shipped v0.5.0

**Goal:** Tail live harness transcripts and ingest turns as sessions run,
replacing post-hoc `sessions discover --import`.

> **Shipped in v0.5.0** as `tesserae/engine/session_tail.py` (engine spine,
> Phases 1–3).

- **Why now:** Needs P1's loop to feed. Delivers the "session monitoring"
  pillar.
- **Scope:** New session-tail trigger source (watch `~/.claude` / `~/.codex`
  JSONL append events) → enqueue. Turn-level incremental extraction in
  `session_graph*.py` so a new turn doesn't invalidate the whole-session cache.
  Index/append store for `harness_sessions` (retire the full-rescan glob).
- **Deliverables:** Daemon ingests a session's turns within seconds of them
  being written; `test_session_tailer.py`.
- **Acceptance:** Start a live agent session in a watched project → new findings
  appear in the graph without any manual command; turn-level cache hit ratio
  measured > whole-session re-extract.
- **Risk:** Med — JSONL formats differ across harnesses; partial-line reads.
- **Audit findings closed:** post-hoc session scan, whole-session cache
  invalidation, flat glob store.

## Phase 3 — Incremental / streaming compile through the GraphStore port ⚙️ Infrastructure shipped v0.5.0 (flag-OFF / experimental)

**Goal:** Replace the fragile `changed_only` graph-eviction patch with a
designed incremental layer flowing through `ports/graph_store.py`.

> **Infrastructure shipped in v0.5.0** (provenance sidecar, `GraphStore` delete
> surface, persistent `url_resolver` async runtime), but the `incremental_compile`
> feature flag **stays OFF / experimental** — Codex review surfaced
> multi-owner / producer-lifecycle / cap-fallback gaps. Byte-parity against
> full-compile is proven for the covered paths; the flag is opt-in until the
> remaining gaps close. v0.5.0 also fixed two real compile bugs uncovered here:
> changed-only idempotence and the injected-store contract.

- **Why now:** Continuous ingestion (P2) makes the current
  reload-strip-evict-merge workaround a correctness liability (the documented
  "2400→1700 nodes" footgun). Self-improvement (P4) needs per-node upserts.
- **Scope:** Make the standalone pipeline flow through `GraphStore` (today it
  bypasses ports straight to JSON). Per-node upsert/delete with provenance +
  freshness timestamps. Converge persistence toward one source of truth (audit:
  JSON artifact vs SQLite; Kuzu was ruled an export in v0.32 and is no longer a
  persistence format). Fix `url_resolver.py` `asyncio.run`-per-call
  (persistent async runtime).
- **Deliverables:** Incremental compile that adds/updates/removes only changed
  nodes correctly; per-node `first_seen_at`/`last_updated_at`.
- **Acceptance:** A 21-file edit updates exactly the affected nodes (no
  collapse); byte-identical full-compile parity retained as a golden test.
- **Risk:** High — touches the core data model. Gate behind a feature flag;
  diff against full-compile output until trusted.
- **Audit findings closed:** fragile `changed_only`, ports bypassed, asyncio
  per-call, three persistence formats, no per-node freshness.

## Phase 4 — Activate & persist self-improvement (Pillar: self-improving) ✅ Shipped v0.5.0

**Goal:** Make the knowledge base actually evolve in place, on by default,
persisted at compile time.

> **Shipped in v0.5.0** via the `node_memory` sidecar (`tesserae/memory/`):
> default-on **supersede** with a deterministic verdict (both directions) plus
> downstream suppression, and **recurring-insight reinforcement** as a **numeric
> recurrence confidence** surfaced in output (cross-session frequency →
> `TemporalFactProjector`), replacing the prior coarse string heuristic. Decay,
> contradiction, and feedback-guidance scaffolding land in the same sidecar.

- **Why now:** Depends on P3's per-node upserts. Closes the most-untested slice.
- **Scope:** Persist **decay** scores at compile (`memory/decay.py` no longer
  query-time-only); bump `access_count`/`last_accessed_at` on MCP reads.
  Default-on **supersede** with downstream *suppression* of obsolete content
  (not just edge-append). Add **contradiction resolution** (upgrade `lint.py`'s
  detection into a confidence-arbitrated pass). **Recurring-insight
  reinforcement** (cross-session frequency → numeric confidence). Wire
  **schema-drift** apply-path and **feedback guidance** into extraction
  (deterministic path currently ignores it). Embedding-based supersede candidate
  gen (retire lexical Jaccard).
- **Deliverables:** Each pass runs in the default pipeline and writes back; a new
  `tests/` suite covering decay/supersede/feedback/drift/contradiction.
- **Acceptance:** Re-stating a fact across sessions raises its confidence; a
  superseded fact stops appearing in context output; decay scores persist and
  shift across runs; the self-improvement suite is green (currently zero tests).
- **Risk:** Med — behavior changes to extraction output; guard with golden
  fixtures.
- **Audit findings closed:** the entire Pillar-2 table.

## Phase 5 — Real default embeddings (Track B foundation) ✅ Shipped v0.5.0

**Goal:** Stop shipping a deterministic hash-bucket pseudo-embedding as the
default "semantic" lane.

> **Shipped in v0.5.0** as Track B: a real default `Model2VecBackend`, a
> **fail-loud** `active_embedding_backend` (no silent blake2b downgrade), the
> semantic-backend flag surfaced in `embedding_status`, and a cosine floor that
> lets the embedding lane admit candidates (not just re-rank).

- **Why now:** P6's context compiler is only as good as retrieval. Independent of
  the daemon — can start as soon as P0 lands.
- **Scope:** Ship a real default embedding backend (or make `auto` fail loud
  instead of silently downgrading to blake2b in `retrieval/hybrid.py`). Let the
  embedding lane generate candidates (not just re-rank) once embeddings are real.
- **Deliverables:** Default install yields genuine semantic retrieval, or an
  explicit, visible "running on hash stub" warning.
- **Acceptance:** Paraphrase/synonym queries surface relevant nodes BM25 misses;
  retrieval quality measured on a small labeled set vs the hash baseline.
- **Risk:** Med — dependency weight / offline install. Offer a tiered default.
- **Audit findings closed:** hash-bucket default, embedding-lane candidate gate.

## Phase 6 — On-demand context compiler (Pillar 3) ✅ Shipped v0.5.0 (headline feature)

**Goal:** The headline feature — "give me context on X" → a tailored, cited,
agent-ready doc.

> **Shipped in v0.5.0 as the headline feature.** A pure `compile_context`
> pipeline in `tesserae/context_compiler.py` returns an in-memory
> `ContextBundle` of `ContextCitation`s (query/seeds → PPR + hybrid search →
> depth-bounded k-hop neighborhood → wiki-body assembly → optional LLM synthesis
> with graceful fallback → budget control). Exposed as the MCP `compile_context`
> tool and the `tesserae context` CLI subcommand; `node_context` now has
> a `use_ppr` ranked path; topic-scoped `llms.txt` export slices ship via
> `slice_export_context_for_topic`.

- **Why now:** Depends on P5 (retrieval quality). Benefits from P4 (cleaner
  base). The product's core value proposition.
- **Scope:** New `tesserae/context_compiler.py`: query/seeds → PPR + hybrid
  search → ranked k-hop neighborhood walk → assemble wiki bodies → optional LLM
  synthesis → one scoped markdown doc with citations + budget control. Expose as
  MCP `compile_context(query|seeds, depth, budget)` and `tesserae context …`
  CLI. Make `agent_harness` topic-scoped; route `node_context` through PPR;
  topic-scoped `llms.txt` export slices.
- **Deliverables:** A tool that returns a downloadable, cited context bundle for
  any query; tests asserting the bundle shape + citation integrity.
- **Acceptance:** `compile_context("X")` returns a coherent multi-node doc whose
  citations all resolve; harness brief regenerates per topic rather than
  hard-coded top-12.
- **Risk:** Med — synthesis quality; keep a deterministic no-LLM assembly mode.
- **Audit findings closed:** "on-demand doc generation does not exist,"
  query-scoped synthesis, static harness, unranked `node_context`, whole-corpus
  exports.

## Phase 7 — Unify serve + watch + deploy + lifecycle tests ⏳ Open (post-v0.5.0)

**Goal:** One supervised process serves the site, recompiles on change, and
publishes continuously; the lifecycle layer gets test coverage.

> **Open as of v0.5.0.** The convergence phase remains the next milestone; the
> daemon (P1) and the output side (P6) it folds together are now both in place.

- **Why now:** Convergence. Needs P1 (daemon) and the output side (P6) to be
  worth continuously publishing.
- **Scope:** Fold `serve.py` (blocking `TCPServer`) and `deploy.py` (manual git
  push) into the daemon so serve + watch + publish share one supervisor.
  Continuous/debounced publish. Add the missing `test_watch`/`test_serve`/
  daemon-lifecycle tests. Delete the deprecated `frontend.py` dead module. Wire
  `review_workflow.py`'s stringly-typed TODO loop into a real apply path.
- **Deliverables:** `tesserae engine --serve --publish` runs the full loop;
  lifecycle test suite; dead code removed.
- **Acceptance:** A source edit propagates to a live-served page and (optionally)
  a published deploy without manual commands; lifecycle tests green.
- **Risk:** Low–Med — mostly integration.
- **Audit findings closed:** serve/watch/deploy split, manual deploy, deprecated
  `frontend.py`, review-loop stub, missing lifecycle tests.

---

## Sequencing summary

| Phase | Theme | Depends on | Parallelizable with | Status |
|---|---|---|---|---|
| P0 | Pipeline orchestrator | — | — | ✅ Shipped v0.5.0 |
| P1 | Supervisor daemon | P0 | — | ✅ Shipped v0.5.0 |
| P2 | Live session monitor | P1 | P5 | ✅ Shipped v0.5.0 |
| P3 | Incremental compile | P1 | P5 | ⚙️ Infra shipped v0.5.0 (flag-OFF) |
| P4 | Self-improvement persistence | P3 | P5, P6 | ✅ Shipped v0.5.0 |
| P5 | Real embeddings | P0 | P2, P3, P4 | ✅ Shipped v0.5.0 |
| P6 | On-demand context compiler | P5 | P2, P3, P4 | ✅ Shipped v0.5.0 (headline) |
| P7 | Unify serve/watch/deploy | P1, P6 | — | ⏳ Open |

**Minimum viable engine:** P0 + P1 + P2 + P3 — a running daemon that watches
live sessions and incrementally compiles. **Differentiated product:** add P5 +
P6 (on-demand agent context). **Polished:** P4 + P7.

# Tesserae as a Context Engine — Gap Analysis

<!-- translations:start -->
<p align="center"><a href="i18n/context-engine-audit.ko.md">한국어</a> · <a href="i18n/context-engine-audit.zh.md">中文</a> · <a href="i18n/context-engine-audit.ja.md">日本語</a> · <a href="i18n/context-engine-audit.ru.md">Русский</a> · <a href="i18n/context-engine-audit.es.md">Español</a> · <a href="i18n/context-engine-audit.fr.md">Français</a> · <a href="../i18n/context-engine-audit.de.md">Deutsch</a></p>
<!-- translations:end -->
> **Mission (2026-06-02):** Tesserae is a *context engine* — it generates
> agent-ready context by reconstructing a **self-improving** knowledge base via
> three pillars: **(1) session monitoring**, **(2) autonomous proactive
> ingestion**, and **(3) on-demand docs**. Knowledge must be **realtime and
> evolving**, ready to hand to agents.

This document audits the current codebase against that mission. It is the
output of a four-way parallel review (ingestion/sessions, self-improvement,
output/agent-facing, orchestration/lifecycle).

## Verdict in one line

Tesserae today is a **mechanically healthy, well-tested batch CLI compiler**.
Against the context-engine vision it is **manual-trigger + post-hoc +
git-HEAD-gated** on all three pillars. The machinery to build the engine
already exists as primitives — what's missing is the **continuous,
self-driving layer** that composes them.

The single biggest missing piece across every slice: **a long-running
supervisor/daemon that owns one event loop** and drives session-tailing,
ingestion, incremental compile, and publish autonomously. Everything else is
incremental on top of that.

---

## Pillar 1 — Session monitoring → **post-hoc, not live**

| Status | Finding | What's needed |
|---|---|---|
| gap | Session capture is a post-hoc scan: `discover_harness_sessions()` walks finished transcripts only when a human runs `sessions discover --import` or `compile`. `compile` deliberately refuses to scan `~/.claude/projects/` (latency). | A **tailer** that watches harness JSONL files and ingests turns as the session runs. |
| gap | The only true watcher (`watch.py WatchLoop`) covers **source markdown**, polls every 2s, and fires a full `compile`. It watches neither sessions nor code. | Extend to session + source-tool triggers under one supervisor. |
| gap | `vault_watch.py` "live" loop is on the **output** (Obsidian back-sync), not ingestion. | Not a substitute for live knowledge pull. |
| rough | Session re-extraction is cache-keyed per `session_id` but **whole-session**: one new turn invalidates the whole cache and re-runs the full LLM pass. | Turn-level incrementality for live tailing. |
| rough | `harness_sessions` store is a flat glob with full-rescan on every list/write. | Indexed/append store for a continuously growing capture set. |
| missing | No per-node freshness/provenance timestamps; currency tracked only at artifact level (git HEAD). | Per-fact freshness for "how fresh is this?" |

## Pillar 2 — Self-improving knowledge base → **one-shot re-extract, bolt-on evolution**

The "evolving" passes exist but run **only inside a single `compile`** (a
re-extract from scratch), and most are **opt-in via env flag or manual CLI**.
Facts are recomputed each compile, not revised in place.

| Status | Finding | What's needed |
|---|---|---|
| gap | **Decay** (`memory/decay.py`, Ebbinghaus 14d half-life) is computed only at *query time*, never persisted or written back at compile. | Compile-time decay write + persisted score. |
| gap | Decay's access loop is **dead**: `last_accessed_at == first_seen_at`, `access_count` never bumped. The "I keep looking at this → it matters" signal does nothing. | An access-recording surface (MCP read → bump). |
| gap | **Supersede** (`memory/supersede.py`) is gated behind `TESSERAE_SUPERSEDE_PASS=true` (off by default) and only *appends* edges — never demotes/hides obsolete content. Belief revision is cosmetic. | Default-on + consumers that suppress superseded facts in output. |
| gap | **Contradictions** are *detected* (`lint.py`, info-severity, brittle string match) but never *resolved*. No confidence arbitration. | A resolution pass, not just a probe. |
| gap | **Schema drift** (`schema_drift.py`) is a manual `schema-drift` subcommand that only writes proposals; the schema never self-refines. | Apply-path + pipeline integration. |
| gap | **Canonicalization** auto-merges only high-confidence aliases; the rest queue for human CLI approval. | Automatic LLM-arbitrated merge over time. |
| gap | **Feedback loop half-closed**: the deterministic baseline extractor *ignores guidance entirely* (`selective_extractor.py:43`); only the optional LLM path consumes corrections. With LLM off, human corrections never re-enter extraction. | Deterministic-path guidance honoring, or LLM-by-default. |
| gap | No **recurring-insight reinforcement**: nothing strengthens confidence when an insight reappears across sessions. `temporal.infer_confidence` is a coarse string heuristic. | Cross-session frequency → numeric confidence. |
| rough | Supersede candidate pairing is **lexical Jaccard (0.55)**; semantic restatements with low lexical overlap never become candidates. | Embedding-based candidate generation. |
| missing | The **entire self-improvement slice is untested** (no decay/supersede/feedback/drift/canonical/temporal tests). | Tests alongside any change here. |

## Pillar 3 — On-demand docs → **does not exist yet**

Query/retrieval plumbing is mature (hybrid RRF, PPR, ~20 MCP tools, per-page
ask, AI exports). But **every artifact is either a static full-corpus
projection or a single-node lookup.** "User asks 'give me context on X' →
tailored doc" is not implemented. The primitives to build it are all present
but never composed.

| Status | Finding | What's needed |
|---|---|---|
| missing | **On-demand doc generation (the core Pillar-3 gap).** No module produces a tailored, query-scoped doc from a request. `report.py` is a compile-time lint summary, not a knowledge artifact. | New `context_compiler`: search → PPR → neighborhood walk → body assembly → optional LLM synthesis. |
| gap | `wiki_page` returns one pre-compiled node body; no multi-node, query-scoped assembly tool. | `compile_context(query|seeds, depth, budget)` MCP tool. |
| gap | `ask` returns prose or a results list, never a downloadable/handoff context artifact. | Answer mode emitting a structured, cited context bundle. |
| gap | `agent_harness.py` is a **static** hand-off (hard-coded top-12 nodes + fixed list), not query-scoped or per-task. | Accept a topic/seed → render a scoped brief. |
| gap | `node_context` is 1-hop, unranked. Weak as an agent context primitive. | Route through PPR for ranked k-hop context. |
| gap | Exports (`llms.txt`, `graph.jsonld`) are whole-corpus dumps; no per-topic slice. | Topic-scoped subgraph → llms-txt slice. |
| rough | Default embedding lane is a **deterministic hash-bucket pseudo-embedding** (blake2b, 128-dim); real semantic backend only if `sentence-transformers` is installed, and `auto` downgrades silently. Out-of-the-box "semantic" retrieval is fake. | Real default embedding, or a loud warning on the hash lane. |
| rough | `query.answer()` **discards a valid LLM answer** if it lacks a node-citation regex match. | Keep the answer; flag missing citations instead. |
| rough | Static-host ask widget serves **canned `DEMO_QA`**; real ask only works under `serve`. The public Pages "ask" is theatre. | Acceptable for demo; not agent-consumable on the published site. |
| rough | `ask` `auto` backend swallows exceptions and degrades invisibly to BM25. | Surface which backend answered and why fallbacks fired. |

## Cross-cutting — Orchestration & lifecycle → **batch CLI, no engine**

| Status | Finding | What's needed |
|---|---|---|
| gap | **No daemon/engine process.** Flat one-shot argparse dispatcher; process exits after each subcommand. Zero signal/SIGTERM/pidfile/launchd handling; watchers die on bare `KeyboardInterrupt`. | A supervised long-running daemon owning one event loop + graceful shutdown. |
| gap | "Continuous" = `while True: time.sleep(interval)` markdown poller. No filesystem events, backpressure, or streaming. | Event-driven core with a single scheduler. |
| gap | **"Refresh" lives in a slash-command markdown skill**, not code — it sequences `sessions discover --import` → `compile` → `obsidian-sync`. | First-class in-process pipeline orchestrator shared by daemon/CLI/MCP. |
| rough | `changed_only` incremental compile is **fragile and self-described as a workaround**: manifest is `{path: sha256}`; must reload prior graph, strip projector/synthesis nodes, evict re-extracted source nodes, then merge — or a 21-file edit collapses 2400 nodes to 1700. | A designed incremental/streaming layer flowing through the `GraphStore` port. |
| rough | `cli.py` is a ~2000-line god-dispatcher (`if args.command == ...` ladder); `ask`/`wiki` have separate hand-built parsers. | Command registry / subcommand modules. |
| rough | Phase-gated flags ship half-finished surface: `--sessions-llm` help says *"Honored once Phase 5 lands"*. | Finish or hide. |
| rough | `graph_stores/url_resolver.py` wraps an async store with `asyncio.run` **per call** — a fresh event loop per upsert. Pathological for streaming. | Persistent async runtime if the engine goes live. |
| rough | `ports/` hexagonal protocols are defined but the standalone pipeline **bypasses them**, going straight to JSON artifacts. Only HypePaper uses the port. | Flow the core pipeline through `GraphStore` consistently. |
| rough | Three persistence formats (JSON artifact, SQLite store, Kuzu) with no single source of truth; Kuzu adapter base64-wraps every field to dodge a 0.16 corruption bug. | Converge on one source of truth. |
| rough | `serve` (`TCPServer.serve_forever`) and `watch` are **separate blocking processes** — can't serve + auto-recompile together. `deploy` is a manual git push, decoupled. | Unify serve + watch + deploy under the supervisor for continuous publish. |
| missing | `frontend.py` is a **deprecated dead module** still shipped, duplicating `tesserae/site/`. | Delete or migrate callers. |
| rough | `review_workflow.py` human-review loop emits stringly-typed `"action": "TODO: merge|keep_separate"` JSON for hand-editing; no programmatic apply path. | Integrated review queue wired into compile. |
| note | TODO/FIXME markers are genuinely sparse — the real debt is **comment-documented workarounds** (changed-only merge, Kuzu base64, asyncio-per-call), not stray TODOs. | — |

---

## Recommended build order (architecture delta → vision)

1. **Supervisor daemon + in-process pipeline orchestrator.** One event loop,
   signals/shutdown, replacing the markdown-skill refresh chain. *Unlocks every
   other pillar.*
2. **Live session monitor.** Tail harness JSONL → turn-level incremental
   extraction → feed the loop. (Replaces manual `sessions discover --import`.)
3. **Real incremental/streaming compile** through the `GraphStore` port,
   retiring the fragile `changed_only` eviction patch.
4. **Activate self-improvement passes by default + persist them**: compile-time
   decay write, access-count bump on MCP reads, supersede-on (with suppression),
   contradiction resolution, recurring-insight confidence.
5. **On-demand context compiler** (`compile_context` MCP tool + CLI): query →
   PPR/hybrid → neighborhood walk → assembled, cited, agent-ready doc.
6. **Real default embeddings** (or a loud degradation warning) so semantic
   retrieval isn't a hash stub out of the box.
7. **Unify serve + watch + deploy** for continuous publish; add lifecycle tests
   (the layer the vision most depends on is currently the least covered).

## Strengths worth preserving

Deterministic byte-identical compile; broad test coverage on the batch
machinery; clean hybrid RRF retrieval + thoughtful PPR edge weighting; broad,
correctly partitioned (public/private) MCP tool surface; solid static exports
(`llms.txt`, JSON-LD, RSS) and a security-conscious ask widget. The foundation
is strong; the work is adding the dynamic, self-driving layer on top.

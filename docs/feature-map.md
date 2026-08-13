# Feature Map

<!-- translations:start -->
<p align="center"><a href="i18n/feature-map.ko.md">한국어</a> · <a href="i18n/feature-map.zh.md">中文</a> · <a href="i18n/feature-map.ja.md">日本語</a> · <a href="i18n/feature-map.ru.md">Русский</a> · <a href="i18n/feature-map.es.md">Español</a> · <a href="i18n/feature-map.fr.md">Français</a> · <a href="../i18n/feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
This document summarizes the features currently implemented in Tesserae, with status, source files, and where they're documented.

Tesserae is a **context engine** running on three pillars: (1) session monitoring, (2) autonomous proactive knowledge ingestion, and (3) on-demand docs/context. The typed graph, vault, and static site are projections of the knowledge base. The features below are grouped by which pillar they serve; the **v0.5.0** milestone (June 2026) shipped the engine spine and the headline Pillar-3 feature, the on-demand context compiler.

Status legend: ✅ shipped · ⚠ in-progress / partial.

> **Reading order.** The sections below are milestones, newest first. Versions
> between v0.12.0 and v0.28.7 are not restated here — their per-release detail
> lives in [`docs/release-notes/`](release-notes/), which is the authoritative
> changelog. This map covers the shape of the system, not every commit.

## Cognitive memory & scope — v0.29.0 → v0.31.0 (August 2026)

The cycle that made the graph *know what happened*, not just what was written:
outcomes survive ingest, one causal edge is derived from them, and the
degradations that used to be silent now say so.

| Feature | Status | Source | Notes |
|---|---|---|---|
| Code layer opt-in | ✅ | `cli.py`, [`tesserae/code_graph.py`](../tesserae/code_graph.py) | `compile` no longer ingests code symbols by default. On a large repo they outnumbered everything else and crowded retrieval; `tesserae code ingest` still wires CodeGraph in deliberately. See [ingest](ingest.md). |
| Unhidden retrieval surface | ✅ | [`tesserae/mcp_server.py`](../tesserae/mcp_server.py), [`tesserae/ask_planner.py`](../tesserae/ask_planner.py) | The bitemporal and view-selective parameters were built and tested but unreachable over MCP. `search_facts` now takes `as_of` (answer as of a past date) alongside `current_only` — **refused together**, they are different clocks — and reports `undated_included` so a caller knows how much of the answer carries no date. The `ask` planner reaches the same filters: its `timeline` and `search_facts` steps take `as_of`, `timeline` also takes a `since` range bound, and `plan.executed` reports what actually ran. |
| Loud degradations | ✅ | [`tesserae/lint.py`](../tesserae/lint.py), [`tesserae/ingest/fetch.py`](../tesserae/ingest/fetch.py), [`tesserae/ingest/orchestrator.py`](../tesserae/ingest/orchestrator.py) | Three silent failures made explicit: binary ingest that produced nothing, undated interval coverage (`INTERVAL_COVERAGE`), and dropped non-text content. Silence read as success; it no longer does. |
| Source-derived `first_seen_at` | ✅ | [`tesserae/temporal.py`](../tesserae/temporal.py), [`tesserae/session_graph.py`](../tesserae/session_graph.py) | A node is dated from the path its source was ingested under, not from wall-clock at compile time — so a rerun dates it identically and byte-idempotence survives. |
| Procedural retrieval pool | ✅ | [`tesserae/context_compiler.py`](../tesserae/context_compiler.py), [`tesserae/research_graph.py`](../tesserae/research_graph.py) | `context` reserves a slot for procedural memory — what was run, and what came of it — **earned by provenance**, not granted by default. `PROCEDURAL_POOLS` lint reports when the slot cannot be filled honestly. |
| Tool results are turns | ✅ | [`tesserae/session_event.py`](../tesserae/session_event.py), [`tesserae/harness_sessions.py`](../tesserae/harness_sessions.py) | Exit codes and error flags survive ingest and land on `Event` nodes. The graph can tell a command that failed from one that merely ran. Home directories are redacted on the way in. |
| The `recovers` edge | ✅ | [`tesserae/session_recovery.py`](../tesserae/session_recovery.py) | The one causal edge: 'this succeeded after that failed', derived from two **observed** outcomes in one session that agree on tool, program family, working directory and operand. `CAUSAL_EDGE_TYPES` is deliberately one element wide. See [session history](session-history.md#the-recovers-edge). |
| Chartered domain structure | ⚠ | [`tesserae/charter.py`](../tesserae/charter.py), `cli.py` | Community detection *proposes* a domain vocabulary; the charter *owns* it between explicit reorgs, because detection is deterministic but not stable (a single 15-node document moves ~29% of members). `tesserae domains status` reads it. **Not yet produced by `compile`** — the command reports "no charter yet" until that lands. |
| Multi-host shared disk | ✅ | [`tesserae/harness_sessions.py`](../tesserae/harness_sessions.py) | `TESSERAE_HOST_ID` scopes prune/overwrite by *who wrote a record*, so N servers on one shared disk stop deleting each other's session history. See [session history](session-history.md). |

## Cross-project & UX — v0.11.0 (June 2026)

| Feature | Status | Source | Notes |
|---|---|---|---|
| Cross-project federation | ✅ | [`tesserae/federation.py`](../tesserae/federation.py) | `ask --scope federated` assembles ONE graph from several registered projects — identity-merge (same arxiv/repo/hash/symbol) + opt-out embedding-backed `shares_concept_with` links — and returns a single cross-referenced, cited answer over the union (PPR + `compile_context`). Per-project `graph.json` is read-only; deterministic for identity-only. |
| Smart `ask` router (no active project) | ✅ | [`tesserae/ask_router.py`](../tesserae/ask_router.py) | The "active project" concept is removed — all registered projects are equal. A bare `ask` routes itself (names a project → that one; comparative → federated; follow-up → keeps route; else federated fallback), with an optional LLM tiebreaker and per-conversation continuity. Per-project ops resolve the project from cwd. |
| Federation inspection | ✅ | `tesserae/federation.py`, `cli.py` | `tesserae federation status` (per-project node counts, identity merges, semantic links) and `federation explain <node>` (why a node bridges projects). |
| Multi-project serve | ✅ | [`tesserae/serve.py`](../tesserae/serve.py), `cli.py` | Bare `tesserae serve` serves EVERY registered project under one server (landing at `/`, each at `/<alias>/`, a Projects switcher in the header, path-contained); `--project X` serves one with the live ask widget. |
| LLM concept layer in `compile` | ✅ | `cli.py`, [`tesserae/llm_extractor.py`](../tesserae/llm_extractor.py), [`tesserae/selective_extractor.py`](../tesserae/selective_extractor.py) | `tesserae compile` builds the concept/claim layer **by default** (`--extractor llm`) via the configured provider (codex/claude/api per `llm_provider`); `--extractor deterministic` is the structural, byte-stable opt-out; `selective-llm --llm-include … --llm-limit N` is cost-aware. |
| `tesserae setup` (interactive) | ✅ | `cli.py`, [`tesserae/deps.py`](../tesserae/deps.py) | Top-level `tesserae setup` — interactive by default (LLM provider/effort + which optional deps); flags skip the prompts. Installs work in pip-less uv-tool envs (uv-pip fallback). |

## Interop, search & setup — v0.10.0 (June 2026)

| Feature | Status | Source | Notes |
|---|---|---|---|
| Google **OKF v0.2** import/export | ✅ | [`tesserae/okf.py`](../tesserae/okf.py) | `tesserae export okf [--import DIR]`. Writes v0.2, reads v0.1 **and** v0.2. Markdown + YAML frontmatter bundle; round-trips Tesserae's own bundles losslessly via an `x_tesserae` namespace, foreign bundles best-effort with unknown frontmatter keys preserved. Emits `title`/`description`/`resource`, `generated`, `sources` + `usage_window`, `status`/`stale_after`; deliberately emits **no** `verified` and **no** Attested Computation scaffolding — see [architecture § OKF v0.2 export/import](architecture.md#okf-v02-exportimport). |
| Fast transcript search (memex) | ✅ | [`tesserae/memex_search.py`](../tesserae/memex_search.py) | `nicosuave/memex` BM25 index over Claude/Codex transcripts, wired to the `tesserae serve` sessions dashboard via `GET /api/transcript-search`. Optional + graceful when absent. |
| Read-discipline handles | ✅ | [`tesserae/mcp_server.py`](../tesserae/mcp_server.py) | `compile_context` `preview=N` returns a bounded preview + a content-keyed handle; `get_handle` pages the rest. Keeps huge payloads out of the agent's context. |
| Extraction quality signals | ✅ | [`tesserae/session_graph_llm.py`](../tesserae/session_graph_llm.py) | Per-finding `confidence` + `confidence_rationale` + `revisit_signals` (byte-stable; surfaced in `fresh_insights`). |
| Machine-wide setup + deps | ✅ | [`tesserae/deps.py`](../tesserae/deps.py), `cli.py` | `tesserae setup` writes global LLM defaults + installs optional deps (memex, raganything); `tesserae config deps` lists/installs; `tesserae init` offers memex. Per-project config still overrides. |

## Context engine — v0.5.0 (June 2026)

The engine spine that drives the three pillars. See [`docs/architecture.md`](architecture.md) for the engine-spine module map, the self-improvement memory sidecar, and the context-compiler dataflow.

### Engine spine (pillars 1 & 2)

| Feature | Status | Source | Notes |
|---|---|---|---|
| `Pipeline` — reusable refresh chain returning `List[StepResult]` | ✅ | [`tesserae/engine/pipeline.py`](../tesserae/engine/pipeline.py) | One step runner the CLI, daemon, and MCP all call. Catches `Exception` per step; stops at first failure. |
| `Daemon` — single-owner asyncio supervisor | ✅ | [`tesserae/engine/daemon.py`](../tesserae/engine/daemon.py) | Watches sources + vault + harness-session dir; debounced cancel-and-reschedule coalesces a burst into one `Pipeline.run()`. Pidfile; survives in-flight exceptions. |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../tesserae/cli.py) | `--interval`, `--debounce`, `--once`. `daemon` is an alias of `engine`. |
| `project refresh` — prose chain (ingest → compile → project) | ✅ | `cli.py` + [`tesserae/project.py`](../tesserae/project.py) | `--changed-only` (opt-in incremental), `--no-sessions`. |
| Live session monitor → findings | ✅ | `harness_sessions.py` + session-graph modules | Imported sessions feed the graph; `fresh_insights` / `find_session_findings` surface them. |

### Self-improvement memory (pillar 2)

| Feature | Status | Source | Notes |
|---|---|---|---|
| `node_memory` SQLite sidecar (decay / confidence / superseded) | ✅ | [`tesserae/memory/store.py`](../tesserae/memory/store.py) | `NodeMemoryRow` + store-agnostic accessors; mutable state only. First-seen lives in the separate `node_provenance` sidecar. |
| Ebbinghaus decay score | ✅ | [`tesserae/memory/decay.py`](../tesserae/memory/decay.py) | Ranks session findings newest + most-accessed first (drives `fresh_insights`). |
| Supersede pass (**default-on**) | ✅ | [`tesserae/memory/supersede.py`](../tesserae/memory/supersede.py) | Deterministic verdict marks an older near-duplicate insight superseded by a newer one; adds a `supersedes` edge. |
| Insight → code-symbol linking | ✅ | [`tesserae/memory/insight_symbol_link.py`](../tesserae/memory/insight_symbol_link.py) | `discusses` edges from session insights to the symbols they reference. |
| Reinforce + contradiction passes | ✅ | [`tesserae/memory/reinforce.py`](../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../tesserae/memory/contradiction.py) | Access reinforcement + contradiction detection over the same sidecar. |
| Numeric recurrence confidence in output | ✅ | [`tesserae/temporal.py`](../tesserae/temporal.py) | Temporal facts stamp `confidence` from `NodeMemoryRow.confidence`, falling back to `infer_confidence`. |

### Retrieval + embeddings (pillars 2 & 3)

| Feature | Status | Source | Notes |
|---|---|---|---|
| Hybrid retriever (BM25 + lexical + embedding, RRF k=60) | ✅ | [`tesserae/retrieval/hybrid.py`](../tesserae/retrieval/hybrid.py) | Local-first, fully deterministic. |
| Personalized PageRank (HippoRAG-2) | ✅ | [`tesserae/retrieval/ppr.py`](../tesserae/retrieval/ppr.py) | Multi-hop seed expansion; depth-bounded subgraph. |
| Real default embeddings (Track B, Phase 6) | ✅ | `retrieval/hybrid.py` | Default = deterministic hash-bucket pseudo-embedding (no deps); `sentence-transformers` (`all-MiniLM-L6-v2`) preferred, loaded lazily when installed. `embedding_status` MCP tool reports the active backend. |

### On-demand context compiler (pillar 3 — headline)

| Feature | Status | Source | Notes |
|---|---|---|---|
| `compile_context` — cited in-memory `ContextBundle` | ✅ | [`tesserae/context_compiler.py`](../tesserae/context_compiler.py) | Seed resolution → PPR expansion → budget-bound selection → cited markdown → optional LLM synthesis. Deterministic unless `synthesize=true`. Writes nothing to disk. |
| `project context` CLI | ✅ | `cli.py` | `[query]`, `--seeds`, `--depth` (2), `--budget` (32000; ≤0 = uncapped), `--llm`, `--output`. |
| `compile_context` MCP tool | ✅ | [`tesserae/mcp_server.py`](../tesserae/mcp_server.py) | Same pipeline over MCP; `budget=0` is uncapped. |
| Topic-scoped export slices | ✅ | [`tesserae/site/exports.py`](../tesserae/site/exports.py) `slice_export_context_for_topic` | Topic-scoped `llms.txt` + `render_harness_context` via `compile_context`. |

### Incremental compile (Phase 4 — experimental)

| Feature | Status | Source | Notes |
|---|---|---|---|
| Provenance sidecar (`node_provenance`, first-seen) | ✅ | [`tesserae/graph_stores/sqlite.py`](../tesserae/graph_stores/sqlite.py) | Foundation for changed-only deletes; always recorded. |
| `GraphStore` delete surface | ✅ | [`tesserae/ports/graph_store.py`](../tesserae/ports/graph_store.py) | `delete_node`, `delete_nodes_by_source` (drops nodes whose provenance set empties; cross-file concepts survive). |
| `url_resolver` runtime store dispatch | ✅ | [`tesserae/graph_stores/url_resolver.py`](../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`. |
| `incremental_compile` flag | ⚠ | [`tesserae/project.py`](../tesserae/project.py) | **Default OFF / experimental.** Byte-parity proven for several edit shapes but multi-owner/producer-lifecycle gaps remain; full compile stays the default. |

## Frontend redesign — April 2026

Document-first, hierarchical wiki replaces the old graph dump. See [`docs/frontend-redesign.md`](frontend-redesign.md) for the route-by-route tour and [`docs/architecture.md`](architecture.md) for the three-layer model.

### Wiki layer (L2 markdown)

| Feature | Status | Source | Doc anchor |
|---|---|---|---|
| `WikiPageStore` (idempotent body-hash writes, frontmatter parser) | ✅ | [`tesserae/wiki_store.py`](../tesserae/wiki_store.py) | [architecture.md § Module map](architecture.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — one md page per wiki-layer node | ✅ | [`tesserae/wiki_projector.py`](../tesserae/wiki_projector.py) | [architecture.md § Pipeline](architecture.md#pipeline) |
| `sources/` pages | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.md#sources) |
| `concepts/` pages | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.md#concepts) |
| `entities/` pages | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.md#entities) |
| `papers/` pages | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.md#papers) |
| `repos/` pages | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.md#repos) |
| `topics/` pages | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.md#topics) |
| `questions/` pages (Open questions) | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.md#questions) |
| `syntheses/` pages | ✅ | [`tesserae/synthesis.py`](../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.md#syntheses) |

### Synthesis kinds (L2 → derived)

`SynthesisProjector` produces seven deterministic templates and adds `Synthesis` nodes + `synthesizes` / `summarizes` edges back into the graph.

| Kind | Status | Source | Notes |
|---|---|---|---|
| `pulse` (one global, drives `/`) | ✅ | `synthesis.py` | Rebuilt every compile. |
| `daily_digest` | ✅ | `synthesis.py` | One per `data/research/daily/<date>/`. |
| `weekly` | ✅ | `synthesis.py` | One per `data/research/weekly/<iso-week>/`. |
| `topic` | ✅ | `synthesis.py` | One per `ResearchTopic` / `ApproachFamily` cluster ≥ 3 papers. |
| `comparison` | ✅ | `synthesis.py` | One per pair of `ApproachFamily` competing on the same task. |
| `field_overview` | ✅ | `synthesis.py` | One per `ResearchField`. |
| LLM-upgraded summaries (env-flagged) | ⚠ | hook only | Heuristic baseline ships; `TESSERAE_SYNTHESIS_LLM=1` hook left as a stub. |

### Static site routes

| Route | Status | Source | Notes |
|---|---|---|---|
| `/` (home, hero pulse) | ✅ | [`tesserae/site/pages.py`](../tesserae/site/pages.py) `render_home` | Stat row + curated entry points + recent activity. |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | Heatmap + day list + synthesis rail. |
| `/timeline/<YYYY-MM-DD>.html` (per-day detail) | ⚠ | n/a yet | Heatmap cells link to the day's `digest.md` source page as an interim. Subagent P is wiring the per-day detail pages through `StaticSiteBuilder`. |
| `/graph/` (interactive 2D + 3D) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, hover tooltips, edge labels, cursor-anchored zoom. |
| `/about.html` | ✅ | `pages.py::render_about` | Schema, build info. |

### AI-friendly exports

| Artifact | Status | Source | Purpose |
|---|---|---|---|
| Per-page `<page>.txt` sibling | ✅ | [`tesserae/site/exports.py`](../tesserae/site/exports.py) `write_siblings` | Plain-text view of one page (no nav, no styling). |
| Per-page `<page>.json` sibling | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`. |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | llmstxt.org short index. |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | Every page body, capped at 5 MB. |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | schema.org `Dataset`, wiki-layer nodes only. |
| `graph.json` | ✅ | `__init__.py::write_site` | Full graph payload (incl. code nodes for tooling). |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../tesserae/site/search.py) | Palette + page search; wiki-layer kinds only. |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | Every emitted route, `lastmod` from frontmatter. |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | Last 30 syntheses. |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | Permissive — crawl + index. |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | Machine-readable site map. |
| `manifest.json` | ✅ | `__init__.py::_manifest` | sha256 + size for every emitted file (idempotence harness). |

### Visual design + UX

| Feature | Status | Source | Notes |
|---|---|---|---|
| Design tokens (light + dark themes, terracotta accent) | ✅ | [`tesserae/site/tokens.py`](../tesserae/site/tokens.py) | One CSS bundle in `assets/style.css`. |
| Theme toggle (persisted, no flash) | ✅ | [`tesserae/site/js.py`](../tesserae/site/js.py) | `data-theme="dark"` in `localStorage`, applied pre-paint. |
| Search palette (`cmd+k` / `ctrl+k` / `/`) | ✅ | `js.py` | Fuzzy match over `search-index.json`; recent-page list. |
| Sticky right TOC | ✅ | `pages.py` + `tokens.py` | Desktop only; mobile drawer via `<details>`. |
| Activity heatmap with month + weekday labels | ✅ | `components.py::heatmap_svg` | 26-week SVG, cells link to the day's `digest.md`. |
| Sparkline (per concept/entity) | ✅ | `components.py::sparkline_svg` | Weekly mention counts, last 12 weeks. |
| Mobile shell (drawer rail, bottom nav, fluid type) | ✅ | `tokens.py` + `pages.py` | Touch hit targets ≥ 44 px. |
| Page transitions (120 ms opacity, prefers-reduced-motion) | ✅ | `tokens.py` | |
| 3D + 2D graph view (hover, edge labels, cursor-anchored zoom) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, vendored as a CDN snapshot. |
| Per-page AI siblings footer | ✅ | `components.py::ai_siblings_footer` | Inline links to the `.txt` and `.json` for the current page. |
| Harness session history pages | ✅ | [`tesserae/harness_sessions.py`](../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../tesserae/site/sessions.py) | Explicit Claude Code/Codex import; `/sessions/` index and detail pages with markdown turns, left turn rail, collapsed tool use, and search entries. |

### Pipeline + CLI

| Feature | Status | Source | Notes |
|---|---|---|---|
| `project compile` calls synthesis + wiki + site in order | ✅ | [`tesserae/project.py`](../tesserae/project.py) | Phase 3 of the redesign plan. |
| `project build-site` standalone | ✅ | `project.py` + [`tesserae/cli.py`](../tesserae/cli.py) | Reads `wiki/` + `graph.json`, writes `site/`. |
| `project serve` local HTTP | ✅ | `cli.py` | Plain stdlib server. |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../tesserae/deploy.py) | Worktree push to `gh-pages`; optional `--enable-pages` via `gh` CLI. `--build`, `--dry-run`, `--branch`, `--remote`, `--force`. |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../tesserae/harness_sessions.py) + `cli.py` | Inbound session history for Claude Code/Codex; discovery is explicit and scoped to the project working directory. |
| `project watch` rebuild-on-change | ✅ | [`tesserae/cli.py`](../tesserae/cli.py) + [`tesserae/watch.py`](../tesserae/watch.py) | Standalone polling watcher: `--interval`, `--debounce`, `--once`, `--paths`, `--quiet`. The multi-source supervisor lives under `project engine`/`daemon` (see Context engine). |
| `project context` — compile a cited context doc | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../tesserae/context_compiler.py) | Pillar-3 headline; see Context engine section. |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../tesserae/engine/) | Prose refresh chain + supervisor loop; see Context engine section. |

## Pre-existing features (carried forward unchanged)

### CLI and installation

- ✅ Installable Python package via `pyproject.toml`.
- ✅ Console commands: `tesserae`, `tesserae`, `tesserae_mcp`.
- ✅ `scripts/install.sh` for `curl | bash` installation.
- ✅ Editable installs by default for fast local development.

### Extraction

- ✅ Deterministic research-note extractor with controlled node/edge vocabularies.
- ✅ Claude CLI/OAuth extractor for higher-quality structured extraction without API keys.
- ✅ Selective Claude routing by glob and budget limit.
- ✅ Deterministic development-code extractor for Python projects.
- ✅ Batch ingest with content hashing and `--changed-only` support.
- ✅ Malformed UTF-8 tolerant source reading.

### Graph governance

- ✅ Controlled `ResearchNodeType` list — now includes `SYNTHESIS`.
- ✅ Controlled edge type whitelist — now includes `synthesizes`, `summarizes`.
- ✅ Validation to reject schema drift.
- ✅ Alias canonicalization.
- ✅ Review queue for ambiguous near-duplicate nodes.
- ✅ Review decisions template and merge/keep-separate workflow.
- ✅ Corpus trend summarization from per-file graphs.

### Persistence and reports

- ✅ Graph JSON export.
- ✅ SQLite graph store.
- ✅ Optional Kuzu graph store.
- ✅ Graph report with counts, evidence coverage, orphan nodes, date buckets, alias-heavy nodes.
- ✅ Competitive report describing absorbed ideas from MegaMem, Graphiti/Zep, MCP graph servers, agentic RAG.

### Project-local workflow

- ✅ `tesserae init --bare`
- ✅ `tesserae compile <paths>`
- ✅ `tesserae compile`
- ✅ `tesserae projects mcp-config`
- ✅ `tesserae export site`
- ✅ `tesserae serve`
- ✅ `tesserae export site --deploy` (GitHub Pages)
- ✅ `tesserae sessions discover/import/list` (explicit local agent-history import)
- ✅ `tesserae export site --watch` (standalone polling watcher)
- ✅ `tesserae engine` (supervisor loop — v0.5.0)
- ✅ `tesserae refresh` (prose ingest → compile → project chain — v0.5.0)
- ✅ `tesserae context` (on-demand context compiler — v0.5.0)
- ✅ `tesserae export harness`
- ✅ `tesserae vault export`
- ✅ `tesserae export graphiti`
- ✅ `tesserae export graphiti --sync`

### Obsidian

- ✅ Ready-to-open vault export.
- ✅ `.obsidian/app.json` and graph settings.
- ✅ Markdown projection.
- ✅ `raw/assets/` structure.
- ✅ `_meta/dashboard.md` with Dataview query.

### Agent harnesses

Generated target files for:

- ✅ Claude Code: `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex: `AGENTS.md`, `mcp.toml`
- ✅ Gemini: `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro: steering and MCP settings
- ✅ Cursor: project rules and MCP config
- ✅ OpenCode: `AGENTS.md`, `opencode.json`

### Graphiti / temporal facts

- ✅ Temporal fact projection with provenance, currentness, confidence, and invalidation fields.
- ✅ Dependency-free Graphiti episode JSONL export.
- ✅ `sync-graphiti --dry-run` smoke without Graphiti installed.
- ✅ Optional live sync with `graphiti_core` and Neo4j.

### MCP server

- ✅ `tesserae_mcp` / `python3 -m tesserae.mcp_server` over stdio JSON-RPC.
- ✅ Retrieval/graph tools: `schema`, `graph_summary`, `search_nodes`, `node_context` (with `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`.
- ✅ Context-engine tools (v0.5.0): `compile_context`, `embedding_status`, `fresh_insights` (decay-ranked), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`, `ask`.
- ✅ Setup tools: `tesserae_setup_plan`, `tesserae_setup_apply`.
- ✅ Multi-project registry: `list_projects`, `register_project`, `unregister_project`, `list_sessions`. Store URL dispatch via `url_resolver`.

## Tests

The current suite covers:

- ✅ ontology guardrails (incl. new `Synthesis` node + `synthesizes` / `summarizes` edges);
- ✅ deterministic extraction;
- ✅ Claude CLI wrapper parsing/validation;
- ✅ selective Claude routing;
- ✅ canonicalization/review workflow;
- ✅ batch ingest;
- ✅ reports;
- ✅ SQLite/Kuzu persistence;
- ✅ Graphiti export/sync dry-run;
- ✅ project CLI workflow;
- ✅ agent harness export;
- ✅ Obsidian export;
- ✅ frontend generation + link integrity (no `nodes/codeclass-*.html`);
- ✅ wiki store idempotence;
- ✅ synthesis projector golden + idempotence;
- ✅ site components, pages, exports, relevance;
- ✅ AI-sibling shape (`.txt` + `.json` per page);
- ✅ end-to-end compile-twice idempotence;
- ✅ engine spine: pipeline, refresh chain, daemon core + sources, `project engine` CLI;
- ✅ self-improvement memory: sidecar, decay/supersede, supersede suppression (incl. MCP), reinforce/contradiction;
- ✅ retrieval + embeddings: hybrid search, PPR, real default embeddings (Phase 6);
- ✅ context compiler: shape/citation-integrity/determinism/budget/PPR-fallback, `project context` CLI, MCP `compile_context`;
- ✅ incremental compile (experimental): differ, parity gates, provenance readiness, SQLite provenance;
- ✅ package install and installer contract.

# Architecture

<!-- translations:start -->
<p align="center"><a href="i18n/architecture.ko.md">한국어</a> · <a href="i18n/architecture.zh.md">中文</a> · <a href="i18n/architecture.ja.md">日本語</a> · <a href="i18n/architecture.ru.md">Русский</a> · <a href="i18n/architecture.es.md">Español</a> · <a href="i18n/architecture.fr.md">Français</a> · <a href="../i18n/architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae is a **context engine**. It reconstructs a self-improving knowledge base from your project and hands it to agents as ready-to-use context. It runs on three pillars: (1) **session monitoring** — watch live agent/work sessions and capture findings as they happen; (2) **autonomous, proactive knowledge ingestion** — a pipeline + supervisor loop that pulls in and re-extracts knowledge continuously, improving the base rather than waiting to be told; (3) **on-demand docs/context** — user-requested artifacts compiled from that same base. The typed graph, the markdown vault, and the static site are *projections* of the knowledge base; the engine is the loop that keeps them fresh and feeds agents.

Underneath, Tesserae turns a directory of source material into a controlled, typed knowledge graph and projects that graph through a durable markdown wiki layer into a static, AI-friendly website. The April 2026 redesign reorganised the projection side around a Karpathy three-layer model: raw evidence stays raw, a typed graph governs ontology, and a markdown wiki layer sits between the graph and any rendered output. The static site is a *renderer* of that wiki layer rather than a direct dump of the graph, with the controlled ontology in [`tesserae/research_graph.py`](../tesserae/research_graph.py) as the schema. The **v0.5.0** milestone (June 2026) added the engine spine that drives all three pillars — see *Engine spine* and *On-demand context compiler* below.

## The Karpathy three-layer model

Andrej Karpathy's framing for LLM-friendly knowledge bases distinguishes three layers, each with its own durability guarantee:

| Layer | Concern | Repo location | Owner |
|---|---|---|---|
| L1 — Raw sources | The literal bytes the user authored or harvested. Append-only. | `data/`, `docs/`, project trees referenced in `.tesserae/config.json` | the user |
| L2 — Wiki | Typed markdown pages (sources, concepts, entities, papers, repos, topics, syntheses, questions) with YAML frontmatter. Idempotent: regenerated each compile, but only rewritten when content hashes change. | `.tesserae/wiki/` | `WikiPageStore`, `WikiLayerProjector`, `SynthesisProjector` |
| L3 — Rendered | The static HTML site, AI-sibling exports, search index, sitemaps, JSON-LD. Wiped and rewritten every compile, but byte-stable across reruns. | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

The schema sits across all three layers as a separate axis: `ResearchGraph` in `graph.json` is the controlled ontology that L2 pages link against, and the `ResearchNodeType` / edge whitelist in [`tesserae/research_graph.py`](../tesserae/research_graph.py) is the source of truth for what types exist at all.

The redesign added L2 explicitly. Before April 2026 the static site was projected straight from `graph.json`; the wiki layer existed only inside the Obsidian vault export. Splitting it out gave us:

- A single human-editable surface (open `.tesserae/wiki/` in Obsidian or any markdown editor).
- Idempotent rebuilds: re-running `project compile` produces zero file diffs unless source content changed.
- An evolution log: synthesis pages accumulate over time and let the project narrate itself.

## Pipeline

```
data/, docs/, src/                                    (L1 raw)
        │
        ▼  project compile  (tesserae/project.py)
┌───────────────────────────┐
│ ResearchGraphExtractor    │   deterministic + selective Claude
│ + canonicalization        │
└───────────┬───────────────┘
            │
            ▼
┌───────────────────────────┐
│ ResearchGraph (graph.json)│   schema: research_graph.py
└───────────┬───────────────┘
            │
            ├──▶ WikiLayerProjector   (one page per L1/L2 node)
            ├──▶ SynthesisProjector   (pulse, daily, weekly, topic, …)
            │
            ▼
┌───────────────────────────┐
│ .tesserae/wiki/  (L2 md)  │   sources/, concepts/, entities/,
│                            │   papers/, repos/, topics/,
│                            │   syntheses/, questions/
└───────────┬───────────────┘
            │
            ▼  StaticSiteBuilder.write_site
┌───────────────────────────┐
│ .tesserae/site/  (L3 html)│   index.html, <kind>/index.html,
│                            │   <kind>/<slug>.html,
│                            │   per-page .txt + .json siblings,
│                            │   llms.txt, llms-full.txt,
│                            │   graph.json, graph.jsonld,
│                            │   search-index.json,
│                            │   sitemap.xml, rss.xml,
│                            │   robots.txt, ai-readme.md,
│                            │   manifest.json
└───────────────────────────┘
```

Every step is incremental. The graph extractor uses `manifest.json` content hashes to skip unchanged source files. `WikiPageStore.write_page` returns `False` (and skips the write) when the body hash matches what's already on disk. `StaticSiteBuilder` wipes and rewrites `.tesserae/site/`, but its output is deterministic — see "Idempotence story" below.

## Context compiler dataflow

The on-demand context compiler ([`tesserae/context_compiler.py`](../tesserae/context_compiler.py)) is Pillar 3's headline path. Given a query and/or explicit seed node ids, `compile_context` builds a tailored, cited markdown bundle straight from the graph and returns it in memory — it writes nothing under `.tesserae/`.

```
query / seeds
     │
     ▼  1. Seed resolution
        explicit seeds (kept iff they exist) + hybrid_search() hits, deduped, stable order
     │
     ▼  2. PPR expansion
        retrieval.ppr.personalized_pagerank ranks the depth-bounded k-hop neighbourhood;
        empty result (disconnected seeds) → fall back to seed order (bundle is never empty)
     │
     ▼  2b. Procedural reservation (earned, not granted)
        one slot per procedural pool, in PROCEDURAL_POOL_ORDER: Runbook, Gotcha, Event,
        DistilledNote, ExpertiseProfile. A slot goes to the top-ranked node of that type
        that carries PRODUCER provenance — not merely the type name
     │
     ▼  3. Budget-bound selection
        walk PPR order (reserved nodes first), include each node's cited body until the
        next would overflow `budget` chars (budget <= 0 = uncapped; over-budget marker
        on a word boundary)
     │
     ▼  4. Cited markdown assembly
        one section per selected node + a trailing `## Citations` block.
        Body text prefers the projected wiki page (when a store + public wiki kind exist),
        else the node description, else a minimal stub. The no-LLM body embeds NO
        wall-clock timestamp → byte-identical for the same (graph, query, seeds, depth, budget).
     │
     ▼  5. Optional LLM synthesis  (only when synthesize=true AND ANTHROPIC_API_KEY is set)
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

Defaults: `depth=2`, `budget=32000`. The deterministic assembly (steps 1–4) is the contract; LLM synthesis is purely additive. The same pipeline backs the `project context` CLI command, the `compile_context` MCP tool, and the topic-scoped export slices (`slice_export_context_for_topic`, topic-scoped `llms.txt`).

**Why the procedural slot is earned by provenance.** The five procedural types
name what an agent did, learned to do, and is good at — but document extraction
is allowed to mint them too, so an LLM reading a call-for-papers legitimately
mints a typed `Event` called "CVPR 2026". Reservation is *additive*: it
promotes a node from anywhere in the neighbourhood to the front of the budget
walk. Reserving on type alone would therefore let a conference deadline evict
the session finding that actually earned the slot. `has_producer_provenance`
is what separates the two, and a reservation is a claim on a slot, not proof of
one — `delivered` is settled after the budget walk, so the caller can tell
"procedural memory was reserved" from "procedural memory arrived". The
`PROCEDURAL_POOLS` lint code reports the gap.

## Module map

### Wiki + synthesis (L2)

| Module | Responsibility |
|---|---|
| [`tesserae/wiki_store.py`](../tesserae/wiki_store.py) | `WikiPage` dataclass, `WikiPageStore` for filesystem I/O. Stdlib-only YAML-subset frontmatter parser. Body-hash idempotence. |
| [`tesserae/wiki_projector.py`](../tesserae/wiki_projector.py) | `WikiLayerProjector`: maps each `ResearchGraph` node of a wiki-layer type to a markdown page in the right `kind/` folder. |
| [`tesserae/synthesis.py`](../tesserae/synthesis.py) | `SynthesisProjector`: deterministic templates for pulse, daily_digest, weekly, topic, comparison, field_overview. Adds `Synthesis` nodes and `synthesizes` / `summarizes` edges back into the graph. |

### Graph + ontology

| Module | Responsibility |
|---|---|
| [`tesserae/research_graph.py`](../tesserae/research_graph.py) | `ResearchNodeType` enum (incl. `SYNTHESIS`), edge-type whitelist (incl. `synthesizes`, `summarizes`), validation. |
| [`tesserae/canonicalization.py`](../tesserae/canonicalization.py) | Alias canonicalization + near-duplicate review queue. |
| [`tesserae/merge_ledger.py`](../tesserae/merge_ledger.py) | `.tesserae/merge-ledger.json`: the loser→survivor tombstone for every duplicate a compile collapses, so an id from a previous compile resolves instead of returning a bare not-found. **Derived state, not history** — the publish unions with what is there, then keeps a record only while its loser is absent from the graph just published *and* the chain out of it lands on a node that is present. A loser that comes back to life is dropped. Read only after the graph itself misses, which is what guarantees a live id can never be redirected. |
| [`tesserae/candidate_ledger.py`](../tesserae/candidate_ledger.py) | `.tesserae/candidate-same-as.json`: pending / confirmed / rejected per candidate merge pair, keyed on the sorted node-id pair and nothing else — score, reason and backend are deliberately outside the key, being exactly the churn a verdict has to outlive. **Accumulated, the opposite of the merge ledger**: a merge is derived state, a human verdict is the one thing in the pipeline a machine cannot re-derive, so nothing here is ever pruned. The two must not share code. |
| [`tesserae/code_graph.py`](../tesserae/code_graph.py) | Deterministic Python AST extractor for the development slice. |
| [`tesserae/llm_extractor.py`](../tesserae/llm_extractor.py) | Claude CLI/OAuth selective extractor. |

### Site renderer (L3)

| Module | Responsibility |
|---|---|
| [`tesserae/site/__init__.py`](../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`: wipes + rebuilds the site, walks every route, emits exports + AI siblings + manifest. |
| [`tesserae/site/pages.py`](../tesserae/site/pages.py) | One renderer per route (home, indexes, detail pages, timeline, graph, about). `SiteContext` carries precomputed indices so renderers stay pure. |
| [`tesserae/site/components.py`](../tesserae/site/components.py) | HTML primitives: `breadcrumbs`, `card`, `badge`, `node_table`, `edge_list`, `sparkline_svg`, `heatmap_svg`, `toc`, `page_shell`, `ai_siblings_footer`. |
| [`tesserae/site/tokens.py`](../tesserae/site/tokens.py) | Design tokens — CSS variables, light + dark themes, layout, typography, all components styled here. |
| [`tesserae/site/js.py`](../tesserae/site/js.py) | Client JS bundle: search palette, theme toggle, sigma + 3D-force graph view. |
| [`tesserae/site/markdown.py`](../tesserae/site/markdown.py) | Stdlib-only markdown renderer (links, autolinks, code, emphasis, headings). No external dependency. |
| [`tesserae/site/relevance.py`](../tesserae/site/relevance.py) | Four-signal relevance scoring (direct link, source overlap, Adamic-Adar, type affinity) used by every `Related` section. |
| [`tesserae/site/search.py`](../tesserae/site/search.py) | `search-index.json` builder. Wiki-layer kinds only. |
| [`tesserae/site/sessions.py`](../tesserae/site/sessions.py) | Session index/detail renderers for imported harness history: project-memory summary sections, conversation turn rail, markdown transcript rendering, and collapsed tool-use blocks. |
| [`tesserae/site/exports.py`](../tesserae/site/exports.py) | `llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, per-page `.txt`/`.json` siblings. |

### Pipeline orchestration

| Module | Responsibility |
|---|---|
| [`tesserae/project.py`](../tesserae/project.py) | `ProjectWiki.compile`: drives extraction → graph → memory passes → wiki layer → site. Owns `ProjectPaths` (`config`, `graph`, `manifest`, `wiki`, `site`, etc.). Decides up front whether a provenance-driven incremental compile is eligible (gated on `incremental_compile`, default OFF). |
| [`tesserae/cli.py`](../tesserae/cli.py) | Flat-verb CLI dispatch (~2,732 lines after the legacy `project`/`wiki` subcommand groups were deleted). Verbs — `init`, `compile`, `ingest`, `context`, `ask`, `query`, `doctor`, `summary`, `decisions`, `refresh`, `serve`, `engine`, `export`, `vault`, `code`, `lab`, `setup`, `config`, `projects`, `sources`, `federation`, `integrations` — are declared as metadata in [`tesserae/cli_tree.py`](../tesserae/cli_tree.py) and wired up from that tree rather than hand-registered. |
| [`tesserae/deploy.py`](../tesserae/deploy.py) | `export site --deploy`: pushes `.tesserae/site/` to a `gh-pages` branch via worktree, optionally enables Pages via `gh`. |

### Engine spine (v0.5.0 — pillars 1 & 2)

The engine spine is the in-process loop that drives session monitoring and autonomous re-ingestion. The same `Pipeline.run()` is the single refresh path that the CLI, the supervisor daemon, and (later) the MCP server all call.

| Module | Responsibility |
|---|---|
| [`tesserae/engine/pipeline.py`](../tesserae/engine/pipeline.py) | `Pipeline`: a sequential step runner. Codifies the prose refresh chain (ingest → compile → project/publish) as an importable object that returns a structured `List[StepResult]` instead of printing-and-exiting, so every caller decides how to surface outcomes. `run()` catches `Exception` per step (lets `KeyboardInterrupt`/`SystemExit` through) and stops at the first failure. |
| [`tesserae/engine/daemon.py`](../tesserae/engine/daemon.py) | `Daemon`: single-owner asyncio supervisor. Watches source dirs, the Obsidian vault, and the harness-session dir; coalesces a burst of `TriggerEvent`s into exactly one `Pipeline.run()` via a cancel-and-reschedule debounce. Reuses the existing `watch.py` / `vault_watch.py` watchers (it does not rewrite them), writes a pidfile, and survives in-flight exceptions. Surfaced as `engine` (`--interval`, `--debounce`, `--once`). |
| [`tesserae/watch.py`](../tesserae/watch.py), [`tesserae/vault_watch.py`](../tesserae/vault_watch.py) | Polling watchers reused by both the standalone `export site --watch` command and the daemon's source/vault lanes. |

### Self-improvement memory (v0.5.0 — pillar 2)

Phase 5 activated persistent self-improvement. Mutable per-node state lives in a `node_memory` SQLite sidecar (inside `.tesserae/sqlite.db`), separate from the immutable `node_provenance.first_seen_at` first-seen stamp (a Phase-4 sidecar). The compile drives a set of deterministic passes over the graph.

| Module | Responsibility |
|---|---|
| [`tesserae/memory/store.py`](../tesserae/memory/store.py) | `NodeMemoryRow` + store-agnostic accessors (`read_memory`, `write_memory`, `bump_access`) over the `node_memory` table — `decay_score`, `last_accessed_at`, `confidence`, `superseded`. No call site embeds raw SQL. |
| [`tesserae/memory/decay.py`](../tesserae/memory/decay.py) | `compute_decay_score`: Ebbinghaus-style freshness score (newest + most-accessed first) used to rank session findings. |
| [`tesserae/memory/supersede.py`](../tesserae/memory/supersede.py) | `run_supersede_pass` (**default-on**): deterministic verdict that marks an older near-duplicate insight as superseded by a newer one, adding a `supersedes` edge. |
| [`tesserae/memory/insight_symbol_link.py`](../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`: links session insights to the code symbols they discuss via `discusses` edges. |
| [`tesserae/memory/reinforce.py`](../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../tesserae/memory/contradiction.py) | Access reinforcement and contradiction detection helpers over the same sidecar. |

Recurrence confidence is numeric in the output: the temporal projection stamps each fact's `confidence` from `NodeMemoryRow.confidence` (text in SQLite, surfaced via `temporal.py`), falling back to `infer_confidence` only when no stored value exists.

### Retrieval (v0.5.0 — pillars 2 & 3)

| Module | Responsibility |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../tesserae/retrieval/hybrid.py) | `hybrid_search`: local-first hybrid retriever fusing three lanes — Okapi BM25 (k1=1.5, b=0.75), case-folded lexical/FTS-style substring, and a pluggable embedding lane — via reciprocal-rank fusion (RRF, k=60). Fully deterministic. |
| [`tesserae/retrieval/ppr.py`](../tesserae/retrieval/ppr.py) | `personalized_pagerank`: HippoRAG-2-style (arXiv:2502.14802) Personalized PageRank over the graph for multi-hop seed expansion — surfaces well-connected nodes several hops from the seed, not just the 1-hop neighbourhood. |
| Embedding backend (Phase 6, Track B) | The hybrid embedding lane's default backend is a deterministic hash-bucket pseudo-embedding that needs no extra deps; `sentence-transformers` (`all-MiniLM-L6-v2`) is preferred and loaded lazily when the optional dependency is installed. The `embedding_status` MCP tool reports which backend is active. |
| [`tesserae/retrieval/vector_cache.py`](../tesserae/retrieval/vector_cache.py) | The `node_vectors` table in the SQLite sidecar, and the one accessor all three `.embed(` sites route through. Keyed on `(backend_name, backend_dim, sha256(embedded_text))` — identity here is the embedded **text**, not the node id, so an unchanged node hits after a full recompile, a project move, or a canonicalization rewrite of its id, while a re-described one misses and re-embeds. Two models' vectors never meet: their spaces are not comparable and a silent mix would corrupt cosine rather than fail. |
| [`tesserae/retrieval/views.py`](../tesserae/retrieval/views.py) | The view registry: `semantic` / `temporal` / `causal` / `entity`, each a named subset of `ALLOWED_EDGE_TYPES`, resolved by `weights_for()` into explicit zero weights for every out-of-view type. Two partition decisions are load-bearing: `summarizes` + `evidenced_by` (~50% of all edges — abstraction and provenance) belong to **no** view, or the semantic view becomes the whole graph again; and the causal view is wider than `CAUSAL_EDGE_TYPES`, since `{recovers}` alone would be a view with no live edges. |
| [`tesserae/blocking.py`](../tesserae/blocking.py) | The single blocking layer for both pairwise passes (canonicalization's review builder and `memory.supersede`). The cap truncates by **sorted id**, so a capped run does not depend on the order nodes arrived in and a narrowed compile stays reproducible; the caller supplies its own tokenizer, because a blocker coarser than its scorer silently deletes true matches. Each pass reports a cap it hit rather than returning a quietly shorter queue. |

### On-demand context compiler (v0.5.0 — pillar 3 headline)

| Module | Responsibility |
|---|---|
| [`tesserae/context_compiler.py`](../tesserae/context_compiler.py) | `compile_context`: the headline Pillar-3 feature. Compiles a tailored, **cited** context bundle for a query/seed set straight from the graph — see *Context compiler dataflow* below. Returns an in-memory `ContextBundle` (with `ContextCitation`s); writes nothing to disk. Exposed as the `project context` CLI command and the `compile_context` MCP tool. |

### Persistence ports + graph stores

| Module | Responsibility |
|---|---|
| [`tesserae/ports/graph_store.py`](../tesserae/ports/graph_store.py) | `GraphStore` protocol: `upsert_node`/`upsert_edge`, `get_node`, `iterate_nodes`, `query_subgraph`, `find_canonical`, and the Phase-4 delete surface — `delete_node` and `delete_nodes_by_source` (deletes nodes whose provenance set becomes empty after removing the given source paths, so cross-file concepts survive). |
| [`tesserae/graph_stores/sqlite.py`](../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`: standalone backing store; owns the `node_provenance` and `node_memory` sidecar tables. |
| [`tesserae/graph_stores/url_resolver.py`](../tesserae/graph_stores/url_resolver.py) | Resolves a store URL (`sqlite:///…`, `hypepaper-postgres://…`) to the right `GraphStore`, letting the MCP server point at any backing store at runtime. |

### External adapters (unchanged this round)

| Module | Responsibility |
|---|---|
| [`tesserae/obsidian_adapter.py`](../tesserae/obsidian_adapter.py) | Obsidian vault projection (graph coloring, Dataview dashboard, raw assets). |
| [`tesserae/agent_harness.py`](../tesserae/agent_harness.py) | Claude Code / Codex / Gemini / Kiro / Cursor / OpenCode harness exports. |
| [`tesserae/harness_sessions.py`](../tesserae/harness_sessions.py) | Inbound Claude Code/Codex session discovery, normalization, storage under `.tesserae/harness_sessions/`, and redacted markdown summaries. |
| [`tesserae/graphiti_adapter.py`](../tesserae/graphiti_adapter.py) | Temporal-fact JSONL + optional live Graphiti sync. Fed the `node_memory` sidecar, so exported confidence is the reinforced value the rest of Tesserae uses — not `infer_confidence`'s heuristic label. |
| [`tesserae/kuzu_adapter.py`](../tesserae/kuzu_adapter.py) | One-way Kuzu database export (`tesserae export kuzu`). Not a store — see [Kuzu export](#kuzu-export). |
| [`tesserae/mcp_server.py`](../tesserae/mcp_server.py) | MCP stdio server. Retrieval/graph: `schema`, `graph_summary`, `search_nodes`, `node_context` (with `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`. Context engine (v0.5.0): `compile_context` (the on-demand context compiler), `embedding_status`, `fresh_insights` (decay-ranked session findings), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`. Plus `ask`, the multi-project registry tools (`list_projects`, `register_project`, `unregister_project`, `list_sessions`), and `tesserae_setup_plan` / `tesserae_setup_apply`. |

## Project workspace layout

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store; also owns the node_provenance
                              (first-seen, Phase 4) and node_memory (decay /
                              confidence / superseded, Phase 5) sidecar tables
  temporal_facts.jsonl        Graphiti-style temporal projection (numeric recurrence confidence)
  graphiti_episodes.jsonl     dependency-free Graphiti episode export
  report.md                   graph quality / summary
  markdown_projection/        flat human-readable markdown
  obsidian_vault/             Obsidian projection w/ .obsidian/, raw/assets/
  agent_harness/              Claude Code / Codex / etc. harness files
  harness_sessions/           imported local Claude Code/Codex sessions
  wiki/                       L2 markdown wiki — see below
  site/                       L3 static site — see below
```

### `.tesserae/wiki/` (L2)

```text
wiki/
  sources/<slug>.md           raw documents from data/ + docs/, with frontmatter
  concepts/<slug>.md          Concept / TechnicalTerm / Algorithm / etc.
  entities/<slug>.md          Model / Dataset / Benchmark / Metric / Org / Person
  papers/<slug>.md            Paper hub
  repos/<slug>.md             Repository / Project / CodeProject
  topics/<slug>.md            ResearchField / ResearchTopic / ApproachFamily / Trend
  syntheses/<slug>.md         pulse, daily_digest, weekly, topic, comparison, field_overview
  questions/<slug>.md         OpenQuestion
```

Each file is editable by hand; the next compile honours user edits as long as the body hash differs from what the projector would write. (Editing only the body wins; editing the frontmatter loses on next compile because frontmatter is regenerated.) Obsidian users can open `.tesserae/wiki/` directly; the existing `obsidian_vault/` adapter is a separate projection, not a substitute.

### `.tesserae/site/` (L3)

```text
site/
  index.html                  home + project pulse
  about.html                  schema, build info
  assets/{style.css,app.js}   single CSS bundle + single JS bundle
  sources/index.html
  sources/<slug>.html
  sources/<slug>.txt          AI sibling — plain text
  sources/<slug>.json         AI sibling — structured record
  concepts/…  entities/…  papers/…  repos/…  topics/…  syntheses/…  questions/…
  sessions/index.html          imported harness-session index
  sessions/<project>/<id>.html session detail: summary, metadata, turn rail, markdown turns, collapsed tools
  timeline/index.html
  graph/index.html            interactive 2D + 3D force layout
  graph.json                  full graph payload (incl. code nodes, for tooling)
  graph.jsonld                schema.org Dataset, wiki-layer nodes only
  search-index.json           palette + page search; wiki-layer kinds only
  llms.txt                    llmstxt.org — short index
  llms-full.txt               llmstxt.org — every page body, capped 5MB
  sitemap.xml                 every emitted route
  rss.xml                     last 30 syntheses
  robots.txt                  permissive (crawl + index)
  ai-readme.md                machine-readable site map
  manifest.json               sha256 + size for every emitted file
```

## The charter

Community detection **proposes** a domain vocabulary; the charter
([`tesserae/charter.py`](../tesserae/charter.py)) **owns** it between explicit
reorgs. The split exists because detection is deterministic but not stable:
identical input reproduces all 1,649 communities exactly, yet a single 15-node
document moves ~29% of members between communities and drops large communities
to Jaccard 0.39–0.60. Anything keyed on community membership therefore takes a
near-total cache miss per ingest — and this corpus ingests daily.

So the charter pins the institution: sections are detected, collapsed into a
quotient graph (one node per section, one `part_of` edge per cross-section L0
edge), and split into divisions → departments → teams **by sub-community, never
by size**. Each domain's anchor is its top-degree member among the types
that can name a subject — `SourceDocument`, `TechnicalTerm`, `EvidenceSpan`,
`Session`, `Event` and `Agent` are demoted, because a section heading, a quoted
span, a transcript's opening line or an account identifier is not a name anyone
can pin an agent to — picked greedily so no two domains share one, and the human-facing slug is minted once from that anchor
and pinned. Across a reorg, `succeed` carries slugs forward by matching on
anchor, so a stable name survives a reshuffle of the members under it. Every
node lands in exactly one domain: `intake_members` catches the dropped
singletons and edge-isolated sections that detection would otherwise silently
lose.

Each domain also carries a **corpus clock**: `distilled_through` is the latest
source-derived timestamp anywhere beneath it, taken from the same ladder
`temporal._source_ts` uses — metadata keys, a leading date in a node's own
name, then a dated directory segment of its `source_path` below a project root
the graph itself declares. Every rung reads bytes already in `graph.json`, so
no wall clock, no `git` and no filesystem mtime can reach the file. A domain no
rung can date is not an error: it gets `distilled_through: null` and
`quality: undated` (48 of 780 domains on this corpus), and every domain reports
`undated_member_count` so a date resting on part of a domain cannot read as
coverage of all of it (340 of 780 are dated over a strict subset).

`tesserae domains status [--json]` prints the tree, each domain's date and the
undated census. **Status:** every `compile` derives the charter into
`.tesserae/charter/charter.json`, from the same canonicalized graph the
hierarchy sidecar is built from. A recompile that reorganises nothing declines
to write, so the file stays byte-identical — `reorg_seq` counts reorgs, not
compiles. The one exception is the clock, which is a fact about content rather
than structure: if it moved while the institution did not, the fresh dates are
written at the *same* `reorg_seq`. A project whose research layer fits a single
read is below the bound and gets no charter at all; there the command still
reports "no charter yet" and exits 0, which is the honest answer.

## What's deliberately excluded

The redesign drew an explicit line: code-class and code-function nodes stay in `graph.json` (so MCP and Graphiti consumers still see them) but never get HTML pages, never appear in `search-index.json`, and never appear in the navigation. That's the user-facing contract — the wiki is a document-first knowledge base, not a function browser.

Concretely, `StaticSiteBuilder` skips any node whose type is not in the L2 wiki kind map (`tesserae/wiki_projector.py::_KIND_FOR_TYPE`):

- Excluded from L2 + L3: `CodeClass`, `CodeFunction`, `CodeModule`, `Dependency`, `EvidenceSpan`, `SourceFile`, all `Claim` variants (`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`).
- Surface where they still appear: as bullets, badges, neighbour counts, or evidence excerpts inline on related wiki pages, and in `graph.json` for downstream tooling.

If you need code-level browsing, point an LSP / call-graph tool at the source tree directly — that's a different problem from "wiki of what this project knows."

## OKF v0.2 export/import

[`tesserae/okf.py`](../tesserae/okf.py) projects the graph into a [Google **OKF v0.2**](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) bundle — a directory tree of Markdown files with YAML frontmatter whose only required key is a non-empty `type`. `tesserae export okf` **writes v0.2**; `tesserae export okf --import DIR` **reads v0.1 and v0.2**. The bundle is a pure projection of `graph.json`: no wall clock, no `os.stat()`, no environment, so two exports of one graph are byte-identical.

What Tesserae emits, and where each value honestly comes from:

| Frontmatter | §  | Derived from |
|---|---|---|
| `type` | §4.1 | The node type, or a foreign type preserved in `metadata.okf_type` |
| `title` | §4.1 | `node.name` — v0.1 wrote a non-spec `name`; see the breaking changes below |
| `description` | §4.1 | First sentence of the node description, capped |
| `resource` | §4.1 | `arxiv_id` → `https://arxiv.org/abs/<id>`, else `repo_url` / `github_repo` |
| `generated: {by, at}` | §5.2 | `by` from `agent_key` → `<key>/tesserae-agent-write`, else `extractor` → `process:tesserae-<extractor>`, else `process:tesserae-compile`; `at` from the shared source-timestamp ladder in [`temporal.py`](../tesserae/temporal.py) |
| `sources[]` | §5.1 | `source_path` made project-root-relative, plus `author` (a lone `authored_by`), `last_modified` (`frontmatter_date` / `analysis_date`), `usage_count` (distinct `discussed_in` sessions) |
| `usage_window` | §5.1 | Min/max `started_at` / `ended_at` of the sessions counted above |
| `status: deprecated`, `stale_after` | §5.4, §5.5 | Nodes targeted by a `supersedes` edge; `stale_after` is the superseding node's date, omitted when it would precede the superseded node's own |
| `x_tesserae` | extension | Real node id, aliases, `source_path`, metadata, typed edges — the lossless round-trip channel |

`index.md` follows §8 (frontmatter is exactly `okf_version: '0.2'`, the one place §12 permits it) and `log.md` follows §9 (no frontmatter, `## YYYY-MM-DD` groups, newest first). On the Tesserae project graph (5197 nodes / 15284 edges) that is 5195 files carrying `generated` on all 5193 concepts, `sources` on 3934, `usage_window` on 1264, `description` on 1749, `resource` on 822, and `status`/`stale_after` on 25.

**Deliberately not emitted.** No `verified` key (§5.2), and therefore no trust tier above `unverified` (§5.3): nothing in the compiled graph is a recorded verification *event* with an actor and a timestamp. `verify_claim` and re-grounding are query-time functions over the graph, and `lint --verify-claims` is an LLM judge, which [`verify.py`](../tesserae/verify.py) itself says is not evidence. Edge provenance classes describe how strongly the graph licenses a *triple*; OKF's trust family is a per-*concept* confirmation. Mapping one onto the other would put a machine-confirmed tier on content nothing confirmed, so `generated.by` can never begin with `human:` — a test pins it. Likewise no Attested Computation family (§10): Tesserae has no sanctioned computations, executor, receipt, or attester ABI, and §10.5 tells a consumer to *gate* on attestation, so empty scaffolding would advertise a contract that cannot be honoured. Also absent for want of an honest source: `tags` (no node-level tag field — `aliases` are alternate names, not categories), per-claim `[^id]` footnotes, `status: draft` (`metadata.confidence` is extraction confidence, not review state), and any stored credibility score (§5.1 records signals, not verdicts). `last_modified` comes from in-graph document dates, **never** file mtime — the obvious-looking `os.stat()` shortcut is exactly the environment leak that has broken byte-idempotence here before.

**Reading.** Per §11 the importer rejects nothing: unknown `type` values, unknown frontmatter keys, missing optional families, broken cross-links, and a missing `index.md` are all tolerated; only a file with no non-empty `type` is skipped. Tesserae's own bundles round-trip losslessly through `x_tesserae`. Foreign bundles map `type` → the matching node kind or `Concept`, body links → `references` edges, and every unrecognised frontmatter key into `metadata.okf` (§4.1's round-trip SHOULD), with a bare `verified` mapping normalised to a one-element list (§11 MUST). v0.1 fallbacks (§13.1): a legacy `timestamp` lands in `metadata["updated_at"]` (a rung the timestamp ladder already reads) and a legacy body `# Citations` list becomes `metadata["okf"]["sources"]`, stripped out of the description instead of being swallowed as prose. On re-export the preserved bucket is merged *over* anything Tesserae derived, so re-exporting someone else's bundle never overwrites their provenance or trust claims with ours; `--import` prints a trust-tier histogram so a mixed bundle is visible rather than silent. Tiers are inferred at read time by `okf_trust_tier`, never stored.

**Breaking changes from Tesserae's v0.1 output.** `name:` becomes `title:` (`name` was never an OKF key in either version; the reader still accepts it, behind `title`). `index.md` and `log.md` lose their `type:` / `name:` frontmatter (§8, §9), so a consumer that treated them as typed concepts loses two phantom entries — which is the point; relatedly they are now reserved at *any* level of the hierarchy (§3.1), not only the bundle root. Every concept file's bytes change, so the first v0.2 export rewrites the whole bundle.

**Known limits.** `usage_count` counts distinct agent/work sessions whose transcript touched the document, not human page reads — §5.1 already warns the signal is coarse; read it as liveness, not popularity. The lifecycle family fires only for nodes targeted by a `supersedes` edge (25 of 5197 here); real coverage would need the temporal validity intervals `TemporalFactProjector` derives at query time, and running that inside the exporter over 15k edges was rejected as scope. `generated.by` uses `process:tesserae-<extractor>` rather than §7's `<producer>/<version>` on purpose: a version-bearing actor would rewrite all ~5200 concept files on every release for no semantic change. No path-valued OKF field (`resource`, `sources[].resource`) ever carries an absolute path — one that cannot be made project-root-relative is omitted rather than emitted raw, since §6.2 would have a consumer read it as bundle-relative — though absolute paths can still appear inside `x_tesserae.source_path` (the node's real identity, which a foreign consumer ignores) and inside node content that happens to quote one.

## Kuzu export

[`tesserae/kuzu_adapter.py`](../tesserae/kuzu_adapter.py) projects `graph.json` into an embedded [Kuzu](https://kuzudb.com) database so another tool can run Cypher over the graph. `tesserae export kuzu` writes it; `--graph PATH` exports a bare extracted graph rather than the project's compiled one. It is a **one-way export**, the third of three beside [OKF](#okf-v02-exportimport) and [Graphiti](../tesserae/graphiti_adapter.py), and `write_graph(replace=True)` deletes and recreates the database so the output is a pure function of the graph handed in.

**Kuzu is deliberately not a store, and the distinction is load-bearing.** A `KuzuResearchGraphStore` used to sit in [`tesserae/persistence.py`](../tesserae/persistence.py) next to the real SQLite store, reachable only through one `extract --kuzu-output` flag whose dependency was declared dev-only — a half-wired second backend, which is what made "should Tesserae adopt a graph database?" read as an open question. It is not open, and the reason is architectural rather than legal (Kuzu is MIT, embedded, and needs no server):

- **A second authoritative store can disagree with `graph.json` about the same fact**, and there is no arbiter. `graph.json` is the source of truth; anything that can contradict it is a bug surface.
- **Byte-idempotence would move from a pure function to a database's write ordering.** The property pinned by `tests/test_byte_idempotence_phase5.py` — two compiles producing byte-identical `graph.json` — holds because compile is a sorted-key pure function of its inputs. No compared graph-memory system even attempts it, and routing writes through an engine is how it would be lost.

Neither objection applies to an export: the database is derived output, wiped and rewritten from the graph, and no compile or query path reads it back. `read_graph` exists for the same reason `okf.read_okf_bundle` does — an export you cannot read back is an export you cannot verify — not because anything in the engine loads from Kuzu. `tests/test_kuzu_adapter.py` asserts that `tesserae.persistence` exposes no Kuzu symbol, so a reinstated store fails the suite rather than the review.

The same verdict rules out Neo4j as a substrate: see [`docs/superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md`](superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md), which adopts the capabilities (a persisted vector index, a soft-merge tombstone, a transaction-time clock) as file-and-SQLite sidecars rather than the engine.

## Idempotence story

The redesign aims for **byte-identical output across two consecutive `project compile` runs over unchanged inputs**. The pieces:

1. **Source extraction** uses `manifest.json` content hashes; unchanged files are skipped, so the graph remains stable.
2. **Wiki layer writes** are idempotent at the body level. `WikiPageStore.write_page` reads the existing file, strips frontmatter, sha256s the body, and short-circuits if the new body hashes the same — even if the new frontmatter has a different `generated_at` timestamp. This is the key trick that keeps git diffs tight on rebuild.
3. **Synthesis output** carries a `content_hash: sha256-…` in its frontmatter. The body hash is computed without `generated_at` so repeated compiles on the same graph produce the same hash, and `Synthesis` nodes carry the same `content_hash` in graph metadata.
4. **Site rendering** wipes `site/` at the start of `write_site`, then writes deterministically: routes are sorted, dictionaries dumped with `sort_keys=True`, `manifest.json` walked via `sorted(rglob("*"))`. Two runs produce byte-identical files including the manifest.
5. **Node dates are source-derived.** A node's `first_seen_at` comes from the path its source was ingested under — not from wall-clock at compile time. A clock reading would make every rerun a diff, which is why the naive version of this defeats point 1. The same rule keeps the `Event` pass byte-idempotent: every minted id, body and date is content-derived, verified over a 481-session corpus.

This is verified by `tests/test_site_pages.py` and the end-to-end smoke in `tests/test_project_e2e_redesign.py` (compile twice, diff sites, expect zero file deltas).

## Scaling notes

- **Graph view node cap.** [`MAX_GRAPH_NODES = 1500`](../tesserae/site/pages.py) bounds the page-embedded payload for the interactive force layout. Beyond ~1500 nodes the browser-side simulation gets sluggish on mid-range hardware, so the page drops the lowest-degree wiki-layer nodes first when the count exceeds the cap. The exported `graph.json` is unaffected — it always contains the full graph. Code nodes are filtered out before the cap is applied.
- **`llms-full.txt` cap.** A 5 MB safety cap applies in [`tesserae/site/exports.py`](../tesserae/site/exports.py); the file ends with a `[TRUNCATED — see graph.jsonld for the full set]` marker if the cap is hit. `graph.jsonld` is uncapped because JSON-LD consumers expect the full set.
- **Search index.** Wiki-layer kinds only. Code-graph nodes never enter `search-index.json`; the redesign target is < 500 KB for the dogfood corpus and we're well under that today.
- **Per-page byte budget (rule of thumb).** Each detail page < 60 KB gz HTML, shared CSS < 30 KB, shared JS < 25 KB, sigma vendor on the graph page only (~60 KB). The graph view uses 3D-force-graph + Three.js loaded once; all other pages stay vanilla.
- **Compile time on dogfood.** ~300 markdown files extract in under 5 s on a recent dev machine; site render adds another ~2 s. The wiki layer's idempotence means subsequent compiles touch only the changed paths.

## Frontend interaction surface

- **Search palette** — `cmd+k` / `ctrl+k` / `/`. Fuzzy match over `search-index.json`, scoped to wiki kinds. Recent pages persisted in `localStorage`.
- **Theme toggle** — top-right button; `data-theme="dark"` is stored in `localStorage` and applied before paint to avoid flash.
- **Sticky right TOC** — desktop only; collapses to a `<details>` drawer on mobile. Generated from `<h2>` / `<h3>` in the page body.
- **Activity heatmap** — 26-week SVG with month + weekday labels. Cells link to the day's `digest.md` source page when one exists. (Per-day timeline detail pages — `/timeline/<YYYY-MM-DD>.html` — are an explicit follow-up; the inline notice in `render_timeline` flags it. ⚠ in-progress.)
- **Graph view** — `/graph/`. 3D force layout (3d-force-graph + Three.js) with hover tooltips, edge labels, cursor-anchored zoom, and a 2D fallback view. Node colors come from `ResearchNodeType`.
- **Mobile shell** — drawer rail, bottom nav, fluid type, touch-safe hit targets (≥ 44 px).

## Testing strategy

- **Unit** — `tests/test_wiki_store.py`, `tests/test_synthesis.py`, `tests/test_site_components.py`, `tests/test_site_pages.py`, `tests/test_site_exports.py`, `tests/test_relevance.py`.
- **Engine spine** — `tests/test_pipeline.py`, `tests/test_refresh_pipeline.py`, `tests/test_daemon_core.py`, `tests/test_daemon_sources.py`, `tests/test_cli_engine.py`.
- **Self-improvement memory** — `tests/test_memory_sidecar.py`, `tests/test_decay_supersede.py`, `tests/test_supersede_suppression.py`, `tests/test_mcp_supersede_suppression.py`, `tests/test_memory_contradiction_reinforce.py`.
- **Retrieval + embeddings** — `tests/test_hybrid_search.py`, `tests/test_ppr.py`, `tests/test_real_embeddings_phase6.py`.
- **Context compiler** — `tests/test_context_compiler.py` (shape, citation integrity, determinism, budget, PPR fallback), `tests/test_cli_context.py`, `tests/test_mcp_server_context.py`.
- **Incremental compile (experimental)** — `tests/test_incremental_compile.py`, `tests/test_incremental_parity.py`, `tests/test_provenance_readiness.py`, `tests/test_sqlite_provenance.py`.
- **Idempotence** — `tests/test_project_e2e_redesign.py` compiles twice and asserts zero diffs in `wiki/` and `site/`.
- **Link integrity** — `tests/test_frontend.py` parses every emitted HTML for hrefs and asserts every internal link resolves to a generated file. No `nodes/codeclass-*.html` is produced.
- **AI siblings** — for every `path/foo.html`, the test suite asserts `path/foo.txt` and `path/foo.json` exist; the JSON parses and contains `{title, kind, body, links}`.
- **No Playwright** — vanilla pytest under `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.

## Related docs

- [Quickstart](quickstart.md) — minimum path from `project init` to a browsable site.
- [Frontend redesign walkthrough](frontend-redesign.md) — annotated tour of every route.
- [Feature map](feature-map.md) — what's shipped, what's in-progress, with file pointers.
- [Self-dogfood demo](self-dogfood.md) — running Tesserae against its own repo.

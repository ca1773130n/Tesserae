# Feature Map

This document summarizes the features currently implemented in LLM-Wiki, with status, source files, and where they're documented.

Status legend: ✅ shipped · ⚠ in-progress / partial.

## Frontend redesign — April 2026

Document-first, hierarchical wiki replaces the old graph dump. See [`docs/frontend-redesign.md`](frontend-redesign.md) for the route-by-route tour and [`docs/architecture.md`](architecture.md) for the three-layer model.

### Wiki layer (L2 markdown)

| Feature | Status | Source | Doc anchor |
|---|---|---|---|
| `WikiPageStore` (idempotent body-hash writes, frontmatter parser) | ✅ | [`llm_wiki/wiki_store.py`](../llm_wiki/wiki_store.py) | [architecture.md § Module map](architecture.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — one md page per wiki-layer node | ✅ | [`llm_wiki/wiki_projector.py`](../llm_wiki/wiki_projector.py) | [architecture.md § Pipeline](architecture.md#pipeline) |
| `sources/` pages | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.md#sources) |
| `concepts/` pages | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.md#concepts) |
| `entities/` pages | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.md#entities) |
| `papers/` pages | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.md#papers) |
| `repos/` pages | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.md#repos) |
| `topics/` pages | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.md#topics) |
| `questions/` pages (Open questions) | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.md#questions) |
| `syntheses/` pages | ✅ | [`llm_wiki/synthesis.py`](../llm_wiki/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.md#syntheses) |

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
| LLM-upgraded summaries (env-flagged) | ⚠ | hook only | Heuristic baseline ships; `LLM_WIKI_SYNTHESIS_LLM=1` hook left as a stub. |

### Static site routes

| Route | Status | Source | Notes |
|---|---|---|---|
| `/` (home, hero pulse) | ✅ | [`llm_wiki/site/pages.py`](../llm_wiki/site/pages.py) `render_home` | Stat row + curated entry points + recent activity. |
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
| Per-page `<page>.txt` sibling | ✅ | [`llm_wiki/site/exports.py`](../llm_wiki/site/exports.py) `write_siblings` | Plain-text view of one page (no nav, no styling). |
| Per-page `<page>.json` sibling | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`. |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | llmstxt.org short index. |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | Every page body, capped at 5 MB. |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | schema.org `Dataset`, wiki-layer nodes only. |
| `graph.json` | ✅ | `__init__.py::write_site` | Full graph payload (incl. code nodes for tooling). |
| `search-index.json` | ✅ | [`llm_wiki/site/search.py`](../llm_wiki/site/search.py) | Palette + page search; wiki-layer kinds only. |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | Every emitted route, `lastmod` from frontmatter. |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | Last 30 syntheses. |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | Permissive — crawl + index. |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | Machine-readable site map. |
| `manifest.json` | ✅ | `__init__.py::_manifest` | sha256 + size for every emitted file (idempotence harness). |

### Visual design + UX

| Feature | Status | Source | Notes |
|---|---|---|---|
| Design tokens (light + dark themes, terracotta accent) | ✅ | [`llm_wiki/site/tokens.py`](../llm_wiki/site/tokens.py) | One CSS bundle in `assets/style.css`. |
| Theme toggle (persisted, no flash) | ✅ | [`llm_wiki/site/js.py`](../llm_wiki/site/js.py) | `data-theme="dark"` in `localStorage`, applied pre-paint. |
| Search palette (`cmd+k` / `ctrl+k` / `/`) | ✅ | `js.py` | Fuzzy match over `search-index.json`; recent-page list. |
| Sticky right TOC | ✅ | `pages.py` + `tokens.py` | Desktop only; mobile drawer via `<details>`. |
| Activity heatmap with month + weekday labels | ✅ | `components.py::heatmap_svg` | 26-week SVG, cells link to the day's `digest.md`. |
| Sparkline (per concept/entity) | ✅ | `components.py::sparkline_svg` | Weekly mention counts, last 12 weeks. |
| Mobile shell (drawer rail, bottom nav, fluid type) | ✅ | `tokens.py` + `pages.py` | Touch hit targets ≥ 44 px. |
| Page transitions (120 ms opacity, prefers-reduced-motion) | ✅ | `tokens.py` | |
| 3D + 2D graph view (hover, edge labels, cursor-anchored zoom) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, vendored as a CDN snapshot. |
| Per-page AI siblings footer | ✅ | `components.py::ai_siblings_footer` | Inline links to the `.txt` and `.json` for the current page. |

### Pipeline + CLI

| Feature | Status | Source | Notes |
|---|---|---|---|
| `project compile` calls synthesis + wiki + site in order | ✅ | [`llm_wiki/project.py`](../llm_wiki/project.py) | Phase 3 of the redesign plan. |
| `project build-site` standalone | ✅ | `project.py` + [`llm_wiki/cli.py`](../llm_wiki/cli.py) | Reads `wiki/` + `graph.json`, writes `site/`. |
| `project serve` local HTTP | ✅ | `cli.py` | Plain stdlib server. |
| `project deploy` → GitHub Pages | ✅ | [`llm_wiki/deploy.py`](../llm_wiki/deploy.py) | Worktree push to `gh-pages`; optional `--enable-pages` via `gh` CLI. `--build`, `--dry-run`, `--branch`, `--remote`, `--force`. |
| `project watch` rebuild-on-change | ⚠ | [`llm_wiki/cli.py`](../llm_wiki/cli.py) | Subagent R is finishing the polling watcher — `--interval`, `--debounce`, `--once`, `--paths`, `--quiet` arg surface is in place; the rebuild loop body is being landed in this round. |

## Pre-existing features (carried forward unchanged)

### CLI and installation

- ✅ Installable Python package via `pyproject.toml`.
- ✅ Console commands: `llm_wiki`, `llm-wiki`, `llm_wiki_mcp`.
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

- ✅ `llm_wiki project init`
- ✅ `llm_wiki project ingest`
- ✅ `llm_wiki project compile`
- ✅ `llm_wiki project mcp-config`
- ✅ `llm_wiki project build-site`
- ✅ `llm_wiki project serve`
- ✅ `llm_wiki project deploy` (new — GitHub Pages)
- ⚠ `llm_wiki project watch` (in-progress)
- ✅ `llm_wiki project export-agent-harness`
- ✅ `llm_wiki project export-obsidian`
- ✅ `llm_wiki project export-graphiti`
- ✅ `llm_wiki project sync-graphiti`

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

### Cognee

- ✅ Cognee JSONL bundle (`nodes.jsonl`, `edges.jsonl`, `manifest.json`).
- ✅ Optional add-only direct import.
- ✅ Optional Codex CLI/OAuth-backed Cognee cognify adapter.
- ✅ Deterministic and Ollama embedding adapter paths for no-API-key smoke/quality workflows.

### MCP server

- ✅ `llm_wiki_mcp` / `python3 -m llm_wiki.mcp_server` over stdio JSON-RPC.
- ✅ Tools: `schema`, `graph_summary`, `search_nodes`, `node_context`, `search_facts`, `timeline`.
- ✅ Multi-project registry.

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
- ✅ Cognee bundles/import patches;
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
- ✅ package install and installer contract.

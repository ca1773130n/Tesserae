# Feature-Map

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a></p>
<!-- translations:end -->
Dieses Dokument fasst die aktuell in Tesserae implementierten Features zusammen, mit Status, Source-Dateien und Verweisen darauf, wo sie dokumentiert sind.

Tesserae ist eine **Kontext-Engine**, die auf drei Säulen ruht: (1) Sitzungsüberwachung, (2) autonome proaktive Wissensaufnahme und (3) Dokumente/Kontext auf Abruf. Der typisierte Graph, der Vault und die statische Site sind Projektionen der Wissensbasis. Die folgenden Features sind nach der Säule gruppiert, der sie dienen; der Meilenstein **v0.5.0** (Juni 2026) lieferte das Engine-Rückgrat und das Vorzeigefeature von Säule 3, den On-Demand-Kontext-Compiler.

Status-Legende: ✅ ausgeliefert · ⚠ in Arbeit / teilweise.

## Kontext-Engine — v0.5.0 (Juni 2026)

Das Engine-Rückgrat, das die drei Säulen antreibt. Siehe [`docs/architecture.md`](architecture.de.md) für die Modulkarte des Engine-Rückgrats, das Sidecar des Selbstverbesserungs-Speichers und den Datenfluss des Kontext-Compilers.

### Engine-Rückgrat (Säulen 1 & 2)

| Feature | Status | Source | Hinweise |
|---|---|---|---|
| `Pipeline` — wiederverwendbare Aktualisierungskette, die `List[StepResult]` zurückgibt | ✅ | [`tesserae/engine/pipeline.py`](../tesserae/engine/pipeline.py) | Ein Schrittausführer, den CLI, Daemon und MCP alle aufrufen. Fängt `Exception` pro Schritt; stoppt beim ersten Fehler. |
| `Daemon` — Asyncio-Supervisor mit alleinigem Besitzer | ✅ | [`tesserae/engine/daemon.py`](../tesserae/engine/daemon.py) | Überwacht Quellen + Vault + Harness-Sitzungsverzeichnis; ein Abbrechen-und-Neuplanen-Debounce fasst eine Serie zu einem `Pipeline.run()` zusammen. Pidfile; überlebt Ausnahmen im laufenden Betrieb. |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../tesserae/cli.py) | `--interval`, `--debounce`, `--once`. `daemon` ist ein Alias von `engine`. |
| `project refresh` — prosaische Kette (Aufnahme → Kompilierung → Projektion) | ✅ | `cli.py` + [`tesserae/project.py`](../tesserae/project.py) | `--changed-only` (optional inkrementell), `--skip-sessions`. |
| Live-Sitzungsüberwachung → Befunde | ✅ | `harness_sessions.py` + Sitzungsgraph-Module | Importierte Sitzungen speisen den Graphen; `fresh_insights` / `find_session_findings` bringen sie an die Oberfläche. |

### Selbstverbesserungs-Speicher (Säule 2)

| Feature | Status | Source | Hinweise |
|---|---|---|---|
| `node_memory`-SQLite-Sidecar (Decay / Konfidenz / ersetzt) | ✅ | [`tesserae/memory/store.py`](../tesserae/memory/store.py) | `NodeMemoryRow` + store-unabhängige Accessoren; nur veränderlicher Zustand. Erstsichtung liegt im separaten `node_provenance`-Sidecar. |
| Ebbinghaus-Decay-Score | ✅ | [`tesserae/memory/decay.py`](../tesserae/memory/decay.py) | Rankt Sitzungsbefunde nach neueste + am häufigsten aufgerufene (treibt `fresh_insights`). |
| Supersede-Pass (**standardmäßig EIN**) | ✅ | [`tesserae/memory/supersede.py`](../tesserae/memory/supersede.py) | Deterministisches Urteil markiert ein älteres Beinahe-Duplikat-Insight als durch ein neueres ersetzt; fügt `supersedes`-Kante hinzu. |
| Insight → Code-Symbol-Verknüpfung | ✅ | [`tesserae/memory/insight_symbol_link.py`](../tesserae/memory/insight_symbol_link.py) | `discusses`-Kanten von Sitzungs-Insights zu den referenzierten Symbolen. |
| Reinforce- + Widerspruchs-Pässe | ✅ | [`tesserae/memory/reinforce.py`](../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../tesserae/memory/contradiction.py) | Zugriffsverstärkung + Widerspruchserkennung über demselben Sidecar. |
| Numerische Wiederkehr-Konfidenz in der Ausgabe | ✅ | [`tesserae/temporal.py`](../tesserae/temporal.py) | Zeitliche Fakten stempeln `confidence` aus `NodeMemoryRow.confidence`, sonst Rückfall auf `infer_confidence`. |

### Retrieval + Embeddings (Säulen 2 & 3)

| Feature | Status | Source | Hinweise |
|---|---|---|---|
| Hybrid-Retriever (BM25 + lexikalisch + Embeddings, RRF k=60) | ✅ | [`tesserae/retrieval/hybrid.py`](../tesserae/retrieval/hybrid.py) | local-first, vollständig deterministisch. |
| Personalized PageRank (HippoRAG-2) | ✅ | [`tesserae/retrieval/ppr.py`](../tesserae/retrieval/ppr.py) | Multi-Hop-Seed-Expansion; tiefenbegrenzter Subgraph. |
| Echte Standard-Embeddings (Track B, Phase 6) | ✅ | `retrieval/hybrid.py` | Standard = deterministisches Hash-Bucket-Pseudo-Embedding (ohne Abhängigkeiten); `sentence-transformers` (`all-MiniLM-L6-v2`) bevorzugt bei Installation, lazy geladen. Das MCP-Werkzeug `embedding_status` meldet das aktive Backend. |

### On-Demand-Kontext-Compiler (Säule 3 — Vorzeige)

| Feature | Status | Source | Hinweise |
|---|---|---|---|
| `compile_context` — zitiertes `ContextBundle` im Speicher | ✅ | [`tesserae/context_compiler.py`](../tesserae/context_compiler.py) | Seed-Auflösung → PPR-Expansion → budgetbegrenzte Auswahl → zitiertes Markdown → optionale LLM-Synthese. Deterministisch außer `synthesize=true`. Schreibt nichts auf die Festplatte. |
| `project context` CLI | ✅ | `cli.py` | `[query]`, `--seeds`, `--depth` (2), `--budget` (32000; ≤0 = unbegrenzt), `--synthesize`, `--output`. |
| `compile_context` MCP-Werkzeug | ✅ | [`tesserae/mcp_server.py`](../tesserae/mcp_server.py) | Dieselbe Pipeline über MCP; `budget=0` = unbegrenzt. |
| Themenbezogene Export-Slices | ✅ | [`tesserae/site/exports.py`](../tesserae/site/exports.py) `slice_export_context_for_topic` | Themenbezogene `llms.txt` + `render_harness_context` via `compile_context`. |

### Inkrementelle Kompilierung (Phase 4 — experimentell)

| Feature | Status | Source | Hinweise |
|---|---|---|---|
| Herkunfts-Sidecar (`node_provenance`, Erstsichtung) | ✅ | [`tesserae/graph_stores/sqlite.py`](../tesserae/graph_stores/sqlite.py) | Grundlage für Changed-only-Löschungen; immer aufgezeichnet. |
| `GraphStore`-Löschfläche | ✅ | [`tesserae/ports/graph_store.py`](../tesserae/ports/graph_store.py) | `delete_node`, `delete_nodes_by_source` (löscht Knoten, deren Herkunftsmenge leer wird; dateiübergreifende Konzepte überleben). |
| `url_resolver` Laufzeit-Store-Dispatch | ✅ | [`tesserae/graph_stores/url_resolver.py`](../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`. |
| `incremental_compile`-Flag | ⚠ | [`tesserae/project.py`](../tesserae/project.py) | **Standard OFF / experimentell.** Byte-Parität für mehrere Edit-Formen nachgewiesen, aber Lücken bei Mehrfach-Eigentümern/Producer-Lebenszyklus bleiben; volle Kompilierung bleibt Standard. |

## Frontend-Redesign — April 2026

Ein dokument-zentriertes, hierarchisches Wiki ersetzt den alten Graph-Dump. Siehe [`docs/frontend-redesign.md`](frontend-redesign.de.md) für die Route-für-Route-Tour und [`docs/architecture.md`](architecture.de.md) für das Drei-Schichten-Modell.

### Wiki-Layer (L2 Markdown)

| Feature | Status | Source | Doc-Anker |
|---|---|---|---|
| `WikiPageStore` (idempotente Body-Hash-Writes, Frontmatter-Parser) | ✅ | [`tesserae/wiki_store.py`](../tesserae/wiki_store.py) | [architecture.md § Module map](architecture.de.md#wiki--synthese-l2) |
| `WikiLayerProjector` — eine md-Seite pro Wiki-Layer-Knoten | ✅ | [`tesserae/wiki_projector.py`](../tesserae/wiki_projector.py) | [architecture.md § Pipeline](architecture.de.md#pipeline) |
| `sources/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.de.md#sources) |
| `concepts/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.de.md#concepts) |
| `entities/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.de.md#entities) |
| `papers/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.de.md#papers) |
| `repos/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.de.md#repos) |
| `topics/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.de.md#topics) |
| `questions/`-Seiten (Open Questions) | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.de.md#questions) |
| `syntheses/`-Seiten | ✅ | [`tesserae/synthesis.py`](../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.de.md#syntheses) |

### Synthese-Kinds (L2 → derived)

`SynthesisProjector` produziert sieben deterministische Templates und fügt `Synthesis`-Knoten + `synthesizes`/`summarizes`-Kanten zurück in den Graph.

| Kind | Status | Source | Hinweise |
|---|---|---|---|
| `pulse` (eine global, treibt `/`) | ✅ | `synthesis.py` | Bei jedem Compile neu gebaut. |
| `daily_digest` | ✅ | `synthesis.py` | Eine pro `data/research/daily/<date>/`. |
| `weekly` | ✅ | `synthesis.py` | Eine pro `data/research/weekly/<iso-week>/`. |
| `topic` | ✅ | `synthesis.py` | Eine pro `ResearchTopic`-/`ApproachFamily`-Cluster ≥ 3 Papers. |
| `comparison` | ✅ | `synthesis.py` | Eine pro Paar von `ApproachFamily`, die auf derselben Task konkurrieren. |
| `field_overview` | ✅ | `synthesis.py` | Eine pro `ResearchField`. |
| LLM-aufgewertete Summaries (env-flag) | ⚠ | nur Hook | Heuristik-Baseline geliefert; Hook `TESSERAE_SYNTHESIS_LLM=1` als Stub belassen. |

### Static-Site-Routen

| Route | Status | Source | Hinweise |
|---|---|---|---|
| `/` (Home, Hero-Pulse) | ✅ | [`tesserae/site/pages.py`](../tesserae/site/pages.py) `render_home` | Stat-Row + kuratierte Einstiegspunkte + Recent Activity. |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | Heatmap + Tagesliste + Synthesis-Rail. |
| `/timeline/<YYYY-MM-DD>.html` (Per-Day-Detail) | ⚠ | n/a yet | Heatmap-Zellen verlinken interimsweise auf die Source-Seite `digest.md` des Tages. Subagent P verdrahtet die Per-Day-Detailseiten durch `StaticSiteBuilder`. |
| `/graph/` (interaktiv 2D + 3D) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, Hover-Tooltips, Edge-Labels, Cursor-verankerter Zoom. |
| `/about.html` | ✅ | `pages.py::render_about` | Schema, Build-Info. |

### KI-freundliche Exporte

| Artefakt | Status | Source | Zweck |
|---|---|---|---|
| Per-Page-`<page>.txt`-Sibling | ✅ | [`tesserae/site/exports.py`](../tesserae/site/exports.py) `write_siblings` | Plain-Text-View einer Seite (keine Nav, kein Styling). |
| Per-Page-`<page>.json`-Sibling | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`. |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | llmstxt.org-Kurzindex. |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | Jeder Page-Body, gedeckelt bei 5 MB. |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | schema.org `Dataset`, nur Wiki-Layer-Knoten. |
| `graph.json` | ✅ | `__init__.py::write_site` | Volles Graph-Payload (inkl. Code-Knoten für Tooling). |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../tesserae/site/search.py) | Palette + Page-Search; nur Wiki-Layer-Kinds. |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | Jede emittierte Route, `lastmod` aus dem Frontmatter. |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | Letzte 30 Syntheses. |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | Permissiv — crawl + index. |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | Maschinenlesbare Site-Map. |
| `manifest.json` | ✅ | `__init__.py::_manifest` | sha256 + Size für jede emittierte Datei (Idempotenz-Harness). |

### Visuelles Design + UX

| Feature | Status | Source | Hinweise |
|---|---|---|---|
| Design Tokens (Light + Dark Themes, Terracotta-Akzent) | ✅ | [`tesserae/site/tokens.py`](../tesserae/site/tokens.py) | Ein CSS-Bundle in `assets/style.css`. |
| Theme-Toggle (persistent, kein Flash) | ✅ | [`tesserae/site/js.py`](../tesserae/site/js.py) | `data-theme="dark"` im `localStorage`, vor Paint angewendet. |
| Search-Palette (`cmd+k` / `ctrl+k` / `/`) | ✅ | `js.py` | Fuzzy-Match über `search-index.json`; Recent-Page-Liste. |
| Sticky-Right-TOC | ✅ | `pages.py` + `tokens.py` | Nur Desktop; Mobile-Drawer via `<details>`. |
| Activity-Heatmap mit Monats- + Wochentag-Labels | ✅ | `components.py::heatmap_svg` | 26-Wochen-SVG, Zellen verlinken auf `digest.md` des Tages. |
| Sparkline (pro Concept/Entity) | ✅ | `components.py::sparkline_svg` | Wöchentliche Mention-Counts, letzte 12 Wochen. |
| Mobile-Shell (Drawer-Rail, Bottom-Nav, fluide Schrift) | ✅ | `tokens.py` + `pages.py` | Touch-Hit-Targets ≥ 44 px. |
| Page Transitions (120 ms Opacity, prefers-reduced-motion) | ✅ | `tokens.py` | |
| 3D + 2D Graph-View (Hover, Edge-Labels, Cursor-verankerter Zoom) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, als CDN-Snapshot vendored. |
| Per-Page AI-Siblings-Footer | ✅ | `components.py::ai_siblings_footer` | Inline-Links zur `.txt` und `.json` der aktuellen Seite. |
| Harness-Session-History-Seiten | ✅ | [`tesserae/harness_sessions.py`](../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../tesserae/site/sessions.py) | Expliziter Claude-Code/Codex-Import; `/sessions/`-Index- und Detailseiten mit Markdown-Turns, Left-Turn-Rail, eingeklapptem Tool-Use und Such-Einträgen. |

### Pipeline + CLI

| Feature | Status | Source | Hinweise |
|---|---|---|---|
| `project compile` ruft Synthese + Wiki + Site in Reihenfolge auf | ✅ | [`tesserae/project.py`](../tesserae/project.py) | Phase 3 des Redesign-Plans. |
| `project build-site` standalone | ✅ | `project.py` + [`tesserae/cli.py`](../tesserae/cli.py) | Liest `wiki/` + `graph.json`, schreibt `site/`. |
| `project serve` lokaler HTTP-Server | ✅ | `cli.py` | Plain Stdlib-Server. |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../tesserae/deploy.py) | Worktree-Push nach `gh-pages`; optional `--enable-pages` via `gh`-CLI. `--build`, `--dry-run`, `--branch`, `--remote`, `--force`. |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../tesserae/harness_sessions.py) + `cli.py` | Inbound-Session-Historie für Claude Code/Codex; Discovery ist explizit und scoped auf das Project-Working-Directory. |
| `project watch` Rebuild-on-Change | ✅ | [`tesserae/cli.py`](../tesserae/cli.py) + [`tesserae/watch.py`](../tesserae/watch.py) | Eigenständiger Polling-Watcher: `--interval`, `--debounce`, `--once`, `--paths`, `--quiet`. Der Multi-Source-Supervisor lebt in `project engine`/`daemon` (siehe Kontext-Engine). |
| `project context` — kompiliert ein zitiertes Kontext-Dokument | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../tesserae/context_compiler.py) | Vorzeige von Säule 3; siehe Abschnitt Kontext-Engine. |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../tesserae/engine/) | Prosaische Aktualisierungskette + Supervisor-Schleife; siehe Abschnitt Kontext-Engine. |

## Vorhandene Features (unverändert übernommen)

### CLI und Installation

- ✅ Installierbares Python-Package via `pyproject.toml`.
- ✅ Console-Commands: `tesserae`, `tesserae`, `tesserae_mcp`.
- ✅ `scripts/install.sh` für `curl | bash`-Installation.
- ✅ Editable Installs als Default für schnelle lokale Entwicklung.

### Extraktion

- ✅ Deterministischer Research-Note-Extraktor mit kontrollierten Node-/Edge-Vokabularien.
- ✅ Claude-CLI-/OAuth-Extraktor für höhere Qualität strukturierter Extraktion ohne API-Keys.
- ✅ Selektives Claude-Routing per Glob und Budget-Limit.
- ✅ Deterministischer Development-Code-Extraktor für Python-Projekte.
- ✅ Batch-Ingest mit Content-Hashing und `--changed-only`-Support.
- ✅ Tolerantes Lesen malformed UTF-8 Quellen.

### Graph-Governance

- ✅ Kontrollierte `ResearchNodeType`-Liste — enthält jetzt `SYNTHESIS`.
- ✅ Kontrollierte Edge-Type-Whitelist — enthält jetzt `synthesizes`, `summarizes`.
- ✅ Validierung, die Schema-Drift ablehnt.
- ✅ Alias-Kanonisierung.
- ✅ Review-Queue für mehrdeutige Near-Duplicate-Knoten.
- ✅ Review-Decisions-Template und Merge/Keep-Separate-Workflow.
- ✅ Korpus-Trend-Zusammenfassung aus Per-File-Graphen.

### Persistenz und Reports

- ✅ Graph-JSON-Export.
- ✅ SQLite-Graph-Store.
- ✅ Optionaler Kuzu-Graph-Store.
- ✅ Graph-Report mit Counts, Evidence-Coverage, Orphan-Nodes, Date-Buckets, Alias-Heavy-Nodes.
- ✅ Competitive-Report, der absorbierte Ideen aus MegaMem, Graphiti/Zep, MCP-Graph-Servern und Agentic RAG beschreibt.

### Projekt-lokaler Workflow

- ✅ `tesserae project init`
- ✅ `tesserae project ingest`
- ✅ `tesserae project compile`
- ✅ `tesserae project mcp-config`
- ✅ `tesserae project build-site`
- ✅ `tesserae project serve`
- ✅ `tesserae project deploy` (GitHub Pages)
- ✅ `tesserae project sessions discover/import/list` (expliziter Import lokaler Agent-Historie)
- ✅ `tesserae project watch` (eigenständiger Polling-Watcher)
- ✅ `tesserae project engine` / `tesserae project daemon` (Supervisor-Schleife — v0.5.0)
- ✅ `tesserae project refresh` (prosaische Kette Aufnahme → Kompilierung → Projektion — v0.5.0)
- ✅ `tesserae project context` (On-Demand-Kontext-Compiler — v0.5.0)
- ✅ `tesserae project export-agent-harness`
- ✅ `tesserae project export-obsidian`
- ✅ `tesserae project export-graphiti`
- ✅ `tesserae project sync-graphiti`

### Obsidian

- ✅ Bereit-zu-öffnen-Vault-Export.
- ✅ `.obsidian/app.json` und Graph-Settings.
- ✅ Markdown-Projektion.
- ✅ `raw/assets/`-Struktur.
- ✅ `_meta/dashboard.md` mit Dataview-Query.

### Agent-Harnesses

Generierte Zieldateien für:

- ✅ Claude Code: `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex: `AGENTS.md`, `mcp.toml`
- ✅ Gemini: `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro: Steering- und MCP-Settings
- ✅ Cursor: Project-Rules und MCP-Config
- ✅ OpenCode: `AGENTS.md`, `opencode.json`

### Graphiti / temporale Fakten

- ✅ Temporal-Fact-Projektion mit Provenance-, Currentness-, Confidence- und Invalidation-Feldern.
- ✅ Dependency-freier Graphiti-Episode-JSONL-Export.
- ✅ `sync-graphiti --dry-run`-Smoke ohne installiertes Graphiti.
- ✅ Optionaler Live-Sync mit `graphiti_core` und Neo4j.

### Cognee

- ✅ Cognee-JSONL-Bundle (`nodes.jsonl`, `edges.jsonl`, `manifest.json`).
- ✅ Optionaler Add-only-Direkt-Import.
- ✅ Optionaler Codex-CLI/OAuth-gestützter Cognee-Cognify-Adapter.
- ✅ Deterministische und Ollama-Embedding-Adapter-Pfade für No-API-Key-Smoke-/Quality-Workflows.

### MCP-Server

- ✅ `tesserae_mcp` / `python3 -m tesserae.mcp_server` über Stdio-JSON-RPC.
- ✅ Retrieval-/Graph-Tools: `schema`, `graph_summary`, `search_nodes`, `node_context` (mit `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`.
- ✅ Kontext-Engine-Tools (v0.5.0): `compile_context`, `embedding_status`, `fresh_insights` (Decay-Ranking), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`, `ask`.
- ✅ Setup-Tools: `tesserae_setup_plan`, `tesserae_setup_apply`.
- ✅ Multi-Project-Registry: `list_projects`, `register_project`, `activate_project`, `unregister_project`, `list_sessions`. Store-URL-Dispatch via `url_resolver`.

## Tests

Die aktuelle Suite deckt ab:

- ✅ Ontologie-Guardrails (inkl. neuem `Synthesis`-Knoten + `synthesizes`-/`summarizes`-Kanten);
- ✅ deterministische Extraktion;
- ✅ Claude-CLI-Wrapper-Parsing/Validierung;
- ✅ selektives Claude-Routing;
- ✅ Kanonisierungs-/Review-Workflow;
- ✅ Batch-Ingest;
- ✅ Reports;
- ✅ SQLite-/Kuzu-Persistenz;
- ✅ Cognee-Bundles/Import-Patches;
- ✅ Graphiti-Export/Sync-Dry-Run;
- ✅ Project-CLI-Workflow;
- ✅ Agent-Harness-Export;
- ✅ Obsidian-Export;
- ✅ Frontend-Generation + Link-Integrität (kein `nodes/codeclass-*.html`);
- ✅ Wiki-Store-Idempotenz;
- ✅ Synthesis-Projector-Golden + Idempotenz;
- ✅ Site-Components, Pages, Exports, Relevance;
- ✅ AI-Sibling-Shape (`.txt` + `.json` pro Seite);
- ✅ End-to-End-Compile-twice-Idempotenz;
- ✅ Engine-Rückgrat: Pipeline, Aktualisierungskette, Daemon-Kern + Quellen, `project engine` CLI;
- ✅ Selbstverbesserungs-Speicher: Sidecar, Decay/Supersede, Supersede-Unterdrückung (inkl. MCP), Reinforce/Widerspruch;
- ✅ Retrieval + Embeddings: Hybrid-Suche, PPR, echte Standard-Embeddings (Phase 6);
- ✅ Kontext-Compiler: Form/Zitat-Integrität/Determinismus/Budget/PPR-Rückfall, `project context` CLI, MCP `compile_context`;
- ✅ inkrementelle Kompilierung (experimentell): Differ, Paritäts-Gates, Herkunfts-Bereitschaft, SQLite-Herkunft;
- ✅ Package-Install und Installer-Contract.

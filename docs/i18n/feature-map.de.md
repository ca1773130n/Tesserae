# Feature-Map

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a></p>
<!-- translations:end -->
Dieses Dokument fasst die aktuell in Tesserae implementierten Features zusammen, mit Status, Quelldateien und Fundstellen der Dokumentation.

Tesserae ist eine **Kontext-Engine**, die auf drei Säulen läuft: (1) Sitzungsüberwachung, (2) autonome proaktive Wissensaufnahme und (3) Dokumente/Kontext auf Abruf. Der typisierte Graph, der Vault und die statische Site sind Projektionen der Wissensbasis. Die Features unten sind danach gruppiert, welcher Säule sie dienen; der Meilenstein **v0.5.0** (Juni 2026) lieferte das Engine-Rückgrat und das Pillar-3-Aushängeschild, den On-Demand-Kontext-Compiler.

Status-Legende: ✅ ausgeliefert · ⚠ in Arbeit / teilweise.

## Cross-Project & UX — v0.11.0 (Juni 2026)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| Cross-Project-Federation | ✅ | [`tesserae/federation.py`](../../tesserae/federation.py) | `ask --scope federated` assembliert EINEN Graph aus mehreren registrierten Projekten — Identity-Merge (gleiche arxiv/repo/hash/symbol) + Opt-out-embedding-gestützte `shares_concept_with`-Links — und liefert eine einzige querverwiesene, zitierte Antwort über die Vereinigung (PPR + `compile_context`). Projektbezogenes `graph.json` ist read-only; deterministisch für Identity-only. |
| Smarter `ask`-Router (kein aktives Projekt) | ✅ | [`tesserae/ask_router.py`](../../tesserae/ask_router.py) | Das Konzept "aktives Projekt" ist entfernt — alle registrierten Projekte sind gleich. Ein nacktes `ask` routet sich selbst (nennt ein Projekt → dieses; komparativ → federated; Follow-up → behält Route; sonst Federated-Fallback), mit optionalem LLM-Tiebreaker und Kontinuität pro Konversation. Projektbezogene Ops lösen das Projekt aus dem cwd auf. |
| Federation-Inspektion | ✅ | `tesserae/federation.py`, `cli.py` | `tesserae federation status` (Knotenzahlen pro Projekt, Identity-Merges, semantische Links) und `federation explain <node>` (warum ein Knoten Projekte überbrückt). |
| Multi-Project-Serve | ✅ | [`tesserae/serve.py`](../../tesserae/serve.py), `cli.py` | Nacktes `tesserae serve` served JEDES registrierte Projekt unter einem Server (Landing unter `/`, jedes unter `/<alias>/`, ein Projects-Switcher im Header, pfad-begrenzt); `--project X` served eines mit dem Live-Ask-Widget. |
| LLM-Konzept-Schicht in `compile` | ✅ | `cli.py`, [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py), [`tesserae/selective_extractor.py`](../../tesserae/selective_extractor.py) | `tesserae compile` baut die Konzept-/Claim-Schicht **standardmäßig** (`--extractor llm`) über den konfigurierten Provider (codex/claude/api gemäß `llm_provider`); `--extractor deterministic` ist der strukturelle, byte-stabile Opt-out; `selective-llm --llm-include … --llm-limit N` ist kostenbewusst. |
| `tesserae setup` (interaktiv) | ✅ | `cli.py`, [`tesserae/deps.py`](../../tesserae/deps.py) | Top-Level `tesserae setup` — standardmäßig interaktiv (LLM-Provider/Effort + welche optionalen Deps); Flags überspringen die Prompts. Installationen funktionieren in pip-losen uv-tool-Umgebungen (uv-pip-Fallback). |

## Interop, Suche & Setup — v0.10.0 (Juni 2026)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| Google-**OKF-v0.1**-Import/Export | ✅ | [`tesserae/okf.py`](../../tesserae/okf.py) | `tesserae export okf [--import DIR]`. Markdown-+-YAML-Frontmatter-Bundle; round-trippt Tesseraes eigene Bundles verlustfrei über einen `x_tesserae`-Namespace, fremde Bundles best-effort. |
| Schnelle Transkript-Suche (memex) | ✅ | [`tesserae/memex_search.py`](../../tesserae/memex_search.py) | `nicosuave/memex`-BM25-Index über Claude-/Codex-Transkripte, verdrahtet mit dem `tesserae serve`-Sessions-Dashboard via `GET /api/transcript-search`. Optional + graceful, wenn abwesend. |
| Read-Discipline-Handles | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | `compile_context` `preview=N` liefert eine begrenzte Vorschau + ein content-keyed Handle; `get_handle` paginiert den Rest. Hält riesige Payloads aus dem Kontext des Agenten. |
| Extraktions-Qualitätssignale | ✅ | [`tesserae/session_graph_llm.py`](../../tesserae/session_graph_llm.py) | `confidence` + `confidence_rationale` + `revisit_signals` pro Befund (byte-stabil; in `fresh_insights` angezeigt). |
| Maschinenweites Setup + Deps | ✅ | [`tesserae/deps.py`](../../tesserae/deps.py), `cli.py` | `tesserae setup` schreibt globale LLM-Defaults + installiert optionale Deps (memex, raganything); `tesserae config deps` listet/installiert; `tesserae init` bietet memex an. Projektbezogene Config überschreibt weiterhin. |

## Kontext-Engine — v0.5.0 (Juni 2026)

Das Engine-Rückgrat, das die drei Säulen antreibt. Siehe [`docs/architecture.md`](architecture.de.md) für die Engine-Rückgrat-Modul-Map, das Selbstverbesserungs-Memory-Sidecar und den Kontext-Compiler-Datenfluss.

### Engine-Rückgrat (Säulen 1 & 2)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| `Pipeline` — wiederverwendbare Refresh-Kette, gibt `List[StepResult]` zurück | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | Ein Step-Runner, den CLI, Daemon und MCP alle aufrufen. Fängt `Exception` pro Step; stoppt beim ersten Fehler. |
| `Daemon` — Single-Owner-asyncio-Supervisor | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | Beobachtet Quellen + Vault + Harness-Session-Verzeichnis; debouncter Cancel-and-Reschedule fasst einen Burst zu einem einzigen `Pipeline.run()` zusammen. Pidfile; überlebt In-Flight-Exceptions. |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`, `--debounce`, `--once`. `daemon` ist ein Alias von `engine`. |
| `project refresh` — Prosa-Kette (ingest → compile → project) | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only` (Opt-in-inkrementell), `--no-sessions`. |
| Live-Session-Monitor → Findings | ✅ | `harness_sessions.py` + Session-Graph-Module | Importierte Sessions speisen den Graph; `fresh_insights` / `find_session_findings` zeigen sie an. |

### Selbstverbesserungs-Memory (Säule 2)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| `node_memory`-SQLite-Sidecar (decay / confidence / superseded) | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + store-agnostische Accessoren; nur mutierbarer Zustand. First-seen lebt im separaten `node_provenance`-Sidecar. |
| Ebbinghaus-Decay-Score | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | Rankt Session-Findings am neuesten + meistzugegriffen zuerst (treibt `fresh_insights`). |
| Supersede-Pass (**default-on**) | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | Deterministisches Verdikt markiert einen älteren Beinahe-Duplikat-Insight als von einem neueren superseded; fügt eine `supersedes`-Kante hinzu. |
| Insight-→-Code-Symbol-Verlinkung | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `discusses`-Kanten von Session-Insights zu den Symbolen, auf die sie verweisen. |
| Reinforce- + Contradiction-Pässe | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Access-Reinforcement + Widerspruchserkennung über dasselbe Sidecar. |
| Numerische Wiederkehr-Confidence im Output | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | Temporale Fakten stempeln `confidence` aus `NodeMemoryRow.confidence`, mit Fallback auf `infer_confidence`. |

### Retrieval + Embeddings (Säulen 2 & 3)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| Hybrid-Retriever (BM25 + lexikalisch + Embedding, RRF k=60) | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | Local-first, vollständig deterministisch. |
| Personalized PageRank (HippoRAG-2) | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | Multi-Hop-Seed-Expansion; tiefenbegrenzter Subgraph. |
| Echte Default-Embeddings (Track B, Phase 6) | ✅ | `retrieval/hybrid.py` | Default = deterministisches Hash-Bucket-Pseudo-Embedding (keine Deps); `sentence-transformers` (`all-MiniLM-L6-v2`) bevorzugt, lazy geladen, wenn installiert. Das MCP-Tool `embedding_status` meldet das aktive Backend. |

### On-Demand-Kontext-Compiler (Säule 3 — Aushängeschild)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| `compile_context` — zitiertes In-Memory-`ContextBundle` | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Seed-Auflösung → PPR-Expansion → budget-begrenzte Auswahl → zitiertes Markdown → optionale LLM-Synthese. Deterministisch, außer `synthesize=true`. Schreibt nichts auf die Platte. |
| `project context`-CLI | ✅ | `cli.py` | `[query]`, `--seeds`, `--depth` (2), `--budget` (32000; ≤0 = unbegrenzt), `--llm`, `--output`. |
| `compile_context`-MCP-Tool | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Dieselbe Pipeline über MCP; `budget=0` ist unbegrenzt. |
| Topic-begrenzte Export-Slices | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | Topic-begrenzte `llms.txt` + `render_harness_context` via `compile_context`. |

### Inkrementeller Compile (Phase 4 — experimentell)

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| Provenance-Sidecar (`node_provenance`, first-seen) | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | Fundament für Changed-only-Deletes; wird immer aufgezeichnet. |
| `GraphStore`-Delete-Oberfläche | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`, `delete_nodes_by_source` (verwirft Knoten, deren Provenance-Menge sich leert; dateiübergreifende Konzepte überleben). |
| `url_resolver`-Runtime-Store-Dispatch | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`. |
| `incremental_compile`-Flag | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **Default OFF / experimentell.** Byte-Parität für mehrere Edit-Formen bewiesen, aber Multi-Owner-/Producer-Lifecycle-Lücken bleiben; Full-Compile bleibt der Default. |

## Frontend-Redesign — April 2026

Ein dokument-orientiertes, hierarchisches Wiki ersetzt den alten Graph-Dump. Siehe [`docs/frontend-redesign.md`](frontend-redesign.de.md) für die Route-für-Route-Tour und [`docs/architecture.md`](architecture.de.md) für das Drei-Schichten-Modell.

### Wiki-Schicht (L2-Markdown)

| Feature | Status | Quelle | Doc-Anker |
|---|---|---|---|
| `WikiPageStore` (idempotente Body-Hash-Writes, Frontmatter-Parser) | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § Modul-Map](architecture.de.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — eine md-Seite pro Wiki-Schicht-Knoten | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § Pipeline](architecture.de.md#pipeline) |
| `sources/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.de.md#sources) |
| `concepts/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.de.md#concepts) |
| `entities/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.de.md#entities) |
| `papers/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.de.md#papers) |
| `repos/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.de.md#repos) |
| `topics/`-Seiten | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.de.md#topics) |
| `questions/`-Seiten (Open Questions) | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.de.md#questions) |
| `syntheses/`-Seiten | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.de.md#syntheses) |

### Synthesis-Arten (L2 → abgeleitet)

`SynthesisProjector` produziert sieben deterministische Templates und fügt `Synthesis`-Knoten + `synthesizes`- / `summarizes`-Kanten zurück in den Graph.

| Art | Status | Quelle | Notizen |
|---|---|---|---|
| `pulse` (eine global, treibt `/`) | ✅ | `synthesis.py` | Bei jedem Compile neu gebaut. |
| `daily_digest` | ✅ | `synthesis.py` | Eine pro `data/research/daily/<date>/`. |
| `weekly` | ✅ | `synthesis.py` | Eine pro `data/research/weekly/<iso-week>/`. |
| `topic` | ✅ | `synthesis.py` | Eine pro `ResearchTopic`- / `ApproachFamily`-Cluster ≥ 3 Paper. |
| `comparison` | ✅ | `synthesis.py` | Eine pro Paar von `ApproachFamily`, die auf derselben Task konkurrieren. |
| `field_overview` | ✅ | `synthesis.py` | Eine pro `ResearchField`. |
| LLM-aufgewertete Summaries (env-geflaggt) | ⚠ | nur Hook | Heuristische Baseline ist ausgeliefert; der `TESSERAE_SYNTHESIS_LLM=1`-Hook bleibt ein Stub. |

### Statische Site-Routen

| Route | Status | Quelle | Notizen |
|---|---|---|---|
| `/` (Home, Hero-Pulse) | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | Stat-Zeile + kuratierte Einstiegspunkte + jüngste Aktivität. |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | Heatmap + Tagesliste + Synthesis-Leiste. |
| `/timeline/<YYYY-MM-DD>.html` (Tages-Detail) | ⚠ | noch n/a | Heatmap-Zellen verlinken interimistisch auf die `digest.md`-Quellseite des Tages. Subagent P verdrahtet die Tages-Detailseiten durch `StaticSiteBuilder`. |
| `/graph/` (interaktiv 2D + 3D) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, Hover-Tooltips, Kantenlabels, cursor-verankerter Zoom. |
| `/about.html` | ✅ | `pages.py::render_about` | Schema, Build-Infos. |

### KI-freundliche Exporte

| Artefakt | Status | Quelle | Zweck |
|---|---|---|---|
| Per-Page-`<page>.txt`-Geschwister | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | Klartext-Ansicht einer Seite (keine Nav, kein Styling). |
| Per-Page-`<page>.json`-Geschwister | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`. |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | Kurzer Index nach llmstxt.org. |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | Jeder Seiten-Body, gedeckelt bei 5 MB. |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | schema.org-`Dataset`, nur Wiki-Schicht-Knoten. |
| `graph.json` | ✅ | `__init__.py::write_site` | Voller Graph-Payload (inkl. Code-Knoten für Tooling). |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | Palette + Seitensuche; nur Wiki-Schicht-Arten. |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | Jede emittierte Route, `lastmod` aus dem Frontmatter. |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | Die letzten 30 Syntheses. |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | Permissiv — Crawlen + Indexieren. |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | Maschinenlesbare Site-Map. |
| `manifest.json` | ✅ | `__init__.py::_manifest` | sha256 + Größe für jede emittierte Datei (Idempotenz-Harness). |

### Visuelles Design + UX

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| Design-Tokens (Light- + Dark-Themes, Terrakotta-Akzent) | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Ein CSS-Bundle in `assets/style.css`. |
| Theme-Toggle (persistiert, kein Flash) | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `data-theme="dark"` in `localStorage`, vor dem Paint angewandt. |
| Suchpalette (`cmd+k` / `ctrl+k` / `/`) | ✅ | `js.py` | Fuzzy-Match über `search-index.json`; Liste kürzlich besuchter Seiten. |
| Sticky rechtes TOC | ✅ | `pages.py` + `tokens.py` | Nur Desktop; Mobile-Drawer via `<details>`. |
| Aktivitäts-Heatmap mit Monats- + Wochentagslabels | ✅ | `components.py::heatmap_svg` | 26-Wochen-SVG, Zellen verlinken auf die `digest.md` des Tages. |
| Sparkline (pro Konzept/Entity) | ✅ | `components.py::sparkline_svg` | Wöchentliche Erwähnungszahlen, letzte 12 Wochen. |
| Mobile-Shell (Drawer-Leiste, Bottom-Nav, fluide Typografie) | ✅ | `tokens.py` + `pages.py` | Touch-Hit-Targets ≥ 44 px. |
| Seitenübergänge (120 ms Opacity, prefers-reduced-motion) | ✅ | `tokens.py` | |
| 3D- + 2D-Graphansicht (Hover, Kantenlabels, cursor-verankerter Zoom) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, als CDN-Snapshot gevendort. |
| Per-Page-KI-Geschwister-Footer | ✅ | `components.py::ai_siblings_footer` | Inline-Links zur `.txt` und `.json` der aktuellen Seite. |
| Harness-Session-Historie-Seiten | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Expliziter Claude-Code-/Codex-Import; `/sessions/`-Index- und Detailseiten mit Markdown-Turns, linker Turn-Leiste, eingeklapptem Tool-Use und Sucheinträgen. |

### Pipeline + CLI

| Feature | Status | Quelle | Notizen |
|---|---|---|---|
| `project compile` ruft Synthesis + Wiki + Site der Reihe nach auf | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | Phase 3 des Redesign-Plans. |
| `project build-site` standalone | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | Liest `wiki/` + `graph.json`, schreibt `site/`. |
| `project serve` lokales HTTP | ✅ | `cli.py` | Reiner stdlib-Server. |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | Worktree-Push nach `gh-pages`; optionales `--enable-pages` via `gh`-CLI. `--build`, `--dry-run`, `--branch`, `--remote`, `--force`. |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Eingehende Session-Historie für Claude Code/Codex; Discovery ist explizit und auf das Projekt-Arbeitsverzeichnis begrenzt. |
| `project watch` Rebuild-on-Change | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | Eigenständiger Polling-Watcher: `--interval`, `--debounce`, `--once`, `--paths`, `--quiet`. Der Multi-Source-Supervisor lebt unter `project engine`/`daemon` (siehe Kontext-Engine). |
| `project context` — kompiliert ein zitiertes Kontextdokument | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Pillar-3-Aushängeschild; siehe Abschnitt Kontext-Engine. |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | Prosa-Refresh-Kette + Supervisor-Schleife; siehe Abschnitt Kontext-Engine. |

## Vorbestehende Features (unverändert weitergeführt)

### CLI und Installation

- ✅ Installierbares Python-Paket via `pyproject.toml`.
- ✅ Console-Befehle: `tesserae`, `tesserae`, `tesserae_mcp`.
- ✅ `scripts/install.sh` für `curl | bash`-Installation.
- ✅ Editierbare Installationen als Default für schnelle lokale Entwicklung.

### Extraktion

- ✅ Deterministischer Research-Note-Extraktor mit kontrollierten Knoten-/Kanten-Vokabularen.
- ✅ Claude-CLI-/OAuth-Extraktor für höherwertige strukturierte Extraktion ohne API-Keys.
- ✅ Selektives Claude-Routing nach Glob und Budget-Limit.
- ✅ Deterministischer Entwicklungs-Code-Extraktor für Python-Projekte.
- ✅ Batch-Ingest mit Content-Hashing und `--changed-only`-Unterstützung.
- ✅ Toleranz für fehlgeformtes UTF-8 beim Quellenlesen.

### Graph-Governance

- ✅ Kontrollierte `ResearchNodeType`-Liste — enthält jetzt `SYNTHESIS`.
- ✅ Kontrollierte Kantentyp-Whitelist — enthält jetzt `synthesizes`, `summarizes`.
- ✅ Validierung zur Zurückweisung von Schema-Drift.
- ✅ Alias-Kanonisierung.
- ✅ Review-Queue für mehrdeutige Beinahe-Duplikat-Knoten.
- ✅ Review-Decisions-Template und Merge-/Keep-Separate-Workflow.
- ✅ Korpus-Trend-Zusammenfassung aus Per-File-Graphen.

### Persistenz und Reports

- ✅ Graph-JSON-Export.
- ✅ SQLite-Graph-Store.
- ✅ Optionaler Kuzu-Graph-Store.
- ✅ Graph-Report mit Zählungen, Evidence-Abdeckung, verwaisten Knoten, Datums-Buckets, alias-lastigen Knoten.
- ✅ Competitive-Report zu übernommenen Ideen aus MegaMem, Graphiti/Zep, MCP-Graph-Servern, agentic RAG.

### Projektlokaler Workflow

- ✅ `tesserae init --bare`
- ✅ `tesserae compile <paths>`
- ✅ `tesserae compile`
- ✅ `tesserae projects mcp-config`
- ✅ `tesserae export site`
- ✅ `tesserae serve`
- ✅ `tesserae export site --deploy` (GitHub Pages)
- ✅ `tesserae sessions discover/import/list` (expliziter lokaler Agent-Historie-Import)
- ✅ `tesserae export site --watch` (eigenständiger Polling-Watcher)
- ✅ `tesserae engine` (Supervisor-Schleife — v0.5.0)
- ✅ `tesserae refresh` (Prosa-Kette ingest → compile → project — v0.5.0)
- ✅ `tesserae context` (On-Demand-Kontext-Compiler — v0.5.0)
- ✅ `tesserae export harness`
- ✅ `tesserae vault export`
- ✅ `tesserae export graphiti`
- ✅ `tesserae export graphiti --sync`

### Obsidian

- ✅ Sofort öffenbarer Vault-Export.
- ✅ `.obsidian/app.json` und Graph-Einstellungen.
- ✅ Markdown-Projektion.
- ✅ `raw/assets/`-Struktur.
- ✅ `_meta/dashboard.md` mit Dataview-Query.

### Agent-Harnesses

Generierte Target-Dateien für:

- ✅ Claude Code: `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex: `AGENTS.md`, `mcp.toml`
- ✅ Gemini: `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro: Steering- und MCP-Einstellungen
- ✅ Cursor: Projekt-Regeln und MCP-Config
- ✅ OpenCode: `AGENTS.md`, `opencode.json`

### Graphiti / temporale Fakten

- ✅ Temporale Fakt-Projektion mit Provenance-, Currentness-, Confidence- und Invalidierungsfeldern.
- ✅ Abhängigkeitsfreier Graphiti-Episoden-JSONL-Export.
- ✅ `sync-graphiti --dry-run`-Smoke ohne installiertes Graphiti.
- ✅ Optionaler Live-Sync mit `graphiti_core` und Neo4j.

### MCP-Server

- ✅ `tesserae_mcp` / `python3 -m tesserae.mcp_server` über stdio JSON-RPC.
- ✅ Retrieval-/Graph-Tools: `schema`, `graph_summary`, `search_nodes`, `node_context` (mit `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`.
- ✅ Kontext-Engine-Tools (v0.5.0): `compile_context`, `embedding_status`, `fresh_insights` (decay-gerankt), `list_communities`, `find_session_findings`, `ask`.
- ✅ Setup-Tools: `tesserae_setup_plan`, `tesserae_setup_apply`.
- ✅ Multi-Projekt-Registry: `list_projects`, `register_project`, `unregister_project`, `list_sessions`. Store-URL-Dispatch via `url_resolver`.

## Tests

Die aktuelle Suite deckt ab:

- ✅ Ontologie-Leitplanken (inkl. neuem `Synthesis`-Knoten + `synthesizes`- / `summarizes`-Kanten);
- ✅ deterministische Extraktion;
- ✅ Claude-CLI-Wrapper-Parsing/-Validierung;
- ✅ selektives Claude-Routing;
- ✅ Kanonisierungs-/Review-Workflow;
- ✅ Batch-Ingest;
- ✅ Reports;
- ✅ SQLite-/Kuzu-Persistenz;
- ✅ Graphiti-Export/Sync-Dry-Run;
- ✅ Projekt-CLI-Workflow;
- ✅ Agent-Harness-Export;
- ✅ Obsidian-Export;
- ✅ Frontend-Generierung + Link-Integrität (kein `nodes/codeclass-*.html`);
- ✅ Wiki-Store-Idempotenz;
- ✅ Synthesis-Projector-Golden + -Idempotenz;
- ✅ Site-Komponenten, -Seiten, -Exporte, -Relevanz;
- ✅ KI-Geschwister-Form (`.txt` + `.json` pro Seite);
- ✅ End-to-End-Compile-zweimal-Idempotenz;
- ✅ Engine-Rückgrat: Pipeline, Refresh-Kette, Daemon-Core + -Quellen, `project engine`-CLI;
- ✅ Selbstverbesserungs-Memory: Sidecar, Decay/Supersede, Supersede-Suppression (inkl. MCP), Reinforce/Contradiction;
- ✅ Retrieval + Embeddings: Hybrid-Suche, PPR, echte Default-Embeddings (Phase 6);
- ✅ Kontext-Compiler: Form/Zitat-Integrität/Determinismus/Budget/PPR-Fallback, `project context`-CLI, MCP `compile_context`;
- ✅ inkrementeller Compile (experimentell): Differ, Paritäts-Gates, Provenance-Readiness, SQLite-Provenance;
- ✅ Paketinstallation und Installer-Vertrag.

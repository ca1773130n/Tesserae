# Arquitectura

<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a> · <a href="architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae es un **motor de contexto**. Reconstruye una base de conocimiento auto-mejorable a partir de tu proyecto y se la entrega a los agentes como contexto listo para usar. Corre sobre tres pilares: (1) **monitorización de sesiones** — observar sesiones vivas de agentes/trabajo y capturar hallazgos según ocurren; (2) **ingesta de conocimiento autónoma y proactiva** — un pipeline + bucle supervisor que absorbe y re-extrae conocimiento continuamente, mejorando la base en lugar de esperar a que se lo pidan; (3) **docs/contexto bajo demanda** — artefactos solicitados por el usuario compilados desde esa misma base. El grafo tipado, el vault markdown y el sitio estático son *proyecciones* de la base de conocimiento; el engine es el bucle que los mantiene frescos y alimenta a los agentes.

Por debajo, Tesserae convierte un directorio de material fuente en un grafo de conocimiento tipado y controlado, y proyecta ese grafo a través de una capa wiki markdown durable hacia un sitio web estático y amigable para IA. El rediseño de abril de 2026 reorganizó el lado de la proyección alrededor de un modelo de tres capas de Karpathy: la evidencia en bruto queda en bruto, un grafo tipado gobierna la ontología, y una capa wiki markdown se sitúa entre el grafo y cualquier salida renderizada. El sitio estático es un *renderizador* de esa capa wiki en lugar de un volcado directo del grafo, con la ontología controlada en [`tesserae/research_graph.py`](../../tesserae/research_graph.py) como esquema. El hito **v0.5.0** (junio 2026) añadió la espina dorsal del engine que impulsa los tres pilares — ver *Espina del engine* y *Compilador de contexto bajo demanda* más abajo.

## El modelo de tres capas de Karpathy

El planteamiento de Andrej Karpathy para bases de conocimiento amigables con LLM distingue tres capas, cada una con su propia garantía de durabilidad:

| Capa | Preocupación | Ubicación en el repo | Propietario |
|---|---|---|---|
| L1 — Fuentes en bruto | Los bytes literales que el usuario escribió o recolectó. Append-only. | `data/`, `docs/`, árboles de proyecto referenciados en `.tesserae/config.json` | el usuario |
| L2 — Wiki | Páginas markdown tipadas (sources, concepts, entities, papers, repos, topics, syntheses, questions) con frontmatter YAML. Idempotente: regenerada en cada compilación, pero reescrita solo cuando los hashes de contenido cambian. | `.tesserae/wiki/` | `WikiPageStore`, `WikiLayerProjector`, `SynthesisProjector` |
| L3 — Renderizado | El sitio HTML estático, exports AI-sibling, índice de búsqueda, sitemaps, JSON-LD. Borrado y reescrito en cada compilación, pero byte-estable entre reruns. | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

El esquema atraviesa las tres capas como un eje aparte: `ResearchGraph` en `graph.json` es la ontología controlada contra la que enlazan las páginas L2, y el `ResearchNodeType` / whitelist de aristas en [`tesserae/research_graph.py`](../../tesserae/research_graph.py) es la fuente de verdad de qué tipos existen siquiera.

El rediseño añadió L2 explícitamente. Antes de abril de 2026 el sitio estático se proyectaba directamente desde `graph.json`; la capa wiki existía solo dentro del export del vault de Obsidian. Separarla nos dio:

- Una única superficie editable por humanos (abre `.tesserae/wiki/` en Obsidian o cualquier editor de markdown).
- Reconstrucciones idempotentes: volver a ejecutar `project compile` produce cero diffs de archivos a menos que el contenido fuente cambiara.
- Un log de evolución: las páginas de síntesis se acumulan con el tiempo y dejan que el proyecto se narre a sí mismo.

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

Cada paso es incremental. El extractor del grafo usa los hashes de contenido de `manifest.json` para saltarse los archivos fuente sin cambios. `WikiPageStore.write_page` devuelve `False` (y se salta la escritura) cuando el hash del cuerpo coincide con lo que ya hay en disco. `StaticSiteBuilder` borra y reescribe `.tesserae/site/`, pero su salida es determinista — ver "Historia de idempotencia" más abajo.

## Dataflow del compilador de contexto

El compilador de contexto bajo demanda ([`tesserae/context_compiler.py`](../../tesserae/context_compiler.py)) es la ruta estrella del Pilar 3. Dada una consulta y/o ids de nodos semilla explícitos, `compile_context` construye un bundle markdown a medida y con citas directamente desde el grafo y lo devuelve en memoria — no escribe nada bajo `.tesserae/`.

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
     ▼  3. Budget-bound selection
        walk PPR order, include each node's cited body until the next would overflow
        `budget` chars (budget <= 0 = uncapped; over-budget marker on a word boundary)
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

Defaults: `depth=2`, `budget=32000`. El ensamblado determinista (pasos 1–4) es el contrato; la síntesis LLM es puramente aditiva. El mismo pipeline respalda el comando CLI `project context`, la herramienta MCP `compile_context` y los slices de export acotados por topic (`slice_export_context_for_topic`, `llms.txt` acotado por topic).

## Mapa de módulos

### Wiki + síntesis (L2)

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | Dataclass `WikiPage`, `WikiPageStore` para I/O de filesystem. Parser de frontmatter con subconjunto de YAML solo-stdlib. Idempotencia por hash del cuerpo. |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector`: mapea cada nodo de `ResearchGraph` de tipo capa-wiki a una página markdown en la carpeta `kind/` correcta. |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector`: plantillas deterministas para pulse, daily_digest, weekly, topic, comparison, field_overview. Añade nodos `Synthesis` y aristas `synthesizes` / `summarizes` de vuelta al grafo. |

### Grafo + ontología

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | Enum `ResearchNodeType` (incl. `SYNTHESIS`), whitelist de tipos de arista (incl. `synthesizes`, `summarizes`), validación. |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) | Canonicalización de alias + cola de revisión de casi-duplicados. |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | Extractor AST de Python determinista para el slice de desarrollo. |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Extractor selectivo Claude CLI/OAuth. |

### Renderizador del sitio (L3)

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`: borra + reconstruye el sitio, recorre cada ruta, emite exports + AI siblings + manifest. |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) | Un renderizador por ruta (home, índices, páginas de detalle, timeline, graph, about). `SiteContext` lleva índices precomputados para que los renderizadores se mantengan puros. |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | Primitivas HTML: `breadcrumbs`, `card`, `badge`, `node_table`, `edge_list`, `sparkline_svg`, `heatmap_svg`, `toc`, `page_shell`, `ai_siblings_footer`. |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Tokens de diseño — variables CSS, temas claro + oscuro, layout, tipografía, todos los componentes estilados aquí. |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) | Bundle JS del cliente: paleta de búsqueda, toggle de tema, vista de grafo sigma + 3D-force. |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) | Renderizador de markdown solo-stdlib (enlaces, autolinks, código, énfasis, encabezados). Sin dependencia externa. |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) | Puntuación de relevancia de cuatro señales (enlace directo, solape de fuentes, Adamic-Adar, afinidad de tipo) usada por cada sección `Related`. |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | Constructor de `search-index.json`. Solo tipos de la capa wiki. |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Renderizadores de índice/detalle de sesión para el historial de harness importado: secciones de resumen de memoria del proyecto, rail de turnos de conversación, renderizado markdown de transcripts y bloques de tool-use colapsados. |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, siblings `.txt`/`.json` por página. |

### Orquestación del pipeline

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile`: dirige extracción → grafo → pases de memoria → capa wiki → sitio. Posee `ProjectPaths` (`config`, `graph`, `manifest`, `wiki`, `site`, etc.). Decide de antemano si una compilación incremental guiada por procedencia es elegible (condicionada a `incremental_compile`, OFF por defecto). |
| [`tesserae/cli.py`](../../tesserae/cli.py) | Dispatch de CLI de verbos planos (~2.732 líneas tras borrar los grupos de subcomandos legacy `project`/`wiki`). Los verbos — `init`, `compile`, `ingest`, `context`, `ask`, `query`, `doctor`, `summary`, `decisions`, `refresh`, `serve`, `engine`, `export`, `vault`, `code`, `lab`, `setup`, `config`, `projects`, `sources`, `federation`, `integrations` — se declaran como metadatos en [`tesserae/cli_tree.py`](../../tesserae/cli_tree.py) y se cablean desde ese árbol en lugar de registrarse a mano. |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `export site --deploy`: empuja `.tesserae/site/` a una rama `gh-pages` vía worktree, opcionalmente habilita Pages vía `gh`. |

### Espina del engine (v0.5.0 — pilares 1 y 2)

La espina del engine es el bucle in-process que impulsa la monitorización de sesiones y la re-ingesta autónoma. El mismo `Pipeline.run()` es la única ruta de refresco a la que llaman la CLI, el daemon supervisor y (más adelante) el servidor MCP.

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline`: un ejecutor de pasos secuencial. Codifica la cadena de refresco en prosa (ingest → compile → project/publish) como un objeto importable que devuelve un `List[StepResult]` estructurado en lugar de imprimir-y-salir, de modo que cada llamador decide cómo exponer los resultados. `run()` captura `Exception` por paso (deja pasar `KeyboardInterrupt`/`SystemExit`) y se detiene en el primer fallo. |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon`: supervisor asyncio de propietario único. Vigila los directorios fuente, el vault de Obsidian y el directorio de sesiones de harness; coalesce una ráfaga de `TriggerEvent`s en exactamente un `Pipeline.run()` vía un debounce cancel-and-reschedule. Reutiliza los watchers existentes de `watch.py` / `vault_watch.py` (no los reescribe), escribe un pidfile y sobrevive a excepciones en vuelo. Expuesto como `engine` (`--interval`, `--debounce`, `--once`). |
| [`tesserae/watch.py`](../../tesserae/watch.py), [`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | Watchers de sondeo reutilizados tanto por el comando standalone `export site --watch` como por las vías fuente/vault del daemon. |

### Memoria de auto-mejora (v0.5.0 — pilar 2)

La Fase 5 activó la auto-mejora persistente. El estado mutable por nodo vive en un sidecar SQLite `node_memory` (dentro de `.tesserae/sqlite.db`), separado del sello inmutable de first-seen `node_provenance.first_seen_at` (un sidecar de la Fase 4). La compilación dirige un conjunto de pases deterministas sobre el grafo.

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + accesores agnósticos del store (`read_memory`, `write_memory`, `bump_access`) sobre la tabla `node_memory` — `decay_score`, `last_accessed_at`, `confidence`, `superseded`. Ningún call site embebe SQL en bruto. |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score`: score de frescura estilo Ebbinghaus (más nuevo + más accedido primero) usado para rankear los hallazgos de sesión. |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass` (**activado por defecto**): veredicto determinista que marca un insight casi-duplicado más antiguo como reemplazado por uno más nuevo, añadiendo una arista `supersedes`. |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`: enlaza los insights de sesión con los símbolos de código que discuten vía aristas `discusses`. |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Helpers de refuerzo por acceso y detección de contradicciones sobre el mismo sidecar. |

La confianza por recurrencia es numérica en la salida: la proyección temporal estampa el `confidence` de cada hecho desde `NodeMemoryRow.confidence` (texto en SQLite, expuesto vía `temporal.py`), recayendo en `infer_confidence` solo cuando no existe valor almacenado.

### Recuperación (v0.5.0 — pilares 2 y 3)

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search`: recuperador híbrido local-first que fusiona tres vías — Okapi BM25 (k1=1.5, b=0.75), substring léxico/estilo-FTS con case-folding, y una vía de embeddings enchufable — vía reciprocal-rank fusion (RRF, k=60). Totalmente determinista. |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank`: Personalized PageRank estilo HippoRAG-2 (arXiv:2502.14802) sobre el grafo para expansión de semillas multi-hop — hace aflorar nodos bien conectados a varios saltos de la semilla, no solo el vecindario a 1 salto. |
| Backend de embeddings (Fase 6, Track B) | El backend por defecto de la vía de embeddings del híbrido es un pseudo-embedding determinista de hash-bucket que no necesita deps extra; `sentence-transformers` (`all-MiniLM-L6-v2`) es el preferido y se carga perezosamente cuando la dependencia opcional está instalada. La herramienta MCP `embedding_status` reporta qué backend está activo. |

### Compilador de contexto bajo demanda (v0.5.0 — titular del pilar 3)

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context`: la función estrella del Pilar 3. Compila un bundle de contexto a medida y **con citas** para una consulta/conjunto de semillas directamente desde el grafo — ver *Dataflow del compilador de contexto* arriba. Devuelve un `ContextBundle` en memoria (con `ContextCitation`s); no escribe nada a disco. Expuesto como el comando CLI `project context` y la herramienta MCP `compile_context`. |

### Puertos de persistencia + graph stores

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | Protocolo `GraphStore`: `upsert_node`/`upsert_edge`, `get_node`, `iterate_nodes`, `query_subgraph`, `find_canonical`, y la superficie de delete de la Fase 4 — `delete_node` y `delete_nodes_by_source` (elimina los nodos cuyo conjunto de procedencia queda vacío tras retirar las rutas fuente dadas, así los conceptos multi-archivo sobreviven). |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`: backing store standalone; posee las tablas sidecar `node_provenance` y `node_memory`. |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | Resuelve una URL de store (`sqlite:///…`, `hypepaper-postgres://…`) al `GraphStore` correcto, permitiendo al servidor MCP apuntar a cualquier backing store en runtime. |

### Adaptadores externos (sin cambios en esta ronda)

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Proyección de vault de Obsidian (coloreado del grafo, dashboard de Dataview, assets en bruto). |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Exports de harness Claude Code / Codex / Gemini / Kiro / Cursor / OpenCode. |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | Descubrimiento, normalización y almacenamiento entrante de sesiones Claude Code/Codex bajo `.tesserae/harness_sessions/`, y resúmenes markdown redactados. |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) | JSONL de hechos temporales + sync opcional en vivo con Graphiti. |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Servidor MCP stdio. Recuperación/grafo: `schema`, `graph_summary`, `search_nodes`, `node_context` (con `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`. Motor de contexto (v0.5.0): `compile_context` (el compilador de contexto bajo demanda), `embedding_status`, `fresh_insights` (hallazgos de sesión rankeados por decay), `list_communities`, `find_session_findings`. Más `ask`, las herramientas del registro multi-proyecto (`list_projects`, `register_project`, `unregister_project`, `list_sessions`), y `tesserae_setup_plan` / `tesserae_setup_apply`. |

## Layout del workspace del proyecto

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
  competitive_report.md       comparison vs. MegaMem / Graphiti / others
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

Cada archivo es editable a mano; la siguiente compilación honra las ediciones del usuario mientras el hash del cuerpo difiera de lo que el proyector escribiría. (Editar solo el cuerpo gana; editar el frontmatter pierde en la siguiente compilación porque el frontmatter se regenera.) Los usuarios de Obsidian pueden abrir `.tesserae/wiki/` directamente; el adaptador `obsidian_vault/` existente es una proyección separada, no un sustituto.

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

## Qué queda deliberadamente excluido

El rediseño trazó una línea explícita: los nodos code-class y code-function se quedan en `graph.json` (para que los consumidores MCP y Graphiti sigan viéndolos) pero nunca reciben páginas HTML, nunca aparecen en `search-index.json` y nunca aparecen en la navegación. Ese es el contrato de cara al usuario — la wiki es una base de conocimiento document-first, no un navegador de funciones.

Concretamente, `StaticSiteBuilder` se salta cualquier nodo cuyo tipo no esté en el mapa de tipos wiki L2 (`tesserae/wiki_projector.py::_KIND_FOR_TYPE`):

- Excluidos de L2 + L3: `CodeClass`, `CodeFunction`, `CodeModule`, `Dependency`, `EvidenceSpan`, `SourceFile`, todas las variantes de `Claim` (`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`).
- Superficie donde sí siguen apareciendo: como bullets, badges, recuentos de vecinos o extractos de evidencia inline en las páginas wiki relacionadas, y en `graph.json` para tooling aguas abajo.

Si necesitas navegación a nivel de código, apunta una herramienta LSP / call-graph al árbol de fuentes directamente — es un problema distinto de "la wiki de lo que este proyecto sabe".

## Historia de idempotencia

El rediseño apunta a **salida byte-idéntica entre dos ejecuciones consecutivas de `project compile` sobre inputs sin cambios**. Las piezas:

1. **La extracción de fuentes** usa los hashes de contenido de `manifest.json`; los archivos sin cambios se saltan, así que el grafo permanece estable.
2. **Las escrituras de la capa wiki** son idempotentes a nivel de cuerpo. `WikiPageStore.write_page` lee el archivo existente, quita el frontmatter, hace sha256 del cuerpo y cortocircuita si el nuevo cuerpo hashea igual — incluso si el nuevo frontmatter tiene un timestamp `generated_at` distinto. Ese es el truco clave que mantiene los diffs de git compactos al reconstruir.
3. **La salida de síntesis** lleva un `content_hash: sha256-…` en su frontmatter. El hash del cuerpo se computa sin `generated_at`, así que compilaciones repetidas sobre el mismo grafo producen el mismo hash, y los nodos `Synthesis` llevan el mismo `content_hash` en los metadatos del grafo.
4. **El renderizado del sitio** borra `site/` al inicio de `write_site`, y luego escribe de forma determinista: las rutas se ordenan, los diccionarios se vuelcan con `sort_keys=True`, `manifest.json` se recorre vía `sorted(rglob("*"))`. Dos ejecuciones producen archivos byte-idénticos incluido el manifest.

Esto lo verifican `tests/test_site_pages.py` y el humo end-to-end en `tests/test_project_e2e_redesign.py` (compilar dos veces, diff de los sitios, esperar cero deltas de archivos).

## Notas de escalado

- **Tope de nodos en la vista de grafo.** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py) acota el payload embebido en la página para el layout de fuerzas interactivo. Más allá de ~1500 nodos la simulación en el navegador se vuelve lenta en hardware medio, así que la página descarta primero los nodos de la capa wiki de menor grado cuando el recuento supera el tope. El `graph.json` exportado no se ve afectado — siempre contiene el grafo completo. Los nodos de código se filtran antes de aplicar el tope.
- **Tope de `llms-full.txt`.** Un tope de seguridad de 5 MB aplica en [`tesserae/site/exports.py`](../../tesserae/site/exports.py); el archivo termina con un marcador `[TRUNCATED — see graph.jsonld for the full set]` si se alcanza el tope. `graph.jsonld` no tiene tope porque los consumidores de JSON-LD esperan el conjunto completo.
- **Índice de búsqueda.** Solo tipos de la capa wiki. Los nodos del grafo de código nunca entran en `search-index.json`; el objetivo del rediseño es < 500 KB para el corpus dogfood y hoy estamos muy por debajo.
- **Presupuesto de bytes por página (regla general).** Cada página de detalle < 60 KB de HTML gz, CSS compartido < 30 KB, JS compartido < 25 KB, vendor de sigma solo en la página del grafo (~60 KB). La vista del grafo usa 3D-force-graph + Three.js cargado una vez; todas las demás páginas se quedan en vanilla.
- **Tiempo de compilación en dogfood.** ~300 archivos markdown se extraen en menos de 5 s en una máquina de desarrollo reciente; el renderizado del sitio añade otros ~2 s. La idempotencia de la capa wiki hace que las compilaciones subsiguientes toquen solo las rutas cambiadas.

## Superficie de interacción del frontend

- **Paleta de búsqueda** — `cmd+k` / `ctrl+k` / `/`. Coincidencia difusa sobre `search-index.json`, acotada a tipos wiki. Páginas recientes persistidas en `localStorage`.
- **Toggle de tema** — botón arriba a la derecha; `data-theme="dark"` se guarda en `localStorage` y se aplica antes del paint para evitar el flash.
- **TOC derecho pegajoso** — solo escritorio; colapsa a un drawer `<details>` en móvil. Generado desde los `<h2>` / `<h3>` del cuerpo de la página.
- **Heatmap de actividad** — SVG de 26 semanas con etiquetas de mes + día de la semana. Las celdas enlazan a la página fuente `digest.md` del día cuando existe. (Las páginas de detalle de timeline por día — `/timeline/<YYYY-MM-DD>.html` — son un follow-up explícito; el aviso inline en `render_timeline` lo señala. ⚠ en progreso.)
- **Vista de grafo** — `/graph/`. Layout de fuerzas 3D (3d-force-graph + Three.js) con tooltips al pasar el cursor, etiquetas de aristas, zoom anclado al cursor, y una vista 2D de respaldo. Los colores de nodo vienen de `ResearchNodeType`.
- **Shell móvil** — rail drawer, nav inferior, tipografía fluida, objetivos táctiles seguros (≥ 44 px).

## Estrategia de testing

- **Unit** — `tests/test_wiki_store.py`, `tests/test_synthesis.py`, `tests/test_site_components.py`, `tests/test_site_pages.py`, `tests/test_site_exports.py`, `tests/test_relevance.py`.
- **Espina del engine** — `tests/test_pipeline.py`, `tests/test_refresh_pipeline.py`, `tests/test_daemon_core.py`, `tests/test_daemon_sources.py`, `tests/test_cli_engine.py`.
- **Memoria de auto-mejora** — `tests/test_memory_sidecar.py`, `tests/test_decay_supersede.py`, `tests/test_supersede_suppression.py`, `tests/test_mcp_supersede_suppression.py`, `tests/test_memory_contradiction_reinforce.py`.
- **Recuperación + embeddings** — `tests/test_hybrid_search.py`, `tests/test_ppr.py`, `tests/test_real_embeddings_phase6.py`.
- **Compilador de contexto** — `tests/test_context_compiler.py` (forma, integridad de citas, determinismo, presupuesto, fallback de PPR), `tests/test_cli_context.py`, `tests/test_mcp_server_context.py`.
- **Compilación incremental (experimental)** — `tests/test_incremental_compile.py`, `tests/test_incremental_parity.py`, `tests/test_provenance_readiness.py`, `tests/test_sqlite_provenance.py`.
- **Idempotencia** — `tests/test_project_e2e_redesign.py` compila dos veces y asevera cero diffs en `wiki/` y `site/`.
- **Integridad de enlaces** — `tests/test_frontend.py` parsea cada HTML emitido en busca de hrefs y asevera que cada enlace interno resuelve a un archivo generado. No se produce ningún `nodes/codeclass-*.html`.
- **AI siblings** — para cada `path/foo.html`, la suite de tests asevera que `path/foo.txt` y `path/foo.json` existen; el JSON parsea y contiene `{title, kind, body, links}`.
- **Sin Playwright** — pytest vanilla bajo `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.

## Docs relacionados

- [Quickstart](quickstart.es.md) — camino mínimo desde `project init` a un sitio navegable.
- [Recorrido del rediseño del frontend](frontend-redesign.es.md) — tour anotado de cada ruta.
- [Mapa de funciones](feature-map.es.md) — qué está entregado, qué está en progreso, con punteros a archivos.
- [Demo self-dogfood](self-dogfood.es.md) — ejecutar Tesserae contra su propio repo.

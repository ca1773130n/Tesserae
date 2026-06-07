# Arquitectura

<!-- translations:start -->
<p align="center"><a href="../architecture.md">English</a> · <a href="architecture.ko.md">한국어</a> · <a href="architecture.zh.md">中文</a> · <a href="architecture.ja.md">日本語</a> · <a href="architecture.ru.md">Русский</a> · <a href="architecture.es.md">Español</a> · <a href="architecture.fr.md">Français</a> · <a href="architecture.de.md">Deutsch</a></p>
<!-- translations:end -->
Tesserae es un **motor de contexto**. Reconstruye una base de conocimiento que se automejora a partir de tu proyecto y la entrega a los agentes como contexto listo para usar. Funciona sobre tres pilares: (1) **monitoreo de sesiones** — observar sesiones en vivo de agentes/trabajo y capturar hallazgos a medida que ocurren; (2) **ingesta de conocimiento autónoma y proactiva** — un pipeline + bucle supervisor que extrae y reextrae conocimiento de forma continua, mejorando la base en lugar de esperar instrucciones; (3) **documentos/contexto bajo demanda** — artefactos solicitados por el usuario compilados desde esa misma base. El grafo tipado, el almacén (vault) markdown y el sitio estático son *proyecciones* de la base de conocimiento; el motor es el bucle que las mantiene frescas y alimenta a los agentes.

Por debajo, Tesserae convierte un directorio de material fuente en un grafo de conocimiento controlado y tipado, y proyecta ese grafo mediante una capa wiki duradera en markdown hacia un sitio web estático y amigable para IA. El rediseño de abril de 2026 reorganizó el lado de las proyecciones alrededor de un modelo de tres capas de Karpathy: la evidencia cruda permanece cruda, un grafo tipado gobierna la ontología y una capa wiki en markdown se sitúa entre el grafo y cualquier salida renderizada. El sitio estático es un *renderizador* de esa capa wiki, no un volcado directo del grafo, con la ontología controlada de [`tesserae/research_graph.py`](../../tesserae/research_graph.py) como esquema. El hito **v0.5.0** (junio de 2026) añadió la columna del motor que impulsa los tres pilares — véanse *Columna del motor* y *Compilador de contexto bajo demanda* más abajo.

## El modelo de tres capas de Karpathy

El marco de Andrej Karpathy para bases de conocimiento amigables para LLM distingue tres capas, cada una con su propia garantía de durabilidad:

| Capa | Responsabilidad | Ubicación en el repositorio | Propietario |
|---|---|---|---|
| L1 — Fuentes crudas | Los bytes literales que el usuario escribió o recopiló. Solo append-only. | `data/`, `docs/`, árboles de proyecto referenciados en `.tesserae/config.json` | el usuario |
| L2 — Wiki | Páginas markdown tipadas (sources, concepts, entities, papers, repos, topics, syntheses, questions) con YAML frontmatter. Idempotente: se regenera en cada compilación, pero solo se reescribe cuando cambian los hashes de contenido. | `.tesserae/wiki/` | `WikiPageStore`, `WikiLayerProjector`, `SynthesisProjector` |
| L3 — Renderizado | El sitio HTML estático, exportaciones AI-sibling, índice de búsqueda, sitemaps, JSON-LD. Se borra y se reescribe en cada compilación, pero es estable a nivel de bytes entre ejecuciones. | `.tesserae/site/` | `StaticSiteBuilder` (`tesserae/site/`) |

El esquema cruza las tres capas como un eje separado: `ResearchGraph` en `graph.json` es la ontología controlada contra la que enlazan las páginas L2, y `ResearchNodeType` / la whitelist de aristas en [`tesserae/research_graph.py`](../../tesserae/research_graph.py) es la fuente de verdad sobre qué tipos existen.

El rediseño añadió L2 explícitamente. Antes de abril de 2026 el sitio estático se proyectaba directamente desde `graph.json`; la capa wiki solo existía dentro de la exportación del vault de Obsidian. Separarla nos dio:

- Una única superficie editable por humanos (abrir `.tesserae/wiki/` en Obsidian o en cualquier editor markdown).
- Reconstrucciones idempotentes: volver a ejecutar `project compile` produce cero diferencias de archivos salvo que el contenido fuente haya cambiado.
- Un registro de evolución: las páginas de síntesis se acumulan con el tiempo y permiten que el proyecto se narre a sí mismo.

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

Cada paso es incremental. El extractor del grafo usa hashes de contenido de `manifest.json` para omitir archivos fuente sin cambios. `WikiPageStore.write_page` devuelve `False` (y omite la escritura) cuando el hash del cuerpo coincide con lo que ya está en disco. `StaticSiteBuilder` borra y reescribe `.tesserae/site/`, pero su salida es determinista; consulta “Historia de idempotencia” más abajo.

## Flujo de datos del compilador de contexto

El compilador de contexto bajo demanda ([`tesserae/context_compiler.py`](../../tesserae/context_compiler.py)) es la ruta estrella del Pilar 3. Dada una consulta y/o ids de nodos semilla explícitos, `compile_context` construye un paquete markdown a medida y **con citas** directamente desde el grafo y lo devuelve en memoria — no escribe nada bajo `.tesserae/`.

```
query / seeds
     │
     ▼  1. Resolución de semillas
        semillas explícitas (se conservan solo si existen en el grafo) + aciertos de hybrid_search(), dedup, orden estable
     │
     ▼  2. Expansión PPR
        retrieval.ppr.personalized_pagerank clasifica el vecindario de k saltos con profundidad limitada;
        resultado vacío (semillas desconectadas) → vuelta al orden de semillas (el paquete nunca está vacío)
     │
     ▼  3. Selección limitada por presupuesto
        recorre el orden PPR, incluyendo el cuerpo citado de cada nodo hasta que el siguiente
        cuerpo desborde `budget` caracteres (budget <= 0 = sin límite; marcador de exceso en límite de palabra)
     │
     ▼  4. Ensamblado de markdown con citas
        una sección por nodo seleccionado + un bloque final `## Citations`.
        El cuerpo prefiere la página wiki proyectada (cuando existen un store y un tipo wiki público),
        si no la descripción del nodo, si no un stub mínimo. El cuerpo sin LLM no incrusta ninguna
        marca de tiempo de reloj → idéntico byte a byte para el mismo (graph, query, seeds, depth, budget).
     │
     ▼  5. Síntesis LLM opcional  (solo cuando synthesize=true Y existe ANTHROPIC_API_KEY)
     ▼
   ContextBundle { query, seeds_used, ranked_nodes, selected_nodes,
                   citations[ContextCitation], body, synthesized,
                   char_budget_used, char_budget_total }
```

Valores por defecto: `depth=2`, `budget=32000`. El ensamblado determinista (pasos 1–4) es el contrato; la síntesis LLM es puramente aditiva. El mismo pipeline respalda el comando CLI `project context`, la herramienta MCP `compile_context` y los recortes de exportación por tema (`slice_export_context_for_topic`, `llms.txt` por tema).

## Mapa de módulos

### Wiki + síntesis (L2)

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | Dataclass `WikiPage`, `WikiPageStore` para I/O de sistema de archivos. Parser de frontmatter YAML-subset solo con stdlib. Idempotencia por hash del cuerpo. |
| [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | `WikiLayerProjector`: asigna cada nodo `ResearchGraph` de un tipo de capa wiki a una página markdown en la carpeta `kind/` correcta. |
| [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | `SynthesisProjector`: plantillas deterministas para pulse, daily_digest, weekly, topic, comparison, field_overview. Añade nodos `Synthesis` y aristas `synthesizes` / `summarizes` de vuelta al grafo. |

### Grafo + ontología

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/research_graph.py`](../../tesserae/research_graph.py) | Enum `ResearchNodeType` (incl. `SYNTHESIS`), whitelist de tipos de arista (incl. `synthesizes`, `summarizes`), validación. |
| [`tesserae/canonicalization.py`](../../tesserae/canonicalization.py) | Canonicalización de alias + cola de revisión de casi duplicados. |
| [`tesserae/code_graph.py`](../../tesserae/code_graph.py) | Extractor determinista de AST de Python para el corte de desarrollo. |
| [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py) | Extractor selectivo Claude CLI/OAuth. |

### Renderizador del sitio (L3)

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/site/__init__.py`](../../tesserae/site/__init__.py) | `StaticSiteBuilder.write_site`: borra + reconstruye el sitio, recorre todas las rutas, emite exportaciones + AI siblings + manifest. |
| [`tesserae/site/pages.py`](../../tesserae/site/pages.py) | Un renderizador por ruta (home, indexes, detail pages, timeline, graph, about). `SiteContext` lleva índices precalculados para que los renderizadores permanezcan puros. |
| [`tesserae/site/components.py`](../../tesserae/site/components.py) | Primitivas HTML: `breadcrumbs`, `card`, `badge`, `node_table`, `edge_list`, `sparkline_svg`, `heatmap_svg`, `toc`, `page_shell`, `ai_siblings_footer`. |
| [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Design tokens — variables CSS, temas claro + oscuro, layout, tipografía; todos los componentes se estilizan aquí. |
| [`tesserae/site/js.py`](../../tesserae/site/js.py) | Bundle JS del cliente: search palette, theme toggle, sigma + 3D-force graph view. |
| [`tesserae/site/markdown.py`](../../tesserae/site/markdown.py) | Renderizador markdown solo con stdlib (links, autolinks, code, emphasis, headings). Sin dependencia externa. |
| [`tesserae/site/relevance.py`](../../tesserae/site/relevance.py) | Scoring de relevancia con cuatro señales (direct link, source overlap, Adamic-Adar, type affinity) usado por cada sección `Related`. |
| [`tesserae/site/search.py`](../../tesserae/site/search.py) | Constructor de `search-index.json`. Solo wiki-layer kinds. |
| [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Renderizadores de índice/detalle de sesiones para historial harness importado: secciones de project-memory summary, rail de turnos de conversación, renderizado de markdown transcript y bloques tool-use colapsados. |
| [`tesserae/site/exports.py`](../../tesserae/site/exports.py) | `llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, siblings `.txt`/`.json` por página. |

### Orquestación del pipeline

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/project.py`](../../tesserae/project.py) | `ProjectWiki.compile`: conduce extraction → graph → pasadas de memoria → wiki layer → site. Posee `ProjectPaths` (`config`, `graph`, `manifest`, `wiki`, `site`, etc.). Decide por adelantado si una compilación incremental basada en procedencia (provenance) es elegible (controlada por `incremental_compile`, por defecto OFF). |
| [`tesserae/cli.py`](../../tesserae/cli.py) | Despacho de CLI por verbos planos (~2.732 líneas tras eliminar los grupos de subcomandos heredados `project`/`wiki`). Los verbos —`init`, `compile`, `context`, `ask`, `refresh`, `serve`, `engine`, `export`, `vault`, `code`, `lab`, `config`, `projects`, `integrations`— se declaran como metadatos en [`tesserae/cli_tree.py`](../../tesserae/cli_tree.py) y se cablean desde ese árbol en lugar de registrarse a mano. |
| [`tesserae/deploy.py`](../../tesserae/deploy.py) | `export site --deploy`: empuja `.tesserae/site/` a una rama `gh-pages` vía worktree, opcionalmente habilita Pages mediante `gh`. |

### Columna del motor (v0.5.0 — pilares 1 & 2)

La columna del motor es el bucle en proceso que impulsa el monitoreo de sesiones y la reingesta autónoma. El mismo `Pipeline.run()` es la única ruta de actualización que invocan la CLI, el demonio supervisor y (más adelante) el servidor MCP.

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | `Pipeline`: ejecutor secuencial de pasos. Codifica la cadena de actualización en prosa (ingesta → compilación → proyección/publicación) como un objeto importable que devuelve un `List[StepResult]` estructurado en lugar de imprimir-y-salir, de modo que cada llamador decide cómo presentar los resultados. `run()` captura `Exception` por paso (deja pasar `KeyboardInterrupt`/`SystemExit`) y se detiene en el primer fallo. |
| [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | `Daemon`: supervisor asyncio de único propietario. Vigila los directorios fuente, el almacén Obsidian y el directorio de sesiones de harness; mediante un debounce de cancelar-y-reprogramar fusiona una ráfaga de `TriggerEvent` en exactamente un `Pipeline.run()`. Reutiliza los observadores existentes `watch.py` / `vault_watch.py` (no los reescribe), escribe un pidfile y sobrevive a excepciones en vuelo. Expuesto como `engine` (`--interval`, `--debounce`, `--once`). |
| [`tesserae/watch.py`](../../tesserae/watch.py), [`tesserae/vault_watch.py`](../../tesserae/vault_watch.py) | Observadores por sondeo reutilizados por el comando autónomo `export site --watch` y por las líneas de fuente/almacén del demonio. |

### Memoria de automejora (v0.5.0 — pilar 2)

La Fase 5 activó la automejora persistente. El estado mutable por nodo vive en un sidecar `node_memory` SQLite (dentro de `.tesserae/sqlite.db`), separado de la marca inmutable de primera aparición `node_provenance.first_seen_at` (sidecar de la Fase 4). La compilación ejecuta un conjunto de pasadas deterministas sobre el grafo.

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + accesores independientes del almacén (`read_memory`, `write_memory`, `bump_access`) sobre la tabla `node_memory` — `decay_score`, `last_accessed_at`, `confidence`, `superseded`. Ningún sitio de llamada incrusta SQL crudo. |
| [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | `compute_decay_score`: puntuación de frescura estilo Ebbinghaus (más nuevo + más accedido primero) usada para ordenar hallazgos de sesión. |
| [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | `run_supersede_pass` (**activado por defecto**): veredicto determinista que marca un insight casi-duplicado más antiguo como reemplazado por uno más nuevo, añadiendo una arista `supersedes`. |
| [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | `run_insight_symbol_link_pass`: enlaza insights de sesión con los símbolos de código que discuten mediante aristas `discusses`. |
| [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Ayudantes de refuerzo de acceso y detección de contradicciones sobre el mismo sidecar. |

La confianza de recurrencia es numérica en la salida: la proyección temporal sella la `confidence` de cada hecho desde `NodeMemoryRow.confidence` (texto en SQLite, expuesto vía `temporal.py`), recurriendo a `infer_confidence` solo cuando no existe un valor almacenado.

### Recuperación (v0.5.0 — pilares 2 & 3)

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | `hybrid_search`: recuperador híbrido local-first que fusiona tres carriles — Okapi BM25 (k1=1.5, b=0.75), coincidencia léxica/estilo FTS de subcadenas sin distinción de mayúsculas y un carril de embeddings enchufable — vía fusión de rangos recíprocos (RRF, k=60). Totalmente determinista. |
| [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | `personalized_pagerank`: PageRank personalizado estilo HippoRAG-2 (arXiv:2502.14802) sobre el grafo para expansión multi-salto de semillas — saca a la superficie nodos bien conectados a varios saltos de la semilla, no solo el vecindario de 1 salto. |
| Backend de embeddings (Fase 6, Track B) | El backend por defecto del carril de embeddings del híbrido es un pseudo-embedding determinista por cubos de hash que no necesita dependencias extra; se prefiere `sentence-transformers` (`all-MiniLM-L6-v2`) y se carga de forma diferida cuando la dependencia opcional está instalada. La herramienta MCP `embedding_status` informa qué backend está activo. |

### Compilador de contexto bajo demanda (v0.5.0 — estrella del Pilar 3)

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | `compile_context`: la función estrella del Pilar 3. Compila un paquete de contexto **con citas** a medida para un conjunto de consultas/semillas directamente desde el grafo — véase *Flujo de datos del compilador de contexto* más abajo. Devuelve un `ContextBundle` en memoria (con `ContextCitation`); no escribe nada en disco. Expuesto como el comando CLI `project context` y la herramienta MCP `compile_context`. |

### Puertos de persistencia + almacenes de grafo

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | Protocolo `GraphStore`: `upsert_node`/`upsert_edge`, `get_node`, `iterate_nodes`, `query_subgraph`, `find_canonical` y la superficie de borrado de la Fase 4 — `delete_node` y `delete_nodes_by_source` (borra nodos cuyo conjunto de procedencia queda vacío tras quitar las rutas fuente dadas, de modo que los conceptos entre archivos sobreviven). |
| [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | `SqliteGraphStore`: almacén de respaldo autónomo; posee las tablas sidecar `node_provenance` y `node_memory`. |
| [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | Resuelve una URL de almacén (`sqlite:///…`, `hypepaper-postgres://…`) al `GraphStore` adecuado, permitiendo al servidor MCP apuntar a cualquier almacén de respaldo en tiempo de ejecución. |

### Adaptadores externos (sin cambios en esta ronda)

| Módulo | Responsabilidad |
|---|---|
| [`tesserae/obsidian_adapter.py`](../../tesserae/obsidian_adapter.py) | Proyección de vault Obsidian (coloreado del grafo, Dataview dashboard, raw assets). |
| [`tesserae/agent_harness.py`](../../tesserae/agent_harness.py) | Exportaciones harness de Claude Code / Codex / Gemini / Kiro / Cursor / OpenCode. |
| [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) | Descubrimiento de sesiones entrantes Claude Code/Codex, normalización, almacenamiento bajo `.tesserae/harness_sessions/` y resúmenes markdown redactados. |
| [`tesserae/graphiti_adapter.py`](../../tesserae/graphiti_adapter.py) | Temporal-fact JSONL + sincronización live Graphiti opcional. |
| [`tesserae/cognee_adapter.py`](../../tesserae/cognee_adapter.py) | Bundle JSONL de nodos/aristas Cognee y ruta directa add/cognify. |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Servidor MCP stdio. Recuperación/grafo: `schema`, `graph_summary`, `search_nodes`, `node_context` (con `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`. Motor de contexto (v0.5.0): `compile_context` (el compilador de contexto bajo demanda), `embedding_status`, `fresh_insights` (hallazgos de sesión ordenados por decaimiento), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`. Además `ask`, las herramientas del registro multiproyecto (`list_projects`, `register_project`, `activate_project`, `unregister_project`, `list_sessions`) y `tesserae_setup_plan` / `tesserae_setup_apply`. |

## Layout del workspace del proyecto

```text
.tesserae/
  config.json                 project name, source kind, source list
  graph.json                  validated ResearchGraph (incl. Synthesis nodes)
  manifest.json               per-source content hashes (input dedup)
  sqlite.db                   SQLite graph store; también posee las tablas sidecar node_provenance
                              (primera aparición, Fase 4) y node_memory (decaimiento / confianza /
                              reemplazado, Fase 5)
  temporal_facts.jsonl        Graphiti-style temporal projection (confianza de recurrencia numérica)
  graphiti_episodes.jsonl     dependency-free Graphiti episode export
  report.md                   graph quality / summary
  competitive_report.md       comparison vs. MegaMem / Graphiti / others
  markdown_projection/        flat human-readable markdown
  obsidian_vault/             Obsidian projection w/ .obsidian/, raw/assets/
  agent_harness/              Claude Code / Codex / etc. harness files
  harness_sessions/           imported local Claude Code/Codex sessions
  cognee_bundle/              Cognee nodes/edges/manifest JSONL
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

Cada archivo se puede editar a mano; la siguiente compilación respeta las ediciones del usuario siempre que el hash del cuerpo difiera de lo que escribiría el projector. (Editar solo el cuerpo gana; editar el frontmatter pierde en la siguiente compilación porque el frontmatter se regenera.) Los usuarios de Obsidian pueden abrir `.tesserae/wiki/` directamente; el adaptador existente `obsidian_vault/` es una proyección separada, no un sustituto.

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

## Qué se excluye deliberadamente

El rediseño trazó una línea explícita: los nodos code-class y code-function permanecen en `graph.json` (de modo que los consumidores MCP, Cognee y Graphiti todavía los ven), pero nunca obtienen páginas HTML, nunca aparecen en `search-index.json` y nunca aparecen en la navegación. Ese es el contrato de cara al usuario: la wiki es una base de conocimiento centrada en documentos, no un navegador de funciones.

En concreto, `StaticSiteBuilder` omite cualquier nodo cuyo tipo no esté en el mapa de tipos wiki L2 (`tesserae/wiki_projector.py::_KIND_FOR_TYPE`):

- Excluidos de L2 + L3: `CodeClass`, `CodeFunction`, `CodeModule`, `Dependency`, `EvidenceSpan`, `SourceFile`, todas las variantes `Claim` (`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`).
- Superficies donde aún aparecen: como bullets, badges, conteos de vecinos o extractos de evidencia inline en páginas wiki relacionadas, y en `graph.json` para tooling downstream.

Si necesitas navegación a nivel de código, apunta una herramienta LSP / call-graph directamente al árbol fuente: ese es un problema diferente de “wiki de lo que este proyecto sabe”.

## Historia de idempotencia

El rediseño busca **salida byte a byte idéntica en dos ejecuciones consecutivas de `project compile` sobre entradas sin cambios**. Las piezas:

1. **Extracción de fuentes** usa hashes de contenido de `manifest.json`; los archivos sin cambios se omiten, así que el grafo permanece estable.
2. **Escrituras de la capa wiki** son idempotentes a nivel del cuerpo. `WikiPageStore.write_page` lee el archivo existente, elimina el frontmatter, calcula sha256 del cuerpo y hace short-circuit si el nuevo cuerpo tiene el mismo hash, incluso si el nuevo frontmatter tiene un timestamp `generated_at` distinto. Este es el truco clave que mantiene los git diffs ajustados en reconstrucciones.
3. **Salida de síntesis** lleva `content_hash: sha256-…` en su frontmatter. El hash del cuerpo se calcula sin `generated_at`, de modo que compilaciones repetidas sobre el mismo grafo producen el mismo hash, y los nodos `Synthesis` llevan el mismo `content_hash` en la metadata del grafo.
4. **Renderizado del sitio** borra `site/` al inicio de `write_site`, luego escribe de forma determinista: las rutas se ordenan, los diccionarios se vuelcan con `sort_keys=True`, `manifest.json` se recorre mediante `sorted(rglob("*"))`. Dos ejecuciones producen archivos byte a byte idénticos, incluido el manifest.

Esto se verifica con `tests/test_site_pages.py` y el smoke end-to-end de `tests/test_project_e2e_redesign.py` (compilar dos veces, diferenciar sitios, esperar cero deltas de archivo).

## Notas de escalado

- **Límite de nodos de la vista del grafo.** [`MAX_GRAPH_NODES = 1500`](../../tesserae/site/pages.py) limita el payload embebido en la página para el layout de fuerza interactivo. Por encima de ~1500 nodos la simulación del navegador se vuelve lenta en hardware medio, así que la página descarta primero los nodos wiki-layer de menor grado cuando el conteo supera el límite. El `graph.json` exportado no se ve afectado: siempre contiene el grafo completo. Los code nodes se filtran antes de aplicar el límite.
- **Límite de `llms-full.txt`.** Se aplica un límite de seguridad de 5 MB en [`tesserae/site/exports.py`](../../tesserae/site/exports.py); si se alcanza el límite, el archivo termina con el marcador `[TRUNCATED — see graph.jsonld for the full set]`. `graph.jsonld` no tiene límite porque los consumidores JSON-LD esperan el conjunto completo.
- **Índice de búsqueda.** Solo wiki-layer kinds. Los code-graph nodes nunca entran en `search-index.json`; el objetivo del rediseño es < 500 KB para el corpus dogfood y hoy estamos muy por debajo.
- **Presupuesto de bytes por página (regla práctica).** Cada detail page < 60 KB gz HTML, shared CSS < 30 KB, shared JS < 25 KB, sigma vendor solo en la graph page (~60 KB). La graph view usa 3D-force-graph + Three.js cargados una sola vez; todas las demás páginas permanecen vanilla.
- **Tiempo de compilación en dogfood.** ~300 archivos markdown se extraen en menos de 5 s en una máquina de desarrollo reciente; el render del sitio añade otros ~2 s. La idempotencia de la capa wiki significa que las compilaciones posteriores solo tocan rutas cambiadas.

## Superficie de interacción frontend

- **Search palette** — `cmd+k` / `ctrl+k` / `/`. Fuzzy match sobre `search-index.json`, limitado a wiki kinds. Las páginas recientes se persisten en `localStorage`.
- **Theme toggle** — botón superior derecho; `data-theme="dark"` se guarda en `localStorage` y se aplica antes del paint para evitar flash.
- **Sticky right TOC** — solo desktop; se colapsa en un drawer `<details>` en mobile. Generado desde `<h2>` / `<h3>` en el body de la página.
- **Activity heatmap** — SVG de 26 semanas con etiquetas de month + weekday. Las celdas enlazan a la source page `digest.md` del día cuando existe. (Per-day timeline detail pages — `/timeline/<YYYY-MM-DD>.html` — son un follow-up explícito; el aviso inline en `render_timeline` lo marca. ⚠ in-progress.)
- **Graph view** — `/graph/`. 3D force layout (3d-force-graph + Three.js) con hover tooltips, edge labels, zoom anclado al cursor y 2D fallback view. Los colores de nodos vienen de `ResearchNodeType`.
- **Mobile shell** — drawer rail, bottom nav, fluid type, touch-safe hit targets (≥ 44 px).

## Estrategia de pruebas

- **Unit** — `tests/test_wiki_store.py`, `tests/test_synthesis.py`, `tests/test_site_components.py`, `tests/test_site_pages.py`, `tests/test_site_exports.py`, `tests/test_relevance.py`.
- **Columna del motor** — `tests/test_pipeline.py`, `tests/test_refresh_pipeline.py`, `tests/test_daemon_core.py`, `tests/test_daemon_sources.py`, `tests/test_cli_engine.py`.
- **Memoria de automejora** — `tests/test_memory_sidecar.py`, `tests/test_decay_supersede.py`, `tests/test_supersede_suppression.py`, `tests/test_mcp_supersede_suppression.py`, `tests/test_memory_contradiction_reinforce.py`.
- **Recuperación + embeddings** — `tests/test_hybrid_search.py`, `tests/test_ppr.py`, `tests/test_real_embeddings_phase6.py`.
- **Compilador de contexto** — `tests/test_context_compiler.py` (forma, integridad de citas, determinismo, presupuesto, repliegue PPR), `tests/test_cli_context.py`, `tests/test_mcp_server_context.py`.
- **Compilación incremental (experimental)** — `tests/test_incremental_compile.py`, `tests/test_incremental_parity.py`, `tests/test_provenance_readiness.py`, `tests/test_sqlite_provenance.py`.
- **Idempotencia** — `tests/test_project_e2e_redesign.py` compila dos veces y afirma cero diffs en `wiki/` y `site/`.
- **Integridad de enlaces** — `tests/test_frontend.py` parsea todos los HTML emitidos para hrefs y afirma que cada enlace interno resuelve a un archivo generado. No se produce `nodes/codeclass-*.html`.
- **AI siblings** — para cada `path/foo.html`, la suite de pruebas afirma que existen `path/foo.txt` y `path/foo.json`; el JSON parsea y contiene `{title, kind, body, links}`.
- **Sin Playwright** — pytest vanilla bajo `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`.

## Documentos relacionados

- [Inicio rápido](quickstart.es.md) — ruta mínima desde `project init` hasta un sitio navegable.
- [Recorrido del rediseño frontend](frontend-redesign.es.md) — tour anotado de cada ruta.
- [Mapa de funciones](feature-map.es.md) — qué está enviado, qué está in-progress, con punteros a archivos.
- [Demo self-dogfood](self-dogfood.es.md) — ejecutar Tesserae contra su propio repositorio.

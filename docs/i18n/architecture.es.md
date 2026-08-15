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
     ▼  2b. Reserva procedimental (ganada, no concedida)
        un hueco por pool, en el orden de PROCEDURAL_POOL_ORDER: Runbook, Gotcha, Event,
        DistilledNote, ExpertiseProfile. El hueco va al nodo mejor clasificado de ese tipo
        que lleve procedencia de PRODUCTOR, no al que solo tenga el nombre del tipo
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

**Por qué el hueco procedimental se gana con procedencia.** Los cinco tipos
procedimentales nombran lo que un agente hizo, aprendió a hacer y se le da bien,
pero la extracción documental también puede acuñarlos: un LLM que lee una
convocatoria de artículos acuña legítimamente un `Event` tipado llamado "CVPR
2026". La reserva es *aditiva*: promueve un nodo desde cualquier punto del
vecindario al frente del recorrido presupuestado. Reservar solo por tipo dejaría,
por tanto, que una fecha límite de congreso desalojara al hallazgo de sesión que
sí se ganó el hueco. Lo que separa ambos casos es `has_producer_provenance`, y una
reserva es una pretensión sobre un hueco, no la prueba de haberlo ocupado:
`delivered` se resuelve después del recorrido presupuestado, de modo que quien
llama distingue "se reservó memoria procedimental" de "llegó memoria
procedimental". El código de lint `PROCEDURAL_POOLS` informa de esa diferencia.


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
| [`tesserae/merge_ledger.py`](../../tesserae/merge_ledger.py) | `.tesserae/merge-ledger.json`: la lápida perdedor→superviviente para cada duplicado que una compilación colapsa, de modo que una id de una compilación anterior se resuelve en lugar de devolver un bare not-found. **Estado derivado, no historial** — la publicación une con lo que hay, luego mantiene un registro solo mientras su perdedor esté ausente del grafo recién publicado *y* la cadena que sale de él cae en un nodo que está presente. Un perdedor que vuelve a la vida se descarta. Se lee solo después de que el grafo mismo falla, lo que garantiza que una id viva nunca pueda ser redirigida. |
| [`tesserae/candidate_ledger.py`](../../tesserae/candidate_ledger.py) | `.tesserae/candidate-same-as.json`: veredictos pendientes / confirmados / rechazados por par candidato de fusión, claveteado en el par de id de nodo ordenado y nada más — score, razón y backend están deliberadamente fuera de la clave, siendo exactamente la rotación que un veredicto debe sobrevivir. **Acumulado, lo opuesto del ledger de fusión**: una fusión es estado derivado, un veredicto humano es la única cosa en el pipeline que una máquina no puede re-derivar, por lo que nada aquí se poda nunca. Los dos no deben compartir código. |
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
| [`tesserae/retrieval/vector_cache.py`](../../tesserae/retrieval/vector_cache.py) | La tabla `node_vectors` en el sidecar SQLite, y el único acceso que todos los tres sitios `.embed(` enrutan a través. Claveteada en `(backend_name, backend_dim, sha256(embedded_text))` — la identidad aquí es el **texto** embebido, no la id de nodo, de modo que un nodo sin cambios da en el caché después de una recompilación completa o un movimiento del proyecto o una reescritura de canonicalización de su id, mientras que uno re-descrito falla y re-embebe. Los vectores de dos modelos nunca se encuentran: sus espacios no son comparables y una mezcla silenciosa corrompería el coseno en lugar de fallar. |
| [`tesserae/retrieval/views.py`](../../tesserae/retrieval/views.py) | El registro de vistas: `semantic` / `temporal` / `causal` / `entity`, cada una un subconjunto nombrado de `ALLOWED_EDGE_TYPES`, resuelto por `weights_for()` en pesos explícitamente cero para cada tipo fuera de la vista. Dos decisiones de partición son críticas: `summarizes` + `evidenced_by` (~50% de todas las aristas — abstracción y procedencia) no pertenecen a **ninguna** vista, o la vista semántica se convierte en el grafo completo de nuevo; y la vista causal es más amplia que `CAUSAL_EDGE_TYPES`, ya que `{recovers}` solo sería una vista sin aristas vivas. |
| [`tesserae/blocking.py`](../../tesserae/blocking.py) | La única capa de bloqueo para ambos pases pareados (constructor de revisión de canonicalización y `memory.supersede`). El límite trunca por **id ordenada**, de modo que una ejecución limitada no depende del orden en que llegaron los nodos y una compilación estrechada permanece reproducible; quien llama suministra su propio tokenizador, porque un bloqueador más grueso que su puntuador elimina silenciosamente coincidencias verdaderas. Cada pase reporta un límite que alcanzó en lugar de devolver una cola silenciosamente más corta. |

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
| [`tesserae/kuzu_adapter.py`](../../tesserae/kuzu_adapter.py) | Exportación unidireccional a una base de datos Kuzu (`tesserae export kuzu`). No es un almacén — véase [Exportación Kuzu](#exportación-kuzu). |
| [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | Servidor MCP stdio. Recuperación/grafo: `schema`, `graph_summary`, `search_nodes`, `node_context` (con `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`. Motor de contexto (v0.5.0): `compile_context` (el compilador de contexto bajo demanda), `embedding_status`, `fresh_insights` (hallazgos de sesión rankeados por decay), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`. Más `ask`, las herramientas del registro multi-proyecto (`list_projects`, `register_project`, `unregister_project`, `list_sessions`), y `tesserae_setup_plan` / `tesserae_setup_apply`. |

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

## La carta fundacional

La detección de comunidades **propone** un vocabulario de dominios; la carta
([`tesserae/charter.py`](../../tesserae/charter.py)) lo **posee** entre
reorganizaciones explícitas. Esa separación existe porque la detección es
determinista pero no estable: una entrada idéntica reproduce exactamente las
1.649 comunidades, y sin embargo un solo documento de 15 nodos mueve cerca del
29 % de los miembros entre comunidades y hunde las comunidades grandes a un
Jaccard de 0,39–0,60. Por tanto, todo lo que se indexe por pertenencia a una
comunidad sufre un fallo de caché casi total en cada ingesta, y este corpus
ingiere a diario.

Así que la carta fija la institución: se detectan secciones, se colapsan en un
grafo cociente (un nodo por sección, una arista `part_of` por cada arista L0
entre secciones) y se dividen en divisiones → departamentos → equipos **por
subcomunidad, nunca por tamaño**. El ancla de cada dominio es su miembro de mayor
grado entre los tipos que pueden nombrar un tema — `SourceDocument`,
`TechnicalTerm`, `EvidenceSpan`, `Session`, `Event` y `Agent` quedan relegados,
porque un encabezado de sección, una cita, la primera línea de una transcripción
o el identificador de una cuenta no son un nombre al que nadie pueda fijar un
agente —, elegido con avidez de modo que dos dominios nunca compartan una; el slug
de cara al humano se acuña una sola vez a partir de esa ancla y queda fijado. A
través de una reorganización, `succeed` arrastra los slugs emparejando por ancla,
de modo que un nombre estable sobrevive a la barajada de los miembros que hay
debajo. Cada nodo cae en exactamente un dominio: `intake_members` recoge los
singletons descartados y las secciones aisladas por aristas que, de otro modo, la
detección perdería en silencio.

`tesserae domains status [--json]` imprime el árbol. **Estado:** cada
`compile` deriva la carta a `.tesserae/charter/charter.json`, desde el mismo
grafo canonizado con el que se construye el sidecar de jerarquía. Una
recompilación que no reorganiza nada se niega a escribir, así que el archivo
queda idéntico byte a byte: `reorg_seq` cuenta reorganizaciones, no
compilaciones. Un proyecto cuya capa de investigación cabe en una sola lectura
queda por debajo del umbral y no tiene carta alguna; ahí el comando sigue
informando "no charter yet" y sale con 0, que es la respuesta honesta.

## Qué queda deliberadamente excluido

El rediseño trazó una línea explícita: los nodos code-class y code-function se quedan en `graph.json` (para que los consumidores MCP y Graphiti sigan viéndolos) pero nunca reciben páginas HTML, nunca aparecen en `search-index.json` y nunca aparecen en la navegación. Ese es el contrato de cara al usuario — la wiki es una base de conocimiento document-first, no un navegador de funciones.

Concretamente, `StaticSiteBuilder` se salta cualquier nodo cuyo tipo no esté en el mapa de tipos wiki L2 (`tesserae/wiki_projector.py::_KIND_FOR_TYPE`):

- Excluidos de L2 + L3: `CodeClass`, `CodeFunction`, `CodeModule`, `Dependency`, `EvidenceSpan`, `SourceFile`, todas las variantes de `Claim` (`Claim`, `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim`).
- Superficie donde sí siguen apareciendo: como bullets, badges, recuentos de vecinos o extractos de evidencia inline en las páginas wiki relacionadas, y en `graph.json` para tooling aguas abajo.

Si necesitas navegación a nivel de código, apunta una herramienta LSP / call-graph al árbol de fuentes directamente — es un problema distinto de "la wiki de lo que este proyecto sabe".

## Exportación/importación OKF v0.2

[`tesserae/okf.py`](../../tesserae/okf.py) proyecta el grafo a un paquete [Google **OKF v0.2**](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md): un árbol de directorios de archivos Markdown con frontmatter YAML cuya única clave obligatoria es un `type` no vacío. `tesserae export okf` **escribe v0.2**; `tesserae export okf --import DIR` **lee v0.1 y v0.2**. El paquete es una proyección pura de `graph.json`: sin reloj de pared, sin `os.stat()`, sin entorno, de modo que dos exportaciones de un mismo grafo son idénticas byte a byte.

Qué emite Tesserae, y de dónde procede honestamente cada valor:

| Frontmatter | §  | Derivado de |
|---|---|---|
| `type` | §4.1 | El tipo de nodo, o un tipo ajeno preservado en `metadata.okf_type` |
| `title` | §4.1 | `node.name` — v0.1 escribía un `name` fuera de especificación; véanse los cambios rompientes más abajo |
| `description` | §4.1 | Primera oración de la descripción del nodo, con tope |
| `resource` | §4.1 | `arxiv_id` → `https://arxiv.org/abs/<id>`, si no `repo_url` / `github_repo` |
| `generated: {by, at}` | §5.2 | `by` desde `agent_key` → `<key>/tesserae-agent-write`, si no `extractor` → `process:tesserae-<extractor>`, si no `process:tesserae-compile`; `at` desde la escalera compartida de marcas de tiempo de origen en [`temporal.py`](../../tesserae/temporal.py) |
| `sources[]` | §5.1 | `source_path` convertido a relativo a la raíz del proyecto, más `author` (un `authored_by` solitario), `last_modified` (`frontmatter_date` / `analysis_date`), `usage_count` (sesiones `discussed_in` distintas) |
| `usage_window` | §5.1 | Mínimo/máximo de `started_at` / `ended_at` de las sesiones contadas arriba |
| `status: deprecated`, `stale_after` | §5.4, §5.5 | Nodos apuntados por una arista `supersedes`; `stale_after` es la fecha del nodo que sustituye, omitida cuando precedería a la del propio nodo sustituido |
| `x_tesserae` | extensión | Id real del nodo, alias, `source_path`, metadatos, aristas tipadas — el canal de ida y vuelta sin pérdidas |

`index.md` sigue §8 (el frontmatter es exactamente `okf_version: '0.2'`, el único sitio donde §12 lo permite) y `log.md` sigue §9 (sin frontmatter, grupos `## YYYY-MM-DD`, del más reciente al más antiguo). Sobre el grafo del proyecto Tesserae (5197 nodos / 15284 aristas) eso son 5195 archivos que llevan `generated` en los 5193 conceptos, `sources` en 3934, `usage_window` en 1264, `description` en 1749, `resource` en 822 y `status`/`stale_after` en 25.

**Deliberadamente no emitido.** Sin clave `verified` (§5.2) y, por tanto, sin nivel de confianza por encima de `unverified` (§5.3): nada en el grafo compilado es un *evento* de verificación registrado con un actor y una marca de tiempo. `verify_claim` y el re-anclaje son funciones en tiempo de consulta sobre el grafo, y `lint --verify-claims` es un juez LLM, del que el propio [`verify.py`](../../tesserae/verify.py) dice que no es evidencia. Las clases de procedencia de aristas describen con cuánta fuerza el grafo autoriza un *triple*; la familia de confianza de OKF es una confirmación por *concepto*. Mapear una sobre la otra pondría un nivel confirmado-por-máquina sobre contenido que nadie confirmó, así que `generated.by` nunca puede empezar por `human:` — hay un test que lo fija. Igualmente, ninguna familia de Attested Computation (§10): Tesserae no tiene cómputos sancionados, ni ejecutor, ni recibo, ni ABI de atestador, y §10.5 le dice al consumidor que *condicione* a la atestación, de modo que un andamiaje vacío anunciaría un contrato imposible de honrar. También ausentes por falta de una fuente honesta: `tags` (no hay campo de etiquetas por nodo — los `aliases` son nombres alternativos, no categorías), notas al pie `[^id]` por afirmación, `status: draft` (`metadata.confidence` es confianza de extracción, no estado de revisión) y cualquier puntuación de credibilidad almacenada (§5.1 registra señales, no veredictos). `last_modified` procede de fechas de documento dentro del grafo, **nunca** del mtime del archivo: el atajo aparentemente obvio de `os.stat()` es exactamente la fuga de entorno que ya ha roto aquí la idempotencia a nivel de bytes.

**Lectura.** Según §11, el importador no rechaza nada: valores `type` desconocidos, claves de frontmatter desconocidas, familias opcionales ausentes, enlaces cruzados rotos y un `index.md` inexistente se toleran todos; solo se omite un archivo sin `type` no vacío. Los propios paquetes de Tesserae hacen ida y vuelta sin pérdidas mediante `x_tesserae`. Los paquetes ajenos mapean `type` → el tipo de nodo coincidente o `Concept`, los enlaces del cuerpo → aristas `references`, y cada clave de frontmatter no reconocida a `metadata.okf` (el SHOULD de ida y vuelta de §4.1), con un `verified` escueto normalizado a una lista de un elemento (MUST de §11). Alternativas de v0.1 (§13.1): un `timestamp` heredado aterriza en `metadata["updated_at"]` (un peldaño que la escalera de marcas de tiempo ya lee) y una lista `# Citations` heredada en el cuerpo se convierte en `metadata["okf"]["sources"]`, recortada de la descripción en lugar de ser engullida como prosa. Al reexportar, el cubo preservado se fusiona *por encima* de todo lo que Tesserae derivó, de modo que reexportar el paquete de otra persona nunca sobrescribe su procedencia ni sus afirmaciones de confianza con las nuestras; `--import` imprime un histograma de niveles de confianza para que un paquete mixto sea visible en vez de silencioso. Los niveles se infieren en tiempo de lectura mediante `okf_trust_tier`, nunca se almacenan.

**Cambios rompientes respecto a la salida v0.1 de Tesserae.** `name:` pasa a ser `title:` (`name` nunca fue una clave OKF en ninguna versión; el lector aún lo acepta, detrás de `title`). `index.md` y `log.md` pierden su frontmatter `type:` / `name:` (§8, §9), así que un consumidor que los tratara como conceptos tipados pierde dos entradas fantasma — que es justamente el objetivo; en relación con esto, ahora quedan reservados en *cualquier* nivel de la jerarquía (§3.1), no solo en la raíz del paquete. Los bytes de cada archivo de concepto cambian, por lo que la primera exportación v0.2 reescribe el paquete entero.

**Límites conocidos.** `usage_count` cuenta sesiones distintas de agente/trabajo cuya transcripción tocó el documento, no lecturas humanas de la página — §5.1 ya avisa de que la señal es gruesa; léela como vitalidad, no como popularidad. La familia de ciclo de vida solo se dispara para nodos apuntados por una arista `supersedes` (25 de 5197 aquí); una cobertura real necesitaría los intervalos de validez temporal que `TemporalFactProjector` deriva en tiempo de consulta, y ejecutar eso dentro del exportador sobre 15k aristas se rechazó por alcance. `generated.by` usa `process:tesserae-<extractor>` en lugar del `<producer>/<version>` de §7 a propósito: un actor con versión reescribiría los ~5200 archivos de concepto en cada release sin ningún cambio semántico. Ningún campo OKF con valor de ruta (`resource`, `sources[].resource`) lleva jamás una ruta absoluta — una que no pueda hacerse relativa a la raíz del proyecto se omite en vez de emitirse en crudo, ya que §6.2 haría que un consumidor la leyera como relativa al paquete — aunque las rutas absolutas sí pueden seguir apareciendo dentro de `x_tesserae.source_path` (la identidad real del nodo, que un consumidor ajeno ignora) y dentro del contenido de un nodo que cite alguna.

## Exportación Kuzu

[`tesserae/kuzu_adapter.py`](../../tesserae/kuzu_adapter.py) proyecta `graph.json` en una base de datos [Kuzu](https://kuzudb.com) embebida para que otra herramienta pueda ejecutar Cypher sobre el grafo. `tesserae export kuzu` la escribe; `--graph PATH` exporta un grafo extraído desnudo en lugar del compilado del proyecto. Es una **exportación unidireccional**, la tercera de tres junto a [OKF](#exportaciónimportación-okf-v02) y [Graphiti](../../tesserae/graphiti_adapter.py), y `write_graph(replace=True)` borra y recrea la base de datos, de modo que la salida es una función pura del grafo entregado.

**Kuzu deliberadamente no es un almacén, y la distinción es estructural.** Un `KuzuResearchGraphStore` solía vivir en [`tesserae/persistence.py`](../../tesserae/persistence.py) junto al almacén SQLite real, alcanzable solo mediante una bandera `extract --kuzu-output` cuya dependencia estaba declarada como dev-only — un segundo backend a medio cablear, que es lo que hacía que «¿debería Tesserae adoptar una base de datos de grafos?» pareciera una pregunta abierta. No lo está, y la razón es arquitectónica antes que legal (Kuzu es MIT, embebida y no necesita servidor):

- **Un segundo almacén autoritativo puede contradecir a `graph.json` sobre el mismo hecho**, y no hay árbitro. `graph.json` es la fuente de verdad; cualquier cosa que pueda contradecirla es una superficie de bugs.
- **La idempotencia a nivel de bytes pasaría de ser una función pura al orden de escritura de una base de datos.** La propiedad que fija `tests/test_byte_idempotence_phase5.py` —dos compilaciones producen un `graph.json` idéntico byte a byte— se sostiene porque compilar es una función pura de claves ordenadas sobre sus entradas. Ningún sistema de memoria-grafo comparado lo intenta siquiera, y enrutar escrituras por un motor es exactamente cómo se perdería.

Ninguna objeción se aplica a una exportación: la base de datos es salida derivada, borrada y reescrita desde el grafo, y ninguna ruta de compilación o consulta la lee de vuelta. `read_graph` existe por la misma razón que `okf.read_okf_bundle` —una exportación que no puedes leer de vuelta es una exportación que no puedes verificar—, no porque algo en el motor cargue desde Kuzu. `tests/test_kuzu_adapter.py` afirma que `tesserae.persistence` no expone ningún símbolo de Kuzu, así que un almacén reinstaurado falla la suite en lugar de la revisión.

El mismo veredicto descarta Neo4j como sustrato: véase [`docs/superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md`](../superpowers/specs/2026-08-14-neo4j-agent-memory-roadmap.md), que adopta las capacidades (un índice vectorial persistido, una lápida de soft-merge, un reloj de tiempo de transacción) como sidecars de archivo y SQLite en vez de como motor.

## Historia de idempotencia

El rediseño apunta a **salida byte-idéntica entre dos ejecuciones consecutivas de `project compile` sobre inputs sin cambios**. Las piezas:

1. **La extracción de fuentes** usa los hashes de contenido de `manifest.json`; los archivos sin cambios se saltan, así que el grafo permanece estable.
2. **Las escrituras de la capa wiki** son idempotentes a nivel de cuerpo. `WikiPageStore.write_page` lee el archivo existente, quita el frontmatter, hace sha256 del cuerpo y cortocircuita si el nuevo cuerpo hashea igual — incluso si el nuevo frontmatter tiene un timestamp `generated_at` distinto. Ese es el truco clave que mantiene los diffs de git compactos al reconstruir.
3. **La salida de síntesis** lleva un `content_hash: sha256-…` en su frontmatter. El hash del cuerpo se computa sin `generated_at`, así que compilaciones repetidas sobre el mismo grafo producen el mismo hash, y los nodos `Synthesis` llevan el mismo `content_hash` en los metadatos del grafo.
4. **El renderizado del sitio** borra `site/` al inicio de `write_site`, y luego escribe de forma determinista: las rutas se ordenan, los diccionarios se vuelcan con `sort_keys=True`, `manifest.json` se recorre vía `sorted(rglob("*"))`. Dos ejecuciones producen archivos byte-idénticos incluido el manifest.
5. **Las fechas de los nodos derivan de la fuente.** El `first_seen_at` de un nodo procede de la ruta bajo la que se ingirió su fuente, no del reloj de pared en el momento de compilar. Leer el reloj convertiría cada reejecución en un diff, que es justo por lo que la versión ingenua de esto derrota al punto 1. La misma regla mantiene la pasada `Event` idempotente a nivel de bytes: cada id, cuerpo y fecha acuñados derivan del contenido, verificado sobre un corpus de 481 sesiones.

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

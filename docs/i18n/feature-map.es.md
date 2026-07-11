# Mapa de funciones

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
Este documento resume las funciones actualmente implementadas en Tesserae, con estado, archivos fuente y dónde están documentadas.

Tesserae es un **motor de contexto** que corre sobre tres pilares: (1) monitorización de sesiones, (2) ingesta de conocimiento autónoma y proactiva, y (3) docs/contexto bajo demanda. El grafo tipado, el vault y el sitio estático son proyecciones de la base de conocimiento. Las funciones de abajo están agrupadas según el pilar al que sirven; el hito **v0.5.0** (junio 2026) entregó la espina dorsal del engine y la función estrella del Pilar 3, el compilador de contexto bajo demanda.

Leyenda de estado: ✅ entregado · ⚠ en progreso / parcial.

## Cross-project y UX — v0.11.0 (junio 2026)

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| Federación cross-project | ✅ | [`tesserae/federation.py`](../../tesserae/federation.py) | `ask --scope federated` ensambla UN grafo desde varios proyectos registrados — identity-merge (mismo arxiv/repo/hash/símbolo) + enlaces `shares_concept_with` respaldados por embeddings con opt-out — y devuelve una única respuesta cruzada y con citas sobre la unión (PPR + `compile_context`). El `graph.json` por proyecto es de solo lectura; determinista para identity-only. |
| Router inteligente de `ask` (sin proyecto activo) | ✅ | [`tesserae/ask_router.py`](../../tesserae/ask_router.py) | El concepto de "proyecto activo" se eliminó — todos los proyectos registrados son iguales. Un `ask` a secas se enruta solo (nombra un proyecto → ese; comparativo → federado; follow-up → mantiene la ruta; si no → fallback federado), con un desempate LLM opcional y continuidad por conversación. Las operaciones por proyecto resuelven el proyecto desde el cwd. |
| Inspección de federación | ✅ | `tesserae/federation.py`, `cli.py` | `tesserae federation status` (recuentos de nodos por proyecto, merges de identidad, enlaces semánticos) y `federation explain <node>` (por qué un nodo puentea proyectos). |
| Serve multi-proyecto | ✅ | [`tesserae/serve.py`](../../tesserae/serve.py), `cli.py` | Un `tesserae serve` a secas sirve CADA proyecto registrado bajo un servidor (landing en `/`, cada uno en `/<alias>/`, un selector de Projects en la cabecera, con rutas contenidas); `--project X` sirve uno con el widget de ask en vivo. |
| Capa de conceptos LLM en `compile` | ✅ | `cli.py`, [`tesserae/llm_extractor.py`](../../tesserae/llm_extractor.py), [`tesserae/selective_extractor.py`](../../tesserae/selective_extractor.py) | `tesserae compile` construye la capa de conceptos/afirmaciones **por defecto** (`--extractor llm`) vía el proveedor configurado (codex/claude/api según `llm_provider`); `--extractor deterministic` es el opt-out estructural y byte-estable; `selective-llm --llm-include … --llm-limit N` es la variante consciente del coste. |
| `tesserae setup` (interactivo) | ✅ | `cli.py`, [`tesserae/deps.py`](../../tesserae/deps.py) | `tesserae setup` de nivel superior — interactivo por defecto (proveedor/esfuerzo LLM + qué deps opcionales); los flags saltan los prompts. Las instalaciones funcionan en entornos uv-tool sin pip (fallback uv-pip). |

## Interop, búsqueda y setup — v0.10.0 (junio 2026)

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| Import/export **OKF v0.1** de Google | ✅ | [`tesserae/okf.py`](../../tesserae/okf.py) | `tesserae export okf [--import DIR]`. Bundle de markdown + frontmatter YAML; hace round-trip sin pérdidas de los bundles propios de Tesserae vía un namespace `x_tesserae`, los bundles ajenos best-effort. |
| Búsqueda rápida de transcripts (memex) | ✅ | [`tesserae/memex_search.py`](../../tesserae/memex_search.py) | Índice BM25 de `nicosuave/memex` sobre transcripts de Claude/Codex, cableado al dashboard de sesiones de `tesserae serve` vía `GET /api/transcript-search`. Opcional + degrada con elegancia si está ausente. |
| Handles de disciplina de lectura | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | `compile_context` `preview=N` devuelve una vista previa acotada + un handle indexado por contenido; `get_handle` pagina el resto. Mantiene los payloads enormes fuera del contexto del agente. |
| Señales de calidad de extracción | ✅ | [`tesserae/session_graph_llm.py`](../../tesserae/session_graph_llm.py) | Por hallazgo: `confidence` + `confidence_rationale` + `revisit_signals` (byte-estable; expuesto en `fresh_insights`). |
| Setup a nivel de máquina + deps | ✅ | [`tesserae/deps.py`](../../tesserae/deps.py), `cli.py` | `tesserae setup` escribe los defaults globales de LLM + instala deps opcionales (memex, cognee, raganything); `tesserae config deps` lista/instala; `tesserae init` ofrece memex. La config por proyecto sigue teniendo prioridad. |

## Motor de contexto — v0.5.0 (junio 2026)

La espina dorsal del engine que impulsa los tres pilares. Ver [`docs/architecture.md`](architecture.es.md) para el mapa de módulos de la espina del engine, el sidecar de memoria de auto-mejora y el dataflow del compilador de contexto.

### Espina del engine (pilares 1 y 2)

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| `Pipeline` — cadena de refresco reutilizable que devuelve `List[StepResult]` | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | Un único ejecutor de pasos al que llaman la CLI, el daemon y MCP. Captura `Exception` por paso; se detiene en el primer fallo. |
| `Daemon` — supervisor asyncio de propietario único | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | Vigila fuentes + vault + directorio de sesiones de harness; el debounce cancel-and-reschedule coalesce una ráfaga en un solo `Pipeline.run()`. Pidfile; sobrevive a excepciones en vuelo. |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`, `--debounce`, `--once`. `daemon` es un alias de `engine`. |
| `project refresh` — cadena en prosa (ingest → compile → project) | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only` (incremental opt-in), `--no-sessions`. |
| Monitor de sesiones en vivo → hallazgos | ✅ | `harness_sessions.py` + módulos de session-graph | Las sesiones importadas alimentan el grafo; `fresh_insights` / `find_session_findings` los exponen. |

### Memoria de auto-mejora (pilar 2)

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| Sidecar SQLite `node_memory` (decay / confidence / superseded) | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + accesores agnósticos del store; solo estado mutable. El first-seen vive en el sidecar separado `node_provenance`. |
| Score de decay de Ebbinghaus | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | Rankea los hallazgos de sesión más nuevos + más accedidos primero (impulsa `fresh_insights`). |
| Pase de supersede (**activado por defecto**) | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | Un veredicto determinista marca un insight casi-duplicado más antiguo como reemplazado por uno más nuevo; añade una arista `supersedes`. |
| Enlace insight → símbolo de código | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | Aristas `discusses` desde los insights de sesión hacia los símbolos que referencian. |
| Pases de reinforce + contradicción | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Refuerzo por acceso + detección de contradicciones sobre el mismo sidecar. |
| Confianza numérica por recurrencia en la salida | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | Los hechos temporales estampan `confidence` desde `NodeMemoryRow.confidence`, con fallback a `infer_confidence`. |

### Recuperación + embeddings (pilares 2 y 3)

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| Recuperador híbrido (BM25 + léxico + embedding, RRF k=60) | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | Local-first, totalmente determinista. |
| Personalized PageRank (HippoRAG-2) | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | Expansión de semillas multi-hop; subgrafo acotado por profundidad. |
| Embeddings reales por defecto (Track B, Fase 6) | ✅ | `retrieval/hybrid.py` | Por defecto = pseudo-embedding determinista de hash-bucket (sin deps); `sentence-transformers` (`all-MiniLM-L6-v2`) preferido, cargado perezosamente cuando está instalado. La herramienta MCP `embedding_status` reporta el backend activo. |

### Compilador de contexto bajo demanda (pilar 3 — titular)

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| `compile_context` — `ContextBundle` en memoria con citas | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Resolución de semillas → expansión PPR → selección acotada por presupuesto → markdown con citas → síntesis LLM opcional. Determinista salvo con `synthesize=true`. No escribe nada a disco. |
| CLI `project context` | ✅ | `cli.py` | `[query]`, `--seeds`, `--depth` (2), `--budget` (32000; ≤0 = sin tope), `--llm`, `--output`. |
| Herramienta MCP `compile_context` | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | El mismo pipeline sobre MCP; `budget=0` es sin tope. |
| Slices de export acotados por topic | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | `llms.txt` acotado por topic + `render_harness_context` vía `compile_context`. |

### Compilación incremental (Fase 4 — experimental)

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| Sidecar de procedencia (`node_provenance`, first-seen) | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | Cimiento de los deletes changed-only; siempre registrado. |
| Superficie de delete de `GraphStore` | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`, `delete_nodes_by_source` (elimina nodos cuyo conjunto de procedencia queda vacío; los conceptos multi-archivo sobreviven). |
| Dispatch de store en runtime por `url_resolver` | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`. |
| Flag `incremental_compile` | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **OFF por defecto / experimental.** Paridad de bytes demostrada para varias formas de edición pero quedan huecos multi-owner/ciclo de vida de productores; la compilación completa sigue siendo el default. |

## Rediseño del frontend — abril 2026

Una wiki document-first y jerárquica reemplaza al viejo volcado del grafo. Ver [`docs/frontend-redesign.md`](frontend-redesign.es.md) para el recorrido ruta a ruta y [`docs/architecture.md`](architecture.es.md) para el modelo de tres capas.

### Capa wiki (markdown L2)

| Función | Estado | Fuente | Ancla de doc |
|---|---|---|---|
| `WikiPageStore` (escrituras idempotentes por hash del cuerpo, parser de frontmatter) | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § Module map](architecture.es.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — una página md por nodo de la capa wiki | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § Pipeline](architecture.es.md#pipeline) |
| Páginas `sources/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.es.md#sources) |
| Páginas `concepts/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.es.md#concepts) |
| Páginas `entities/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.es.md#entities) |
| Páginas `papers/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.es.md#papers) |
| Páginas `repos/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.es.md#repos) |
| Páginas `topics/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.es.md#topics) |
| Páginas `questions/` (Open questions) | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.es.md#questions) |
| Páginas `syntheses/` | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.es.md#syntheses) |

### Tipos de síntesis (L2 → derivado)

`SynthesisProjector` produce siete plantillas deterministas y añade nodos `Synthesis` + aristas `synthesizes` / `summarizes` de vuelta al grafo.

| Tipo | Estado | Fuente | Notas |
|---|---|---|---|
| `pulse` (uno global, impulsa `/`) | ✅ | `synthesis.py` | Reconstruido en cada compilación. |
| `daily_digest` | ✅ | `synthesis.py` | Uno por `data/research/daily/<date>/`. |
| `weekly` | ✅ | `synthesis.py` | Uno por `data/research/weekly/<iso-week>/`. |
| `topic` | ✅ | `synthesis.py` | Uno por clúster `ResearchTopic` / `ApproachFamily` ≥ 3 papers. |
| `comparison` | ✅ | `synthesis.py` | Uno por par de `ApproachFamily` compitiendo en la misma tarea. |
| `field_overview` | ✅ | `synthesis.py` | Uno por `ResearchField`. |
| Resúmenes mejorados por LLM (tras flag de entorno) | ⚠ | solo hook | La línea base heurística se entrega; el hook `TESSERAE_SYNTHESIS_LLM=1` queda como stub. |

### Rutas del sitio estático

| Ruta | Estado | Fuente | Notas |
|---|---|---|---|
| `/` (home, hero pulse) | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | Fila de stats + puntos de entrada curados + actividad reciente. |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | Heatmap + lista de días + rail de síntesis. |
| `/timeline/<YYYY-MM-DD>.html` (detalle por día) | ⚠ | n/a todavía | Las celdas del heatmap enlazan a la página fuente `digest.md` del día como interinidad. El subagente P está cableando las páginas de detalle por día a través de `StaticSiteBuilder`. |
| `/graph/` (2D + 3D interactivo) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, tooltips al pasar el cursor, etiquetas de aristas, zoom anclado al cursor. |
| `/about.html` | ✅ | `pages.py::render_about` | Esquema, info de build. |

### Exports amigables para IA

| Artefacto | Estado | Fuente | Propósito |
|---|---|---|---|
| Sibling `<page>.txt` por página | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | Vista en texto plano de una página (sin nav, sin estilos). |
| Sibling `<page>.json` por página | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`. |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | Índice corto de llmstxt.org. |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | El cuerpo de cada página, con tope de 5 MB. |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | `Dataset` de schema.org, solo nodos de la capa wiki. |
| `graph.json` | ✅ | `__init__.py::write_site` | Payload completo del grafo (incl. nodos de código para tooling). |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | Búsqueda de paleta + páginas; solo tipos de la capa wiki. |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | Cada ruta emitida, `lastmod` desde el frontmatter. |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | Las últimas 30 síntesis. |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | Permisivo — crawl + index. |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | Mapa del sitio legible por máquinas. |
| `manifest.json` | ✅ | `__init__.py::_manifest` | sha256 + tamaño de cada archivo emitido (harness de idempotencia). |

### Diseño visual + UX

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| Tokens de diseño (temas claro + oscuro, acento terracota) | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Un bundle CSS en `assets/style.css`. |
| Toggle de tema (persistido, sin flash) | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `data-theme="dark"` en `localStorage`, aplicado antes del paint. |
| Paleta de búsqueda (`cmd+k` / `ctrl+k` / `/`) | ✅ | `js.py` | Coincidencia difusa sobre `search-index.json`; lista de páginas recientes. |
| TOC derecho pegajoso | ✅ | `pages.py` + `tokens.py` | Solo escritorio; drawer móvil vía `<details>`. |
| Heatmap de actividad con etiquetas de mes + día de la semana | ✅ | `components.py::heatmap_svg` | SVG de 26 semanas, las celdas enlazan al `digest.md` del día. |
| Sparkline (por concepto/entidad) | ✅ | `components.py::sparkline_svg` | Recuentos semanales de menciones, últimas 12 semanas. |
| Shell móvil (rail drawer, nav inferior, tipografía fluida) | ✅ | `tokens.py` + `pages.py` | Objetivos táctiles ≥ 44 px. |
| Transiciones de página (opacidad 120 ms, prefers-reduced-motion) | ✅ | `tokens.py` | |
| Vista de grafo 3D + 2D (hover, etiquetas de aristas, zoom anclado al cursor) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, vendorizado como snapshot de CDN. |
| Footer de AI siblings por página | ✅ | `components.py::ai_siblings_footer` | Enlaces inline al `.txt` y al `.json` de la página actual. |
| Páginas de historial de sesiones de harness | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Importación explícita de Claude Code/Codex; índice `/sessions/` y páginas de detalle con turnos en markdown, rail de turnos a la izquierda, tool-use colapsado y entradas de búsqueda. |

### Pipeline + CLI

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| `project compile` llama a síntesis + wiki + sitio en orden | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | Fase 3 del plan de rediseño. |
| `project build-site` standalone | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | Lee `wiki/` + `graph.json`, escribe `site/`. |
| `project serve` HTTP local | ✅ | `cli.py` | Servidor de stdlib puro. |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | Push por worktree a `gh-pages`; `--enable-pages` opcional vía CLI `gh`. `--build`, `--dry-run`, `--branch`, `--remote`, `--force`. |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Historial de sesiones entrante para Claude Code/Codex; el descubrimiento es explícito y acotado al directorio de trabajo del proyecto. |
| `project watch` rebuild-on-change | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | Watcher de sondeo standalone: `--interval`, `--debounce`, `--once`, `--paths`, `--quiet`. El supervisor multi-fuente vive bajo `project engine`/`daemon` (ver Motor de contexto). |
| `project context` — compila un doc de contexto con citas | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Titular del Pilar 3; ver la sección Motor de contexto. |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | Cadena de refresco en prosa + bucle supervisor; ver la sección Motor de contexto. |

## Funciones preexistentes (mantenidas sin cambios)

### CLI e instalación

- ✅ Paquete Python instalable vía `pyproject.toml`.
- ✅ Comandos de consola: `tesserae`, `tesserae`, `tesserae_mcp`.
- ✅ `scripts/install.sh` para instalación `curl | bash`.
- ✅ Instalaciones editables por defecto para desarrollo local rápido.

### Extracción

- ✅ Extractor determinista de notas de investigación con vocabularios controlados de nodos/aristas.
- ✅ Extractor Claude CLI/OAuth para extracción estructurada de mayor calidad sin claves de API.
- ✅ Enrutamiento selectivo de Claude por glob y límite de presupuesto.
- ✅ Extractor determinista de código de desarrollo para proyectos Python.
- ✅ Ingesta por lotes con hashing de contenido y soporte de `--changed-only`.
- ✅ Lectura de fuentes tolerante a UTF-8 malformado.

### Gobernanza del grafo

- ✅ Lista controlada `ResearchNodeType` — ahora incluye `SYNTHESIS`.
- ✅ Whitelist controlada de tipos de arista — ahora incluye `synthesizes`, `summarizes`.
- ✅ Validación para rechazar la deriva de esquema.
- ✅ Canonicalización de alias.
- ✅ Cola de revisión para nodos casi-duplicados ambiguos.
- ✅ Plantilla de decisiones de revisión y flujo merge/keep-separate.
- ✅ Resumen de tendencias del corpus desde grafos por archivo.

### Persistencia e informes

- ✅ Export JSON del grafo.
- ✅ Store de grafo SQLite.
- ✅ Store de grafo Kuzu opcional.
- ✅ Informe del grafo con recuentos, cobertura de evidencia, nodos huérfanos, buckets por fecha, nodos con muchos alias.
- ✅ Informe competitivo describiendo ideas absorbidas de MegaMem, Graphiti/Zep, servidores de grafo MCP, RAG agéntico.

### Flujo local al proyecto

- ✅ `tesserae init --bare`
- ✅ `tesserae compile <paths>`
- ✅ `tesserae compile`
- ✅ `tesserae projects mcp-config`
- ✅ `tesserae export site`
- ✅ `tesserae serve`
- ✅ `tesserae export site --deploy` (GitHub Pages)
- ✅ `tesserae sessions discover/import/list` (importación explícita de historial local de agentes)
- ✅ `tesserae export site --watch` (watcher de sondeo standalone)
- ✅ `tesserae engine` (bucle supervisor — v0.5.0)
- ✅ `tesserae refresh` (cadena en prosa ingest → compile → project — v0.5.0)
- ✅ `tesserae context` (compilador de contexto bajo demanda — v0.5.0)
- ✅ `tesserae export harness`
- ✅ `tesserae vault export`
- ✅ `tesserae export graphiti`
- ✅ `tesserae export graphiti --sync`

### Obsidian

- ✅ Export de vault listo para abrir.
- ✅ `.obsidian/app.json` y ajustes del grafo.
- ✅ Proyección markdown.
- ✅ Estructura `raw/assets/`.
- ✅ `_meta/dashboard.md` con consulta de Dataview.

### Agent harnesses

Archivos target generados para:

- ✅ Claude Code: `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex: `AGENTS.md`, `mcp.toml`
- ✅ Gemini: `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro: steering y ajustes MCP
- ✅ Cursor: reglas de proyecto y config MCP
- ✅ OpenCode: `AGENTS.md`, `opencode.json`

### Graphiti / hechos temporales

- ✅ Proyección de hechos temporales con campos de procedencia, vigencia, confianza e invalidación.
- ✅ Export JSONL de episodios Graphiti sin dependencias.
- ✅ Prueba de humo `sync-graphiti --dry-run` sin Graphiti instalado.
- ✅ Sincronización en vivo opcional con `graphiti_core` y Neo4j.

### Cognee

- ✅ Bundle JSONL de Cognee (`nodes.jsonl`, `edges.jsonl`, `manifest.json`).
- ✅ Importación directa add-only opcional.
- ✅ Adaptador opcional de cognify de Cognee respaldado por Codex CLI/OAuth.
- ✅ Rutas de adaptador de embeddings determinista y Ollama para flujos de humo/calidad sin claves de API.

### Servidor MCP

- ✅ `tesserae_mcp` / `python3 -m tesserae.mcp_server` sobre stdio JSON-RPC.
- ✅ Herramientas de recuperación/grafo: `schema`, `graph_summary`, `search_nodes`, `node_context` (con `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`, `doctor_report`.
- ✅ Herramientas del motor de contexto (v0.5.0): `compile_context`, `embedding_status`, `fresh_insights` (rankeado por decay), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`, `ask`.
- ✅ Herramientas de setup: `tesserae_setup_plan`, `tesserae_setup_apply`.
- ✅ Registro multi-proyecto: `list_projects`, `register_project`, `unregister_project`, `list_sessions`. Dispatch de URL de store vía `url_resolver`.

## Tests

La suite actual cubre:

- ✅ guardarraíles de ontología (incl. nuevo nodo `Synthesis` + aristas `synthesizes` / `summarizes`);
- ✅ extracción determinista;
- ✅ parsing/validación del wrapper de Claude CLI;
- ✅ enrutamiento selectivo de Claude;
- ✅ flujo de canonicalización/revisión;
- ✅ ingesta por lotes;
- ✅ informes;
- ✅ persistencia SQLite/Kuzu;
- ✅ bundles/parches de importación de Cognee;
- ✅ export/sync dry-run de Graphiti;
- ✅ flujo CLI de proyecto;
- ✅ export de agent harness;
- ✅ export de Obsidian;
- ✅ generación de frontend + integridad de enlaces (sin `nodes/codeclass-*.html`);
- ✅ idempotencia del wiki store;
- ✅ golden + idempotencia del proyector de síntesis;
- ✅ componentes, páginas, exports y relevancia del sitio;
- ✅ forma de los AI siblings (`.txt` + `.json` por página);
- ✅ idempotencia end-to-end de compilar dos veces;
- ✅ espina del engine: pipeline, cadena de refresco, núcleo del daemon + fuentes, CLI de `project engine`;
- ✅ memoria de auto-mejora: sidecar, decay/supersede, supresión de supersede (incl. MCP), reinforce/contradicción;
- ✅ recuperación + embeddings: búsqueda híbrida, PPR, embeddings reales por defecto (Fase 6);
- ✅ compilador de contexto: forma/integridad de citas/determinismo/presupuesto/fallback de PPR, CLI de `project context`, `compile_context` de MCP;
- ✅ compilación incremental (experimental): differ, puertas de paridad, preparación de procedencia, procedencia SQLite;
- ✅ instalación del paquete y contrato del instalador.

# Mapa de funciones

<!-- translations:start -->
<p align="center"><a href="../feature-map.md">English</a> · <a href="feature-map.ko.md">한국어</a> · <a href="feature-map.zh.md">中文</a> · <a href="feature-map.ja.md">日本語</a> · <a href="feature-map.ru.md">Русский</a> · <a href="feature-map.es.md">Español</a> · <a href="feature-map.fr.md">Français</a> · <a href="feature-map.de.md">Deutsch</a></p>
<!-- translations:end -->
Este documento resume las funciones implementadas actualmente en Tesserae, con su estado, archivos fuente y dónde están documentadas.

Tesserae es un **motor de contexto** que funciona sobre tres pilares: (1) monitoreo de sesiones, (2) ingesta de conocimiento autónoma y proactiva, y (3) documentos/contexto bajo demanda. El grafo tipado, el almacén (vault) y el sitio estático son proyecciones de la base de conocimiento. Las funciones de abajo se agrupan por el pilar al que sirven; el hito **v0.5.0** (junio de 2026) publicó la columna del motor y la función estrella del Pilar 3, el compilador de contexto bajo demanda.

Leyenda de estado: ✅ publicado · ⚠ en progreso / parcial.

## Motor de contexto — v0.5.0 (junio de 2026)

La columna del motor que impulsa los tres pilares. Véase [`docs/architecture.md`](architecture.es.md) para el mapa de módulos de la columna del motor, el sidecar de memoria de automejora y el flujo de datos del compilador de contexto.

### Columna del motor (pilares 1 & 2)

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| `Pipeline` — cadena de actualización reutilizable que devuelve `List[StepResult]` | ✅ | [`tesserae/engine/pipeline.py`](../../tesserae/engine/pipeline.py) | Un único ejecutor de pasos que llaman la CLI, el demonio y MCP. Captura `Exception` por paso; se detiene en el primer fallo. |
| `Daemon` — supervisor asyncio de único propietario | ✅ | [`tesserae/engine/daemon.py`](../../tesserae/engine/daemon.py) | Vigila fuentes + almacén + directorio de sesiones de harness; un debounce de cancelar-y-reprogramar fusiona una ráfaga en un `Pipeline.run()`. pidfile; sobrevive a excepciones en vuelo. |
| `project engine` / `project daemon` | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) | `--interval`, `--debounce`, `--once`. `daemon` es un alias de `engine`. |
| `project refresh` — cadena en prosa (ingesta → compilación → proyección) | ✅ | `cli.py` + [`tesserae/project.py`](../../tesserae/project.py) | `--changed-only` (incremental opcional), `--skip-sessions`. |
| Monitoreo de sesiones en vivo → hallazgos | ✅ | `harness_sessions.py` + módulos de grafo de sesión | Las sesiones importadas alimentan el grafo; `fresh_insights` / `find_session_findings` los sacan a la superficie. |

### Memoria de automejora (pilar 2)

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| sidecar `node_memory` SQLite (decaimiento / confianza / reemplazado) | ✅ | [`tesserae/memory/store.py`](../../tesserae/memory/store.py) | `NodeMemoryRow` + accesores independientes del almacén; solo estado mutable. La primera aparición vive en el sidecar `node_provenance` aparte. |
| Puntuación de decaimiento Ebbinghaus | ✅ | [`tesserae/memory/decay.py`](../../tesserae/memory/decay.py) | Ordena hallazgos de sesión por más nuevo + más accedido (impulsa `fresh_insights`). |
| Pasada de reemplazo (**activada por defecto**) | ✅ | [`tesserae/memory/supersede.py`](../../tesserae/memory/supersede.py) | Veredicto determinista marca un insight casi-duplicado más antiguo como reemplazado por uno más nuevo; añade arista `supersedes`. |
| Enlace insight → símbolo de código | ✅ | [`tesserae/memory/insight_symbol_link.py`](../../tesserae/memory/insight_symbol_link.py) | Aristas `discusses` desde insights de sesión a los símbolos referenciados. |
| Pasadas de refuerzo + contradicción | ✅ | [`tesserae/memory/reinforce.py`](../../tesserae/memory/reinforce.py), [`tesserae/memory/contradiction.py`](../../tesserae/memory/contradiction.py) | Refuerzo de acceso + detección de contradicciones sobre el mismo sidecar. |
| Confianza de recurrencia numérica en la salida | ✅ | [`tesserae/temporal.py`](../../tesserae/temporal.py) | Los hechos temporales sellan `confidence` desde `NodeMemoryRow.confidence`, si no recurren a `infer_confidence`. |

### Recuperación + embeddings (pilares 2 & 3)

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| Recuperador híbrido (BM25 + léxico + embeddings, RRF k=60) | ✅ | [`tesserae/retrieval/hybrid.py`](../../tesserae/retrieval/hybrid.py) | Local-first, totalmente determinista. |
| PageRank personalizado (HippoRAG-2) | ✅ | [`tesserae/retrieval/ppr.py`](../../tesserae/retrieval/ppr.py) | Expansión multi-salto de semillas; subgrafo con profundidad limitada. |
| Embeddings reales por defecto (Track B, Fase 6) | ✅ | `retrieval/hybrid.py` | Por defecto = pseudo-embedding determinista por cubos de hash (sin dependencias); `sentence-transformers` (`all-MiniLM-L6-v2`) preferido al instalarse, carga diferida. La herramienta MCP `embedding_status` informa el backend activo. |

### Compilador de contexto bajo demanda (Pilar 3 — estrella)

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| `compile_context` — `ContextBundle` en memoria con citas | ✅ | [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Resolución de semillas → expansión PPR → selección por presupuesto → markdown con citas → síntesis LLM opcional. Determinista salvo `synthesize=true`. No escribe en disco. |
| CLI `project context` | ✅ | `cli.py` | `[query]`, `--seeds`, `--depth` (2), `--budget` (32000; ≤0 = sin límite), `--synthesize`, `--output`. |
| Herramienta MCP `compile_context` | ✅ | [`tesserae/mcp_server.py`](../../tesserae/mcp_server.py) | El mismo pipeline vía MCP; `budget=0` = sin límite. |
| Recortes de exportación por tema | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `slice_export_context_for_topic` | `llms.txt` por tema + `render_harness_context` vía `compile_context`. |

### Compilación incremental (Fase 4 — experimental)

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| sidecar de procedencia (`node_provenance`, primera aparición) | ✅ | [`tesserae/graph_stores/sqlite.py`](../../tesserae/graph_stores/sqlite.py) | Base para borrados solo-cambios; siempre se registra. |
| Superficie de borrado de `GraphStore` | ✅ | [`tesserae/ports/graph_store.py`](../../tesserae/ports/graph_store.py) | `delete_node`, `delete_nodes_by_source` (elimina nodos cuyo conjunto de procedencia queda vacío; los conceptos entre archivos sobreviven). |
| Despacho de almacén en runtime `url_resolver` | ✅ | [`tesserae/graph_stores/url_resolver.py`](../../tesserae/graph_stores/url_resolver.py) | `sqlite:///…` / `hypepaper-postgres://…` → `GraphStore`. |
| Flag `incremental_compile` | ⚠ | [`tesserae/project.py`](../../tesserae/project.py) | **Por defecto OFF / experimental.** Paridad de bytes probada para varias formas de edición, pero quedan brechas de múltiples propietarios/ciclo de vida de productores; la compilación completa sigue siendo la opción por defecto. |

## Rediseño del frontend — abril de 2026

Una wiki jerárquica centrada en documentos reemplaza el antiguo volcado de grafos. Consulta [`docs/frontend-redesign.md`](frontend-redesign.es.md) para el recorrido ruta por ruta y [`docs/architecture.md`](architecture.es.md) para el modelo de tres capas.

### Capa wiki (L2 markdown)

| Función | Estado | Fuente | Ancla de documentación |
|---|---|---|---|
| `WikiPageStore` (escrituras idempotentes con body-hash, parser de frontmatter) | ✅ | [`tesserae/wiki_store.py`](../../tesserae/wiki_store.py) | [architecture.md § Mapa de módulos](architecture.es.md#wiki--synthesis-l2) |
| `WikiLayerProjector` — una página md por nodo de la capa wiki | ✅ | [`tesserae/wiki_projector.py`](../../tesserae/wiki_projector.py) | [architecture.md § Canalización](architecture.es.md#pipeline) |
| Páginas `sources/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Sources](frontend-redesign.es.md#sources) |
| Páginas `concepts/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Concepts](frontend-redesign.es.md#concepts) |
| Páginas `entities/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Entities](frontend-redesign.es.md#entities) |
| Páginas `papers/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Papers](frontend-redesign.es.md#papers) |
| Páginas `repos/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Repos](frontend-redesign.es.md#repos) |
| Páginas `topics/` | ✅ | `wiki_projector.py` | [frontend-redesign.md § Topics](frontend-redesign.es.md#topics) |
| Páginas `questions/` (preguntas abiertas) | ✅ | `wiki_projector.py` | [frontend-redesign.md § Questions](frontend-redesign.es.md#questions) |
| Páginas `syntheses/` | ✅ | [`tesserae/synthesis.py`](../../tesserae/synthesis.py) | [frontend-redesign.md § Syntheses](frontend-redesign.es.md#syntheses) |

### Tipos de síntesis (L2 → derivado)

`SynthesisProjector` produce siete plantillas deterministas y añade nodos `Synthesis` y aristas `synthesizes` / `summarizes` de vuelta al grafo.

| Tipo | Estado | Fuente | Notas |
|---|---|---|---|
| `pulse` (uno global, alimenta `/`) | ✅ | `synthesis.py` | Se reconstruye en cada compilación. |
| `daily_digest` | ✅ | `synthesis.py` | Uno por `data/research/daily/<date>/`. |
| `weekly` | ✅ | `synthesis.py` | Uno por `data/research/weekly/<iso-week>/`. |
| `topic` | ✅ | `synthesis.py` | Uno por clúster `ResearchTopic` / `ApproachFamily` con ≥ 3 papers. |
| `comparison` | ✅ | `synthesis.py` | Uno por par de `ApproachFamily` que compiten en la misma tarea. |
| `field_overview` | ✅ | `synthesis.py` | Uno por `ResearchField`. |
| Resúmenes mejorados con LLM (activados por variable de entorno) | ⚠ | solo hook | La línea base heurística se entrega; el hook `TESSERAE_SYNTHESIS_LLM=1` queda como stub. |

### Rutas del sitio estático

| Ruta | Estado | Fuente | Notas |
|---|---|---|---|
| `/` (inicio, pulse principal) | ✅ | [`tesserae/site/pages.py`](../../tesserae/site/pages.py) `render_home` | Fila de estadísticas + puntos de entrada curados + actividad reciente. |
| `/sources/`, `/sources/<slug>.html` | ✅ | `pages.py::render_sources_index`, `render_source_detail` | |
| `/concepts/`, `/concepts/<slug>.html` | ✅ | `pages.py::render_concepts_index`, `render_concept_detail` | |
| `/entities/`, `/entities/<slug>.html` | ✅ | `pages.py::render_entities_index`, `render_entity_detail` | |
| `/papers/`, `/papers/<slug>.html` | ✅ | `pages.py::render_papers_index`, `render_paper_detail` | |
| `/repos/`, `/repos/<slug>.html` | ✅ | `pages.py::render_repos_index`, `render_repo_detail` | |
| `/topics/`, `/topics/<slug>.html` | ✅ | `pages.py::render_topics_index`, `render_topic_detail` | |
| `/syntheses/`, `/syntheses/<slug>.html` | ✅ | `pages.py::render_syntheses_index`, `render_synthesis_detail` | |
| `/questions/`, `/questions/<slug>.html` | ✅ | `pages.py::render_questions_index`, `render_question_detail` | |
| `/timeline/` | ✅ | `pages.py::render_timeline` | Mapa de calor + lista de días + carril de síntesis. |
| `/timeline/<YYYY-MM-DD>.html` (detalle por día) | ⚠ | aún n/a | Las celdas del mapa de calor enlazan provisionalmente a la página fuente `digest.md` de ese día. Subagent P está conectando las páginas de detalle diario mediante `StaticSiteBuilder`. |
| `/graph/` (2D + 3D interactivo) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, tooltips al pasar el cursor, etiquetas de aristas, zoom anclado al cursor. |
| `/about.html` | ✅ | `pages.py::render_about` | Esquema, información de compilación. |

### Exportaciones amigables para IA

| Artefacto | Estado | Fuente | Propósito |
|---|---|---|---|
| Archivo hermano `<page>.txt` por página | ✅ | [`tesserae/site/exports.py`](../../tesserae/site/exports.py) `write_siblings` | Vista en texto plano de una página (sin navegación ni estilos). |
| Archivo hermano `<page>.json` por página | ✅ | `exports.py::write_siblings` | `{title, kind, body, body_text, links, source_path, frontmatter}`. |
| `llms.txt` | ✅ | `exports.py::render_llms_txt` | Índice corto de llmstxt.org. |
| `llms-full.txt` | ✅ | `exports.py::render_llms_full_txt` | Cuerpo de todas las páginas, limitado a 5 MB. |
| `graph.jsonld` | ✅ | `exports.py::render_graph_jsonld` | `Dataset` de schema.org, solo nodos de la capa wiki. |
| `graph.json` | ✅ | `__init__.py::write_site` | Payload completo del grafo (incl. nodos de código para herramientas). |
| `search-index.json` | ✅ | [`tesserae/site/search.py`](../../tesserae/site/search.py) | Paleta + búsqueda de páginas; solo tipos de la capa wiki. |
| `sitemap.xml` | ✅ | `exports.py::render_sitemap_xml` | Todas las rutas emitidas, `lastmod` desde frontmatter. |
| `rss.xml` | ✅ | `exports.py::render_rss_xml` | Últimas 30 syntheses. |
| `robots.txt` | ✅ | `exports.py::render_robots_txt` | Permisivo — rastrear + indexar. |
| `ai-readme.md` | ✅ | `exports.py::render_ai_readme` | Mapa del sitio legible por máquina. |
| `manifest.json` | ✅ | `__init__.py::_manifest` | sha256 + tamaño de cada archivo emitido (arnés de idempotencia). |

### Diseño visual + UX

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| Tokens de diseño (temas claro + oscuro, acento terracota) | ✅ | [`tesserae/site/tokens.py`](../../tesserae/site/tokens.py) | Un único paquete CSS en `assets/style.css`. |
| Alternador de tema (persistente, sin destello) | ✅ | [`tesserae/site/js.py`](../../tesserae/site/js.py) | `data-theme="dark"` en `localStorage`, aplicado antes del pintado. |
| Paleta de búsqueda (`cmd+k` / `ctrl+k` / `/`) | ✅ | `js.py` | Coincidencia difusa sobre `search-index.json`; lista de páginas recientes. |
| TOC derecho fijo | ✅ | `pages.py` + `tokens.py` | Solo escritorio; cajón móvil mediante `<details>`. |
| Mapa de calor de actividad con etiquetas de mes + día de semana | ✅ | `components.py::heatmap_svg` | SVG de 26 semanas, las celdas enlazan al `digest.md` del día. |
| Sparkline (por concepto/entidad) | ✅ | `components.py::sparkline_svg` | Conteos semanales de menciones, últimas 12 semanas. |
| Shell móvil (carril de cajón, navegación inferior, tipografía fluida) | ✅ | `tokens.py` + `pages.py` | Objetivos táctiles ≥ 44 px. |
| Transiciones de página (opacidad 120 ms, prefers-reduced-motion) | ✅ | `tokens.py` | |
| Vista de grafo 3D + 2D (hover, etiquetas de aristas, zoom anclado al cursor) | ✅ | `pages.py::render_graph_view` + `js.py` | 3d-force-graph + Three.js, vendorizado como snapshot de CDN. |
| Pie de hermanos IA por página | ✅ | `components.py::ai_siblings_footer` | Enlaces en línea al `.txt` y `.json` de la página actual. |
| Páginas de historial de sesiones del arnés | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + [`tesserae/site/sessions.py`](../../tesserae/site/sessions.py) | Importación explícita de Claude Code/Codex; índice `/sessions/` y páginas de detalle con turnos markdown, carril izquierdo de turnos, uso de herramientas colapsado y entradas de búsqueda. |

### Canalización + CLI

| Función | Estado | Fuente | Notas |
|---|---|---|---|
| `project compile` llama a synthesis + wiki + site en orden | ✅ | [`tesserae/project.py`](../../tesserae/project.py) | Fase 3 del plan de rediseño. |
| `project build-site` independiente | ✅ | `project.py` + [`tesserae/cli.py`](../../tesserae/cli.py) | Lee `wiki/` + `graph.json`, escribe `site/`. |
| `project serve` HTTP local | ✅ | `cli.py` | Servidor stdlib simple. |
| `project deploy` → GitHub Pages | ✅ | [`tesserae/deploy.py`](../../tesserae/deploy.py) | Push de worktree a `gh-pages`; `--enable-pages` opcional vía CLI `gh`. `--build`, `--dry-run`, `--branch`, `--remote`, `--force`. |
| `project sessions discover/import/list` | ✅ | [`tesserae/harness_sessions.py`](../../tesserae/harness_sessions.py) + `cli.py` | Historial de sesiones entrante para Claude Code/Codex; el descubrimiento es explícito y limitado al directorio de trabajo del proyecto. |
| `project watch` recompila al cambiar | ✅ | [`tesserae/cli.py`](../../tesserae/cli.py) + [`tesserae/watch.py`](../../tesserae/watch.py) | Watcher por sondeo autónomo: `--interval`, `--debounce`, `--once`, `--paths`, `--quiet`. El supervisor multi-fuente vive en `project engine`/`daemon` (véase Motor de contexto). |
| `project context` — compila un documento de contexto con citas | ✅ | `cli.py` + [`tesserae/context_compiler.py`](../../tesserae/context_compiler.py) | Estrella del Pilar 3; véase la sección Motor de contexto. |
| `project refresh` / `project engine` / `project daemon` | ✅ | `cli.py` + [`tesserae/engine/`](../../tesserae/engine/) | Cadena de actualización en prosa + bucle supervisor; véase la sección Motor de contexto. |

## Funciones preexistentes (conservadas sin cambios)

### CLI e instalación

- ✅ Paquete Python instalable mediante `pyproject.toml`.
- ✅ Comandos de consola: `tesserae`, `tesserae`, `tesserae_mcp`.
- ✅ `scripts/install.sh` para instalación con `curl | bash`.
- ✅ Instalaciones editables por defecto para desarrollo local rápido.

### Extracción

- ✅ Extractor determinista de notas de investigación con vocabularios controlados de nodos/aristas.
- ✅ Extractor Claude CLI/OAuth para extracción estructurada de mayor calidad sin claves API.
- ✅ Enrutamiento selectivo de Claude por glob y límite de presupuesto.
- ✅ Extractor determinista de código de desarrollo para proyectos Python.
- ✅ Ingesta por lotes con hashing de contenido y soporte `--changed-only`.
- ✅ Lectura de fuentes tolerante a UTF-8 malformado.

### Gobernanza del grafo

- ✅ Lista controlada `ResearchNodeType` — ahora incluye `SYNTHESIS`.
- ✅ Lista blanca controlada de tipos de arista — ahora incluye `synthesizes`, `summarizes`.
- ✅ Validación para rechazar deriva de esquema.
- ✅ Canonicalización de alias.
- ✅ Cola de revisión para nodos casi duplicados ambiguos.
- ✅ Plantilla de decisiones de revisión y flujo de fusionar/mantener separado.
- ✅ Resumen de tendencias del corpus a partir de grafos por archivo.

### Persistencia e informes

- ✅ Exportación Graph JSON.
- ✅ Almacén de grafos SQLite.
- ✅ Almacén de grafos Kuzu opcional.
- ✅ Informe de grafo con conteos, cobertura de evidencia, nodos huérfanos, buckets de fecha y nodos con muchos alias.
- ✅ Informe competitivo que describe ideas absorbidas de MegaMem, Graphiti/Zep, MCP graph servers, agentic RAG.

### Flujo de trabajo local del proyecto

- ✅ `tesserae project init`
- ✅ `tesserae project ingest`
- ✅ `tesserae project compile`
- ✅ `tesserae project mcp-config`
- ✅ `tesserae project build-site`
- ✅ `tesserae project serve`
- ✅ `tesserae project deploy` (GitHub Pages)
- ✅ `tesserae project sessions discover/import/list` (importación explícita de historial de agente local)
- ✅ `tesserae project watch` (watcher por sondeo autónomo)
- ✅ `tesserae project engine` / `tesserae project daemon` (bucle supervisor — v0.5.0)
- ✅ `tesserae project refresh` (cadena en prosa ingesta → compilación → proyección — v0.5.0)
- ✅ `tesserae project context` (compilador de contexto bajo demanda — v0.5.0)
- ✅ `tesserae project export-agent-harness`
- ✅ `tesserae project export-obsidian`
- ✅ `tesserae project export-graphiti`
- ✅ `tesserae project sync-graphiti`

### Obsidian

- ✅ Exportación de vault lista para abrir.
- ✅ `.obsidian/app.json` y configuración de grafo.
- ✅ Proyección Markdown.
- ✅ Estructura `raw/assets/`.
- ✅ `_meta/dashboard.md` con consulta Dataview.

### Arneses de agente

Archivos de destino generados para:

- ✅ Claude Code: `CLAUDE.md`, `.claude/settings.json`
- ✅ Codex: `AGENTS.md`, `mcp.toml`
- ✅ Gemini: `GEMINI.md`, `.gemini/settings.json`
- ✅ Kiro: steering y configuración MCP
- ✅ Cursor: reglas de proyecto y configuración MCP
- ✅ OpenCode: `AGENTS.md`, `opencode.json`

### Graphiti / hechos temporales

- ✅ Proyección de hechos temporales con campos de procedencia, vigencia, confianza e invalidación.
- ✅ Exportación JSONL de episodios Graphiti sin dependencias.
- ✅ Smoke `sync-graphiti --dry-run` sin Graphiti instalado.
- ✅ Sincronización en vivo opcional con `graphiti_core` y Neo4j.

### Cognee

- ✅ Paquete Cognee JSONL (`nodes.jsonl`, `edges.jsonl`, `manifest.json`).
- ✅ Importación directa opcional solo-adición.
- ✅ Adaptador Cognee cognify opcional respaldado por Codex CLI/OAuth.
- ✅ Rutas de adaptador de embeddings determinista y Ollama para flujos smoke/calidad sin clave API.

### Servidor MCP

- ✅ `tesserae_mcp` / `python3 -m tesserae.mcp_server` sobre stdio JSON-RPC.
- ✅ Herramientas de recuperación/grafo: `schema`, `graph_summary`, `search_nodes`, `node_context` (con `use_ppr`), `search_facts`, `timeline`, `graph_ppr`, `wiki_page`, `raw_source`, `lint_report`.
- ✅ Herramientas del motor de contexto (v0.5.0): `compile_context`, `embedding_status`, `fresh_insights` (orden por decaimiento), `list_communities`, `find_session_findings`, `find_code_symbol_mentions`, `ask`.
- ✅ Herramientas de configuración: `tesserae_setup_plan`, `tesserae_setup_apply`.
- ✅ Registro multiproyecto: `list_projects`, `register_project`, `activate_project`, `unregister_project`, `list_sessions`. Despacho de URL de almacén vía `url_resolver`.

## Pruebas

La suite actual cubre:

- ✅ guardrails de ontología (incl. nuevo nodo `Synthesis` + aristas `synthesizes` / `summarizes`);
- ✅ extracción determinista;
- ✅ parsing/validación del wrapper Claude CLI;
- ✅ enrutamiento selectivo de Claude;
- ✅ flujo de canonicalización/revisión;
- ✅ ingesta por lotes;
- ✅ informes;
- ✅ persistencia SQLite/Kuzu;
- ✅ bundles/import patches de Cognee;
- ✅ exportación/sincronización Graphiti dry-run;
- ✅ flujo CLI del proyecto;
- ✅ exportación de arnés de agente;
- ✅ exportación Obsidian;
- ✅ generación frontend + integridad de enlaces (sin `nodes/codeclass-*.html`);
- ✅ idempotencia del almacén wiki;
- ✅ golden + idempotencia de synthesis projector;
- ✅ componentes, páginas, exportaciones y relevancia del sitio;
- ✅ forma de hermanos IA (`.txt` + `.json` por página);
- ✅ idempotencia end-to-end al compilar dos veces;
- ✅ columna del motor: pipeline, cadena de actualización, núcleo del demonio + fuentes, CLI `project engine`;
- ✅ memoria de automejora: sidecar, decaimiento/reemplazo, supresión de reemplazo (incl. MCP), refuerzo/contradicción;
- ✅ recuperación + embeddings: búsqueda híbrida, PPR, embeddings reales por defecto (Fase 6);
- ✅ compilador de contexto: forma/integridad de citas/determinismo/presupuesto/repliegue PPR, CLI `project context`, MCP `compile_context`;
- ✅ compilación incremental (experimental): diferenciador, puertas de paridad, preparación de procedencia, procedencia SQLite;
- ✅ instalación de paquete y contrato del instalador.

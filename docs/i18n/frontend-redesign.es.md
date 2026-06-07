# Rediseño del frontend — recorrido anotado de rutas

<!-- translations:start -->
<p align="center"><a href="../frontend-redesign.md">English</a> · <a href="frontend-redesign.ko.md">한국어</a> · <a href="frontend-redesign.zh.md">中文</a> · <a href="frontend-redesign.ja.md">日本語</a> · <a href="frontend-redesign.ru.md">Русский</a> · <a href="frontend-redesign.es.md">Español</a> · <a href="frontend-redesign.fr.md">Français</a> · <a href="frontend-redesign.de.md">Deutsch</a></p>
<!-- translations:end -->
Este documento es una visita guiada por todas las rutas visibles del sitio estático rediseñado de Tesserae. Complementa el modelo de alto nivel en [`architecture.md`](architecture.es.md) y la tabla de estado en [`feature-map.md`](feature-map.es.md).

Después de `tesserae project compile`, el sitio vive en `.tesserae/site/`. Para explorarlo localmente:

```bash
tesserae project serve --port 8765
# open http://127.0.0.1:8765/
```

Cada ruta es un archivo HTML estático con dos archivos hermanos (`<page>.txt`, `<page>.json`) para consumidores de IA. Las exportaciones de todo el sitio (`llms.txt`, `llms-full.txt`, `graph.jsonld`, `sitemap.xml`, `rss.xml`, `robots.txt`, `ai-readme.md`, `manifest.json`) se describen al final de este documento.

Leyenda de estado: ✅ entregado · ⚠ en progreso.

## Convenciones en todas las páginas

Cada página hoja sigue la misma anatomía (§3.3 de la especificación de diseño):

```
breadcrumbs
eyebrow (type · last updated · ≈ reading time)
TITLE
right-rail TOC (sticky on desktop, drawer on mobile)
lead paragraph
rendered markdown body
Mentions in the corpus  — bullets with badges + counts
Related (4-signal ranked) — card grid
Source provenance       — file path, line excerpt
Activity                — sparkline, weekly mentions
AI siblings footer      — links to the .txt and .json
```

Chrome común del sitio:

- **Barra superior.** Logotipo + nombre del proyecto a la izquierda, disparador de búsqueda + selector de tema a la derecha.
- **Raíl izquierdo** (desktop ≥ 1024 px): árbol jerárquico — Home, Recent activity, Sources, Concepts, Entities, Papers, Repos, Topics, Syntheses, Open questions, Sessions, Timeline, Graph view, About. Los conteos vienen de `manifest.json`.
- **Navegación inferior** (mobile): el raíl tipo drawer se colapsa; la navegación inferior expone los tipos más usados.
- **Paleta de búsqueda.** `cmd+k` / `ctrl+k` / `/` — coincidencia difusa sobre `search-index.json`, limitada a los tipos de wiki. Las páginas recientes se persisten en `localStorage`.
- **Tema.** Claro por defecto; el interruptor persiste `data-theme="dark"` en `localStorage`. Se aplica antes del primer pintado para evitar parpadeos.

## Home

### `/` ✅

> _Screenshot: home.png_

La página de inicio es el pulso del proyecto. La impulsa la síntesis global `pulse` (`wiki/syntheses/pulse.md`), que se regenera en cada compilación. El hero es una fila de estadísticas — sources, concepts, papers, open questions — seguida por 1-3 tarjetas de "what's new this week" tomadas del cuerpo más reciente de `pulse`. Debajo del hero, puntos de entrada curados enlazan a la página índice de cada tipo para que un visitante nuevo pueda profundizar sin tener que leer la navegación.

Esta es la primera página donde conviene aterrizar a un agente LLM; entrega el resumen del corpus con la mejor relación señal-ruido. Las tarjetas enlazan a páginas hoja, no de vuelta a índices.

**Interacciones destacadas.** Los clics en la fila de estadísticas desplazan o navegan al índice del tipo correspondiente. El texto del hero es editable: si escribes a mano `wiki/overview.md`, la página de inicio lo recoge en la siguiente compilación.

**Rutas relacionadas.** [Timeline](#timeline) para el registro de actividad, [Syntheses](#syntheses) para la forma larga, [Graph](#graph-view) para la vista espacial.

## Sources

Estos son los documentos sin procesar L1: archivos en `data/`, `docs/` y el árbol del proyecto referenciado por `.tesserae/config.json`. Cada fuente se convierte en un nodo `SourceDocument` (o `Paper` / `Repository`) y obtiene una página wiki proyectada por `WikiLayerProjector`.

### `/sources/` ✅

> _Screenshot: sources-index.png_

Una tabla de cada documento sin procesar que conoce el corpus. Columnas: badge de tipo (Document / Paper / Repository / Project), título, conteo de menciones, última actualización. La tabla tiene franjas zebra, el hover muestra una vista previa de 1 línea, y el badge de tipo se puede filtrar mediante la paleta de búsqueda (`/ kind:papers`).

Este es el índice del agente cuando necesita enumerar la evidencia literal desde la que se construye la wiki.

**Rutas relacionadas.** [Papers](#papers) para el corte solo de artículos, [Repos](#repos) para solo repos, [Concepts](#concepts) para la vista de lo extraído.

### `/sources/<slug>.html` ✅

> _Screenshot: source-detail.png_

Un único documento fuente, renderizado mediante el pipeline markdown de stdlib (`tesserae/site/markdown.py`). El cuerpo es el markdown original con renderizado seguro de enlaces e imágenes. Debajo del cuerpo:

- **Mentions** — cada concept / entity / paper extraído de esta fuente, con badges de tipo de arista.
- **Backlinks** — todas las demás páginas wiki que enlazan aquí.
- **Related** — ranking de cuatro señales (direct link 3.0 + source overlap 4.0 + Adamic-Adar 1.5 + type affinity 1.0).
- **Source provenance** — ruta del archivo original + primeras 12 líneas del archivo bruto como evidencia.
- **Activity** — sparkline de menciones semanales durante las últimas 12 semanas.
- **AI siblings footer** — vista de texto plano `<page>.txt`, registro estructurado `<page>.json`.

**Interacciones destacadas.** URL e IDs de arXiv autoenlazados en el cuerpo; click-to-copy en bloques de código; el TOC del raíl derecho sigue el desplazamiento.

## Concepts

Concepts son las ideas, términos, algoritmos, arquitecturas y metodologías recurrentes extraídas de todo el corpus. Cubren `Concept`, `TechnicalTerm`, `Algorithm`, `MathematicalConcept`, `MethodologicalConcept`, `ArchitecturePattern`, `TrainingParadigm`, `InferenceStrategy`, `EvaluationProtocol`, `Task`, `Capability`, `ObjectiveFunction`.

### `/concepts/` ✅

> _Screenshot: concepts-index.png_

Una cuadrícula de tarjetas con filtros facetados. Cada tarjeta lleva el badge de tipo, título, definición de 1 línea, conteo de menciones y fecha de última actualización. La página admite filtros por tipo mediante tag chips (Algorithm, Architecture, Training paradigm, …) y ordena por conteo de menciones por defecto.

Aquí es donde vas a preguntar "¿de qué habla este corpus?"

**Rutas relacionadas.** [Papers](#papers) — los conceptos normalmente se introducen en artículos, [Topics](#topics) — los conceptos se agrupan en temas.

### `/concepts/<slug>.html` ✅

> _Screenshot: concept-detail.png_

Una página rica de concepto con una definición sintetizada (o el primer párrafo de la fuente más autorizada si no hay síntesis disponible), una lista de todas las menciones del corpus, vecinos relacionados rankeados y el sparkline de actividad.

La sección "Mentions in the corpus" es la de mayor peso para los agentes: lista cada artículo / fuente / repo que referencia el concepto, con un extracto de una línea para contexto.

**Interacciones destacadas.** El TOC del raíl derecho sigue los `<h2>` / `<h3>` del cuerpo; la cuadrícula de tarjetas relacionadas respeta la puntuación de cuatro señales para que los vecinos se sientan relevantes y no solo adyacentes.

## Entities

Entities son las cosas nombradas e identificables del corpus: `Model`, `Dataset`, `Benchmark`, `Metric`, `Organization`, `Person`. Se proyectan desde nodos del grafo y sus páginas enfatizan afirmaciones y resultados por encima de la prosa.

### `/entities/` ✅

> _Screenshot: entities-index.png_

Una tabla facetada por tipo. Columnas: badge de tipo, nombre, summary (primera oración de `description` en frontmatter si existe; si no, primer párrafo del cuerpo), conteo de menciones. Filtrable por type chip.

### `/entities/<slug>.html` ✅

> _Screenshot: entity-detail.png_

La página de detalle destaca tres secciones:

- **Claims** — aristas `ContributionClaim`, `PerformanceClaim`, `ComparisonClaim`, `LimitationClaim`, `CausalClaim` que tocan esta entidad, con extractos de evidencia inline. (Los nodos Claim no obtienen sus propias URL: aparecen como viñetas aquí.)
- **Reported results** — cada `Result` / `evaluated_on` / `reports_result` enlazado a esta entidad, listado con métrica + score + procedencia del artículo.
- **Mentions in the corpus** — la misma forma que en las páginas de conceptos.

Esta es la página correcta cuando un agente necesita responder "¿qué sabemos sobre model X?" o "¿en qué datasets se reporta benchmark Y?"

## Papers

Papers es literatura de investigación tratada como evidencia de primera clase. El rediseño los sacó del grupo genérico de fuentes y les dio un tipo dedicado para poder renderizar affordances específicas de artículos.

### `/papers/` ✅

> _Screenshot: papers-index.png_

Una cuadrícula de tarjetas filtrable por facetas con chips de year, topic y family. Cada tarjeta: título, autores (los tres primeros + "et al."), extracto de abstract de 1 línea, badge de año, conteo de menciones. Ordena por año descendente por defecto.

### `/papers/<slug>.html` ✅

> _Screenshot: paper-detail.png_

Un layout de tarjeta de artículo: título, autores, año, extracto del abstract, luego secciones para:

- **Contributions** — aristas `ContributionClaim` desde el artículo.
- **Results** — aristas `reports_result` con métrica + score.
- **Comparisons** — aristas `compares_against`.
- **Related concepts** — aristas `introduces` / `extends` / `uses`.
- **Open questions** — `OpenQuestion` enlazadas a través del artículo.

Los enlaces arXiv se autoenlazan mediante `tesserae/site/markdown.py`; el TOC del raíl derecho refleja la lista de secciones anterior.

## Repos

Repos son proyectos de software: `Repository`, `Project`, `CodeProject`. El rediseño explícitamente no renderiza páginas HTML por clase / función; las páginas de repos resumen la superficie del proyecto y enlazan al árbol de código fuente.

### `/repos/` ✅

> _Screenshot: repos-index.png_

Una cuadrícula de tarjetas con badges de lenguaje. Cada tarjeta: nombre, extracto de README de 1 línea, lenguaje(s) principal(es), conteo de estrellas si se conoce, última actualización.

### `/repos/<slug>.html` ✅

> _Screenshot: repo-detail.png_

La página de repo muestra:

- **README excerpt** — primeros ~600 caracteres del `README.md` del repo si está en el corpus.
- **Dependencies** — aristas salientes de tipo `depends_on` / `imports` / `uses` hacia otros repos / models / concepts.
- **Implements** — aristas `implemented_in` desde artículos (es decir, "este repo implementa paper X").
- **Module overview** — conteos de módulos / clases / funciones, pero sin enlaces por función: por diseño.
- **Related** — ranking de cuatro señales.

Esta es la página correcta para un agente que necesita elegir un repo dentro de una familia de enfoques.

## Topics

Topics agrupa conceptos en campos más amplios: `ResearchField`, `ResearchTopic`, `ProblemArea`, `ApproachFamily`, `Trend`. Las páginas de temas se proyectan en parte desde nodos del grafo y en parte se sintetizan; consulta [Syntheses](#syntheses) para las páginas de overview de campo que alimentan la introducción de un tema.

### `/topics/` ✅

> _Screenshot: topics-index.png_

Una cuadrícula de tarjetas indexada por type chip. Cada tarjeta expone el nombre del tema, el campo padre y estadísticas "X papers · Y concepts · Z repos".

### `/topics/<slug>.html` ✅

> _Screenshot: topic-detail.png_

Una página de tema abre con un párrafo de síntesis (cuando existe en `wiki/syntheses/topic-<slug>.md`) y lista:

- **Papers in this topic** — tabla.
- **Approach families** — subtemas de tipo `ApproachFamily`.
- **Concepts in scope** — nube de chips.
- **Open questions** — nodos `OpenQuestion` enlazados.
- **Related fields** — vecinos `belongs_to` / `rising_in`.

**Rutas relacionadas.** [Syntheses → topic-…](#syntheses) para la narrativa de formato largo, [Concepts](#concepts) para los átomos constituyentes.

## Syntheses

Syntheses son páginas de orden superior producidas por `SynthesisProjector`. Siete tipos (pulse, daily_digest, weekly, topic, comparison, field_overview) cubren los cortes temporales y estructurales del corpus. Hoy los cuerpos de síntesis son plantillas deterministas; `TESSERAE_SYNTHESIS_LLM=1` es el hook de actualización LLM (stub).

### `/syntheses/` ✅

> _Screenshot: syntheses-index.png_

El índice lista cada síntesis agrupada por tipo, ordenada por `generated_at` descendente dentro de cada grupo. Cada fila: kind badge, título, lead de 1 línea, timestamp generated-at.

### `/syntheses/<slug>.html` ✅

> _Screenshot: synthesis-detail.png_

Una página de síntesis renderiza el cuerpo markdown tal cual. El frontmatter lleva `synthesis_kind`, `slug`, `sources`, `inputs`, `generated_at`, `generator`, `content_hash`; la página expone `synthesis_kind` y `generated_at` en el eyebrow. Debajo del cuerpo:

- **Sources consumed** — los destinos de aristas `summarizes`: uno por cada fuente de la que se alimentó la síntesis.
- **Inputs (graph nodes)** — los destinos de aristas `synthesizes`: cada nodo que entró.
- **Related syntheses** — mismo tipo / inputs solapados.

La síntesis `pulse` es la página de inicio; las síntesis daily / weekly anclan el raíl de [Timeline](#timeline).

## Questions

Open questions se extraen del corpus como nodos `OpenQuestion`: lugares donde un artículo, fuente o síntesis marca explícitamente un problema no resuelto.

### `/questions/` ✅

> _Screenshot: questions-index.png_

Vista de lista, una fila por pregunta abierta. Columnas: texto de la pregunta, fuentes que la plantearon, conceptos relacionados, edad (desde la primera vez que se vio). Ordenada por recencia por defecto.

### `/questions/<slug>.html` ✅

> _Screenshot: question-detail.png_

Una página enfocada en una sola pregunta abierta con:

- El texto literal de la pregunta.
- **Raised in** — sources / papers / syntheses donde aparece la pregunta.
- **Related concepts** — de qué trata la pregunta.
- **Adjacent questions** — misma fuente o conceptos compartidos.

Esta es la página en la que aterrizar cuando a un agente se le pregunta "¿qué sigue sin respuesta en esta área?"

## Sessions

Sessions son transcripts locales importados de AI-harness, normalizados en `.tesserae/harness_sessions/`, y luego renderizados como memoria de proyecto buscable. La importación es explícita mediante `tesserae project sessions discover --import` o `tesserae project sessions import ...`; las builds normales del sitio solo consumen registros ya normalizados.

### `/sessions/` ✅

> _Screenshot: sessions-index.png_

El índice de sessions agrupa sesiones de nivel superior de Claude Code y Codex para el proyecto. Tarjetas/tablas exponen title, harness, model, project path, start/end timestamps, message count, tool count, token counts when known, files touched, commands y preview text. La página está enlazada desde el raíl global, las Browse cards de inicio y las entradas de la paleta de búsqueda de tipo `session`.

### `/sessions/<project>/<session>.html` ✅

> _Screenshot: session-detail.png_

Una página de detalle de session usa el shell compartido en lugar de un volcado bruto de transcript. El layout incluye hero, stat strip, High-Level Summary, Timeline & size, decisions/files/commands/tools/errors, árbol de subagent colapsado y un bloque de conversación turno por turno.

El raíl izquierdo específico de session reemplaza el raíl genérico de árbol de archivos por anclas de turnos user/assistant (`#turn-N`). Los turnos de usuario y asistente se renderizan mediante el renderer markdown del sitio; superficies semánticas como inline code, command/tag markup, paths, filenames y hashtags se convierten en chips compactos. Las tool calls se colapsan bajo el turno de asistente precedente, con fondos oscuros de code/tool tanto en tema claro como oscuro.

La tipografía actual de detalle mantiene la prosa normal de conversación compacta a 8 px, code fences genéricos de conversación a 10 px, contenido de fenced bash/shell code a 11 px, tool details/summary a 10 px, tool headers a 8 px y tool payload text a 6 px. Consulta [`session-history.md`](session-history.es.md) para el mapa de selectores y la checklist de privacidad de publicación.

## Timeline

La página timeline es el registro de actividad: ¿cuándo creció el corpus, qué tipos de nodos se añadieron y cómo se ve a través de días y semanas?

### `/timeline/` ✅

> _Screenshot: timeline.png_

La página tiene tres raíles:

- **Activity heatmap** — SVG de 26 semanas con etiquetas de mes + día de semana, celdas coloreadas por node-add-count. Cada celda enlaza a la página fuente `digest.md` del día cuando existe.
- **Days** — últimos 60 días fechados; cada fila muestra `<date> · X activity · Y papers`. Cuando la fecha tiene un `digest.md`, la fecha es un enlace.
- **Syntheses rail** — cada síntesis ordenada por recencia, kind badge + title + timestamp.

Esta es la página para marcar como favorita para la pregunta "qué ha estado pasando".

### `/timeline/<YYYY-MM-DD>.html` ✅

> _Screenshot: timeline-day.png_

Las páginas de detalle por día — que listan cada paper / repo / concept / synthesis vinculado a ese día de calendario — ya están disponibles. `render_timeline_day` (`tesserae/site/pages.py`) se emite por cada día con fecha mediante `StaticSiteBuilder` (`tesserae/site/__init__.py`), y cada página se registra en `manifest.json` como una ruta `timeline_day`. Las celdas del heatmap enlazan directamente a la página del día (recurriendo a la página fuente `digest.md` del día solo cuando no existe una ruta por día).

## Graph view

### `/graph/` ✅

> _Screenshot: graph.png_

La vista interactiva de grafo es un 3D force layout (3d-force-graph + Three.js, vendorizados como snapshot CDN en `assets/`) con fallback 2D. Los nodos se colorean por `ResearchNodeType`. Las aristas muestran su tipo como etiqueta al hacer hover.

**Interacciones destacadas.**

- Hover sobre un nodo → tooltip con nombre, tipo, conteo de menciones.
- Click en un nodo → navegar a su página wiki.
- Hover sobre una arista → etiqueta que muestra la relación (`uses` / `extends` / `compares_against` / …).
- Rueda del ratón → zoom anclado al cursor (acerca hacia el cursor, no hacia el centro).
- Arrastrar → orbit (3D) o pan (2D).
- Alternar 2D/3D en la esquina superior derecha.

El payload incrustado en la página está limitado a `MAX_GRAPH_NODES = 1500` (consulta [`pages.py`](../../tesserae/site/pages.py)). El grafo completo siempre está disponible en `/graph.json` para tooling. Los nodos de code-graph (`CodeClass`, `CodeFunction`, `Dependency`, …) se filtran de la visualización por diseño.

**Rutas relacionadas.** Cada página wiki enlaza a una vista enfocada de subgrafo.

## About

### `/about.html` ✅

> _Screenshot: about.png_

Una página estática que explica el esquema (cada `ResearchNodeType` y whitelist de aristas, con descripciones de una línea), la información de build (commit SHA, generator version, timestamp generated-at) y el inventario de AI exports.

Esta es la página correcta para ubicar a un nuevo contribuidor sobre qué tipos existen y para qué sirve cada uno.

## AI siblings — cómo cada página también es datos

Tesserae entrega cada página en tres formas: el HTML humano, un hermano de texto plano y un hermano JSON estructurado. Además, exportaciones de todo el sitio para crawlers y agentes.

### Per-page `<page>.txt` ✅

Vista de texto plano de una página: sin nav, sin styling, sin decoración markdown más allá de lo que el cuerpo use explícitamente. Adecuada cuando un agente quiere ingerir una sola página como contexto sin escribir un scraper HTML.

```bash
curl http://127.0.0.1:8765/concepts/diffusion-model.txt
```

### Per-page `<page>.json` ✅

Un registro estructurado:

```json
{
  "title": "...",
  "kind": "concepts",
  "body": "raw markdown body",
  "body_text": "plain-text body",
  "links": ["..."],
  "source_path": "data/...",
  "frontmatter": { ... }
}
```

Adecuado cuando una herramienta necesita acceso tipado — la lista de enlaces, el frontmatter, el kind tag — sin un parser markdown.

### `/llms.txt` ✅

El índice corto de llmstxt.org. Una sola página que lista cada tipo con las entradas más relevantes por tipo. Adecuado para la primera solicitud que hace un agente LLM al descubrir el sitio.

### `/llms-full.txt` ✅

La forma larga de llmstxt.org: todas las páginas wiki concatenadas. Limitada a 5 MB; si se alcanza el límite, un marcador `[TRUNCATED — see graph.jsonld for the full set]` cierra el archivo. Adecuada cuando un agente tiene presupuesto para ingerir todo el corpus en una solicitud y un contexto de 5 MB.

### `/graph.json` ✅

El payload completo de `ResearchGraph`, incluidos nodos de code-graph que no tienen páginas HTML. Adecuado cuando una herramienta quiere el grafo completo para su propio análisis (consumidores MCP, Cognee y Graphiti leen esto).

### `/graph.jsonld` ✅

Un JSON-LD `Dataset` de schema.org. Solo nodos wiki-layer (sin code nodes). Adecuado para crawlers que entienden datos estructurados: Google Knowledge Graph, search indexers, agregadores conscientes de schema.org.

### `/search-index.json` ✅

El índice de la paleta + búsqueda de páginas. Solo tipos wiki-layer. Adecuado al integrar una UI de búsqueda de terceros; el esquema es una lista de entradas `{kind, title, slug, body, score_hints}`.

### `/sitemap.xml` ✅

Cada ruta emitida con `lastmod` derivado del frontmatter (`generated_at`, `updated_at`, `published_at`, `date`). Adecuado para motores de búsqueda.

### `/rss.xml` ✅

Las últimas 30 síntesis ordenadas de más nueva a más antigua. Adecuado para "subscribe to this wiki": lectores RSS, IFTTT, listas de correo.

### `/robots.txt` ✅

Permisivo: crawl + index everything. La wiki está pensada para que la lean agentes.

### `/ai-readme.md` ✅

Un mapa del sitio legible por máquina para AI agents que no manejan bien HTML. Lista cada artifact anterior con su purpose y una descripción de una línea de cuándo es correcto cada format.

### `/manifest.json` ✅

Un registro sha256 + size para cada emitted file del sitio. Lo usan:

- La prueba de idempotencia compile-twice (`tests/test_project_e2e_redesign.py`).
- Downstream tooling que quiere detectar "¿ha cambiado este sitio desde la última visita?" sin un diff completo.
- El comando deploy, para omitir pushes cuando no cambió nada.

## Elegir el formato correcto

| Si eres… | Lee |
|---|---|
| Una persona que visita por primera vez | `/` y luego profundiza en [Concepts](#concepts) o [Papers](#papers) |
| Una persona que quiere "qué hay de nuevo" | [Timeline](#timeline) y [Syntheses](#syntheses) recientes |
| Una persona que quiere estructura | [Graph view](#graph-view) |
| Un agente LLM haciendo una consulta | `<page>.json` para acceso tipado |
| Un agente LLM haciendo una consulta con presupuesto limitado | `<page>.txt` |
| Un agente LLM ingiriendo todo | `/llms-full.txt` (≤ 5 MB) o `/graph.jsonld` (sin límite) |
| Una herramienta que construye una UI personalizada | `/search-index.json` + `/graph.json` |
| Un motor de búsqueda | `/sitemap.xml` + `/graph.jsonld` |
| Un suscriptor | `/rss.xml` |
| Un detector de cambios | `/manifest.json` |

## Documentos relacionados

- [Architecture](architecture.es.md) — el modelo de tres capas, mapa de módulos e historia de idempotencia.
- [Feature map](feature-map.es.md) — cada feature con estado, archivos fuente y enlaces aquí.
- [Quickstart](quickstart.es.md) — ruta mínima desde `project init` hasta un sitio navegable.
- [Self-dogfood demo](self-dogfood.es.md) — ejecutar Tesserae contra su propio repo, incluido este sitio.

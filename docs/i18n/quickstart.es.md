# Inicio rápido

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
Esta página muestra el camino más corto desde un directorio de proyecto existente hasta un Tesserae navegable.

## Resumen de comandos

La CLI está agrupada: un puñado de verbos cotidianos en el nivel superior, más grupos
(`sessions`, `vault`, `export`, `code`, `config`, `projects`, `integrations`,
`lab`) para el resto. Ejecuta `tesserae --help` para ver todo el árbol:

```text
usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  context       Compile agent-ready context for a query
  ask           Ask the project memory a question
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query

ANALYSIS
  query         Raw retrieval over the graph (top-k, kind filters)
  lint          Graph lint report (--fix-trivial, --severity, --json)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  config        llm | show — machine-wide defaults (~/.tesserae/config.json)
  projects      register | list | activate | unregister | mcp-config — registry
  integrations  refresh raganything|understand-anything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve | schema-drift — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

Para ver los flags de un comando concreto, ejecuta `tesserae <command> --help` (por ejemplo, `tesserae compile --help`).

## 1. Ejecutar el asistente de configuración

Desde el proyecto que quieres indexar:

```bash
cd /path/to/my-project
tesserae init
```

El asistente detecta source comunes como `README.md`, `docs`, `src`, `lib`, `app`, `packages` y `data`, y luego escribe `.tesserae/config.json`. También configura el Cognee backend por defecto para que `tesserae ask` pueda probar Cognee y recurrir a la búsqueda wiki compilada.

Para una configuración no interactiva (CI, scripts), pasa `--yes` para aceptar los valores por defecto detectados sin preguntar:

```bash
tesserae init --yes
```

Para una configuración totalmente automática con Understand Anything y Cognee runtime memory habilitados:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
  --run-cognee \
  --install-cognee
```

Qué hace eso:

| Flag | Effect |
|---|---|
| `--with-understand-anything` | Añade la UA graph projection como source. |
| `--install-understand-anything` | Instala/actualiza las UA companion skills. |
| `--understand-anything-platform codex` | Usa Codex para ejecutar el UA refresh wrapper gestionado por Tesserae. |
| `--with-raganything` | Habilita el ingestion multimodal vía RAG-Anything. |
| `--install-raganything` | Instala raganything[all] durante la configuración. |
| `--raganything-parser` | Elección de parser: mineru (por defecto), docling, paddleocr. |
| `--run-raganything` | Refresca automáticamente RAG-Anything en cada compile. |
| `--run-cognee` | Ejecuta un Cognee runtime cognify best-effort durante el compile. |
| `--install-cognee` | Instala Cognee con el Python actual si falta. |

Los usuarios no necesitan conocer la ruta de instalación de UA ni escribir `/understand`; cuando el UA graph falta o está obsoleto, `tesserae compile` ejecuta `tesserae integrations refresh understand-anything`.

> **Saltarse el asistente.** `tesserae init --bare` escribe un `.tesserae/config.json` mínimo sin detección de source ni sondeo de backend — útil cuando quieres editar el config a mano antes del primer compile.

## 2. Compilar el grafo y las proyecciones

```bash
tesserae compile
```

`compile` escribe los artefactos duraderos:

```text
.tesserae/
  config.json
  graph.json
  manifest.json
  sqlite.db
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  report.md
  competitive_report.md
  markdown_projection/
  obsidian_vault/
  agent_harness/
  harness_sessions/
  site/
  cognee_bundle/
```

Después de la primera ejecución usa `--changed-only` para omitir los archivos markdown sin cambios, preservando el grafo anterior cuando no cambia ningún archivo. Si Understand Anything está habilitado, compile primero refresh/materialize `.tesserae/external/understand-anything.md`; si Cognee runtime está habilitado, también actualiza Cognee de forma best-effort tras escribir `.tesserae/cognee_bundle/`.

Para hacer ingest ad-hoc de rutas adicionales sin tocar los source configurados, pásalas posicionalmente: `tesserae compile path/to/extra.md docs/`.

### Los interruptores de integración ahora viven en config

`tesserae compile` está deliberadamente limitado a los flags cotidianos (paths
posicionales más `--project`, `--changed-only`, `--limit`, `--refresh-integrations`,
`--sessions`/`--no-sessions` y los tres flags de LLM). Todos los demás flags
anteriores de compile se trasladaron a un bloque `compile_options` en
`.tesserae/config.json`; el valor por defecto de argparse anterior sigue siendo el
fallback. Establece una clave allí para cambiar el comportamiento:

| Clave `compile_options` | Flag antiguo | Por defecto | Qué hace |
|---|---|---|---|
| `source_kind` | `--source-kind` | (ninguno) | Sobrescribe el source kind configurado. |
| `trends` | `--trends` | `false` | Añade nodos Trend a nivel de corpus. |
| `min_trend_sources` | `--min-trend-sources` | `2` | Mínimo de source necesarios para un nodo Trend. |
| `exclude_data` | `--exclude-data` | `false` | Omite la auto-inclusión implícita de `project_root/data`. |
| `no_vault_pull` | `--no-vault-pull` | `false` | No vuelve a hacer pull de las ediciones existentes del vault antes del compile. |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | Reinyecta resultados de extraction previos en la ejecución. |
| `sessions_llm` | `--sessions-llm` | (auto) | Modo de extracción de sesiones por LLM (`auto`/`true`/`false`). |
| `sessions_model` | `--sessions-model` | (ninguno) | Sobrescribe el modelo LLM usado para la extracción de sesiones. |
| `cognee_add` | `--cognee-add` | `false` | Añade el Cognee bundle al dataset (sin cognify). |
| `cognee_cognify` | `--cognee-cognify` | `false` | Añade el bundle y ejecuta Cognee cognify. |
| `cognee_codex_cognify` | `--cognee-codex-cognify` | `false` | Ejecuta cognify con el LLM client de Cognee parcheado a Codex. |
| `cognee_codex_model` | `--cognee-codex-model` | `gpt-5.4` | Modelo de Codex CLI para `cognee_codex_cognify`. |
| `cognee_codex_timeout` | `--cognee-codex-timeout` | `300` | Timeout por llamada de Codex CLI (segundos). |
| `cognee_dataset` | `--cognee-dataset` | `tesserae_research_graph` | Nombre del dataset de Cognee. |
| `cognee_embedding_provider` | `--cognee-embedding-provider` | `deterministic` | Embedding provider para la lane de Cognee. |
| `cognee_ollama_embedding_model` | `--cognee-ollama-embedding-model` | `qwen3-embedding:0.6b` | Modelo de embedding de Ollama. |
| `cognee_ollama_embedding_endpoint` | `--cognee-ollama-embedding-endpoint` | `http://127.0.0.1:11434/api/embed` | Endpoint `/api/embed` de Ollama. |
| `cognee_ollama_embedding_timeout` | `--cognee-ollama-embedding-timeout` | `120` | Timeout de la petición de embedding de Ollama (segundos). |
| `cognee_local_embedding_dimensions` | `--cognee-local-embedding-dimensions` | `128` | Dimensionalidad del embedding local. |
| `cognee_system_root` | `--cognee-system-root` | (ninguno) | Directorio system root aislado de Cognee. |
| `cognee_data_root` | `--cognee-data-root` | (ninguno) | Directorio data root aislado de Cognee. |

> **Pipeline de un solo paso.** `tesserae refresh` ejecuta todo el bucle en proceso: importa cualquier sesión nueva de agente, compila y sincroniza el vault en un solo comando. Pasa `--changed-only` para el compile incremental opcional.

## 3. Construir y servir el frontend estático

`serve` construye automáticamente el site si falta, así que un solo comando te da un Tesserae navegable:

```bash
tesserae serve --port 8765
```

Abre:

```text
http://127.0.0.1:8765/
```

Para construir el site de forma explícita (por ejemplo, para deploy sin servir) usa `export site`; pasa `--no-build` a `serve` cuando quieras navegar un site construido previamente sin reconstruirlo:

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### Reconstrucción automática al guardar

Empareja el servidor de desarrollo con el watcher integrado para que las ediciones bajo `data/` y `docs/` disparen un recompile incremental:

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` sondea cada 2 s, hace debounce 1 s y ejecuta `compile --changed-only`. Usa `--once` para reconstrucciones estilo cron (instantáneas frente a `.tesserae/.watch-cache.json`), `--paths <dir>` para añadir directorios de vigilancia personalizados y `--interval` / `--debounce` para ajustar la cadencia.
<!-- END: subagent-r-watch -->

### Ejecutar el daemon de refresh

Si quieres un motor siempre activo que mantenga la base de conocimiento fresca por sí mismo — vigilando tus source, fusionando ráfagas de ediciones y recompilando automáticamente — inicia el daemon supervisado:

```bash
tesserae engine
```

`engine` es el supervisor de larga duración: sondea cada 2 s y espera una ventana de silencio de 1 s antes de cada reconstrucción. Ajusta la cadencia con `--interval` y `--debounce`, apúntalo a otro proyecto con `--project`, o pasa `--once` para ejecutar un único ciclo de drain determinista y salir (útil para cron o CI). Es la contraparte sin intervención de `export site --watch`: déjalo corriendo y el grafo, el vault y el site se mantienen al día mientras tú y tus agentes trabajáis.

Para un recorrido anotado de cada ruta visible — home, sources, concepts, entities, papers, repos, topics, syntheses, questions, timeline, graph, además de los AI siblings — consulta [`docs/frontend-redesign.md`](frontend-redesign.es.md).

El frontend es ligero en dependencias y escribe:

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. Importar el historial local de sesiones de agente

La importación del historial de sesiones es explícita: el compile/build normal lee sesiones ya normalizadas, pero no escanea por su cuenta los almacenes privados de transcripciones de Claude Code o Codex.

```bash
# Preview matching Claude Code/Codex sessions for this project:
tesserae sessions discover

# Normalize and store them under .tesserae/harness_sessions/:
tesserae sessions discover --import

# Confirm the imported set:
tesserae sessions list

# Rebuild so sessions/index.html and session detail pages are emitted:
tesserae export site
```

Las sesiones importadas aparecen en la sección global Sessions, en la búsqueda del site y en las tarjetas Browse de la home. Las páginas de detalle de sesión renderizan los turnos user/assistant como markdown legible, adjuntan los bloques tool-use bajo el turno assistant anterior y exponen un riel de turnos a la izquierda para la navegación `#turn-N`. Para notas de privacidad, formatos de importación y el mapa tipográfico actual de transcripciones, consulta [`docs/session-history.md`](session-history.es.md).

## 5. Lint del wiki

```bash
tesserae lint
```

Recorre el graph + wiki + site compilados y marca orphan papers, stale citations, drift entre graph y wiki/, ghost synthesis inputs y más. Escribe `.tesserae/lint-report.md` y `.tesserae/lint-report.json`. Pasa `--fix-trivial` para aplicar auto-correcciones seguras (edges `implemented_in` faltantes, poda de ghost-input) y `--severity error` para que el código de salida solo falle ante errores.

## 6. Consultar el wiki

```bash
tesserae query "What is Gaussian Splatting?"
```

Solo búsqueda por defecto — BM25 sobre `.tesserae/site/search-index.json`, con un extracto de 200 caracteres tomado del `wiki/<kind>/<slug>.md` coincidente. Pasa `--kind papers` (o `concepts`, `repos`, etc.) para acotar, `--top-k N` para ampliar y `--json` para salida estructurada. Añade `--llm` (o define `TESSERAE_QUERY_LLM=1`) para pedir a Claude una respuesta sintetizada con citas `[node_id]`; `--interactive` abre un REPL readline — línea en blanco o EOF sale. `TESSERAE_QUERY_DRY_RUN=1` ejercita el prompt sin llamar a la API.

## 7. Compilar context listo para agentes bajo demanda

Lo destacado de v0.5.0 es el On-Demand Context Compiler: pide al grafo compilado un único documento de context citado, acotado a una consulta y dimensionado para caber en la ventana de un agente.

```bash
tesserae context "How does session import work?"
```

Siembra Personalized PageRank desde los nodos que coinciden con tu consulta (usa `--seeds <node_id>` para sembrar explícitamente), expande la vecindad (`--depth`, por defecto 2) y ensambla un documento citado limitado por un `--budget` de caracteres (por defecto 32000; pasa `<= 0` para sin límite). Añade `--synthesize` para un resumen escrito por LLM encima (requiere un LLM backend) y `-o/--output <file>` para escribir el documento en disco en lugar de stdout.

El mismo compiler se expone a los agentes por MCP como la herramienta `compile_context`, así que un agente de codificación puede extraer justo el context del proyecto necesario y acotado por budget a mitad de conversación, sin un export manual.

## 8. Exportar archivos de agent harness

```bash
tesserae export harness
```

Targets soportados:

- Claude Code
- Codex
- Gemini
- Kiro
- Cursor
- OpenCode

Ejemplo de subconjunto:

```bash
tesserae export harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. Exportar un vault de Obsidian

```bash
tesserae vault export
```

O escribir en un vault existente:

```bash
tesserae vault export --vault "$OBSIDIAN_VAULT_PATH"
```

El vault incluye markdown projections, `.obsidian` defaults, coloreado del grafo, `raw/assets/` y un Dataview dashboard. Usa `tesserae vault sync` para reconciliar un vault existente con el último compile (añade `--prune` para descartar notas huérfanas).

## 10. Configurar MCP

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

Pega la salida bajo `mcp_servers` en `~/.hermes/config.yaml`, luego reinicia Hermes/gateway.

## 11. Export / sync de Graphiti

Export de episodios sin dependencias:

```bash
tesserae export graphiti
```

Smoke de sync en dry-run sin Graphiti instalado:

```bash
tesserae export graphiti --sync --dry-run
```

La sync en vivo requiere `graphiti_core` y un Neo4j backend alcanzable:

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. Desplegar en GitHub Pages

Empuja el site compilado en `.tesserae/site/` a la rama `gh-pages` del git origin del proyecto:

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` ejecuta `compile` primero para que el site esté fresco. `--enable-pages` activa Pages vía la `gh` CLI (idempotente; se omite con una pista si falta `gh`). Usa `--dry-run` para hacer stage y commit sin push, `--branch` / `--remote` para sobrescribir los valores por defecto y `--force` para permitir el deploy con un árbol de trabajo sucio.

El sitio queda accesible en `https://<owner>.github.io/<repo>/`.

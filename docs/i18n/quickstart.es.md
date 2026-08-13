# Inicio rápido

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
Esta página muestra el camino más corto desde un directorio de proyecto existente hasta un Tesserae navegable.

## Resumen de comandos

La CLI está agrupada: un puñado de verbos cotidianos en el nivel superior, más grupos
(`sessions`, `vault`, `export`, `code`, `config`, `projects`, `agents`, `domains`, `integrations`,
`lab`) para el resto. Ejecuta `tesserae --help` para ver el árbol completo:

```text
tesserae 0.31.0 — a context engine

usage: tesserae <command> [options]

EVERYDAY
  init          Set up .tesserae (wizard by default; --yes non-interactive)
  compile       Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  ingest        Ingest a document file or URL into the knowledge base
  context       Compile agent-ready context for a query
  ask           LLM answer over the knowledge graph (planned retrieval)
  serve         Browse the compiled site (auto-builds if missing)
  status        Node/edge counts, last compile, vault state

AUTOMATION
  engine        Refresh daemon: watch sessions/sources, coalesced recompiles, idle 'sleep' consolidation
  refresh       One-shot: import sessions + compile + sync vault
  research      Autonomous research mode: investigate a query
  distill       Per-agent L1 expertise artifacts (opt-in: TESSERAE_AGENT_DISTILL)

ANALYSIS
  query         raw retrieval: BM25/semantic + explicit backends
  graph-map     Budgeted Descent navigation (the graph_map tool as a CLI verb; JSON out)
  verify-claim  Does the graph license this triple? Deterministic verdict, JSON out
  schema-drift  Propose ResearchNodeType sub-types from clustered nodes (proposals only; promotion is a human edit)
  lint          Graph lint report (--fix-trivial, --severity, --json)
  doctor        Health checks: init/graph/registry/staleness/locks (--fix = safe repairs only)
  summary       Daily/weekly activity digest (sessions, findings, commits, PRs, docs)
  decisions     Decisions across projects + time (human AskUserQuestion + agent)

GROUPS
  sessions      import | discover | list — agent session history
  vault         sync | sync-all | set-root | export | prune — Obsidian projection
  export        harness | graphiti | site — artifact exports
  code          ingest | sync — CodeGraph ⇄ project graph (hook-invoked)
  setup         Machine-wide setup: LLM defaults + optional deps (interactive by default)
  config        llm | deps | show | status | clip-token — LLM backend defaults + resolved view & liveness ping
  projects      register | list | unregister | mcp-config — registry
  agents        init | list | tree | show | drill | set-parent | rename — role-grade agent org registry
  domains       status — chartered domain tree (divisions/departments/teams)
  sources       add | list | remove — manage compile source dirs (local & global)
  federation    status | explain — inspect cross-project federation
  integrations  refresh raganything
  extract       Low-level: extract a typed graph from markdown paths

LAB
  lab           evolve — experimental LLM ops

Run `tesserae <command> --help` for command details.
```

Ejecuta `tesserae <command> --help` (p. ej. `tesserae compile --help`) para ver los flags de
cualquier comando individual.

## 1. Ejecuta el asistente de configuración

Desde el proyecto que quieres indexar:

```bash
cd /path/to/my-project
tesserae init
```

`tesserae init` es el único paso de onboarding. El asistente detecta fuentes comunes como `README.md`, `docs`, `src`, `lib`, `app`, `packages` y `data`, sondea qué CLIs de LLM están instaladas **y con sesión iniciada**, te deja elegir el proveedor LLM, y escribe `.tesserae/config.json`. El backend de memoria opcional RAG-Anything está **desactivado por defecto**; habilítalo más tarde en `memory_backends` en la config, y consúltalo explícitamente con `tesserae query --backend raganything`.

Para una configuración no interactiva (CI, scripts), pasa `--yes` para aceptar los valores
detectados sin preguntar (todas las integraciones opcionales OFF):

```bash
tesserae init --yes
```

### Configuración del proveedor LLM

La elección de proveedor del asistente (o los flags equivalentes) persiste estas claves de config:

| Clave de config | Flag | Qué es |
|---|---|---|
| `llm_provider` | `--llm-provider {claude,codex,anthropic,custom}` | Backend para el cliente LLM: `claude`/`codex` usan la CLI con sesión iniciada vía OAuth; `anthropic` usa la API directamente; `custom` apunta a cualquier endpoint compatible con claude. |
| `llm_model` | `--llm-model` | Modelo para el cliente LLM de síntesis/insights. |
| `llm_base_url` | `--llm-base-url` | URL base del endpoint para `anthropic`/`custom`. |
| `llm_api_key` | `--llm-api-key` | Clave de API para `anthropic`/`custom`. |

> **Advertencia de texto plano.** `llm_api_key` se guarda en **texto plano** en
> `.tesserae/config.json`. Prefiere las variables de entorno en su lugar:
> `ANTHROPIC_API_KEY` (clave), `ANTHROPIC_BASE_URL` (endpoint) y
> `TESSERAE_LLM_MODEL` (modelo). El orden de resolución es env → config del proyecto →
> config a nivel de máquina (`~/.tesserae/config.json`, escrita por `tesserae setup`)
> → valor por defecto integrado.

Volver a ejecutar `init` sobre un proyecto existente **fusiona** — tus `sources`
y `memory_backends` configurados se preservan, no se machacan.

Ejemplos de configuraciones de proveedor no interactivas:

```bash
tesserae init --yes --llm-provider codex
tesserae init --yes --llm-provider custom \
  --llm-base-url https://llm.internal.example/v1 \
  --llm-model my-model            # key via ANTHROPIC_API_KEY
```

> **Sáltate el asistente.** `tesserae init --bare` escribe un `.tesserae/config.json` mínimo
> sin detección de fuentes ni sondeo de backends — práctico cuando quieres editar a mano
> la config antes de la primera compilación.

## 2. Compila el grafo y las proyecciones

```bash
tesserae compile
```

`compile` escribe los artefactos durables:

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
```

Usa `--changed-only` después de la primera ejecución para saltarte los archivos markdown sin cambios preservando el grafo previo cuando ningún archivo cambió.

Para ingerir rutas extra ad-hoc sin tocar las fuentes configuradas, pásalas
posicionalmente: `tesserae compile path/to/extra.md docs/`.

### Los knobs de integración ahora viven en la config

`tesserae compile` está deliberadamente limitado a los flags cotidianos (rutas posicionales
más `--project`, `--changed-only`, `--limit`, `--refresh-integrations`,
`--sessions`/`--no-sessions`, y los tres flags de LLM). Cada antiguo flag de compile
restante se movió a un bloque `compile_options` en `.tesserae/config.json`; el antiguo
valor por defecto de argparse sigue siendo el fallback. Establece una clave allí para cambiar el comportamiento:

| Clave de `compile_options` | Flag antiguo | Por defecto | Qué hace |
|---|---|---|---|
| `source_kind` | `--source-kind` | (ninguno) | Anula el source kind configurado. |
| `trends` | `--trends` | `false` | Añade nodos Trend a nivel de corpus. |
| `min_trend_sources` | `--min-trend-sources` | `2` | Mínimo de fuentes necesarias para un nodo Trend. |
| `exclude_data` | `--exclude-data` | `false` | Se salta el auto-include implícito de `project_root/data`. |
| `no_vault_pull` | `--no-vault-pull` | `false` | No traer de vuelta las ediciones existentes del vault antes de compilar. |
| `use_extraction_feedback` | `--use-extraction-feedback` | `false` | Reinyecta resultados de extracción previos en la ejecución. |
| `sessions_llm` | `--sessions-llm` | (auto) | Modo de extracción de sesiones con LLM (`auto`/`true`/`false`). |
| `sessions_model` | `--sessions-model` | (ninguno) | Anula el modelo LLM usado para la extracción de sesiones. |

> **Cognee fue eliminado en 0.19.** El backend de cognee fue degradado en 0.18 y
> nunca alimentó el grafo. Las configs que aún lleven una sección
> `memory_backends.cognee` (u opciones de compile `cognee_*`) siguen cargando —
> la sección se ignora con una nota de una línea.

> **Pipeline de un solo golpe.** `tesserae refresh` ejecuta todo el bucle en el propio proceso — importa las sesiones de agente nuevas, compila y sincroniza el vault en un solo comando. Pasa `--changed-only` para la compilación incremental opt-in.

## 3. Construye y sirve el frontend estático

`serve` auto-construye el sitio si falta, así que un solo comando te da un
Tesserae navegable. **Un `serve` a secas sirve cada proyecto registrado** bajo un
servidor — una landing de proyectos en `/`, cada proyecto en `/<alias>/`, y un
selector de Projects en la cabecera para saltar entre ellos. El **widget de ask** en la página **funciona en vivo
en cualquiera de los dos modos**, enrutado al proyecto de la página en la que estás:

```bash
tesserae serve --port 8765                 # all registered projects
tesserae serve --project . --port 8765     # just this one
```

Abre:

```text
http://127.0.0.1:8765/
```

Para construir el sitio explícitamente (p. ej. para desplegar sin servir) usa `export site`;
pasa `--no-build` a `serve` cuando quieras navegar un sitio construido previamente
sin reconstruirlo:

```bash
tesserae export site
tesserae serve --no-build --port 8765
```

<!-- BEGIN: subagent-r-watch -->
### Auto-reconstrucción al guardar

Empareja el servidor de desarrollo con el watcher integrado para que las ediciones bajo `data/` y `docs/` disparen una recompilación incremental:

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae export site --watch
```

`export site --watch` sondea cada 2 s, aplica debounce de 1 s, y ejecuta `compile --changed-only`. Usa `--once` para reconstrucciones estilo cron (snapshots vs `.tesserae/.watch-cache.json`), `--paths <dir>` para añadir directorios de vigilancia personalizados, y `--interval` / `--debounce` para ajustar la cadencia.
<!-- END: subagent-r-watch -->

### Ejecuta el daemon de refresco

Para un engine siempre encendido que mantiene la base de conocimiento fresca por su cuenta — vigilando tus fuentes, coalesciendo ráfagas de ediciones y auto-recompilando — arranca el daemon supervisado:

```bash
tesserae engine
```

`engine` es el supervisor de larga vida: sondea cada 2 s y espera una ventana de calma de 1 s antes de cada reconstrucción. Ajusta la cadencia con `--interval` y `--debounce`, apúntalo a otro proyecto con `--project`, o pasa `--once` para ejecutar un único ciclo de drenaje determinista y salir (útil para cron o CI). Es la contraparte manos-libres de `export site --watch`: déjalo corriendo y el grafo, el vault y el sitio se mantienen al día mientras tú y tus agentes trabajáis.

Para un recorrido anotado de cada ruta visible — home, sources, concepts, entities, papers, repos, topics, syntheses, questions, timeline, graph, más los AI siblings — ver [`docs/frontend-redesign.md`](frontend-redesign.es.md).

El frontend es ligero en dependencias y escribe:

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. Importa el historial local de sesiones de agente

La importación del historial de sesiones es explícita: la compilación/build normal lee las sesiones ya normalizadas pero no escanea por su cuenta los almacenes privados de transcripts de Claude Code o Codex.

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

Las sesiones importadas aparecen en la sección global Sessions, la búsqueda del sitio y las tarjetas Browse de la home. Las páginas de detalle de sesión renderizan los turnos usuario/asistente como markdown legible, adjuntan los bloques de tool-use bajo el turno de asistente precedente, y exponen un rail de turnos a la izquierda para la navegación `#turn-N`. Ver [`docs/session-history.md`](session-history.es.md) para notas de privacidad, formatos de importación y el mapa tipográfico actual de los transcripts.

## 5. Lintea la wiki

```bash
tesserae lint
```

Recorre el grafo compilado + wiki + sitio y marca papers huérfanos, citas obsoletas, deriva entre grafo y wiki/, inputs fantasma de síntesis, y más. Escribe `.tesserae/lint-report.md` y `.tesserae/lint-report.json`. Pasa `--fix-trivial` para aplicar auto-correcciones seguras (aristas `implemented_in` ausentes, poda de inputs fantasma) y `--severity error` para que el código de salida solo falle con errores.

Para la salud del workspace más allá del propio grafo — consistencia del registro, staleness, locks, login del LLM, higiene — ejecuta `tesserae doctor` (`--fix` aplica solo las reparaciones seguras). Ver [`docs/doctor.md`](doctor.es.md).

## 6. Pregunta y consulta la wiki

```bash
# LLM-planned, cited answer over the compiled graph (the default):
tesserae ask "What is Gaussian Splatting?"

# Raw retrieval — ranked search hits, no LLM:
tesserae query "What is Gaussian Splatting?"
```

`ask` es la superficie de respuestas: el modelo planifica la recuperación sobre el grafo compilado y luego sintetiza una respuesta con citas. Funciona con una CLI `claude`/`codex` con sesión iniciada (OAuth) o con `ANTHROPIC_API_KEY`; pasa `--no-llm` para obtener solo hits de búsqueda rankeados (este apagado forzoso gana a `TESSERAE_QUERY_LLM=1`). `TESSERAE_QUERY_DRY_RUN=1` ejercita el prompt sin llamada a la API.

`query` es la superficie de recuperación: búsqueda BM25/semántica sobre `.tesserae/site/search-index.json`, con un extracto de 200 caracteres tomado del `wiki/<kind>/<slug>.md` coincidente. Pasa `--kind papers` (o `concepts`, `repos`, etc.) para acotar, `--top-k N` para ampliar, y `--json` para salida estructurada; `--interactive` abre un REPL readline — línea en blanco o EOF sale. El backend de memoria explícito también vive aquí: `--backend raganything` cortocircuita a ese backend y expone sus errores. No hay síntesis LLM en `query` — eso es `ask`.

## 7. Compila contexto listo para agentes bajo demanda

El titular de v0.5.0 es el On-Demand Context Compiler: pide al grafo compilado un único documento de contexto con citas acotado a una consulta, dimensionado para caber en la ventana de un agente.

```bash
tesserae context "How does session import work?"
```

Siembra Personalized PageRank desde los nodos que coinciden con tu consulta (usa `--seeds <node_id>` para sembrar explícitamente), expande el vecindario (`--depth`, por defecto 2), y ensambla un doc con citas limitado a un `--budget` de caracteres (por defecto 32000; pasa `<= 0` para sin tope). Añade `--llm` para un resumen escrito por LLM encima (requiere un backend LLM) y `-o/--output <file>` para escribir el doc a disco en lugar de stdout.

El mismo compilador se expone a los agentes por MCP como la herramienta `compile_context`, de modo que un agente de código pueda extraer contexto de proyecto justo-suficiente y acotado por presupuesto a mitad de conversación sin un export manual.

## 8. Exporta archivos de agent harness

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

## 9. Exporta un vault de Obsidian

```bash
tesserae vault export
```

O escribe en un vault existente:

```bash
tesserae vault export --output "$OBSIDIAN_VAULT_PATH"
```

El vault incluye proyecciones markdown, valores por defecto de `.obsidian`, coloreado del grafo, `raw/assets/`, y un dashboard de Dataview. Usa `tesserae vault sync` para reconciliar un vault existente con la última compilación (añade `--prune` para eliminar notas huérfanas).

## 10. Configura MCP

```bash
tesserae projects mcp-config --server-name my_project_wiki
```

Pega la salida bajo `mcp_servers` en `~/.hermes/config.yaml`, y luego reinicia Hermes/gateway.

## 11. Export / sync de Graphiti

Export de episodios sin dependencias:

```bash
tesserae export graphiti
```

Prueba de humo de sync en dry-run sin Graphiti instalado:

```bash
tesserae export graphiti --sync --dry-run
```

La sincronización en vivo requiere `graphiti_core` y un backend Neo4j alcanzable:

```bash
tesserae export graphiti --sync \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. Despliega a GitHub Pages

Empuja el sitio compilado en `.tesserae/site/` a la rama `gh-pages` del origin git del proyecto:

```bash
tesserae export site --deploy --build --enable-pages
```

`--build` ejecuta `compile` primero para que el sitio esté fresco. `--enable-pages` activa Pages vía la CLI `gh` (idempotente; se salta con una pista si falta `gh`). Usa `--dry-run` para preparar y commitear sin empujar, `--branch` / `--remote` para anular los valores por defecto, y `--force` para permitir desplegar con un árbol de trabajo sucio.

El sitio queda accesible en `https://<owner>.github.io/<repo>/`.

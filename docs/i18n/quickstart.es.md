# Inicio rápido

<!-- translations:start -->
<p align="center"><a href="../quickstart.md">English</a> · <a href="quickstart.ko.md">한국어</a> · <a href="quickstart.zh.md">中文</a> · <a href="quickstart.ja.md">日本語</a> · <a href="quickstart.ru.md">Русский</a> · <a href="quickstart.es.md">Español</a> · <a href="quickstart.fr.md">Français</a> · <a href="quickstart.de.md">Deutsch</a></p>
<!-- translations:end -->
Esta página muestra el camino más corto desde un directorio de proyecto existente hasta un Tesserae navegable.

## 1. Ejecuta el asistente de configuración

Desde el proyecto que quieres indexar:

```bash
cd /path/to/my-project
tesserae project setup
```

El asistente detecta fuentes comunes como `README.md`, `docs`, `src`, `lib`, `app`, `packages` y `data`, y luego escribe `.tesserae/config.json`. También configura el backend Cognee predeterminado para que `project ask` pueda probar Cognee y hacer fallback a la búsqueda del wiki compilado.

Para una configuración totalmente automática con Understand Anything y Cognee runtime memory activados:

```bash
tesserae project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

Qué hace:

| Flag | Effect |
|---|---|
| `--with-understand-anything` | Añade la UA graph projection como source. |
| `--install-understand-anything` | Instala/actualiza las UA companion skills. |
| `--understand-anything-platform codex` | Usa Codex para ejecutar el managed UA refresh wrapper de Tesserae. |
| `--run-cognee` | Ejecuta best-effort Cognee runtime cognify durante compile. |
| `--install-cognee` | Instala Cognee con el Python actual si falta. |

Los usuarios no necesitan conocer la UA install path ni escribir `/understand`; `project compile` ejecuta `project refresh-understand-anything` cuando el UA graph falta o está obsoleto.

## 2. Compila el grafo y las projections

```bash
tesserae project compile
```

`project compile` escribe los durable artifacts:

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

Usa `--changed-only` después de la primera ejecución para omitir archivos markdown sin cambios y preservar el graph anterior cuando no haya cambios. Si Understand Anything está activado, compile primero refresca/materializa `.tesserae/external/understand-anything.md`; si Cognee runtime está activado, también actualiza Cognee en modo best-effort después de escribir `.tesserae/cognee_bundle/`.

> **Pipeline de un solo paso.** `tesserae project refresh` ejecuta todo el bucle in-process: importa cualquier nueva agent session, compila y sincroniza el vault en un único comando. Pasa `--changed-only` para el compile incremental opcional y `--skip-sessions` para omitir el escaneo más lento de descubrimiento de harness-sessions.

## 3. Construye y sirve el frontend estático

```bash
tesserae project build-site
tesserae project serve --port 8765
```

Abre:

```text
http://127.0.0.1:8765/
```

<!-- BEGIN: subagent-r-watch -->
### Auto-rebuild al guardar

Combina el dev server con un polling watcher para que las ediciones bajo `data/` y `docs/` disparen un recompile incremental:

```bash
# terminal 1
python3 -m http.server 56821 --directory .tesserae/site

# terminal 2
tesserae project watch
```

`project watch` hace polling cada 2 s, debounce de 1 s y ejecuta `compile --changed-only`. Usa `--once` para rebuilds tipo cron (snapshots vs `.tesserae/.watch-cache.json`), `--paths <dir>` para añadir custom watch dirs y `--interval` / `--debounce` para ajustar la cadence.
<!-- END: subagent-r-watch -->

### Ejecuta el daemon de refresh

Si quieres un motor siempre activo que vigile tus fuentes por su cuenta, agrupe ráfagas de ediciones y recompile automáticamente para mantener la base de conocimiento al día, arranca el daemon supervisado:

```bash
tesserae project engine
```

`project engine` (con alias `project daemon`) es el supervisor de larga duración: hace polling cada 2 s y espera una ventana de silencio de 1 s antes de cada rebuild. Ajusta la cadencia con `--interval` y `--debounce`, apunta a otro proyecto con `--project`, o pasa `--once` para ejecutar un único ciclo de drain determinista y salir (útil para cron o CI). Es la contraparte desatendida de `project watch`: déjalo corriendo y el graph, el vault y el site se mantendrán al día mientras tú y tus agents trabajáis.

Para un tour anotado de cada route visible — home, sources, concepts, entities, papers, repos, topics, syntheses, questions, timeline, graph, además de los AI siblings — consulta [`docs/frontend-redesign.md`](frontend-redesign.es.md).

El frontend tiene pocas dependencias y escribe:

```text
.tesserae/site/index.html
.tesserae/site/sessions/index.html
.tesserae/site/graph.json
.tesserae/site/search-index.json
.tesserae/site/llms.txt
```

## 4. Importa el historial de sesiones de agentes locales

La importación de historial de sesiones es explícita: compile/build normal lee sesiones ya normalizadas, pero no escanea por sí solo almacenes privados de transcript de Claude Code o Codex.

```bash
# Preview matching Claude Code/Codex sessions for this project:
tesserae project sessions discover

# Normalize and store them under .tesserae/harness_sessions/:
tesserae project sessions discover --import

# Confirm the imported set:
tesserae project sessions list

# Rebuild so sessions/index.html and session detail pages are emitted:
tesserae project build-site
```

Las sesiones importadas aparecen en la sección global Sessions, la búsqueda del sitio y las tarjetas Browse de inicio. Las páginas de detalle renderizan turnos user/assistant como markdown legible, adjuntan tool-use blocks bajo el turno assistant anterior y exponen un turn rail izquierdo para navegación `#turn-N`. Consulta [`docs/session-history.md`](session-history.es.md) para notas de privacidad, formatos de importación y el mapa actual de transcript typography.

## 5. Lint del wiki

```bash
tesserae project lint
```

Recorre el compiled graph + wiki + site y marca orphan papers, stale citations, drift entre graph y wiki/, ghost synthesis inputs y más. Escribe `.tesserae/lint-report.md` y `.tesserae/lint-report.json`. Pasa `--fix-trivial` para aplicar auto-fixes seguros (missing `implemented_in` edges, ghost-input pruning) y `--severity error` para fallar el exit code solo con errors.

## 6. Consulta el wiki

```bash
tesserae project query "What is Gaussian Splatting?"
```

Por defecto solo búsqueda: BM25 sobre `.tesserae/site/search-index.json`, con un excerpt de 200 caracteres tomado del `wiki/<kind>/<slug>.md` coincidente. Pasa `--kind papers` (o `concepts`, `repos`, etc.) para acotar, `--top-k N` para ampliar y `--json` para salida estructurada. Añade `--llm` (o define `TESSERAE_QUERY_LLM=1`) para pedir a Claude una respuesta sintetizada con citations `[node_id]`; `--interactive` abre un REPL readline — línea en blanco o EOF sale. `TESSERAE_QUERY_DRY_RUN=1` ejercita el prompt sin llamada API.

## 7. Compila context para agents bajo demanda

La gran novedad de v0.5.0 es el On-Demand Context Compiler: pídele al graph compilado un único documento de context citado y acotado a una consulta, dimensionado para caber en la context window de un agent.

```bash
tesserae project context "¿Cómo funciona session import?"
```

Usa como seed de Personalized PageRank los nodos que coinciden con tu consulta (usa `--seeds <node_id>` para indicar el seed de forma explícita), expande la vecindad (`--depth`, por defecto 2) y ensambla un documento citado limitado por un `--budget` de caracteres (por defecto 32000; pasa `<= 0` para sin límite). Añade `--synthesize` para un resumen escrito por un LLM encima (requiere un backend LLM) y `-o/--output <file>` para escribir el documento en disco en lugar de stdout.

El mismo compiler se expone a los agents por MCP como la herramienta `compile_context`, de modo que un agent de programación puede obtener justo el context del proyecto que necesita, acotado por budget, en mitad de una conversación y sin un export manual.

## 8. Exporta archivos agent harness

```bash
tesserae project export-agent-harness
```

Targets soportados:

- Claude Code
- Codex
- Gemini
- Kiro
- Cursor
- OpenCode

Subset de ejemplo:

```bash
tesserae project export-agent-harness \
  --target claude-code \
  --target cursor \
  --target opencode
```

## 9. Exporta un vault de Obsidian

```bash
tesserae project export-obsidian
```

O escribe en un vault existente:

```bash
tesserae project export-obsidian --vault "$OBSIDIAN_VAULT_PATH"
```

El vault incluye markdown projections, defaults de `.obsidian`, graph coloring, `raw/assets/` y un dashboard Dataview.

## 10. Configura MCP

```bash
tesserae project mcp-config --server-name my_project_wiki
```

Pega la salida bajo `mcp_servers` en `~/.hermes/config.yaml`, luego reinicia Hermes/gateway.

## 11. Graphiti export / sync

Episode export sin dependencias:

```bash
tesserae project export-graphiti
```

Dry-run sync smoke sin Graphiti instalado:

```bash
tesserae project sync-graphiti --dry-run
```

Live sync requiere `graphiti_core` y un backend Neo4j alcanzable:

```bash
tesserae project sync-graphiti \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password '<password>'
```

## 12. Despliega en GitHub Pages

Haz push del compiled site en `.tesserae/site/` a la rama `gh-pages` del git origin del proyecto:

```bash
tesserae project deploy --build --enable-pages
```

`--build` ejecuta `project compile` primero para que el site esté fresco. `--enable-pages` activa Pages mediante la CLI `gh` (idempotente; se omite con una pista si falta `gh`). Usa `--dry-run` para stage y commit sin push, `--branch` / `--remote` para reemplazar defaults y `--force` para permitir desplegar con un working tree dirty.

El sitio queda accesible en `https://<owner>.github.io/<repo>/`.

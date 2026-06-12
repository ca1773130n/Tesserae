# Tesserae

<p align="center">
  <img src="docs/assets/tesserae-graph-view.png" alt="Vista del grafo de Tesserae: conceptos, artículos, repositorios, síntesis y entidades agrupadas alrededor de un nodo enfocado" width="100%" />
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./README.ko.md">한국어</a> ·
  <a href="./README.zh.md">中文</a> ·
  <a href="./README.ja.md">日本語</a> ·
  <a href="./README.ru.md">Русский</a> ·
  <a href="./README.fr.md">Français</a> ·
  <a href="./README.de.md">Deutsch</a>
</p>

> Un motor de contexto que mantiene una base de conocimiento auto-mejorable de tu proyecto y compila contexto listo para agentes bajo demanda.

<p align="center">
  <img src="docs/screencasts/showcase.gif" alt="Screencast en tres pasos: tesserae init -> compile -> ask, grabado sobre el corpus de demostración de 135 documentos" width="100%" />
</p>

<p align="center">
  <a href="https://ca1773130n.github.io/Tesserae">Demo en vivo</a> ·
  <a href="docs/">Documentación</a> ·
  <a href="docs/release-notes/">Notas de versión</a> ·
  <a href="docs/integrations/mcp.md">Configuración MCP</a> ·
  <a href="docs/integrations/obsidian.md">Exportar a Obsidian</a>
</p>

## Qué es

Apunta Tesserae a un directorio con Markdown, código fuente y, opcionalmente,
PDF/documentos Office/imágenes. Reconstruye un **grafo de conocimiento tipado**
del proyecto y lo mantiene actualizado, de modo que los agentes siempre tienen
contexto fundamentado y con citas.
Tres pilares:

1. **Monitoreo de sesiones** — tus conversaciones de Claude Code / Codex sobre el
   proyecto se convierten en nodos de primer nivel del grafo (decisiones, hallazgos,
   preguntas, TODO) en tiempo real.
2. **Ingesta autónoma** — un motor supervisado observa fuentes y sesiones,
   agrupa cambios, recompila, y un sidecar de auto-mejora refuerza hallazgos
   recurrentes y reemplaza los obsoletos.
3. **Contexto bajo demanda** — el compilador de contexto ensambla un documento de
   contexto personalizado y con citas para cualquier consulta o nodo semilla
   (PageRank Personalizado bajo un presupuesto de caracteres), listo para pegar
   en cualquier agente.

El grafo, el vault de Obsidian y el sitio estático son *proyecciones* de una
única base de conocimiento. Todo se ejecuta localmente; es un paso de compilación
más un motor en vivo, no un servicio alojado.

## Inicio rápido

Requiere **Python 3.10+**.

```bash
pip install tesserae          # añade [semantic] para embeddings reales
# o: pipx install tesserae   # instalación más segura para PATH
# o: npx @jokerized/tesserae # wrapper Node alrededor del mismo CLI

cd /path/to/my-project
tesserae init --yes           # asistente; --yes acepta valores detectados por defecto
tesserae compile              # construir el grafo de conocimiento
tesserae ask "Where is Mermaid rendering implemented?"

# Compilar un documento de contexto personalizado y con citas:
tesserae context "How does the parser handle arXiv IDs?" --budget 32000 -o context.md

tesserae serve --port 8765    # explorar el grafo y la wiki localmente
```

Las funciones basadas en LLM usan por defecto los CLI `codex` / `claude` vía OAuth —
**no se requieren claves API** para el flujo habitual. Consulta
[docs/quickstart.md](docs/quickstart.md) y
[docs/installation.md](docs/installation.md).

<details>
<summary><strong><code>tesserae: command not found</code> tras la instalación? ¿Problemas en Linux?</strong></summary>

La solución más fiable en cualquier plataforma es [`pipx`](https://pipx.pypa.io/):

```bash
# macOS: brew install pipx · Ubuntu/Debian: sudo apt install pipx
pipx ensurepath          # añade ~/.local/bin al PATH; abre un nuevo terminal después
pipx install tesserae
```

Problemas comunes en Ubuntu con `pip install tesserae`:

| Error | Causa | Solución |
|---|---|---|
| `error: externally-managed-environment` | PEP 668 — Python del sistema bloqueado | Usa `pipx` (arriba) o un venv |
| `command not found` tras `pip install --user …` | `~/.local/bin` no está en `PATH` | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
| `ModuleNotFoundError` en distros antiguas | `python3` del sistema < 3.10 | `sudo apt install python3.11 python3.11-venv`, luego instala con `python3.11 -m pip` |

</details>

<details>
<summary><strong>GIFs de demostración</strong> — cada paso del inicio rápido sobre el corpus de demo de 135 documentos incluido</summary>

<details>
<summary>1. Configuración — apuntar a un directorio de investigación, obtener un scaffold de wiki de proyecto</summary>
<br/>
<img src="docs/screencasts/setup.gif" alt="tesserae init --source ./research ejecutándose de forma no interactiva y escribiendo .tesserae/" width="100%" />
</details>

<details>
<summary>2. Compilar + construir sitio — determinista, sin llamadas a LLM</summary>
<br/>
<img src="docs/screencasts/compile.gif" alt="tesserae compile seguido de tesserae export site, emitiendo graph.json y el árbol del sitio estático" width="100%" />
</details>

<details>
<summary>3. Ask — consultar la wiki compilada desde el CLI</summary>
<br/>
<img src="docs/screencasts/ask.gif" alt="tesserae ask --backend wiki devuelve los 3 mejores resultados con puntuación, tipo y relaciones salientes" width="100%" />
</details>

Reconstruye cualquier GIF con `vhs docs/screencasts/<name>.tape`.

</details>

## Comandos del día a día

Ejecuta `tesserae --help` para la lista completa agrupada, `tesserae <cmd> --help` para las opciones.

| Comando | Qué hace |
|---|---|
| `tesserae init` | Asistente de configuración → `.tesserae/config.json`. `--yes` no interactivo, `--bare` mínimo. |
| `tesserae compile` | Reconstruir el grafo de conocimiento y todos los artefactos. `compile <paths>` ingiere archivos extra de forma puntual. |
| `tesserae ingest <file\|url>` | Incorporar un solo documento o página web a la base de conocimiento sin recompilar por completo (ruta incremental rápida). |
| `tesserae context "<query>"` | **Compilador de contexto bajo demanda**: documento de contexto con citas vía expansión PPR bajo `--budget`; `--synthesize` añade un resumen LLM. |
| `tesserae ask "<question>"` | Consultar la base de conocimiento compilada (`--scope all-registered` abarca todos los proyectos). |
| `tesserae engine` | Demonio de actualización supervisado para el proyecto actual: vigilar, debounce, recompilar. |
| `tesserae engine --all` | **Modo flota**: un proceso mantiene actualizados *todos* los proyectos registrados — recarga en caliente del registro, limitación con `--compile-slots`. |
| `tesserae refresh` | Pipeline de una sola vez: importar sesiones nuevas → compilar → sincronizar vault. |
| `tesserae sessions discover --import` | Encontrar e importar el historial de sesiones locales de Claude Code / Codex para este proyecto. |
| `tesserae export site` | Construir el sitio estático (`--deploy`, `--watch`). |
| `tesserae serve` | Servir el sitio localmente con el widget de consulta integrado (`/api/ask`). |
| `tesserae projects …` | Registro de múltiples proyectos: `register`, `list`, `activate`, `mcp-config`. |
| `tesserae integrations refresh …` | Volver a ejecutar herramientas complementarias (Understand-Anything, RAG-Anything). |

## Mantenerlo actualizado automáticamente

El motor es lo que hace que la base de conocimiento sea *auto-mejorable* en vez
de una compilación única:

```bash
# Un proyecto: vigilar fuentes + sesiones en vivo, recompilar al detectar cambios.
tesserae engine

# Todos los proyectos registrados, un proceso (v0.8.0):
tesserae engine --all --compile-slots 1
```

El modo flota reconcilia con `~/.tesserae/registry.json` cada 10 s —
registrar o eliminar un proyecto surte efecto sin reiniciar — y serializa
las compilaciones entre proyectos para que la extracción LLM concurrente nunca
agote los límites de la cuenta. La primera ejecución recorre el historial de
sesiones una sola vez (se indica en el log); los reinicios se retoman desde un
punto de partida persistido.

## Qué obtienes tras compilar

```text
.tesserae/
  graph.json              # nodos/aristas tipados (la base de conocimiento)
  sqlite.db               # almacén de grafo consultable
  markdown_projection/    # páginas wiki legibles por humanos
  obsidian_vault/         # listo para añadir a Obsidian
  site/                   # sitio estático (vista de grafo + wiki + búsqueda)
  harness_sessions/       # memoria de sesiones Claude/Codex importada
  agent_harness/          # configuración de contexto por agente (Claude/Codex/Gemini/...)
  cognee_bundle/          # JSONL listo para ingestar en Cognee
  config.json · manifest.json · report.md · …
```

## Servidor MCP

`tesserae projects mcp-config` imprime una entrada de servidor para Claude Code, Codex o
cualquier cliente MCP. Herramientas principales:

- **`compile_context`** — documento de contexto personalizado y con citas para una consulta o nodos semilla
  (determinista salvo que `synthesize=true`), respaldado por `graph_ppr`.
- **Grafo + wiki**: `search_nodes`, `node_context`, `graph_summary`,
  `wiki_page`, `raw_source`, `timeline`, `search_facts`, `lint_report`, `ask`.
- **Memoria de sesiones**: `list_sessions`, `find_session_findings`,
  `find_code_symbol_mentions`, `fresh_insights` (clasificado por decaimiento, deduplicado).
- **Registro**: `list_projects`, `register_project`, `activate_project`.

## Múltiples proyectos

Un registro en `~/.tesserae/registry.json` resuelve nombres de proyectos en todas partes —
CLI, MCP y el motor de flota:

```bash
tesserae projects register /path/to/my-project --name myproj
tesserae projects activate myproj
tesserae ask "..." --scope all-registered        # abarcar todos los proyectos
```

El Markdown de un proyecto puede enlazar en profundidad a un nodo de otro mediante
`wiki://<alias>/<kind>/<slug>`; al compilar, estos se convierten en nodos puente en
la vista del grafo. Consulta la [documentación](docs/) para más detalles.

## Integraciones (todas opcionales)

- **Plugin de Claude Code** — slash commands, hooks de sesión, skill y auto-registro MCP
  con un solo `/plugin install`.
  [docs/integrations/claude-code-plugin.md](docs/integrations/claude-code-plugin.md)
- **Grafo de sesiones** — conversaciones de Claude Code / Codex → nodos Insight / Decision /
  Question / TODO, enlazados a los documentos que tocaron. No se requiere clave API.
  [docs/integrations/sessions.md](docs/integrations/sessions.md)
- **Understand-Anything** — ingesta de grafo de conocimiento de código.
  [docs/integrations/understand-anything.md](docs/integrations/understand-anything.md)
- **RAG-Anything** — ingesta multimodal (PDF/Office/imágenes vía
  MinerU/Docling) y un backend de preguntas LightRAG.
  [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md)
- **Cognee** — backend de memoria grafo+vector; la compilación siempre escribe un
  bundle listo para Cognee; el cognify en tiempo de ejecución es de mejor esfuerzo.
- **Obsidian** — sincronización bidireccional del vault con overlay de ediciones del usuario.
  [docs/integrations/obsidian.md](docs/integrations/obsidian.md)

## Comparación

<details>
<summary>Matriz de características frente a Quartz, Logseq, Cognee, Foam</summary>

| Característica | Tesserae | Quartz | Logseq | Cognee | Foam |
|---|---|---|---|---|---|
| Salida en HTML estático | sí | sí | parcial (export) | no | parcial (publish) |
| Vista de grafo integrada | sí | sí | sí | sí (UI separada) | sí (VSCode) |
| Esquema de nodos tipado | sí (41 tipos) | no | parcial (etiquetas) | sí | no |
| Extracción de conceptos desde fuentes | sí (LLM) | no | no | sí | no |
| Ingesta multimodal (PDF/imagen) | sí (vía RAG-Anything) | no | parcial (embeds) | sí | no |
| Ingesta de grafo de código | sí | no | no | parcial | no |
| Servidor MCP | sí | no | no | sí | no |
| Compilador de contexto con citas bajo demanda | sí (PPR + presupuesto) | no | no | no | no |
| Monitoreo de sesiones en vivo → grafo | sí | no | no | no | no |
| Registro de múltiples proyectos | sí | no | sí (grafos) | parcial | no |
| Demonio de flota para múltiples proyectos | sí | no | no | no | no |
| Funciona sin clave API (OAuth) | sí | n/a | n/a | no | n/a |
| Compilación determinista byte a byte | sí | sí | n/a | no | n/a |
| Edición en vivo | no | parcial | sí | n/a | sí |
| Colaboración en tiempo real | no | no | sí (DB beta) | no | no |

</details>

Tesserae elige compilar desde las fuentes en vez de editar en vivo. Si quieres editar
notas en un UI, usa Logseq u Obsidian. Si quieres una herramienta de compilación *y un motor
en vivo* para tu grafo de conocimiento, este es el proyecto.

**Úsalo si** quieres un grafo de conocimiento durable e inspectable sobre las fuentes
pesadas en texto de un proyecto, un servidor MCP local anclado en tus propios archivos,
o bundles limpios para Cognee/Obsidian sin escribir código de pegamento.

**Omítelo si** solo necesitas búsqueda vectorial sobre un directorio pequeño, quieres
una wiki alojada con UI de edición, o esperas un agente «pregunta lo que sea» listo para
usar — Tesserae construye el sustrato; tú lo conectas al agente de tu elección.

## Autenticación y proveedores LLM

El flujo habitual no usa **ninguna clave API**:

- **Codex CLI** (por defecto) y **Claude Code CLI** vía OAuth, con
  rotación de múltiples cuentas.
- **Embeddings**: la recuperación híbrida nativa usa un canal semántico offline sin torch
  mediante `pip install "tesserae[semantic]"` (`model2vec`). Los backends de Cognee/RAG-Anything
  usan por defecto un proveedor determinista; cambia a Ollama o cualquier endpoint compatible con
  OpenAI para mejor recall.

`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` se usan si están presentes, nunca son obligatorias.

## Estado y limitaciones

Versión actual: ver [notas de versión](docs/release-notes/). Limitaciones conocidas:

- Las compilaciones iniciales sobre corpus grandes (miles de archivos) tardan minutos;
  el tiempo de compilación escala de forma aproximadamente lineal. La compilación incremental
  (`--changed-only`) existe pero es experimental y está desactivada por defecto.
- Sin el extra `semantic`, la recuperación híbrida degrada a un stub no semántico
  (con una advertencia visible).
- La visión de RAG-Anything (descripción de imágenes) aún no está conectada de extremo a extremo.
- El cognify en tiempo de ejecución de Cognee es de mejor esfuerzo: los proveedores faltantes
  se registran y omiten, nunca son fatales.
- El conjunto de herramientas MCP es estable; el esquema del grafo puede seguir ganando tipos de nodos.

## Estructura del proyecto

```text
tesserae/        # el paquete (CLI, compilador, motor, servidor MCP, adaptadores)
docs/            # documentación en inglés + docs/i18n/ para los otros siete idiomas
ontology/        # esquemas de nodos/aristas contra los que valida el compilador
prompts/         # prompts de extracción y síntesis
tests/           # suite pytest
evals/           # harnesses de evaluación de calidad del grafo
examples/        # corpus de demo usado por los screencasts
```

## Documentación localizada

[한국어](./README.ko.md) ·
[中文](./README.zh.md) ·
[日本語](./README.ja.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md) ·
[Français](./README.fr.md) ·
[Deutsch](./README.de.md)

La documentación extensa está disponible en `docs/i18n/`.

## Licencia

MIT. Ver [LICENSE](LICENSE).

# Acompañante multimodal RAG-Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ja.md">日本語</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.es.md">Español</a> · <a href="rag-anything.fr.md">Français</a> · <a href="rag-anything.de.md">Deutsch</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) es un framework de RAG multimodal (construido sobre LightRAG) que parsea PDFs, documentos de Office, imágenes y ecuaciones a través de MinerU/Docling/PaddleOCR. Tesserae lo integra tanto como pipeline de ingesta multimodal (proyección de grafo nativa al estilo UA) como el backend de memoria en runtime opcional.

## ¿Por qué usar ambos?

- Tesserae — memoria de agente de larga vida, compilación de wiki, proyección de grafo.
- RAG-Anything — ingesta multimodal + recuperación en runtime con LightRAG.

Los dos se complementan: RAG-Anything aporta la comprensión de PDF/Office/imágenes que los loaders de fuentes centrados en texto de Tesserae no proporcionan; Tesserae mantiene la memoria de larga vida y consultable que sobrevive entre sesiones.

## Flujo actual de baja fricción

La ruta recomendada es el asistente de configuración:

```bash
tesserae init
```

RAG-Anything es ahora un **prompt interactivo del asistente** en lugar de un conjunto de flags
de CLI. Cuando el asistente corre, responde a los prompts de integración:

- habilita RAG-Anything cuando se te pregunte;
- instálalo cuando se te pida (instala `raganything` + `docling`);
- elige el parser `mineru`;
- habilita la ejecución de refresco post-instalación cuando se te ofrezca.

Luego compila:

```bash
tesserae compile
```

Para automatización no interactiva (CI), ejecuta el asistente con los defaults (todas
las integraciones opcionales OFF), luego habilita RAG-Anything en `.tesserae/config.json`
— el asistente escribe la config de integración bajo las claves `external_tools` /
`memory_backends` (ver las claves que referencia este doc más abajo) — y ejecuta el
refresco gestionado:

```bash
tesserae init --yes
# enable raganything in .tesserae/config.json (external_tools key)
tesserae integrations refresh raganything --parser mineru
tesserae compile
```

El asistente de configuración instala `raganything` y `docling` juntos. MinerU sigue siendo opt-in: instálalo con `pip install 'mineru[core]'` solo si tienes PDFs o imágenes que ingerir.

Tesserae guarda un comando de refresco gestionado en lugar de pedir a los usuarios que inventen uno:

```bash
tesserae integrations refresh raganything --parser mineru
```

Durante la compilación, Tesserae:

1. comprueba si `.tesserae/external/raganything/manifest.json` existe y coincide con el commit actual de git (vía el `meta.json#gitCommitHash` guardado);
2. ejecuta el wrapper de refresco gestionado si falta/está obsoleto o se pasa `--refresh-external-tools`;
3. descubre las fuentes no-código (PDFs, docs de Office, imágenes, markdown) y las parsea vía el parser configurado;
4. escribe `manifest.json` + `meta.json`;
5. continúa la compilación de memoria normal.

Puedes forzar todos los comandos de refresco externos configurados antes de una compilación:

```bash
tesserae compile --refresh-integrations
```

## Equivalente manual

```bash
pip install 'raganything[all]'
python -m tesserae.raganything_refresh --project . --parser mineru
tesserae compile
```

## Compile-time vs runtime

Tesserae separa la integración limpiamente:

- **Parsing en tiempo de compilación** (`refresh-raganything` y `compile`): ejecuta los parsers directamente — lectura nativa para `.md/.txt/.rst`, `docling.DocumentConverter` para todo lo demás. El pipeline completo de RAG-Anything *no* se invoca aquí, así que no se necesitan claves de LLM/embedding/visión para que compile tenga éxito.
- **Consultas en runtime** (`project ask`): `raganything_query.py` instancia `RAGAnything` con las funciones LLM/embedding/visión configuradas del proyecto y ejecuta `aquery` contra el store de LightRAG. Esta ruta requiere claves de API.

La separación hace que `compile` sea rápido, determinista y sin claves; solo las operaciones en tiempo de recuperación cuestan tokens de LLM.

## Sincronización nativa del grafo

Tesserae importa el manifest parseado nativamente durante la compilación cuando la herramienta configurada usa `sync_mode: native_graph`.

El adaptador nativo lee `.tesserae/external/raganything/manifest.json`, proyecta cada documento parseado en un nodo `SourceFile` con metadatos de bloques multimodales, y escribe un manifest de sync:

```text
.tesserae/external/raganything-sync.json
```

Mapeo actual:

| RAG-Anything | Dirección Tesserae |
|---|---|
| `documents[*]` | nodo `SourceFile`, `metadata.parser="raganything"` |
| `content_list[type=text]` | plegado en `SourceFile.description`; conceptos vía el extractor existente |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` y `metadata.equations[]` (LaTeX preservado) |

La procedencia se preserva en cada nodo:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".tesserae/external/raganything/manifest.json"}
```

Nota: la vista de grafo interactiva oculta los nodos del grupo `sources` por defecto para enfocarse en conceptos y entidades — los SourceDocuments proyectados de raganything se quedan en `graph.json` (MCP, la búsqueda y las vistas wiki por página siguen viéndolos), simplemente no inundan el lienzo. Establece `graph_view.show_sources = true` en `.tesserae/config.json` para restaurar la vista densa.

## Backend de memoria en runtime

`memory_backends.raganything` (default producido por `default_raganything_backend_config`) es el único backend de memoria opcional. RAG-Anything es opt-in (por defecto `enabled: false`); el flag de setup `--with-raganything` lo activa.

### Proveedor LLM (sin clave de API)

El backend en runtime de RAG-Anything necesita un LLM para responder consultas. Tesserae usa por defecto sus integraciones CLI basadas en OAuth existentes — sin clave de API requerida:

| Proveedor | Cómo se autentica | Flag de setup |
|---|---|---|
| `codex` (por defecto) | OAuth de la CLI `codex` (iniciaste sesión una vez con `codex login`) | `--raganything-llm-provider codex` |
| `claude` | CLI `claude -p`; respeta `CLAUDE_CONFIG_DIR` para setups multi-cuenta | `--raganything-llm-provider claude --raganything-claude-config-dir ~/.claude-personal2` |

Para setups de Claude multi-cuenta (p. ej., `~/.claude-personal1`, `~/.claude-personal2`), pasa `--raganything-claude-config-dir <path>` en el setup. El backend en runtime exportará `CLAUDE_CONFIG_DIR=<path>` antes de cada invocación para que se use la auth de la cuenta elegida sin tocar tu `~/.claude` por defecto.

### Embeddings

| Proveedor | Cuándo usarlo |
|---|---|
| `deterministic` (por defecto) | Sin deps externas. Basado en hash; baja calidad semántica pero suficiente para que LightRAG construya un índice. Buena línea base para demostrar que la integración funciona. |
| `ollama` | Ollama local corriendo con un modelo de embeddings (p. ej., `nomic-embed-text`). Pasa `--raganything-embedding ollama`; el backend usa por defecto `http://localhost:11434`. |

El soporte directo de embeddings de OpenAI no está cableado a través de estos flags en v1 — los usuarios con claves de OpenAI pueden establecer `OPENAI_API_KEY` y anular `memory_backends.raganything.embedding.provider` directamente en `.tesserae/config.json` (RAGAnything recogerá la variable de entorno vía los defaults de LightRAG).

### Invocar desde la CLI

```bash
# `ask` never enters memory backends: it plans retrieval over the compiled
# graph and synthesizes a cited LLM answer (--no-llm = ranked hits only).
tesserae ask "What does the integration spec say about parser routing?"

# Explicit backends live on `query` — raw retrieval, no LLM synthesis.
tesserae query "..." --backend raganything
tesserae query "..." --backend wiki
```

`tesserae query --backend raganything` llama a `tesserae.raganything_query.query` directamente. Un `working_dir` relativo en `memory_backends.raganything` se resuelve contra la raíz del proyecto antes de la llamada.

### `ask` de nivel superior (usa el registro multi-proyecto)

Para flujos donde quieres preguntar a varios proyectos Tesserae registrados sin hacer `cd` a cada uno, el comando de nivel superior `tesserae ask` resuelve el proyecto vía el registro persistente compartido con el servidor MCP:

```bash
# One-time: register your projects (saved to ~/.tesserae/registry.json).
tesserae projects register ~/Developer/Projects/Tesserae --name tesserae
tesserae projects register ~/Developer/Projects/Other --name other

# List registered projects.
tesserae projects list

# Ask from inside a project (the smart router picks the target otherwise).
tesserae ask "How does the parser routing work?"

# Ask a specific registered project by name.
tesserae ask "What is the architecture?" --name other

# Pass a direct path, or hit a memory backend explicitly via `query`.
tesserae ask "..." --project /tmp/somewhere
tesserae query "..." --backend raganything --json
```

La lógica de dispatch — `--project > --name > router` — está implementada en el handler de ask de nivel superior y el formateo de la respuesta se comparte con la herramienta MCP `ask` a través de `tesserae.query.ask_project` (los backends de memoria son alcanzables solo a través de `tesserae query --backend …`). El registro está respaldado por archivo (`~/.tesserae/registry.json` por defecto), así que persiste entre sesiones y se mantiene en sync con la lista de proyectos del servidor MCP.

#### Consultar varios vaults a la vez (`--scope all-registered`)

Bet B2 — cuando tienes varios proyectos registrados (vault de investigación, vault de trabajo, vault de side-project) y quieres hacer la misma pregunta contra todos, usa `--scope all-registered`:

```bash
# Fan out across every registered project. The aggregated envelope is
# {"scope": "all-registered", "question": "...", "by_project": {"<alias>": <envelope>}}.
tesserae ask "What did I write about RLHF?" --scope all-registered --json

# Restrict to a hand-picked subset of aliases.
tesserae ask "..." --scope all-registered --scope-aliases research side-projects
```

El handler itera los proyectos registrados en orden alfabético, llama a `ask_project` contra cada uno, y agrega los envelopes por proyecto. Que un solo proyecto falle — config ausente, RAG-Anything no habilitado — se captura como `{"error": "..."}` en el slot de ese alias y nunca aborta el resto del fan-out. El mismo argumento `scope` es aceptado por la herramienta MCP `ask`, así que los agentes de código dirigidos por MCP obtienen el mismo fan-out sin plomería extra.

### Registro multi-proyecto (`tesserae projects`)

| Comando | Propósito |
| --- | --- |
| `tesserae projects list [--json]` | Muestra los proyectos registrados (todos son iguales — no hay uno "activo"). |
| `tesserae projects register <path> [--name <alias>]` | Añade un proyecto al registro; el alias es por defecto el nombre saneado del directorio. |
| `tesserae projects unregister <name>` | Retira una entrada del registro. |

Estos comandos operan directamente sobre `tesserae.mcp_server.ProjectRegistry` — sin roundtrip por MCP — así que pueden scriptarse sin ejecutar el servidor MCP.

### Invocar desde MCP

El servidor MCP stdio expone una herramienta `ask` con el mismo selector de backend:

```json
{
  "name": "ask",
  "arguments": {
    "question": "What does the integration spec say about parser routing?",
    "backend": "auto",
    "project": "tesserae"
  }
}
```

El orden de dispatch (`raganything` → búsqueda de la wiki compilada) y la resolución de `working_dir` reflejan exactamente el handler de la CLI, así que los agentes de código y los operadores humanos convergen en las mismas respuestas.

## Prerrequisitos de sistema

- **Python 3.10+** es requerido para RAG-Anything (el paquete upstream `raganything` ≥1.3.0 depende transitivamente de `mineru[core]`, que es Python 3.10+). En Pythons más antiguos Tesserae desactiva la integración con una advertencia clara en lugar de instalar silenciosamente un placeholder roto.
- **LibreOffice** para el parsing de `.doc/.docx/.ppt/.pptx/.xls/.xlsx` — instálalo por separado vía el gestor de paquetes de tu plataforma. RAG-Anything se salta los documentos de Office con una advertencia cuando falta LibreOffice.
- **Los pesos del modelo de MinerU** se descargan en el primer parseo y se cachean (~GBs). Las ejecuciones posteriores reutilizan la caché.
- **Claves LLM/embedding/visión compatibles con OpenAI** para el backend de memoria en runtime (`OPENAI_API_KEY`, `OPENAI_BASE_URL`). El modo solo-parser no requiere claves.

## Enrutamiento de parsers

Tesserae auto-enruta las fuentes al parser correcto por extensión de archivo:

| Extensión | Parser | Razón |
|---|---|---|
| `.md`, `.markdown`, `.txt`, `.rst` | `docling` | Ligero; sin descarga del modelo de MinerU. |
| `.doc`, `.docx`, `.ppt`, `.pptx`, `.xls`, `.xlsx` | `docling` | Mejor preservación de la estructura de Office según upstream. |
| `.pdf`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.tiff`, `.webp` | default configurado (`--raganything-parser`, por defecto `mineru`) | OCR + extracción de tablas. |

El wrapper gestionado `tesserae integrations refresh raganything` expone `--parser` (el default configurado para PDFs/imágenes), `--parse-method {auto,ocr,txt}`, `--root` (repetible, restringe a un subárbol), `--force` y `--full`. El enrutamiento por bucket de texto/office es fijo (ambos por defecto `docling`). Para anular el parser de texto u office explícitamente, llama al módulo subyacente directamente — `python -m tesserae.raganything_refresh --text-parser <p> --office-parser <p>` — que expone esos dos flags extra. El default configurado sigue aplicando a PDFs e imágenes.

Antes de que corra el bucle de parseo, Tesserae sondea si el paquete Python de cada parser requerido es importable (`importlib.import_module(...)`) y falla rápido con un único error agregado que lista cada parser ausente y su comando de instalación. Deliberadamente no usamos el `RAGAnything.check_parser_installation()` de upstream porque solo inspecciona el parser configurado en la instancia e incorpora comprobaciones de disponibilidad de pesos de modelo que no encajan en una etapa de pre-flight.

Tesserae también elige el parser de construcción de `RAGAnything` a partir de la distribución de enrutamiento real (gana el parser más elegido) en lugar de `--raganything-parser` directamente. Esto evita el modo de fallo en que `RAGAnything.__init__` intenta inicializar un parser pesado (p. ej. `mineru`) cuyos pesos de modelo aún no están en disco y arruina toda la ejecución antes de que los overrides `parser=` por llamada puedan surtir efecto. El flag `--raganything-parser` sigue controlando el default para las fuentes no-texto y no-Office (PDFs, imágenes).

### Paquetes de parser

La ruta de parseo en tiempo de compilación usa `docling.DocumentConverter` directamente para cada fuente no-texto; instálalo una vez y estás cubierto:

| Parser | Comando de instalación |
|---|---|
| `docling` (default en tiempo de compilación para todo excepto texto nativo) | incluido cuando ejecutas `--with-raganything --install-raganything` (o `pip install docling` standalone) |
| `paddleocr` (alternativa de OCR opcional) | `pip install 'raganything[paddleocr]>=1.3.0'` y `pip install paddlepaddle` (wheel específico de plataforma) |

> Nota: `mineru` actualmente **no se invoca en tiempo de compilación**. La ruta de compilación evita el pipeline completo de RAG-Anything (que requeriría callables de LLM/embedding/visión) y enruta cada fuente no-texto a través de docling directamente. El soporte de MinerU está reservado para una futura ruta de importación directa que ingiera un `content_list.json` producido externamente.

Cuando un parser configurado falta, `refresh-raganything` falla rápido — listando cada parser ausente en un único error con el comando de instalación correcto — en lugar de encadenar fallos por archivo.

### Widget de ask por página

Cada página de detalle (concepto, paper, repo, síntesis, entidad, topic, question, source) incluye un widget inline de "pregunta sobre esta página". Hace POST a `/api/ask` en la instancia local de `tesserae serve`, que llama a `tesserae.query.ask_project` y renderiza la respuesta inline. A diferencia de la CLI (donde `tesserae ask` es LLM-por-defecto), `/api/ask` usa por defecto la **recuperación sin LLM** por latencia del widget; envía `{"llm": true}` en el payload para optar por la respuesta planificada/sintetizada. El widget antepone el nombre del nodo de la página actual a la pregunta del usuario como pista de contexto en lenguaje natural (p. ej. `` About `<NodeName>`: <question> ``); un futuro PR puede cablear el scoping real de subgrafo en el propio `ask_project`.

El widget detecta la disponibilidad del backend vía `/api/ask/health` al cargar. Cuando la wiki se sirve estáticamente (GitHub Pages, `file://`, S3, cualquier host estático plano) el widget colapsa a una nota de una línea que apunta a los lectores a `tesserae serve` para uso interactivo local. Ninguna petición falla y nada bloquea el renderizado de la página — el widget es una isla JS diferida, separada del bundle más pesado del grafo.

Combina esto con el registro multi-proyecto (`tesserae projects register`) y puedes preguntar a la wiki de cualquier proyecto registrado desde cualquiera de sus páginas de detalle.

## Principio de colaboración

Tesserae sigue siendo el compilador de memoria. RAG-Anything sigue siendo un acompañante independiente: un parser multimodal + motor de recuperación LightRAG.

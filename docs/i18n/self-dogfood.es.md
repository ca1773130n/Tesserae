# Demo Self-dogfood

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a> · <a href="self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
Este proyecto puede indexarse a sí mismo. El flujo self-dogfood demuestra que Tesserae puede instalarse, configurarse dentro de su propio repositorio, ingerir sus propios docs/source/tests/scripts, actualizar opcionalmente Understand Anything y Cognee, compilar artefactos de grafo y construir el frontend web estático.

También ejercita el bucle de auto-mejora. Cada compilación re-deriva el estado de memoria mutable —`decay_score`, `access_count`, `confidence` y el flag `superseded`— a una **tabla sidecar `node_memory`** dentro de `.tesserae/sqlite.db`. Estos escalares viven *solo* en el sidecar y nunca en `graph.json`, de modo que una recompilación dogfood es byte-identical en el grafo mientras el sidecar rastrea decay y recurrence. Los insights que recurren en `>= 3` sesiones distintas se refuerzan con un confidence numérico en `(0, 1]` (3 sesiones → `0.5`, 4 → `0.75`, 5+ → `1.0`, con tope), se escriben en el sidecar y los expone la herramienta MCP `fresh_insights`, que por defecto oculta los findings reemplazados (superseded) por un near-duplicate más reciente.

## Comandos

Desde la raíz del repositorio:

```bash
# Asegúrate de que el comando de shell esté instalado.
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# (opcional) instala el backend de embeddings semánticos por defecto.
pip install 'tesserae[semantic]'

# Configura este repositorio como un proyecto Tesserae.
tesserae init \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee

# Compila las fuentes configuradas.
tesserae compile

# Reconstruye explícitamente el frontend estático.
tesserae export site

# Sirve localmente (compila el sitio automáticamente primero si es necesario).
tesserae serve --port 8765
```

Abre:

```text
http://127.0.0.1:8765/
```

## Workspace generado

La self-demo escribe los artefactos generados bajo:

```text
.tesserae/
```

Artefactos clave:

```text
.tesserae/config.json
.tesserae/graph.json
.tesserae/manifest.json
.tesserae/sqlite.db          # grafo tipado + sidecar node_memory + HarnessSessionsDB en vivo
.tesserae/report.md
.tesserae/competitive_report.md
.tesserae/temporal_facts.jsonl
.tesserae/graphiti_episodes.jsonl
.tesserae/markdown_projection/
.tesserae/obsidian_vault/
.tesserae/agent_harness/
.tesserae/site/
.tesserae/cognee_bundle/
```

El workspace generado no se confirma por defecto de forma intencional. Es reproducible desde el código fuente del repositorio con los comandos anteriores.

## Última ejecución verificada

Verificado el `2026-04-27 11:11:23 KST` desde el propio repositorio Tesserae.

Las opciones de integración (Understand Anything, cognee) ahora son **preguntas
interactivas del asistente**, no banderas de CLI. El equivalente no interactivo
de abajo ejecuta `tesserae init --yes` (integraciones DESACTIVADAS), activa las
integraciones en `.tesserae/config.json` (el asistente las escribe bajo las
claves `memory_backends` y `external_tools` — consulta los documentos de
integración para las claves exactas) y luego actualiza cada una antes de
compilar.

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae init --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts
                 # luego activa Understand Anything + cognee en .tesserae/config.json y ejecuta:
                 #   tesserae integrations refresh understand-anything
                 #   tesserae integrations refresh cognee
ingest command:  tesserae compile README.md docs --changed-only
compile command: tesserae compile
site command:    tesserae export site
serve command:   tesserae serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

Conteos finales de artefactos:

```text
nodes:               667
edges:               1020
markdown notes:      684
obsidian notes:      686
agent harness files: 14
cognee nodes:        667
cognee edges:        1020
graphiti episodes:  1020
temporal facts:      1020
site files:          index.html, nodes/index.html, sources/index.html, graph/index.html, graph.json, search-index.json, llms.txt, llms-full.txt, manifest.json, assets/style.css, assets/app.js
node pages:          687
source pages:        56
```

Tipos de nodo principales:

```text
CodeFunction:    452
Dependency:       55
CodeClass:        54
Concept:          51
SourceFile:       47
SourceDocument:    7
CodeProject:       1
```

Verificación en navegador:

```text
loaded title: Home · tesserae_self
visible stats: 667 nodes / 1020 edges / 55 sources / 7 types
sources page: source evidence table links to per-source pages
source detail: tesserae/frontend.py shows 41 nodes, 54 related edges, type mix, node links, and edge table
search smoke: StaticSiteBuilder returned CodeClass and StaticSiteBuilder.write_site results
console: no JavaScript errors on home, sources, source detail, or graph pages
server: TCP *:56821 LISTEN, serving via --host 0.0.0.0
```

## Qué demuestra esto

- La ruta de instalación pública funciona.
- El comando de shell `tesserae` funciona.
- Un repositorio puede adjuntar un workspace `.tesserae` local al proyecto.
- El markdown de investigación/documentación y los nodos de grafo de código de desarrollo pueden coexistir.
- Las proyecciones Markdown, Obsidian, frontend, Graphiti, Cognee, SQLite, report y agent-harness se producen desde una sola canalización de grafo.
- El frontend HTML estático puede explorar el grafo del proyecto sin un paso de compilación JavaScript.
- El bucle de auto-mejora corre y persiste: decay, conteos de acceso, recurrence confidence y flags supersede van al sidecar `node_memory` sin perturbar `graph.json`.
- La recuperación híbrida resuelve un backend semántico real cuando `tesserae[semantic]` está instalado (orden `auto` por defecto: model2vec → sentence-transformers → stub hash-bucket); sin él, la recuperación por embeddings se degrada al stub hash-bucket no semántico y emite una advertencia ruidosa.

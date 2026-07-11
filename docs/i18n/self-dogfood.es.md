# Demo Self-dogfood

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a> · <a href="self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
Este proyecto puede indexarse a sí mismo. El flujo self-dogfood demuestra que Tesserae puede instalarse, configurarse dentro de su propio repositorio, ingerir sus propios docs/código/tests/scripts, opcionalmente refrescar RAG-Anything y Cognee, compilar los artefactos del grafo y construir el frontend web estático.

El mismo flujo sirve también como prueba de humo multimodal. Con RAG-Anything instalado (`tesserae setup --install raganything`) y habilitado en `.tesserae/config.json` (`memory_backends.raganything.enabled: true`), la compilación dogfood apunta RAG-Anything al markdown de `docs/` del propio Tesserae más las imágenes de `docs/assets/` y del `assets/` a nivel de proyecto. Eso valida el pipeline multimodal contra un corpus no-código real y propio del proyecto — cubriendo capturas de pantalla y diagramas que los loaders de fuentes centrados en texto se saltan — sin inventar un conjunto de fixtures aparte.

También ejercita el bucle de auto-mejora. Cada compilación re-deriva el estado
mutable de memoria — `decay_score`, `access_count`, `confidence` y el flag
`superseded` — en una tabla **sidecar `node_memory`** dentro de
`.tesserae/sqlite.db`. Estos escalares viven *solo* en el sidecar y nunca en
`graph.json`, así que una compilación dogfood fresca es byte-idéntica en el grafo mientras
el sidecar rastrea el decay y la recurrencia. Los insights que recurren en `>= 3`
sesiones distintas se refuerzan con una confianza numérica en `(0, 1]`
(3 sesiones → `0.5`, 4 → `0.75`, 5+ → `1.0`, con tope), escrita en el sidecar
y expuesta por la herramienta MCP `fresh_insights`, que por defecto oculta los hallazgos
reemplazados por un casi-duplicado más nuevo.

## Comandos

Desde la raíz del repositorio:

```bash
# Ensure the shell command is installed.
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# (optional) install the default semantic embedding backend.
pip install 'tesserae[semantic]'

# Set up this repository as an Tesserae project.
tesserae init \
  --yes \
  --name tesserae_self \
  --source README.md \
  --source docs \
  --source tesserae \
  --source tests \
  --source scripts

# (optional) install + enable the heavier companions afterwards:
#   tesserae setup --install raganything --install cognee
#   then flip memory_backends.*.enabled / external_tools in .tesserae/config.json

# Compile the configured sources.
tesserae compile

# Rebuild the static frontend explicitly.
tesserae export site

# Serve locally (auto-builds the site first if needed).
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
.tesserae/sqlite.db          # typed graph + node_memory sidecar + live HarnessSessionsDB
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

El workspace generado intencionadamente no se commitea por defecto. Es reproducible desde la fuente del repositorio con los comandos de arriba.

## Última ejecución verificada

Verificado el `2026-04-27 11:11:23 KST` desde el propio repositorio de Tesserae.

Los opt-ins de integración (RAG-Anything, cognee) ahora son **prompts
interactivos del asistente**, no flags de CLI. El equivalente no interactivo de abajo ejecuta
`tesserae init --yes` (integraciones OFF), habilita las integraciones en
`.tesserae/config.json` (el asistente las escribe bajo las claves `memory_backends`
y `external_tools` — ver los docs de integración para las claves exactas), y luego
refresca cada una antes de compilar.

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae init --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts
                 # then enable the optional integrations in .tesserae/config.json and run:
                 #   tesserae integrations refresh raganything
                 #   tesserae integrations refresh cognee
ingest command:  tesserae compile README.md docs --changed-only
compile command: tesserae compile
site command:    tesserae export site
serve command:   tesserae serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

Recuentos finales de artefactos:

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
- Un repositorio puede acoplar un workspace `.tesserae` local al proyecto.
- El markdown de investigación/documentación y los nodos del grafo de código de desarrollo pueden coexistir.
- Las proyecciones Markdown, Obsidian, frontend, Graphiti, Cognee, SQLite, informe y agent-harness se producen desde un único pipeline de grafo.
- El frontend HTML estático puede navegar el grafo del proyecto sin un paso de build de JavaScript.
- El bucle de auto-mejora corre y persiste: decay, recuentos de acceso, confianza por recurrencia y flags de supersede aterrizan en el sidecar `node_memory` sin perturbar `graph.json`.
- La recuperación híbrida resuelve un backend semántico real cuando `tesserae[semantic]` está instalado (orden `auto` por defecto: model2vec → sentence-transformers → stub hash-bucket); sin él, la recuperación por embeddings degrada al stub no semántico de hash-bucket y emite una advertencia bien visible.

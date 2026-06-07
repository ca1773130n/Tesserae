# Self-Dogfood-Demo

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a></p>
<!-- translations:end -->
Dieses Projekt kann sich selbst indexieren. Der Self-Dogfood-Flow beweist, dass Tesserae installiert, in seinem eigenen Repository aufgesetzt werden kann, seine eigenen Docs/Source/Tests/Scripts ingestiert, optional Understand Anything und Cognee refresht, Graph-Artefakte kompiliert und das statische Web-Frontend baut.

Derselbe Flow dient gleichzeitig als multimodaler Smoke-Test. Mit `--with-raganything --install-raganything --run-raganything` richtet der Dogfood-Compile RAG-Anything auf das eigene `docs/`-Markdown von Tesserae plus die Bilder unter `docs/assets/` und projekt-level `assets/`. Das validiert die multimodale Pipeline gegen einen echten, projekt-eigenen Nicht-Code-Korpus — abdeckend Screenshots und Diagramme, die die textzentrierten Source-Loader überspringen — ohne ein separates Fixture-Set zu erfinden.

Er übt außerdem die Selbstverbesserungs-Schleife. Jeder Compile leitet den veränderlichen Memory-Zustand — `decay_score`, `access_count`, `confidence` und das `superseded`-Flag — neu in eine **`node_memory`-Sidecar-Tabelle** innerhalb von `.tesserae/sqlite.db` ab. Diese Skalare leben *nur* im Sidecar und niemals in `graph.json`, sodass ein erneuter Dogfood-Compile auf dem Graph byte-identical ist, während der Sidecar Decay und Recurrence verfolgt. Insights, die über `>= 3` verschiedene Sessions wiederkehren, werden mit einem numerischen Confidence in `(0, 1]` verstärkt (3 Sessions → `0.5`, 4 → `0.75`, 5+ → `1.0`, gedeckelt), in den Sidecar geschrieben und vom MCP-Tool `fresh_insights` ausgegeben, das standardmäßig Findings ausblendet, die von einem neueren Near-Duplicate ersetzt (superseded) wurden.

## Befehle

Aus dem Repository-Root:

```bash
# Ensure the shell command is installed.
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# (optional) den Standard-Semantik-Embedding-Backend installieren.
pip install 'tesserae[semantic]'

# Set up this repository as an Tesserae project.
tesserae project setup \
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
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
  --run-cognee \
  --install-cognee

# Compile the configured sources.
tesserae project compile

# Rebuild the static frontend explicitly.
tesserae project build-site

# Serve locally.
tesserae project serve --port 8765
```

Öffne:

```text
http://127.0.0.1:8765/
```

## Generiertes Workspace

Die Self-Demo schreibt generierte Artefakte unter:

```text
.tesserae/
```

Schlüssel-Artefakte:

```text
.tesserae/config.json
.tesserae/graph.json
.tesserae/manifest.json
.tesserae/sqlite.db          # typisierter Graph + node_memory-Sidecar + Live-HarnessSessionsDB
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

Das generierte Workspace wird absichtlich standardmäßig nicht committet. Es ist mit den obigen Befehlen aus den Repository-Sources reproduzierbar.

## Letzter verifizierter Lauf

Verifiziert am `2026-04-27 11:11:23 KST` aus dem Tesserae-Repository selbst.

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae project setup --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts --with-understand-anything --install-understand-anything --understand-anything-platform codex --run-cognee --install-cognee
ingest command:  tesserae project ingest README.md docs --changed-only
compile command: tesserae project compile
site command:    tesserae project build-site
serve command:   tesserae project serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

Finale Artefakt-Counts:

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

Top-Node-Typen:

```text
CodeFunction:    452
Dependency:       55
CodeClass:        54
Concept:          51
SourceFile:       47
SourceDocument:    7
CodeProject:       1
```

Browser-Verifikation:

```text
loaded title: Home · tesserae_self
visible stats: 667 nodes / 1020 edges / 55 sources / 7 types
sources page: source evidence table links to per-source pages
source detail: tesserae/frontend.py shows 41 nodes, 54 related edges, type mix, node links, and edge table
search smoke: StaticSiteBuilder returned CodeClass and StaticSiteBuilder.write_site results
console: no JavaScript errors on home, sources, source detail, or graph pages
server: TCP *:56821 LISTEN, serving via --host 0.0.0.0
```

## Was das demonstriert

- Der öffentliche Install-Pfad funktioniert.
- Der `tesserae`-Shell-Befehl funktioniert.
- Ein Repository kann ein projekt-lokales `.tesserae`-Workspace anhängen.
- Research-/Dokumentations-Markdown und Development-Code-Graph-Knoten können koexistieren.
- Markdown-, Obsidian-, Frontend-, Graphiti-, Cognee-, SQLite-, Report- und Agent-Harness-Projektionen werden aus einer Graph-Pipeline produziert.
- Das statische HTML-Frontend kann den Project-Graph ohne JavaScript-Build-Schritt browsen.
- Die Selbstverbesserungs-Schleife läuft und persistiert: Decay, Access-Counts, Recurrence-Confidence und Supersede-Flags landen im `node_memory`-Sidecar, ohne `graph.json` zu stören.
- Hybride Retrieval löst ein echtes semantisches Backend auf, wenn `tesserae[semantic]` installiert ist (Standard-`auto`-Reihenfolge: model2vec → sentence-transformers → Hash-Bucket-Stub); ohne es degradiert das Embedding-Retrieval zum nicht-semantischen Hash-Bucket-Stub und gibt eine laute Warnung aus.

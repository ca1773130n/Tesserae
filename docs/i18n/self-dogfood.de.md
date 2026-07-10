# Self-Dogfood-Demo

<!-- translations:start -->
<p align="center"><a href="../self-dogfood.md">English</a> · <a href="self-dogfood.ko.md">한국어</a> · <a href="self-dogfood.zh.md">中文</a> · <a href="self-dogfood.ja.md">日本語</a> · <a href="self-dogfood.ru.md">Русский</a> · <a href="self-dogfood.es.md">Español</a> · <a href="self-dogfood.fr.md">Français</a></p>
<!-- translations:end -->
Dieses Projekt kann sich selbst indexieren. Der Self-Dogfood-Flow beweist, dass Tesserae installiert, im eigenen Repository eingerichtet werden, seine eigenen Docs/Quellen/Tests/Skripte ingesten, optional Understand Anything und Cognee refreshen, Graph-Artefakte kompilieren und das statische Web-Frontend bauen kann.

Derselbe Flow dient zugleich als multimodaler Smoke-Test. Mit installiertem RAG-Anything (`tesserae setup --install raganything`) und aktiviert in `.tesserae/config.json` (`memory_backends.raganything.enabled: true`) richtet der Dogfood-Compile RAG-Anything auf Tesseraes eigenes `docs/`-Markdown plus die Bilder unter `docs/assets/` und dem projektweiten `assets/`. Das validiert die multimodale Pipeline gegen einen realen, projekteigenen Nicht-Code-Korpus — inklusive Screenshots und Diagrammen, die die text-first Source-Loader überspringen — ohne ein separates Fixture-Set zu erfinden.

Er exerziert außerdem die Selbstverbesserungsschleife. Jeder Compile leitet den mutierbaren
Memory-Zustand neu ab — `decay_score`, `access_count`, `confidence` und das
`superseded`-Flag — in eine **`node_memory`-Sidecar**-Tabelle innerhalb von
`.tesserae/sqlite.db`. Diese Skalare leben *nur* im Sidecar und nie in
`graph.json`, sodass ein frischer Dogfood-Compile auf dem Graph byte-identisch ist, während
das Sidecar Decay und Wiederkehr verfolgt. Insights, die über `>= 3`
verschiedene Sessions wiederkehren, werden mit einer numerischen Confidence in `(0, 1]`
verstärkt (3 Sessions → `0.5`, 4 → `0.75`, 5+ → `1.0`, gedeckelt), ins Sidecar
geschrieben und vom MCP-Tool `fresh_insights` angezeigt, das standardmäßig Befunde
verbirgt, die von einem neueren Beinahe-Duplikat superseded wurden.

## Befehle

Vom Repository-Root:

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
#   tesserae setup --install raganything --install understand-anything --install cognee
#   then flip memory_backends.*.enabled / external_tools in .tesserae/config.json

# Compile the configured sources.
tesserae compile

# Rebuild the static frontend explicitly.
tesserae export site

# Serve locally (auto-builds the site first if needed).
tesserae serve --port 8765
```

Öffne:

```text
http://127.0.0.1:8765/
```

## Generierter Workspace

Die Self-Demo schreibt generierte Artefakte unter:

```text
.tesserae/
```

Zentrale Artefakte:

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

Der generierte Workspace wird absichtlich standardmäßig nicht committet. Er ist mit den obigen Befehlen aus dem Repository-Quellcode reproduzierbar.

## Letzter verifizierter Lauf

Verifiziert am `2026-04-27 11:11:23 KST` aus dem Tesserae-Repository selbst.

Integrations-Opt-ins (Understand Anything, cognee) sind jetzt **interaktive
Wizard-Prompts**, keine CLI-Flags. Das nicht-interaktive Äquivalent unten führt
`tesserae init --yes` aus (Integrationen AUS), aktiviert die Integrationen in
`.tesserae/config.json` (der Wizard schreibt sie unter die Keys `memory_backends`
und `external_tools` — siehe die Integrations-Docs für die exakten Keys) und
refresht dann jede vor dem Kompilieren.

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/Tesserae --skip-shell-config
setup command:   tesserae init --yes --name tesserae_self --source README.md --source docs --source tesserae --source tests --source scripts
                 # then enable Understand Anything + cognee in .tesserae/config.json and run:
                 #   tesserae integrations refresh understand-anything
                 #   tesserae integrations refresh cognee
ingest command:  tesserae compile README.md docs --changed-only
compile command: tesserae compile
site command:    tesserae export site
serve command:   tesserae serve --host 0.0.0.0 --port 56821
local URL:       http://127.0.0.1:56821/
LAN URL:         http://192.168.45.130:56821/
```

Finale Artefakt-Zahlen:

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

Top-Knotentypen:

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

## Was dies demonstriert

- Der öffentliche Installationspfad funktioniert.
- Der Shell-Befehl `tesserae` funktioniert.
- Ein Repository kann sich einen projektlokalen `.tesserae`-Workspace anhängen.
- Research-/Dokumentations-Markdown und Entwicklungs-Code-Graphknoten können koexistieren.
- Markdown-, Obsidian-, Frontend-, Graphiti-, Cognee-, SQLite-, Report- und Agent-Harness-Projektionen werden aus einer einzigen Graph-Pipeline produziert.
- Das statische HTML-Frontend kann den Projektgraph ohne JavaScript-Build-Schritt browsen.
- Die Selbstverbesserungsschleife läuft und persistiert: Decay, Access-Counts, Wiederkehr-Confidence und Supersede-Flags landen im `node_memory`-Sidecar, ohne `graph.json` zu stören.
- Hybrid-Retrieval löst ein echtes semantisches Backend auf, wenn `tesserae[semantic]` installiert ist (Default-`auto`-Reihenfolge: model2vec → sentence-transformers → Hash-Bucket-Stub); ohne es degradiert Embedding-Retrieval zum nicht-semantischen Hash-Bucket-Stub und gibt eine laute Warnung aus.

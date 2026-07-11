# Self-dogfood Demo

<!-- translations:start -->
<p align="center"><a href="i18n/self-dogfood.ko.md">한국어</a> · <a href="i18n/self-dogfood.zh.md">中文</a> · <a href="i18n/self-dogfood.ja.md">日本語</a> · <a href="i18n/self-dogfood.ru.md">Русский</a> · <a href="i18n/self-dogfood.es.md">Español</a> · <a href="i18n/self-dogfood.fr.md">Français</a> · <a href="../i18n/self-dogfood.de.md">Deutsch</a></p>
<!-- translations:end -->
This project can index itself. The self-dogfood flow proves that Tesserae can be installed, set up inside its own repository, ingest its own docs/source/tests/scripts, optionally refresh RAG-Anything and Cognee, compile graph artifacts, and build the static web frontend.

The same flow doubles as a multimodal smoke test. With RAG-Anything installed (`tesserae setup --install raganything`) and enabled in `.tesserae/config.json` (`memory_backends.raganything.enabled: true`), the dogfood compile points RAG-Anything at Tesserae's own `docs/` markdown plus the `docs/assets/` and project-level `assets/` images. That validates the multimodal pipeline against a real, project-owned non-code corpus — covering screenshots and diagrams the text-first source loaders skip — without inventing a separate fixture set.

It also exercises the self-improvement loop. Each compile re-derives mutable
memory state — `decay_score`, `access_count`, `confidence`, and the
`superseded` flag — into a **`node_memory` sidecar** table inside
`.tesserae/sqlite.db`. These scalars live in the sidecar *only* and never in
`graph.json`, so a fresh dogfood compile is byte-identical on the graph while
the sidecar tracks decay and recurrence. Insights that recur across `>= 3`
distinct sessions are reinforced with a numeric confidence in `(0, 1]`
(3 sessions → `0.5`, 4 → `0.75`, 5+ → `1.0`, capped), written to the sidecar
and surfaced by the MCP `fresh_insights` tool, which by default hides findings
superseded by a newer near-duplicate.

## Commands

From the repository root:

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

Open:

```text
http://127.0.0.1:8765/
```

## Generated workspace

The self-demo writes generated artifacts under:

```text
.tesserae/
```

Key artifacts:

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

The generated workspace is intentionally not committed by default. It is reproducible from the repository source with the commands above.

## Latest verified run

Verified on `2026-04-27 11:11:23 KST` from the Tesserae repository itself.

Integration opt-ins (RAG-Anything, cognee) are **interactive wizard
prompts** now, not CLI flags. The non-interactive equivalent below runs
`tesserae init --yes` (integrations OFF), enables the integrations in
`.tesserae/config.json` (the wizard writes them under the `memory_backends`
and `external_tools` keys — see the integration docs for the exact keys), then
refreshes each before compiling.

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

Final artifact counts:

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

Top node types:

```text
CodeFunction:    452
Dependency:       55
CodeClass:        54
Concept:          51
SourceFile:       47
SourceDocument:    7
CodeProject:       1
```

Browser verification:

```text
loaded title: Home · tesserae_self
visible stats: 667 nodes / 1020 edges / 55 sources / 7 types
sources page: source evidence table links to per-source pages
source detail: tesserae/frontend.py shows 41 nodes, 54 related edges, type mix, node links, and edge table
search smoke: StaticSiteBuilder returned CodeClass and StaticSiteBuilder.write_site results
console: no JavaScript errors on home, sources, source detail, or graph pages
server: TCP *:56821 LISTEN, serving via --host 0.0.0.0
```

## What this demonstrates

- Public install path works.
- `tesserae` shell command works.
- A repository can attach a project-local `.tesserae` workspace.
- Research/documentation markdown and development-code graph nodes can coexist.
- Markdown, Obsidian, frontend, Graphiti, Cognee, SQLite, report, and agent-harness projections are produced from one graph pipeline.
- The static HTML frontend can browse the project graph without a JavaScript build step.
- The self-improvement loop runs and persists: decay, access counts, recurrence confidence, and supersede flags land in the `node_memory` sidecar without perturbing `graph.json`.
- Hybrid retrieval resolves a real semantic backend when `tesserae[semantic]` is installed (default `auto` order: model2vec → sentence-transformers → hash-bucket stub); without it, embedding retrieval degrades to the non-semantic hash-bucket stub and emits a loud warning.

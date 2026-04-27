# Self-dogfood Demo

This project can index itself. The self-dogfood flow proves that LLM-Wiki can be installed, initialized inside its own repository, ingest its own docs/source/tests/scripts, compile graph artifacts, and build the static web frontend.

## Commands

From the repository root:

```bash
# Ensure the shell command is installed.
./scripts/install.sh --dir "$PWD"
export PATH="$HOME/.local/bin:$PATH"

# Initialize this repository as an LLM-Wiki project.
llm_wiki project init \
  --name llm_wiki_self \
  --source-kind Repository \
  --source README.md \
  --source docs \
  --source llm_wiki \
  --source tests \
  --source scripts

# Ingest/compile the configured sources.
llm_wiki project compile --changed-only

# Rebuild the static frontend explicitly.
llm_wiki project build-site

# Serve locally.
llm_wiki project serve --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

## Generated workspace

The self-demo writes generated artifacts under:

```text
.llm-wiki/
```

Key artifacts:

```text
.llm-wiki/config.json
.llm-wiki/graph.json
.llm-wiki/manifest.json
.llm-wiki/sqlite.db
.llm-wiki/report.md
.llm-wiki/competitive_report.md
.llm-wiki/temporal_facts.jsonl
.llm-wiki/graphiti_episodes.jsonl
.llm-wiki/markdown_projection/
.llm-wiki/obsidian_vault/
.llm-wiki/agent_harness/
.llm-wiki/site/
.llm-wiki/cognee_bundle/
```

The generated workspace is intentionally not committed by default. It is reproducible from the repository source with the commands above.

## Latest verified run

Verified on `2026-04-27 11:11:23 KST` from the LLM-Wiki repository itself.

```text
install command: ./scripts/install.sh --dir /Users/neo/Developer/Projects/LLM-Wiki --skip-shell-config
init command:    llm_wiki project init --name llm_wiki_self --source-kind Repository --source README.md --source docs --source llm_wiki --source tests --source scripts
ingest command:  llm_wiki project ingest README.md docs --changed-only
compile command: llm_wiki project compile
site command:    llm_wiki project build-site
serve command:   llm_wiki project serve --host 0.0.0.0 --port 56821
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
loaded title: Home · llm_wiki_self
visible stats: 667 nodes / 1020 edges / 55 sources / 7 types
sources page: source evidence table links to per-source pages
source detail: llm_wiki/frontend.py shows 41 nodes, 54 related edges, type mix, node links, and edge table
search smoke: StaticSiteBuilder returned CodeClass and StaticSiteBuilder.write_site results
console: no JavaScript errors on home, sources, source detail, or graph pages
server: TCP *:56821 LISTEN, serving via --host 0.0.0.0
```

## What this demonstrates

- Public install path works.
- `llm_wiki` shell command works.
- A repository can attach a project-local `.llm-wiki` workspace.
- Research/documentation markdown and development-code graph nodes can coexist.
- Markdown, Obsidian, frontend, Graphiti, Cognee, SQLite, report, and agent-harness projections are produced from one graph pipeline.
- The static HTML frontend can browse the project graph without a JavaScript build step.

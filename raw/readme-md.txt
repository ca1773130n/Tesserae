README.md
=========

<h1 align="center">LLM-Wiki</h1>

<p align="center">
  <strong>Turn research notes, docs, code, and agent sessions into a typed wiki graph you can browse, search, query, and publish.</strong>
  <br />
  <em>A local-first knowledge pipeline for humans, coding agents, and long-lived project memory.</em>
</p>

<p align="center">
  <a href="#-quick-start"><img src="https://img.shields.io/badge/Quick_Start-blue" alt="Quick Start" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow" alt="License: MIT" /></a>
  <a href="docs/architecture.md"><img src="https://img.shields.io/badge/Architecture-typed_graph-8A2BE2" alt="Architecture" /></a>
  <a href="docs/session-history.md"><img src="https://img.shields.io/badge/Sessions-project_memory-38bdf8" alt="Session History" /></a>
  <a href="docs/quickstart.md"><img src="https://img.shields.io/badge/Docs-quickstart-d4a574" alt="Docs" /></a>
  <a href="https://ca1773130n.github.io/LLM-Wiki/"><img src="https://img.shields.io/badge/Demo-GitHub_Pages-00c853" alt="Demo" /></a>
</p>

---

> [!TIP]
> You have papers in one folder, design docs in another, a living codebase, and weeks of Claude/Codex sessions explaining decisions. Where is the map?

LLM-Wiki builds that map. It compiles raw project knowledge into a validated graph, projects it into a readable markdown/wiki layer, and publishes a static site with search, graph navigation, AI-readable exports, and optional local MCP tools.

> **Graphs that stay useful > graphs that merely look impressive.**

LLM-Wiki is not a generic noun-phrase extractor. It uses a controlled ontology so the important objects stay explicit: papers, repositories, models, datasets, benchmarks, metrics, claims, evidence spans, concepts, source files, classes, functions, dependencies, trends, and imported agent sessions.

```text
raw sources → validated typed graph → markdown/wiki projection → static site + agent interfaces
```

---

## ✨ What you get

### Explore a real project wiki

Compile a project into a browsable static site with home, sources, concepts, entities, papers, repos, topics, syntheses, open questions, sessions, timeline, graph view, and AI sibling files.

The site is just files under `.llm-wiki/site/`: easy to serve locally, push to GitHub Pages, copy to an internal server, or hand to another agent.

### Keep research and code in the same memory system

LLM-Wiki has separate ontology slices for research and development code, but one durable pipeline:

- research notes become papers, claims, evidence, topics, concepts, metrics, datasets, models, and trends;
- docs and repositories become source documents and project pages;
- code projects add source files, classes, functions, and dependencies;
- all projections remain reproducible from the graph.

### Import agent sessions as project memory

Claude Code and Codex transcripts can be explicitly discovered, normalized, and rendered into `/sessions/` pages. Session details show summaries, metadata, decisions, touched files, commands, a turn rail, readable markdown turns, and collapsed tool-use blocks.

No surprise scraping: normal builds read already-imported `.llm-wiki/harness_sessions/` records only.

### Give agents structured access

LLM-Wiki writes machine-readable exports alongside the human site:

- `search-index.json`
- `graph.json`
- `graph.jsonld`
- `llms.txt`
- `llms-full.txt`
- `manifest.json`
- per-page `.txt` and `.json` siblings
- optional MCP stdio server tools such as `search_nodes`, `node_context`, `search_facts`, and `timeline`

<table>
  <tr>
    <td width="50%" valign="top">
      <h3>🧭 Typed knowledge graph</h3>
      <p>Controlled node and edge vocabularies keep the graph meaningful instead of becoming arbitrary entity soup.</p>
    </td>
    <td width="50%" valign="top">
      <h3>🔍 Search + static site</h3>
      <p>Generate a dependency-light site with command-palette search, source previews, related pages, graph view, and AI-friendly exports.</p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>🧠 Session history</h3>
      <p>Turn local Claude Code/Codex sessions into searchable project memory without silently scanning private transcript stores.</p>
    </td>
    <td width="50%" valign="top">
      <h3>🧩 Agent harnesses</h3>
      <p>Export context/config for Claude Code, Codex, Gemini, Cursor, Kiro, and OpenCode so agents can consume the wiki.</p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>🗄️ Storage backends</h3>
      <p>Use markdown, SQLite, optional Kuzu, Obsidian vaults, Cognee bundles, and Graphiti-style temporal facts.</p>
    </td>
    <td width="50%" valign="top">
      <h3>🔐 Local-first by default</h3>
      <p>The deterministic path needs no API key. Optional Claude CLI/OAuth and Codex CLI/OAuth adapters fit no-API-key workflows.</p>
    </td>
  </tr>
</table>

---

## 🚀 Quick Start

### 1. Install

```bash
pip install llm-wiki
```

For contributor checkouts:

```bash
git clone https://github.com/ca1773130n/LLM-Wiki.git
cd LLM-Wiki
pip install -e .
```

### 2. Initialize a wiki inside any project

```bash
cd /path/to/my-project
llm_wiki project init \
  --name my_project_wiki \
  --source-kind Repository \
  --source README.md \
  --source docs \
  --source src \
  --source tests
```

### 3. Compile the graph and site

```bash
llm_wiki project compile --changed-only
llm_wiki project build-site
```

This writes a project-local wiki workspace:

```text
.llm-wiki/
  config.json
  graph.json
  manifest.json
  sqlite.db
  temporal_facts.jsonl
  graphiti_episodes.jsonl
  markdown_projection/
  wiki/
  site/
  agent_harness/
  harness_sessions/
  obsidian_vault/
  cognee_bundle/
```

### 4. Open the site

```bash
llm_wiki project serve --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

### 5. Keep exploring

```bash
# Query the compiled wiki
llm_wiki project query "What are the main abstractions in this project?"

# Lint graph/wiki/site consistency
llm_wiki project lint

# Export Claude Code/Codex/Gemini/Cursor/Kiro/OpenCode harness files
llm_wiki project export-agent-harness

# Export an Obsidian vault projection
llm_wiki project export-obsidian

# Export Graphiti-compatible temporal episodes
llm_wiki project export-graphiti

# Run a local MCP server over a compiled graph
llm_wiki_mcp --graph .llm-wiki/graph.json
```

---

## 🧠 Import local agent sessions

Session import is explicit. Preview first, import second, rebuild third:

```bash
# See matching local Claude Code/Codex sessions for this project
llm_wiki project sessions discover

# Normalize and store them under .llm-wiki/harness_sessions/
llm_wiki project sessions discover --import

# Confirm what will be rendered
llm_wiki project sessions list

# Emit sessions/index.html and session detail pages
llm_wiki project build-site
```

Generated session detail pages include:

- high-level summary and timeline;
- files, commands, tools, decisions, and errors;
- collapsed subagent history;
- user/assistant conversation turns rendered as markdown;
- collapsed tool-use payloads under the preceding assistant message;
- a left turn rail with `#turn-N` anchors.

Read the privacy and publishing notes in [`docs/session-history.md`](docs/session-history.md) before publishing transcript-derived pages publicly.

---

## 🌐 Publish the wiki

Every compile produces a static site at `.llm-wiki/site/`. Serve it locally, copy it to any web server, or push it to GitHub Pages:

```bash
llm_wiki project deploy --build --enable-pages
```

Common deploy options:

```bash
# Preview the gh-pages commit without pushing
llm_wiki project deploy --dry-run

# Use a custom deploy branch or remote
llm_wiki project deploy --branch site --remote upstream

# Use a custom deploy message
llm_wiki project deploy --message "Refresh wiki for release notes"

# Allow deploy with a dirty working tree
llm_wiki project deploy --force
```

The default public URL is:

```text
https://<github-owner>.github.io/<repo-name>/
```

---

## 🔌 Interfaces and exports

| Interface | Command / Artifact | Use it for |
|---|---|---|
| Static site | `.llm-wiki/site/index.html` | Human browsing, search, graph exploration, Pages deploys |
| Search index | `.llm-wiki/site/search-index.json` | Fast local search and agent retrieval |
| Graph JSON | `.llm-wiki/graph.json` and `.llm-wiki/site/graph.json` | Authoritative graph payloads |
| LLM text exports | `llms.txt`, `llms-full.txt`, per-page `.txt` | Context packs for agents |
| MCP server | `llm_wiki_mcp --graph .llm-wiki/graph.json` | Tool calls from Hermes or other MCP clients |
| Agent harness | `llm_wiki project export-agent-harness` | Claude Code, Codex, Gemini, Cursor, Kiro, OpenCode setup files |
| Obsidian | `llm_wiki project export-obsidian` | Open the projection as a vault |
| Graphiti | `llm_wiki project export-graphiti` / `sync-graphiti` | Temporal fact export/sync |
| Cognee | `--cognee-output`, `--cognee-add`, `--cognee-codex-cognify` | Cognee bundles and optional no-API-key cognify path |

---

## 🔧 Under the hood

LLM-Wiki is a pipeline, not a dashboard bolted onto a folder.

| Stage | What happens |
|---|---|
| Source ingest | Read configured project sources, tolerate malformed text, hash content, and support changed-only runs. |
| Extraction | Deterministic extractors and optional Claude CLI/OAuth enrichment produce candidate graph facts. |
| Validation | `ResearchGraph` enforces controlled node types and edge types before anything is persisted. |
| Canonicalization | Alias handling and review queues help merge near-duplicate concepts safely. |
| Projection | Wiki markdown, static HTML, search index, graph payloads, AI siblings, Obsidian, Cognee, Graphiti, SQLite, and Kuzu outputs are generated from the graph. |
| Agent memory | Imported harness sessions and exported harness configs connect the wiki to everyday coding-agent workflows. |

The controlled ontology is the guardrail: it keeps useful distinctions alive instead of flattening everything into generic entities.

---

## 📚 Documentation

- [Quickstart](docs/quickstart.md)
- [Installation](docs/installation.md)
- [Architecture](docs/architecture.md)
- [Feature map](docs/feature-map.md)
- [Harness session history](docs/session-history.md)
- [Frontend route walkthrough](docs/frontend-redesign.md)
- [Self-dogfood demo](docs/self-dogfood.md)
- [Publishing checklist](docs/publishing-checklist.md)

---

## 🧪 Development

Run focused tests while iterating:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest \
  tests/test_frontend.py \
  tests/test_project_cli.py \
  tests/test_harness_sessions.py \
  tests/test_site_tokens.py \
  -q
```

Run the full suite:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q
```

> [!NOTE]
> If you add or change docs, run `llm_wiki project compile` before `build-site`; docs are source inputs, not just static files.

---

## 🤝 Contributing

Contributions are welcome.

1. Fork the repository.
2. Create a branch: `git checkout -b feature/my-change`.
3. Make the change with tests or docs.
4. Run the relevant pytest command.
5. Commit and open a pull request.

Please open an issue first for large changes to ontology, extraction behavior, generated routes, or deploy semantics.

---

<p align="center">
  <strong>Stop losing project knowledge in scattered files and forgotten chats. Build the map once, then keep it alive.</strong>
</p>

<p align="center">
  MIT License &copy; LLM-Wiki Contributors
</p>

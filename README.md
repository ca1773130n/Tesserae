<h1 align="center">LLM-Wiki</h1>

<p align="center">
  <strong>Turn your repo, docs, papers, and agent chats into a website + graph that agents can actually use.</strong>
  <br />
  <em>Karpathy's LLM Wiki idea, rebuilt as a typed, publishable, agent-native project memory system.</em>
</p>

<p align="center">
  <a href="#quick-start"><img src="https://img.shields.io/badge/Quick_Start-blue" alt="Quick Start" /></a>
  <a href="docs/architecture.md"><img src="https://img.shields.io/badge/Graph-2D%2F3D-8A2BE2" alt="2D/3D Graph" /></a>
  <a href="docs/session-history.md"><img src="https://img.shields.io/badge/Sessions-agent_memory-38bdf8" alt="Session History" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow" alt="MIT License" /></a>
</p>

<p align="center">
  <img src="docs/assets/wiki-graph-screenshot.png" alt="LLM-Wiki website showing a 3D knowledge graph, source explorer, navigation, and graph controls" width="100%" />
</p>

---

## Why I made this

Andrej Karpathy's [`llm-wiki.md`](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) is a beautiful idea:

> raw sources stay immutable, an LLM maintains a persistent markdown wiki, and the wiki compounds over time.

A lot of follow-up projects implement that pattern as folders of markdown plus an agent prompt. That's useful, but I wanted something more developer-native:

- not just notes, but a typed graph;
- not just a private folder, but a website you can ship;
- not just human browsing, but structured context for coding agents;
- not just documents, but the Claude/Codex sessions where the real project decisions happened.

LLM-Wiki is that missing layer.

It gives your agents a memory system that looks like a wiki, behaves like a graph, and ships like a static site.

---

## What you get

### 1. A wiki website builder

Run it on a repo and get a static website under `.llm-wiki/site/`:

- home page
- source/document pages
- concepts, entities, papers, repos, topics, syntheses
- search
- graph view
- session history pages
- `llms.txt`, `llms-full.txt`, JSON siblings, and graph payloads for agents

Serve it locally, publish it to GitHub Pages, or hand the folder to another agent.

### 2. 2D / 3D graph view

LLM-Wiki does not stop at markdown files. It builds a typed knowledge graph and renders it as an interactive website graph:

- switch between 2D and 3D;
- search nodes;
- filter by type;
- browse source, paper, repo, concept, and session relationships;
- export the same graph as JSON / JSON-LD.

### 3. Session history as project memory

Your important project knowledge is not only in `README.md` and `src/`.
It is also buried in agent sessions.

LLM-Wiki can explicitly import local Claude Code / Codex sessions and turn them into searchable wiki pages with:

- summaries;
- decisions;
- files touched;
- commands run;
- readable conversation turns;
- collapsed tool-use blocks.

No surprise scraping: session import is explicit.

### 4. Agent-first outputs

The website is nice, but agents are the main customer.

LLM-Wiki writes structured artifacts that coding agents can retrieve directly:

| Artifact | Why it matters |
|---|---|
| `search-index.json` | fast project search |
| `graph.json` / `graph.jsonld` | typed relationships |
| `llms.txt` / `llms-full.txt` | context packs |
| per-page `.txt` / `.json` | precise page context |
| MCP server | tool calls like `search_nodes`, `node_context`, `timeline` |
| agent harness export | Claude Code, Codex, Gemini, Cursor, Kiro, OpenCode setup |

---

## Quick start

```bash
pip install llm-wiki
```

Inside any project:

```bash
llm_wiki project setup
llm_wiki project compile
llm_wiki project build-site
llm_wiki project serve --port 8765
```

The setup wizard is a colored TUI: choose sources, enable companion tools like Understand Anything, and optionally store a refresh command so external graph artifacts update automatically before future compiles.

Open:

```text
http://127.0.0.1:8765/
```

---

## Import agent sessions

```bash
# Preview matching local Claude Code / Codex sessions
llm_wiki project sessions discover

# Import them into .llm-wiki/harness_sessions/
llm_wiki project sessions discover --import

# Rebuild the website with /sessions/ pages
llm_wiki project build-site
```

---

## Publish

```bash
llm_wiki project deploy --build --enable-pages
```

The generated site is plain static files, so you can also copy `.llm-wiki/site/` to any web server.

---

## How it works

```text
raw sources
  ↓
deterministic + optional LLM extraction
  ↓
validated typed graph
  ↓
markdown/wiki projection
  ↓
static website + 2D/3D graph + search + agent exports
```

The controlled ontology is the important part. It keeps the graph useful instead of turning your project into random entity soup.

---

## Docs

- [Quickstart](docs/quickstart.md)
- [Installation](docs/installation.md)
- [Architecture](docs/architecture.md)
- [Feature map](docs/feature-map.md)
- [Session history](docs/session-history.md)
- [Understand Anything companion workflow](docs/integrations/understand-anything.md)
- [Publishing checklist](docs/publishing-checklist.md)

---

<p align="center">
  <strong>Give your agents a project memory they can search, cite, update, and ship.</strong>
</p>

<p align="center">
  MIT License &copy; LLM-Wiki Contributors
</p>

<h1 align="center">LLM-Wiki</h1>

<p align="center">
  <strong>A beautiful setup wizard that turns docs, code, graphs, and agent sessions into a publishable LLM-native wiki.</strong>
  <br />
  <em>Karpathy's LLM Wiki pattern, upgraded with a website builder, typed graph, session memory, and companion-tool automation.</em>
</p>

<p align="center">
  <a href="#quick-start"><img src="https://img.shields.io/badge/Quick_Start-setup_wizard-blue" alt="Quick Start" /></a>
  <a href="docs/architecture.md"><img src="https://img.shields.io/badge/Graph-2D%2F3D-8A2BE2" alt="2D/3D Graph" /></a>
  <a href="docs/session-history.md"><img src="https://img.shields.io/badge/Sessions-agent_memory-38bdf8" alt="Session History" /></a>
  <a href="docs/integrations/understand-anything.md"><img src="https://img.shields.io/badge/Companion-Understand_Anything-d4a574" alt="Understand Anything companion workflow" /></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow" alt="MIT License" /></a>
</p>

<p align="center">
  <img src="docs/assets/wiki-graph-screenshot.png" alt="LLM-Wiki website showing a 3D knowledge graph, source explorer, navigation, and graph controls" width="100%" />
</p>

---

## The pitch

Karpathy's [`llm-wiki.md`](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) sketched the right primitive:

> raw sources stay immutable, an LLM maintains a persistent markdown wiki, and the wiki compounds over time.

LLM-Wiki turns that pattern into a developer product:

- a colored setup wizard instead of a pile of flags;
- a static wiki website instead of loose markdown only;
- an interactive 2D / 3D graph instead of a flat folder;
- local Claude Code / Codex sessions as searchable project memory;
- agent-facing exports your tools can actually consume;
- optional companion-tool refresh, starting with Understand Anything.

In short: **give your agents a memory system that looks good, ships easily, and keeps itself wired to your project.**

---

## Quick start

```bash
pip install llm-wiki
```

Inside any repo:

```bash
llm_wiki project setup
llm_wiki project compile
llm_wiki project build-site
llm_wiki project serve --port 8765
```

Open:

```text
http://127.0.0.1:8765/
```

The setup wizard detects common sources like `README.md`, `docs`, `src`, `data`, and companion artifacts. You choose what to include; LLM-Wiki writes the config.

```text
◆ LLM-Wiki project setup
Choose sources and companion tools. Press Enter to accept defaults.

Sources
  ✓ README.md
  ✓ docs
  ✓ src
  ✓ .llm-wiki/external/understand-anything.md

External tools
  ◆ Understand Anything → .llm-wiki/external/understand-anything.md
```

---

## What you get

<table>
  <tr>
    <td width="50%" valign="top">
      <h3>✨ Wiki website builder</h3>
      <p>Generate a polished static site under <code>.llm-wiki/site/</code> with home, source pages, concepts, papers, repos, syntheses, search, graph, and session routes.</p>
    </td>
    <td width="50%" valign="top">
      <h3>🕸️ 2D / 3D graph view</h3>
      <p>Browse a typed project graph, switch between 2D and 3D, search nodes, filter by type, and publish the same graph as JSON / JSON-LD.</p>
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <h3>🧠 Agent session memory</h3>
      <p>Import Claude Code and Codex sessions into searchable pages with summaries, decisions, files, commands, readable turns, and collapsed tool payloads.</p>
    </td>
    <td width="50%" valign="top">
      <h3>🤝 Companion tools</h3>
      <p>Use independent tools like Understand Anything without vendoring them. Store refresh commands and compile their generated artifacts into the wiki.</p>
    </td>
  </tr>
</table>

---

## Understand Anything + LLM-Wiki

Understand Anything already does a great job generating a code knowledge graph:

```text
.understand-anything/knowledge-graph.json
```

LLM-Wiki does not absorb it. Instead, the setup wizard can treat it as a companion artifact:

1. refresh the external graph if you provide a command;
2. materialize a readable projection at `.llm-wiki/external/understand-anything.md`;
3. compile that projection alongside docs, code, research notes, and agent sessions;
4. publish everything as one agent-native wiki website.

If the refresh command is a shell alias/function, wrap it explicitly:

```bash
zsh -ic 'reunderstand'
```

More: [Understand Anything companion workflow](docs/integrations/understand-anything.md)

---

## Agent-first outputs

The website is the visible layer. The real target is agents.

| Artifact | Use it for |
|---|---|
| `search-index.json` | fast local/project search |
| `graph.json` / `graph.jsonld` | typed relationships |
| `llms.txt` / `llms-full.txt` | context packs |
| per-page `.txt` / `.json` | precise retrievable page context |
| MCP server | `search_nodes`, `node_context`, `timeline`, and friends |
| agent harness export | Claude Code, Codex, Gemini, Cursor, Kiro, OpenCode setup |

---

## Import local agent sessions

```bash
llm_wiki project sessions discover
llm_wiki project sessions discover --import
llm_wiki project build-site
```

Session pages make your agent history part of the project memory instead of leaving it buried in chat logs.

---

## Publish

```bash
llm_wiki project deploy --build --enable-pages
```

The generated site is plain static files, so `.llm-wiki/site/` can also be copied to any web server.

---

## How it works

```text
raw sources + external artifacts + agent sessions
  ↓
setup wizard + configured refresh commands
  ↓
deterministic / optional LLM extraction
  ↓
validated typed graph + markdown projection
  ↓
static website + 2D/3D graph + search + agent exports
```

The controlled ontology is the guardrail. It keeps project memory useful instead of turning everything into random entity soup.

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
  <strong>Stop losing project knowledge in docs, dashboards, and forgotten chats. Compile it into memory your agents can use.</strong>
</p>

<p align="center">
  MIT License &copy; LLM-Wiki Contributors
</p>

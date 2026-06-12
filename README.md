# Tesserae

<p align="center">
  <img src="docs/assets/tesserae-graph-view.png" alt="Tesserae graph view showing concepts, papers, repos, syntheses, and entities clustered around a focused node" width="100%" />
</p>

<p align="center">
  <a href="./README.ko.md">한국어</a> ·
  <a href="./README.zh.md">中文</a> ·
  <a href="./README.ja.md">日本語</a> ·
  <a href="./README.ru.md">Русский</a> ·
  <a href="./README.es.md">Español</a> ·
  <a href="./README.fr.md">Français</a> ·
  <a href="./README.de.md">Deutsch</a>
</p>

> A context engine that keeps a self-improving knowledge base of your project and compiles agent-ready context on demand.

<p align="center">
  <img src="docs/screencasts/showcase.gif" alt="Three-step screencast: tesserae init -> compile -> ask, recorded against the 135-doc demo corpus" width="100%" />
</p>

<p align="center">
  <a href="https://ca1773130n.github.io/Tesserae">Live demo</a> ·
  <a href="docs/">Docs</a> ·
  <a href="docs/release-notes/">Release notes</a> ·
  <a href="docs/integrations/mcp.md">MCP setup</a> ·
  <a href="docs/integrations/obsidian.md">Obsidian export</a>
</p>

## What it is

Point Tesserae at a directory of markdown, source code, and (optionally)
PDFs/Office docs/images. It reconstructs a **typed knowledge graph** of the
project and keeps it fresh, so agents always have grounded, cited context.
Three pillars:

1. **Session monitoring** — your Claude Code / Codex conversations about the
   project become first-class graph nodes (decisions, insights, questions,
   TODOs) as they happen.
2. **Autonomous ingestion** — a supervised engine watches sources and sessions,
   coalesces bursts, recompiles, and a self-improvement sidecar reinforces
   recurring findings while superseding stale ones.
3. **On-demand context** — the context compiler assembles a tailored, cited
   context document for any query or seed node (Personalized PageRank under a
   character budget), ready to paste into any agent.

The graph, the Obsidian vault, and the static site are *projections* of one
knowledge base. Everything runs locally; it is a build step plus a live
engine, not a hosted service.

## Quickstart

Requires **Python 3.10+**.

```bash
pip install tesserae          # add [semantic] for real embeddings
# or: pipx install tesserae   # easiest PATH-safe install
# or: npx @jokerized/tesserae # Node wrapper around the same CLI

cd /path/to/my-project
tesserae init --yes           # wizard; --yes accepts detected defaults
tesserae compile              # build the knowledge graph
tesserae ask "Where is Mermaid rendering implemented?"

# Compile a tailored, cited context doc for a query:
tesserae context "How does the parser handle arXiv IDs?" --budget 32000 -o context.md

tesserae serve --port 8765    # browse the graph + wiki locally
```

LLM-backed features default to the `codex` / `claude` CLIs over OAuth — **no
API keys required** for the common path. See
[docs/quickstart.md](docs/quickstart.md) and
[docs/installation.md](docs/installation.md).

<details>
<summary><strong><code>tesserae: command not found</code> after install? Linux gotchas?</strong></summary>

The most reliable fix on any platform is [`pipx`](https://pipx.pypa.io/):

```bash
# macOS: brew install pipx · Ubuntu/Debian: sudo apt install pipx
pipx ensurepath          # adds ~/.local/bin to PATH; open a new shell after
pipx install tesserae
```

Common Ubuntu issues with plain `pip install tesserae`:

| Error | Cause | Fix |
|---|---|---|
| `error: externally-managed-environment` | PEP 668 — system Python is locked | Use `pipx` (above) or a venv |
| `command not found` after `pip install --user …` | `~/.local/bin` not on `PATH` | `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.bashrc && source ~/.bashrc` |
| `ModuleNotFoundError` on old distros | system `python3` is < 3.10 | `sudo apt install python3.11 python3.11-venv`, then install with `python3.11 -m pip` |

</details>

<details>
<summary><strong>Walkthrough GIFs</strong> — each Quickstart step against the bundled 135-doc demo corpus</summary>

<details>
<summary>1. Setup — point at a research directory, get a project wiki scaffold</summary>
<br/>
<img src="docs/screencasts/setup.gif" alt="tesserae init --source ./research running non-interactively and writing .tesserae/" width="100%" />
</details>

<details>
<summary>2. Compile + build site — deterministic, no LLM calls</summary>
<br/>
<img src="docs/screencasts/compile.gif" alt="tesserae compile followed by tesserae export site, emitting graph.json and the static site tree" width="100%" />
</details>

<details>
<summary>3. Ask — query the compiled wiki from the CLI</summary>
<br/>
<img src="docs/screencasts/ask.gif" alt="tesserae ask --backend wiki returning top-3 hits with score, kind, and outbound relations" width="100%" />
</details>

Rebuild any GIF with `vhs docs/screencasts/<name>.tape`.

</details>

## Everyday commands

Run `tesserae --help` for the full grouped list, `tesserae <cmd> --help` for flags.

| Command | What it does |
|---|---|
| `tesserae init` | Setup wizard → `.tesserae/config.json`. `--yes` non-interactive, `--bare` minimal. |
| `tesserae compile` | Rebuild the knowledge graph and all artifacts. `compile <paths>` ad-hoc ingests extra files. |
| `tesserae ingest <file\|url>` | Merge a single document or web page into the knowledge base without a full recompile (parity-gated incremental fast path). |
| `tesserae context "<query>"` | **On-demand context compiler**: cited context doc via PPR expansion under `--budget`; `--synthesize` adds an LLM summary. |
| `tesserae ask "<question>"` | Ask the compiled knowledge base (`--scope all-registered` fans out across projects). |
| `tesserae engine` | Supervised refresh daemon for the current project: watch, debounce, recompile. |
| `tesserae engine --all` | **Fleet mode**: one process keeps *every* registered project fresh — registry hot-reload, `--compile-slots` throttling. |
| `tesserae refresh` | One-shot pipeline: import new sessions → compile → sync vault. |
| `tesserae sessions discover --import` | Find and import local Claude Code / Codex session history for this project. |
| `tesserae export site` | Build the static site (`--deploy`, `--watch`). |
| `tesserae serve` | Serve the site locally with the inline ask widget (`/api/ask`). |
| `tesserae projects …` | Multi-project registry: `register`, `list`, `activate`, `mcp-config`. |
| `tesserae integrations refresh …` | Re-run companion tools (Understand-Anything, RAG-Anything). |

## Keep it fresh automatically

The engine is what makes the knowledge base *self-improving* rather than a
one-shot build:

```bash
# One project: watch sources + live sessions, recompile on change.
tesserae engine

# Every registered project, one process (v0.8.0):
tesserae engine --all --compile-slots 1
```

Fleet mode reconciles against `~/.tesserae/registry.json` every 10 s —
registering or removing a project takes effect without a restart — and
serializes compiles across projects so concurrent LLM extraction never
tramples shared account rate limits. The first run sweeps your session history
once (it says so in the log); restarts resume from a persisted floor.

## What you get after compile

```text
.tesserae/
  graph.json              # typed nodes/edges (the knowledge base)
  sqlite.db               # queryable graph store
  markdown_projection/    # human-readable wiki pages
  obsidian_vault/         # ready to drop into Obsidian
  site/                   # static site (graph view + wiki + search)
  harness_sessions/       # imported Claude/Codex session memory
  agent_harness/          # per-agent context config (Claude/Codex/Gemini/...)
  cognee_bundle/          # JSONL ready for Cognee ingest
  config.json · manifest.json · report.md · …
```

## MCP server

`tesserae projects mcp-config` prints a server entry for Claude Code, Codex, or
any MCP client. Headline tools:

- **`compile_context`** — tailored, cited context doc for a query or seed nodes
  (deterministic unless `synthesize=true`), backed by `graph_ppr`.
- **Graph + wiki**: `search_nodes`, `node_context`, `graph_summary`,
  `wiki_page`, `raw_source`, `timeline`, `search_facts`, `lint_report`, `ask`.
- **Session memory**: `list_sessions`, `find_session_findings`,
  `find_code_symbol_mentions`, `fresh_insights` (decay-ranked, deduplicated).
- **Registry**: `list_projects`, `register_project`, `activate_project`.

## Multi-project

A registry at `~/.tesserae/registry.json` resolves project names everywhere —
CLI, MCP, and the fleet engine:

```bash
tesserae projects register /path/to/my-project --name myproj
tesserae projects activate myproj
tesserae ask "..." --scope all-registered        # fan out across all projects
```

Markdown in one project can deep-link a node in another via
`wiki://<alias>/<kind>/<slug>`; at compile time these become bridge nodes in
the graph view. See [docs](docs/) for details.

## Integrations (all opt-in)

- **Claude Code plugin** — slash commands, session hooks, skill, and MCP
  auto-registration in one `/plugin install`.
  [docs/integrations/claude-code-plugin.md](docs/integrations/claude-code-plugin.md)
- **Session graph** — Claude Code / Codex conversations → Insight / Decision /
  Question / TODO nodes, linked to the docs they touched. No API key required.
  [docs/integrations/sessions.md](docs/integrations/sessions.md)
- **Understand-Anything** — code knowledge graph ingestion.
  [docs/integrations/understand-anything.md](docs/integrations/understand-anything.md)
- **RAG-Anything** — multimodal ingestion (PDF/Office/images via
  MinerU/Docling) and a LightRAG question backend.
  [docs/integrations/rag-anything.md](docs/integrations/rag-anything.md)
- **Cognee** — graph+vector memory backend; compile always writes a
  Cognee-ready bundle, runtime cognify is best-effort.
- **Obsidian** — bidirectional vault sync with user-edit overlay.
  [docs/integrations/obsidian.md](docs/integrations/obsidian.md)

## How it compares

<details>
<summary>Feature matrix vs Quartz, Logseq, Cognee, Foam</summary>

| Feature | Tesserae | Quartz | Logseq | Cognee | Foam |
|---|---|---|---|---|---|
| Static HTML output | yes | yes | partial (export) | no | partial (publish) |
| Built-in graph view | yes | yes | yes | yes (separate UI) | yes (VSCode) |
| Typed node schema | yes (41 types) | no | partial (tags) | yes | no |
| Concept extraction from sources | yes (LLM) | no | no | yes | no |
| Multimodal ingestion (PDF/image) | yes (via RAG-Anything) | no | partial (embeds) | yes | no |
| Code-graph ingestion | yes | no | no | partial | no |
| MCP server | yes | no | no | yes | no |
| On-demand cited context compiler | yes (PPR + budget) | no | no | no | no |
| Live session monitoring → graph | yes | no | no | no | no |
| Multi-project registry | yes | no | yes (graphs) | partial | no |
| Multi-project daemon (fleet) | yes | no | no | no | no |
| Works without API key (OAuth) | yes | n/a | n/a | no | n/a |
| Deterministic byte-identical compile | yes | yes | n/a | no | n/a |
| Live edit | no | partial | yes | n/a | yes |
| Real-time collaboration | no | no | yes (DB beta) | no | no |

</details>

Tesserae picks compile-from-source over live editing. If you want to edit
notes in a UI, use Logseq or Obsidian. If you want a build tool *and a live
engine* for your knowledge graph, this is the project.

**Use it if** you want a durable, inspectable knowledge graph over a
project's text-heavy sources, a local MCP server grounded in your own files,
or clean bundles for Cognee/Obsidian without writing glue.

**Skip it if** you only need vector search over a small directory, want a
hosted wiki with an editing UI, or expect a turnkey "ask anything" agent —
Tesserae builds the substrate; you wire it into your agent of choice.

## Authentication and LLM providers

The common path uses **no API keys**:

- **Codex CLI** (default) and **Claude Code CLI** over OAuth, with
  multi-account rotation.
- **Embeddings**: native hybrid retrieval uses an offline, torch-free semantic
  lane via `pip install "tesserae[semantic]"` (`model2vec`). Cognee/RAG-Anything
  backends default to a deterministic provider; switch to Ollama or any
  OpenAI-compatible endpoint for better recall.

`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` are picked up if present, never required.

## Status and limitations

Current release: see [release notes](docs/release-notes/). Known limitations:

- First-run compiles over large corpora (thousands of files) take minutes;
  compile time scales roughly linearly. Incremental compile (`--changed-only`)
  ships but is experimental and OFF by default.
- Without the `semantic` extra, hybrid retrieval degrades to a non-semantic
  stub (with a loud warning).
- RAG-Anything vision (image description) is not yet wired end-to-end.
- Cognee runtime cognify is best-effort: missing providers are logged and
  skipped, never fatal.
- The MCP tool set is stable; the graph schema may still gain node types.

## Project layout

```text
tesserae/        # the package (CLI, compiler, engine, MCP server, adapters)
docs/            # English docs + docs/i18n/ for the seven other languages
ontology/        # node/edge schemas the compiler validates against
prompts/         # extraction and synthesis prompts
tests/           # pytest suite
evals/           # graph quality eval harnesses
examples/        # demo corpus used by the screencasts
```

## Localized docs

[한국어](./README.ko.md) ·
[中文](./README.zh.md) ·
[日本語](./README.ja.md) ·
[Русский](./README.ru.md) ·
[Español](./README.es.md) ·
[Français](./README.fr.md) ·
[Deutsch](./README.de.md)

Long-form docs are mirrored under `docs/i18n/`.

## License

MIT. See [LICENSE](LICENSE).

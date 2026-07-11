# RAG-Anything multimodal companion

<!-- translations:start -->
<p align="center"><a href="../i18n/integrations/rag-anything.ko.md">한국어</a> · <a href="../i18n/integrations/rag-anything.zh.md">中文</a> · <a href="../i18n/integrations/rag-anything.ja.md">日本語</a> · <a href="../i18n/integrations/rag-anything.ru.md">Русский</a> · <a href="../i18n/integrations/rag-anything.es.md">Español</a> · <a href="../i18n/integrations/rag-anything.fr.md">Français</a> · <a href="../i18n/integrations/rag-anything.de.md">Deutsch</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) is a multimodal RAG framework (built on LightRAG) that parses PDFs, Office documents, images, and equations through MinerU/Docling/PaddleOCR. Tesserae integrates it both as a multimodal ingestion pipeline (UA-style native graph projection) and as the optional runtime memory backend.

## Why use both?

- Tesserae — long-lived agent memory, wiki compilation, graph projection.
- RAG-Anything — multimodal ingestion + LightRAG runtime retrieval.

The two complement each other: RAG-Anything brings PDF/Office/image understanding that Tesserae's text-first source loaders don't provide; Tesserae keeps the long-lived, queryable memory that survives across sessions.

## Current low-friction workflow

The recommended path is the setup wizard:

```bash
tesserae init
```

RAG-Anything is now an **interactive wizard prompt** rather than a set of CLI
flags. When the wizard runs, answer the integration prompts:

- enable RAG-Anything when prompted;
- install it when asked (installs `raganything` + `docling`);
- pick the parser `mineru`;
- enable the post-install refresh run when offered.

Then compile:

```bash
tesserae compile
```

For non-interactive automation (CI), run the wizard with defaults (all
optional integrations OFF), then enable RAG-Anything in `.tesserae/config.json`
— the wizard writes integration config under the `external_tools` /
`memory_backends` keys (see the keys this doc references below) — and run the
managed refresh:

```bash
tesserae init --yes
# enable raganything in .tesserae/config.json (external_tools key)
tesserae integrations refresh raganything --parser mineru
tesserae compile
```

The setup wizard installs both `raganything` and `docling` together. MinerU stays opt-in: install it with `pip install 'mineru[core]'` only if you have PDFs or images to ingest.

Tesserae stores a managed refresh command rather than asking users to invent one:

```bash
tesserae integrations refresh raganything --parser mineru
```

During compile, Tesserae:

1. checks whether `.tesserae/external/raganything/manifest.json` exists and matches the current git commit (via the stored `meta.json#gitCommitHash`);
2. runs the managed refresh wrapper if missing/stale or `--refresh-external-tools` is passed;
3. discovers non-code sources (PDFs, Office docs, images, markdown) and parses them via the configured parser;
4. writes `manifest.json` + `meta.json`;
5. continues the normal memory compile.

You can force all configured external refresh commands before a compile:

```bash
tesserae compile --refresh-integrations
```

## Manual equivalent

```bash
pip install 'raganything[all]'
python -m tesserae.raganything_refresh --project . --parser mineru
tesserae compile
```

## Compile-time vs runtime

Tesserae splits the integration cleanly:

- **Compile-time parsing** (`refresh-raganything` and `compile`): runs parsers directly — native read for `.md/.txt/.rst`, `docling.DocumentConverter` for everything else. RAG-Anything's full pipeline is *not* invoked here, so no LLM/embedding/vision keys are needed for compile to succeed.
- **Runtime queries** (`project ask`): `raganything_query.py` instantiates `RAGAnything` with the project's configured LLM/embedding/vision functions and runs `aquery` against LightRAG's store. This path requires API keys.

The split means `compile` is fast, deterministic, and key-free; only retrieval-time operations cost LLM tokens.

## Native graph synchronization

Tesserae imports the parsed manifest natively during compile when the configured tool uses `sync_mode: native_graph`.

The native adapter reads `.tesserae/external/raganything/manifest.json`, projects each parsed document into a `SourceFile` node with multimodal block metadata, and writes a sync manifest:

```text
.tesserae/external/raganything-sync.json
```

Current mapping:

| RAG-Anything | Tesserae direction |
|---|---|
| `documents[*]` | `SourceFile` node, `metadata.parser="raganything"` |
| `content_list[type=text]` | folded into `SourceFile.description`; concepts via existing extractor |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` and `metadata.equations[]` (LaTeX preserved) |

Provenance is preserved on each node:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".tesserae/external/raganything/manifest.json"}
```

Note: the interactive graph view hides `sources`-group nodes by default to focus on concepts and entities — projected raganything SourceDocuments stay in `graph.json` (MCP, search, per-page wiki views still see them), they just don't flood the canvas. Set `graph_view.show_sources = true` in `.tesserae/config.json` to restore the dense view.

## Runtime memory backend

`memory_backends.raganything` (default produced by `default_raganything_backend_config`) is the only optional memory backend. RAG-Anything is opt-in (default `enabled: false`); the setup flag `--with-raganything` flips it on.

### LLM provider (no API key needed)

RAG-Anything's runtime backend needs an LLM to answer queries. Tesserae defaults to its existing OAuth-based CLI integrations — no API key required:

| Provider | How it authenticates | Setup flag |
|---|---|---|
| `codex` (default) | `codex` CLI OAuth (you logged in once with `codex login`) | `--raganything-llm-provider codex` |
| `claude` | `claude -p` CLI; respects `CLAUDE_CONFIG_DIR` for multi-account setups | `--raganything-llm-provider claude --raganything-claude-config-dir ~/.claude-personal2` |

For multi-account Claude setups (e.g., `~/.claude-personal1`, `~/.claude-personal2`), pass `--raganything-claude-config-dir <path>` at setup. The runtime backend will export `CLAUDE_CONFIG_DIR=<path>` before each invocation so the chosen account's auth is used without touching your default `~/.claude`.

### Embeddings

| Provider | When to use |
|---|---|
| `deterministic` (default) | No external deps. Hash-based; low semantic quality but enough for LightRAG to construct an index. Good baseline for proving the integration works. |
| `ollama` | Local Ollama running with an embedding model (e.g., `nomic-embed-text`). Pass `--raganything-embedding ollama`; the backend defaults to `http://localhost:11434`. |

Direct OpenAI embedding support is not wired through these flags in v1 — users with OpenAI keys can set `OPENAI_API_KEY` and override `memory_backends.raganything.embedding.provider` directly in `.tesserae/config.json` (RAGAnything will pick up the env var via LightRAG's defaults).

### Invoking from the CLI

```bash
# `ask` never enters memory backends: it plans retrieval over the compiled
# graph and synthesizes a cited LLM answer (--no-llm = ranked hits only).
tesserae ask "What does the integration spec say about parser routing?"

# Explicit backends live on `query` — raw retrieval, no LLM synthesis.
tesserae query "..." --backend raganything
tesserae query "..." --backend wiki
```

`tesserae query --backend raganything` calls `tesserae.raganything_query.query` directly. A relative `working_dir` in `memory_backends.raganything` is resolved against the project root before the call.

### Top-level `ask` (uses the multi-project registry)

For workflows where you want to ask across multiple registered Tesserae projects without `cd`-ing into each one, the top-level `tesserae ask` command resolves the project via the persistent registry shared with the MCP server:

```bash
# One-time: register your projects (saved to ~/.tesserae/registry.json).
tesserae projects register ~/Developer/Projects/Tesserae --name tesserae
tesserae projects register ~/Developer/Projects/Other --name other

# List registered projects.
tesserae projects list

# Ask from inside a project (the smart router picks the target otherwise).
tesserae ask "How does the parser routing work?"

# Ask a specific registered project by name.
tesserae ask "What is the architecture?" --name other

# Pass a direct path, or hit a memory backend explicitly via `query`.
tesserae ask "..." --project /tmp/somewhere
tesserae query "..." --backend raganything --json
```

The dispatch logic — `--project > --name > router` — is implemented in the top-level ask handler and the answer formatting is shared with the MCP `ask` tool through `tesserae.query.ask_project` (memory backends are reachable only through `tesserae query --backend …`). The registry is file-backed (`~/.tesserae/registry.json` by default), so it persists across sessions and stays in sync with the MCP server's project list.

#### Querying across multiple vaults (`--scope all-registered`)

Bet B2 — when you have several registered projects (research vault, work vault, side-project vault) and you want to ask the same question against all of them, use `--scope all-registered`:

```bash
# Fan out across every registered project. The aggregated envelope is
# {"scope": "all-registered", "question": "...", "by_project": {"<alias>": <envelope>}}.
tesserae ask "What did I write about RLHF?" --scope all-registered --json

# Restrict to a hand-picked subset of aliases.
tesserae ask "..." --scope all-registered --scope-aliases research side-projects
```

The handler iterates registered projects in alphabetical order, calls `ask_project` against each, and aggregates the per-project envelopes. A single project failing — missing config, RAG-Anything not enabled — is captured as `{"error": "..."}` in that alias's slot and never aborts the rest of the fan-out. The same `scope` argument is accepted by the MCP `ask` tool, so MCP-driven coding agents get the same fan-out without extra plumbing.

### Multi-project registry (`tesserae projects`)

| Command | Purpose |
| --- | --- |
| `tesserae projects list [--json]` | Show registered projects (all are equal — there is no "active" one). |
| `tesserae projects register <path> [--name <alias>]` | Add a project to the registry; alias defaults to the sanitized directory name. |
| `tesserae projects unregister <name>` | Remove an entry from the registry. |

These commands operate directly on `tesserae.mcp_server.ProjectRegistry` — no MCP roundtrip — so they can be scripted without running the MCP server.

### Invoking from MCP

The stdio MCP server exposes an `ask` tool with the same backend selector:

```json
{
  "name": "ask",
  "arguments": {
    "question": "What does the integration spec say about parser routing?",
    "backend": "auto",
    "project": "tesserae"
  }
}
```

The dispatch order (`raganything` → compiled-wiki search) and `working_dir` resolution mirror the CLI handler exactly, so coding agents and human operators converge on the same answers.

## System prerequisites

- **Python 3.10+** is required for RAG-Anything (the upstream `raganything` package ≥1.3.0 transitively depends on `mineru[core]`, which is Python 3.10+). On older Pythons Tesserae disables the integration with a clear warning rather than silently installing a broken placeholder.
- **LibreOffice** for `.doc/.docx/.ppt/.pptx/.xls/.xlsx` parsing — install separately via your platform's package manager. RAG-Anything skips Office documents with a warning when LibreOffice is missing.
- **MinerU model weights** are downloaded on first parse and cached (~GBs). Subsequent runs reuse the cache.
- **OpenAI-compatible LLM/embedding/vision keys** for the runtime memory backend (`OPENAI_API_KEY`, `OPENAI_BASE_URL`). Parser-only mode does not require keys.

## Parser routing

Tesserae auto-routes sources to the right parser per file extension:

| Extension | Parser | Reason |
|---|---|---|
| `.md`, `.markdown`, `.txt`, `.rst` | `docling` | Lightweight; no MinerU model download. |
| `.doc`, `.docx`, `.ppt`, `.pptx`, `.xls`, `.xlsx` | `docling` | Better Office structure preservation per upstream. |
| `.pdf`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.tiff`, `.webp` | configured default (`--raganything-parser`, default `mineru`) | OCR + table extraction. |

The managed `tesserae integrations refresh raganything` wrapper exposes `--parser` (the configured default for PDFs/images), `--parse-method {auto,ocr,txt}`, `--root` (repeatable, restrict to a subtree), `--force`, and `--full`. Per-bucket text/office routing is fixed (both default to `docling`). To override the text or office parser explicitly, call the underlying module directly — `python -m tesserae.raganything_refresh --text-parser <p> --office-parser <p>` — which exposes those two extra flags. The configured default still applies to PDFs and images.

Before the parse loop runs, Tesserae probes whether each required parser's Python package is importable (`importlib.import_module(...)`) and bails fast with a single aggregated error listing every missing parser and its install command. We deliberately don't use upstream `RAGAnything.check_parser_installation()` because it only inspects the parser configured on the instance and folds in model-weight readiness checks that don't fit a pre-flight stage.

Tesserae also picks `RAGAnything`'s construction-time parser from the actual routing distribution (most-common picked parser wins) rather than from `--raganything-parser` directly. This avoids the failure mode where `RAGAnything.__init__` tries to initialize a heavy parser (e.g. `mineru`) whose model weights aren't yet on disk and brick the entire run before per-call `parser=` overrides can take effect. The `--raganything-parser` flag still controls the default for non-text, non-Office sources (PDFs, images).

### Parser packages

The compile-time parse path uses `docling.DocumentConverter` directly for every non-text source; install it once and you're covered:

| Parser | Install command |
|---|---|
| `docling` (compile-time default for everything except native text) | bundled when you run `--with-raganything --install-raganything` (or `pip install docling` standalone) |
| `paddleocr` (optional OCR alternative) | `pip install 'raganything[paddleocr]>=1.3.0'` and `pip install paddlepaddle` (platform-specific wheel) |

> Note: `mineru` is currently **not invoked at compile-time**. The compile path bypasses RAG-Anything's full pipeline (which would require LLM/embedding/vision callables) and routes every non-text source through docling directly. MinerU support is reserved for a future direct-import path that ingests an externally-produced `content_list.json`.

When a configured parser is missing, `refresh-raganything` bails fast — listing every missing parser in a single error with the right install command — instead of cascading per-file failures.

### Per-page ask widget

Every detail page (concept, paper, repo, synthesis, entity, topic, question, source) includes an inline "ask about this page" widget. It POSTs to `/api/ask` on the local `tesserae serve` instance, which calls `tesserae.query.ask_project` and renders the answer inline. Unlike the CLI (where `tesserae ask` is LLM-by-default), `/api/ask` defaults to **non-LLM retrieval** for widget latency; send `{"llm": true}` in the payload to opt into the planned/synthesized answer. The widget prepends the current page's node name to the user's question as a natural-language context hint (e.g. `` About `<NodeName>`: <question> ``); a future PR can wire real subgraph scoping into `ask_project` itself.

The widget detects backend availability via `/api/ask/health` on load. When the wiki is served statically (GitHub Pages, `file://`, S3, any plain static host) the widget collapses to a one-line note pointing readers at `tesserae serve` for local interactive use. No requests fail and nothing blocks page rendering — the widget is a deferred JS island, separate from the heavier graph bundle.

Pair this with the multi-project registry (`tesserae projects register`) and you can ask any registered project's wiki from any of its detail pages.

## Collaboration principle

Tesserae remains the memory compiler. RAG-Anything remains an independent companion: a multimodal parser + LightRAG retrieval engine.

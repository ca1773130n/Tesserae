# RAG-Anything multimodal companion

<!-- translations:start -->
<p align="center"><a href="../i18n/integrations/rag-anything.ko.md">한국어</a> · <a href="../i18n/integrations/rag-anything.zh.md">中文</a> · <a href="../i18n/integrations/rag-anything.ja.md">日本語</a> · <a href="../i18n/integrations/rag-anything.ru.md">Русский</a> · <a href="../i18n/integrations/rag-anything.es.md">Español</a> · <a href="../i18n/integrations/rag-anything.fr.md">Français</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) is a multimodal RAG framework (built on LightRAG) that parses PDFs, Office documents, images, and equations through MinerU/Docling/PaddleOCR. LLM-Wiki integrates it both as a multimodal ingestion pipeline (UA-style native graph projection) and as a runtime memory backend alongside Cognee.

## Why use both?

- LLM-Wiki — long-lived agent memory, wiki compilation, graph projection.
- RAG-Anything — multimodal ingestion + LightRAG runtime retrieval.

The two complement each other: RAG-Anything brings PDF/Office/image understanding that LLM-Wiki's text-first source loaders don't provide; LLM-Wiki keeps the long-lived, queryable memory that survives across sessions.

## Current low-friction workflow

The recommended path is the setup wizard:

```bash
llm_wiki project setup
```

For automation:

```bash
llm_wiki project setup \
  --yes \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything
llm_wiki project compile
```

The setup wizard installs both `raganything` and `docling` together. MinerU stays opt-in: install it with `pip install 'mineru[core]'` only if you have PDFs or images to ingest.

LLM-Wiki stores a managed refresh command rather than asking users to invent one:

```bash
llm_wiki project refresh-raganything --parser mineru
```

During compile, LLM-Wiki:

1. checks whether `.llm-wiki/external/raganything/manifest.json` exists and matches the current git commit (via the stored `meta.json#gitCommitHash`);
2. runs the managed refresh wrapper if missing/stale or `--refresh-external-tools` is passed;
3. discovers non-code sources (PDFs, Office docs, images, markdown) and parses them via the configured parser;
4. writes `manifest.json` + `meta.json`;
5. continues the normal memory compile.

You can force all configured external refresh commands before a compile:

```bash
llm_wiki project compile --refresh-external-tools
```

## Manual equivalent

```bash
pip install 'raganything[all]'
python -m llm_wiki.raganything_refresh --project . --parser mineru
llm_wiki project compile
```

## Compile-time vs runtime

LLM-Wiki splits the integration cleanly:

- **Compile-time parsing** (`refresh-raganything` and `compile`): runs parsers directly — native read for `.md/.txt/.rst`, `docling.DocumentConverter` for everything else. RAG-Anything's full pipeline is *not* invoked here, so no LLM/embedding/vision keys are needed for compile to succeed.
- **Runtime queries** (`project ask`): `raganything_query.py` instantiates `RAGAnything` with the project's configured LLM/embedding/vision functions and runs `aquery` against LightRAG's store. This path requires API keys.

The split means `compile` is fast, deterministic, and key-free; only retrieval-time operations cost LLM tokens.

## Native graph synchronization

LLM-Wiki imports the parsed manifest natively during compile when the configured tool uses `sync_mode: native_graph`.

The native adapter reads `.llm-wiki/external/raganything/manifest.json`, projects each parsed document into a `SourceFile` node with multimodal block metadata, and writes a sync manifest:

```text
.llm-wiki/external/raganything-sync.json
```

Current mapping:

| RAG-Anything | LLM-Wiki direction |
|---|---|
| `documents[*]` | `SourceFile` node, `metadata.parser="raganything"` |
| `content_list[type=text]` | folded into `SourceFile.description`; concepts via existing extractor |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` and `metadata.equations[]` (LaTeX preserved) |

Provenance is preserved on each node:

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".llm-wiki/external/raganything/manifest.json"}
```

Note: the interactive graph view hides `sources`-group nodes by default to focus on concepts and entities — projected raganything SourceDocuments stay in `graph.json` (MCP, Cognee, search, per-page wiki views still see them), they just don't flood the canvas. Set `graph_view.show_sources = true` in `.llm-wiki/config.json` to restore the dense view.

## Runtime memory backend

`memory_backends.raganything` (default produced by `default_raganything_backend_config`) coexists with Cognee. `project ask` tries backends in priority order; per-project priority can be set via `memory_backends.priority`. RAG-Anything is opt-in (default `enabled: false`); the setup flag `--with-raganything` flips it on.

### LLM provider (no API key needed)

RAG-Anything's runtime backend needs an LLM to answer queries. LLM-Wiki defaults to its existing OAuth-based CLI integrations — no API key required:

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

Direct OpenAI embedding support is not wired through these flags in v1 — users with OpenAI keys can set `OPENAI_API_KEY` and override `memory_backends.raganything.embedding.provider` directly in `.llm-wiki/config.json` (RAGAnything will pick up the env var via LightRAG's defaults).

### Invoking from the CLI

```bash
# Auto mode: tries RAG-Anything (when enabled), then Cognee, then compiled-wiki search.
llm_wiki project ask "What does the integration spec say about parser routing?"

# Force a specific backend.
llm_wiki project ask "..." --backend raganything
llm_wiki project ask "..." --backend cognee
llm_wiki project ask "..." --backend wiki
```

`--backend raganything` calls `llm_wiki.raganything_query.query` directly. A relative `working_dir` in `memory_backends.raganything` is resolved against the project root before the call.

### Top-level `ask` (uses the multi-project registry)

For workflows where you want to ask across multiple registered LLM-Wiki projects without `cd`-ing into each one, the top-level `llm_wiki ask` command resolves the project via the persistent registry shared with the MCP server:

```bash
# One-time: register your projects (saved to ~/.llm-wiki/registry.json).
llm_wiki wiki register ~/Developer/Projects/LLM-Wiki --name llm-wiki --activate
llm_wiki wiki register ~/Developer/Projects/Other --name other

# List registered projects.
llm_wiki wiki list

# Ask the currently active project.
llm_wiki ask "How does the parser routing work?"

# Ask a specific registered project (no need to activate it).
llm_wiki ask "What is the architecture?" --wiki other

# Force a backend or pass a direct path.
llm_wiki ask "..." --wiki llm-wiki --backend raganything --json
llm_wiki ask "..." --project /tmp/somewhere
```

The dispatch logic — `--project > --wiki > active project` — is implemented in `_top_level_ask_handler` and the answer formatting / backend selection is shared with `project ask` and the MCP `ask` tool through `llm_wiki.query.ask_project`. The registry is file-backed (`~/.llm-wiki/registry.json` by default), so it persists across sessions and stays in sync with the MCP server's project list.

### Multi-project registry (`llm_wiki wiki`)

| Command | Purpose |
| --- | --- |
| `llm_wiki wiki list [--json]` | Show registered projects and which one is active. |
| `llm_wiki wiki register <path> [--name <alias>] [--activate]` | Add a project to the registry; alias defaults to the sanitized directory name. |
| `llm_wiki wiki activate <name>` | Mark an entry as the active project for subsequent `llm_wiki ask` calls without `--wiki`. |
| `llm_wiki wiki unregister <name>` | Remove an entry; clears the active pointer when it matched. |

These commands operate directly on `llm_wiki.mcp_server.ProjectRegistry` — no MCP roundtrip — so they can be scripted without running the MCP server.

### Invoking from MCP

The stdio MCP server exposes an `ask` tool with the same backend selector:

```json
{
  "name": "ask",
  "arguments": {
    "question": "What does the integration spec say about parser routing?",
    "backend": "auto",
    "project": "llm_wiki"
  }
}
```

The dispatch order (`raganything` → `cognee` → compiled-wiki search) and `working_dir` resolution mirror the CLI handler exactly, so coding agents and human operators converge on the same answers.

## System prerequisites

- **Python 3.10+** is required for RAG-Anything (the upstream `raganything` package ≥1.3.0 transitively depends on `mineru[core]`, which is Python 3.10+). On older Pythons LLM-Wiki disables the integration with a clear warning rather than silently installing a broken placeholder.
- **LibreOffice** for `.doc/.docx/.ppt/.pptx/.xls/.xlsx` parsing — install separately via your platform's package manager. RAG-Anything skips Office documents with a warning when LibreOffice is missing.
- **MinerU model weights** are downloaded on first parse and cached (~GBs). Subsequent runs reuse the cache.
- **OpenAI-compatible LLM/embedding/vision keys** for the runtime memory backend (`OPENAI_API_KEY`, `OPENAI_BASE_URL`). Parser-only mode does not require keys.

## Parser routing

LLM-Wiki auto-routes sources to the right parser per file extension:

| Extension | Parser | Reason |
|---|---|---|
| `.md`, `.markdown`, `.txt`, `.rst` | `docling` | Lightweight; no MinerU model download. |
| `.doc`, `.docx`, `.ppt`, `.pptx`, `.xls`, `.xlsx` | `docling` | Better Office structure preservation per upstream. |
| `.pdf`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`, `.tiff`, `.webp` | configured default (`--raganything-parser`, default `mineru`) | OCR + table extraction. |

Override per-bucket with `--text-parser` and `--office-parser` on `refresh-raganything`. The configured default still applies to PDFs and images.

Before the parse loop runs, LLM-Wiki probes whether each required parser's Python package is importable (`importlib.import_module(...)`) and bails fast with a single aggregated error listing every missing parser and its install command. We deliberately don't use upstream `RAGAnything.check_parser_installation()` because it only inspects the parser configured on the instance and folds in model-weight readiness checks that don't fit a pre-flight stage.

LLM-Wiki also picks `RAGAnything`'s construction-time parser from the actual routing distribution (most-common picked parser wins) rather than from `--raganything-parser` directly. This avoids the failure mode where `RAGAnything.__init__` tries to initialize a heavy parser (e.g. `mineru`) whose model weights aren't yet on disk and brick the entire run before per-call `parser=` overrides can take effect. The `--raganything-parser` flag still controls the default for non-text, non-Office sources (PDFs, images).

### Parser packages

The compile-time parse path uses `docling.DocumentConverter` directly for every non-text source; install it once and you're covered:

| Parser | Install command |
|---|---|
| `docling` (compile-time default for everything except native text) | bundled when you run `--with-raganything --install-raganything` (or `pip install docling` standalone) |
| `paddleocr` (optional OCR alternative) | `pip install 'raganything[paddleocr]>=1.3.0'` and `pip install paddlepaddle` (platform-specific wheel) |

> Note: `mineru` is currently **not invoked at compile-time**. The compile path bypasses RAG-Anything's full pipeline (which would require LLM/embedding/vision callables) and routes every non-text source through docling directly. MinerU support is reserved for a future direct-import path that ingests an externally-produced `content_list.json`.

When a configured parser is missing, `refresh-raganything` bails fast — listing every missing parser in a single error with the right install command — instead of cascading per-file failures.

### Per-page ask widget

Every detail page (concept, paper, repo, synthesis, entity, topic, question, source) includes an inline "ask about this page" widget. It POSTs to `/api/ask` on the local `llm_wiki project serve` instance, which calls `llm_wiki.query.ask_project` and renders the answer inline. The widget prepends the current page's node name to the user's question as a natural-language context hint (e.g. `` About `<NodeName>`: <question> ``); a future PR can wire real subgraph scoping into `ask_project` itself.

The widget detects backend availability via `/api/ask/health` on load. When the wiki is served statically (GitHub Pages, `file://`, S3, any plain static host) the widget collapses to a one-line note pointing readers at `llm_wiki project serve` for local interactive use. No requests fail and nothing blocks page rendering — the widget is a deferred JS island, separate from the heavier graph bundle.

Pair this with the multi-project registry (`llm_wiki wiki register`) and you can ask any registered project's wiki from any of its detail pages.

## Collaboration principle

LLM-Wiki remains the memory compiler. RAG-Anything remains an independent companion: a multimodal parser + LightRAG retrieval engine.

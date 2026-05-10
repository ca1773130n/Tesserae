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

## Runtime memory backend

`memory_backends.raganything` (default produced by `default_raganything_backend_config`) coexists with Cognee. `project ask` tries backends in priority order; per-project priority can be set via `memory_backends.priority`. RAG-Anything is opt-in (default `enabled: false`); the setup flag `--with-raganything` flips it on.

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

Before the parse loop runs, LLM-Wiki calls `RAGAnything.check_parser_installation()` for each parser actually needed by the discovered sources and bails fast with an install hint when one is missing — no more cascading per-file errors.

## Collaboration principle

LLM-Wiki remains the memory compiler. RAG-Anything remains an independent companion: a multimodal parser + LightRAG retrieval engine.

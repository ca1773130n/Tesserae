# RAG-Anything Integration — Design Spec

**Status:** Draft (pre-implementation)
**Date:** 2026-05-10
**Author:** brainstorming session (autopilot mode)
**Companion projects:** [Understand Anything](https://github.com/Lum1104/Understand-Anything) (existing), [Cognee](https://github.com/topoteretes/cognee) (existing), [RAG-Anything](https://github.com/HKUDS/RAG-Anything) (new)

## 1. Goal

Integrate [HKUDS/RAG-Anything](https://github.com/HKUDS/RAG-Anything) (PyPI: `raganything`, built on LightRAG) into LLM-Wiki as **both**:

1. A **multimodal ingestion pipeline** that parses non-code sources (PDFs, Office docs, images, equations, plain text/markdown) via MinerU/Docling/PaddleOCR and projects the resulting `content_list` into the unified `ResearchGraph` during `project compile`. (UA-style "companion graph" pattern.)
2. A **runtime memory backend** registered alongside Cognee in `.llm-wiki/config.json#memory_backends`, so `project ask` can query RAG-Anything's LightRAG store with multimodal awareness.

The integration must mirror the existing UA pattern (`llm_wiki/understand_anything_adapter.py` + `understand_anything_refresh.py` + `project_setup.py` flags + `external_tools` config + manifest with stable id mapping) so the codebase remains consistent.

## 2. Non-Goals (v1)

- Auto-installing system dependencies (LibreOffice, paddlepaddle). v1 detects + warns.
- Routing source code (`.py`/`.ts`/etc.) through RAG-Anything. `CodeGraphExtractor` remains canonical for code; RAG-Anything ingests non-code only.
- Introducing new ontology node types for figures/tables. v1 attaches multimodal blocks as `metadata.multimodal_blocks` on existing `SourceFile` nodes; concept extraction reuses the existing canonicalization path.
- Replacing the markdown source loader. RAG-Anything runs *in addition to* existing source loaders for non-code; deduplication is metadata-merge based (same pattern as UA).
- Vendoring or forking RAG-Anything. It is an external pip-installed companion (just like Cognee and UA).

## 3. Architectural Role

| Concern | Owner |
|---|---|
| AST-aware code analysis | `CodeGraphExtractor` (existing) |
| Markdown text extraction (light path) | existing source loaders + `ResearchGraphExtractor` |
| **Multimodal parsing (PDF/Office/images)** | **RAG-Anything (new)** |
| Companion code-knowledge graph | Understand Anything (existing) |
| Runtime memory backend (default) | Cognee (existing) |
| **Runtime memory backend (multimodal)** | **RAG-Anything (new)** |
| Wiki/static publishing | LLM-Wiki (existing) |

RAG-Anything's storage stays under `.llm-wiki/external/raganything/` and never spills into the project root.

## 4. Storage Layout

```
<project>/.llm-wiki/external/raganything/
├── working_dir/        # LightRAG vector + KG storage (managed by raganything)
├── parsed/             # MinerU/Docling/PaddleOCR per-document outputs
│   └── <doc-sha256>/   # one dir per source doc, contains content_list.json + media
├── manifest.json       # adapter-readable artifact (top-level)
├── meta.json           # gitCommitHash, mineru_version, parser, parser_version, last_run
└── raganything-sync.json  # mapping written by adapter (UA-equivalent of *-sync.json)
```

`manifest.json` is the artifact the adapter consumes. It is produced by the refresh wrapper (`raganything_refresh.py`) and has the shape:

```json
{
  "version": 1,
  "project": {"name": "<project>", "root": "."},
  "parser": "mineru",
  "parser_version": "2.x.x",
  "git_commit": "<sha>",
  "documents": [
    {
      "id": "doc-<sha256>",
      "path": "docs/whitepaper.pdf",
      "sha256": "...",
      "parsed_dir": ".llm-wiki/external/raganything/parsed/<sha256>",
      "content_list": [
        {"type": "text",     "page_idx": 0, "text": "..."},
        {"type": "image",    "page_idx": 1, "img_path": "...", "img_caption": [...]},
        {"type": "table",    "page_idx": 2, "table_body": "...", "table_caption": [...]},
        {"type": "equation", "page_idx": 3, "latex": "...", "equation_caption": [...]}
      ]
    }
  ]
}
```

## 5. Components

### 5.1 New files

| Path | Purpose | LOC ≈ |
|---|---|---|
| `llm_wiki/raganything_adapter.py` | Pure-Python adapter: reads `manifest.json` and produces `ResearchGraph` nodes/edges with `external_refs` provenance. Mirrors `understand_anything_adapter.py`. | 280 |
| `llm_wiki/raganything_refresh.py` | Managed wrapper: detects staleness via git head + `meta.json`, runs RAG-Anything async pipeline (`process_folder_complete`) over discovered non-code sources, writes manifest. Mirrors `understand_anything_refresh.py`. | 240 |
| `llm_wiki/raganything_query.py` | Thin async query bridge for `project ask` / runtime-memory backend role. Wraps `RAGAnything.aquery` / `aquery_with_multimodal`. | 90 |
| `tests/test_raganything_adapter.py` | Adapter unit tests with synthetic manifest payloads (mirrors `test_understand_anything_adapter.py`). | 160 |
| `tests/test_raganything_refresh.py` | Refresh wrapper tests (subprocess mocked; staleness logic). | 80 |
| `tests/test_raganything_query.py` | Query backend tests (RAGAnything mocked). | 60 |
| `tests/test_project_setup_raganything.py` | Wizard flag tests. | 80 |
| `docs/integrations/rag-anything.md` | Integration doc (English). | 200 |
| `docs/i18n/integrations/rag-anything.{ko,zh,ja,ru,es,fr}.md` | Localized integration docs. | 6 × 200 |

### 5.2 Modified files

| Path | Change |
|---|---|
| `llm_wiki/project.py` | Add `_merge_configured_raganything_graph(graph, cfg)` parallel to `_merge_configured_understand_anything_graph`. Add `default_raganything_backend_config()`. Wire into `compile()` and `_load_memory_backend_config()`. |
| `llm_wiki/project_setup.py` | Add `include_raganything`, `install_raganything`, `raganything_parser`, `raganything_extras`, `run_raganything` parameters to `build_setup_plan()`. Append a second entry to `external_tools` (id=`raganything`, sync_mode=`native_graph`). Default-add to `memory_backends`. |
| `llm_wiki/cli.py` | New `--with-raganything`, `--skip-raganything`, `--install-raganything`, `--skip-install-raganything`, `--raganything-parser`, `--raganything-extras`, `--run-raganything` flags. New subcommand `project refresh-raganything`. Plumb through to `build_setup_plan`. |
| `pyproject.toml` | Add optional extras `[project.optional-dependencies] raganything = ["raganything>=1.0"]` and `raganything-all = ["raganything[all]>=1.0"]`. |
| `.gitignore` | Add `.llm-wiki/external/raganything/working_dir/` and `.llm-wiki/external/raganything/parsed/` (artifacts that can be regenerated). Keep `manifest.json` + `meta.json` tracked-when-committed (default committed=false; user choice). |
| `README.md` + 6 i18n | Add a new bullet under integrations and a flag block in setup snippet. |
| `docs/quickstart.md` | Add `--with-raganything --install-raganything --raganything-parser=mineru` to the optional-integrations recipe. |
| `docs/installation.md` | Add `pip install 'llm-wiki[raganything-all]'` extras and LibreOffice prerequisite. |
| `docs/publishing-checklist.md` | Add a checkbox for "RAG-Anything index refreshed" when the tool is enabled. |
| `docs/self-dogfood.md` | Mention dogfood path (running RAG-Anything against LLM-Wiki's own docs). |

## 6. Adapter Mapping (`content_list` → `ResearchGraph`)

| `content_list` item | `ResearchNode` |
|---|---|
| Per-document root | `SourceFile` node (id seed = `doc:<sha256>`), `metadata.parser="raganything"`, `metadata.multimodal_blocks=[…]` |
| `text` block | folded into `SourceFile.description` (concatenated, page-indexed); concepts extracted via existing `ResearchGraphExtractor` path |
| `image` block | added to `SourceFile.metadata.multimodal_blocks` with `{type:"image", page, img_path, caption}`. Caption text routed through concept extraction. |
| `table` block | added to `multimodal_blocks` `{type:"table", page, table_body_md, caption}`. Caption + headers routed through concept extraction. |
| `equation` block | added to `multimodal_blocks` `{type:"equation", latex, caption}`. Caption routed through concept extraction; latex preserved verbatim. |

| `content_list` relation | `ResearchEdge` |
|---|---|
| Source contains its blocks | `contains` (SourceFile → derived Concept nodes from captions) |
| Image/table/equation has caption referencing concept already in graph | `documents` |
| Cross-document concept reuse | `shares_concept_with` (canonicalized via `normalize_display_name`) |

`metadata.external_refs` schema (mirrors UA):

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".llm-wiki/external/raganything/manifest.json"}
```

When a Concept already exists from another source (markdown loader, UA, code), the existing node is preserved and `external_refs` is appended. This reuses `_add_or_merge_node` semantics from `understand_anything_adapter.py`.

## 7. Data Flow

### 7.1 Compile path

```
project compile
  ├─ load .llm-wiki/config.json
  ├─ if external_tools[id=raganything].auto_refresh → raganything_refresh.run()
  │     └─ if stale (git head ≠ meta.gitCommitHash OR manifest missing):
  │         ├─ asyncio.run(RAGAnything(...).process_folder_complete(...))
  │         ├─ collect content_lists → write manifest.json + meta.json
  │         └─ write parsed/<sha>/ per doc
  ├─ run existing source loaders + ResearchGraphExtractor → graph₀
  ├─ _merge_configured_understand_anything_graph(graph₀, cfg) → graph₁
  ├─ _merge_configured_raganything_graph(graph₁, cfg) → graph₂   ← NEW
  ├─ wiki_projector.partition_graph(graph₂) → wiki pages, exports
  └─ write outputs
```

### 7.2 Ask path

```
project ask "<question>"
  ├─ resolve memory_backends.priority (default: ["raganything","cognee","wiki_search"])
  ├─ for each backend: try until one returns non-empty answer (best-effort fallback)
  │     ├─ raganything → asyncio.run(RAGAnything(...).aquery(question, mode="hybrid"))
  │     ├─ cognee     → existing cognee_query
  │     └─ wiki_search→ existing compiled-wiki search
  └─ render answer
```

`memory_backends.raganything` config (default produced by `default_raganything_backend_config`):

```json
{
  "enabled": true,
  "working_dir": ".llm-wiki/external/raganything/working_dir",
  "parser": "mineru",
  "parse_method": "auto",
  "query_mode": "hybrid",
  "vlm_enhanced": true,
  "install": {"command": "{python} -m pip install 'raganything[all]'", "auto_install": false}
}
```

LLM/embedding/vision functions reuse the existing OpenAI/synthesis-llm config; if not configured, the runtime backend stays disabled and only the compile-time adapter (which doesn't require API keys for parser-only mode) runs.

## 8. Setup Wizard Flags

Mirrors UA exactly:

| Flag | Effect |
|---|---|
| `--with-raganything` | Add `raganything` entry to `external_tools` and `memory_backends`. |
| `--skip-raganything` | Negate auto-detect even when artifact exists. |
| `--install-raganything` | Install `raganything[all]` (pip) during setup; on first compile, retry once if missing. |
| `--skip-install-raganything` | Don't auto-install even when selected. |
| `--raganything-parser={mineru,docling,paddleocr}` | Default `mineru`. Persisted to `external_tools[].parser`. |
| `--raganything-extras={all,image,text,paddleocr}` | Default `all`. Maps to pip extras. |
| `--run-raganything` | Auto-refresh on every compile (sets `auto_refresh: true`). |
| `--yes` | (existing) Accept defaults. |

The non-interactive recipe in `docs/quickstart.md` becomes:

```bash
llm_wiki project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything \
  --run-cognee \
  --install-cognee
llm_wiki project compile
```

## 9. CLI Surface

- `llm_wiki project refresh-raganything [--full] [--parser=...]` — manual refresh; mirrors `refresh-understand-anything`. Writes `manifest.json` + `meta.json`.
- `llm_wiki project compile --refresh-external-tools` — already covers RAG-Anything once registered.
- `llm_wiki project ask "<q>"` — unchanged signature; backend resolution adds RAG-Anything per `memory_backends.priority`.

## 10. Errors, Fallback, Staleness

- **Manifest missing** → adapter no-ops; compile proceeds. Logged at INFO.
- **Manifest unreadable / wrong schema_version** → adapter raises `RagAnythingArtifactError`; compile fails fast (mirrors UA).
- **Refresh failure** (subprocess error, MinerU model download fails, OOM) → compile continues with a WARN log (mirrors UA's `keep compile running when memory refresh fails` pattern from commit `6cd8237`).
- **Staleness detection**: `meta.json.gitCommitHash` compared to `git rev-parse HEAD`. If `meta.json` absent or commit differs, refresh runs. `--full` forces a clean re-parse (purges `parsed/` + `working_dir/`).
- **API keys missing**: parser runs (no LLM needed for parsing), but LightRAG indexing is skipped; runtime backend stays disabled with a clear log line.
- **LibreOffice missing**: Office documents are skipped with a WARN; non-Office sources still indexed.

## 11. Testing Strategy

| Test | Target | Pattern |
|---|---|---|
| Unit: adapter | `RagAnythingGraphAdapter.import_artifact` produces nodes/edges + manifest with stable id map | mirror `test_understand_anything_adapter_imports_nodes_edges_and_manifest` |
| Unit: adapter merge | Concept canonicalization across UA + raganything (e.g., "Mermaid Rendering" appears in both) | mirror `test_understand_anything_concepts_merge_with_existing_llm_wiki_concepts` |
| Unit: refresh staleness | git head vs meta.json detection; `--full` purge | new |
| Unit: refresh subprocess error | non-zero exit logged, compile continues | new (regression for `keep compile running` pattern) |
| Unit: query bridge | `aquery` + `aquery_with_multimodal` happy path with mocked `RAGAnything` | new |
| Integration: `ProjectWiki.compile` | `external_tools[id=raganything].sync_mode=native_graph` populates graph + writes `raganything-sync.json` | mirror `test_project_compile_merges_configured_understand_anything_native_graph` |
| Integration: setup wizard | `build_setup_plan(include_raganything=True, ...)` produces correct plan + config json | mirror `test_project_setup` UA cases |
| CLI: argparse | new flags wire to `build_setup_plan` correctly | new |

All RAG-Anything imports are guarded behind try/except so unit tests run without `raganything` installed in the dev env. Tests that need it use `pytest.importorskip("raganything")`.

## 12. Documentation & i18n

- New page `docs/integrations/rag-anything.md` describes: what it is, why it complements Cognee/UA, current low-friction workflow, manual equivalent, native graph synchronization, mapping table (mirrors UA doc structure).
- Six i18n copies under `docs/i18n/integrations/` (ko, zh, ja, ru, es, fr) — same content translated, same screenshots/links.
- README updates (English + 6 i18n): new bullet in optional-integrations and an updated automated-setup snippet.
- `docs/quickstart.md`, `docs/installation.md`, `docs/publishing-checklist.md`, `docs/self-dogfood.md` updated as listed in §5.2.

## 13. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| MinerU model download (~GBs) on first compile breaks CI / first-run UX | `--install-raganything` runs `mineru --version` and prints model-download hint; refresh wrapper catches model-download failures and continues |
| LibreOffice not installed on macOS/Linux dev boxes | wizard prints platform install hint; Office docs skipped with WARN |
| RAG-Anything's LightRAG storage path conflicts with multiple LLM-Wiki projects | each project has its own `.llm-wiki/external/raganything/working_dir/` (project-relative) |
| API-key-dependent embeddings make compile slow/expensive | parser-only mode (no API key) still produces `manifest.json` for adapter; LightRAG indexing flagged off when keys absent |
| Concept duplication across UA + RAG-Anything + markdown extractor | existing `_add_or_merge_node` + `normalize_display_name` already handle this; integration test asserts dedup |
| Async ↔ sync interop | refresh and query bridges call `asyncio.run(...)` exactly once at boundary; no nested loops |
| Heavy dep regresses light wheel | `raganything` only in optional extras; default `pip install llm-wiki` unchanged |

## 14. Out-of-Scope / Deferred

- Adding ontology types `Figure`, `Table`, `Equation` (v2; needs broader graph-projection updates).
- Streaming / incremental compile that diffs `content_list` per-doc (v2; v1 re-parses changed docs only via sha256).
- Dashboard pages for multimodal blocks in the static frontend (v2).
- Auto-tuning between MinerU/Docling/PaddleOCR per file type (v2).
- LibreOffice auto-install (out — system-dep).

## 15. Acceptance Criteria

1. `llm_wiki project setup --yes --with-raganything --install-raganything --raganything-parser mineru --run-raganything` completes without error on a project with mixed markdown + PDF sources.
2. `llm_wiki project compile` produces a graph that includes `SourceFile` nodes for the parsed PDFs with `metadata.parser="raganything"` and at least one `multimodal_blocks` entry per multimodal doc.
3. `.llm-wiki/external/raganything/manifest.json` and `raganything-sync.json` exist and round-trip through the adapter (manifest sha256 stable across reruns when sources unchanged).
4. Concept canonicalization across UA, markdown, and RAG-Anything produces a single graph node with `external_refs` from all three systems for a shared concept (verified by integration test).
5. `llm_wiki project ask "<question about a PDF>"` returns a non-empty answer when API keys + RAG-Anything are configured.
6. All existing tests pass; new tests cover §11.
7. README + 6 i18n + integration doc + 6 i18n updated, links resolve, build site succeeds.

## 16. Self-Review Notes (post-write)

- Placeholders: none. Every section is concrete.
- Internal consistency: §6 mapping aligns with §10 schema and §11 tests.
- Scope: single implementation plan; v2 deferrals explicit in §14.
- Ambiguity: `memory_backends.priority` default is explicitly `["raganything","cognee","wiki_search"]`; backend disable conditions enumerated in §10.

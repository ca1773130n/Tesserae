# Understand Anything companion workflow

<!-- translations:start -->
<p align="center"><a href="../i18n/integrations/understand-anything.ko.md">한국어</a> · <a href="../i18n/integrations/understand-anything.zh.md">中文</a> · <a href="../i18n/integrations/understand-anything.ja.md">日本語</a> · <a href="../i18n/integrations/understand-anything.ru.md">Русский</a> · <a href="../i18n/integrations/understand-anything.es.md">Español</a> · <a href="../i18n/integrations/understand-anything.fr.md">Français</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything) and LLM-Wiki are complementary projects.

- Understand Anything is great at producing a codebase knowledge graph and interactive dashboard.
- LLM-Wiki is focused on long-lived agent memory: docs, markdown/wiki compilation, static publishing, session history, and agent-facing exports.

LLM-Wiki should not vendor or absorb Understand Anything. Treat it as an independent companion that can produce useful graph artifacts.

## Why use both?

Understand Anything can write:

```text
.understand-anything/knowledge-graph.json
```

That graph captures code structure such as files, functions, classes, modules, concepts, dependencies, layers, and tours.

LLM-Wiki can then preserve that artifact alongside the rest of the project memory:

- source docs and markdown pages;
- repository files;
- research notes;
- local Claude Code / Codex session history;
- generated static wiki pages;
- 2D / 3D graph website views;
- `llms.txt`, `llms-full.txt`, `search-index.json`, `graph.json`, and per-page agent siblings.

## Current low-friction workflow

The recommended path is the setup wizard:

```bash
llm_wiki project setup
```

Choose Understand Anything in the companion-tools step. LLM-Wiki installs/updates the companion skills when requested and writes a managed refresh command into `.llm-wiki/config.json`. Future `llm_wiki project compile` calls run that wrapper automatically when the UA graph is missing or stale.

For non-interactive automation, use:

```bash
llm_wiki project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex
llm_wiki project compile
```

The stored command is LLM-Wiki-owned, not something the user has to invent:

```bash
llm_wiki project refresh-understand-anything --platform codex
```

During compile, LLM-Wiki:

1. checks whether `.understand-anything/knowledge-graph.json` exists and matches the current git commit when metadata is available;
2. runs the configured agent platform (`codex`, `opencode`, or `claude`) only when the graph is missing/stale or refresh is forced;
3. verifies the graph was written;
4. materializes `.llm-wiki/external/understand-anything.md`;
5. continues the normal memory compile.

You can force all configured external refresh commands before a compile:

```bash
llm_wiki project compile --refresh-external-tools
```

Need Cognee too? Add the runtime memory flags in the same setup command:

```bash
llm_wiki project setup \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

## Manual equivalent

The managed setup path is preferred. If you intentionally want to use UA outside LLM-Wiki, run Understand Anything first inside your agent environment:

```bash
/understand
```

Then run `llm_wiki project setup --with-understand-anything` so LLM-Wiki records the markdown projection source. Direct JSON files are kept as raw companion artifacts, not hand-entered source paths.

```bash
llm_wiki project setup --with-understand-anything
llm_wiki project compile
llm_wiki project build-site
```

If you also want local agent-session memory:

```bash
llm_wiki project sessions discover --import
llm_wiki project build-site
```

## Native graph synchronization

LLM-Wiki now keeps the markdown projection for readability and also imports the UA graph natively during compile when the configured tool uses `sync_mode: native_graph`.

The native adapter reads `.understand-anything/knowledge-graph.json`, maps UA nodes/edges into LLM-Wiki's controlled ontology, and writes a sync manifest:

```text
.llm-wiki/external/understand-anything-sync.json
```

Current mapping:

| Understand Anything | LLM-Wiki direction |
|---|---|
| `project` | repository/project metadata |
| `nodes[type=file]` | `SourceFile` nodes |
| `nodes[type=function]` / `method` | `CodeFunction` nodes |
| `nodes[type=class]` / `component` | `CodeClass` nodes |
| `nodes[type=module]` / `package` | `CodeModule` nodes |
| `nodes[type=concept]` / `topic` | canonical `Concept` nodes |
| `nodes[type=feature]` / `capability` | `Capability` nodes |
| `edges[type=imports]` | `imports` edges |
| `edges[type=contains]` | `contains` edges |
| `edges[type=calls]` | `calls` edges |
| unknown edge types | `shares_concept_with` with `ua_edge_type` metadata |

Concept synchronization is canonicalized instead of blindly duplicated. If UA emits `Mermaid Rendering` and LLM-Wiki already has `Mermaid rendering`, compile keeps one concept node and adds UA provenance under `metadata.external_refs`.

LLM-Wiki remains the memory compiler; UA remains an independent companion graph generator.

## Collaboration principle

Do not frame LLM-Wiki as replacing Understand Anything.

A better framing:

- Understand Anything helps a developer understand a codebase now.
- LLM-Wiki helps agents remember, search, cite, update, and publish project knowledge over time.

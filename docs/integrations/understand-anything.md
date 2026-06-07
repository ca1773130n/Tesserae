# Understand Anything companion workflow

<!-- translations:start -->
<p align="center"><a href="../i18n/integrations/understand-anything.ko.md">한국어</a> · <a href="../i18n/integrations/understand-anything.zh.md">中文</a> · <a href="../i18n/integrations/understand-anything.ja.md">日本語</a> · <a href="../i18n/integrations/understand-anything.ru.md">Русский</a> · <a href="../i18n/integrations/understand-anything.es.md">Español</a> · <a href="../i18n/integrations/understand-anything.fr.md">Français</a> · <a href="../i18n/integrations/understand-anything.de.md">Deutsch</a></p>
<!-- translations:end -->
[Understand Anything](https://github.com/Lum1104/Understand-Anything) and Tesserae are complementary projects.

- Understand Anything is great at producing a codebase knowledge graph and interactive dashboard.
- Tesserae is focused on long-lived agent memory: docs, markdown/wiki compilation, static publishing, session history, and agent-facing exports.

Tesserae should not vendor or absorb Understand Anything. Treat it as an independent companion that can produce useful graph artifacts.

## Why use both?

Understand Anything can write:

```text
.understand-anything/knowledge-graph.json
```

That graph captures code structure such as files, functions, classes, modules, concepts, dependencies, layers, and tours.

Tesserae can then preserve that artifact alongside the rest of the project memory:

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
tesserae init
```

Choose Understand Anything in the companion-tools step. Tesserae installs/updates the companion skills when requested and writes a managed refresh command into `.tesserae/config.json`. Future `tesserae compile` calls run that wrapper automatically when the UA graph is missing or stale.

For non-interactive automation, use:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex
tesserae compile
```

The stored command is Tesserae-owned, not something the user has to invent:

```bash
tesserae integrations refresh understand-anything --platform codex
```

During compile, Tesserae:

1. checks whether `.understand-anything/knowledge-graph.json` exists and matches the current git commit when metadata is available;
2. runs the configured agent platform (`codex`, `opencode`, or `claude`) only when the graph is missing/stale or refresh is forced;
3. verifies the graph was written;
4. materializes `.tesserae/external/understand-anything.md`;
5. continues the normal memory compile.

You can force all configured external refresh commands before a compile:

```bash
tesserae compile --refresh-integrations
```

Need Cognee too? Add the runtime memory flags in the same setup command:

```bash
tesserae init \
  --yes \
  --with-understand-anything \
  --install-understand-anything \
  --understand-anything-platform codex \
  --run-cognee \
  --install-cognee
```

## Manual equivalent

The managed setup path is preferred. If you intentionally want to use UA outside Tesserae, run Understand Anything first inside your agent environment:

```bash
/understand
```

Then run the setup wizard and **enable Understand Anything when prompted** so
Tesserae records the markdown projection source. Direct JSON files are kept as
raw companion artifacts, not hand-entered source paths.

```bash
tesserae init
# enable Understand Anything when the wizard prompts
tesserae compile
tesserae export site
```

For non-interactive automation, run `tesserae init --yes` (integrations OFF),
enable Understand Anything in `.tesserae/config.json` (the wizard writes the
integration under the `external_tools` key), then `tesserae integrations
refresh understand-anything` before compiling.

If you also want local agent-session memory:

```bash
tesserae sessions discover --import
tesserae export site
```

## Native graph synchronization

Tesserae now keeps the markdown projection for readability and also imports the UA graph natively during compile when the configured tool uses `sync_mode: native_graph`.

The native adapter reads `.understand-anything/knowledge-graph.json`, maps UA nodes/edges into Tesserae's controlled ontology, and writes a sync manifest:

```text
.tesserae/external/understand-anything-sync.json
```

Current mapping:

| Understand Anything | Tesserae direction |
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

Concept synchronization is canonicalized instead of blindly duplicated. If UA emits `Mermaid Rendering` and Tesserae already has `Mermaid rendering`, compile keeps one concept node and adds UA provenance under `metadata.external_refs`.

Tesserae remains the memory compiler; UA remains an independent companion graph generator.

## Collaboration principle

Do not frame Tesserae as replacing Understand Anything.

A better framing:

- Understand Anything helps a developer understand a codebase now.
- Tesserae helps agents remember, search, cite, update, and publish project knowledge over time.

# Understand Anything companion workflow

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

Choose Understand Anything in the companion-tools step. If you provide a refresh command, LLM-Wiki stores it in `.llm-wiki/config.json` and runs it automatically before future `llm_wiki project compile` calls when auto-refresh is enabled.

For non-interactive automation, use:

```bash
llm_wiki project setup \
  --yes \
  --with-understand-anything \
  --understand-anything-command '<command that refreshes .understand-anything/knowledge-graph.json>' \
  --run-understand-anything
```

You can also force all configured external refresh commands before a compile:

```bash
llm_wiki project compile --refresh-external-tools
```

## Manual equivalent

If you prefer explicit config, run Understand Anything first:

```bash
/understand
```

Then either use `llm_wiki project setup`, or generate a small markdown projection under `.llm-wiki/external/understand-anything.md` and include that projection as the source. The setup wizard does this projection automatically; direct JSON files are kept as raw companion artifacts, not hand-entered source paths.

```bash
llm_wiki project init \
  --name my_project_wiki \
  --source-kind Repository \
  --source README.md \
  --source docs \
  --source src \
  --source .llm-wiki/external/understand-anything.md

llm_wiki project compile
llm_wiki project build-site
```

If you also want local agent-session memory:

```bash
llm_wiki project sessions discover --import
llm_wiki project build-site
```

## Possible future bridge

A future optional bridge could map Understand Anything's graph schema into LLM-Wiki's typed graph ontology more directly.

Likely mapping:

| Understand Anything | LLM-Wiki direction |
|---|---|
| `project` | repository/project metadata |
| `nodes[type=file]` | source/document/file nodes |
| `nodes[type=function]` | function/code symbol nodes |
| `nodes[type=class]` | class/code symbol nodes |
| `nodes[type=module]` | module/package nodes |
| `nodes[type=concept]` | concept nodes |
| `edges[type=imports]` | imports/dependency edges |
| `edges[type=contains]` | containment edges |
| `edges[type=calls]` | call/reference edges |
| `layers[]` | architecture grouping metadata |
| `tour[]` | onboarding/synthesis pages |

Keep this bridge optional and external unless both projects agree on a stable exchange contract.

## Collaboration principle

Do not frame LLM-Wiki as replacing Understand Anything.

A better framing:

- Understand Anything helps a developer understand a codebase now.
- LLM-Wiki helps agents remember, search, cite, update, and publish project knowledge over time.

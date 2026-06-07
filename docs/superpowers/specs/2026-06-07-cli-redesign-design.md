# CLI Redesign — crystal-clear command tree (clean break)

**Date:** 2026-06-07 · **Status:** approved (user: "go") · **Target:** v0.6.0

## Problem

The CLI is undiscoverable and overgrown:

- `tesserae --help` shows a raw extraction parser; the real entry points
  (`project`, `ask`, `wiki`, `llm-defaults`) are invisible prefix-dispatches.
- `tesserae project` hides ~26 flat subcommands; `setup` has 29 flags,
  `compile` 26.
- Naming drift: `export-agent-harness`, `engine`/`daemon` aliases,
  `obsidian-sync` vs `export-obsidian`, registry ops under `wiki`.

## Decisions (user-approved)

1. **Clean break** — old commands become one-line error stubs with the exact
   replacement; no silent aliases. Pre-1.0, hours after the 0.5.0 publish.
2. **Flat verbs + few groups** — gh/cargo shape.

## New command tree

```
tesserae <command>

EVERYDAY
  init       Set up .tesserae (wizard by default; --yes non-interactive)
  compile    Rebuild the knowledge graph (compile [paths] = ad-hoc ingest)
  context    Compile agent-ready context for a query
  ask        Ask the project memory a question
  serve      Browse the compiled site (auto-builds if missing/stale; --no-build)
  status     Node/edge counts, last compile, vault state

AUTOMATION
  engine     Refresh daemon: watch sessions/sources, coalesced recompiles
  refresh    One-shot: import sessions + compile + sync vault

GROUPS
  sessions   import | discover | list
  vault      sync | export | prune        (Obsidian projection)
  export     harness | graphiti | site    (site: --deploy publishes)
  config     llm | show                   (machine-wide ~/.tesserae/config.json)
  projects   list | activate | unregister | mcp-config   (registry)
  integrations  refresh <name>            (raganything | understand-anything)
  extract    <paths>                      (low-level typed-graph extraction)

LAB
  lab        evolve | schema-drift        (experimental LLM ops)
```

`tesserae` with no args prints the grouped help above — never the extraction
parser.

## Full old → new mapping

| Old | New |
|---|---|
| `tesserae <paths>` (bare extraction) | `tesserae extract <paths>` |
| `tesserae ask` | `tesserae ask` (unchanged) |
| `tesserae wiki list/activate/unregister` | `tesserae projects list/activate/unregister` |
| `tesserae llm-defaults` | `tesserae config llm` (`--show` → `tesserae config show`) |
| `project init` | `tesserae init --bare` (no wizard) |
| `project setup` | `tesserae init` (wizard default; `--yes` accepts defaults) |
| `project ingest <paths>` | `tesserae compile <paths>` |
| `project compile` | `tesserae compile` |
| `project context` | `tesserae context` |
| `project ask` | `tesserae ask` (project-scoped via cwd, `--project`) |
| `project status` *(MCP/skill only today)* | `tesserae status` (thin read-only wrapper over existing graph/manifest data — no new behavior) |
| `project build-site` | `tesserae export site` |
| `project deploy` | `tesserae export site --deploy` |
| `project serve` | `tesserae serve` (auto-build) |
| `project watch` | error stub → `tesserae engine` (verify semantics at plan time; if watch ≠ engine, fold as `tesserae engine --site-only`) |
| `project engine` / `project daemon` | `tesserae engine` (alias removed) |
| `project refresh` | `tesserae refresh` |
| `project sessions import/discover/list` | `tesserae sessions import/discover/list` |
| `project obsidian-sync` | `tesserae vault sync` (`--prune-orphans` → `tesserae vault prune`) |
| `project export-obsidian` | `tesserae vault export` |
| `project export-agent-harness` | `tesserae export harness [--target codex]` |
| `project export-graphiti` | `tesserae export graphiti` |
| `project sync-graphiti` | `tesserae export graphiti --sync` |
| `project mcp-config` | `tesserae projects mcp-config` |
| `project refresh-raganything` | `tesserae integrations refresh raganything` |
| `project refresh-understand-anything` | `tesserae integrations refresh understand-anything` |
| `project evolve` | `tesserae lab evolve` |
| `project schema-drift` | `tesserae lab schema-drift` |

(`integrations` is a 7th group, deliberately small: `refresh <name>` only.)

## Flag diet

- **init**: ≤8 flags (`--yes`, `--bare`, `--project`, `--name`, `--source`,
  `--llm-provider`, `--claude-config-dir`, `--codex-home`). The other ~21
  setup flags become wizard prompts and/or documented `config.json` keys
  (`tesserae init --yes` writes the same defaults they encoded).
- **compile**: ~8 everyday flags (`--changed-only`, `--no-sessions`,
  `--project`, `--limit`, `--llm-provider`, `--claude-config-dir`,
  `--codex-home`, `--refresh-integrations`). Kuzu/graphiti/cognee output
  knobs move to `export`/config keys.
- Rule: every removed flag remains settable via `config.json`; the flag's
  old help text becomes the config key's doc.

## Clean-break stubs

- `tesserae project <anything>` → exit 2 with exactly one line:
  `tesserae project compile has moved → tesserae compile` (mapping table
  drives the message; unknown subcommand → point at `tesserae --help`).
- `tesserae wiki …`, `tesserae llm-defaults` → same one-line stub.
- Stubs live in one table in `cli.py`; no duplicated parser code.

## Help/UX standards

- Grouped top-level help (EVERYDAY/AUTOMATION/GROUPS/LAB) via a custom
  formatter; one line per command.
- Every command: one-line description + `EXAMPLES:` epilog (2–3 real
  invocations).
- Errors never print tracebacks for user errors; exit codes: 2 usage,
  1 operational failure, 0 success.

## Blast radius (must migrate in the same change)

1. Plugin slash commands + SessionStart/End hooks shelling `tesserae project …`
   (`.claude-plugin/`, hook scripts).
2. CI `build-demo` workflow + `.claude/skills/release/SKILL.md` smoke steps.
3. Docs: quickstart/installation/integrations/README ×8 langs (i18n
   invariant applies to docs/, not this spec dir).
4. Tests invoking `project_main([...])`/`main([...])` (~60 call sites) —
   they migrate to the new tree; stub behavior gets its own tests.
5. `pyproject.toml` console scripts unchanged (`tesserae`, `tesserae_mcp`);
   `tesserae_mcp` and MCP tool names untouched.

## Testing strategy

- TDD per command group: parser-level tests (command exists, flags defined),
  handler dispatch tests, stub-message tests (every row of the mapping
  table asserted).
- Full-suite gate green before merge; CI smoke (`init --yes` → `compile` →
  `export site`) updated in the same commit as the workflow.

## Out of scope

- `tesserae_mcp` server and MCP tool names.
- Behavior changes inside handlers (this is a surface refactor; compile
  internals untouched).
- PyPI 0.6.0 release (separate, after this lands).

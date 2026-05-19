# Claude Code plugin for Tesserae — implementation plan

> Status: plan v2 (post-codex-review) • 2026-05-19 • spec: [`docs/superpowers/specs/2026-05-19-claude-code-plugin-design.md`](../specs/2026-05-19-claude-code-plugin-design.md)

**Changelog**

- v1 → v2 (post-codex review of v1): integrated Codex's review. Five corrections:
  layout is "plugin root" not "everything under `.claude-plugin/`";
  slash-command frontmatter is `argument-hint` / `allowed-tools` (kebab),
  not `arguments` / `allowed_tools`; commands are markdown prompts with
  `!`-prefixed executable blocks, not "frontmatter + fenced bash";
  hooks live in `hooks/hooks.json`, not the plugin manifest; MCP server
  shape uses top-level server-name objects per the plugin-dev guide.
  Added a Phase 0 validation spike to de-risk the schema assumptions
  before building seven commands on top of them.
- v2 → v3 (post-install validation): the marketplace install resolved
  the plugin root by name rather than the configured subdir, picking
  the `tesserae/` Python package by mistake and missing every
  command. Moved every plugin asset from `plugin/<x>` to repo root
  `<x>/` so it mirrors HarnessSync's working marketplace layout.
  Install command shortened from `/plugin install /path/to/Tesserae/plugin/`
  to `/plugin install /path/to/Tesserae/`. The phase-by-phase body
  below still references the old `plugin/` paths as a historical
  record of what was built and when; the current on-disk layout
  has everything one directory up.

## Plugin root location

The plugin's root directory becomes `plugin/` at the repo root.
The directory layout is:

```
plugin/                              # the plugin root
├── .claude-plugin/
│   └── plugin.json                  # manifest (only file under .claude-plugin/)
├── commands/                        # slash commands
├── skills/                          # skills
├── hooks/
│   ├── hooks.json                   # hook registration (NOT in plugin.json)
│   └── *.sh                         # hook script bodies
├── scripts/                         # helpers used by commands (e.g. ask wrapper)
├── .mcp.json                        # MCP auto-registration
└── README.md
```

Users install via `/plugin install /path/to/Tesserae/` (local
dev) or `/plugin install https://github.com/ca1773130n/Tesserae`
(remote).

Seven phases (Phase 0 added). Each ends with `bash -n` syntax checks
+ a manual install/uninstall round-trip + a commit.

## Phase 0 — Validation spike

**Goal** (codex-recommended): before building the full surface, ship
a minimal plugin (one slash command, one hook, one MCP server) that
verifies every schema assumption end-to-end. Cheaper to discover a
manifest typo here than across nine commands and four hooks.

**Files**

- `plugin/.claude-plugin/plugin.json` — minimal manifest with
  `name`, `version`, `description`. No `hooks` block (those go in
  `hooks/hooks.json`).
- `plugin/commands/ping.md` — `/tesserae:ping` slash command. Body:
  one fenced executable block that echoes a recognisable string.
- `plugin/hooks/hooks.json` — wrapper object registering one
  `SessionStart` hook that touches `/tmp/tesserae-plugin-spike-ran`.
- `plugin/hooks/spike-session-start.sh` — the hook body.
- `plugin/.mcp.json` — registers the `tesserae` MCP server (top-level
  server-name shape, not `{"mcpServers": …}` wrapper).

**Verification**

- `/plugin install /path/to/Tesserae/` — confirm plugin appears in
  `/plugin list` and no schema-validation errors at install.
- Restart a Claude Code session — confirm `/tmp/tesserae-plugin-spike-ran`
  exists (session-start hook fired) and `/tesserae:ping` works.
- Confirm the MCP server appears in `/mcp` listing and one of its
  tools is callable. Note the actual prefixed tool name (e.g.
  `mcp__plugin_tesserae_tesserae__list_projects`); copy it into the
  Phase 5 skill notes.
- Confirm install/uninstall is clean (no leftover state).

**Commit** — `feat(plugin): Phase 0 spike — validate manifest + hooks.json + .mcp.json schemas`

---

## Phase 1 — Manifest + MCP auto-registration finalised

**Goal**: replace the spike's minimal manifest with the production
plugin.json + production .mcp.json. Spike-only files (the `ping`
command, the touch-file hook) are deleted in the same commit.

**Files**

- `plugin/.claude-plugin/plugin.json` — final manifest. `name =
  "tesserae"`, `version` mirrors `tesserae` PyPI version (read from
  `pyproject.toml` at release time), `description`, `repository`,
  `homepage`, `keywords`.
- `plugin/.mcp.json` — top-level server-name shape:
  ```json
  {
    "tesserae": {
      "command": "tesserae_mcp",
      "args": []
    }
  }
  ```
- `plugin/README.md` — one-page plugin-specific quickstart (refined
  in Phase 6 once all commands exist).

**Verification**

- Reinstall plugin, confirm metadata in `/plugin info tesserae`
  matches the manifest.
- `/mcp` lists `tesserae` server; tool calls work.

**Commit** — `feat(plugin): finalise manifest + MCP server registration`

---

## Phase 2 — 1:1 wrapper slash commands

**Goal**: seven verb-mapped commands. Each is a Markdown prompt with
a `!`-prefixed executable block (per the correct command body shape).

**Per-command structure** (corrected per codex):

```markdown
---
description: One-sentence summary shown in the command picker.
argument-hint: "[--changed-only] [--no-vault-pull]"
allowed-tools:
  - "Bash(tesserae project compile:*)"
---

Run the Tesserae compile pipeline against the current project.

!`tesserae project compile $ARGUMENTS`
```

Notes:
- `argument-hint` is a hint string for the picker UI; there is no
  structured arg schema like I assumed in v1.
- `allowed-tools` is kebab-case AND uses restrictive Bash filters so
  the command can only run the exact subcommand it wraps. Prevents a
  prompt-injection from turning `/tesserae:compile` into an arbitrary
  shell invocation.
- The `!` prefix marks the fenced block as executable; the agent runs
  it and returns the output inline.

**Files** — one `.md` per command:

| File | Wraps | `allowed-tools` filter |
|---|---|---|
| `plugin/commands/compile.md` | `tesserae project compile` | `Bash(tesserae project compile:*)` |
| `plugin/commands/ask.md` | `tesserae project ask` | `Bash(tesserae project ask:*)` + see ask-quoting note below |
| `plugin/commands/setup.md` | `tesserae project setup` | `Bash(tesserae project setup:*)`. **`disable-model-invocation: true`** in frontmatter so Claude can't auto-invoke the interactive wizard. |
| `plugin/commands/sessions-import.md` | `tesserae sessions discover --import` | `Bash(tesserae sessions discover:*)` |
| `plugin/commands/build-site.md` | `tesserae project build-site` | `Bash(tesserae project build-site:*)` |
| `plugin/commands/serve.md` | `tesserae project serve` | `Bash(tesserae project serve:*)` |
| `plugin/commands/obsidian-sync.md` | `tesserae project obsidian-sync` | `Bash(tesserae project obsidian-sync:*)` |

**Ask-command quoting** (codex flag): `/tesserae:ask "what did we
decide?"` mustn't break on quoted multi-word input. Use a script
helper:

```markdown
# plugin/commands/ask.md body
!`${CLAUDE_PLUGIN_ROOT}/scripts/tesserae-ask.sh "$ARGUMENTS"`
```

```bash
#!/usr/bin/env bash
# plugin/scripts/tesserae-ask.sh
# Strips one matching pair of surrounding quotes (Claude Code passes
# the literal user text including any quotes), then calls the CLI.
set -euo pipefail
q="${1-}"
if [[ "$q" =~ ^\"(.*)\"$ ]] || [[ "$q" =~ ^\'(.*)\'$ ]]; then
  q="${BASH_REMATCH[1]}"
fi
exec tesserae project ask "$q"
```

**Verification**

- YAML frontmatter parses for every `.md`.
- `bash -n plugin/scripts/tesserae-ask.sh` clean.
- Manual install + run each `/tesserae:<cmd>` in a tmpdir project;
  confirm the underlying CLI receives the right args. Specifically
  test `/tesserae:ask "multi word question with spaces"`.

**Commit** — `feat(plugin): seven 1:1 slash commands wrapping the tesserae CLI verbs`

---

## Phase 3 — Workflow macros (refresh + status)

**Goal**: two macro commands.

**Files**

- `plugin/commands/refresh.md` — Markdown prompt body chains the
  three steps:
  ```markdown
  Refresh the project memory: import new sessions, recompile, sync vault.

  !`tesserae sessions discover --import`
  !`tesserae project compile`
  !`tesserae project obsidian-sync`

  Summarize the final node/edge/session counts.
  ```
  The agent runs the three `!` blocks in order and the trailing prose
  instructs it to emit a one-line summary parsing the compile output.

- `plugin/commands/status.md` — pure read. Uses an
  `${CLAUDE_PLUGIN_ROOT}/scripts/tesserae-status.sh` helper that
  prints the node/edge counts from `.tesserae/graph.json`, the last
  build timestamp from `.tesserae/.build-history.jsonl`, and session
  counts from `.tesserae/harness_sessions/manifest.json`. Frontmatter:
  `allowed-tools: ["Bash(*status*)"]`.

- `plugin/scripts/tesserae-status.sh` — shell helper. Uses `jq`
  when present, falls back to grep/sed otherwise.

**Verification**

- `/tesserae:refresh` in the Tesserae repo runs all three commands;
  the agent's summary line includes plausible counts.
- `/tesserae:status` prints a compact table.

**Commit** — `feat(plugin): /tesserae:refresh and /tesserae:status macro commands`

---

## Phase 4 — Hooks via hooks/hooks.json

**Goal**: four hooks land. Hook registration goes in
`plugin/hooks/hooks.json` (NOT `plugin.json`); each hook's body is a
small shell script in `plugin/hooks/`.

**Files**

- `plugin/hooks/hooks.json` — codex-corrected wrapper object:
  ```json
  {
    "description": "Tesserae plugin hooks — auto-compile sessions, status checks, large-graph confirmation.",
    "hooks": {
      "SessionStart": [{"command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"}],
      "SessionEnd":   [{"command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-end.sh"}],
      "PostToolUse":  [{"matcher": "Edit|Write|MultiEdit",
                         "command": "${CLAUDE_PLUGIN_ROOT}/hooks/posttooluse-edit.sh"}],
      "PreToolUse":   [{"matcher": "Bash",
                         "command": "${CLAUDE_PLUGIN_ROOT}/hooks/pretooluse-compile.sh"}]
    }
  }
  ```

- `plugin/hooks/_lib.sh` — shared helpers: `find_tesserae` (PATH
  + `~/.local/bin` + `~/Library/Python/3.*/bin`),
  `read_plugin_setting <key>` (parse `.claude/tesserae.local.md`
  frontmatter via `sed`), `log_to <file>`.

- `plugin/hooks/session-start.sh` — pure read. Prints `tesserae: X
  nodes, last compile N days ago` to stdout (Claude Code surfaces
  hook stdout in the session). Suggests `/tesserae:refresh` if the
  last compile was > 7 days ago.

- `plugin/hooks/session-end.sh` — backgrounds the import+compile,
  exit 0 always.

- `plugin/hooks/posttooluse-edit.sh` — **path filter implemented
  inside the script** (codex correction): reads stdin JSON, extracts
  `tool_input.file_path`, checks if it starts with `docs/`, falls
  through if not. Debounced via `.tesserae/.recompile.lock`. Disabled
  by default; user opts in via local settings.

- `plugin/hooks/pretooluse-compile.sh` — confirmation gate. Emits a
  hook JSON response with `permissionDecision: "ask"` (codex
  correction: not a blocking shell prompt) when the project has >
  5000 nodes AND the per-session confirmation lock is absent.
  Otherwise emits `permissionDecision: "allow"`.

**Settings file**

`.claude/tesserae.local.md` (per-project, in the user's project, not
the plugin):

```yaml
---
hooks:
  session_start: true
  session_end: true
  posttooluse_edit: false
  pretooluse_compile: true
---
```

Each hook reads its own key first thing; missing file → defaults
apply.

**Verification**

- `bash -n` on every `.sh` file.
- Hook scripts are unit-tested via `tests/test_plugin_hooks.bats`
  (install bats-core if not present) with a fake `tesserae` binary
  on PATH that records its invocations.
- Manual: trigger each event in a real session, confirm correct
  behaviour and no errors at session close.

**Commit** — `feat(plugin): four hooks via hooks/hooks.json with path filter + permissionDecision gate`

---

## Phase 5 — Skill (using-tesserae)

**Goal**: an auto-loading skill that teaches the agent which surface
to use.

**Files**

- `plugin/skills/using-tesserae/SKILL.md` — single file. Frontmatter
  fields: `name`, `description` (carefully crafted to match queries
  about wiki / typed graph / session findings — see
  `plugin-dev:skill-development` guidance for triggering hygiene).
- Body sections:
  - "What Tesserae is" — one paragraph.
  - "Decision table: MCP tool vs slash command" — for a given task,
    which is faster.
  - "MCP tool names" — codex correction: the actual prefixed names
    (`mcp__plugin_tesserae_tesserae__<tool>`), copied from the
    Phase 0 spike's `/mcp` listing.
  - "Node-type cheat sheet" — 41 types grouped by category, one-line
    gloss each, generated once from `tesserae/research_graph.py`.
  - "Common recipes" — three micro-flows: `refresh after edits`,
    `ask about a paper`, `inspect what a session decided`.

**Verification**

- Markdown lint, no broken internal links.
- Manual: queries like "what did we decide about X yesterday" should
  surface the skill in the picker.

**Commit** — `feat(plugin): using-tesserae skill orienting the agent in the tesserae surface`

---

## Phase 6 — README + i18n + plugin README

**Goal**: discoverability. Main `README.md` + 7 translations get a
short paragraph + link to the plugin install command. The plugin's
own `README.md` becomes the one-page quickstart users land on.

**Files**

- `plugin/README.md` — overview + 9-command table + 4-hook table +
  install + per-project opt-out example.
- `README.md` — extend Integrations section with the plugin entry.
- 7 i18n READMEs — same paragraph translated (de/es/fr/ja/ko/ru/zh).
  Reuse the per-file-edit pattern from the session-graph rollout.
- `docs/integrations/claude-code-plugin.md` — full integration doc.
- 7 i18n translations of the integration doc under
  `docs/i18n/integrations/claude-code-plugin.{de,es,fr,ja,ko,ru,zh}.md`.

**Verification**

- Render-check on GitHub for English doc + spot-check one
  translation.
- Existing tests unaffected.

**Commit** — `docs(plugin): README + integration doc + 7 i18n translations for the Claude Code plugin`

---

## Cross-cutting invariants

- **Zero Python in `plugin/`**. Shell + Markdown + JSON only. Every
  command and hook shells out to the user's installed `tesserae` CLI.
- **Restrictive `allowed-tools` per command** — each command may
  only run the exact CLI subcommand it wraps. Prompt injection from
  the user's question text into an `/tesserae:ask` argument cannot
  escalate to arbitrary shell.
- **Hook safety**: missing `tesserae` binary → hook exits 0 silently
  and logs to `.tesserae/.<hook>.log`. Never blocks the user's
  session.
- **Path-filter in script, not matcher** (codex correction): event
  matchers only match event/tool names. Path-based filtering happens
  inside `posttooluse-edit.sh` by parsing the stdin JSON
  `tool_input.file_path`.
- **PreToolUse compile gate uses `permissionDecision: "ask"`**
  (codex correction), not a blocking shell prompt.
- **Per-project opt-out via `.claude/tesserae.local.md`** — each
  hook reads its own key from this frontmatter file before doing
  anything. Missing file → defaults apply (session-end on by default;
  posttooluse-edit off; the other two on).
- **Version pin lockstep**: `plugin/.claude-plugin/plugin.json`
  `version` matches the `tesserae` PyPI version that ships in the
  same git tag.
- **Setup is `disable-model-invocation: true`**: the interactive
  wizard shouldn't be auto-launched by Claude — only ever explicitly
  invoked by the user via `/tesserae:setup`.

## Rollback plan

Each phase is one commit on `main`. Plugin install is reversible via
`/plugin uninstall tesserae`. To roll back any phase: `git revert
<commit>`. The plugin directory at `plugin/` is fully additive —
removing it doesn't affect the CLI, the MCP server, or any consumer
project's `.tesserae/`.

Riskiest phase is Phase 4 (hooks) — a misfiring hook slows every
session. Mitigation: per-project opt-out via the local settings file;
only `session-end` is enabled by default.

## Open implementation questions

- **`disable-model-invocation` exact field name**: confirm during
  Phase 2 by reading one of the cached plugins'
  `commands/*.md` files. If the field name differs, update.
- **`permissionDecision` JSON response shape**: confirm during Phase
  4 by reading the plugin-dev:hook-development skill content.
- **MCP merge vs clobber**: Phase 0 spike must verify whether plugin
  `.mcp.json` merges with user-managed MCP config or clobbers. If
  clobber, document the conflict in the plugin README.

---

**Estimated time**: ~2 working days. Phase 0 (~2 hours), Phase 1
(~1 hour), Phase 2 (~3 hours), Phase 3 (~2 hours), Phase 4 (~4
hours), Phase 5 (~3 hours), Phase 6 (~3 hours + translations).

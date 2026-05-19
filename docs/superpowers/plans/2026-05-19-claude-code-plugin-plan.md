# Claude Code plugin for Tesserae — implementation plan

> Status: plan • 2026-05-19 • spec: [`docs/superpowers/specs/2026-05-19-claude-code-plugin-design.md`](../specs/2026-05-19-claude-code-plugin-design.md)

Six phases, each ends in green tests + one commit + a push. The plugin
lives entirely under `.claude-plugin/` at the repo root and ships zero
Python — every slash command shells out to the already-installed
`tesserae` CLI. Phases are ordered so each one is shippable on its own:
after Phase 2 the user can already use the wrapper commands; macros,
hooks, skill come on top.

## Phase 1 — Scaffolding + MCP auto-registration

**Goal**: a valid `.claude-plugin/` directory that Claude Code accepts
on `/plugin install`. No commands yet — just the structure + the MCP
server registration so the agent gets the tools immediately even
before any slash commands exist.

**Files**

- `.claude-plugin/plugin.json` — manifest. `name = "tesserae"`,
  `version` mirrors the `tesserae` PyPI version, `description` one
  sentence, `author`, repo URL.
- `.claude-plugin/.mcp.json` — registers a single MCP server named
  `tesserae` with command `tesserae_mcp`.
- `.claude-plugin/README.md` — one-page plugin quickstart. Linked
  from the main `README.md` Integrations bullet for the session-graph
  feature.

**Verification**

- `/plugin install /path/to/Tesserae/.claude-plugin/` from a Claude Code
  session against a tmp config — confirm plugin appears in `/plugin
  list` and the `tesserae` MCP server registers without manual edits
  to `claude_desktop_config.json`.
- Existing test suite unaffected — no Python changed.

**Commit** — `feat(plugin): scaffold .claude-plugin/ with manifest + MCP auto-registration`

---

## Phase 2 — 1:1 wrapper slash commands

**Goal**: the seven verb-mapped commands work as plain shell wrappers.
Each is a markdown file with YAML frontmatter + a bash body.

**Files**

- `.claude-plugin/commands/compile.md` — wraps `tesserae project compile`.
  Accepts `--changed-only` and `--no-vault-pull` via frontmatter
  `arguments`.
- `.claude-plugin/commands/ask.md` — wraps `tesserae project ask`.
  Takes a required `question` positional arg.
- `.claude-plugin/commands/setup.md` — wraps `tesserae project setup`.
  Opens the interactive wizard inside the TUI.
- `.claude-plugin/commands/sessions-import.md` — wraps
  `tesserae sessions discover --import`.
- `.claude-plugin/commands/build-site.md` — wraps `tesserae project build-site`.
- `.claude-plugin/commands/serve.md` — wraps `tesserae project serve`.
  Takes optional `--port N` (default 8765).
- `.claude-plugin/commands/obsidian-sync.md` — wraps
  `tesserae project obsidian-sync`. Accepts `--prune-orphans`.

**Each file's frontmatter shape**

```yaml
---
description: One-sentence description shown in the command picker.
arguments:
  - name: option-name
    description: ...
allowed_tools:
  - Bash
---
```

Body: a single fenced bash block that executes the underlying CLI with
`$ARGUMENTS` interpolated where appropriate.

**Verification**

- Each `.md` file passes a quick `python3 -c 'import yaml; yaml.safe_load(open(...).read().split("---")[1])'` parse to confirm valid frontmatter.
- Manual: install plugin, run each `/tesserae:<command>` from a
  test project, confirm the underlying CLI runs with the right args.

**Commit** — `feat(plugin): seven 1:1 slash-command wrappers around the tesserae CLI`

---

## Phase 3 — Workflow macros (refresh + status)

**Goal**: the two macro commands ship.

**Files**

- `.claude-plugin/commands/refresh.md` — chains
  `tesserae sessions discover --import && tesserae project compile &&
  tesserae project obsidian-sync`. On completion emits a one-line
  summary parsed from the compile output (`nodes: X, edges: Y,
  sessions: Z, vault orphans pruned: W`).
- `.claude-plugin/commands/status.md` — pure read. Loads
  `.tesserae/graph.json`, last-build timestamp from
  `.tesserae/.build-history.jsonl`, session count from
  `.tesserae/harness_sessions/manifest.json`. Renders a compact
  table.

**Verification**

- Manual: `/tesserae:refresh` in the Tesserae repo itself produces a
  non-zero summary; `/tesserae:status` prints the current graph
  numbers.

**Commit** — `feat(plugin): /tesserae:refresh and /tesserae:status macro commands`

---

## Phase 4 — Hooks (4 scripts)

**Goal**: the four hooks land, each with the safety guards from the
spec. All hooks read the per-project opt-out frontmatter at
`.claude/tesserae.local.md` before doing anything.

**Files**

- `.claude-plugin/hooks/_lib.sh` — shared helpers: `find_tesserae`
  (PATH probe + `~/.local/bin` + `~/Library/Python/3.*/bin`),
  `read_plugin_setting` (parse `.claude/tesserae.local.md`
  frontmatter via `sed`), `log_to <file>`.
- `.claude-plugin/hooks/session-start.sh` — pure read. Reads graph
  summary + last-compile timestamp; prints a one-liner; suggests
  `/tesserae:refresh` if last compile > 7 days.
- `.claude-plugin/hooks/session-end.sh` — backgrounded
  `tesserae sessions discover --import && tesserae project compile`.
  Logs to `.tesserae/.session-end-hook.log`. Exit code always 0
  (never blocks session close).
- `.claude-plugin/hooks/posttooluse-edit.sh` — debounced via
  `.tesserae/.recompile.lock`. Default opt-out; enabled when the
  user sets `posttooluse_edit: true` in their local settings.
- `.claude-plugin/hooks/pretooluse-compile.sh` — confirmation gate.
  Reads `.tesserae/graph.json` summary; if `nodes > 5000` AND
  `.tesserae/.confirmed-compile.lock` is absent, prompts and aborts;
  otherwise drops the lock and proceeds.
- `.claude-plugin/plugin.json` updated — `hooks` section registers
  the four scripts against their respective events with the right
  matcher patterns.

**Verification**

- `bash -n` syntax check on every `.sh` file (run as part of the
  commit pre-flight).
- Manual: trigger each event in a test session, confirm correct
  behaviour (status line at session start, log entry at session end,
  no-op when opt-out is set, confirmation prompt for large graphs).

**Commit** — `feat(plugin): SessionStart / SessionEnd / PostToolUse-edit / PreToolUse-compile hooks`

---

## Phase 5 — Skill (using-tesserae)

**Goal**: an auto-loading skill that teaches the agent when to use
which tesserae surface.

**Files**

- `.claude-plugin/skills/using-tesserae/SKILL.md` — single-file skill.
  Frontmatter: `name`, `description` (carefully crafted to match
  queries about wikis / typed graphs / project memory / past-session
  recall — see plugin-dev:skill-development guidance), and
  `allowed_tools`.
- Body sections:
  - "What Tesserae is" (one paragraph).
  - "When to use MCP tools vs slash commands" (decision table).
  - "Node type cheat sheet" — the 41 node types grouped by category,
    one-line gloss each. Copy-paste-derivable from
    `tesserae/research_graph.py` `ResearchNodeType` enum.
  - "Common workflows" — three or four micro-recipes
    (`refresh after edits`, `ask about a paper`, `inspect what a
    session decided`).

**Verification**

- Markdown lint — no broken links, valid frontmatter.
- Manual: queries like "what did we decide about X yesterday" should
  surface the skill in the Claude Code skill picker.

**Commit** — `feat(plugin): using-tesserae skill — orients the agent in the tesserae surface area`

---

## Phase 6 — README + i18n + plugin README

**Goal**: discoverability. Main README points at the plugin; all 7
translations get the same paragraph; the plugin's own `README.md` is
the one-page quickstart users land on after install.

**Files**

- `.claude-plugin/README.md` — full content (overview + the 9
  command table + the 4 hook table + install command + per-project
  opt-out example).
- `README.md` — extend the existing session-graph Integrations bullet
  (or add a new "Claude Code plugin" section under Integrations) to
  link the plugin install command.
- 7 i18n READMEs — same paragraph translated (de/es/fr/ja/ko/ru/zh).
  Reuse the pattern from the session-graph bullet roll-out.
- `docs/integrations/claude-code-plugin.md` — new integration doc.
- 7 i18n translations: `docs/i18n/integrations/claude-code-plugin.{de,es,fr,ja,ko,ru,zh}.md`.

**Verification**

- Render check on GitHub for the English doc + spot-check one
  translation.
- Existing tests still pass (no source code changes).

**Commit** — `docs(plugin): README + integration doc + 7 i18n translations for the Claude Code plugin`

---

## Cross-cutting invariants

These hold across every phase:

- **Zero Python in `.claude-plugin/`**. The plugin is shell, markdown,
  and JSON only. Every command shells out to the `tesserae` CLI.
- **Hooks fail safe**. Missing `tesserae` binary → no-op + log entry.
  Never blocks the user's session.
- **Opt-out is per-project, not global**. Each hook reads
  `.claude/tesserae.local.md` frontmatter; a project that hates the
  PostToolUse hook turns it off without affecting other projects on
  the same machine.
- **Version lockstep**. `plugin.json::version` always matches the
  `tesserae` PyPI version that ships in the same git tag.
- **No new MCP tools**. Phase 7 of the session-graph plan already
  added `list_sessions` + `find_session_findings`; the plugin
  registers the existing `tesserae_mcp` server, it doesn't introduce
  new tools.

## Rollback plan

Each phase is one commit on `main`. Plugin install is fully reversible
via `/plugin uninstall tesserae`. To roll back any phase:
`git revert <commit>`. The plugin directory is additive — removing it
doesn't affect the CLI or MCP server.

The riskiest phase is Phase 4 (hooks) because a misfiring hook can
slow down a session. Mitigation: every hook is opt-out-able per-project
via the local settings file, and `session-end` is the only one
enabled by default — the other three default to off-or-passive.

## Open implementation questions

- **`plugin.json` schema specifics**: confirm the exact field names
  Claude Code expects (`hooks` vs `hooks_config`, etc.) during Phase
  1 by reading the live plugin manifest at
  `~/.claude-personal2/plugins/cache/claude-plugins-official/<some-plugin>/`.
- **Slash-command argument syntax**: verify that the `$ARGUMENTS`
  interpolation pattern + `arguments:` frontmatter shape are still
  current.
- **MCP merge behaviour**: confirm whether plugin `.mcp.json`
  merges-by-replace or merges-by-union with user-managed MCP config.
  If by-replace, document the conflict in the plugin README.

---

**Estimated time**: ~1.5 working days. Phase 1 (~1 hour), Phase 2
(~3 hours), Phase 3 (~2 hours), Phase 4 (~4 hours incl. shell tests),
Phase 5 (~3 hours), Phase 6 (~3 hours + translations).

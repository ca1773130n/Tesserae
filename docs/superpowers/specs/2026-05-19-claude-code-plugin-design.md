# Claude Code plugin for Tesserae — design

> Status: design • 2026-05-19 • owner: Tesserae maintainers • supersedes: nothing

A Claude Code plugin that surfaces Tesserae's CLI as slash commands,
auto-wires the existing `tesserae_mcp` server, ships a skill that
teaches the agent when to invoke which command, and installs four
hooks that close the agent ↔ project memory loop without manual
shell calls.

## Goal

After `/plugin install`, a user in a Claude Code TUI session can
run `/tesserae:compile`, `/tesserae:ask "<question>"`,
`/tesserae:refresh`, etc., without leaving the agent. The MCP tools
(`search_nodes`, `ask`, `list_sessions`, `find_session_findings`,
…) become available to the agent without the user having to edit
their MCP config. The hooks ensure that what the user does in a
session feeds back into the project memory the *next* session
sees — closing the loop the session-graph feature was designed for.

## Non-goals

- Re-implementing CLI logic in the plugin. Every slash command shells
  out to the user's installed `tesserae` binary. No Python in the
  plugin, no duplicated code paths.
- Replacing the CLI. Power users on a plain terminal still use
  `tesserae project compile` directly; the plugin is purely a TUI
  ergonomics layer.
- Marketplace publishing in v1. Plugin lives in-repo at
  `.claude-plugin/`; users install via `/plugin install
  https://github.com/ca1773130n/Tesserae .claude-plugin/`. A
  marketplace mirror is a follow-up.
- A web UI. Slash commands return text. The static site (`tesserae
  project serve`) covers the rich-UI need separately.

## User-visible behaviour

### Slash commands (9 total)

The seven 1:1 wrappers — each takes the same arguments the underlying
CLI subcommand takes:

| Slash | Wraps | Notes |
|---|---|---|
| `/tesserae:setup` | `tesserae project setup` | Opens the interactive setup wizard. |
| `/tesserae:compile [--changed-only] [--no-vault-pull]` | `tesserae project compile` | Full compile. Confirmation gate (PreToolUse hook) intercepts compiles on graphs > 5000 nodes. |
| `/tesserae:ask <question>` | `tesserae project ask` | Returns the answer envelope inline. |
| `/tesserae:sessions-import` | `tesserae sessions discover --import` | Imports normalised sessions from `~/.claude/projects/` (or `~/.codex/sessions/`) into `.tesserae/harness_sessions/`. |
| `/tesserae:build-site` | `tesserae project build-site` | Builds the static site under `.tesserae/site/`. |
| `/tesserae:serve [--port N]` | `tesserae project serve` | Default port 8765. Runs in foreground; user kills with Ctrl-C. |
| `/tesserae:obsidian-sync [--prune-orphans]` | `tesserae project obsidian-sync` | Vault projection sync. |

Plus two macros:

| Slash | Behaviour |
|---|---|
| `/tesserae:refresh` | Runs `sessions-import → compile → obsidian-sync` in sequence. The common "I made edits, update everything" cycle. Emits a one-line summary at the end (`nodes: 2058, edges: 4878, sessions: 12, vault orphans pruned: 2`). |
| `/tesserae:status` | Reads `.tesserae/graph.json` summary, last-compile timestamp from `.build-history.jsonl`, and session counts. Pure read; never mutates. |

### Skill (auto-loads on relevance)

`skills/using-tesserae/SKILL.md` — a single skill that orients the
agent in Tesserae. Description matches queries about wikis, knowledge
graphs, insights/decisions/takeaways from past sessions, "what did we
decide about X", "remind me what we talked about yesterday", etc.

Content:
- One-paragraph overview of what Tesserae produces (typed graph +
  vault + MCP).
- When to prefer the MCP tools (`search_nodes`, `find_session_findings`,
  `ask`) over the slash commands (low-friction lookups inside a
  conversation flow).
- When to suggest a slash command (`/tesserae:refresh` after a long
  doc edit; `/tesserae:setup` for an uninitialised project; etc.).
- Node-type cheat sheet so the agent can decode MCP responses without
  hallucinating types.

### Hooks (4 total)

All hooks are opt-out per-project via `.claude/tesserae.local.md`
frontmatter (see plugin-settings pattern).

| Hook | Event | Behaviour |
|---|---|---|
| `session-start` | `SessionStart` | If `.tesserae/graph.json` exists, prints a one-line summary (`tesserae: 2058 nodes, last compile 2 days ago`). If last compile > 7 days ago, suggests `/tesserae:refresh`. No mutation. |
| `session-end` | `SessionEnd` | Backgrounds `tesserae sessions discover --import && tesserae project compile` so the conversation just-ended becomes graph nodes for the next session. Logs to `.tesserae/.session-end-hook.log`. |
| `posttooluse-edit` | `PostToolUse` matching `Edit\|Write` on paths under `docs/` | Debounced (one run per 60s window) `tesserae project compile --changed-only`. Disabled by default; user opts in via the per-project local settings. |
| `pretooluse-compile` | `PreToolUse` matching `Bash` with `tesserae project compile` in the command | When `graph.json` reports > 5000 nodes, ask for confirmation before letting the compile proceed. Avoids surprise multi-minute waits when the agent autonomously invokes compile. |

### MCP auto-registration

`.claude-plugin/.mcp.json` registers a single MCP server named
`tesserae` whose command is `tesserae_mcp`. On `/plugin install`,
Claude Code merges this into the user's session-scoped MCP config so
the agent can call `search_nodes`, `ask`, `list_sessions`, etc.
immediately. No manual `claude_desktop_config.json` edits required.

## Architecture

### File layout

```
.claude-plugin/
├── plugin.json                       # manifest (name, version, description)
├── README.md                         # plugin-specific quickstart (linked from main README)
├── commands/
│   ├── compile.md                    # YAML frontmatter + bash command body
│   ├── ask.md
│   ├── setup.md
│   ├── sessions-import.md
│   ├── build-site.md
│   ├── serve.md
│   ├── obsidian-sync.md
│   ├── refresh.md                    # macro: 3 commands chained
│   └── status.md                     # macro: graph.json read + format
├── skills/
│   └── using-tesserae/
│       └── SKILL.md                  # auto-loaded skill
├── hooks/
│   ├── session-start.sh              # exec'd at session start
│   ├── session-end.sh                # exec'd at session end
│   ├── posttooluse-edit.sh           # exec'd after Edit/Write matching docs/
│   └── pretooluse-compile.sh         # exec'd before Bash compile
└── .mcp.json                         # auto-registered MCP server config
```

Each command's `.md` file follows the standard Claude Code slash-command
shape: YAML frontmatter declaring args + description + tool
permissions, body containing the bash to execute.

### Hook scripts

Each hook is a small shell script (no Python — keeps deps minimal).
Logs to `.tesserae/.<hook-name>.log` for debuggability. Exit code 0
allows the event to proceed; non-zero blocks (only used by
`pretooluse-compile`).

### MCP registration

```jsonc
// .claude-plugin/.mcp.json
{
  "mcpServers": {
    "tesserae": {
      "command": "tesserae_mcp",
      "args": []
    }
  }
}
```

The `tesserae_mcp` binary ships with the existing `pip install
tesserae` — no separate install. If the binary isn't on PATH, the MCP
server entry silently fails on startup (Claude Code logs but continues);
the slash commands still work because they shell out to `tesserae` not
to MCP.

### Settings + opt-out

Following the plugin-settings convention:

```
.claude/tesserae.local.md
```

A user can edit this file to disable individual hooks per-project:

```yaml
---
hooks:
  session_end: false           # don't auto-compile on session close
  posttooluse_edit: true       # opt in to the live-recompile hook
  session_start: true
  pretooluse_compile: true
---
```

Plugin reads the frontmatter via a tiny `bash + sed` helper in each
hook script (no jq dependency). Missing file → defaults apply.

## Distribution

```bash
# User installs the plugin
/plugin install https://github.com/ca1773130n/Tesserae .claude-plugin/

# Or, for local development of the plugin itself
/plugin install /path/to/Tesserae/.claude-plugin/
```

Plugin version in `plugin.json` matches the `tesserae` Python package
version. Each PyPI release of `tesserae` is implicitly a plugin release
since users pin both via the same git tag.

## Hook safety

All hooks fail closed in the operator's favour:

- **session-end**: if `tesserae` binary is missing, hook exits 0
  silently. No alarming red errors at session close.
- **posttooluse-edit**: debounced via a lock file at
  `.tesserae/.recompile.lock`. If a compile is already running, the
  hook no-ops.
- **session-start**: pure read; can't damage the project.
- **pretooluse-compile**: only blocks when the explicit
  > 5000-node threshold is hit AND the user hasn't already
  confirmed in this session (one-confirm-per-session via a lock file).
  Errs on the side of letting compile proceed when the heuristic is
  uncertain.

## Testing strategy

| Layer | Test type | What |
|---|---|---|
| Slash command parsing | unit (markdown lint) | Each command file has valid YAML frontmatter + a bash body that doesn't reference undefined env vars. |
| Hook scripts | shell test (bats or plain `sh -n`) | Syntax check + smoke run with a fake `tesserae` binary on PATH that records args. Confirms hooks call the right commands with the right args. |
| MCP auto-registration | integration | Install plugin into a tmpdir Claude Code config, confirm `tesserae` server appears in `mcp_servers`. |
| Skill activation | manual | Type a few queries that should activate the skill ("what did we decide about X", "build the wiki", "compile the graph") and confirm SKILL.md gets loaded. |
| End-to-end install | manual | `/plugin install <path>`, run each of the 9 commands, confirm no errors. Document any setup steps in the plugin README. |

## Open questions / risks

- **Plugin metadata format.** Claude Code plugins are a newish surface;
  the exact `plugin.json` schema may evolve. v1 targets the current
  shape documented in `~/.claude-personal*/plugins/cache/claude-plugins-official/`;
  if the schema changes upstream, we update during a follow-up.
- **Hook latency.** `session-end` triggers a full compile in the
  background. On a 5000+ node project that's 30s-2min; the user
  doesn't see it because the session is already closing, but if the
  user re-opens immediately, the next session may start before
  compile finishes. Acceptable for v1 — the next-next session catches
  up.
- **MCP name collision.** Users with an existing `tesserae` MCP entry
  (e.g. from a manual `tesserae project mcp-config` setup) will see a
  conflict. Plugin's `.mcp.json` should NOT clobber user-managed
  config. Verify Claude Code's merge behaviour during implementation.
- **`tesserae` not on PATH.** Hooks need to find the binary. They
  attempt `command -v tesserae` first, then fall back to
  `~/.local/bin/tesserae`, then `~/Library/Python/3.*/bin/tesserae`.
  If none found → no-op + log.

## Out of scope (deferred follow-ups)

- **Marketplace submission.** v1 ships in-repo. If discoverability
  becomes the bottleneck, mirror to a marketplace as a follow-up.
- **Sub-agents.** A `/tesserae` subagent with all tools pre-wired
  would be powerful but adds another component layer to maintain.
  Defer until the slash commands prove out.
- **Cross-project commands.** Slash commands assume `cwd ==
  project_root`. For multi-project workflows the user falls back to
  the `--project` flag on the underlying CLI. v2 could add a
  `/tesserae:project-switch` command if demand warrants.
- **Hook telemetry.** No metrics on which hooks fire how often. If
  users complain about a noisy hook, we add per-hook `--dry-run` and
  `--verbose` flags then.

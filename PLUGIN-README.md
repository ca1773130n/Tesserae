# Tesserae — Claude Code plugin

Slash commands, hooks, a skill, and MCP auto-registration for [Tesserae](https://github.com/ca1773130n/Tesserae) — the typed-graph project-memory compiler. Lets you drive Tesserae from inside a Claude Code TUI session instead of dropping out to a shell.

## Install

Requires `tesserae` already installed (`pip install tesserae` or `pipx install tesserae`).

```bash
# In a Claude Code session, from a local checkout
/plugin install /path/to/Tesserae/
```

For the remote-install form against this repo, check `/plugin install --help` in your version of Claude Code — the URL+subpath syntax has shifted between releases.

## Slash commands

| Command | What it runs |
|---|---|
| `/tesserae:setup` | Interactive setup wizard — detects sources, asks which companion tools to enable, writes `.tesserae/config.json`. (`disable-model-invocation: true` — only you can invoke it.) |
| `/tesserae:compile [--changed-only] [--no-vault-pull]` | `tesserae project compile`. Walks sources, extracts the typed graph, writes the vault + site. |
| `/tesserae:ask "<question>"` | `tesserae project ask` via a quote-stripping wrapper. Handles ASCII and Unicode smart quotes; un-escapes inner `\"` / `\'`. |
| `/tesserae:sessions-import` | `tesserae sessions discover --import`. Normalises Claude Code / Codex sessions into `.tesserae/harness_sessions/`. |
| `/tesserae:build-site` | `tesserae project build-site`. Static site at `.tesserae/site/`. |
| `/tesserae:serve [--host HOST] [--port PORT]` | `tesserae project serve`. Default port 8765. |
| `/tesserae:obsidian-sync [--prune-orphans] [--watch]` | `tesserae project obsidian-sync`. |
| `/tesserae:refresh` | **Macro**: chains import → compile → obsidian-sync with stop-on-failure semantics. Emits a one-line summary at the end (`nodes=NNN edges=NNN processed=NNN sessions=NNN vault_orphans_pruned=NNN`). |
| `/tesserae:status` | **Macro**: read-only status of the current project — graph counts, last compile, session count, configured Obsidian vault. |

## Hooks

All four are opt-out per-project via `.claude/tesserae.local.md` frontmatter (see "Per-project opt-out" below).

| Hook | When it fires | What it does | Default |
|---|---|---|---|
| `SessionStart` | Every session start in a Tesserae project | Prints a one-liner: `tesserae: N nodes, M edges, last compile <ts>`. Warns if last compile > 7 days. | on |
| `SessionEnd` | Every session close | Backgrounds `sessions discover --import` + `project compile` so this conversation's insights become graph nodes for the next session. Detached via `setsid`/`nohup` so it survives session reap. | on |
| `PostToolUse` (Edit / Write / MultiEdit) | Agent edits a file under `docs/` | Debounced `tesserae project compile --changed-only`. Path filter applied inside the script (matchers can't filter by path). | **off** — opt in if you want live wiki updates as you write docs |
| `PreToolUse` (Bash → `tesserae project compile`) | Agent invokes compile via Bash | If graph has > 5000 nodes, emits `{"permissionDecision": "ask", "systemMessage": "..."}` so Claude Code surfaces a confirmation dialog. | on |

## MCP auto-registration

`.mcp.json` registers a single MCP server named `tesserae` whose command is `tesserae_mcp`. After install, the agent gets the existing MCP tools without manual `claude_desktop_config.json` edits:

- `mcp__plugin_tesserae_tesserae__ask` — natural-language Q&A
- `mcp__plugin_tesserae_tesserae__search_nodes` — keyword node lookup
- `mcp__plugin_tesserae_tesserae__node_context` — node + 1-hop neighbourhood
- `mcp__plugin_tesserae_tesserae__list_sessions` — Session envelopes for active project
- `mcp__plugin_tesserae_tesserae__find_session_findings` — Session-derived findings for a given node
- `mcp__plugin_tesserae_tesserae__list_projects` / `activate_project` / `register_project` / `unregister_project` — multi-project registry navigation

## Skill

`skills/using-tesserae/SKILL.md` auto-loads when your query mentions the typed graph, past-session recall, or any tesserae workflow. Tells the agent which MCP tool to call vs which slash command to suggest, includes a node-type cheat sheet, and lists three common recipes.

## Per-project opt-out

Create `.claude/tesserae.local.md` in your project root:

```yaml
---
hooks:
  session_start: true        # default on
  session_end: true          # default on
  posttooluse_edit: true     # default OFF — opt in for live wiki updates
  pretooluse_compile: true   # default on
---
```

Defaults apply when the file (or any individual key) is missing.

## Verify install

```
/plugin list           # tesserae should appear
/mcp                   # `tesserae` MCP server should be registered
/tesserae:status       # prints the active project's graph stats
```

If the MCP server is missing, the `tesserae_mcp` binary isn't on the PATH Claude Code inherited at launch:

```bash
which tesserae_mcp
```

If `which` returns a path but the server still doesn't register, Claude Code was launched from a shell whose `PATH` didn't include the bin directory (pipx installs to `~/.local/bin/` which isn't on every login shell). Restart from a shell where `which tesserae_mcp` succeeds, or `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.zshrc`.

## Uninstall

```
/plugin uninstall tesserae
```

Plugin uninstall is reversible and does not touch any project's `.tesserae/` directory.

## See also

- [Implementation plan](https://github.com/ca1773130n/Tesserae/blob/main/docs/superpowers/plans/2026-05-19-claude-code-plugin-plan.md)
- [Design spec](https://github.com/ca1773130n/Tesserae/blob/main/docs/superpowers/specs/2026-05-19-claude-code-plugin-design.md)
- [Tesserae itself](https://github.com/ca1773130n/Tesserae)

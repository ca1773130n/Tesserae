# Tesserae — Claude Code plugin

MCP auto-registration for [Tesserae](https://github.com/ca1773130n/Tesserae), the typed-graph project-memory compiler. Slash commands, hooks, and a skill arrive in later releases — see the roadmap below.

## Install

Requires `tesserae` already installed (`pip install tesserae` or `pipx install tesserae`).

```bash
# In a Claude Code session, from a local checkout (verified path)
/plugin install /path/to/Tesserae/plugin/
```

Remote install from this repo via Claude Code's plugin URL syntax is documented but not yet verified end-to-end against the released CLI — check the plugin install help (`/plugin install --help`) in your version for the exact remote form.

## What you get today

- **MCP auto-registration** for the `tesserae_mcp` server. After install, the agent can call the tools the existing MCP server exposes: `ask`, `search_nodes`, `list_projects`, `list_sessions`, `find_session_findings`, etc. Tool names are prefixed by Claude Code on the plugin path — e.g. `mcp__plugin_tesserae_tesserae__ask`.

## Roadmap (tracked by the implementation plan)

| Phase | Adds | Status |
|---|---|---|
| 2 | Seven 1:1 slash commands (`/tesserae:compile`, `/tesserae:ask`, `…`) | not yet shipped |
| 3 | Two workflow macros (`/tesserae:refresh`, `/tesserae:status`) | not yet shipped |
| 4 | Four hooks (SessionStart / SessionEnd / PostToolUse / PreToolUse) | not yet shipped |
| 5 | `using-tesserae` skill | not yet shipped |
| 6 | README + i18n integration doc | not yet shipped |

See the full plan at [`docs/superpowers/plans/2026-05-19-claude-code-plugin-plan.md`](https://github.com/ca1773130n/Tesserae/blob/main/docs/superpowers/plans/2026-05-19-claude-code-plugin-plan.md) on GitHub.

## Verify

```
/plugin list           # tesserae should appear
/mcp                   # a `tesserae` MCP server should be registered
```

If the MCP server is missing, the `tesserae_mcp` binary isn't on the `PATH` Claude Code inherited at launch:

```bash
which tesserae_mcp
```

If `which` returns a path but the MCP server still doesn't register, the most common cause is that Claude Code was started from a shell whose `PATH` didn't include the binary's directory (e.g. pipx installs to `~/.local/bin/` which isn't on every login shell's `PATH` by default). Restart Claude Code from a terminal where `which tesserae_mcp` succeeds, or add the bin directory to your shell rc: `echo 'export PATH=$HOME/.local/bin:$PATH' >> ~/.zshrc` (or `~/.bashrc`).

## Uninstall

```
/plugin uninstall tesserae
```

Plugin uninstall is reversible and does not touch any project's `.tesserae/` directory.

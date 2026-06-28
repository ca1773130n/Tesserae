# Claude Code plugin

<!-- translations:start -->
<p align="center"><a href="../i18n/integrations/claude-code-plugin.ko.md">한국어</a> · <a href="../i18n/integrations/claude-code-plugin.zh.md">中文</a> · <a href="../i18n/integrations/claude-code-plugin.ja.md">日本語</a> · <a href="../i18n/integrations/claude-code-plugin.ru.md">Русский</a> · <a href="../i18n/integrations/claude-code-plugin.es.md">Español</a> · <a href="../i18n/integrations/claude-code-plugin.fr.md">Français</a> · <a href="../i18n/integrations/claude-code-plugin.de.md">Deutsch</a></p>
<!-- translations:end -->

Tesserae ships a [Claude Code](https://docs.claude.com/en/docs/claude-code) plugin so you can drive the full Tesserae workflow from inside a TUI session — slash commands, an auto-registered MCP server, a skill that orients the agent, and four hooks that close the agent↔project-memory loop. The plugin lives in-repo at `plugin/`.

## Install

```bash
# In a Claude Code session, from a local checkout
/plugin install /path/to/Tesserae/
```

Pre-req: `tesserae` already installed (`pip install tesserae` or `pipx install tesserae`). If installing via pipx, make sure `~/.local/bin` is on the PATH Claude Code inherits at launch.

## What's shipped

* **9 slash commands** — seven 1:1 wrappers around the CLI (`/tesserae:compile`, `/tesserae:ask`, `/tesserae:sessions-import`, `/tesserae:build-site`, `/tesserae:serve`, `/tesserae:obsidian-sync`, `/tesserae:setup`) plus two workflow macros (`/tesserae:refresh` chains import + compile + obsidian-sync; `/tesserae:status` shows graph counts and last compile).
* **Auto-MCP-registration** for the `tesserae` server — the agent gets the full tool surface as `mcp__plugin_tesserae_tesserae__<tool>` without manual config edits: graph queries (`search_nodes`, `node_context`, `graph_ppr`, `search_facts`), the on-demand `compile_context` / `list_communities` / `fresh_insights` compiler, session memory (`ask`, `list_sessions`, `find_session_findings`, `find_code_symbol_mentions`), and guided `tesserae_setup_plan` / `tesserae_setup_apply`. See [mcp.md](mcp.md) for the complete list.
* **`using-tesserae` skill** — auto-loads when you ask about the typed graph, past-session recall, wiki/vault content, or any tesserae workflow. Teaches the agent which MCP tool to use vs which slash command to suggest.
* **5 hooks** — `SessionStart` prints a graph summary; `SessionEnd` backgrounds an import+compile so this conversation's insights become graph nodes for the next session; two `PostToolUse` hooks fire on `Edit`/`Write`/`MultiEdit` — one does an opt-in incremental recompile on docs/ edits, the other debounces (~30s) a code-graph sync; `PreToolUse` (on `Bash`) gates large-graph compiles via a confirmation dialog.

Full details, the complete command/hook tables, and per-project opt-out instructions are in the plugin's own [`plugin/README.md`](https://github.com/ca1773130n/Tesserae/blob/main/PLUGIN-README.md).

## Why a plugin AND an MCP server?

Different surfaces, different roles:

- **MCP tools** = read-only graph queries the agent calls during a conversation. Always-on, low-friction.
- **Slash commands** = workflow actions you explicitly invoke (compile, refresh, obsidian-sync). High-leverage but should be your decision.

You can use the MCP server alone (manual `claude_desktop_config.json` edit via `tesserae projects mcp-config`) — the plugin just packages it together with the slash commands, the skill, and the hooks so installation is one step.

## Verify install

```
/plugin list           # tesserae should appear
/mcp                   # `tesserae` MCP server should be registered
/tesserae:status       # prints the resolved project's graph stats
```

## Uninstall

```
/plugin uninstall tesserae
```

Reversible. Does not touch any project's `.tesserae/` directory.

## See also

- [Implementation plan](../superpowers/plans/2026-05-19-claude-code-plugin-plan.md)
- [Design spec](../superpowers/specs/2026-05-19-claude-code-plugin-design.md)
- [Sessions integration](sessions.md) — the session-graph feature the plugin's hooks close the loop on

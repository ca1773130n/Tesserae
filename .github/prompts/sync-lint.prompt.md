---
description: Lint and validate Claude Code config before syncing
---

Validate the source Claude Code config before syncing. Checks for duplicate MCP server names, malformed JSON, oversized CLAUDE.md, broken skill references, unclosed sync tags, and other common issues.

Usage: /sync-lint [--scope user|project|all]

Options:
- --scope: Config scope to lint (default: all)

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_lint.py [user-provided arguments]

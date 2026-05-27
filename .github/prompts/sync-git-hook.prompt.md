---
description: Install/uninstall git post-commit hook for automatic config sync
---

Install a git post-commit hook that automatically syncs config whenever CLAUDE.md, .claude/, or .mcp.json changes in a commit.

Usage: /sync-git-hook [install|uninstall|status]

Actions:
- install: Install the post-commit hook in the current git repo
- uninstall: Remove the HarnessSync section from the post-commit hook
- status: Show whether the hook is installed (default)

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_git_hook.py [user-provided arguments]

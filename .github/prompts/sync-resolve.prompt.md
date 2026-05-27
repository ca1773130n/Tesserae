---
description: Interactively resolve conflicts caused by manual edits to target harness config files
---

When HarnessSync detects that a target config file was manually edited since the last sync, it warns but leaves the file untouched. Use this command to resolve those conflicts interactively.

For each conflicted file it shows a numbered hunk diff between the backup snapshot (what HarnessSync last wrote) and your current file. You pick `mine`, `theirs`, or `skip` per hunk. The resolved file is written and the conflict flag is cleared so the next `/sync` proceeds normally.

Usage: /sync-resolve [TARGET] [--list] [--no-interactive]

Options:
- TARGET: Resolve conflicts for a specific harness only (e.g. codex, gemini)
- --list: List all targets with active conflicts and exit
- --no-interactive: Auto-choose 'mine' for every hunk (keep your edits everywhere)

Examples:
- /sync-resolve              — resolve all conflicts interactively
- /sync-resolve cursor       — resolve only Cursor conflicts
- /sync-resolve --list       — see which targets have conflicts
- /sync-resolve --no-interactive  — keep all your edits without prompting

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_resolve.py [user-provided arguments]

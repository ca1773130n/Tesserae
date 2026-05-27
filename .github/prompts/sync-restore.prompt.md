---
description: List and restore HarnessSync config snapshots
---

List available config snapshots or restore a previous state. Snapshots are
created automatically before every sync operation.

Usage: /sync-restore [--list] [--latest] [--date YYYY-MM-DD] [--target codex|gemini|opencode]

Options:
- --list: Show all available snapshots (default when no flags given)
- --latest: Restore the most recent snapshot for each target
- --date YYYY-MM-DD: Restore closest snapshot to the given date
- --target NAME: Limit to a specific target harness

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_restore.py [user-provided arguments]

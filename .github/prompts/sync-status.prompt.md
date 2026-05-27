---
description: Show HarnessSync status and drift detection for all targets
---

Show sync status, last sync time per target, and drift detection.
Also available as: /sync-health status

Usage: /sync-status [--account NAME] [--list-accounts]

Options:
- --account NAME: Show status for specific account
- --list-accounts: List all configured accounts with sync status

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_status.py [user-provided arguments]

---
description: Live terminal dashboard showing sync status, drift level, and health metrics for all configured harness targets
---

Open a live terminal UI that shows last sync time, drift level, and health status for each configured target harness in a single view. Eliminates the need to run multiple commands to get a holistic picture.

Usage: /sync-dashboard [--live] [--refresh N] [--account ACCOUNT] [--project-dir DIR]

Options:
- --live: Auto-refresh every 30 seconds until Ctrl+C
- --refresh N: Refresh every N seconds (0 = show once and exit, default: 0)
- --account ACCOUNT: Account name for multi-account setups
- --project-dir DIR: Override project directory

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_dashboard.py [user-provided arguments]

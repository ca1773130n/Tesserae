---
description: Schedule periodic background syncs via cron (independent of Claude Code sessions)
---

Schedule HarnessSync to run automatically at regular intervals using cron (macOS/Linux).
Syncs happen in the background even when Claude Code is not running, ensuring your
harness configs stay current after direct file edits or git pulls.

Usage: /sync-schedule --every INTERVAL [--scope SCOPE]

Options:
- --every INTERVAL: Sync interval: 30m, 1h, 6h, 12h, 1d, etc.
- --scope SCOPE: Sync scope: user | project | all (default: all)
- --list: Show all HarnessSync scheduled syncs
- --remove: Remove the cron job for this project
- --dry-run: Print the cron line without installing it

Examples:
  /sync-schedule --every 1h
  /sync-schedule --every 6h --scope project
  /sync-schedule --list
  /sync-schedule --remove

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_schedule.py [user-provided arguments]

---
description: Show sync audit log — persistent history of every sync operation with JSON/CSV export
---

Display the persistent sync audit log showing timestamp, what changed, and which targets were updated. Export history as machine-queryable JSON or CSV for compliance and team review.

Usage: /sync-log [--tail N] [--clear] [--export-json FILE] [--export-csv FILE] [--target TARGET] [--since DATE]

Options:
- --tail N: Show only the last N log entries
- --clear: Clear the sync log (irreversible)
- --export-json FILE: Export full sync history as JSON (use - for stdout)
- --export-csv FILE: Export full sync history as CSV (use - for stdout)
- --target TARGET: Filter entries by target harness name
- --since DATE: Show entries since a date (YYYY-MM-DD format)
- --interactive: Interactive timeline browser with pagination and search

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_log.py [user-provided arguments]

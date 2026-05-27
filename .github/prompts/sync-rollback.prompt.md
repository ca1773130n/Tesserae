---
description: Restore a target harness config to a previous backed-up state — supports time-travel by timestamp or git commit
---

List available sync backups and restore any target harness config to a previous state. HarnessSync automatically creates backups before each sync.

Supports time-travel rollback: restore to "yesterday" or "before commit abc123" without needing to know the exact backup name.

Usage: /sync-rollback [--list] [--target TARGET] [--backup NAME] [--timestamp DATE] [--before-commit SHA] [--steps N]

Options:
- --list: List all available backups for all targets
- --target TARGET: Target to restore (codex, gemini, opencode, cursor, etc.)
- --backup NAME: Specific backup to restore (default: most recent)
- --label LABEL: Find backup by label instead of name
- --timestamp DATE: Time-travel: restore backup from on or before this date (YYYY-MM-DD or YYYY-MM-DDTHH:MM)
- --before-commit SHA: Time-travel: restore the backup taken just before the given git commit SHA
- --steps N: Undo the last N sync operations using the undo stack (requires --target)
- --dry-run: Preview what would be restored without writing files

Examples:
- /sync-rollback --list
- /sync-rollback --target codex
- /sync-rollback --target codex --steps 3
- /sync-rollback --target codex --steps 1 --dry-run
- /sync-rollback --target gemini --timestamp 2025-03-10
- /sync-rollback --target codex --before-commit abc1234
- /sync-rollback --target gemini --backup AGENTS.md_20240115_143022

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_rollback.py [user-provided arguments]

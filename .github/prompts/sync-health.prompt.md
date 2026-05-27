---
description: Unified diagnostic hub — harness health, sync status, and feature parity
---

Comprehensive diagnostic hub for HarnessSync. Runs the full health dashboard by
default; use subcommands to access sync status or feature parity reports.

Usage: /sync-health [SUBCOMMAND] [OPTIONS]

Subcommands:
- (none):   Harness installation status, config health score, readiness checklist, skill compatibility
- status:   Sync status, last sync time per target, and drift detection (same as /sync-status)
- parity:   Feature parity report — what each harness supports from your Claude Code config (same as /sync-parity)

Options (default subcommand):
- --score: Show config health score and recommendations
- --readiness: Show harness readiness checklist (prerequisites per target)
- --skills: Show skill compatibility report across targets
- --all: Show everything (default behavior)

Examples:
  /sync-health                    # full health dashboard
  /sync-health status             # drift detection and sync timestamps
  /sync-health parity             # compatibility scores per target
  /sync-health status --account mywork
  /sync-health parity --scope project

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_health.py [user-provided arguments]

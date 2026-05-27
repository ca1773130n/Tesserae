---
description: Show sync analytics — coverage, fidelity per target, and problem sections
---

Show HarnessSync analytics: how many rules/skills/agents/MCP servers are synced,
what percentage of your Claude Code config is reflected in each target harness,
and which sections are systematically losing fidelity.

Usage: /sync-report [--scope SCOPE] [--json]

Options:
- --scope SCOPE: Sync scope: user | project | all (default: all)
- --json: Output raw JSON instead of formatted report

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_report.py [user-provided arguments]

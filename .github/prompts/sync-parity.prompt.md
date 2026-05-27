---
description: Feature parity report — what each harness supports from your Claude Code config
---

Show a structured report of every Claude Code feature in use with per-target
compatibility scores and specific gaps.
Also available as: /sync-health parity

Usage: /sync-parity [--scope user|project|all]

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_parity.py [user-provided arguments]

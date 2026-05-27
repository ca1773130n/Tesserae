---
description: Show capability matrix — which config sections each harness supports natively, adapts, or drops
---

Show a table of every config section (MCP servers, rules, skills, agents, etc.) and which target harnesses support it natively, which get approximate translation, and which silently drop it.

Usage: /sync-matrix [--notes]

Options:
- --notes: Show detailed per-cell notes explaining translation behavior

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_matrix.py [user-provided arguments]

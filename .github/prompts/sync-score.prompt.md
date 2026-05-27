---
description: Compute portability score (0-100) for your Claude Code config
---

Show how well your Claude Code config translates to other harnesses. Outputs a 0-100 portability score with four sub-scores (skill portability, MCP dependency breadth, path hygiene, settings coverage) and the top 2 specific fixes for each.

Usage: /sync-score [--format text|json]

Options:
- --format: Output format: text (default) or machine-readable json

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_score.py [user-provided arguments]

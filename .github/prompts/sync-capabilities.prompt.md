---
description: Show which Claude Code features each target harness supports natively, partially, or not at all
---

Show a visual matrix of Claude Code behavioral features (MCP, hooks, agents, permissions, etc.) across target harnesses — before you sync. Helps you understand what you'll lose before committing to a sync operation.

Different from /sync-matrix (which shows config section support): /sync-capabilities focuses on AI behavioral features and runtime capabilities.

Usage: /sync-capabilities [--target TARGET] [--category CATEGORY] [--detail] [--targets a,b,c]

Options:
- --target TARGET: Show detailed feature report for a single target (codex, gemini, opencode, cursor, aider, windsurf)
- --category CATEGORY: Filter by category (instructions, integrations, lifecycle, security, settings, harnesssync, commands)
- --detail: Show per-cell implementation notes for each target
- --targets a,b,c: Comma-separated list of targets to include in the matrix

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_capabilities.py [user-provided arguments]

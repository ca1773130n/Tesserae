---
description: Generate a visual map of your entire HarnessSync config topology
---

Generate a Markdown diagram showing your entire config topology: source, all targets, what's synced vs skipped per section, which MCP servers are active where, and a capability matrix.

Usage: /sync-map [--scope user|project|all] [--output FILE]

Options:
- --scope: Config scope to map (default: all)
- --output FILE: Write map to a file instead of stdout (e.g., CONFIG-MAP.md)

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_map.py [user-provided arguments]

---
description: Show portability coverage matrix for each harness
---

Check which config elements (MCP servers, skills, rules, env vars, permissions) will sync cleanly, be approximated, or be dropped for each target harness.

Usage: /sync-coverage [--target <harness>] [--format text|json]

Options:
- --target: Limit output to a specific harness (e.g. codex, gemini)
- --format: Output format — text (default) or json
- --scope: Config scope to read (default: all)

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_coverage.py [user-provided arguments]

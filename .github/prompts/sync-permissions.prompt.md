---
description: Visualize permission boundary translation across harnesses
---

Show which Claude Code tool permissions and approval-mode settings can be
translated to each target harness, which are approximated, and which are
silently dropped with no equivalent.

Helps understand security implications before running /sync.

Usage: /sync-permissions [--target TARGET] [--gaps-only] [--json] [--scope all|user|project]

Options:
- --target TARGET: Show only one specific harness
- --gaps-only: Show only dropped or comment-only (unenforced) settings
- --json: Output results as JSON
- --scope SCOPE: Config scope to read (default: all)

Examples:
- /sync-permissions                    — full permission boundary report
- /sync-permissions --gaps-only        — only show what gets dropped
- /sync-permissions --target codex     — show Codex translation only
- /sync-permissions --json             — machine-readable output

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_permissions.py [user-provided arguments]

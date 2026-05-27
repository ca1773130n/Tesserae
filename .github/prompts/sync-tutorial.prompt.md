---
description: Interactive tutorial — scaffold a TaskFlow example project and learn HarnessSync step by step
---

Learn HarnessSync by building a real project. Scaffolds a TaskFlow todo app and walks you through
syncing every config surface (CLAUDE.md, rules, permissions, skills, agents, commands, MCP, hooks,
annotations) to all 11 target harnesses.

Usage: /sync-tutorial [action] [--dir PATH]

Actions:
- start: Scaffold the example project and begin the tutorial
- next: Advance to the next step (default)
- reset: Remove tutorial state and start over
- status: Show current step and progress
- goto N: Jump to step N (for returning users)
- cleanup: Remove the scaffolded project directory entirely

Options:
- --dir PATH: Target directory (default: /tmp/taskflow-playground)

Examples:
- /sync-tutorial start                    # Begin the tutorial
- /sync-tutorial start --dir ~/playground # Use custom directory
- /sync-tutorial next                     # Proceed to next step
- /sync-tutorial goto 8                   # Jump to annotations step
- /sync-tutorial status                   # Check progress

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_tutorial.py [user-provided arguments]

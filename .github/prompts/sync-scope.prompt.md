---
description: Visualize rule scope hierarchy, detect conflicts, and preview which skills/agents sync to each harness
---

Show which CLAUDE.md rules apply at each scope level (global, project, subdirectory) and highlight conflicts where the same rule name appears at multiple scopes.

With --assets, also shows which skills and agents will be synced to each registered harness target based on their YAML frontmatter `sync:` tags.

Usage: /sync-scope [--project-dir DIR] [--assets] [--target TARGET]

Options:
- --project-dir DIR: Override project directory
- --assets: Show per-target visibility for skills, agents, and commands
- --target TARGET: With --assets, restrict output to a single target harness (e.g. codex)

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_scope.py [user-provided arguments]

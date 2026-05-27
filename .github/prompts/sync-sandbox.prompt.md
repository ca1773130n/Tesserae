---
description: Simulate a full sync in a temp directory — browse the output before committing
---

Run a full HarnessSync simulation in an isolated temp directory, generating the
complete file tree that each target would receive — without writing anything to
your real filesystem. More powerful than --dry-run: files are fully written and
can be browsed or diffed before you commit to a real sync.

Usage: /sync-sandbox [--scope SCOPE] [--only SECTIONS] [--only-targets LIST] [--keep] [--json]

Options:
- --scope SCOPE: Sync scope: user | project | all (default: all)
- --only SECTIONS: Comma-separated sections (rules,skills,agents,commands,mcp,settings)
- --skip SECTIONS: Sections to exclude from simulation
- --only-targets LIST: Comma-separated target harnesses to simulate
- --keep: Keep the sandbox directory after showing the report (for manual inspection)
- --json: Output raw JSON report

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_sandbox.py [user-provided arguments]

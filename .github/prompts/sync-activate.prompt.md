---
description: Activate a harness context — show sync summary and export env vars
---

Activate a harness context: shows what's synced to it, outputs shell env-var exports, and optionally opens the config file. Reduces cognitive overhead when switching between coding tools mid-session.

Usage: /sync-activate [harness] [--export] [--open] [--list] [--json]

Options:
- harness: Harness to activate (codex, gemini, opencode, cursor, aider, windsurf)
- --export: Emit shell exports only (eval-able: eval $(sync-activate codex --export))
- --open: Open the primary config file in $EDITOR
- --list: List all harnesses and their sync state
- --json: Output summary as JSON

Shell integration (add to ~/.zshrc):
  harness() { eval "$(/path/to/sync-activate "$1" --export)"; }

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_activate.py [user-provided arguments]

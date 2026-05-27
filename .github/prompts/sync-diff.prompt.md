---
description: Show a side-by-side diff of Claude Code config vs what's currently written to each target harness
---

Compare what Claude Code has in CLAUDE.md against what each target harness currently has on disk. Makes config drift visible at a glance without needing to manually open multiple files.

Usage: /sync-diff [--target TARGET] [--mode MODE] [--account ACCOUNT] [--project-dir DIR]

Options:
- --target TARGET: Diff only this specific target (codex, gemini, opencode, cursor, aider, windsurf)
- --mode unified|side-by-side: Display mode (default: unified). unified shows git-style diffs; side-by-side shows parallel columns
- --account ACCOUNT: Account name for multi-account setups
- --project-dir DIR: Override project directory

Examples:
- /sync-diff                    Show unified diffs for all targets
- /sync-diff --target gemini    Show only the Gemini diff
- /sync-diff --mode side-by-side  Show parallel column comparison

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_diff.py [user-provided arguments]

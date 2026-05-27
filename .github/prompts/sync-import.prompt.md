---
description: Import configuration FROM a target harness (Gemini, Codex, Cursor, etc.) INTO Claude Code format
---

HarnessSync normally pushes Claude Code config out to other harnesses. This command reverses the flow: it reads an existing harness config and converts it to Claude Code format, staging the result under `.claude/imported/` for review before merging.

Usage: /sync-import TARGET [--path PATH] [--merge] [--dry-run]

Arguments:
- TARGET: Harness to import from (gemini, codex, cursor, aider, windsurf, opencode, ...)

Options:
- --path PATH: Path to the target harness config root (default: auto-detect in current project)
- --merge: After staging, immediately merge into live Claude Code config without prompting
- --dry-run: Preview what would be imported without writing any files
- --project-dir DIR: Override project directory

Examples:
- /sync-import gemini              — stage Gemini config for review
- /sync-import codex --merge       — import Codex config and merge immediately
- /sync-import cursor --dry-run    — preview what would be imported from Cursor
- /sync-import gemini --path ~/other-project  — import from a different directory

Note: Adapters that have not yet implemented `import_to_claude()` will return nothing. The base implementation is a no-op; specific adapters can override it.

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_import.py [user-provided arguments]

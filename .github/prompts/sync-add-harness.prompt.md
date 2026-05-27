---
description: Bootstrap a new AI coding harness in one command
---

Add a new AI coding tool to HarnessSync. Detects the installed tool, translates your current Claude Code config into its native format, and verifies the result — replacing the 30–60 minute manual setup process.

Usage: /sync-add-harness [NAME] [--list] [--dry-run] [--force] [--project-dir PATH]

Options:
- NAME: Harness to add (codex, gemini, opencode, cursor, aider, windsurf, cline, continue, zed, neovim). Omit to auto-detect.
- --list: List detected but unconfigured harnesses and exit
- --dry-run: Preview what would be written without modifying any files
- --force: Add even if the harness appears to be already configured
- --project-dir PATH: Project root directory (default: current working directory)

Examples:
- /sync-add-harness               Auto-detect unconfigured harness
- /sync-add-harness cursor        Add Cursor support
- /sync-add-harness --list        Show what's installed but unconfigured
- /sync-add-harness aider --dry-run  Preview aider setup without writing files

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_add_harness.py [user-provided arguments]

---
description: Watch Claude Code config files for changes and auto-sync to all harnesses when modified outside Claude Code
---

Polls CLAUDE.md, skills, agents, commands, settings, and .mcp.json using `os.stat` every 2 seconds. Triggers a full sync whenever any source file changes — filling the gap left by the PostToolUse hook, which only fires inside Claude Code sessions.

The daemon PID is stored at `.claude/harness-sync/watch.pid` so `/sync` can detect a running watcher and avoid redundant syncs.

Usage: /sync-watch [--interval SECS] [--targets T1,T2] [--dry-run] [--stop]

Options:
- --interval SECS: Polling interval in seconds (default: 2, minimum: 0.5)
- --targets T1,T2: Comma-separated list of targets to sync (default: all)
- --dry-run: Preview what would sync without writing files
- --stop: Stop a running sync-watch daemon
- --project-dir DIR: Override project directory

Examples:
- /sync-watch                        — start watching with 2s interval
- /sync-watch --interval 5           — watch with 5s interval
- /sync-watch --targets cursor,zed   — only sync Cursor and Zed
- /sync-watch --stop                 — stop a running watcher

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_watch.py [user-provided arguments]

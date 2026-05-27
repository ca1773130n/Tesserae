---
description: Push/pull HarnessSync config to GitHub Gist for multi-machine or team sharing
---

Sync your Claude Code config across machines or share with teammates via GitHub Gist.

Usage: /sync-cloud <push|pull|share> [OPTIONS]

Actions:
- push: Upload current config to a Gist (creates new or updates existing)
- pull: Download config from a Gist into current project
- share: Build a shareable bundle URL from current config

Options:
- --token TOKEN: GitHub token (or set GITHUB_TOKEN env var)
- --gist-id ID: Gist ID to update (push) or fetch (pull)
- --gist-url URL: Full Gist URL (alternative to --gist-id)
- --profile NAME: Include named profile in the bundle
- --no-overwrite: Don't overwrite existing local files on pull
- --project-dir DIR: Override project directory

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_cloud.py [user-provided arguments]

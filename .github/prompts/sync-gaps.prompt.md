---
description: Track cross-harness capability gaps with upstream issue links
---

Track and view capability gaps — features that can't be synced because a target harness lacks support. Logs gaps with optional upstream issue tracker links so user frustration becomes actionable feedback.

Usage: /sync-gaps [--target NAME] [--auto] [--include-resolved] [--json]
       /sync-gaps log <harness> <feature> '<description>' [--url URL]
       /sync-gaps resolve <harness> <feature>

Commands:
- log: Log a new gap (e.g. /sync-gaps log codex skills "Skills dropped — no equivalent")
- resolve: Mark a gap as resolved (e.g. /sync-gaps resolve codex skills)

Options:
- --target NAME: Filter by harness name
- --auto: Seed all well-known capability gaps automatically
- --include-resolved: Show resolved gaps too
- --json: Output as JSON

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_gaps.py [user-provided arguments]

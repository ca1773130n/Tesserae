---
description: Post a PR comment showing per-harness config diff — gives reviewers visibility into CLAUDE.md change impact
---

Post a formatted comment on a GitHub PR showing what each target harness
config would look like after sync. Gives code reviewers visibility into
the downstream impact of AI config changes without running HarnessSync locally.

The comment is idempotent — re-running updates the existing comment in place.

Usage: /sync-pr-comment [--pr NUMBER] [--repo OWNER/REPO] [--scope all|user|project] [--dry-run]

!PY=$(command -v python3 || command -v python) && [ -n "$PY" ] || { echo "Error: Python not found. Install Python 3 to use HarnessSync." >&2; exit 1; }; "$PY" ${CLAUDE_PLUGIN_ROOT}/src/commands/sync_pr_comment.py [user-provided arguments]

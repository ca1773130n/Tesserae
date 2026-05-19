---
description: Show a compact status of the current Tesserae project (node/edge/session counts, last compile, vault).
argument-hint: ""
allowed-tools:
  - "Bash($CLAUDE_PLUGIN_ROOT/scripts/tesserae-status.sh:*)"
---

Print a one-screen overview of this Tesserae project: graph node/edge counts, last compile timestamp, number of imported sessions, and the configured Obsidian vault path. Pure read — never mutates anything.

!`${CLAUDE_PLUGIN_ROOT}/scripts/tesserae-status.sh`

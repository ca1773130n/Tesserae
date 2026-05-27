---
description: Print instructions for running the interactive Tesserae setup wizard (must be in a terminal — not slash-runnable).
argument-hint: ""
allowed-tools:
  - "Bash($CLAUDE_PLUGIN_ROOT/scripts/tesserae-setup-help.sh:*)"
disable-model-invocation: true
---

The Tesserae setup wizard is interactive — it prompts for wiki name, sources, and companion-tool selections, then writes `.tesserae/config.json`. Claude Code slash commands run in a non-interactive shell with no stdin attached, so the wizard's first `input()` call hits EOF immediately and aborts.

Run it from a real terminal instead — the helper script prints the exact `cd` + command for your current working directory:

!`${CLAUDE_PLUGIN_ROOT}/scripts/tesserae-setup-help.sh`

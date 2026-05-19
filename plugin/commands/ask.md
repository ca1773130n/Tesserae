---
description: Ask a question against the compiled Tesserae project memory.
argument-hint: "\"your question here\""
allowed-tools:
  - "Bash($CLAUDE_PLUGIN_ROOT/scripts/tesserae-ask.sh:*)"
---

Ask the current project's Tesserae graph a question. Routes through whichever question backend the project's `.tesserae/config.json` declares (wiki / RAG-Anything / Cognee). Quote the question if it contains spaces — the wrapper script strips one matching pair of surrounding quotes before forwarding to the CLI.

Example: `/tesserae:ask "what did we decide about extractor dedup?"`

!`${CLAUDE_PLUGIN_ROOT}/scripts/tesserae-ask.sh "$ARGUMENTS"`

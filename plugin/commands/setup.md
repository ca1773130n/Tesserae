---
description: Open the interactive Tesserae setup wizard for the current project.
argument-hint: ""
allowed-tools:
  - "Bash(tesserae project setup:*)"
disable-model-invocation: true
---

Launch the colored interactive setup wizard. Detects common sources (`README.md`, `docs/`, `src/`, `data/`), asks which companion tools to enable (Understand-Anything, RAG-Anything, Cognee), and writes `.tesserae/config.json`.

This command is `disable-model-invocation: true` — only you can invoke it. The agent will never auto-launch an interactive wizard inside the conversation.

!`tesserae project setup`

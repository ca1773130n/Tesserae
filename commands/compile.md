---
description: Compile the Tesserae project — extract typed graph, write vault + site artifacts.
argument-hint: "[--changed-only]"
allowed-tools:
  - "Bash(tesserae compile:*)"
---

Run `tesserae compile` for the current project. Walks configured sources, extracts the typed knowledge graph, writes the vault projection, syncs the static site. Use `--changed-only` for an incremental recompile keyed off the manifest hash.

!`tesserae compile $ARGUMENTS`

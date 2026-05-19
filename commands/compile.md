---
description: Compile the Tesserae project — extract typed graph, write vault + site artifacts.
argument-hint: "[--changed-only] [--no-vault-pull]"
allowed-tools:
  - "Bash(tesserae project compile:*)"
---

Run `tesserae project compile` for the current project. Walks configured sources, extracts the typed knowledge graph, writes the vault projection, syncs the static site. Use `--changed-only` for an incremental recompile keyed off the manifest hash.

!`tesserae project compile $ARGUMENTS`

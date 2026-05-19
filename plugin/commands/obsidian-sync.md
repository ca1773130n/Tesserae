---
description: Sync the Tesserae vault projection with Obsidian (with optional orphan pruning).
argument-hint: "[--prune-orphans] [--watch]"
allowed-tools:
  - "Bash(tesserae project obsidian-sync:*)"
---

Push the compiled vault projection into the configured Obsidian vault and pull back any user-edited overlays. Pass `--prune-orphans` to delete vault pages whose source nodes no longer exist. Pass `--watch` to keep syncing as the vault changes.

!`tesserae project obsidian-sync $ARGUMENTS`

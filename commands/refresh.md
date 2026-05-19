---
description: Refresh the Tesserae project — import new sessions, compile, sync vault.
argument-hint: ""
allowed-tools:
  - "Bash($CLAUDE_PLUGIN_ROOT/scripts/tesserae-refresh.sh:*)"
---

Run the three-step refresh cycle for the current Tesserae project: import any new Claude Code / Codex sessions that ran inside this project, recompile the graph, then sync the vault projection back to Obsidian. Use this after you've made significant edits or just finished an agent session whose insights you want captured in the next compile's graph.

Steps run sequentially with stop-on-failure semantics — if `sessions discover --import` fails, compile and vault-sync are skipped. Vault-sync is tolerated as optional (a project that hasn't configured Obsidian still gets a clean compile).

The final line of output is a deterministic summary parsed from the compile log: `nodes=NNN edges=NNN processed=NNN sessions=NNN vault_orphans_pruned=NNN`.

!`${CLAUDE_PLUGIN_ROOT}/scripts/tesserae-refresh.sh`

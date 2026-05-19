---
description: Import normalised Claude Code / Codex sessions into the current Tesserae project.
argument-hint: ""
allowed-tools:
  - "Bash(tesserae project sessions discover:*)"
---

Run `tesserae project sessions discover --import` to scan the local Claude Code / Codex history for sessions that ran inside this project's `cwd`, normalise them, and write them into `.tesserae/harness_sessions/`. The next `tesserae project compile` will incorporate them as Session and Session<Kind> nodes in the graph.

Run this once per project; subsequent compiles read from the cached `harness_sessions/` directory rather than re-scanning the filesystem.

!`tesserae project sessions discover --import`

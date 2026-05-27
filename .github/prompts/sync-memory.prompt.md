---
description: Sync Claude Code memory files to all target harnesses for cross-harness context continuity
---

Syncs Claude Code memory files (`.claude/memory/*.md`) to equivalent persistent context
mechanisms in all target harnesses.  Project knowledge accumulated in Claude Code
becomes available when switching to Gemini, Codex, Windsurf, Cursor, or any other harness.

Usage: /sync-memory [--dry-run] [--target TARGET] [--list]

Options:
- --dry-run: Preview which files would be written without writing
- --target TARGET: Sync to a specific harness only (gemini, codex, windsurf, etc.)
- --list: Show discovered memory files without syncing

Examples:
  /sync-memory
  /sync-memory --dry-run
  /sync-memory --target gemini
  /sync-memory --list

Memory files are stored in `.claude/memory/` (project-scoped) or `~/.claude/memory/`
(user-scoped).  Each `.md` file is a named memory document — for example:
  .claude/memory/project_context.md
  .claude/memory/coding_style.md
  .claude/memory/api_decisions.md

Target harness locations:
- gemini:   ~/.gemini/context.md (appended managed section)
- codex:    ~/.codex/memory.md (appended managed section)
- windsurf: .windsurf/memories/hs-memory-<name>.md (one file per memory)
- cursor:   .cursor/rules/hs-memory.mdc (always-apply rule file)
- aider:    .aider-hs-memory.md (added to read_files list)
- cline:    .roo/memory/hs-memory-<name>.md (one file per memory)

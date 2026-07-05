---
description: Decisions across projects + time — explicit human choices (AskUserQuestion) + agent decisions.
argument-hint: "[--day YYYY-MM-DD] [--week [YYYY-MM-DD]] [--since ISO] [--until ISO] [--project NAME] [--no-llm] [--json]"
allowed-tools:
  - "Bash(tesserae decisions:*)"
---

Run `tesserae decisions` to retrieve the decisions made across your registered Tesserae projects within a time range. **Human decisions** are extracted deterministically from Claude Code's `AskUserQuestion` tool — the question asked and the option you chose. **Agent decisions** are mined from the in-window conversation by the LLM (pass `--no-llm` for the deterministic human decisions only). Each decision is dated by its own timestamp — never a session's `started_at` — so a time window is exact. Output is grouped by project; use `--json` for the structured list. For "since last Monday", pass `--since <that date>`.

Examples:
- `/tesserae:decisions --since 2026-06-30` — all projects since a date
- `/tesserae:decisions --week` — the last 7 days
- `/tesserae:decisions --project my-repo --no-llm` — human decisions only
- `/tesserae:decisions --since 2026-06-30 --json`

!`tesserae decisions $ARGUMENTS`

---
description: Daily/weekly activity digest — sessions, findings, commits, PRs, ingested docs.
argument-hint: "[--day YYYY-MM-DD] [--week [YYYY-MM-DD]] [--since ISO] [--until ISO] [--project NAME] [--no-llm]"
allowed-tools:
  - "Bash(tesserae summary:*)"
---

Run `tesserae summary` to build an activity digest for your registered Tesserae projects. Each artifact is windowed by its **own** timestamp — session turns by the turn time, findings by their source turn, commits/PRs by their git/GitHub dates, ingested docs by the document's own timestamp — never by a session's `started_at`. The deterministic markdown digest is written to `.tesserae/summaries/<project>/`, and unless you pass `--no-llm` an LLM narrative of "what happened and why it mattered" is prepended.

Examples:
- `/tesserae:summary` — today, every registered project
- `/tesserae:summary --week` — the last 7 days
- `/tesserae:summary --day 2026-07-04 --project my-repo --no-llm`

!`tesserae summary $ARGUMENTS`

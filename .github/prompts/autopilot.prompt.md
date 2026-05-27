---
description: Plan and execute multiple phases on autopilot with fresh context per step
argument-hint: "[--phase-from N] [--phase-to N] [--milestone] [--dry-run] [--skip-post-pipeline] [--max-milestones N]"
---

Run the autopilot command to plan and execute phases with dependency-aware parallel planning and execution:

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js autopilot [user-provided arguments]
```

Phases are grouped into dependency waves. Independent phases within each wave are planned AND executed in parallel using git worktrees for filesystem isolation. Each phase gets its own worktree, and after execution, a post-phase pipeline runs: simplify -> create PR -> code review -> rebase & merge.

Auto-resume is always on: completed phases are skipped, partially-done phases resume from the correct step.

IMPORTANT: This command is long-running (spawns multiple Claude subprocesses). You MUST run it in the background using `run_in_background: true` on the Bash tool to avoid hitting the Bash tool's default timeout. Use `TaskOutput` with `block: false` to check progress periodically.

Report the JSON results including wave grouping. If any phase failed, explain what happened. Auto-resume will pick up from the failed phase on the next run.

### Autopilot Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--phase-from N` | Start from phase N | -- (all phases) |
| `--phase-to N` | Stop at phase N | -- (all phases) |
| `--milestone` | Explicit milestone mode (runs wireup after all phases) | true when no phase range |
| `--dry-run` | Preview what would happen without executing | false |
| `--skip-plan` | Skip planning step | false |
| `--skip-execute` | Skip execution step | false |
| `--skip-post-pipeline` | Skip post-phase pipeline (simplify, PR, review, merge) | false |
| `--timeout N` | Per-subprocess timeout in minutes | 120 |
| `--max-turns N` | Max turns per claude -p subprocess | -- |
| `--model <model>` | Model override for claude -p | -- |

### Post-Phase Pipeline

After each phase execution completes, the following steps run on the phase's worktree branch:

1. **Simplify** — Code quality review and simplification
2. **Create PR** — Push branch and create pull request
3. **Code Review** — Review PR diff, fix BLOCKER/WARNING findings
4. **Rebase & Merge** — Rebase on main, auto-resolve conflicts, merge PR

Use `--skip-post-pipeline` to bypass this pipeline (useful for debugging or local-only runs).

### Milestone Mode

When no `--phase-from`/`--phase-to` is specified (or `--milestone` is passed), autopilot runs in milestone mode:
- Processes all phases in the current milestone
- After all phases complete and merge, runs a **wireup step** to discover unwired features

## Multi-Milestone Mode

To orchestrate work across milestone boundaries -- completing one milestone and automatically starting the next -- use the multi-milestone autopilot command:

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js multi-milestone-autopilot [user-provided arguments]
```

This extends single-milestone autopilot to process multiple milestones in sequence. The loop: completes all phases in the current milestone, archives the milestone, resolves the next milestone from LONG-TERM-ROADMAP.md, creates it, and continues.

### Multi-Milestone Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--max-milestones N` | Maximum milestones to process (safety cap) | 10 |
| `--dry-run` | Preview what would happen without executing | false |
| `--timeout N` | Per-subprocess timeout in minutes | 120 |
| `--max-turns N` | Max turns per claude -p subprocess | -- |
| `--model <model>` | Model override for claude -p | -- |
| `--skip-plan` | Skip planning step | false |
| `--skip-execute` | Skip execution step | false |
| `--skip-post-pipeline` | Skip post-phase pipeline | false |

### Multi-Milestone Examples

```bash
# Run multi-milestone autopilot (processes up to 10 milestones)
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js multi-milestone-autopilot

# Preview without executing
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js multi-milestone-autopilot --dry-run

# Limit to 3 milestones
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js multi-milestone-autopilot --max-milestones 3
```

### Pre-flight Context

To get pre-flight context for multi-milestone autopilot (LT roadmap state, current milestone, next milestone):

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init multi-milestone-autopilot
```

### Requirements

- LONG-TERM-ROADMAP.md must exist in .planning/ for cross-milestone resolution
- Claude CLI must be available for subprocess spawning
- Autonomous mode recommended for unattended operation

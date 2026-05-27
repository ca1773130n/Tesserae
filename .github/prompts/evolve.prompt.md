---
description: Run autonomous self-improvement loop with sonnet-tier models
argument-hint: "[--iterations N] [--pick-pct N] [--dry-run] [--no-worktree] [--infinite]"
---

Run the evolve command to discover improvements and execute them autonomously:

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js evolve run [user-provided arguments]
```

The evolve loop uses a paired discover→execute architecture per iteration:
1. Discovers 5-10 specific, immediately implementable improvements on the current codebase state
2. Groups items by theme and selects top priority groups using `--pick-pct` (default 50%)
3. Executes selected groups in a single subprocess call (max 10 items)
4. Runs a review/verification pass
5. Writes evolution notes and commits changes
6. Next iteration discovers again on the NOW-EVOLVED codebase (sees its own changes)
7. Repeats — each iteration builds on the previous one's improvements

This means each iteration is a tight discover→execute→discover cycle that progressively improves the codebase.

Flags:
- `--iterations N` — Number of iterations (0 = unlimited, runs until all groups done)
- `--pick-pct N` — Percentage of total groups to pick per iteration (default: 50, min 1 group)
- `--dry-run` — Discover and group only, don't execute
- `--no-worktree` — Disable git worktree isolation (by default, enabled when `branching_strategy !== 'none'`)
- `--timeout N` — Timeout per subprocess in minutes
- `--max-turns N` — Max turns per subprocess

When git isolation is enabled (config `branching_strategy` is not `'none'`), all execution runs in a dedicated worktree. A PR is created automatically after completion. Use `--no-worktree` to disable.

All operations enforce a sonnet model ceiling — no opus-class models are used.

IMPORTANT: This command is long-running (spawns multiple Claude subprocesses). You MUST run it in the background using `run_in_background: true` on the Bash tool to avoid hitting the Bash tool's default timeout. Use `TaskOutput` with `block: false` to check progress periodically.

Report the JSON results. If any groups failed, explain what happened. Suggest running again for continued improvement.

## Infinite Mode

To run a fully autonomous development loop (discover -> autoplan -> autopilot -> repeat):

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js evolve run --infinite [user-provided arguments]
```

The infinite evolve loop:
1. Discovers improvements across the codebase
2. Creates a milestone from discoveries using autoplan
3. Executes all phases in that milestone using autopilot
4. Repeats until: max cycles reached, time budget exhausted, or no discoveries remain

### Infinite Mode Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--infinite` | Enable infinite evolve mode | false |
| `--max-cycles N` | Maximum discover-plan-execute cycles | 10 |
| `--time-budget N` | Total time budget in minutes (0 = unlimited) | 0 |
| `--max-milestones N` | Max milestones per autopilot run per cycle | 1 |
| `--pick-pct N` | Discovery pick percentage per cycle | 50 |
| `--dry-run` | Preview each step without executing | false |
| `--timeout N` | Per-subprocess timeout in minutes | -- |
| `--max-turns N` | Max turns per subprocess | -- |

### Infinite Mode Examples

```bash
# Preview what infinite evolve would do
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js evolve run --infinite --dry-run

# Run 3 cycles with 60-minute time budget
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js evolve run --infinite --max-cycles 3 --time-budget 60

# Run with custom pick percentage
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js evolve run --infinite --pick-pct 30
```

IMPORTANT: Infinite mode is extremely long-running. You MUST run it in the background using `run_in_background: true`. Monitor progress via the log file at `.planning/autopilot/infinite-evolve.log`.

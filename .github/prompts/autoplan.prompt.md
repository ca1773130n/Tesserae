---
description: Automatically generate milestones from evolve discoveries or fresh codebase analysis
argument-hint: "[--dry-run] [--pick-pct N] [--name <name>] [--timeout N]"
---

Run the autoplan command to automatically generate a new milestone from evolve discovery results:

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js autoplan [user-provided arguments]
```

Autoplan bridges the gap between evolve's discovery and autopilot's execution. It takes discovered improvement groups (from evolve's discovery engine or from the current evolve state) and creates a structured milestone complete with phases, requirements, and a roadmap -- entirely autonomously.

The autoplan workflow:
1. Loads evolve state (or runs fresh discovery if no state exists)
2. Selects priority groups based on --pick-pct
3. Builds a prompt that instructs claude -p to create a milestone using /grd:new-milestone
4. Spawns the subprocess which creates the milestone artifacts
5. Returns the result with status and prompt used

### Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--dry-run` | Preview the prompt without spawning subprocess | false |
| `--pick-pct N` | Percentage of groups to use (only if running fresh discovery) | 50 |
| `--name <name>` | Override the derived milestone name | auto |
| `--timeout N` | Subprocess timeout in minutes | 120 |
| `--max-turns N` | Max turns for claude -p subprocess | -- |
| `--model <model>` | Model override for subprocess | -- |

### Pre-flight Context

To get pre-flight context for autoplan (evolve state, current milestone):

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init autoplan
```

### Examples

```bash
# Preview what autoplan would create
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js autoplan --dry-run

# Run autoplan with custom milestone name
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js autoplan --name "Performance Improvements"

# Run autoplan with fresh discovery at 30% pick rate
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js autoplan --pick-pct 30
```

IMPORTANT: This command spawns a Claude subprocess. You MUST run it in the background using `run_in_background: true` on the Bash tool to avoid hitting the Bash tool's default timeout.

Report the JSON results. If autoplan created a milestone, suggest running `/grd:autopilot` to execute it.

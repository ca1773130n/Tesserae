---
description: Check project progress and route to the next action
argument-hint: "[dashboard | health | phase <N>]"
---

<purpose>
Unified project status command. Default mode checks progress, summarizes recent work, and
routes to the next action. Sub-modes provide dashboard overview, health diagnostics, and
phase drill-down — replacing the former /grd:dashboard, /grd:health, and /grd:phase-detail
commands.

Modes:
  (no args)         Smart progress + routing (default)
  dashboard | full  TUI tree view of milestones, phases, plans
  health            Blockers, velocity, stale phases, risk register
  phase <N>         Detailed drill-down for a single phase
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<shell_safety>
NEVER use inline `node -e` or `python3 -c` with `!=` or `!==` — zsh escapes `!` and breaks them.
Use `grd-tools.js` pre-formatted output directly. Do NOT pipe `--raw` JSON through inline one-liners.
If JSON field extraction is needed, write a temp .js file or use inverted equality (`=== "x"` + negate).
</shell_safety>

<process>

<step name="parse_mode">
**Parse arguments to determine mode:**

Check `[user-provided arguments]`:
- Empty or not provided: **default mode** (progress + routing)
- `dashboard` or `full`: **dashboard mode**
- `health`: **health mode**
- `phase <N>` (where N is a number): **phase-detail mode** with phase number N

Store the mode for routing in the next step.
</step>

<!-- ================================================================== -->
<!-- MODE: DASHBOARD                                                     -->
<!-- ================================================================== -->

<step name="mode_dashboard" condition="mode is dashboard or full">
**Full graphical TUI overview of milestones, phases, and plans.**

Load the dashboard data:

```bash
DASHBOARD=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js dashboard --raw)
```

This returns JSON with:
- `milestones[]` — each with phases, progress_percent, start/target dates
- `summary` — total_milestones, total_phases, total_plans, total_summaries, active_blockers, pending_deferred, total_decisions

Render the TUI tree view (or use pre-formatted output):

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js dashboard
```

This renders:
- Milestone headings with date ranges
- Per-milestone progress bars
- Phase list with status symbols (checkmark=complete, diamond=in-progress, circle=planned, arrow=active)
- Plan counts per phase
- Summary footer with totals

Present the output to the user, then offer navigation:

- `/grd:progress health` — project health indicators
- `/grd:progress phase <N>` — drill down into a specific phase
- `/grd:execute-phase <N>` — execute the next incomplete phase
- `/grd:progress` — smart routing view

**Done.** Do not continue to default mode steps.
</step>

<!-- ================================================================== -->
<!-- MODE: HEALTH                                                        -->
<!-- ================================================================== -->

<step name="mode_health" condition="mode is health">
**Project health indicators — blockers, velocity, stale phases, risk register.**

Load the health data:

```bash
HEALTH=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js health --raw)
```

This returns JSON with:
- `blockers` — count and items list
- `deferred_validations` — total, pending, resolved counts with item details
- `velocity` — total_plans, total_duration_min, avg_duration_min, recent_5_avg_min
- `stale_phases` — phases with plans but no summaries
- `risks` — risk register entries with probability, impact, and phase
- `baseline` — baseline assessment status (null if no BASELINE.md)

Render the health indicators (or use pre-formatted output):

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js health
```

This renders:
- Blockers section (None with checkmark, or list of active blockers)
- Deferred validations with pending/resolved counts and item details
- Velocity metrics (average plan duration, recent trend)
- Stale phases list (warning indicator)
- Risk register table

Present the output to the user.

**Highlight warnings** that need attention:
- **Active blockers** — suggest resolving or escalating
- **Pending deferred validations** — note which phase will validate them
- **Stale phases** — phases with plans but no progress, suggest `/grd:execute-phase <N>`
- **High-impact risks** — risks with Critical impact that are not yet mitigated
- **Velocity trend** — if recent_5_avg is significantly higher than overall avg, note slowdown

Offer navigation:

- `/grd:progress dashboard` — full project overview
- `/grd:progress phase <N>` — drill into a specific phase
- `/grd:execute-phase <N>` — address stale phases
- `/grd:progress` — smart routing view

**Done.** Do not continue to default mode steps.
</step>

<!-- ================================================================== -->
<!-- MODE: PHASE DETAIL                                                  -->
<!-- ================================================================== -->

<step name="mode_phase_detail" condition="mode is phase N">
**Detailed drill-down view for a single phase.**

If no phase number was provided, show usage:
```
Usage: /grd:progress phase <N>
Example: /grd:progress phase 4
```
Exit.

Load the phase detail data:

```bash
PHASE_DETAIL=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js phase-detail ${PHASE_NUM} --raw)
```

This returns JSON with:
- `phase_number`, `phase_name`, `directory`
- `plans[]` — each with id, wave, type, status, duration, tasks, files, objective
- `decisions[]` — key decisions made in this phase
- `artifacts[]` — files created
- `has_eval`, `has_verification`, `has_review`, `has_context`, `has_research` — supplementary file flags
- `summary_stats` — total_plans, completed, total_duration, total_tasks, total_files

Check for error: if `error` field exists in response, the phase was not found.

Render the detailed phase view (or use pre-formatted output):

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js phase-detail ${PHASE_NUM}
```

This renders:
- Phase header with status and plan count
- Plans table with wave, status symbol, duration, tasks, files, objective
- Totals row
- Decisions list
- Artifact status indicators (Context, Research, Eval, Verification, Review)

Present the output to the user, then offer navigation:

- `/grd:progress dashboard` — return to full project view
- `/grd:execute-phase <N>` — execute this phase (if plans exist but incomplete)
- `/grd:plan-phase <N>` — create plans for this phase (if no plans exist)
- `/grd:progress phase <N+1>` — view next phase
- `/grd:progress health` — project health check

**Done.** Do not continue to default mode steps.
</step>

<!-- ================================================================== -->
<!-- MODE: DEFAULT (progress + smart routing)                            -->
<!-- ================================================================== -->

<step name="init_context">
**Load progress context (with file contents to avoid redundant reads):**

```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init progress --include state,roadmap,project,config)
```

Extract from init JSON: `project_exists`, `roadmap_exists`, `state_exists`, `phases`, `current_phase`, `next_phase`, `milestone_version`, `completed_count`, `phase_count`, `paused_at`, `phases_dir`, `phase_dir`.

**File contents (from --include):** `state_content`, `roadmap_content`, `project_content`, `config_content`. These are null if files don't exist.

If `project_exists` is false (no `.planning/` directory):

```
No planning structure found.

Run /grd:init to start a new project.
```

Exit.

If missing STATE.md: suggest `/grd:init`.

**If ROADMAP.md missing but PROJECT.md exists:**

This means a milestone was completed and archived. Go to **Route F** (between milestones).

If missing both ROADMAP.md and PROJECT.md: suggest `/grd:init`.
</step>

<step name="load">
**Use project context from INIT:**

All file contents are already loaded via `--include` in init_context step:
- `state_content` — living memory (position, decisions, issues)
- `roadmap_content` — phase structure and objectives
- `project_content` — current state (What This Is, Core Value, Requirements)
- `config_content` — settings (model_profile, workflow toggles)

No additional file reads needed.
</step>

<step name="analyze_roadmap">
**Get comprehensive roadmap analysis (replaces manual parsing):**

```bash
ROADMAP=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js roadmap analyze)
```

This returns structured JSON with:
- All phases with disk status (complete/partial/planned/empty/no_directory)
- Goal and dependencies per phase
- Plan and summary counts per phase
- Aggregated stats: total plans, summaries, progress percent
- Current and next phase identification

Use this instead of manually reading/parsing ROADMAP.md.
</step>

<step name="recent">
**Gather recent work context:**

- Find the 2-3 most recent SUMMARY.md files
- Use `summary-extract` for efficient parsing:
  ```bash
  node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js summary-extract <path> --fields one_liner
  ```
- This shows "what we've been working on"
</step>

<step name="position">
**Parse current position from init context and roadmap analysis:**

- Use `current_phase` and `next_phase` from roadmap analyze
- Use phase-level `has_context` and `has_research` flags from analyze
- Note `paused_at` if work was paused (from init context)
- Count pending todos: use `init todos` or `list-todos`
- Check for active debug sessions: `ls .planning/debug/*.md 2>/dev/null | grep -v resolved | wc -l`
</step>

<step name="report">
**Generate progress bar from grd-tools, then present rich status report:**

```bash
PROGRESS_BAR=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js progress bar --raw)
```

Present:

```
# [Project Name]

**Progress:** {PROGRESS_BAR}
**Profile:** [quality/balanced/budget]

## Recent Work
- [Phase X, Plan Y]: [what was accomplished - 1 line from summary-extract]
- [Phase X, Plan Z]: [what was accomplished - 1 line from summary-extract]

## Current Position
Phase [N] of [total]: [phase-name]
Plan [M] of [phase-total]: [status]
CONTEXT: [Y if has_context | - if not]

## Key Decisions Made
- [decision 1 from STATE.md]
- [decision 2]

## Blockers/Concerns
- [any blockers or concerns from STATE.md]

## Pending Todos
- [count] pending — /grd:check-todos to review

## Active Debug Sessions
- [count] active — /grd:debug to continue
(Only show this section if count > 0)

## What's Next
[Next phase/plan objective from roadmap analyze]
```

</step>

<step name="route">
**Determine next action based on verified counts.**

**Step 1: Count plans, summaries, and issues in current phase**

```bash
ls -1 ${phase_dir}/*-PLAN.md 2>/dev/null | wc -l
ls -1 ${phase_dir}/*-SUMMARY.md 2>/dev/null | wc -l
ls -1 ${phase_dir}/*-UAT.md 2>/dev/null | wc -l
```

**Step 2: Route based on counts**

| Condition | Meaning | Action |
|-----------|---------|--------|
| uat_with_gaps > 0 | UAT gaps need fix plans | Go to **Route E** |
| summaries < plans | Unexecuted plans exist | Go to **Route A** |
| summaries = plans AND plans > 0 | Phase complete | Go to Step 3 |
| plans = 0 | Phase not yet planned | Go to **Route B** |

**Route A: Unexecuted plan exists**

```
---

## Next Up

**{phase}-{plan}: [Plan Name]** — [objective summary from PLAN.md]

`/grd:execute-phase {phase}`

<sub>`/clear` first -> fresh context window</sub>

---
```

**Route B: Phase needs planning**

Check if CONTEXT.md exists.

**If CONTEXT.md exists:**
```
---

## Next Up

**Phase {N}: {Name}** — {Goal from ROADMAP.md}
<sub>Context gathered, ready to plan</sub>

`/grd:plan-phase {phase-number}`

<sub>`/clear` first -> fresh context window</sub>

---
```

**If CONTEXT.md does NOT exist:**
```
---

## Next Up

**Phase {N}: {Name}** — {Goal from ROADMAP.md}

`/grd:discuss-phase {phase}` — gather context and clarify approach

<sub>`/clear` first -> fresh context window</sub>

---

**Also available:**
- `/grd:plan-phase {phase}` — skip discussion, plan directly
- `/grd:list-phase-assumptions {phase}` — see Claude's assumptions

---
```

**Route E: UAT gaps need fix plans**
```
---

## UAT Gaps Found

**{phase}-UAT.md** has {N} gaps requiring fixes.

`/grd:plan-phase {phase} --gaps`

<sub>`/clear` first -> fresh context window</sub>

---
```

**Step 3: Check milestone status (only when phase complete)**

**Route C: Phase complete, more phases remain**
```
---

## Phase {Z} Complete

## Next Up

**Phase {Z+1}: {Name}** — {Goal from ROADMAP.md}

`/grd:discuss-phase {Z+1}` — gather context and clarify approach

<sub>`/clear` first -> fresh context window</sub>

---
```

**Route D: Milestone complete**
```
---

## Milestone Complete

All {N} phases finished!

## Next Up

**Complete Milestone** — archive and prepare for next

`/grd:complete-milestone`

<sub>`/clear` first -> fresh context window</sub>

---
```

**Route F: Between milestones (ROADMAP.md missing, PROJECT.md exists)**
```
---

## Milestone v{X.Y} Complete

Ready to plan the next milestone.

## Next Up

**Start Next Milestone** — questioning -> research -> requirements -> roadmap

`/grd:new-milestone`

<sub>`/clear` first -> fresh context window</sub>

---
```

</step>

</process>

<success_criteria>

- [ ] Correct mode selected from arguments (default / dashboard / health / phase N)
- [ ] Dashboard mode: TUI tree view with milestones, phases, plans rendered
- [ ] Health mode: blockers, velocity, stale phases, risks displayed with warnings
- [ ] Phase detail mode: plans table, decisions, artifact status for requested phase
- [ ] Default mode: rich context provided (recent work, decisions, issues)
- [ ] Default mode: current position clear with visual progress
- [ ] Default mode: smart routing to next action
- [ ] Navigation options reference /grd:progress sub-modes (not removed commands)
</success_criteria>

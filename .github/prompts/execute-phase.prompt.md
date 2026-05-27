---
description: Execute all plans in a phase using wave-based parallel execution
argument-hint: <phase number>
---

<!-- Variable reference guide:
  ${CLAUDE_PLUGIN_ROOT} — Absolute path to the GRD plugin root. Used for:
    - bin/grd-tools.js calls (grd-tools lives in bin/, not the skill directory)
    - @references/*.md and @templates/*.md (cross-directory references)
    - agents/*.md references from commands/ (cross-directory)
  ${CLAUDE_SKILL_DIR} — Resolves to the directory containing THIS skill file (commands/).
    Available since Claude Code v2.1.69. Use for same-directory references
    (e.g., referencing another command from a command file).
    Currently unused because all GRD cross-references are cross-directory.
-->

<purpose>
Execute all plans in a phase using wave-based parallel execution. Orchestrator stays lean — delegates plan execution to subagents. After execution, auto-triggers eval report if EVAL.md exists and tracks experiment parameters in commit messages.
</purpose>

<core_principle>
Orchestrator coordinates, not executes. Each subagent loads the full execute-plan context. Orchestrator: discover plans -> analyze deps -> group waves -> spawn agents -> handle checkpoints -> collect results -> trigger eval.
</core_principle>

<required_reading>
Read STATE.md before any operation to load project context.
</required_reading>

<process>

<step name="initialize" priority="first">
Load all context in one call:

```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init execute-phase "${PHASE_ARG}" --include context)
```

Parse JSON for: `executor_model`, `verifier_model`, `reviewer_model`, `commit_docs`, `parallelization`, `branching_strategy`, `branch_name`, `base_branch`, `worktree_dir`, `branch_template`, `phase_found`, `phase_dir`, `phase_number`, `phase_name`, `phase_slug`, `plans`, `incomplete_plans`, `plan_count`, `incomplete_count`, `state_exists`, `roadmap_exists`, `autonomous_mode`, `use_teams`, `code_review_enabled`, `code_review_timing`, `code_review_severity_gate`, `team_timeout_minutes`, `max_concurrent_teammates`, `phases_dir`, `research_dir`, `codebase_dir`, `webmcp_available`, `webmcp_skip_reason`, `isolation_mode`, `main_repo_path`, `native_worktree_available`, `context_content`.

**If `phase_found` is false:** Error — phase directory not found.
**If `plan_count` is 0:** Error — no plans found in phase.
**If `state_exists` is false but `.planning/` exists:** Offer reconstruct or continue.

When `parallelization` is false, plans within a wave execute sequentially.
</step>

<step name="setup_isolation" condition="branching_strategy != none">
Set up isolation for this phase execution based on `isolation_mode` from init JSON.

**Mode A: native (isolation_mode='native')**

Native worktree isolation is available (Claude Code manages worktrees automatically). Do NOT create a worktree manually.

- Record: `ISOLATION_MODE=native`
- Record: `MAIN_REPO_PATH` from init JSON (for STATE.md writes)
- Note: Each executor agent will be spawned with `isolation: "worktree"` parameter on the Task call
- The branch name will be captured from each executor's Task result
- No `WORKTREE_PATH` is pre-computed — Claude Code handles worktree creation transparently

**Mode B: manual (isolation_mode='manual')**

GRD manages the worktree explicitly (v0.2.5 behavior). Preserve existing worktree creation:

1. **Check for uncommitted changes on current branch:**
   ```bash
   DIRTY=$(git status --porcelain)
   ```
   If non-empty: warn "Uncommitted changes detected. Stash or commit before worktree creation." Stop.

2. **Create worktree:**
   ```bash
   WT_RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js worktree create --phase "${PHASE_NUMBER}" --slug "${PHASE_SLUG}")
   ```
   Parse JSON result for `path` and `branch`. If `error` field present: report error and stop.
   Store: `WORKTREE_PATH` = result.path, `WORKTREE_BRANCH` = result.branch

3. **Verify worktree is ready:**
   ```bash
   ls "${WORKTREE_PATH}/.planning" && echo "Worktree ready"
   ```

- Record: `ISOLATION_MODE=manual`, `WORKTREE_PATH`, `WORKTREE_BRANCH`
- Record: `MAIN_REPO_PATH` as the current working directory
- All executor agents will receive `WORKTREE_PATH` and operate within it.
- The main checkout remains clean on its original branch.

**Mode C: overstory (isolation_mode='overstory')**

Overstory manages agent lifecycle, worktrees, and merging. GRD dispatches plans via `ov sling`.

- Record: `ISOLATION_MODE=overstory`
- Record: `MAIN_REPO_PATH` from init JSON
- Load: `OVERSTORY_CONFIG` from init JSON `overstory_config`
- **Stale overlay cleanup:** Remove any leftover files matching `.planning/.tmp/overlay-*.md` from prior crashed runs
- Verify: `overstory_available` is true from init JSON. If false:
  - Check if `OVERSTORY_CONFIG.install_prompt` is true
  - If yes: prompt user "Overstory CLI not found. Install? (bun install -g overstory)"
  - On confirm: run `node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js overstory install`, then re-detect
  - On decline or if install_prompt is false: fall back to Mode A (native) or Mode B (manual) with warning
- No worktree pre-creation — Overstory handles this via `ov sling`

**When `branching_strategy` is `"none"`:** Skip this step entirely. Continue on the current directory with no worktree isolation (backwards compatible). No `ISOLATION_MODE` is set.
</step>

<step name="validate_phase">
From init JSON: `phase_dir`, `plan_count`, `incomplete_count`.

Report: "Found {plan_count} plans in {phase_dir} ({incomplete_count} incomplete)"
</step>

<step name="discover_and_group_plans">
Load plan inventory with wave grouping in one call:

```bash
PLAN_INDEX=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js phase-plan-index "${PHASE_NUMBER}")
```

Parse JSON for: `phase`, `plans[]` (each with `id`, `wave`, `autonomous`, `objective`, `files_modified`, `task_count`, `has_summary`), `waves` (map of wave number -> plan IDs), `incomplete`, `has_checkpoints`.

**Filtering:** Skip plans where `has_summary: true`. If `--gaps-only`: also skip non-gap_closure plans. If all filtered: "No matching incomplete plans" -> exit.

Report:
```
## Execution Plan

**Phase {X}: {Name}** — {total_plans} plans across {wave_count} waves

| Wave | Plans | What it builds |
|------|-------|----------------|
| 1 | 01-01, 01-02 | {from plan objectives, 3-8 words} |
| 2 | 01-03 | ... |
```
</step>

<step name="create_phase_team" condition="use_teams=true">
**Only when `use_teams: true` from init.**

Create an Agent Team for this phase:

```
TeamCreate(
  team_name="grd-phase-${PHASE_NUMBER}-${PHASE_SLUG}",
  description="Executing phase ${PHASE_NUMBER}: ${PHASE_NAME}"
)
```

The team lead (this orchestrator) coordinates all executor teammates and handles checkpoint mediation.
</step>

<step name="execute_waves_teams" condition="use_teams=true">
**Alternative to `execute_waves` — only when `use_teams: true`.**

Execute each wave in sequence using Agent Teams coordination.

**For each wave:**

1. **Describe what's being built (same as standard flow).**

2. **Spawn executor teammates** (up to `max_concurrent_teammates`):

   **When ISOLATION_MODE=native:** Add `isolation: "worktree"` parameter to the Task call. Use `<native_isolation>` block instead of `<worktree>`:

   ```
   Task(
     subagent_type="grd:grd-executor",
     model="{executor_model}",
     team_name="grd-phase-${PHASE_NUMBER}-${PHASE_SLUG}",
     name="executor-${PLAN_ID}",
     isolation: "worktree",
     prompt="
       <objective>
       Execute plan ${plan_number} of phase ${phase_number}-${phase_name}.
       Commit each task atomically. Create SUMMARY.md. Update STATE.md.
       Track experiment parameters in commit messages where applicable.
       </objective>

       <team_coordination>
       You are part of team: grd-phase-${PHASE_NUMBER}-${PHASE_SLUG}
       Team lead: orchestrator
       If you hit a checkpoint, use SendMessage to report to team lead.
       Wait for team lead's response before continuing.
       </team_coordination>

       <native_isolation>
       Isolation mode: native
       You are running in a Claude Code-managed worktree. Your working directory
       IS the worktree — operate naturally. No path prefixing needed.
       Main repo path: ${MAIN_REPO_PATH}
       STATE.md updates must use this main repo path (not your working directory).
       </native_isolation>

       <phase_context>
       ${context_content or "No CONTEXT.md found for this phase."}
       </phase_context>

       <execution_context>
       @${CLAUDE_PLUGIN_ROOT}/references/execute-plan.md
       @${CLAUDE_PLUGIN_ROOT}/templates/summary.md
       @${CLAUDE_PLUGIN_ROOT}/references/checkpoints.md
       @${CLAUDE_PLUGIN_ROOT}/references/tdd.md
       </execution_context>

       <paths>
       research_dir: ${research_dir}
       phases_dir: ${phases_dir}
       phase_dir: ${phase_dir}
       codebase_dir: ${codebase_dir}
       </paths>

       <files_to_read>
       Read these files at execution start using the Read tool:
       - Plan: ${phase_dir}/${plan_file}
       - State: .planning/STATE.md
       - Config: .planning/config.json (if exists)
       </files_to_read>

       <experiment_tracking>
       When committing tasks that involve experiment parameters (hyperparameters, model configs, dataset splits, etc.):
       - Include key parameters in commit message body
       - Format: param: value on separate lines
       </experiment_tracking>

       <success_criteria>
       - [ ] All tasks executed
       - [ ] Each task committed individually
       - [ ] Experiment parameters tracked in commits
       - [ ] SUMMARY.md created in plan directory
       - [ ] STATE.md updated with position and decisions
       </success_criteria>
     "
   )
   ```

   **When ISOLATION_MODE=manual:** Keep existing `<worktree>` block (v0.2.5 behavior):

   ```
   Task(
     subagent_type="grd:grd-executor",
     model="{executor_model}",
     team_name="grd-phase-${PHASE_NUMBER}-${PHASE_SLUG}",
     name="executor-${PLAN_ID}",
     prompt="
       <objective>
       Execute plan ${plan_number} of phase ${phase_number}-${phase_name}.
       Commit each task atomically. Create SUMMARY.md. Update STATE.md.
       Track experiment parameters in commit messages where applicable.
       </objective>

       <team_coordination>
       You are part of team: grd-phase-${PHASE_NUMBER}-${PHASE_SLUG}
       Team lead: orchestrator
       If you hit a checkpoint, use SendMessage to report to team lead.
       Wait for team lead's response before continuing.
       </team_coordination>

       <worktree>
       Working directory: ${WORKTREE_PATH}
       All file operations (Read, Write, Edit) and Bash commands MUST use this
       directory as the working root. Use absolute paths prefixed with this directory
       for all file operations. For Bash commands, use: cd "${WORKTREE_PATH}" && ...
       </worktree>

       <phase_context>
       ${context_content or "No CONTEXT.md found for this phase."}
       </phase_context>

       <execution_context>
       @${CLAUDE_PLUGIN_ROOT}/references/execute-plan.md
       @${CLAUDE_PLUGIN_ROOT}/templates/summary.md
       @${CLAUDE_PLUGIN_ROOT}/references/checkpoints.md
       @${CLAUDE_PLUGIN_ROOT}/references/tdd.md
       </execution_context>

       <paths>
       research_dir: ${research_dir}
       phases_dir: ${phases_dir}
       phase_dir: ${phase_dir}
       codebase_dir: ${codebase_dir}
       </paths>

       <files_to_read>
       Read these files at execution start using the Read tool:
       - Plan: ${phase_dir}/${plan_file}
       - State: .planning/STATE.md
       - Config: .planning/config.json (if exists)
       </files_to_read>

       <experiment_tracking>
       When committing tasks that involve experiment parameters (hyperparameters, model configs, dataset splits, etc.):
       - Include key parameters in commit message body
       - Format: param: value on separate lines
       </experiment_tracking>

       <success_criteria>
       - [ ] All tasks executed
       - [ ] Each task committed individually
       - [ ] Experiment parameters tracked in commits
       - [ ] SUMMARY.md created in plan directory
       - [ ] STATE.md updated with position and decisions
       </success_criteria>
     "
   )
   ```

   **When branching_strategy=none:** No isolation block at all in the prompt. Omit both `<worktree>` and `<native_isolation>` blocks, and do not set `isolation` parameter on the Task call. Include `<phase_context>` block in all modes.

3. **Create and assign tasks:**

   For each plan in this wave:
   ```
   TaskCreate(subject="Execute plan ${PLAN_ID}", description="...")
   TaskUpdate(taskId=..., owner="executor-${PLAN_ID}")
   ```

4. **Monitor via TaskList** until all wave tasks show `completed`.

5. **Handle teammate messages:**
   - **Checkpoint messages:** Present checkpoint to user, get response, SendMessage response back to teammate
   - **Completion messages:** Verify via spot-checks (same as standard flow)
   - **Failure messages:** Report to user, ask "Retry?" or "Continue?"

6. **Spot-check results** (same as standard `execute_waves` step 4).

6b. **WebMCP sanity checks (if `webmcp_available=true` from init):**

    Apply the same WebMCP sanity check logic described in the standard `execute_waves` step 4b. Skip when `webmcp_available` is false (log: "WebMCP not available — skipping health checks (reason: {webmcp_skip_reason})"). When enabled, run three health checks (`hive_get_health_status`, `hive_check_console_errors`, `hive_get_page_info`) for each completed plan in this wave. Retry failed checks once; halt on second consecutive failure.

7. **Code review (if `code_review_timing="per_wave"` and `code_review_enabled=true`):**

   ```
   Task(
     subagent_type="grd:grd-code-reviewer",
     model="{reviewer_model}",
     team_name="grd-phase-${PHASE_NUMBER}-${PHASE_SLUG}",
     name="reviewer-wave-${WAVE}",
     prompt="
       Review wave ${WAVE} of phase ${PHASE_NUMBER}.
       Plans reviewed: ${PLAN_IDS_IN_WAVE}
       Phase directory: ${PHASE_DIR}

       PATHS:
       research_dir: ${research_dir}
       phases_dir: ${phases_dir}
       phase_dir: ${phase_dir}
       codebase_dir: ${codebase_dir}

       <phase_context>
       ${context_content or "No CONTEXT.md found for this phase."}
       </phase_context>

       Read each plan's PLAN.md and SUMMARY.md.
       Produce ${PHASE_NUMBER}-${WAVE}-REVIEW.md in ${PHASE_DIR}.
     "
   )
   ```

   **Handle review verdict:**
   - `pass`: Proceed to next wave
   - `blocker_found` and `severity_gate="blocker"` or `severity_gate="warning"`:
     Present blockers to user. Options: "Fix and re-run wave" / "Acknowledge and continue" / "Stop execution"
   - `warnings_only` and `severity_gate="warning"`:
     Present warnings to user. Same options.
   - `severity_gate="none"`: Log review, always continue.

8. **Proceed to next wave.**
</step>

<step name="execute_waves" condition="use_teams=false">
**Standard execution — when `use_teams: false` (default).**

Execute each wave in sequence. Within a wave: parallel if `PARALLELIZATION=true`, sequential if `false`.

**For each wave:**

1. **Describe what's being built (BEFORE spawning):**

   Read each plan's `<objective>`. Extract what's being built and why.

   ```
   ---
   ## Wave {N}

   **{Plan ID}: {Plan Name}**
   {2-3 sentences: what this builds, technical approach, why it matters}

   Spawning {count} agent(s)...
   ---
   ```

2. **Spawn executor agents:**

   Pass paths only — executors read files themselves with their fresh 200k context.
   This keeps orchestrator context lean (~10-15%).

   **When ISOLATION_MODE=native:** Add `isolation: "worktree"` parameter to the Task call. Use the `<native_isolation>` block instead of `<worktree>`:

   ```
   Task(
     subagent_type="grd:grd-executor",
     model="{executor_model}",
     isolation: "worktree",
     prompt="
       <objective>
       Execute plan {plan_number} of phase {phase_number}-{phase_name}.
       Commit each task atomically. Create SUMMARY.md. Update STATE.md.
       Track experiment parameters in commit messages where applicable.
       </objective>

       <native_isolation>
       Isolation mode: native
       You are running in a Claude Code-managed worktree. Your working directory
       IS the worktree — operate naturally. No path prefixing needed.
       Main repo path: ${MAIN_REPO_PATH}
       STATE.md updates must use this main repo path (not your working directory).
       </native_isolation>

       <phase_context>
       ${context_content or "No CONTEXT.md found for this phase."}
       </phase_context>

       <execution_context>
       @${CLAUDE_PLUGIN_ROOT}/references/execute-plan.md
       @${CLAUDE_PLUGIN_ROOT}/templates/summary.md
       @${CLAUDE_PLUGIN_ROOT}/references/checkpoints.md
       @${CLAUDE_PLUGIN_ROOT}/references/tdd.md
       </execution_context>

       <paths>
       research_dir: ${research_dir}
       phases_dir: ${phases_dir}
       phase_dir: ${phase_dir}
       codebase_dir: ${codebase_dir}
       </paths>

       <files_to_read>
       Read these files at execution start using the Read tool:
       - Plan: {phase_dir}/{plan_file}
       - State: .planning/STATE.md
       - Config: .planning/config.json (if exists)
       </files_to_read>

       <experiment_tracking>
       When committing tasks that involve experiment parameters (hyperparameters, model configs, dataset splits, etc.):
       - Include key parameters in commit message body
       - Format: `param: value` on separate lines
       - Example: `feat(03-02): train baseline model\n\nlr: 0.001\nbatch_size: 32\nepochs: 50`
       </experiment_tracking>

       <success_criteria>
       - [ ] All tasks executed
       - [ ] Each task committed individually
       - [ ] Experiment parameters tracked in commits
       - [ ] SUMMARY.md created in plan directory
       - [ ] STATE.md updated with position and decisions
       </success_criteria>
     "
   )
   ```

   **When ISOLATION_MODE=manual:** Keep existing `<worktree>` block (v0.2.5 behavior):

   ```
   Task(
     subagent_type="grd:grd-executor",
     model="{executor_model}",
     prompt="
       <objective>
       Execute plan {plan_number} of phase {phase_number}-{phase_name}.
       Commit each task atomically. Create SUMMARY.md. Update STATE.md.
       Track experiment parameters in commit messages where applicable.
       </objective>

       <worktree>
       Working directory: ${WORKTREE_PATH}
       All file operations (Read, Write, Edit) and Bash commands MUST use this
       directory as the working root. Use absolute paths prefixed with this directory
       for all file operations. For Bash commands, use: cd "${WORKTREE_PATH}" && ...
       </worktree>

       <phase_context>
       ${context_content or "No CONTEXT.md found for this phase."}
       </phase_context>

       <execution_context>
       @${CLAUDE_PLUGIN_ROOT}/references/execute-plan.md
       @${CLAUDE_PLUGIN_ROOT}/templates/summary.md
       @${CLAUDE_PLUGIN_ROOT}/references/checkpoints.md
       @${CLAUDE_PLUGIN_ROOT}/references/tdd.md
       </execution_context>

       <paths>
       research_dir: ${research_dir}
       phases_dir: ${phases_dir}
       phase_dir: ${phase_dir}
       codebase_dir: ${codebase_dir}
       </paths>

       <files_to_read>
       Read these files at execution start using the Read tool:
       - Plan: {phase_dir}/{plan_file}
       - State: .planning/STATE.md
       - Config: .planning/config.json (if exists)
       </files_to_read>

       <experiment_tracking>
       When committing tasks that involve experiment parameters (hyperparameters, model configs, dataset splits, etc.):
       - Include key parameters in commit message body
       - Format: `param: value` on separate lines
       - Example: `feat(03-02): train baseline model\n\nlr: 0.001\nbatch_size: 32\nepochs: 50`
       </experiment_tracking>

       <success_criteria>
       - [ ] All tasks executed
       - [ ] Each task committed individually
       - [ ] Experiment parameters tracked in commits
       - [ ] SUMMARY.md created in plan directory
       - [ ] STATE.md updated with position and decisions
       </success_criteria>
     "
   )
   ```

   **When branching_strategy=none:** No isolation block at all in the prompt. Omit both `<worktree>` and `<native_isolation>` blocks, and do not set `isolation` parameter on the Task call. Include `<phase_context>` block in all modes. The executor operates in the normal working directory.

**If ISOLATION_MODE is 'overstory':**

For each wave:

1. **Generate overlays:**
   For each plan in wave:
   - Read PLAN.md content from phase directory
   - Generate overlay via lib/overstory.ts `generateOverlay(planContent, context)`
   - Write overlay to `.planning/.tmp/overlay-${PHASE_NUMBER}-${PLAN_ID}.md`

2. **Dispatch workers:**
   For each plan in wave:
   - Call `ov sling "GRD plan ${PLAN_ID}: execute plan" --runtime ${OVERSTORY_CONFIG.runtime} --model ${EXECUTOR_MODEL} --overlay <overlay_path>`
   - Parse JSON result: record `agent_id`, `worktree_path`, `branch` per plan

3. **Poll for completion:**
   Every `OVERSTORY_CONFIG.poll_interval_ms` (default 5000ms):
   - Run `ov status --json`, filter to dispatched agent_ids
   - For each agent with `state: 'running'`:
     a. Check mail: `ov mail --agent ${agent_id} --json`
     b. If checkpoint messages found (type='checkpoint'):
        - Surface checkpoint body to user
        - Get user response
        - Send via `ov nudge ${agent_id} -- "${response}"`
   - For each agent with `state: 'done'`:
     a. **Copy SUMMARY.md from worktree BEFORE merge:**
        `cp ${agent.worktree_path}/${PHASE_DIR}/${PLAN_ID}-SUMMARY.md ${MAIN_REPO_PATH}/${PHASE_DIR}/`
     b. Run `ov merge ${agent_id} --json` — parse MergeResult
     c. If `merged: false`: log conflict to SUMMARY.md, warn user
   - For each agent with `state: 'failed'`:
     a. Log failure, copy any partial SUMMARY.md
     b. Mark plan as failed in status tracker
   - Check timeout: if any agent exceeds `team_timeout_minutes`, run `ov stop ${agent_id}`

4. **Wave complete:**
   - Clean up overlay files: remove `.planning/.tmp/overlay-${PHASE_NUMBER}-*.md`
   - Code review (if timing=per_wave): run as normal
   - Proceed to next wave

5. **Phase complete:**
   - Clean up `.planning/.tmp/` if empty
   - Record metrics via `state record-metric`
   - Trigger eval report if EVAL.md exists

3. **Wait for all agents in wave to complete.**

4. **Report completion — spot-check claims first:**

   For each SUMMARY.md:
   - Verify first 2 files from `key-files.created` exist on disk
   - Check `git log --oneline --all --grep="{phase}-{plan}"` returns >=1 commit
   - Check for `## Self-Check: FAILED` marker

   If ANY spot-check fails: report which plan failed, route to failure handler — ask "Retry plan?" or "Continue with remaining waves?"

   If pass:
   ```
   ---
   ## Wave {N} Complete

   **{Plan ID}: {Plan Name}**
   {What was built — from SUMMARY.md}
   {Notable deviations, if any}

   {If more waves: what this enables for next wave}
   ---
   ```

4b. **WebMCP sanity checks (if `webmcp_available=true` from init):**

    **Skip condition:** If `webmcp_available` is `false` in the INIT JSON, skip this step entirely. Log: "WebMCP not available — skipping health checks (reason: {webmcp_skip_reason})"

    **When enabled, run three health checks for each completed plan in this wave:**

    ```
    ## WebMCP Health Check — Plan {PLAN_ID}

    Check 1: hive_get_health_status
    → Verify backend is responding. Expected: status field indicates healthy.

    Check 2: hive_check_console_errors
    → Verify no new JavaScript errors. Expected: no new errors since plan execution started.

    Check 3: hive_get_page_info
    → Verify app is rendering. Expected: page has content and is interactive.
    ```

    **Retry logic:**
    - If ANY check fails, log which check failed and the error
    - Retry the FAILED check(s) once (not all three — only the ones that failed)
    - If the retry also fails, HALT execution with:
      ```
      ## WebMCP Health Check FAILED

      **Plan:** {PLAN_ID}
      **Failed check:** {check_name} (e.g., hive_check_console_errors)
      **Error:** {error_details}
      **Attempts:** 2/2

      Execution halted. The web application is in an unhealthy state after plan {PLAN_ID}.

      Options:
      - Fix the issue and re-run: `/grd:execute-phase {PHASE}`
      - Skip WebMCP checks: set `webmcp.enabled: false` in `.planning/config.json`
      ```
    - If all checks pass (including any retried ones): continue to step 5

    **Important:** These checks call Chrome DevTools MCP tools directly via the orchestrator's tool access (not via a subagent). The orchestrator already has access to MCP tools from its tool list.

5. **Handle failures:**

   **Known Claude Code bug (classifyHandoffIfNeeded):** If an agent reports "failed" with error containing `classifyHandoffIfNeeded is not defined`, this is a Claude Code runtime bug — not a GRD or agent issue. The error fires in the completion handler AFTER all tool calls finish. In this case: run the same spot-checks as step 4 (SUMMARY.md exists, git commits present, no Self-Check: FAILED). If spot-checks PASS -> treat as **successful**. If spot-checks FAIL -> treat as real failure below.

   For real failures: report which plan failed -> ask "Continue?" or "Stop?" -> if continue, dependent plans may also fail. If stop, partial completion report.

6. **Execute checkpoint plans between waves** — see `<checkpoint_handling>`.

7. **Proceed to next wave.**
</step>

<step name="checkpoint_handling">
Plans with `autonomous: false` require user interaction.

**Flow:**

1. Spawn agent for checkpoint plan
2. Agent runs until checkpoint task or auth gate -> returns structured state
3. Agent return includes: completed tasks table, current task + blocker, checkpoint type/details, what's awaited
4. **Present to user:**
   ```
   ## Checkpoint: [Type]

   **Plan:** 03-03 Dashboard Layout
   **Progress:** 2/3 tasks complete

   [Checkpoint Details from agent return]
   [Awaiting section from agent return]
   ```
5. User responds: "approved"/"done" | issue description | decision selection
6. **Spawn continuation agent (NOT resume)** using continuation-prompt.md template
7. Continuation agent verifies previous commits, continues from resume point
8. Repeat until plan completes or user stops
</step>

<step name="aggregate_results">
After all waves:

```markdown
## Phase {X}: {Name} Execution Complete

**Waves:** {N} | **Plans:** {M}/{total} complete

| Wave | Plans | Status |
|------|-------|--------|
| 1 | plan-01, plan-02 | Complete |
| CP | plan-03 | Verified |
| 2 | plan-04 | Complete |

### Plan Details
1. **03-01**: [one-liner from SUMMARY.md]
2. **03-02**: [one-liner from SUMMARY.md]

### Issues Encountered
[Aggregate from SUMMARYs, or "None"]
```
</step>

<step name="completion_flow" condition="branching_strategy != none">
Present the user with 4 completion options for the worktree.

**ExitWorktree (native isolation only):**

Before presenting completion options, exit the worktree to return to the main repository context. This ensures all subsequent git operations (merge, PR, discard) run from the correct directory.

When ISOLATION_MODE=native, call the ExitWorktree tool:

```
Use the ExitWorktree tool to leave the current worktree and return to the main repository.
```

This step is skipped when ISOLATION_MODE=manual (GRD manages worktree switching itself) or when branching_strategy=none.

**When ISOLATION_MODE=native:**

After all executor agents complete, determine the worktree branch name and path:

1. **Find the worktree:** Run `git worktree list` in the main repo (`${MAIN_REPO_PATH}`) and look for the phase branch. The branch may follow Claude Code's naming convention rather than GRD's template.
2. **Capture branch name:** Parse the branch from `git worktree list` output, or run `git rev-parse --abbrev-ref HEAD` within the worktree directory.
3. **Capture worktree path:** Parse from `git worktree list` output. Store as `WORKTREE_PATH` for use in completion commands.

Present the 4 completion options:

```
## Phase Complete -- Choose Completion Action

All plans executed in worktree. Choose how to finish:

1. **Merge locally** -- Run tests, merge branch into base, delete worktree
2. **Push and create PR** -- Run tests, push branch, create PR, delete worktree
3. **Keep branch** -- Leave worktree and branch intact for later
4. **Discard work** -- Delete worktree and branch (destructive!)
```

For each completion option, use the worktree commands with explicit branch name:
- **Merge:** `node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js worktree merge --phase "${PHASE_NUMBER}" --branch "${BRANCH_NAME}"` (uses the explicit branch parameter from Plan 02 to handle non-GRD branch names)
- **PR:** `node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js worktree push-pr --phase "${PHASE_NUMBER}"` (reads branch from worktree HEAD automatically)
- **Keep:** Note the branch name and worktree path for the user
- **Discard:** `node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js worktree remove --phase "${PHASE_NUMBER}"`

The test gate should run in the worktree directory. Use the worktree path obtained from step 1.

**When ISOLATION_MODE=manual:**

Preserve existing v0.2.5 completion flow exactly:

```
## Phase Complete -- Choose Completion Action

All plans executed in worktree. Choose how to finish:

1. **Merge locally** -- Run tests, merge branch into base, delete worktree
2. **Push and create PR** -- Run tests, push branch, create PR, delete worktree
3. **Keep branch** -- Leave worktree and branch intact for later
4. **Discard work** -- Delete worktree and branch (destructive!)
```

Based on user's choice, call the worktree complete command:

```bash
COMPLETE_RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js worktree complete \
  --action "${ACTION}" \
  --phase "${PHASE_NUMBER}" \
  [--test-cmd "${TEST_CMD}"] \
  [--base "${BASE_BRANCH}"] \
  [--title "Phase ${PHASE_NUMBER}: ${PHASE_NAME} (${MILESTONE_VERSION})"] \
  [--body "${PR_BODY}"])
```

**For both modes, parse JSON result:**

**If `blocked: true`** (test gate failed on merge/pr):
```
## Test Gate Failed

Tests must pass before merge/PR. Fix failures and retry.

Exit code: ${test_exit_code}
${test_stdout}
${test_stderr}

Options:
- Fix tests in the worktree and retry completion
- Choose "keep" to preserve the branch for manual fixing
- Choose "discard" to abandon this work
```

**If `error`** (merge conflict, push failure, etc.):
Report the error with actionable guidance.

**If success** (action-specific):
- `merge`: "Phase ${PHASE_NUMBER} merged into ${BASE_BRANCH}. Branch deleted."
- `pr`: "PR created: ${pr_url}. Branch: ${branch} -> ${base}."
- `keep`: "Worktree and branch preserved at ${path}."
- `discard`: "Worktree and branch deleted."
</step>

<step name="tracker_sync">
**Sync phase status to issue tracker (non-blocking):**

Check tracker config provider first. **For GitHub:**
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker sync-phase "${PHASE_NUMBER}" --raw 2>/dev/null || true
```

**For mcp-atlassian** (see @${CLAUDE_PLUGIN_ROOT}/references/mcp-tracker-protocol.md):
```bash
OPS=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker prepare-phase-sync "${PHASE_NUMBER}" --raw)
```
For each `"create"` operation, call MCP `create_issue`, then `record-mapping`.

This updates the tracker with any new plans created during execution. If no tracker is configured, this is a no-op.
</step>

<step name="code_review_per_phase" condition="code_review_timing=per_phase AND code_review_enabled=true">
**Run code review across all plans in the phase.**

Only when `code_review_timing: "per_phase"` and `code_review_enabled: true`. Skipped if `timing: "per_wave"` (already reviewed per wave) or `timing: "disabled"`.

```
Task(
  subagent_type="grd:grd-code-reviewer",
  model="{reviewer_model}",
  prompt="
    Review all plans in phase ${PHASE_NUMBER}.
    Plans: ${ALL_PLAN_IDS}
    Phase directory: ${PHASE_DIR}

    PATHS:
    research_dir: ${research_dir}
    phases_dir: ${phases_dir}
    phase_dir: ${phase_dir}
    codebase_dir: ${codebase_dir}

    <phase_context>
    ${context_content or "No CONTEXT.md found for this phase."}
    </phase_context>

    Read each plan's PLAN.md and SUMMARY.md.
    Produce ${PHASE_NUMBER}-REVIEW.md in ${PHASE_DIR}.
  "
)
```

**Handle review verdict** (same as per-wave handling):
- `pass`: Continue to eval
- `blocker_found`: Present to user, get acknowledgement before proceeding
- `warnings_only`: Present if `severity_gate="warning"`, else continue
</step>

<step name="teardown_phase_team" condition="use_teams=true">
**Only when `use_teams: true`.**

After all execution and review complete, shut down the team:

1. Send shutdown requests to all remaining teammates:
   ```
   SendMessage(type="shutdown_request", recipient="executor-{plan_id}", content="Phase execution complete")
   ```
   For each teammate still active.

2. Wait for shutdown confirmations.

3. Delete the team:
   ```
   TeamDelete()
   ```
</step>

<step name="auto_trigger_eval">
**After execution completes, check for EVAL.md:**

```bash
ls "${PHASE_DIR}"/*-EVAL.md 2>/dev/null
```

**If EVAL.md exists:**

Display:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► RUNNING EVALUATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

Read EVAL.md and run Tier 1 (sanity) and Tier 2 (proxy) checks automatically.
Record results in `{phase}-EVAL-RESULTS.md`.

**If eval results < targets:**

```
## Eval Results Below Targets

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| [metric] | [target] | [actual] | BELOW |

---

**Options:**
- /grd:iterate — re-evaluate approach and iterate
- /grd:plan-phase {X} --gaps — plan targeted fixes
- Continue to verification anyway
```

**If eval results >= targets:** Report success, continue to verification.

**If no EVAL.md:** Skip eval, proceed to verification.
</step>

<step name="verify_phase_goal">
Verify phase achieved its GOAL, not just completed tasks.

```
Task(
  prompt="Verify phase {phase_number} goal achievement.
Phase directory: {phase_dir}
Phase goal: {goal from ROADMAP.md}
Check must_haves against actual codebase. Create VERIFICATION.md.

PATHS:
research_dir: ${research_dir}
phases_dir: ${phases_dir}
phase_dir: ${phase_dir}
codebase_dir: ${codebase_dir}

<phase_context>
${context_content or 'No CONTEXT.md found for this phase.'}
</phase_context>",
  subagent_type="grd:grd-verifier",
  model="{verifier_model}"
)
```

Read status:
```bash
grep "^status:" "$PHASE_DIR"/*-VERIFICATION.md | cut -d: -f2 | tr -d ' '
```

| Status | Action |
|--------|--------|
| `passed` | -> update_roadmap |
| `human_needed` | Present items for human testing, get approval or feedback |
| `gaps_found` | Present gap summary, offer `/grd:plan-phase {phase} --gaps` |

**If gaps_found:**
```
## Phase {X}: {Name} — Gaps Found

**Score:** {N}/{M} must-haves verified
**Report:** {phase_dir}/{phase}-VERIFICATION.md

### What's Missing
{Gap summaries from VERIFICATION.md}

---
## Next Up

`/grd:plan-phase {X} --gaps`
`/grd:iterate` — if eval results also below target

<sub>`/clear` first -> fresh context window</sub>

Also: `cat {phase_dir}/{phase}-VERIFICATION.md` — full report
Also: `/grd:verify-work {X}` — manual testing first
```

Gap closure cycle: `/grd:plan-phase {X} --gaps` reads VERIFICATION.md -> creates gap plans with `gap_closure: true` -> user runs `/grd:execute-phase {X} --gaps-only` -> verifier re-runs.
</step>

<step name="update_roadmap">
Mark phase complete in ROADMAP.md (date, status).

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs(phase-{X}): complete phase execution" --files .planning/ROADMAP.md .planning/STATE.md ${phase_dir}/*-VERIFICATION.md .planning/REQUIREMENTS.md
```
</step>

<step name="offer_next">

**If more phases:**
```
## Next Up

**Phase {X+1}: {Name}** — {Goal}

`/grd:plan-phase {X+1}`

<sub>`/clear` first for fresh context</sub>
```

**If milestone complete:**
```
MILESTONE COMPLETE!

All {N} phases executed.

`/grd:complete-milestone`
```
</step>

</process>

<context_efficiency>
Orchestrator: ~10-15% context. Subagents: fresh 200k each. No polling (Task blocks). No context bleed.
</context_efficiency>

<failure_handling>
- **classifyHandoffIfNeeded false failure:** Agent reports "failed" but error is `classifyHandoffIfNeeded is not defined` -> Claude Code bug, not GRD. Spot-check (SUMMARY exists, commits present) -> if pass, treat as success
- **Agent fails mid-plan:** Missing SUMMARY.md -> report, ask user how to proceed
- **Dependency chain breaks:** Wave 1 fails -> Wave 2 dependents likely fail -> user chooses attempt or skip
- **All agents in wave fail:** Systemic issue -> stop, report for investigation
- **Checkpoint unresolvable:** "Skip this plan?" or "Abort phase execution?" -> record partial progress in STATE.md
</failure_handling>

<resumption>
Re-run `/grd:execute-phase {phase}` -> discover_plans finds completed SUMMARYs -> skips them -> resumes from first incomplete plan -> continues wave execution.

STATE.md tracks: last completed plan, current wave, pending checkpoints.

**When resuming with manual isolation:** Check if a worktree already exists for this phase (via `worktree list`). If it does, reuse it instead of creating a new one. If it doesn't, create a fresh one. The `setup_isolation` step handles this: if `worktree create` returns an `error` with "already exists", parse the existing worktree path from the error response and reuse it.

**When resuming with native isolation:** Check `git worktree list` in the main repo for an existing worktree on the phase branch. If found, reuse it — Claude Code will recognize the existing worktree when spawning executor agents with `isolation: "worktree"`. If not found, native isolation will create a new one automatically on the next executor spawn.
</resumption>

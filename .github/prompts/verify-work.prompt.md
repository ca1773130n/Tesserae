---
description: Validate built features through conversational testing with metrics
argument-hint: <phase number>
---

<purpose>
Validate built features through conversational testing with persistent state and quantitative metrics. Creates UAT.md that tracks test progress, survives /clear, and feeds gaps into /grd:plan-phase --gaps.

User tests, Claude records. One test at a time. Plain text responses.

GRD addition: After standard UAT tests, also checks quantitative metrics from EVAL.md if available. Metric tests present expected values and let users verify actuals.
</purpose>

<philosophy>
**Show expected, ask if reality matches.**

Claude presents what SHOULD happen. User confirms or describes what's different.
- "yes" / "y" / "next" / empty → pass
- Anything else → logged as issue, severity inferred

No Pass/Fail buttons. No severity questions. Just: "Here's what should happen. Does it?"

**R&D extension:** For quantitative metrics, present expected ranges and actual values. User confirms if results are acceptable or flags concerns.
</philosophy>

<template>
@${CLAUDE_PLUGIN_ROOT}/templates/UAT.md
</template>

<process>

<step name="initialize" priority="first">
If [user-provided arguments] contains a phase number, load context:

```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init verify-work "${PHASE_ARG}")
```

Parse JSON for: `planner_model`, `checker_model`, `commit_docs`, `phase_found`, `phase_dir`, `phase_number`, `phase_name`, `has_verification`, `has_eval`.
</step>

<step name="check_active_session">
**First: Check for active UAT sessions**

```bash
find ${phases_dir} -name "*-UAT.md" -type f 2>/dev/null | head -5
```

**If active sessions exist AND no [user-provided arguments] provided:**

Read each file's frontmatter (status, phase) and Current Test section.

Display inline:

```
## Active UAT Sessions

| # | Phase | Status | Current Test | Progress |
|---|-------|--------|--------------|----------|
| 1 | 04-model | testing | 3. Forward Pass | 2/6 |
| 2 | 05-training | testing | 1. Training Loop | 0/4 |

Reply with a number to resume, or provide a phase number to start new.
```

Wait for user response.

- If user replies with number (1, 2) → Load that file, go to `resume_from_file`
- If user replies with phase number → Treat as new session, go to `create_uat_file`

**If active sessions exist AND [user-provided arguments] provided:**

Check if session exists for that phase. If yes, offer to resume or restart.
If no, continue to `create_uat_file`.

**If no active sessions AND no [user-provided arguments]:**

```
No active UAT sessions.

Provide a phase number to start testing (e.g., /grd:verify-work 4)
```

**If no active sessions AND [user-provided arguments] provided:**

Continue to `create_uat_file`.
</step>

<step name="find_summaries">
**Find what to test:**

Use `phase_dir` from init (or run init if not already done).

```bash
ls "$phase_dir"/*-SUMMARY.md 2>/dev/null
```

Read each SUMMARY.md to extract testable deliverables.

**GRD: Also check for EVAL.md:**
```bash
ls "$phase_dir"/*-EVAL.md 2>/dev/null
```

If EVAL.md exists, extract quantitative test criteria (metrics, baselines, targets).
</step>

<step name="extract_tests">
**Extract testable deliverables from SUMMARY.md:**

Parse for:
1. **Accomplishments** - Features/functionality added
2. **User-facing changes** - UI, workflows, interactions
3. **Experiment results** - Model metrics, evaluation outcomes (GRD-specific)

Focus on USER-OBSERVABLE outcomes and MEASURABLE metrics, not implementation details.

For each deliverable, create a test:
- name: Brief test name
- expected: What the user should see/experience (specific, observable)

**GRD-specific tests from EVAL.md (if exists):**
- name: Metric name
- expected: "Metric X should be >= Y (baseline: Z)"
- type: metric (distinguish from functional tests)
- verify_command: Command to run to check the metric

Examples:
- Accomplishment: "Implemented attention mechanism with learned positional encoding"
  → Test: "Model Forward Pass"
  → Expected: "Running forward pass on sample input produces output with expected shape"

- Eval metric: "accuracy: 0.856 (target: >= 0.85)"
  → Test: "Model Accuracy"
  → Expected: "Accuracy >= 0.85 (baseline: 0.78, target: 0.85). Run: `python src/eval/evaluate.py`"
  → Type: metric

Skip internal/non-observable items (refactors, type changes, etc.).
</step>

<step name="create_uat_file">
**Create UAT file with all tests:**

```bash
mkdir -p "$PHASE_DIR"
```

Build test list from extracted deliverables.

Create file:

```markdown
---
status: testing
phase: XX-name
source: [list of SUMMARY.md files]
eval_source: [EVAL.md if exists, null otherwise]
started: [ISO timestamp]
updated: [ISO timestamp]
---

## Current Test
<!-- OVERWRITE each test - shows where we are -->

number: 1
name: [first test name]
expected: |
  [what user should observe]
awaiting: user response

## Tests

### 1. [Test Name]
expected: [observable behavior]
result: [pending]

### 2. [Test Name]
expected: [observable behavior]
result: [pending]

...

### M. [Metric Test Name] (metric)
expected: [metric >= target (baseline: X)]
verify_command: [command to run]
result: [pending]

## Summary

total: [N]
passed: 0
issues: 0
pending: [N]
skipped: 0
metrics_checked: 0
metrics_passed: 0

## Gaps

[none yet]
```

Write to `${phase_dir}/{phase}-UAT.md`

Proceed to `present_test`.
</step>

<step name="present_test">
**Present current test to user:**

Read Current Test section from UAT file.

**For functional tests:**

Display using checkpoint box format:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CHECKPOINT: Verification Required
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Test {number}: {name}**

{expected}

──────────────────────────────────────────────────────
→ Type "pass" or describe what's wrong
──────────────────────────────────────────────────────
```

**For metric tests (GRD-specific):**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 CHECKPOINT: Metric Verification
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Test {number}: {name}** (metric)

{expected}

**To verify, run:**
{verify_command}

──────────────────────────────────────────────────────
→ Type "pass {actual_value}" or describe the issue
──────────────────────────────────────────────────────
```

Wait for user response (plain text, no AskUserQuestion).
</step>

<step name="process_response">
**Process user response and update file:**

**If response indicates pass:**
- Empty response, "yes", "y", "ok", "pass", "next", "approved"

Update Tests section:
```
### {N}. {name}
expected: {expected}
result: pass
```

For metric tests with value provided (e.g., "pass 0.862"):
```
### {N}. {name} (metric)
expected: {expected}
result: pass
actual_value: {value from user}
delta_from_baseline: {+/- delta}
```

**If response indicates skip:**
- "skip", "can't test", "n/a"

Update Tests section:
```
### {N}. {name}
expected: {expected}
result: skipped
reason: [user's reason if provided]
```

**If response is anything else:**
- Treat as issue description

Infer severity from description:
- Contains: crash, error, exception, fails, broken, unusable → blocker
- Contains: doesn't work, wrong, missing, can't → major
- Contains: slow, weird, off, minor, small → minor
- Contains: color, font, spacing, alignment, visual → cosmetic
- Contains: below target, metric worse, regression, lower than → metric_miss (GRD-specific)
- Default if unclear: major

Update Tests section:
```
### {N}. {name}
expected: {expected}
result: issue
reported: "{verbatim user response}"
severity: {inferred}
```

Append to Gaps section (structured YAML for plan-phase --gaps):
```yaml
- truth: "{expected behavior from test}"
  status: failed
  reason: "User reported: {verbatim user response}"
  severity: {inferred}
  test: {N}
  type: {functional | metric}
  actual_value: {if metric test, value reported}
  artifacts: []  # Filled by diagnosis
  missing: []    # Filled by diagnosis
```

**After any response:**

Update Summary counts (including metrics_checked and metrics_passed for metric tests).
Update frontmatter.updated timestamp.

If more tests remain → Update Current Test, go to `present_test`
If no more tests → Go to `complete_session`
</step>

<step name="resume_from_file">
**Resume testing from UAT file:**

Read the full UAT file.

Find first test with `result: [pending]`.

Announce:
```
Resuming: Phase {phase} UAT
Progress: {passed + issues + skipped}/{total}
Issues found so far: {issues count}
Metrics: {metrics_passed}/{metrics_checked} passed

Continuing from Test {N}...
```

Update Current Test section with the pending test.
Proceed to `present_test`.
</step>

<step name="complete_session">
**Complete testing and commit:**

Update frontmatter:
- status: complete
- updated: [now]

Clear Current Test section:
```
## Current Test

[testing complete]
```

Commit the UAT file:
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "test({phase}): complete UAT - {passed} passed, {issues} issues, {metrics_passed}/{metrics_checked} metrics" --files "${phase_dir}/{phase}-UAT.md"
```

Present summary:
```
## UAT Complete: Phase {phase}

| Result | Count |
|--------|-------|
| Passed | {N}   |
| Issues | {N}   |
| Skipped| {N}   |
| Metrics Checked | {N} |
| Metrics Passed  | {N} |

[If metric issues:]
### Metric Results

| Metric | Baseline | Target | Actual | Status |
|--------|----------|--------|--------|--------|
| {name} | {baseline} | {target} | {actual} | PASS/FAIL |

[If issues > 0:]
### Issues Found

[List from Issues section]
```

**If issues > 0:** Proceed to `diagnose_issues`

**If issues == 0:**
```
All tests passed. Ready to continue.

- `/grd:plan-phase {next}` — Plan next phase
- `/grd:execute-phase {next}` — Execute next phase
- `/grd:eval-report {phase}` — Generate detailed evaluation report
- `/grd:iterate {phase}` — If metrics need improvement
```
</step>

<step name="diagnose_issues">
**Diagnose root causes before planning fixes:**

```
---

{N} issues found. Diagnosing root causes...

Spawning parallel debug agents to investigate each issue.
```

Resolve debugger model:
```bash
DEBUGGER_MODEL=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js resolve-model grd-debugger --raw)
```

For each gap, spawn a grd-debugger agent with symptoms pre-filled:

```
Task(
  prompt="""
<objective>
Investigate issue: {slug}

**Summary:** {truth}
</objective>

<symptoms>
expected: {expected}
actual: {reported}
errors: {errors or "None reported"}
reproduction: Test {test_num} in UAT
timeline: Discovered during UAT
</symptoms>

<phase_context>
@${phase_dir}/*-CONTEXT.md
</phase_context>

<mode>
symptoms_prefilled: true
goal: find_root_cause_only
</mode>

<debug_file>
Create: .planning/debug/{slug}.md
</debug_file>
""",
  subagent_type="grd:grd-debugger",
  model="{debugger_model}",
  description="Debug: {truth_short}"
)
```

**All agents spawn in single message** (parallel execution).

Collect root causes from all agents. Update UAT.md gaps with root_cause, artifacts, and missing fields.

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs({phase}): add root causes from diagnosis" --files "${phase_dir}/{phase}-UAT.md"
```

Display diagnosis summary, then proceed to `plan_gap_closure`.
</step>

<step name="plan_gap_closure">
**Auto-plan fixes from diagnosed gaps:**

Display:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► PLANNING FIXES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Spawning planner for gap closure...
```

Resolve planner model:
```bash
PLANNER_MODEL=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js resolve-model grd-planner --raw)
```

Spawn grd-planner in --gaps mode:

```
Task(
  prompt="""
<planning_context>

**Phase:** {phase_number}
**Mode:** gap_closure

**UAT with diagnoses:**
@${phase_dir}/{phase}-UAT.md

**Project State:**
@.planning/STATE.md

**Roadmap:**
@.planning/ROADMAP.md

**Phase Context:**
@${phase_dir}/*-CONTEXT.md

</planning_context>

<paths>
research_dir: ${research_dir}
phase_dir: ${phase_dir}
</paths>

<downstream_consumer>
Output consumed by /grd:execute-phase
Plans must be executable prompts.
</downstream_consumer>
""",
  subagent_type="grd:grd-planner",
  model="{planner_model}",
  description="Plan gap fixes for Phase {phase}"
)
```

On return:
- **PLANNING COMPLETE:** Proceed to `verify_gap_plans`
- **PLANNING INCONCLUSIVE:** Report and offer manual intervention
</step>

<step name="verify_gap_plans">
**Verify fix plans with checker:**

Display:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► VERIFYING FIX PLANS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Spawning plan checker...
```

Initialize: `iteration_count = 1`

Resolve checker model:
```bash
CHECKER_MODEL=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js resolve-model grd-plan-checker --raw)
```

Spawn grd-plan-checker:

```
Task(
  prompt="""
<verification_context>

**Phase:** {phase_number}
**Phase Goal:** Close diagnosed gaps from UAT

**Plans to verify:**
@${phase_dir}/*-PLAN.md

**Phase Context:**
@${phase_dir}/*-CONTEXT.md

</verification_context>

<paths>
research_dir: ${research_dir}
phase_dir: ${phase_dir}
</paths>

<expected_output>
Return one of:
- ## VERIFICATION PASSED — all checks pass
- ## ISSUES FOUND — structured issue list
</expected_output>
""",
  subagent_type="grd:grd-plan-checker",
  model="{checker_model}",
  description="Verify Phase {phase} fix plans"
)
```

On return:
- **VERIFICATION PASSED:** Proceed to `present_ready`
- **ISSUES FOUND:** Proceed to `revision_loop`
</step>

<step name="revision_loop">
**Iterate planner <-> checker until plans pass (max 3):**

**If iteration_count < 3:**

Display: `Sending back to planner for revision... (iteration {N}/3)`

Spawn grd-planner with revision context, then spawn checker again.
Increment iteration_count.

**If iteration_count >= 3:**

Display: `Max iterations reached. {N} issues remain.`

Offer options:
1. Force proceed (execute despite issues)
2. Provide guidance (user gives direction, retry)
3. Abandon (exit, user runs /grd:plan-phase manually)

Wait for user response.
</step>

<step name="present_ready">
**Present completion and next steps:**

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► FIXES READY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Phase {X}: {Name}** — {N} gap(s) diagnosed, {M} fix plan(s) created

| Gap | Type | Root Cause | Fix Plan |
|-----|------|------------|----------|
| {truth 1} | functional | {root_cause} | {phase}-04 |
| {truth 2} | metric | {root_cause} | {phase}-05 |

Plans verified and ready for execution.

───────────────────────────────────────────────────────────────

## Next Up

**Execute fixes** — run fix plans

`/clear` then `/grd:execute-phase {phase} --gaps-only`

───────────────────────────────────────────────────────────────

**For metric gaps, also consider:**
- `/grd:iterate {phase}` — Full iteration loop (re-survey, re-plan, re-execute)
- `/grd:eval-report {phase}` — Detailed metric analysis before fixing
───────────────────────────────────────────────────────────────
```
</step>

</process>

<update_rules>
**Batched writes for efficiency:**

Keep results in memory. Write to file only when:
1. **Issue found** — Preserve the problem immediately
2. **Session complete** — Final write before commit
3. **Checkpoint** — Every 5 passed tests (safety net)

| Section | Rule | When Written |
|---------|------|--------------|
| Frontmatter.status | OVERWRITE | Start, complete |
| Frontmatter.updated | OVERWRITE | On any file write |
| Current Test | OVERWRITE | On any file write |
| Tests.{N}.result | OVERWRITE | On any file write |
| Summary | OVERWRITE | On any file write |
| Gaps | APPEND | When issue found |

On context reset: File shows last checkpoint. Resume from there.
</update_rules>

<severity_inference>
**Infer severity from user's natural language:**

| User says | Infer |
|-----------|-------|
| "crashes", "error", "exception", "fails completely" | blocker |
| "doesn't work", "nothing happens", "wrong behavior" | major |
| "works but...", "slow", "weird", "minor issue" | minor |
| "color", "spacing", "alignment", "looks off" | cosmetic |
| "below target", "metric worse", "regression", "accuracy dropped" | metric_miss |

Default to **major** if unclear. User can correct if needed.

**Never ask "how severe is this?"** - just infer and move on.
</severity_inference>

<success_criteria>
- [ ] UAT file created with all tests from SUMMARY.md
- [ ] Metric tests extracted from EVAL.md if available
- [ ] Tests presented one at a time with expected behavior
- [ ] Metric tests include verify_command and expected range
- [ ] User responses processed as pass/issue/skip
- [ ] Metric actual values recorded when provided
- [ ] Severity inferred from description (never asked)
- [ ] Batched writes: on issue, every 5 passes, or completion
- [ ] Committed on completion with metric summary
- [ ] If issues: parallel grd-debugger agents diagnose root causes
- [ ] If issues: grd-planner creates fix plans (gap_closure mode)
- [ ] If issues: grd-plan-checker verifies fix plans
- [ ] If issues: revision loop until plans pass (max 3 iterations)
- [ ] Ready for `/grd:execute-phase --gaps-only` when complete
</success_criteria>

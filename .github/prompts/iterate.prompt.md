---
description: Trigger iteration loop — re-evaluate, re-survey, re-plan based on eval results not meeting targets
argument-hint: <phase number or name>
---

<purpose>
Trigger iteration loop when evaluation results do not meet targets. Re-evaluates the
situation, identifies which metrics are below target and why, then offers structured
options: survey alternatives, adjust approach, adjust targets, or skip. Records all
iterations in EVAL.md history and updates KNOWHOW.md with failure analysis and
next-approach rationale.
</purpose>

<context>
CLAUDE.md rules: @CLAUDE.md

**Project structure** (paths resolved via init):
- `${phase_dir}/EVAL.md` — evaluation results with targets
- `${phase_dir}/PLAN.md` — current phase plan
- `.planning/KNOWHOW.md` — accumulated engineering knowledge
- `.planning/ROADMAP.md` — project roadmap
- `${research_dir}/LANDSCAPE.md` — research landscape
- `.planning/BENCHMARKS.md` — historical benchmark data
- `.planning/config.json` — GRD configuration (autonomous_mode)

**This workflow does NOT spawn a dedicated agent.** It orchestrates decisions using
existing data and delegates to other GRD workflows as needed.
</context>

<process>

## Step 0: INITIALIZE — Load Evaluation State

0. **Run initialization**:
   ```bash
   INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init iterate "$PHASE")
   ```
   Parse JSON for: `research_dir`, `phases_dir`, `phase_dir` (resolve from phases_dir + phase number), `landscape_exists`, `knowhow_exists`, `baseline_exists`, `autonomous_mode`.

1. **Parse arguments**: Extract phase identifier from `[user-provided arguments]`
   - If phase number: resolve to `${phases_dir}/{N}-{name}/`
   - If empty: detect phase with most recent EVAL.md results below target
   - Validate phase directory and EVAL.md exist

2. **Load EVAL.md results**:
   - Parse the most recent `## Results` section
   - Extract per-metric results: `{metric, baseline, target, result, status}`
   - Identify metrics with status = MISS (below target)
   - Load `## History` section for iteration count

3. **Load supporting context**:
   - PLAN.md — current approach being used
   - CONTEXT.md — user decisions and constraints for this phase
   - KNOWHOW.md — any prior failure learnings
   - LANDSCAPE.md — alternative methods available
   - BENCHMARKS.md — trend data across iterations

4. **Compute iteration number**:
   - Count entries in EVAL.md History section
   - Current iteration = previous count + 1

**STEP_0_CHECKPOINT:**
- [ ] Phase identified with EVAL.md results
- [ ] Below-target metrics identified
- [ ] Iteration number computed
- [ ] Supporting context loaded

---

## Step 1: DISPLAY ITERATION TRIGGER

```
╔══════════════════════════════════════════════════════════════╗
║  GRD >>> ITERATION TRIGGERED                                ║
║                                                             ║
║  Phase: {N} — {phase_name}                                  ║
║  Iteration: #{iteration_number}                             ║
║                                                             ║
║  Metrics below target:                                      ║
║    {metric_1}: {actual} vs target {target} (gap: {gap})     ║
║    {metric_2}: {actual} vs target {target} (gap: {gap})     ║
║                                                             ║
║  Metrics meeting target:                                    ║
║    {metric_3}: {actual} >= {target}  OK                     ║
║                                                             ║
║  Trend: {improving | stagnant | regressing}                 ║
║                                                             ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 2: ANALYZE FAILURE

Using extended thinking, analyze why metrics fell short:

1. **Gap characterization**:
   - For each below-target metric:
     - Gap magnitude: `|target - actual| / |target - baseline|` (% of improvement missing)
     - Trend direction: improving, flat, or regressing across iterations?
     - Correlation: do failures cluster (e.g., all accuracy metrics fail together)?

2. **Root cause hypothesis**:
   - Is the approach fundamentally limited? (architectural ceiling)
   - Is the implementation incomplete? (more engineering needed)
   - Is the data insufficient? (quality/quantity issue)
   - Are the targets unrealistic? (SOTA gap too large for one phase)
   - Is there a bug masking real performance? (measurement issue)

3. **Effort-to-close estimate**:
   - For each gap: estimated effort to close with current approach
   - Diminishing returns analysis: are we on a plateau?

```
FAILURE ANALYSIS:

  Root cause hypothesis: {hypothesis}
  Confidence: {HIGH | MEDIUM | LOW}
  Evidence: {what supports this hypothesis}

  Gap closure estimate:
    {metric_1}: {effort} to close {gap_pct}% gap
    {metric_2}: {effort} to close {gap_pct}% gap

  Plateau detection: {yes/no — are improvements flattening?}
```

---

## Step 3: PRESENT OPTIONS

```
╔══════════════════════════════════════════════════════════════╗
║  ITERATION OPTIONS                                          ║
╠══════════════════════════════════════════════════════════════╣
║                                                             ║
║  a) SURVEY ALTERNATIVES                                     ║
║     Run /grd:survey with refined topic based on failure     ║
║     Then re-plan phase with new approach                    ║
║     Best when: current approach has hit its ceiling         ║
║                                                             ║
║  b) ADJUST APPROACH                                         ║
║     Modify current plan based on failure analysis           ║
║     Keep same method, change implementation strategy        ║
║     Best when: approach is sound but execution needs tuning ║
║                                                             ║
║  c) ADJUST TARGETS                                          ║
║     Lower targets if they were unrealistic                  ║
║     Document justification for target change                ║
║     Best when: targets exceed SOTA or phase scope           ║
║                                                             ║
║  d) SKIP                                                    ║
║     Accept current results and move forward                 ║
║     Document decision to accept sub-target results          ║
║     Best when: results are "good enough" for product goals  ║
║                                                             ║
║  Recommendation: {a|b|c|d} — {rationale}                   ║
║                                                             ║
╚══════════════════════════════════════════════════════════════╝
```

**In autonomous mode**: Execute the recommendation automatically. Log the decision.

**In interactive mode**: Wait for user selection.

---

## Step 4: EXECUTE SELECTED OPTION

### Option A: SURVEY ALTERNATIVES

1. **Derive refined survey topic** from failure analysis:
   - Current approach: {method}
   - Failure mode: {what went wrong}
   - Refined topic: "{original_topic} alternatives that address {failure_mode}"

2. **Execute**: Trigger `/grd:survey {refined_topic}`

3. **After survey completes**:
   - Compare new methods against current approach
   - If better candidate found: update PLAN.md with new approach
   - Re-execute phase with new plan

4. **Record in EVAL.md History**:
   ```
   | {date} | {iter} | SURVEY_ALT | Surveyed alternatives, pivoting to {new_method} |
   ```

### Option B: ADJUST APPROACH

1. **Identify specific adjustments**:
   - Hyperparameter changes
   - Data preprocessing modifications
   - Architecture tweaks within same method
   - Training procedure changes

2. **Update PLAN.md** with adjustments:
   - Document what changed and why
   - Mark adjusted tasks

3. **Re-execute modified plan** (or suggest `/grd:execute-phase`)

4. **Record in EVAL.md History**:
   ```
   | {date} | {iter} | ADJUST_APPROACH | Modified: {what_changed} |
   ```

### Option C: ADJUST TARGETS

1. **For each below-target metric**:
   - Propose new target with justification
   - New target must be above baseline (no regression allowed)
   - Reference SOTA to show target is realistic

2. **Update EVAL.md** target values:
   - Original target preserved in history
   - New target annotated with justification

3. **Record in EVAL.md History**:
   ```
   | {date} | {iter} | ADJUST_TARGET | {metric}: {old_target} -> {new_target} |
   ```

### Option D: SKIP

1. **Document acceptance decision**:
   - Which metrics are below target
   - Why results are acceptable
   - Impact on downstream phases

2. **Record in EVAL.md History**:
   ```
   | {date} | {iter} | SKIP | Accepted {metric} at {value} vs target {target} |
   ```

3. **Update ROADMAP.md**: Mark phase as complete-with-caveats

---

## Step 5: UPDATE KNOWHOW.md

Regardless of option chosen, append failure learnings:

```markdown
## Iteration {N} — Phase {phase} ({date})

**What failed**: {metric} at {value} vs target {target}
**Root cause**: {hypothesis}
**Action taken**: {option chosen and details}
**Key learning**: {what we now know that we did not before}
**Next approach**: {what will be different next time}
```

This section is critical — it prevents repeating the same mistakes.

---

## Step 6: COMMIT

```bash
git add ${phase_dir}/*-EVAL.md
git add ${phase_dir}/*-PLAN.md 2>/dev/null
git add .planning/KNOWHOW.md
git add .planning/ROADMAP.md 2>/dev/null
git commit -m "iterate: phase {N} iter #{iteration} — {option_chosen}"
```

---

## Step 7: GUARD AGAINST INFINITE ITERATION

1. **Check iteration count**:
   - If iteration > 3 for same phase: WARN — consider fundamental approach change
   - If iteration > 5: STRONG WARNING — escalate to product-level re-planning

2. **Check for oscillation**:
   - If metrics alternate between improving and regressing: detect oscillation pattern
   - Suggest: lock hyperparameters, change evaluation methodology, or accept

3. **Display guard status**:
   ```
   Iteration #{N} of max 5 for this phase.
   {Guard status message if applicable}
   ```

---

## Step 8: ROUTE NEXT ACTION

| Option Taken | Next Step |
|-------------|-----------|
| Survey alternatives (with new method) | `/grd:plan-phase {N}` — re-plan with new approach |
| Adjust approach | `/grd:execute-phase {N}` — re-execute modified plan |
| Adjust targets | `/grd:eval-report {N}` — re-evaluate with new targets |
| Skip | `/grd:verify-phase {N}` or next phase |
| Max iterations reached | `/grd:product-plan` — re-evaluate product strategy |

</process>

<output>
**FILES_UPDATED:**
- `${phase_dir}/EVAL.md` — history section updated
- `${phase_dir}/PLAN.md` — updated if approach adjusted
- `.planning/KNOWHOW.md` — failure analysis and learnings appended
- `.planning/ROADMAP.md` — updated if targets/status changed

**DISPLAY**: Failure analysis, option selection, and next-step routing

**GIT**: Committed: `iterate: phase {N} iter #{iteration} — {option_chosen}`
</output>

<error_handling>
- **EVAL.md has no results**: STOP, direct to `/grd:eval-report` first
- **No below-target metrics**: Nothing to iterate on — inform user, suggest verify-phase
- **KNOWHOW.md missing**: Create it with first iteration entry
- **Iteration history malformed**: Reconstruct from EVAL.md results timestamps
- **All options seem equally bad**: Recommend escalating to `/grd:product-plan` for strategy review
</error_handling>

<success_criteria>
- Failure analysis identifies specific root cause (not generic "needs improvement")
- Option recommendation is justified with evidence
- KNOWHOW.md captures reusable learning
- Iteration is recorded in EVAL.md history
- Infinite iteration guard is enforced
- Next action is clear and specific
</success_criteria>

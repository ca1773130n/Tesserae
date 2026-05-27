---
description: Collect and analyze quantitative evaluation results, compare against baselines, run ablations
argument-hint: <phase number or name>
---

<purpose>
Collect and report evaluation results after phase execution. Runs the evaluation protocol
defined in EVAL.md, compares results against baselines and targets, performs ablation
analysis, and updates BENCHMARKS.md with new data points. If results fall below targets,
suggests iteration via /grd:iterate.
</purpose>

<context>
CLAUDE.md rules: @CLAUDE.md

**Project structure** (paths resolved via init):
- `${phase_dir}/EVAL.md` — evaluation plan (must exist)
- `${phase_dir}/PLAN.md` — phase execution plan
- `.planning/BASELINE.md` — current performance baseline
- `.planning/BENCHMARKS.md` — historical benchmark data across phases
- `${research_dir}/LANDSCAPE.md` — SOTA references
- `.planning/config.json` — GRD configuration

**Agent available:**
- `grd-eval-reporter` — specialized evaluation execution and analysis agent
</context>

<process>

## Step 0: INITIALIZE — Load Evaluation Context

0. **Run initialization**:
   ```bash
   INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init eval-report "$PHASE")
   ```
   Parse JSON for: `research_dir`, `phases_dir`, `phase_dir` (resolve from phases_dir + phase number), `landscape_exists`, `baseline_exists`, `autonomous_mode`.

1. **Parse arguments**: Extract phase identifier from `[user-provided arguments]`
   - If phase number: resolve to `${phases_dir}/{N}-{name}/`
   - If empty: detect current active phase
   - Validate phase directory exists

2. **Load EVAL.md**:
   - Path: `${phase_dir}/EVAL.md`
   - If missing: STOP, suggest `/grd:eval-plan {N}` first
   - Parse all three tiers: sanity, proxy, deferred
   - Extract metric definitions, targets, measurement commands

3. **Load context**:
   - Read BASELINE.md (baseline values for comparison)
   - Read BENCHMARKS.md (historical data if available)
   - Read LANDSCAPE.md (SOTA references)

4. **Determine evaluation scope**:
   - Default: run Tier 1 (sanity) + Tier 2 (proxy)
   - If milestone flag set: also run Tier 3 (deferred)
   - If user specifies tier: run only that tier

**STEP_0_CHECKPOINT:**
- [ ] Phase identified and EVAL.md loaded
- [ ] Metric definitions and commands extracted
- [ ] Baseline and historical data loaded
- [ ] Evaluation scope determined

---

## Step 1: DISPLAY BANNER

```
╔══════════════════════════════════════════════════════════════╗
║  GRD >>> EVAL REPORT                                        ║
║                                                             ║
║  Phase: {N} — {phase_name}                                  ║
║  Scope: {Tier 1 + Tier 2 | All tiers}                      ║
║  Metrics to evaluate: {count}                               ║
║  Baseline: {available | unavailable}                        ║
║  Previous results: {count from EVAL.md history}             ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 2: SPAWN EVAL REPORTER AGENT

**Launch `grd-eval-reporter` agent via Task tool:**

Use Task tool with `subagent_type="grd:grd-eval-reporter"`:

```
Execute evaluation protocol and analyze results for phase: {N} — {phase_name}

PATHS:
research_dir: ${research_dir}
phases_dir: ${phases_dir}
phase_dir: ${phase_dir}

EVALUATION PLAN:
{Full EVAL.md content}

BASELINE:
{BASELINE.md content, or "No baseline"}

HISTORICAL RESULTS:
{Previous entries from EVAL.md Results section, or "First evaluation"}

EVALUATION SCOPE: {tiers to run}

EXECUTE THE FOLLOWING PROTOCOL:

## 1. RUN TIER 1 — SANITY CHECKS
For each sanity check in EVAL.md:
- Execute the specified command
- Capture pass/fail result and output
- Record runtime
- If any sanity check FAILS: flag as critical, continue running remaining checks

## 2. RUN TIER 2 — PROXY METRICS
For each proxy metric in EVAL.md:
- Execute the specified evaluation command/script
- Capture numeric result
- Compare against: baseline, target, SOTA
- Compute improvement: (result - baseline) / (target - baseline) * 100%
- Record runtime and resource usage

## 3. RUN TIER 3 — DEFERRED (if in scope)
For each deferred evaluation:
- Execute the specified protocol
- Capture comprehensive results
- Document any issues encountered during execution

## 4. ABLATION ANALYSIS (if ablation plan defined)
For each ablation component:
- Disable the component
- Re-run Tier 2 proxy metrics
- Record impact: delta from full-system result
- Rank components by contribution

## 5. ANALYSIS
- Which metrics met targets? Which fell short?
- What is the gap for metrics below target?
- Trend analysis: improving, stable, or regressing?
- Ablation insights: which components matter most?
- Statistical significance of improvements (if multiple runs)

## 6. VERDICT
- ALL_TARGETS_MET: phase evaluation passes
- PARTIAL: some targets met, some below
- BELOW_TARGETS: most metrics below target
- REGRESSION: worse than baseline

OUTPUT FORMAT:
Return structured results with:
- Per-metric results table (metric, baseline, target, result, status)
- Ablation impact table (component, delta, significance)
- Trend chart data (if historical results available)
- Analysis narrative
- Verdict with specifics on gaps
```

**STEP_2_CHECKPOINT:**
- [ ] Eval reporter agent launched
- [ ] All specified tiers executed
- [ ] Results captured for each metric
- [ ] Analysis and verdict provided

---

## Step 3: DISPLAY RESULTS DASHBOARD

```
╔══════════════════════════════════════════════════════════════╗
║  EVALUATION RESULTS                                         ║
╠══════════════════════════════════════════════════════════════╣
║                                                             ║
║  Phase: {N} — {phase_name}                                  ║
║  Verdict: {ALL_TARGETS_MET | PARTIAL | BELOW_TARGETS |      ║
║            REGRESSION}                                      ║
║                                                             ║
║  TIER 1 — SANITY:  {passed}/{total} checks passed          ║
║  TIER 2 — PROXY:                                            ║
║    {metric_1}: {result} / {target}  {PASS|MISS} {+/-delta}  ║
║    {metric_2}: {result} / {target}  {PASS|MISS} {+/-delta}  ║
║  TIER 3 — DEFERRED: {ran | skipped}                        ║
║                                                             ║
║  vs Baseline: {overall_improvement}% improvement            ║
║  vs SOTA:     {gap_to_sota}% gap remaining                  ║
║                                                             ║
║  Top ablation finding:                                      ║
║    {component}: {contribution}% of total improvement        ║
║                                                             ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 4: UPDATE EVAL.md WITH RESULTS

1. **Populate Results section** in `${phase_dir}/EVAL.md`:
   ```markdown
   ## Results — {YYYY-MM-DD}

   ### Tier 1: Sanity Checks
   | Check | Status | Runtime | Notes |
   |-------|--------|---------|-------|
   | {check} | PASS/FAIL | {time} | {notes} |

   ### Tier 2: Proxy Metrics
   | Metric | Baseline | Target | Result | Status | Delta |
   |--------|----------|--------|--------|--------|-------|
   | {metric} | {base} | {target} | {result} | PASS/MISS | {+/-} |

   ### Tier 3: Deferred (if ran)
   | Evaluation | Result | Notes |
   |------------|--------|-------|
   | {eval} | {result} | {notes} |

   ### Ablation Analysis
   | Component | Full Result | Ablated Result | Delta | Contribution |
   |-----------|-------------|----------------|-------|--------------|
   | {comp} | {full} | {ablated} | {delta} | {%} |

   ### Verdict: {verdict}
   {analysis narrative}
   ```

2. **Append to History section** in EVAL.md:
   ```markdown
   ## History
   | Date | Iteration | Verdict | Key Metric | Value | Notes |
   |------|-----------|---------|------------|-------|-------|
   | {date} | {N} | {verdict} | {metric} | {value} | {notes} |
   ```

---

## Step 5: UPDATE BENCHMARKS.md

1. **Load or create** `.planning/BENCHMARKS.md`
2. **Append new data points**:
   ```markdown
   ## Phase {N}: {phase_name} — {date}

   | Metric | Value | vs_Baseline | vs_SOTA | Trend |
   |--------|-------|-------------|---------|-------|
   | {metric} | {value} | {delta} | {gap} | {up/down/flat} |
   ```
3. **Preserve all historical entries** — this is an append-only log

---

## Step 6: COMMIT

```bash
git add ${phase_dir}/*-EVAL.md
git add .planning/BENCHMARKS.md
git commit -m "eval: report phase {N} results — {verdict}"
```

---

## Step 7: ROUTE NEXT ACTION

| Verdict | Suggestion |
|---------|------------|
| ALL_TARGETS_MET | `/grd:verify-phase {N}` — proceed to verification |
| PARTIAL | Review gaps, decide: iterate or accept |
| BELOW_TARGETS | `/grd:iterate {N}` — trigger iteration loop |
| REGRESSION | `/grd:iterate {N}` — urgent, something broke |

**If BELOW_TARGETS or REGRESSION:**
```
  WARNING: Results below targets.

  Metrics below target:
    {metric_1}: {result} vs target {target} (gap: {gap})
    {metric_2}: {result} vs target {target} (gap: {gap})

  Suggested action: /grd:iterate {N}
  This will analyze gaps and suggest corrections.
```

</process>

<output>
**FILES_UPDATED:**
- `${phase_dir}/EVAL.md` — results and history populated
- `.planning/BENCHMARKS.md` — new benchmark data appended

**DISPLAY**: Results dashboard with per-metric status, ablation findings, and verdict

**GIT**: Committed: `eval: report phase {N} results — {verdict}`
</output>

<error_handling>
- **EVAL.md missing**: STOP, direct to `/grd:eval-plan {N}` first
- **Evaluation command fails**: Record failure, continue with remaining metrics, flag in report
- **No baseline for comparison**: Show absolute values only, note baseline gap
- **Metrics return non-numeric output**: Parse error, ask user for manual metric value
- **All sanity checks fail**: STOP evaluation, suggest debugging: `/grd:debug`
- **BENCHMARKS.md corrupted**: Back up and recreate from EVAL.md history sections
</error_handling>

<success_criteria>
- All specified tiers executed with results captured
- Results compared against baseline, target, and SOTA
- EVAL.md Results section is populated with complete data
- BENCHMARKS.md updated with new data points
- Verdict is clear with specific gap identification
- Below-target metrics trigger clear iteration suggestion
</success_criteria>

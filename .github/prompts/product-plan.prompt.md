---
description: Product-level planning — high-level roadmap, goal setting, and work distribution across research phases
argument-hint: [product goal description or path to goals document]
---

<purpose>
Product-level planning that bridges research findings and engineering execution. Reads
BASELINE.md and product goals, assesses gaps between current state and desired state,
and creates a high-level roadmap distributed across research-informed phases. This is
the strategic layer above individual phase planning.
</purpose>

<context>
CLAUDE.md rules: @CLAUDE.md

**Project structure** (paths resolved via init):
- `.planning/PROJECT.md` — project definition and goals
- `.planning/ROADMAP.md` — phase-level roadmap (output of this workflow)
- `.planning/BASELINE.md` — current system baseline
- `.planning/KNOWHOW.md` — production engineering knowledge
- `${research_dir}/LANDSCAPE.md` — research landscape
- `${research_dir}/COMPARISON-*.md` — method comparisons
- `${research_dir}/deep-dives/` — paper analyses
- `.planning/config.json` — GRD configuration

**Agent available:**
- `grd-product-owner` — specialized product strategy and roadmap planning agent
</context>

<process>

## Step 0: INITIALIZE — Gather All Context

0. **Run initialization**:
   ```bash
   INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init product-plan)
   ```
   Parse JSON for: `research_dir`, `phases_dir`, `landscape_exists`, `papers_exists`, `knowhow_exists`, `baseline_exists`, `autonomous_mode`, `research_gates`.

1. **Parse arguments**: Extract product goals from `[user-provided arguments]`
   - If file path: read goals document
   - If text description: use as product goal input
   - If empty: check PROJECT.md for existing goals, ASK user if not found

2. **Load all available context** (read in parallel):
   - `.planning/PROJECT.md` — existing project definition
   - `.planning/BASELINE.md` — current state metrics
   - `.planning/KNOWHOW.md` — production knowledge learned
   - `${research_dir}/LANDSCAPE.md` — research landscape
   - `${research_dir}/COMPARISON-*.md` — method comparison results
   - Available deep-dives — paper verdicts and feasibility results
   - `.planning/ROADMAP.md` — existing roadmap (if updating)

3. **Assess context completeness**:
   | Input | Status | Impact |
   |-------|--------|--------|
   | BASELINE.md | {present/missing} | {critical for gap analysis} |
   | LANDSCAPE.md | {present/missing} | {critical for method selection} |
   | KNOWHOW.md | {present/missing} | {helpful for effort estimation} |
   | Comparisons | {present/missing} | {helpful for approach selection} |
   | Deep-dives | {N available} | {helpful for risk assessment} |

4. **Read config**: Check `research_gates.product_plan_approval`

**STEP_0_CHECKPOINT:**
- [ ] Product goals extracted (from args, PROJECT.md, or user)
- [ ] Available context loaded and inventoried
- [ ] Context gaps identified
- [ ] Config loaded

---

## Step 1: DISPLAY BANNER

```
╔══════════════════════════════════════════════════════════════╗
║  GRD >>> PRODUCT PLAN                                       ║
║                                                             ║
║  Goal: {product_goal_summary}                               ║
║  Baseline: {available | not assessed}                       ║
║  Research: {N methods surveyed, M deep-dived}               ║
║  Knowhow: {N production insights}                           ║
║  Existing roadmap: {yes — updating | no — creating}         ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 2: SPAWN PRODUCT OWNER AGENT

**Launch `grd-product-owner` agent via Task tool:**

Use Task tool with `subagent_type="grd:grd-product-owner"`:

```
Create product-level plan for: {product_goal}

PATHS:
research_dir: ${research_dir}
codebase_dir: ${codebase_dir}
phases_dir: ${phases_dir}

CURRENT STATE (BASELINE):
{BASELINE.md content, or "Not assessed — will need /grd:assess-baseline"}

RESEARCH CONTEXT:
Landscape: {LANDSCAPE.md summary — methods, benchmarks, recommendations}
Comparisons: {COMPARISON files summary — winners, trade-offs}
Deep-dives: {Paper verdicts and key findings}
Feasibility: {Feasibility verdicts from KNOWHOW.md}

PRODUCTION KNOWLEDGE:
{KNOWHOW.md content, or "No prior production knowledge"}

EXISTING ROADMAP:
{ROADMAP.md content if updating, or "Creating new roadmap"}

CREATE A PRODUCT PLAN WITH THESE SECTIONS:

## 1. VISION AND GOALS
- Product vision statement (1-2 sentences)
- Primary goal with measurable success criteria
- Secondary goals
- Non-goals (explicit exclusions)
- Timeline expectations

## 2. GAP ANALYSIS
Current state → Desired state for each dimension:
| Dimension | Current (from baseline) | Target | Gap | Approach |
|-----------|------------------------|--------|-----|----------|
| {quality metric} | {baseline_value} | {target} | {delta} | {method} |
| {performance metric} | {baseline_value} | {target} | {delta} | {method} |
| {capability} | {present/absent} | {required} | {gap} | {method} |

## 3. APPROACH SELECTION
Based on research context:
- Primary approach: {method} — why selected (reference comparison/feasibility)
- Backup approach: {method} — when to pivot
- Discarded approaches: {methods} — why rejected

## 4. QUALITY TARGETS
Define quantitative targets for the product:
| Metric | Baseline | Phase_1_Target | Phase_2_Target | Final_Target | SOTA |
|--------|----------|----------------|----------------|--------------|------|
| {metric} | {value} | {value} | {value} | {value} | {value} |

## 5. PHASE BREAKDOWN
Distribute work across phases:

### Phase 1: {name} — {goal}
- Scope: {what gets built}
- Research dependency: {method/paper if applicable}
- Success criteria: {measurable}
- Effort estimate: {weeks}
- Risk: {level and key risk}

### Phase 2: {name} — {goal}
...

### Phase N: Integration and Verification
- Always include a final integration/verification phase

## 6. RISK REGISTER
| Risk | Probability | Impact | Mitigation | Owner |
|------|-------------|--------|------------|-------|
| {risk} | {H/M/L} | {H/M/L} | {strategy} | {who} |

## 7. DECISION LOG
Key architectural/approach decisions made during planning:
| Decision | Options Considered | Chosen | Rationale |
|----------|-------------------|--------|-----------|
| {decision} | {A, B, C} | {chosen} | {why} |

## 8. ITERATION STRATEGY
- When to iterate vs accept results
- Metric thresholds that trigger iteration
- Maximum iterations per phase
- Escalation path if iterations exhaust

OUTPUT FORMAT:
Return complete product plan as structured markdown ready for ROADMAP.md
```

**STEP_2_CHECKPOINT:**
- [ ] Product owner agent launched with full context
- [ ] Agent returned structured product plan
- [ ] All 8 sections present
- [ ] Phases have measurable success criteria

---

## Step 3: VALIDATE PRODUCT PLAN

1. **Check completeness**:
   - Vision and goals are specific and measurable
   - Gap analysis references actual baseline values (not generic)
   - Phase breakdown has at least 2 phases
   - Final phase includes integration/verification
   - Quality targets progress from baseline toward SOTA

2. **Check consistency**:
   - Approach selection matches research context (not contradicting comparisons)
   - Phase dependencies are logical (no circular dependencies)
   - Effort estimates sum to reasonable total
   - Risk mitigations are specific (not generic platitudes)

3. **Check research alignment**:
   - Selected approach was recommended by comparison or has ADOPT/TRIAL verdict
   - Feasibility concerns are addressed in risk register
   - KNOWHOW.md insights are reflected in estimates

---

## Step 4: DISPLAY PRODUCT PLAN SUMMARY

```
╔══════════════════════════════════════════════════════════════╗
║  PRODUCT PLAN                                               ║
╠══════════════════════════════════════════════════════════════╣
║                                                             ║
║  Vision: {vision_short}                                     ║
║  Approach: {primary_method}                                 ║
║  Phases: {N}                                                ║
║  Total effort: {weeks} weeks                                ║
║                                                             ║
║  Phase roadmap:                                             ║
║    1. {phase_1}: {goal} ({effort}wk)                        ║
║    2. {phase_2}: {goal} ({effort}wk)                        ║
║    ...                                                      ║
║    N. Integration & Verification ({effort}wk)               ║
║                                                             ║
║  Key quality targets:                                       ║
║    {metric_1}: {baseline} -> {target}                       ║
║    {metric_2}: {baseline} -> {target}                       ║
║                                                             ║
║  Top risks:                                                 ║
║    1. {risk_1}                                              ║
║    2. {risk_2}                                              ║
║                                                             ║
║  Context gaps (may affect plan quality):                    ║
║    {missing_context_items}                                  ║
║                                                             ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 5: WRITE ROADMAP AND PROJECT FILES

1. **Write or update ROADMAP.md**:
   - Path: `.planning/ROADMAP.md`
   - Include phase table: `| # | Name | Goal | Effort | Status | Dependencies |`
   - Include quality targets section
   - Include approach and decision log
   - Include iteration strategy

2. **Update PROJECT.md** (if exists):
   - Add or update vision, goals, and non-goals
   - Reference ROADMAP.md

3. **Create phase directories**:
   ```bash
   for each phase:
     mkdir -p ${phases_dir}/{N}-{phase-name}/
   ```

---

## Step 6: COMMIT

```bash
git add .planning/ROADMAP.md
git add .planning/PROJECT.md 2>/dev/null
git add ${phases_dir}/
git commit -m "product: plan {N} phases — {primary_approach}, {total_effort}wk"
```

---

## Step 7: ROUTE NEXT ACTION

| Condition | Suggestion |
|-----------|------------|
| Baseline not assessed | `/grd:assess-baseline` — establish baseline for accurate gap analysis |
| No research context | `/grd:survey {topic}` — survey before planning |
| Plan ready, start phase 1 | `/grd:plan-phase 1` — create detailed phase plan |
| Need more research on approach | `/grd:deep-dive {paper}` or `/grd:feasibility {method}` |
| Plan needs discussion | `/grd:discuss-phase 1` — gather context for first phase |

</process>

<output>
**FILES_WRITTEN:**
- `.planning/ROADMAP.md` — product roadmap with phased plan
- `.planning/PROJECT.md` — updated with vision and goals (if exists)
- `${phases_dir}/{N}-{name}/` — phase directories created

**DISPLAY**: Product plan summary with phases, targets, risks, and context gaps

**GIT**: Committed: `product: plan {N} phases — {approach}, {effort}wk`
</output>

<error_handling>
- **No baseline available**: Proceed but mark all gap analysis as estimates, strongly suggest assess-baseline
- **No research context**: Proceed but flag that approach selection is uninformed, suggest survey
- **Existing roadmap conflict**: Present diff, ask user whether to replace or merge
- **Goals too vague**: ASK user for specific measurable criteria before proceeding
- **Phase count too high (>10)**: Suggest consolidation, warn about planning overhead
</error_handling>

<success_criteria>
- Product vision is specific and measurable
- Gap analysis uses actual baseline values (not placeholders)
- Approach selection is justified by research (or noted as uninformed)
- Phase breakdown is logical with dependencies
- Quality targets progress from baseline toward goal
- Iteration strategy is defined for each phase
- Plan is actionable — next step is always clear
</success_criteria>

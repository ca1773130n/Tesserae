---
description: Compare research methods/approaches from LANDSCAPE.md with performance/complexity matrix
argument-hint: [method1, method2, ...] or "all"
---

<purpose>
Orchestrate comparison of methods from LANDSCAPE.md. Build a structured comparison matrix
covering performance, complexity, production readiness, and trade-offs. Helps decide which
approach to pursue before committing to feasibility analysis or implementation.
</purpose>

<context>
CLAUDE.md rules: @CLAUDE.md

**Research directory structure** (paths resolved via init):
- `${research_dir}/LANDSCAPE.md` — source of methods to compare
- `${research_dir}/deep-dives/` — detailed analyses (if available)
- `${research_dir}/COMPARISON-{topic}.md` — output comparison matrix
- `${research_dir}/PAPERS.md` — paper index with verdicts
- `.planning/config.json` — GRD configuration

**This workflow does NOT spawn a dedicated agent.** It synthesizes existing research
artifacts (LANDSCAPE.md, deep-dives) into a comparison matrix. The orchestrator itself
performs the analysis using structured reasoning.
</context>

<process>

## Step 0: INITIALIZE — Load Research Context

0. **Run initialization**:
   ```bash
   INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init survey "compare-methods")
   ```
   Parse JSON for: `research_dir`, `landscape_exists`, `deep_dives`, `autonomous_mode`, `research_gates`.

1. **Check LANDSCAPE.md exists**:
   - Path: `${research_dir}/LANDSCAPE.md`
   - If missing: STOP and suggest `/grd:survey {topic}` first
   - If present: parse methods table

2. **Extract available methods** from LANDSCAPE.md:
   - Parse the `## Methods` section
   - Build list: `METHOD_NAME | paper | year | approach_type | key_metric`
   - Count total methods available

3. **Load deep-dives** (if any exist):
   - Scan `${research_dir}/deep-dives/` for completed analyses
   - Map deep-dive verdicts to method names
   - Note which methods have deep-dives and which do not

4. **Parse arguments**:
   - If specific methods listed: validate they exist in LANDSCAPE.md
   - If "all" or empty: prepare to compare all methods
   - If methods not in landscape: WARN and suggest survey first

**STEP_0_CHECKPOINT:**
- [ ] LANDSCAPE.md loaded and parsed
- [ ] Methods list extracted
- [ ] Deep-dives mapped to methods
- [ ] Comparison set determined

---

## Step 1: SELECT METHODS TO COMPARE

**If not specified in arguments, present selection to user:**

```
Available methods from LANDSCAPE.md:

  1. {method_1} ({year}) — {approach_type} [deep-dive: yes/no]
  2. {method_2} ({year}) — {approach_type} [deep-dive: yes/no]
  3. {method_3} ({year}) — {approach_type} [deep-dive: yes/no]
  ...

Options:
  a) Compare ALL methods
  b) Enter numbers to compare (e.g., "1,2,4")
  c) Compare by category (e.g., "all supervised methods")

Which methods to compare?
```

**In autonomous mode** (`autonomous_mode: true`): Compare all methods automatically.

**GATE**: If `research_gates.comparison_approval` is true, confirm selection with user.

---

## Step 2: DISPLAY BANNER

```
╔══════════════════════════════════════════════════════════════╗
║  GRD >>> COMPARE METHODS                                    ║
║                                                             ║
║  Comparing: {N} methods                                     ║
║  Methods: {method_1}, {method_2}, ...                       ║
║  Deep-dives available: {M}/{N}                              ║
║  Source: LANDSCAPE.md ({survey_date})                        ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 3: BUILD COMPARISON DIMENSIONS

For each selected method, evaluate across these axes:

### 3a. Performance Matrix

| Method | Benchmark_1 | Benchmark_2 | ... | Best_Reported |
|--------|-------------|-------------|-----|---------------|
| {m1}   | {score}     | {score}     |     | {metric}      |
| {m2}   | {score}     | {score}     |     | {metric}      |

- Source: LANDSCAPE.md metrics + deep-dive reported results
- Mark cells with `*` if from deep-dive, `~` if from survey only
- Mark `?` if metric not available

### 3b. Complexity Matrix

| Method | Training_Cost | Inference_Latency | Memory | Data_Required | Impl_Complexity |
|--------|---------------|-------------------|--------|---------------|-----------------|
| {m1}   | {est}         | {est}             | {est}  | {est}         | {1-5}           |

- Use relative scale: LOW / MEDIUM / HIGH / VERY_HIGH
- Note: estimates from papers, may not reflect production reality

### 3c. Production Readiness

| Method | Code_Available | License | Dependencies | Maintenance | Docs |
|--------|----------------|---------|--------------|-------------|------|
| {m1}   | {yes/partial/no} | {license} | {framework} | {active/stale} | {good/minimal/none} |

### 3d. Trade-off Analysis

For each pair of methods, identify key trade-offs:

```
{method_1} vs {method_2}:
  Performance: {m1} wins by {margin} on {benchmark}
  Complexity:  {m2} is simpler — {reason}
  Production:  {m1} has code, {m2} is paper-only
  Trade-off:   {summary of key decision factor}
```

### 3e. Risk Assessment

| Method | Technical_Risk | Data_Risk | Scale_Risk | Overall |
|--------|---------------|-----------|------------|---------|
| {m1}   | {L/M/H}       | {L/M/H}   | {L/M/H}    | {L/M/H} |

---

## Step 4: SYNTHESIZE RECOMMENDATION

Using extended thinking, synthesize all dimensions:

```
RECOMMENDATION MATRIX:

  Best performance:        {method} — {reason}
  Best simplicity:         {method} — {reason}
  Best production-ready:   {method} — {reason}
  Best risk profile:       {method} — {reason}

  OVERALL RECOMMENDATION:  {method}
  Rationale: {2-3 sentences explaining the recommendation}

  RUNNER-UP:               {method}
  When to prefer runner-up: {condition}
```

---

## Step 5: DISPLAY COMPARISON SUMMARY

```
╔══════════════════════════════════════════════════════════════╗
║  COMPARISON RESULTS                                         ║
╠══════════════════════════════════════════════════════════════╣
║                                                             ║
║  Methods compared: {N}                                      ║
║                                                             ║
║  WINNER: {method_name}                                      ║
║    Performance:  {score_summary}                            ║
║    Complexity:   {complexity_summary}                       ║
║    Production:   {readiness_summary}                        ║
║    Risk:         {risk_summary}                             ║
║                                                             ║
║  RUNNER-UP: {method_name}                                   ║
║    Prefer when: {condition}                                 ║
║                                                             ║
║  GAPS: {methods_without_deep_dives} methods need deep-dives ║
║                                                             ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 6: WRITE COMPARISON FILE

1. **Determine topic slug** from LANDSCAPE.md header or user topic
2. **Write comparison matrix**:
   - Path: `${research_dir}/COMPARISON-{topic-slug}.md`
   - Include all matrices from Step 3
   - Include recommendation from Step 4
   - Include metadata: date, methods compared, data sources

3. **Also display full comparison to console** for immediate review

---

## Step 7: COMMIT (optional)

**Ask user** if they want to commit the comparison file:
- In autonomous mode: commit automatically

```bash
git add ${research_dir}/COMPARISON-{topic-slug}.md
git commit -m "research: compare {N} methods for {topic} — recommend {winner}"
```

---

## Step 8: ROUTE NEXT ACTION

| Condition | Suggestion |
|-----------|------------|
| Winner lacks deep-dive | `/grd:deep-dive {winner_paper}` |
| Winner needs feasibility check | `/grd:feasibility {winner}` |
| Methods too close to call | `/grd:deep-dive` both, then re-compare |
| Gaps in deep-dives | `/grd:deep-dive {method_without_dive}` |
| Ready to proceed | `/grd:product-plan` or `/grd:plan-phase` |

</process>

<output>
**FILES_WRITTEN:**
- `${research_dir}/COMPARISON-{topic}.md` — full comparison matrix

**DISPLAY**: Comparison summary to console with winner, runner-up, and gaps

**GIT**: Optional commit: `research: compare {N} methods for {topic} — recommend {winner}`
</output>

<error_handling>
- **LANDSCAPE.md missing**: STOP, direct to `/grd:survey` first
- **No methods in landscape**: STOP, landscape may be malformed, suggest re-survey
- **Selected method not in landscape**: WARN, offer to add or survey for it
- **Insufficient data for comparison**: Mark cells as `?`, note data gaps prominently
- **All methods score equally**: Report tie, suggest deep-dives for differentiators
</error_handling>

<success_criteria>
- Comparison covers all 5 dimensions (performance, complexity, production, trade-offs, risk)
- Recommendation is specific with rationale
- Data gaps are clearly marked, not silently filled
- Comparison is traceable to LANDSCAPE.md and deep-dive sources
- Output is immediately actionable (clear winner or clear next step)
</success_criteria>

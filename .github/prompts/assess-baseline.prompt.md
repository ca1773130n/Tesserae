---
description: Assess current code/model quality with quantitative metrics and establish performance baseline
argument-hint: [scope: "full" | "quick" | specific component]
---

<purpose>
Assess current code quality and establish a quantitative performance baseline. Runs
analysis across code health, test coverage, performance metrics, and domain-specific
quality measures. Creates or updates .planning/BASELINE.md — the reference point against
which all future improvements are measured.
</purpose>

<context>
CLAUDE.md rules: @CLAUDE.md

**Project structure:**
- `.planning/BASELINE.md` — baseline assessment output (created or updated)
- `.planning/BENCHMARKS.md` — historical benchmark tracking
- `.planning/ROADMAP.md` — project roadmap (may reference baseline)
- `.planning/config.json` — GRD configuration
- `package.json` / `pyproject.toml` / `Cargo.toml` / etc. — project manifest

**Agent available:**
- `grd-baseline-assessor` — specialized code/system quality assessment agent
</context>

<process>

## Step 0: INITIALIZE — Detect Project and Scope

1. **Parse arguments**: Determine assessment scope from `[user-provided arguments]`
   - `full`: comprehensive assessment of all dimensions
   - `quick`: code health + test coverage only (skip expensive benchmarks)
   - Specific component: focus assessment on named module/directory
   - Empty: default to `full`

2. **Detect project type**:
   - Scan for manifest files (package.json, pyproject.toml, Cargo.toml, go.mod, etc.)
   - Identify language, framework, package manager
   - Identify test framework and runner
   - Identify linting/formatting tools
   - Identify build system

3. **Load existing BASELINE.md** (if present):
   - Parse previous assessment metrics
   - Store as `PREVIOUS_BASELINE` for trend comparison
   - Note timestamp of last assessment

4. **Inventory available tooling**:
   - What static analysis tools are installed/configured?
   - What test suites exist?
   - What benchmark scripts are available?
   - What CI/CD pipeline checks are defined?

**STEP_0_CHECKPOINT:**
- [ ] Assessment scope determined
- [ ] Project type and toolchain identified
- [ ] Previous baseline loaded (or noted as first assessment)
- [ ] Available tooling inventoried

---

## Step 1: DISPLAY BANNER

```
╔══════════════════════════════════════════════════════════════╗
║  GRD >>> ASSESS BASELINE                                    ║
║                                                             ║
║  Scope: {full | quick | component}                          ║
║  Project: {language} / {framework}                          ║
║  Previous baseline: {date | "first assessment"}             ║
║  Tools detected: {linter, test runner, ...}                 ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 2: SPAWN BASELINE ASSESSOR AGENT

**Launch `grd-baseline-assessor` agent via Task tool:**

Use Task tool with `subagent_type="grd:grd-baseline-assessor"`:

```
Assess current code quality and performance baseline.

PROJECT TYPE: {language} / {framework}
MANIFEST: {manifest file contents}
SCOPE: {full | quick | component: {name}}

PREVIOUS BASELINE:
{Previous BASELINE.md content, or "First assessment"}

AVAILABLE TOOLS:
{List of detected static analysis, test, and benchmark tools}

ASSESS THE FOLLOWING DIMENSIONS:

## 1. CODE HEALTH
Run available static analysis and report:
- Lint errors/warnings count (by severity)
- Type errors count (if typed language)
- Code style violations
- Cyclomatic complexity (top 10 most complex functions)
- Dead code / unused imports
- Dependency health (outdated, vulnerable, deprecated)
- Tech debt indicators (TODO/FIXME/HACK counts)

Commands to try:
- {linter} (e.g., eslint, ruff, clippy, golint)
- {type checker} (e.g., tsc --noEmit, mypy, cargo check)
- Dependency audit (npm audit, pip-audit, cargo audit)

## 2. TEST HEALTH
Run test suite and report:
- Total tests count
- Pass/fail/skip breakdown
- Test coverage percentage (line, branch, function)
- Test runtime
- Flaky test detection (if multiple runs feasible)
- Test-to-code ratio

Commands to try:
- {test runner} with coverage flag
- Coverage report generation

## 3. BUILD HEALTH
- Build success/failure
- Build time
- Bundle size (if applicable)
- Artifact count and sizes
- Build warnings

## 4. ARCHITECTURE METRICS
- Total source files count
- Total lines of code (excluding tests, generated)
- Module/package count
- Dependency count (direct and transitive)
- Circular dependency detection
- API surface area (exported functions/classes)

## 5. PERFORMANCE METRICS (if applicable and scope=full)
- Startup time / cold start
- Key operation latency (if benchmarks exist)
- Memory usage baseline
- Resource utilization patterns
- Known performance bottlenecks

## 6. DOMAIN-SPECIFIC METRICS (if applicable)
- Model accuracy/quality metrics (if ML project)
- API response times (if web service)
- UI render performance (if frontend)
- Throughput measurements
- Custom benchmark results

OUTPUT FORMAT:
Return structured assessment with:
- Summary scorecard (dimension: score/grade)
- Detailed metrics per dimension (tables)
- Trend comparison vs previous baseline (if available)
- Top 5 improvement opportunities ranked by impact
- Risk areas requiring attention
```

**STEP_2_CHECKPOINT:**
- [ ] Baseline assessor agent launched
- [ ] All applicable dimensions assessed
- [ ] Quantitative metrics captured
- [ ] Comparison with previous baseline computed (if available)

---

## Step 3: DISPLAY BASELINE SCORECARD

```
╔══════════════════════════════════════════════════════════════╗
║  BASELINE ASSESSMENT                                        ║
╠══════════════════════════════════════════════════════════════╣
║                                                             ║
║  Code Health:       {score}/10  {trend_arrow}               ║
║    Lint issues:     {count} ({delta})                       ║
║    Type errors:     {count} ({delta})                       ║
║    Tech debt:       {count} TODOs/FIXMEs                    ║
║                                                             ║
║  Test Health:       {score}/10  {trend_arrow}               ║
║    Tests:           {pass}/{total} passing                  ║
║    Coverage:        {pct}% ({delta})                        ║
║    Runtime:         {time}                                  ║
║                                                             ║
║  Build Health:      {score}/10  {trend_arrow}               ║
║    Status:          {success/failure}                       ║
║    Time:            {time}                                  ║
║    Warnings:        {count}                                 ║
║                                                             ║
║  Architecture:      {score}/10  {trend_arrow}               ║
║    Source files:    {count}                                  ║
║    LOC:            {count}                                   ║
║    Dependencies:   {count}                                   ║
║                                                             ║
║  OVERALL:          {score}/10  {trend_arrow}                ║
║                                                             ║
║  Top improvement opportunities:                             ║
║    1. {opportunity_1}                                       ║
║    2. {opportunity_2}                                       ║
║    3. {opportunity_3}                                       ║
║                                                             ║
╚══════════════════════════════════════════════════════════════╝
```

Trend arrows: up-arrow for improvement, down-arrow for regression, dash for stable.

---

## Step 4: WRITE BASELINE.md

1. **Write baseline assessment**:
   - Path: `.planning/BASELINE.md`
   - Include timestamp and scope
   - Include YAML frontmatter:
     ```yaml
     ---
     assessed: {YYYY-MM-DD}
     scope: {full|quick|component}
     overall_score: {N}/10
     project_type: {language/framework}
     ---
     ```
   - Include all dimension details with metric tables
   - Include improvement opportunities
   - Include risk areas

2. **Append to BENCHMARKS.md** (if exists):
   - Add baseline data point for historical tracking
   - Format: `| baseline | {date} | {metric} | {value} |`

3. **If previous baseline exists**:
   - Include `## Change Log` section showing delta from previous
   - Highlight regressions in bold

---

## Step 5: COMMIT

```bash
git add .planning/BASELINE.md
git add .planning/BENCHMARKS.md 2>/dev/null
git commit -m "baseline: assess {scope} — overall {score}/10"
```

---

## Step 6: ROUTE NEXT ACTION

| Condition | Suggestion |
|-----------|------------|
| First baseline, project starting | `/grd:product-plan` — plan with baseline context |
| Baseline established, research done | `/grd:eval-plan` — design evaluation with baseline refs |
| Regressions found vs previous | Address regressions before new work |
| Poor code health | Consider code health phase before research work |
| Ready for next phase | `/grd:plan-phase` with baseline awareness |

</process>

<output>
**FILES_WRITTEN:**
- `.planning/BASELINE.md` — comprehensive baseline assessment
- `.planning/BENCHMARKS.md` — updated with baseline data point (if exists)

**DISPLAY**: Baseline scorecard with per-dimension scores and improvement opportunities

**GIT**: Committed: `baseline: assess {scope} — overall {score}/10`
</output>

<error_handling>
- **No test suite found**: Report 0 coverage, 0 tests — note this as critical gap
- **Lint tool not installed**: Skip dimension, note as unavailable
- **Build fails**: Record failure as baseline data point, proceed with other dimensions
- **Previous BASELINE.md parse error**: Back up, create fresh baseline
- **Large project timeout**: Reduce scope to `quick`, note incomplete assessment
- **No manifest file**: Try to detect project type from file extensions, ask user if unclear
</error_handling>

<success_criteria>
- Quantitative metrics captured for all applicable dimensions
- Scores are reproducible (same assessment on same code yields same results)
- Baseline serves as reference for future eval-plan and eval-report
- Improvement opportunities are specific and actionable
- BASELINE.md is self-contained (explains methodology and measurements)
</success_criteria>

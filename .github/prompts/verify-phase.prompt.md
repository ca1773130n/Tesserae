---
description: Verify phase goal achievement through tiered evaluation
argument-hint: <phase number>
---

<purpose>
Verify phase goal achievement through tiered evaluation and goal-backward analysis. Uses EVAL.md for verification plan when available, reports quantitative results (not just pass/fail), and tracks deferred validations for integration phases.

Executed by a verification subagent spawned from execute-phase.md.
</purpose>

<core_principle>
**Task completion != Goal achievement**

A task "create chat component" can be marked complete when the component is a placeholder. The task was done — but the goal "working chat interface" was not achieved.

**Tiered verification:**
1. Tier 1 (Sanity): Quick automated checks — types, lint, tests pass
2. Tier 2 (Proxy): Automated metrics — coverage, benchmarks, quality scores
3. Tier 3 (Deferred): Human/integration validations — user testing, real-world performance

Goal-backward verification:
1. What must be TRUE for the goal to be achieved?
2. What must EXIST for those truths to hold?
3. What must be WIRED for those artifacts to function?

Then verify each level against the actual codebase.
</core_principle>

<required_reading>
@${CLAUDE_PLUGIN_ROOT}/references/verification-patterns.md
@${CLAUDE_PLUGIN_ROOT}/templates/verification-report.md
</required_reading>

<process>

<step name="load_context" priority="first">
Load phase operation context:

```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init phase-op "${PHASE_ARG}")
```

Extract from init JSON: `phase_dir`, `phase_number`, `phase_name`, `has_plans`, `plan_count`.

Then load phase details and list plans/summaries:
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js roadmap get-phase "${phase_number}"
grep -E "^| ${phase_number}" .planning/REQUIREMENTS.md 2>/dev/null
ls "$phase_dir"/*-SUMMARY.md "$phase_dir"/*-PLAN.md "$phase_dir"/*-EVAL.md 2>/dev/null
```

Extract **phase goal** from ROADMAP.md (the outcome to verify, not tasks) and **requirements** from REQUIREMENTS.md if it exists.
</step>

<step name="load_eval_plan">
**Check for EVAL.md (tiered verification plan):**

```bash
EVAL_FILE=$(ls "$PHASE_DIR"/*-EVAL.md 2>/dev/null | head -1)
```

**If EVAL.md exists:** Read and parse tiered verification plan:
- Tier 1 metrics and thresholds
- Tier 2 metrics and thresholds
- Tier 3 deferred validations

Use EVAL.md to guide verification — run Tier 1 and Tier 2 checks as specified.

**If no EVAL.md:** Fall back to goal-backward verification (establish must-haves from plan frontmatter or phase goal).
</step>

<step name="establish_must_haves">
**Option A: Must-haves in PLAN frontmatter**

Use grd-tools to extract must_haves from each PLAN:

```bash
for plan in "$PHASE_DIR"/*-PLAN.md; do
  MUST_HAVES=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js frontmatter get "$plan" --field must_haves)
  echo "=== $plan ===" && echo "$MUST_HAVES"
done
```

Returns JSON: `{ truths: [...], artifacts: [...], key_links: [...] }`

Aggregate all must_haves across plans for phase-level verification.

**Option B: Derive from phase goal**

If no must_haves in frontmatter (MUST_HAVES returns error or empty):
1. State the goal from ROADMAP.md
2. Derive **truths** (3-7 observable behaviors, each testable)
3. Derive **artifacts** (concrete file paths for each truth)
4. Derive **key links** (critical wiring where stubs hide)
5. Document derived must-haves before proceeding
</step>

<step name="run_tiered_verification">
**If EVAL.md exists, run tiered checks:**

**Tier 1 (Sanity) — run immediately:**
For each Tier 1 check in EVAL.md:
- Execute the specified command/check
- Record: metric name, expected threshold, actual value, pass/fail

**Tier 2 (Proxy) — run after Tier 1 passes:**
For each Tier 2 check in EVAL.md:
- Execute the specified measurement
- Record: metric name, target, actual value, delta from target, pass/fail

**Tier 3 (Deferred) — collect for tracking:**
For each Tier 3 item in EVAL.md:
- Record in STATE.md under "### Deferred Validations"
- Format: `- Phase {X}: {validation description} — PENDING`
- These are verified at integration phases or milestone audit

**Report quantitative results:**
```
## Tiered Evaluation Results

### Tier 1: Sanity
| Check | Expected | Actual | Status |
|-------|----------|--------|--------|
| Types pass | 0 errors | 0 errors | PASS |
| Tests pass | 100% | 98% | FAIL |

### Tier 2: Proxy Metrics
| Metric | Target | Actual | Delta | Status |
|--------|--------|--------|-------|--------|
| Coverage | >80% | 85% | +5% | PASS |
| Benchmark | >0.75 | 0.72 | -0.03 | BELOW |

### Tier 3: Deferred
| Validation | Status |
|-----------|--------|
| User testing | DEFERRED to Phase {X+1} |
| Load testing | DEFERRED to milestone audit |
```
</step>

<step name="verify_truths">
For each observable truth, determine if the codebase enables it.

**Status:** VERIFIED (all supporting artifacts pass) | FAILED (artifact missing/stub/unwired) | ? UNCERTAIN (needs human)

For each truth: identify supporting artifacts -> check artifact status -> check wiring -> determine truth status.
</step>

<step name="verify_artifacts">
Use grd-tools for artifact verification against must_haves in each PLAN:

```bash
for plan in "$PHASE_DIR"/*-PLAN.md; do
  ARTIFACT_RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js verify artifacts "$plan")
  echo "=== $plan ===" && echo "$ARTIFACT_RESULT"
done
```

Parse JSON result: `{ all_passed, passed, total, artifacts: [{path, exists, issues, passed}] }`

**Artifact status from result:**
- `exists=false` -> MISSING
- `issues` not empty -> STUB (check issues for "Only N lines" or "Missing pattern")
- `passed=true` -> VERIFIED (Levels 1-2 pass)

**Level 3 — Wired (manual check for artifacts that pass Levels 1-2):**
```bash
grep -r "import.*$artifact_name" src/ --include="*.ts" --include="*.tsx"  # IMPORTED
grep -r "$artifact_name" src/ --include="*.ts" --include="*.tsx" | grep -v "import"  # USED
```
WIRED = imported AND used. ORPHANED = exists but not imported/used.

| Exists | Substantive | Wired | Status |
|--------|-------------|-------|--------|
| Y | Y | Y | VERIFIED |
| Y | Y | N | ORPHANED |
| Y | N | - | STUB |
| N | - | - | MISSING |
</step>

<step name="verify_wiring">
Use grd-tools for key link verification against must_haves in each PLAN:

```bash
for plan in "$PHASE_DIR"/*-PLAN.md; do
  LINKS_RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js verify key-links "$plan")
  echo "=== $plan ===" && echo "$LINKS_RESULT"
done
```

Parse JSON result: `{ all_verified, verified, total, links: [{from, to, via, verified, detail}] }`
</step>

<step name="verify_requirements">
If REQUIREMENTS.md exists:
```bash
grep -E "Phase ${PHASE_NUM}" .planning/REQUIREMENTS.md 2>/dev/null
```

For each requirement: parse description -> identify supporting truths/artifacts -> status: SATISFIED / BLOCKED / ? NEEDS HUMAN.
</step>

<step name="collect_deferred_validations">
**At integration phases, collect and verify deferred validations from prior phases:**

```bash
grep "DEFERRED" .planning/STATE.md 2>/dev/null | grep -v "^#"
```

For each deferred validation:
- If this is the integration phase specified -> attempt verification now
- Update STATE.md: change PENDING -> VERIFIED or FAILED
- Report results in verification report

**Track deferred validations in STATE.md:**

```markdown
### Deferred Validations

| Phase | Validation | Target Phase | Status |
|-------|-----------|--------------|--------|
| 2 | User can see real-time updates | 4 (integration) | PENDING |
| 3 | API handles 100 req/s | milestone audit | PENDING |
```
</step>

<step name="scan_antipatterns">
Extract files modified in this phase from SUMMARY.md, scan each:

| Pattern | Search | Severity |
|---------|--------|----------|
| TODO/FIXME/XXX/HACK | `grep -n -E "TODO\|FIXME\|XXX\|HACK"` | Warning |
| Placeholder content | `grep -n -iE "placeholder\|coming soon\|will be here"` | Blocker |
| Empty returns | `grep -n -E "return null\|return \{\}\|return \[\]\|=> \{\}"` | Warning |
| Log-only functions | Functions containing only console.log | Warning |

Categorize: Blocker (prevents goal) | Warning (incomplete) | Info (notable).
</step>

<step name="identify_human_verification">
**Always needs human:** Visual appearance, user flow completion, real-time behavior (WebSocket/SSE), external service integration, performance feel, error message clarity.

**Needs human if uncertain:** Complex wiring grep can't trace, dynamic state-dependent behavior, edge cases.

Format each as: Test Name -> What to do -> Expected result -> Why can't verify programmatically.
</step>

<step name="determine_status">
**passed:** All truths VERIFIED, all artifacts pass levels 1-3, all key links WIRED, no blocker anti-patterns, all Tier 1-2 metrics meet targets.

**gaps_found:** Any truth FAILED, artifact MISSING/STUB, key link NOT_WIRED, or blocker found, or Tier 1-2 metrics below target.

**human_needed:** All automated checks pass but human verification items remain.

**Score:** `verified_truths / total_truths`

**Quantitative summary:** Include actual metric values, not just pass/fail.
</step>

<step name="generate_fix_plans">
If gaps_found:

1. **Cluster related gaps:** API stub + component unwired -> "Wire frontend to backend". Multiple missing -> "Complete core implementation". Wiring only -> "Connect existing components".

2. **Generate plan per cluster:** Objective, 2-3 tasks (files/action/verify each), re-verify step. Keep focused: single concern per plan.

3. **Order by dependency:** Fix missing -> fix stubs -> fix wiring -> verify.
</step>

<step name="create_report">
```bash
REPORT_PATH="$PHASE_DIR/${PHASE_NUM}-VERIFICATION.md"
```

Fill template sections: frontmatter (phase/timestamp/status/score), goal achievement, tiered eval results (if EVAL.md existed), artifact table, wiring table, requirements coverage, anti-patterns, deferred validations, human verification, gaps summary, fix plans (if gaps_found), metadata.

**Include quantitative results prominently:**
```markdown
## Quantitative Results

| Category | Metric | Target | Actual | Status |
|----------|--------|--------|--------|--------|
| Tier 1 | Type errors | 0 | 0 | PASS |
| Tier 2 | Test coverage | >80% | 85% | PASS |
| Tier 2 | Benchmark score | >0.75 | 0.72 | BELOW |
| Goal | Must-haves | 5/5 | 4/5 | GAPS |
```

See ${CLAUDE_PLUGIN_ROOT}/templates/verification-report.md for complete template.
</step>

<step name="tracker_comment">
**Post verification results to tracker (non-blocking):**

**For GitHub:**
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker add-comment "${PHASE_NUM}" "${REPORT_PATH}" 2>/dev/null || true
```

**For mcp-atlassian** (see @${CLAUDE_PLUGIN_ROOT}/references/mcp-tracker-protocol.md):
```bash
COMMENT_INFO=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker add-comment "${PHASE_NUM}" "${REPORT_PATH}" --raw 2>/dev/null || true)
```
If response has `provider: "mcp-atlassian"`, call MCP tool `add_comment` with `issue_key` and `content` from response.
</step>

<step name="return_to_orchestrator">
Return status (`passed` | `gaps_found` | `human_needed`), score (N/M must-haves), report path, quantitative metrics summary.

If gaps_found: list gaps + recommended fix plan names + eval metrics that missed targets.
If human_needed: list items requiring human testing.

Orchestrator routes: `passed` -> update_roadmap | `gaps_found` -> create/execute fixes, re-verify (or /grd:iterate if eval targets missed) | `human_needed` -> present to user.
</step>

</process>

<success_criteria>
- [ ] EVAL.md loaded and tiered checks executed (if exists)
- [ ] Must-haves established (from frontmatter or derived)
- [ ] All truths verified with status and evidence
- [ ] All artifacts checked at all three levels
- [ ] All key links verified
- [ ] Tiered evaluation results recorded with quantitative values
- [ ] Deferred validations tracked in STATE.md
- [ ] At integration phases: prior deferred validations collected and verified
- [ ] Requirements coverage assessed (if applicable)
- [ ] Anti-patterns scanned and categorized
- [ ] Human verification items identified
- [ ] Overall status determined with quantitative summary
- [ ] Fix plans generated (if gaps_found)
- [ ] VERIFICATION.md created with complete report including metrics
- [ ] Results returned to orchestrator
</success_criteria>

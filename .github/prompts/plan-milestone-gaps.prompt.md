---
description: Create phases to close gaps identified by milestone audit
---

<purpose>
Create all phases necessary to close gaps identified by `/grd:audit-milestone`. Reads MILESTONE-AUDIT.md, groups gaps into logical phases, creates phase entries in ROADMAP.md, and offers to plan each phase. One command creates all fix phases — no manual `/grd:add-phase` per gap.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>

## 0. Load Context

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init plan-milestone-gaps
```

Use the returned JSON for milestone info, audit file location, gap counts, phase inventory, and file existence checks. Skip manual discovery steps that the init already covers.

## 1. Load Audit Results

```bash
# Find the most recent audit file
ls -t .planning/v*-MILESTONE-AUDIT.md 2>/dev/null | head -1
```

Parse YAML frontmatter to extract structured gaps:
- `gaps.requirements` — unsatisfied requirements
- `gaps.integration` — missing cross-phase connections
- `gaps.flows` — broken E2E flows
- `gaps.metrics` — quantitative targets not met (GRD-specific)
- `gaps.deferred` — deferred validations not resolved by milestone boundary (GRD-specific)

If no audit file exists or has no gaps, error:
```
No audit gaps found. Run `/grd:audit-milestone` first.
```

## 2. Prioritize Gaps

Group gaps by priority from REQUIREMENTS.md and evaluation results:

| Priority | Action |
|----------|--------|
| `must` | Create phase, blocks milestone |
| `should` | Create phase, recommended |
| `nice` | Ask user: include or defer? |
| `metric_miss` | Create iteration phase — primary metric below target (GRD-specific) |
| `deferred_fail` | Create validation phase — deferred validation unresolved (GRD-specific) |

For integration/flow gaps, infer priority from affected requirements.

**Metric gap priority inference:**
- Primary metric below target by > tolerance: `metric_miss` (must-level)
- Primary metric below target by < tolerance: `metric_miss` (should-level)
- Secondary metric miss only: `metric_miss` (nice-level, document in KNOWHOW.md)
- Regression from baseline: `metric_miss` (must-level, immediate action)

**Deferred validation priority inference:**
- Deferred from earlier than 2 phases ago: `deferred_fail` (must-level)
- Deferred with target "resolve by this milestone": `deferred_fail` (must-level)
- Deferred with no target resolution date: `deferred_fail` (should-level)

## 3. Group Gaps into Phases

Cluster related gaps into logical phases:

**Grouping rules:**
- Same affected phase → combine into one fix phase
- Same subsystem (auth, API, UI) → combine
- Dependency order (fix stubs before wiring)
- Keep phases focused: 2-4 tasks each
- Metric gaps → group into `iteration` type phases (GRD-specific)
- Deferred validation gaps → group into `validation` type phases (GRD-specific)

**Example grouping:**
```
Gap: DASH-01 unsatisfied (Dashboard doesn't fetch)
Gap: Integration Phase 1→3 (Auth not passed to API calls)
Gap: Flow "View dashboard" broken at data fetch

→ Phase 6: "Wire Dashboard to API"
  - Add fetch to Dashboard.tsx
  - Include auth header in fetch
  - Handle response, update state
  - Render user data
```

## 4. Determine Phase Numbers

Find highest existing phase:
```bash
# Get sorted phase list, extract last one
PHASES=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js phases list)
HIGHEST=$(echo "$PHASES" | jq -r '.directories[-1]')
```

New phases continue from there:
- If Phase 5 is highest, gaps become Phase 6, 7, 8...

## 5. Present Gap Closure Plan

```markdown
## Gap Closure Plan

**Milestone:** {version}
**Gaps to close:** {N} requirements, {M} integration, {K} flows, {L} metrics, {D} deferred

### Proposed Phases

**Phase {N}: {Name}**
Closes:
- {REQ-ID}: {description}
- Integration: {from} → {to}
Tasks: {count}

**Phase {N+1}: {Name}**
Closes:
- {REQ-ID}: {description}
- Flow: {flow name}
Tasks: {count}

{If nice-to-have gaps exist:}

### Deferred (nice-to-have)

These gaps are optional. Include them?
- {gap description}
- {gap description}

---

Create these {X} phases? (yes / adjust / defer all optional)
```

Wait for user confirmation.

## 6. Update ROADMAP.md

Add new phases to current milestone:

```markdown
### Phase {N}: {Name}
**Goal:** {derived from gaps being closed}
**Type:** {fix | iteration | validation}
**Requirements:** {REQ-IDs being satisfied}
**Gap Closure:** Closes gaps from audit
```

For iteration-type phases (metric gaps), also include eval_metrics:

```markdown
### Phase {N}: {Name}
**Goal:** Improve {metric} from {current} to {target}
**Type:** iteration
**Requirements:** {REQ-IDs being satisfied}
**Gap Closure:** Closes metric gap from audit
**eval_metrics:**
  - metric: {metric_name}
    baseline: {current_value}
    target: {target_value}
    tolerance: {acceptable_margin}
**Verification Level:** proxy (Tier 1 + Tier 2)
```

For validation-type phases (deferred gaps), include deferred resolution:

```markdown
### Phase {N}: {Name}
**Goal:** Resolve deferred validations {DV-IDs}
**Type:** validation
**Gap Closure:** Resolves deferred validations from STATE.md
**Deferred Items:** {DV-ID list}
**Verification Level:** full (all tiers as needed)
```

## 7. Create Phase Directories

```bash
mkdir -p "${phases_dir}/{NN}-{name}"
```

## 8. Commit Roadmap Update

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs(roadmap): add gap closure phases {N}-{M}" --files .planning/ROADMAP.md
```

## 9. Offer Next Steps

```markdown
## Gap Closure Phases Created

**Phases added:** {N} - {M}
**Gaps addressed:** {count} requirements, {count} integration, {count} flows, {count} metrics, {count} deferred

---

## Next Up

**Plan first gap closure phase**

`/grd:plan-phase {N}`

<sub>`/clear` first -> fresh context window</sub>

---

**Also available:**
- `/grd:execute-phase {N}` — if plans already exist
- `cat .planning/ROADMAP.md` — see updated roadmap

---

**After all gap phases complete:**

`/grd:audit-milestone` — re-audit to verify gaps closed
`/grd:complete-milestone {version}` — archive when audit passes
```

</process>

<gap_to_phase_mapping>

## How Gaps Become Tasks

**Requirement gap → Tasks:**
```yaml
gap:
  id: DASH-01
  description: "User sees their data"
  reason: "Dashboard exists but doesn't fetch from API"
  missing:
    - "useEffect with fetch to /api/user/data"
    - "State for user data"
    - "Render user data in JSX"

becomes:

phase: "Wire Dashboard Data"
tasks:
  - name: "Add data fetching"
    files: [src/components/Dashboard.tsx]
    action: "Add useEffect that fetches /api/user/data on mount"

  - name: "Add state management"
    files: [src/components/Dashboard.tsx]
    action: "Add useState for userData, loading, error states"

  - name: "Render user data"
    files: [src/components/Dashboard.tsx]
    action: "Replace placeholder with userData.map rendering"
```

**Integration gap → Tasks:**
```yaml
gap:
  from_phase: 1
  to_phase: 3
  connection: "Auth token → API calls"
  reason: "Dashboard API calls don't include auth header"
  missing:
    - "Auth header in fetch calls"
    - "Token refresh on 401"

becomes:

phase: "Add Auth to Dashboard API Calls"
tasks:
  - name: "Add auth header to fetches"
    files: [src/components/Dashboard.tsx, src/lib/api.ts]
    action: "Include Authorization header with token in all API calls"

  - name: "Handle 401 responses"
    files: [src/lib/api.ts]
    action: "Add interceptor to refresh token or redirect to login on 401"
```

**Flow gap → Tasks:**
```yaml
gap:
  name: "User views dashboard after login"
  broken_at: "Dashboard data load"
  reason: "No fetch call"
  missing:
    - "Fetch user data on mount"
    - "Display loading state"
    - "Render user data"

becomes:

# Usually same phase as requirement/integration gap
# Flow gaps often overlap with other gap types
```

**Metric gap → Iteration Phase (GRD-specific):**
```yaml
gap:
  metric: "PSNR"
  current: 29.1
  target: 30.0
  baseline: 28.5
  delta_needed: +0.9
  source_phase: 3
  approach: "SwinIR with L1 loss"

becomes:

phase: "Iterate SwinIR — Improve PSNR"
type: iteration
eval_metrics:
  - metric: PSNR
    baseline: 29.1
    target: 30.0
    tolerance: 0.2
tasks:
  - name: "Analyze metric gap"
    action: "Review eval results, consult KNOWHOW.md for known fixes"
    verify: "Gap analysis documented"

  - name: "Apply targeted improvement"
    action: "Add perceptual loss / adjust learning rate / increase depth"
    verify: "Tier 1: model trains without error"

  - name: "Run proxy evaluation"
    action: "Evaluate on Set5, compare against baseline 29.1"
    verify: "Tier 2: PSNR improved toward 30.0 target"
```

**Deferred validation gap → Validation Phase (GRD-specific):**
```yaml
gap:
  id: DV-01
  what: "Full eval on Urban100 dataset"
  deferred_from: "Phase 3"
  reason: "Dataset not yet downloaded"
  resolve_by: "Phase 5"

becomes:

phase: "Resolve Deferred Validations"
type: validation
tasks:
  - name: "Setup Urban100 dataset"
    action: "Download and prepare Urban100 evaluation dataset"
    verify: "Dataset files present and loadable"

  - name: "Run full evaluation on Urban100"
    action: "Execute eval.py --dataset Urban100 --metrics all"
    verify: "Tier 3: metrics computed, results recorded in BENCHMARKS.md"

  - name: "Update STATE.md"
    action: "Mark DV-01 as resolved, record results"
    verify: "No remaining deferred validations for this milestone"
```

</gap_to_phase_mapping>

<success_criteria>
- [ ] MILESTONE-AUDIT.md loaded and gaps parsed (including metric and deferred gaps)
- [ ] Gaps prioritized (must/should/nice/metric_miss/deferred_fail)
- [ ] Gaps grouped into logical phases (fix/iteration/validation types)
- [ ] Metric gaps include eval_metrics with baseline, target, and tolerance
- [ ] Deferred gaps reference DV-IDs from STATE.md
- [ ] User confirmed phase plan
- [ ] ROADMAP.md updated with new phases (including type and verification_level)
- [ ] Phase directories created
- [ ] Changes committed
- [ ] User knows to run `/grd:plan-phase` next
</success_criteria>

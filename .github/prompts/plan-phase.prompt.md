---
description: Create executable phase plans with research, verification, and eval planning. Use --research-only or --eval-only for focused modes.
argument-hint: <phase number> [--research-only | --eval-only]
---

<purpose>
Create executable phase prompts (PLAN.md files) for a roadmap phase with integrated research, verification, and eval planning. Default flow: Research (if needed) -> Plan -> Verify -> Eval Plan -> Done. Orchestrates grd-phase-researcher, grd-planner, grd-plan-checker, and grd-eval-planner agents with a revision loop (max 3 iterations). Loads research landscape context before planning.

Supports focused modes via flags:
- `--research-only` — Run only the researcher agent (replaces standalone `/grd:research-phase`)
- `--eval-only` — Run only the eval planner agent (replaces standalone `/grd:eval-plan`)
</purpose>

<modes>

## Focused Modes

### `--research-only`

Run only the phase researcher agent. Skips planner, plan-checker, and eval-planner entirely.
Produces `{NN}-RESEARCH.md` in the phase directory. Equivalent to the former `/grd:research-phase` command.

After completion, offers: Plan phase / Dig deeper / Review research / Done.

### `--eval-only`

Run only the eval planner agent. Skips researcher, planner, and plan-checker entirely.
Produces `{NN}-EVAL.md` in the phase directory. Equivalent to the former `/grd:eval-plan` command.
Requires that PLAN.md files already exist for the phase (so the eval planner has context).

After completion, offers: Execute phase / Review eval plan / Done.

### Default (no flag)

Full orchestrated flow: Research -> Plan -> Verify -> Eval Plan -> Done.

</modes>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.

@${CLAUDE_PLUGIN_ROOT}/references/ui-brand.md
</required_reading>

<process>

## 1. Initialize

Load all context in one call (include file contents to avoid redundant reads):

```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init plan-phase "$PHASE" --include state,roadmap,requirements,context,research,verification,uat)
```

Parse JSON for: `researcher_model`, `planner_model`, `checker_model`, `research_enabled`, `plan_checker_enabled`, `commit_docs`, `phase_found`, `phase_dir`, `phase_number`, `phase_name`, `phase_slug`, `padded_phase`, `has_research`, `has_context`, `has_plans`, `plan_count`, `planning_exists`, `roadmap_exists`, `autonomous_mode`, `research_dir`, `phases_dir`, `codebase_dir`.

**File contents (from --include):** `state_content`, `roadmap_content`, `requirements_content`, `context_content`, `research_content`, `verification_content`, `uat_content`. These are null if files don't exist.

**If `planning_exists` is false:** Error — run `/grd:init` first.

## 1.5. Load Research Landscape Context

**Before any planning, load research context from `${research_dir}/`:**

```bash
LANDSCAPE=$(cat ${research_dir}/LANDSCAPE.md 2>/dev/null)
KNOWHOW=$(cat ${research_dir}/KNOWHOW.md 2>/dev/null)
BENCHMARKS=$(cat ${research_dir}/BENCHMARKS.md 2>/dev/null)
```

Also check for relevant deep-dive files:
```bash
ls ${research_dir}/deep-dives/*.md 2>/dev/null
```

Store as `research_landscape_context` — this will be passed to the planner agent.

## 2. Parse and Normalize Arguments

Extract from [user-provided arguments]: phase number (integer or decimal like `2.1`), flags (`--research`, `--skip-research`, `--gaps`, `--skip-verify`, `--research-only`, `--eval-only`, `--candidates N`).

**v0.4 multi-candidate mode (`--candidates N`, N > 1):** When the caller passes `--candidates N` with N > 1, the planner agent MUST emit N alternative plans for the phase. Each alternative goes between marker fences and is captured by `gd plan-candidates <phase> --candidates N`. See the `<multi_candidate>` block below — it activates ONLY when N > 1 and is otherwise silent (N === 1 is the v0.3.x default and keeps writing a single bare `PLAN.md` via the Write tool).

<multi_candidate>
**Active only when `--candidates N` is set with N > 1.**

You are producing {N} ALTERNATIVE plans for this phase. They must:

- Differ in **approach**, not just in wording. Choose meaningfully distinct strategies — e.g. one might emphasize adding new modules, another refactoring existing modules, another delegating to existing helpers.
- All satisfy the same `must_haves` (REQUIREMENTS.md).
- Each include their own `<reflection>` block; the `hypothesis` MUST differ across candidates (this is what `gd plan-lint` and Phase 3's deterministic selector will compare).
- Be emitted as marker-fenced text blocks, NOT written to disk via the Write tool. Do NOT call Write for `PLAN-i.md` files — the orchestrator runs `gd plan-candidates <phase> --candidates {N}` against your stdout and writes the files itself, with fail-closed count validation.

**Output format (REQUIRED — no other content between markers):**

```
<<<PLAN-1>>>
<full PLAN.md content for candidate 1: YAML frontmatter + body + tasks + reflection>
<<</PLAN-1>>>
<<<PLAN-2>>>
<full PLAN.md content for candidate 2>
<<</PLAN-2>>>
... (repeat for each candidate up to N) ...
```

Do NOT nest markers. Do NOT skip indices. Do NOT emit a bare `PLAN.md`. The orchestrator will fail closed (no files written, non-zero exit) if the count is wrong, the indices don't cover 1..N exactly, or any block is malformed. Use `--allow-partial-candidates` only on retry after explicit confirmation.
</multi_candidate>

**Focused mode routing:**
- If `--research-only`: After initialization (steps 1-4), jump to step 5 (Handle Research, forced). After researcher returns, skip to step 14 (Present Final Status) with research-only summary. Do NOT run planner, checker, or eval-planner.
- If `--eval-only`: After initialization (steps 1-4), skip research/planner/checker and jump directly to step 13 (Eval Planning Step). After eval-planner returns, skip to step 14 (Present Final Status) with eval-only summary. Requires PLAN.md files to exist (error if missing).

**If no phase number:** Detect next unplanned phase from roadmap.

**If `phase_found` is false:** Validate phase exists in ROADMAP.md. If valid, create the directory using `phase_slug` and `padded_phase` from init:
```bash
mkdir -p "${phases_dir}/${padded_phase}-${phase_slug}"
```

**Existing artifacts from init:** `has_research`, `has_plans`, `plan_count`.

## 3. Validate Phase

```bash
PHASE_INFO=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js roadmap get-phase "${PHASE}")
```

**If `found` is false:** Error with available phases. **If `found` is true:** Extract `phase_number`, `phase_name`, `goal` from JSON.

## 4. Load CONTEXT.md

Use `context_content` from init JSON (already loaded via `--include context`).

**CRITICAL:** Use `context_content` from INIT — pass to researcher, planner, checker, and revision agents.

If `context_content` is not null, display: `Using phase context from: ${PHASE_DIR}/*-CONTEXT.md`

## 5. Handle Research

**Skip if:** `--gaps` flag, `--skip-research` flag, or `research_enabled` is false (from init) without `--research` override.

**If `has_research` is true (from init) AND no `--research` flag:** Use existing, skip to step 6.

**If RESEARCH.md missing OR `--research` flag:**

Display banner:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► RESEARCHING PHASE {X}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

* Spawning researcher...
```

### Spawn grd-phase-researcher

```bash
PHASE_DESC=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js roadmap get-phase "${PHASE}" | jq -r '.section')
REQUIREMENTS=$(echo "$INIT" | jq -r '.requirements_content // empty' | grep -A100 "## Requirements" | head -50)
STATE_SNAP=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js state-snapshot)
```

Research prompt:

```markdown
PATHS:
research_dir: ${research_dir}
phases_dir: ${phases_dir}
phase_dir: ${phase_dir}
codebase_dir: ${codebase_dir}

<objective>
Research how to implement Phase {phase_number}: {phase_name}
Answer: "What do I need to know to PLAN this phase well?"
</objective>

<phase_context>
IMPORTANT: If CONTEXT.md exists below, it contains user decisions from /grd:discuss-phase.
- **Decisions** = Locked — research THESE deeply, no alternatives
- **Claude's Discretion** = Freedom areas — research options, recommend
- **Deferred Ideas** = Out of scope — ignore

{context_content}
</phase_context>

<research_landscape>
{research_landscape_context}
</research_landscape>

<additional_context>
**Phase description:** {phase_description}
**Requirements:** {requirements}
**Prior decisions:** {decisions}
</additional_context>

<output>
Write to: {phase_dir}/{phase}-RESEARCH.md
</output>
```

```
Task(
  prompt="First, read ${CLAUDE_PLUGIN_ROOT}/agents/grd-phase-researcher.md for your role and instructions.\n\n" + research_prompt,
  subagent_type="general-purpose",
  model="{researcher_model}",
  description="Research Phase {phase}"
)
```

### Handle Researcher Return

- **`## RESEARCH COMPLETE`:** Display confirmation. **If `--research-only`:** skip to step 14 with research-only summary (offer: Plan phase / Dig deeper / Review / Done). **Otherwise:** continue to step 6.
- **`## RESEARCH BLOCKED`:** Display blocker, offer: 1) Provide context, 2) Skip research, 3) Abort

## 6. Check Existing Plans

```bash
ls "${PHASE_DIR}"/*-PLAN.md 2>/dev/null
```

**If exists:** Offer: 1) Add more plans, 2) View existing, 3) Replan from scratch.

## 7. Use Context Files from INIT

All file contents are already loaded via `--include` in step 1 (`@` syntax doesn't work across Task() boundaries):

```bash
STATE_CONTENT=$(echo "$INIT" | jq -r '.state_content // empty')
ROADMAP_CONTENT=$(echo "$INIT" | jq -r '.roadmap_content // empty')
REQUIREMENTS_CONTENT=$(echo "$INIT" | jq -r '.requirements_content // empty')
RESEARCH_CONTENT=$(echo "$INIT" | jq -r '.research_content // empty')
VERIFICATION_CONTENT=$(echo "$INIT" | jq -r '.verification_content // empty')
UAT_CONTENT=$(echo "$INIT" | jq -r '.uat_content // empty')
CONTEXT_CONTENT=$(echo "$INIT" | jq -r '.context_content // empty')
# Ouroboros context (codex r44 P1 #2-4): the planner agent's <dead-ends>,
# <genome>, and prior_reflections blocks document these inputs, but the
# orchestrator must extract them from INIT and inject them into the
# planner prompt — otherwise the planner never sees them.
DEAD_ENDS_MD=$(echo "$INIT" | jq -r '.dead_ends_md // empty')
GENOME_MD=$(echo "$INIT" | jq -r '.genome_md // empty')
PRIOR_REFLECTIONS=$(echo "$INIT" | jq -r '.prior_reflections // empty')
```

## 8. Spawn grd-planner Agent

Display banner:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► PLANNING PHASE {X}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

* Spawning planner...
```

Planner prompt:

```markdown
PATHS:
research_dir: ${research_dir}
phases_dir: ${phases_dir}
phase_dir: ${phase_dir}
codebase_dir: ${codebase_dir}

<planning_context>
**Phase:** {phase_number}
**Mode:** {standard | gap_closure}

**Project State:** {state_content}
**Roadmap:** {roadmap_content}
**Requirements:** {requirements_content}

**Phase Context:**
IMPORTANT: If context exists below, it contains USER DECISIONS from /grd:discuss-phase.
- **Decisions** = LOCKED — honor exactly, do not revisit
- **Claude's Discretion** = Freedom — make implementation choices
- **Deferred Ideas** = Out of scope — do NOT include

{context_content}

**Research:** {research_content}

**Research Landscape:**
{research_landscape_context}

**Prior Reflections (from completed phases):**
{prior_reflections}
NOTE: If verdict is "falsified" for any pattern in your considered approach, refuse to re-propose that approach. Reference the prior phase in your plan rationale.

**Dead Ends Registry (.planning/DEAD-ENDS.md):**
{dead_ends_md}
NOTE: Each entry is an approach that has been tried and failed. Treat as hard "do-not-propose" list.

**Strategy Genome (.planning/GENOME.md):**
{genome_md}
NOTE: Curated heuristics + dated snapshots of past project state. Use heuristics to inform plan choices.

**Gap Closure (if --gaps):** {verification_content} {uat_content}
</planning_context>

<downstream_consumer>
Output consumed by /grd:execute-phase. Plans need:
- Frontmatter (wave, depends_on, files_modified, autonomous)
- Tasks in XML format
- Verification criteria
- must_haves for goal-backward verification
</downstream_consumer>

<quality_gate>
- [ ] PLAN.md files created in phase directory
- [ ] Each plan has valid frontmatter
- [ ] Tasks are specific and actionable
- [ ] Dependencies correctly identified
- [ ] Waves assigned for parallel execution
- [ ] must_haves derived from phase goal
</quality_gate>
```

```
Task(
  prompt="First, read ${CLAUDE_PLUGIN_ROOT}/agents/grd-planner.md for your role and instructions.\n\n" + filled_prompt,
  subagent_type="general-purpose",
  model="{planner_model}",
  description="Plan Phase {phase}"
)
```

## 9. Handle Planner Return

- **`## PLANNING COMPLETE`:** Display plan count. If `--skip-verify` or `plan_checker_enabled` is false (from init): skip to step 13. Otherwise: step 10.
- **`## CHECKPOINT REACHED`:** Present to user, get response, spawn continuation (step 12)
- **`## PLANNING INCONCLUSIVE`:** Show attempts, offer: Add context / Retry / Manual

## 10. Spawn grd-plan-checker Agent

Display banner:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► VERIFYING PLANS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

* Spawning plan checker...
```

```bash
PLANS_CONTENT=$(cat "${PHASE_DIR}"/*-PLAN.md 2>/dev/null)
```

Checker prompt:

```markdown
<verification_context>
**Phase:** {phase_number}
**Phase Goal:** {goal from ROADMAP}

**Plans to verify:** {plans_content}
**Requirements:** {requirements_content}

**Phase Context:**
IMPORTANT: Plans MUST honor user decisions. Flag as issue if plans contradict.
- **Decisions** = LOCKED — plans must implement exactly
- **Claude's Discretion** = Freedom areas — plans can choose approach
- **Deferred Ideas** = Out of scope — plans must NOT include

{context_content}
</verification_context>

<expected_output>
- ## VERIFICATION PASSED — all checks pass
- ## ISSUES FOUND — structured issue list
</expected_output>
```

```
Task(
  prompt=checker_prompt,
  subagent_type="grd:grd-plan-checker",
  model="{checker_model}",
  description="Verify Phase {phase} plans"
)
```

## 11. Handle Checker Return

- **`## VERIFICATION PASSED`:** Display confirmation, proceed to step 13.
- **`## ISSUES FOUND`:** Display issues, check iteration count, proceed to step 12.

## 12. Revision Loop (Max 3 Iterations)

Track `iteration_count` (starts at 1 after initial plan + check).

**If iteration_count < 3:**

Display: `Sending back to planner for revision... (iteration {N}/3)`

```bash
PLANS_CONTENT=$(cat "${PHASE_DIR}"/*-PLAN.md 2>/dev/null)
```

Revision prompt:

```markdown
<revision_context>
**Phase:** {phase_number}
**Mode:** revision

**Existing plans:** {plans_content}
**Checker issues:** {structured_issues_from_checker}

**Phase Context:**
Revisions MUST still honor user decisions.
{context_content}
</revision_context>

<instructions>
Make targeted updates to address checker issues.
Do NOT replan from scratch unless issues are fundamental.
Return what changed.
</instructions>
```

```
Task(
  prompt="First, read ${CLAUDE_PLUGIN_ROOT}/agents/grd-planner.md for your role and instructions.\n\n" + revision_prompt,
  subagent_type="general-purpose",
  model="{planner_model}",
  description="Revise Phase {phase} plans"
)
```

After planner returns -> spawn checker again (step 10), increment iteration_count.

**If iteration_count >= 3:**

Display: `Max iterations reached. {N} issues remain:` + issue list

Offer: 1) Force proceed, 2) Provide guidance and retry, 3) Abandon

## 13. Eval Planning Step

**After plan creation/verification, spawn grd-eval-planner to create EVAL.md:**

**Skip if:** `--skip-eval` flag is present (and not `--eval-only`).

**If `--eval-only`:** Validate that PLAN.md files exist for this phase. If missing, error: "No plans found for phase {X}. Run `/grd:plan-phase {X}` first." Load plan content and baseline/landscape context, then proceed to spawn the eval planner below.

Display banner:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► DESIGNING EVALUATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

* Spawning eval planner...
```

```
Task(
  prompt="
PATHS:
research_dir: ${research_dir}
phases_dir: ${phases_dir}
phase_dir: ${phase_dir}
codebase_dir: ${codebase_dir}

<eval_context>
**Phase:** {phase_number}: {phase_name}
**Phase Goal:** {goal from ROADMAP}
**Plans:** {plans summary}
**Benchmarks:** {BENCHMARKS content from research landscape}
**Requirements:** {requirements_content}
</eval_context>

<instructions>
Create EVAL.md with tiered verification plan:
1. Tier 1 (Sanity): Quick checks that run in seconds — type checks, lint, unit tests pass
2. Tier 2 (Proxy): Automated metrics that approximate real quality — test coverage, benchmark scores, output quality checks
3. Tier 3 (Deferred): Validations that require human/integration — user testing, real-world performance, domain expert review

For each tier:
- Define specific metrics with pass/fail thresholds
- Specify how to measure (commands, scripts, tools)
- Set targets based on BENCHMARKS.md if available

Write to: {phase_dir}/{phase}-EVAL.md
</instructions>
",
  subagent_type="grd:grd-eval-planner",
  model="{checker_model}",
  description="Design evaluation for Phase {phase}"
)
```

## 13.5. Research Gate: Verification Design Review

**Check research_gates.verification_design from config:**

```bash
VD_GATE=$(cat .planning/config.json 2>/dev/null | jq -r '.research_gates.verification_design // false')
```

**If `autonomous_mode` is true (YOLO):** Skip all gates, auto-approve.

**If verification_design gate is true:**

Use AskUserQuestion:
- header: "Eval Review"
- question: "EVAL.md created for Phase {X}. Review the verification design before proceeding?"
- options:
  - "Approve" — EVAL.md looks good, proceed
  - "Review" — Show me the EVAL.md contents
  - "Edit" — I want to adjust metrics/targets

**If "Review":** Display EVAL.md, then re-ask.
**If "Edit":** Let user provide feedback, update EVAL.md.
**If "Approve":** Continue.

**If verification_design gate is false:** Auto-approve.

## 13.7. Tracker Integration

**After plan creation, sync phase to issue tracker (non-blocking):**

Check tracker config provider first. **For GitHub:**
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker sync-phase "${PHASE}" --raw 2>/dev/null || true
```

**For mcp-atlassian** (see @${CLAUDE_PLUGIN_ROOT}/references/mcp-tracker-protocol.md):
```bash
OPS=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker prepare-phase-sync "${PHASE}" --raw)
```
For each `"create"` operation, call MCP `create_issue`, then `record-mapping`.

This creates task issues for each plan in the configured tracker (GitHub Issues or MCP Atlassian). Idempotent — already-synced plans are skipped. If no tracker is configured, this is a no-op.

## 14. Present Final Status

Route to `<offer_next>`.

</process>

<offer_next>
Output this markdown directly (not as a code block):

**If `--research-only` mode:**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► PHASE {X} RESEARCHED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Phase {X}: {Name}** — Research complete

---

## Next Up

**Plan Phase {X}** — create execution plans using this research

/grd:plan-phase {X}

---

**Also available:**
- cat ${phase_dir}/*-RESEARCH.md — review research
- /grd:plan-phase {X} --research-only — re-research
- /grd:deep-dive \<paper\> — dig deeper into a specific paper

---

**If `--eval-only` mode:**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► PHASE {X} EVAL PLANNED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Phase {X}: {Name}** — Eval plan created

Tier 1 (Sanity): {N} checks
Tier 2 (Proxy): {N} metrics
Tier 3 (Deferred): {N} evaluations

---

## Next Up

**Execute Phase {X}** — run all plans

/grd:execute-phase {X}

---

**Also available:**
- cat ${phase_dir}/*-EVAL.md — review eval plan
- /grd:assess-baseline — establish baseline first

---

**If default (full) mode:**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► PHASE {X} PLANNED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Phase {X}: {Name}** — {N} plan(s) in {M} wave(s)

| Wave | Plans | What it builds |
|------|-------|----------------|
| 1    | 01, 02 | [objectives] |
| 2    | 03     | [objective]  |

Research: {Completed | Used existing | Skipped}
Verification: {Passed | Passed with override | Skipped}
Eval Plan: {Created | Skipped}

---

## Next Up

**Execute Phase {X}** — run all {N} plans

/grd:execute-phase {X}

<sub>/clear first -> fresh context window</sub>

---

**Also available:**
- cat ${phase_dir}/*-PLAN.md — review plans
- cat ${phase_dir}/*-EVAL.md — review eval plan
- /grd:plan-phase {X} --research-only — re-research
- /grd:plan-phase {X} --eval-only — redesign eval plan

---
</offer_next>

<success_criteria>
- [ ] .planning/ directory validated
- [ ] Phase validated against roadmap
- [ ] Phase directory created if needed
- [ ] Research landscape context loaded (LANDSCAPE.md, KNOWHOW.md, deep-dives)
- [ ] CONTEXT.md loaded early (step 4) and passed to ALL agents
- [ ] **Full mode:** Research completed (unless --skip-research or --gaps or exists)
- [ ] **Full mode:** grd-phase-researcher spawned with CONTEXT.md and research landscape
- [ ] **Full mode:** Existing plans checked
- [ ] **Full mode:** grd-planner spawned with CONTEXT.md + RESEARCH.md + research landscape
- [ ] **Full mode:** Plans created (PLANNING COMPLETE or CHECKPOINT handled)
- [ ] **Full mode:** grd-plan-checker spawned with CONTEXT.md
- [ ] **Full mode:** Verification passed OR user override OR max iterations with user decision
- [ ] **Full mode:** grd-eval-planner spawned to create EVAL.md with tiered verification
- [ ] **Full mode:** Research gate honored (verification_design review if enabled, skipped in YOLO mode)
- [ ] **Full mode:** GitHub issues created/updated (if gh available)
- [ ] **--research-only:** Only grd-phase-researcher spawned; planner/checker/eval-planner skipped
- [ ] **--eval-only:** Only grd-eval-planner spawned; researcher/planner/checker skipped; PLAN.md must exist
- [ ] User sees status between agent spawns
- [ ] User knows next steps
</success_criteria>

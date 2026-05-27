---
description: Systematic debugging with persistent state across context resets
argument-hint: [bug description or session ID to resume]
---

<purpose>
Systematic debugging with persistent state across context resets. Manages debug sessions via .planning/debug/ files that track hypotheses, evidence, and resolutions. Spawns grd-debugger agent for investigation.

User reports issue, Claude investigates autonomously. Debug state persists across /clear for long investigations.
</purpose>

<paths>
DEBUG_DIR=.planning/debug
DEBUG_RESOLVED_DIR=.planning/debug/resolved

Debug files use the `.planning/debug/` path.
</paths>

<core_principle>
**Scientific method for debugging.**

1. Observe symptoms precisely
2. Form falsifiable hypotheses
3. Design experiments to test them
4. Record evidence
5. Eliminate failed hypotheses
6. Confirm root cause with evidence
7. Apply and verify fix

The debug file IS the brain. Claude should resume perfectly from any /clear point.
</core_principle>

<process>

<step name="initialize" priority="first">
Load debug context:

```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js state load)
DEBUGGER_MODEL=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js resolve-model grd-debugger --raw)
```

Parse: `commit_docs`, `autonomous_mode`.
</step>

<step name="check_active_sessions">
**First: Check for active debug sessions**

```bash
ls .planning/debug/*.md 2>/dev/null | grep -v resolved
```

**If active sessions exist AND no [user-provided arguments] provided:**

Read each file's frontmatter (status, trigger) and Current Focus section.

Display:

```
## Active Debug Sessions

| # | Issue | Status | Current Hypothesis | Evidence |
|---|-------|--------|-------------------|----------|
| 1 | auth-screen-dark | investigating | Background CSS override | 3 findings |
| 2 | model-nan-loss | gathering | [awaiting symptoms] | 0 findings |

Reply with a number to resume, or describe a new issue to start.
```

Wait for user response.

- If number → Load that file, spawn grd-debugger with resume context
- If text → Treat as new issue, go to `create_session`

**If active sessions AND [user-provided arguments] provided:**
- Start new session with [user-provided arguments] as the issue description

**If no active sessions AND no [user-provided arguments]:**
```
No active debug sessions. Describe the issue to start debugging.
```

**If no active sessions AND [user-provided arguments] provided:**
- Continue to `create_session`
</step>

<step name="create_session">
**Create debug session and spawn debugger:**

Generate slug from user input (lowercase, hyphens, max 30 chars).

```bash
mkdir -p .planning/debug
```

Spawn grd-debugger agent:

```
Task(
  prompt="""
<objective>
Investigate issue reported by user.

**User report:** {[user-provided arguments] or user input}
</objective>

<mode>
symptoms_prefilled: false
goal: find_and_fix
</mode>

<debug_file>
Create: .planning/debug/{slug}.md
</debug_file>
""",
  subagent_type="grd:grd-debugger",
  model="{debugger_model}",
  description="Debug: {slug}"
)
```

The grd-debugger agent will:
1. Create the debug file immediately
2. Gather symptoms through questioning (if not pre-filled)
3. Investigate autonomously
4. Return ROOT CAUSE FOUND, DEBUG COMPLETE, CHECKPOINT REACHED, or INVESTIGATION INCONCLUSIVE
</step>

<step name="resume_session">
**Resume an existing debug session:**

Read the debug file to get current state.

Spawn grd-debugger with resume context:

```
Task(
  prompt="""
<objective>
Continue debugging {slug}. Evidence is in the debug file.
</objective>

<prior_state>
Debug file: @.planning/debug/{slug}.md
</prior_state>

<mode>
goal: find_and_fix
</mode>
""",
  subagent_type="grd:grd-debugger",
  model="{debugger_model}",
  description="Resume debug: {slug}"
)
```
</step>

<step name="handle_checkpoint">
**Handle checkpoint from debugger agent:**

When grd-debugger returns CHECKPOINT REACHED:

Display the checkpoint to user:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► DEBUG CHECKPOINT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Issue:** {slug}
**Type:** {human-verify | human-action | decision}
**Progress:** {evidence_count} findings, {eliminated_count} hypotheses eliminated

{checkpoint details from agent}

──────────────────────────────────────────────────────
→ {what's needed from user}
──────────────────────────────────────────────────────
```

Wait for user response.

Spawn continuation with user's response:

```
Task(
  prompt="""
<objective>
Continue debugging {slug}. Evidence is in the debug file.
</objective>

<prior_state>
Debug file: @.planning/debug/{slug}.md
</prior_state>

<checkpoint_response>
**Type:** {checkpoint_type}
**Response:** {user_response}
</checkpoint_response>

<mode>
goal: find_and_fix
</mode>
""",
  subagent_type="grd:grd-debugger",
  model="{debugger_model}",
  description="Continue debug: {slug}"
)
```
</step>

<step name="handle_completion">
**Handle completed debug session:**

When grd-debugger returns DEBUG COMPLETE:

Display:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► DEBUG RESOLVED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Issue:** {slug}
**Root Cause:** {root_cause}
**Fix Applied:** {fix description}
**Files Changed:** {files list}
**Verification:** {how verified}

Debug session archived to .planning/debug/resolved/{slug}.md

───────────────────────────────────────────────────────────────

## Next Up

- `/grd:verify-work {phase}` — Run full UAT
- `/grd:execute-phase {phase}` — Continue phase execution
- `/grd:debug` — Start another debug session

───────────────────────────────────────────────────────────────
```

When grd-debugger returns INVESTIGATION INCONCLUSIVE:

Display:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► DEBUG INCONCLUSIVE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Issue:** {slug}
**What was checked:** {areas examined}
**Hypotheses eliminated:** {count}
**Remaining possibilities:**
{list from agent return}

Debug session saved to .planning/debug/{slug}.md

Options:
1. Resume investigation (fresh context, same evidence)
2. Provide additional context (describe what you know)
3. Abandon session
```

Wait for user response.
</step>

</process>

<debug_file_template>
Template for `.planning/debug/{slug}.md`:

```markdown
---
status: gathering | investigating | fixing | verifying | resolved
trigger: "[verbatim user input]"
created: [ISO timestamp]
updated: [ISO timestamp]
---

## Current Focus
<!-- OVERWRITE on each update - reflects NOW -->

hypothesis: [current theory]
test: [how testing it]
expecting: [what result means]
next_action: [immediate next step]

## Symptoms
<!-- Written during gathering, then IMMUTABLE -->

expected: [what should happen]
actual: [what actually happens]
errors: [error messages]
reproduction: [how to trigger]
started: [when broke / always broken]

## Eliminated
<!-- APPEND only - prevents re-investigating -->

- hypothesis: [theory that was wrong]
  evidence: [what disproved it]
  timestamp: [when eliminated]

## Evidence
<!-- APPEND only - facts discovered -->

- timestamp: [when found]
  checked: [what examined]
  found: [what observed]
  implication: [what this means]

## Resolution
<!-- OVERWRITE as understanding evolves -->

root_cause: [empty until found]
fix: [empty until applied]
verification: [empty until verified]
files_changed: []
```
</debug_file_template>

<session_management>

## Session Lifecycle

| Event | Action |
|-------|--------|
| `/grd:debug "issue description"` | Create new session, spawn debugger |
| `/grd:debug` (with active sessions) | List sessions, offer resume |
| `/grd:debug` (no sessions, no args) | Prompt for issue description |
| Agent returns CHECKPOINT | Present to user, spawn continuation |
| Agent returns DEBUG COMPLETE | Show resolution, archive session |
| Agent returns INCONCLUSIVE | Offer resume, context, or abandon |
| `/clear` during debug | Session state preserved in file |

## File State Guarantees

The debug file always reflects the latest state:
- **Current Focus** shows what was happening when last updated
- **Eliminated** prevents re-investigating dead ends
- **Evidence** accumulates findings
- Any Claude instance can resume from the file alone

</session_management>

<failure_handling>

**Agent fails or errors:**
- Debug file preserves progress up to last update
- Resume with `/grd:debug` — will detect existing session
- Evidence and eliminated hypotheses are never lost

**Agent times out:**
- Check .planning/debug/{slug}.md for partial progress
- Resume picks up from Current Focus

**User /clear during investigation:**
- File persists
- Next `/grd:debug` detects active session
- Offers to resume from last recorded state

</failure_handling>

<success_criteria>
- [ ] Active sessions detected and offered for resume
- [ ] New sessions create debug file immediately
- [ ] grd-debugger agent spawned with appropriate context
- [ ] Checkpoints presented to user and responses forwarded
- [ ] Completed sessions archived to resolved/
- [ ] Inconclusive sessions offer resume options
- [ ] State persists across /clear boundaries
- [ ] All commands reference grd-tools.js via ${CLAUDE_PLUGIN_ROOT}
</success_criteria>

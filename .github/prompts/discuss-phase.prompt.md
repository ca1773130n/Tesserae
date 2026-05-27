---
description: Extract implementation decisions before phase planning
argument-hint: <phase number>
---

<purpose>
Extract implementation decisions that downstream agents need. Analyze the phase to identify gray areas, let the user choose what to discuss, then deep-dive each selected area until satisfied.

You are a thinking partner, not an interviewer. The user is the visionary — you are the builder. Your job is to capture decisions that will guide research and planning, not to figure out implementation yourself.
</purpose>

<downstream_awareness>
**CONTEXT.md feeds into:**

1. **grd-phase-researcher** — Reads CONTEXT.md to know WHAT to research
2. **grd-planner** — Reads CONTEXT.md to know WHAT decisions are locked

**Your job:** Capture decisions clearly enough that downstream agents can act on them without asking the user again.
</downstream_awareness>

<process>

<step name="initialize" priority="first">
Phase number from argument (required).

```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init phase-op "${PHASE}")
```

Parse JSON for: `commit_docs`, `phase_found`, `phase_dir`, `phase_number`, `phase_name`, `phase_slug`, `padded_phase`, `has_research`, `has_context`, `has_plans`, `has_verification`, `plan_count`, `roadmap_exists`, `planning_exists`.

**If `phase_found` is false:**
```
Phase [X] not found in roadmap.

Use /grd:progress to see available phases.
```
Exit workflow.
</step>

<step name="check_existing">
Check if CONTEXT.md already exists using `has_context` from init.

**If exists:**
Use AskUserQuestion:
- header: "Existing context"
- question: "Phase [X] already has context. What do you want to do?"
- options:
  - "Update it" — Review and revise existing context
  - "View it" — Show me what's there
  - "Skip" — Use existing context as-is
</step>

<step name="analyze_phase">
Analyze the phase to identify gray areas worth discussing. Read the phase description from ROADMAP.md and determine:

1. **Domain boundary** — What capability is this phase delivering?
2. **Gray areas by category** — For each relevant category, identify 1-2 specific ambiguities
3. **Skip assessment** — If no meaningful gray areas exist, the phase may not need discussion
</step>

<step name="present_gray_areas">
Present the domain boundary and gray areas to user.

**First, state the boundary:**
```
Phase [X]: [Name]
Domain: [What this phase delivers]

We'll clarify HOW to implement this.
(New capabilities belong in other phases.)
```

**Then use AskUserQuestion (multiSelect: true):**
- header: "Discuss"
- question: "Which areas do you want to discuss for [phase name]?"
- options: Generate 3-4 phase-specific gray areas
</step>

<step name="discuss_areas">
For each selected area, conduct a focused discussion loop.

**CRITICAL: No solutions before understanding.** You are gathering context, not solving problems. Do not propose approaches until you fully understand the problem space. Premature solutions anchor thinking and close off better options.

**Protocol:**

1. Ask questions **ONE AT A TIME**. Each answer informs the next question. Never batch multiple questions — it overwhelms and produces shallow answers.

2. After 4 questions per area, offer a check:
   - Use AskUserQuestion:
     - header: "Direction"
     - question: "We've explored [area]. What next?"
     - options:
       - "Go deeper" — More questions on this area
       - "Propose approaches" — I've shared enough, show me options
       - "Skip" — Move to next area

3. **If "Go deeper"**: Ask 4 more questions, then check again.

4. **If "Propose approaches"**: Enter approach proposal mode (see `propose_approaches` sub-step below).

5. **If "Skip"**: Move to next selected area.

**Anti-patterns to avoid:**
- Solution anchoring ("Should we use a transformer here?")
- Leading questions ("Don't you think X would work?")
- Premature trade-off analysis
- Skipping the "why" to jump to "how"
- Question batching
- Assumption embedding ("How should we implement the cache?" assumes caching is needed)
</step>

<step name="propose_approaches">
When user selects "Propose approaches" during discussion:

Present 2-3 **distinct** approaches (not minor variations). For each:
- **What it is** (1-2 sentences)
- **Research backing** (paper references from LANDSCAPE.md/PAPERS.md if applicable)
- **Complexity** (low / medium / high)
- **Risk** (what could go wrong)
- **Trade-offs** vs alternatives

Use AskUserQuestion for selection:
- header: "Approach"
- question: "Which approach for [area]?"
- options: the approaches + "Combine elements" + "None — explore more"

**If "Combine elements"**: Ask which elements from which approaches, then synthesize.
**If "None — explore more"**: Return to questioning loop.
**Otherwise**: Record selection with rationale.
</step>

<step name="write_context">
Create CONTEXT.md capturing decisions made.

**File location:** `${phase_dir}/${padded_phase}-CONTEXT.md`

Structure with: Phase Boundary, Implementation Decisions, Approach Selections (if any approaches were proposed and selected), Claude's Discretion, Specific Ideas, Deferred Ideas.
</step>

<step name="confirm_creation">
Present summary and next steps:

```
Created: ${phase_dir}/${PADDED_PHASE}-CONTEXT.md

## Decisions Captured

### [Category]
- [Key decision]

---

## Next Up

**Phase ${PHASE}: [Name]** — [Goal from ROADMAP.md]

`/grd:plan-phase ${PHASE}`

<sub>`/clear` first -> fresh context window</sub>

---

**Also available:**
- `/grd:plan-phase ${PHASE} --skip-research` — plan without research
- Review/edit CONTEXT.md before continuing

---
```
</step>

<step name="git_commit">
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs(${padded_phase}): capture phase context" --files "${phase_dir}/${padded_phase}-CONTEXT.md"
```
</step>

</process>

<success_criteria>
- Phase validated against roadmap
- Gray areas identified through intelligent analysis
- User selected which areas to discuss
- Each selected area explored until user satisfied
- Scope creep redirected to deferred ideas
- CONTEXT.md captures actual decisions
- User knows next steps
</success_criteria>

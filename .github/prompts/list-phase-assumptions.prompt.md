---
description: Surface Claude's assumptions about a phase before planning
argument-hint: <phase number>
---

<purpose>
Surface Claude's assumptions about a phase before planning, enabling users to correct misconceptions early.

Key difference from discuss-phase: This is ANALYSIS of what Claude thinks, not INTAKE of what user knows. No file output - purely conversational to prompt discussion.
</purpose>

<process>

<step name="validate_phase" priority="first">
Phase number: [user-provided arguments] (required)

**If argument missing:**

```
Error: Phase number required.

Usage: /grd:list-phase-assumptions [phase-number]
Example: /grd:list-phase-assumptions 3
```

Exit workflow.

**If argument provided:**
Validate phase exists in roadmap:

```bash
cat .planning/ROADMAP.md | grep -i "Phase ${PHASE}"
```

**If phase not found:**

```
Error: Phase ${PHASE} not found in roadmap.

Available phases:
[list phases from roadmap]
```

Exit workflow.

**If phase found:**
Parse phase details from roadmap:

- Phase number
- Phase name
- Phase description/goal
- Any scope details mentioned

Continue to analyze_phase.
</step>

<step name="analyze_phase">
**First, check if CONTEXT.md exists for this phase:**
```bash
cat ${PHASE_DIR}/*-CONTEXT.md 2>/dev/null
```
If CONTEXT.md exists, incorporate its locked decisions, discretion areas, and deferred ideas into your assumption analysis. Note which assumptions align with or contradict the existing context.

Based on roadmap description and project context, identify assumptions across seven areas:

**1. Technical Approach:**
What libraries, frameworks, patterns, or tools would Claude use?
- "I'd use X library because..."
- "I'd follow Y pattern because..."
- "I'd structure this as Z because..."

**2. Implementation Order:**
What would Claude build first, second, third?
- "I'd start with X because it's foundational"
- "Then Y because it depends on X"
- "Finally Z because..."

**3. Scope Boundaries:**
What's included vs excluded in Claude's interpretation?
- "This phase includes: A, B, C"
- "This phase does NOT include: D, E, F"
- "Boundary ambiguities: G could go either way"

**4. Risk Areas:**
Where does Claude expect complexity or challenges?
- "The tricky part is X because..."
- "Potential issues: Y, Z"
- "I'd watch out for..."

**5. Dependencies:**
What does Claude assume exists or needs to be in place?
- "This assumes X from previous phases"
- "External dependencies: Y, Z"
- "This will be consumed by..."

**6. Research Methodology:**
What papers, methods, or prior work inform this phase?
- "I'd base this on paper X because..."
- "The approach from LANDSCAPE.md recommends Y"
- "KNOWHOW.md notes Z about this technique"
- "I'd follow the method from deep-dive: [paper-slug]"
- "Alternative approaches considered: A, B (rejected because...)"

**7. Evaluation Strategy:**
How would Claude verify this phase meets quantitative targets?
- "Tier 1 sanity: model loads, output shape correct, no NaN"
- "Tier 2 proxy: run on Set5, expect PSNR > X"
- "Tier 3 deferred: full eval on all datasets at milestone boundary"
- "Baseline comparison: must not regress from BASELINE.md values"
- "Key metrics: [list from BENCHMARKS.md or eval_config]"

Be honest about uncertainty. Mark assumptions with confidence levels:
- "Fairly confident: ..." (clear from roadmap)
- "Assuming: ..." (reasonable inference)
- "Unclear: ..." (could go multiple ways)
</step>

<step name="present_assumptions">
Present assumptions in a clear, scannable format:

```
## My Assumptions for Phase ${PHASE}: ${PHASE_NAME}

### Technical Approach
[List assumptions about how to implement]

### Implementation Order
[List assumptions about sequencing]

### Scope Boundaries
**In scope:** [what's included]
**Out of scope:** [what's excluded]
**Ambiguous:** [what could go either way]

### Risk Areas
[List anticipated challenges]

### Dependencies
**From prior phases:** [what's needed]
**External:** [third-party needs]
**Feeds into:** [what future phases need from this]

### Research Methodology
**Papers/methods:** [what informs the approach]
**LANDSCAPE.md alignment:** [does chosen method match survey recommendations?]
**KNOWHOW.md considerations:** [relevant lessons from prior iterations]

### Evaluation Strategy
**Verification tier:** [Tier 1 sanity / Tier 2 proxy / Tier 3 full]
**Key metrics:** [what to measure]
**Baseline:** [what values to compare against]
**Deferred validations:** [what cannot be verified now]

---

**What do you think?**

Are these assumptions accurate? Let me know:
- What I got right
- What I got wrong
- What I'm missing
```

Wait for user response.
</step>

<step name="gather_feedback">
**If user provides corrections:**

Acknowledge the corrections:

```
Key corrections:
- [correction 1]
- [correction 2]

This changes my understanding significantly. [Summarize new understanding]
```

**If user confirms assumptions:**

```
Assumptions validated.
```

Continue to offer_next.
</step>

<step name="offer_next">
Present next steps:

```
What's next?
1. Discuss context (/grd:discuss-phase ${PHASE}) - Let me ask you questions to build comprehensive context
2. Plan this phase (/grd:plan-phase ${PHASE}) - Create detailed execution plans
3. Research context (/grd:research-phase ${PHASE}) - Survey literature and build research context first
4. Re-examine assumptions - I'll analyze again with your corrections
5. Done for now
```

Wait for user selection.

If "Discuss context": Note that CONTEXT.md will incorporate any corrections discussed here
If "Plan this phase": Proceed knowing assumptions are understood
If "Re-examine": Return to analyze_phase with updated understanding
</step>

</process>

<success_criteria>
- Phase number validated against roadmap
- Assumptions surfaced across seven areas: technical approach, implementation order, scope, risks, dependencies, research methodology, evaluation strategy
- Confidence levels marked where appropriate
- "What do you think?" prompt presented
- User feedback acknowledged
- Clear next steps offered
</success_criteria>

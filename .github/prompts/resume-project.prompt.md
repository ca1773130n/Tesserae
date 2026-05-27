---
description: Restore full project context to resume work seamlessly
---

<trigger>
Use this workflow when:
- Starting a new session on an existing project
- User says "continue", "what's next", "where were we", "resume"
- Any planning operation when .planning/ already exists
- User returns after time away from project
</trigger>

<purpose>
Instantly restore full project context so "Where were we?" has an immediate, complete answer.
</purpose>

<required_reading>
@${CLAUDE_PLUGIN_ROOT}/references/continuation-format.md
</required_reading>

<process>

<step name="initialize">
```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init resume)
```

Parse JSON for: `state_exists`, `roadmap_exists`, `project_exists`, `planning_exists`, `has_interrupted_agent`, `interrupted_agent_id`, `commit_docs`, `phases_dir`, `todos_dir`.

**If `state_exists` is true:** Proceed to load_state
**If `state_exists` is false but `roadmap_exists` or `project_exists` is true:** Offer to reconstruct STATE.md
**If `planning_exists` is false:** This is a new project - route to /grd:init
</step>

<step name="load_state">
Read and parse STATE.md, then PROJECT.md:

```bash
cat .planning/STATE.md
cat .planning/PROJECT.md
```

Extract: Project Reference, Current Position, Progress, Recent Decisions, Pending Todos, Blockers/Concerns, Session Continuity, Requirements (Validated, Active, Out of Scope), Key Decisions, Constraints.
</step>

<step name="check_incomplete_work">
```bash
ls ${phases_dir}/*/.continue-here*.md 2>/dev/null

for plan in ${phases_dir}/*/*-PLAN.md; do
  summary="${plan/PLAN/SUMMARY}"
  [ ! -f "$summary" ] && echo "Incomplete: $plan"
done 2>/dev/null
```

Check for interrupted agents using `has_interrupted_agent` from init.
</step>

<step name="present_status">
Present complete project status with progress bar, phase/plan position, last activity, incomplete work, pending todos, and blockers.
</step>

<step name="determine_next_action">
Based on project state, determine the most logical next action:

- Interrupted agent -> Resume or start fresh
- .continue-here file -> Resume from checkpoint
- Incomplete plan -> Complete the plan
- Phase complete, all plans done -> Transition to next
- Phase ready to plan -> Check for CONTEXT.md, suggest discuss-phase or plan-phase
- Phase ready to execute -> Execute next plan
</step>

<step name="offer_options">
Present contextual options:

```
What would you like to do?

1. [Primary action based on state]
2. Review current phase status
3. Check pending todos ([N] pending)
4. Something else
```

When offering phase planning, check for CONTEXT.md first. If missing, suggest discuss-phase.
</step>

<step name="route_to_workflow">
Based on user selection, route to appropriate workflow:

- **Execute plan** -> `/grd:execute-phase {phase}`
- **Plan phase** -> `/grd:plan-phase [phase-number]`
- **Check todos** -> Read ${todos_dir}/pending/
- **Something else** -> Ask what they need

All routes suggest `/clear` first for fresh context.
</step>

<step name="update_session">
Update STATE.md session continuity before proceeding.
</step>

</process>

<success_criteria>
- [ ] STATE.md loaded (or reconstructed)
- [ ] Incomplete work detected and flagged
- [ ] Clear status presented to user
- [ ] Contextual next actions offered
- [ ] User knows exactly where project stands
- [ ] Session continuity updated
</success_criteria>

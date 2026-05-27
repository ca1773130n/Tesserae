---
description: Save work state to a handoff file for session continuity
---

<purpose>
Create `.CONTINUE-HERE.md` handoff file to preserve complete work state across sessions. Enables seamless resumption with full context restoration.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>

<step name="init_context" priority="first">
```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init resume)
```

Parse JSON for: `phases_dir`, `phase_dir`, `phase_number`, `phase_name`, `state_exists`, `commit_docs`.
</step>

<step name="detect">
Find current phase directory from init context (prefer `phase_dir` from init). If not available, detect from most recently modified files:

```bash
ls -lt ${phases_dir}/*-*/*-PLAN.md 2>/dev/null | head -1
```

If no active phase detected, ask user which phase they're pausing work on.
</step>

<step name="gather">
Collect complete state for handoff:

1. **Current position**: Which phase, which plan, which task
2. **Work completed**: What got done this session
3. **Work remaining**: What's left in current plan/phase
4. **Decisions made**: Key decisions and rationale
5. **Blockers/issues**: Anything stuck
6. **Mental context**: The approach, next steps
7. **Files modified**: What's changed but not committed

Ask user for clarifications if needed.
</step>

<step name="write">
Write handoff to `${phase_dir}/.CONTINUE-HERE.md` with sections: current_state, completed_work, remaining_work, decisions_made, blockers, context, next_action.

Use timestamps from:
```bash
timestamp=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js current-timestamp full --raw)
```
</step>

<step name="commit">
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "wip: [phase-name] paused at task [X]/[Y]" --files ${phase_dir}/.CONTINUE-HERE.md
```
</step>

<step name="confirm">
```
Handoff created: ${phase_dir}/.CONTINUE-HERE.md

Current state:
- Phase: [XX-name]
- Task: [X] of [Y]
- Status: [in_progress/blocked]
- Committed as WIP

To resume: /grd:resume-work
```
</step>

</process>

<success_criteria>
- [ ] .CONTINUE-HERE.md created in correct phase directory
- [ ] All sections filled with specific content
- [ ] Committed as WIP
- [ ] User knows location and how to resume
</success_criteria>

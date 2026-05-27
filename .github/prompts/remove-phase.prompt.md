---
description: Remove an unstarted phase from the roadmap and renumber subsequent phases
argument-hint: <phase number>
---

<purpose>
Remove an unstarted future phase from the project roadmap, delete its directory, renumber all subsequent phases to maintain a clean linear sequence, and commit the change.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>

<step name="parse_arguments">
Parse: phase number to remove (integer or decimal).
If no argument: Error with usage. Exit.
</step>

<step name="init_context">
```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init phase-op "${target}")
```

Extract: `phase_found`, `phase_dir`, `phase_number`, `commit_docs`, `roadmap_exists`.
</step>

<step name="validate_future_phase">
Target must be > current phase number.

If target <= current phase:
```
ERROR: Cannot remove Phase {target}
Only future phases can be removed.
To abandon current work, use /grd:pause-work instead.
```
Exit.
</step>

<step name="confirm_removal">
Present removal summary and confirm:
```
Removing Phase {target}: {Name}

This will:
- Delete: ${phase_dir}/
- Renumber all subsequent phases
- Update: ROADMAP.md, STATE.md

Proceed? (y/n)
```
</step>

<step name="execute_removal">
```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js phase remove "${target}")
```

If phase has executed plans, use `--force` only if user confirms.
</step>

<step name="commit">
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "chore: remove phase {target} ({original-phase-name})" --files .planning/
```
</step>

<step name="completion">
```
Phase {target} ({original-name}) removed.

Changes:
- Deleted: ${phase_dir}/
- Renumbered: {N} directories and {M} files
- Updated: ROADMAP.md, STATE.md
- Committed

---

## What's Next

- `/grd:progress` — see updated roadmap status
- Continue with current phase

---
```
</step>

</process>

<success_criteria>
- [ ] Target phase validated as future/unstarted
- [ ] `grd-tools phase remove` executed successfully
- [ ] Changes committed with descriptive message
- [ ] User informed of changes
</success_criteria>

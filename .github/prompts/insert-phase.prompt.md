---
description: Insert a decimal phase for urgent work between existing phases
argument-hint: <after phase number> <phase name and goal>
---

<purpose>
Insert a decimal phase for urgent work discovered mid-milestone between existing integer phases. Uses decimal numbering (72.1, 72.2, etc.) to preserve the logical sequence of planned phases while accommodating urgent insertions without renumbering the entire roadmap.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>

<step name="parse_arguments">
Parse the command arguments:
- First argument: integer phase number to insert after
- Remaining arguments: phase description

Example: `/grd:insert-phase 72 Fix critical auth bug`
-> after = 72, description = "Fix critical auth bug"

If arguments missing:
```
ERROR: Both phase number and description required
Usage: /grd:insert-phase <after> <description>
Example: /grd:insert-phase 72 Fix critical auth bug
```
Exit.
</step>

<step name="init_context">
```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init phase-op "${after_phase}")
```

Parse JSON for: `roadmap_exists`, `phases_dir`, `commit_docs`.

Check `roadmap_exists` from init JSON. If false: Error and exit.
</step>

<step name="insert_phase">
```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js phase insert "${after_phase}" "${description}")
```

Extract from result: `phase_number`, `after_phase`, `name`, `slug`, `directory`.
</step>

<step name="update_project_state">
Update STATE.md under "### Roadmap Evolution":
```
- Phase {decimal_phase} inserted after Phase {after_phase}: {description} (URGENT)
```
</step>

<step name="reschedule_check">
If `schedule_affected` is true in the result:

1. Check tracker config:
```bash
TRACKER_CONFIG=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker get-config --raw)
```

2. If `provider` is `"mcp-atlassian"` and `auto_sync` is true:
   - Run reschedule automatically:
   ```bash
   OPS=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker prepare-reschedule --raw)
   ```
   - Parse `start_date_field` and `operations` from JSON
   - For each operation with `action: "update"`: call MCP tool `update_issue` with `issue_key` and `additional_fields: {[start_date_field]: start_date, "duedate": due_date}`
   - Display count of updated issues

3. If `provider` is `"mcp-atlassian"` but `auto_sync` is false:
   - Notify: `Schedule affected — inserted phase shifts subsequent dates. Run /grd:sync reschedule to update Jira dates.`

4. Otherwise: skip (no tracker or GitHub provider — dates not applicable).
</step>

<step name="completion">
```
Phase {decimal_phase} inserted after Phase {after_phase}:
- Description: {description}
- Directory: ${phases_dir}/{decimal-phase}-{slug}/
- Duration: 3d (default for insertions — edit in ROADMAP.md to adjust)
- Status: Not planned yet
- Marker: (INSERTED) - indicates urgent work
{If reschedule ran: "- Jira dates updated: {count} issues rescheduled"}

---

## Next Up

**Phase {decimal_phase}: {description}** -- urgent insertion

`/grd:plan-phase {decimal_phase}`

<sub>`/clear` first -> fresh context window</sub>

---
```
</step>

</process>

<success_criteria>
- [ ] `grd-tools phase insert` executed successfully
- [ ] Phase directory created
- [ ] Roadmap updated with new phase entry (includes "(INSERTED)" marker)
- [ ] STATE.md updated with roadmap evolution note
- [ ] User informed of next steps and dependency implications
</success_criteria>

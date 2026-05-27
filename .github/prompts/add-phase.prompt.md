---
description: Add a new phase to the end of the current milestone
argument-hint: <phase name and goal>
---

<purpose>
Add a new integer phase to the end of the current milestone in the roadmap. Automatically calculates next phase number, creates phase directory, and updates roadmap structure.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>

<step name="parse_arguments">
Parse the command arguments:
- All arguments become the phase description
- Example: `/grd:add-phase Add authentication` -> description = "Add authentication"

If no arguments provided:
```
ERROR: Phase description required
Usage: /grd:add-phase <description>
Example: /grd:add-phase Add authentication system
```
Exit.

If the user provided substantial context beyond a short name (multi-line text, goals, constraints, decisions, or background), capture it as `context` text for the `--context` flag. Short single-phrase descriptions do not need context capture.
</step>

<step name="init_context">
```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init phase-op "0")
```

Parse JSON for: `roadmap_exists`, `phases_dir`, `commit_docs`.

Check `roadmap_exists` from init JSON. If false:
```
ERROR: No roadmap found (.planning/ROADMAP.md)
Run /grd:init to initialize.
```
Exit.
</step>

<step name="add_phase">
If `context` was captured:
```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js phase add "${description}" --context "${context}")
```

Otherwise:
```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js phase add "${description}")
```

Extract from result: `phase_number`, `padded`, `name`, `slug`, `directory`.
</step>

<step name="update_project_state">
Update STATE.md under "## Accumulated Context" -> "### Roadmap Evolution":
```
- Phase {N} added: {description}
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
   - Notify: `Schedule affected. Run /grd:sync reschedule to update Jira dates.`

4. Otherwise: skip (no tracker or GitHub provider — dates not applicable).
</step>

<step name="completion">
```
Phase {N} added to current milestone:
- Description: {description}
- Directory: ${phases_dir}/{phase-num}-{slug}/
- Duration: 7d (default — edit in ROADMAP.md to adjust)
- Status: Not planned yet
{If reschedule ran: "- Jira dates updated: {count} issues rescheduled"}

---

## Next Up

**Phase {N}: {description}**

`/grd:plan-phase {N}`

<sub>`/clear` first -> fresh context window</sub>

---
```
</step>

</process>

<success_criteria>
- [ ] `grd-tools phase add` executed successfully
- [ ] Phase directory created
- [ ] CONTEXT.md created if substantial context was provided
- [ ] Roadmap updated with new phase entry
- [ ] STATE.md updated with roadmap evolution note
- [ ] User informed of next steps
</success_criteria>

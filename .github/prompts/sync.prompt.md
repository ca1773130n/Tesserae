---
description: Sync GRD project state to issue tracker (GitHub Issues or MCP Atlassian)
argument-hint: [roadmap | phase <N> | status]
---

<purpose>
Manually sync GRD project state to the configured issue tracker. Pushes roadmap phases, plan tasks, and status updates. GRD is always source of truth — this is a one-way push.
</purpose>

<context>
Tracker integration reference: @${CLAUDE_PLUGIN_ROOT}/references/tracker-integration.md
MCP protocol reference: @${CLAUDE_PLUGIN_ROOT}/references/mcp-tracker-protocol.md
</context>

<process>

## Step 1: Load Tracker Config

```bash
TRACKER_CONFIG=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker get-config --raw)
```

Parse JSON for: `provider`, `auth_status`, `auto_sync`.

**If `provider` is `"none"`:**
```
No tracker configured. Run /grd:tracker-setup to configure GitHub or MCP Atlassian integration.
```
Exit.

**If auth not ready** (GitHub: `not_authenticated`):
```
Tracker configured (${provider}) but authentication incomplete.

GitHub: Run `gh auth login`
```
Exit.

Note: `mcp-atlassian` auth_status is always `"mcp_server"` — the MCP server handles authentication transparently.

## Step 2: Parse Arguments

Parse `[user-provided arguments]`:
- Empty or `"roadmap"` → sync-roadmap mode
- `"phase <N>"` or `"phase N"` → sync-phase mode for phase N
- `"status"` → show sync status
- `"reschedule"` → reschedule mode (cascade date updates)

## Step 3: Execute Sync

### Mode: sync-roadmap

**If provider is `"github"`:**

```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker sync-roadmap --raw)
```

Parse `created`, `updated`, `skipped`, `errors` from result.

**If provider is `"mcp-atlassian"`:**

```bash
OPS=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker prepare-roadmap-sync --raw)
```

Parse JSON for `project_key`, `start_date_field`, `milestone_issue_type`, `phase_issue_type`, and `operations` array.

Initialize counters: created=0, skipped=0, errors=0.

Process operations in order — milestones first, then phases:

For each operation:
- If `action` is `"skip"`: increment skipped counter.
- If `action` is `"create"` and `type` is `"milestone"`:
  1. Build `additional_fields` object:
     - If operation has `start_date`: add `{[start_date_field]: start_date}`
     - If operation has `due_date`: add `{"duedate": due_date}`
  2. Call MCP tool `create_issue`:
     - `project_key`: from response `project_key`
     - `summary`: from operation `summary`
     - `issue_type`: from response `milestone_issue_type` (default: "Epic")
     - `description`: from operation `description`
     - `additional_fields`: the built object (if any date fields present)
  3. If MCP call succeeds, record mapping:
     ```bash
     node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker record-mapping --type milestone --milestone {version} --key {issue_key} --url {issue_url} 2>/dev/null || true
     ```
     Increment created counter.
  4. If MCP call fails: increment errors counter, continue.
- If `action` is `"create"` and `type` is `"phase"`:
  1. Build `additional_fields` object:
     - If `parent_key` present: `{"parent": {"key": "{parent_key}"}}`
     - If operation has `start_date`: add `{[start_date_field]: start_date}`
     - If operation has `due_date`: add `{"duedate": due_date}`
  2. Call MCP tool `create_issue`:
     - `project_key`: from response `project_key`
     - `summary`: from operation `summary`
     - `issue_type`: from response `phase_issue_type` (default: "Task")
     - `additional_fields`: the built object
  3. If MCP call succeeds, record mapping:
     ```bash
     node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker record-mapping --type phase --phase {phase} --key {issue_key} --url {issue_url} --parent {parent_key} 2>/dev/null || true
     ```
     Increment created counter.
  4. If MCP call fails: increment errors counter, continue.

Display:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► TRACKER SYNC — ROADMAP
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Provider: {provider}

| Action  | Count |
|---------|-------|
| Created | {created} |
| Skipped | {skipped} (already synced) |
| Errors  | {errors} |

Mapping saved to .planning/TRACKER.md
```

### Mode: sync-phase

**If provider is `"github"`:**

```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker sync-phase "${PHASE}" --raw)
```

**If provider is `"mcp-atlassian"`:**

```bash
OPS=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker prepare-phase-sync "${PHASE}" --raw)
```

Parse JSON for `project_key`, `plan_issue_type`, `parent_key`, and `operations` array.

Initialize counters: created=0, skipped=0, errors=0.

For each operation:
- If `action` is `"skip"`: increment skipped counter.
- If `action` is `"create"`:
  1. Call MCP tool `create_issue`:
     - `project_key`: from response `project_key`
     - `summary`: from operation `summary`
     - `issue_type`: from response `plan_issue_type` (default: "Sub-task")
     - `additional_fields`: `{"parent": {"key": "{parent_key}"}}` (links Sub-task to phase Task)
  2. If MCP call succeeds, record mapping:
     ```bash
     node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker record-mapping --type plan --phase {phase} --plan {plan} --key {issue_key} --url {issue_url} --parent {parent_key} 2>/dev/null || true
     ```
     Increment created counter.
  3. If MCP call fails: increment errors counter, continue.

Display:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► TRACKER SYNC — PHASE {N}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Provider: {provider}

| Action  | Count |
|---------|-------|
| Created | {created} |
| Skipped | {skipped} (already synced) |
| Errors  | {errors} |
```

### Mode: reschedule

**Only available for `"mcp-atlassian"` provider.** Cascades updated dates to all synced Jira issues after phase add/insert operations shift the schedule.

```bash
OPS=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker prepare-reschedule --raw)
```

Parse JSON for `start_date_field` and `operations` array.

Initialize counters: updated=0, skipped=0, errors=0.

For each operation:
- If `action` is `"update"`:
  1. Build `additional_fields`:
     - `{[start_date_field]: operation.start_date, "duedate": operation.due_date}`
  2. Call MCP tool `update_issue`:
     - `issue_key`: from operation `issue_key`
     - `additional_fields`: the built object
  3. If MCP call succeeds: increment updated counter.
  4. If MCP call fails: increment errors counter, continue.

Display:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► TRACKER RESCHEDULE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Provider: mcp-atlassian

| Action  | Count |
|---------|-------|
| Updated | {updated} |
| Errors  | {errors} |

Dates recomputed from milestone start + cumulative phase durations.
```

### Mode: sync-status

```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker sync-status --raw)
```

Display:
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► TRACKER STATUS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Provider: {provider}
Last Synced: {last_synced}

Phases: {synced_phases}/{total_phases} synced
Plans: {plan_count} tracked

{If unsynced phases exist:}
Unsynced phases: {unsynced list}
Run `/grd:sync roadmap` to push them.
```

</process>

<success_criteria>
- [ ] Tracker config loaded and validated
- [ ] Auth status checked before attempting operations
- [ ] Correct subcommand routed based on arguments
- [ ] GitHub path uses direct grd-tools.js sync commands
- [ ] MCP Atlassian path uses prepare → MCP tool → record pattern
- [ ] Date fields (start_date, due_date) included in additional_fields when present
- [ ] Reschedule mode updates synced issues with recomputed dates
- [ ] Results displayed with created/skipped/error counts
- [ ] Clear guidance when tracker not configured
</success_criteria>

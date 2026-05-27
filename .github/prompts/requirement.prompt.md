---
description: Look up requirements by ID, list with filters, or query traceability matrix
argument-hint: <get REQ-ID | list [--phase N] [--status S] | traceability [--phase N] | update-status REQ-ID STATUS>
---

<purpose>
Query and manage project requirements. Look up individual requirements by ID, list requirements with filtering by phase/priority/status/category, view the traceability matrix, or update requirement status. Wraps the requirement CLI commands from grd-tools.js.
</purpose>

<process>

## Step 0: Parse Arguments

Parse `[user-provided arguments]` for the subcommand:

- `get <REQ-ID>` — look up a single requirement
- `list [--phase N] [--priority P] [--status S] [--category C] [--all]` — list requirements with filters
- `traceability [--phase N]` — show traceability matrix
- `update-status <REQ-ID> <STATUS>` — update a requirement's status

If no arguments provided, default to `list` (show all current milestone requirements).

---

## Get: Look Up a Requirement

```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js requirement get <REQ-ID>)
```

Present the requirement details: ID, description, priority, status, phase mapping, and any notes.

---

## List: Filter Requirements

```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js requirement list [--phase N] [--priority P] [--status S] [--category C] [--all])
```

Present as a formatted table:

```
## Requirements

| ID | Description | Priority | Status | Phase |
|----|-------------|----------|--------|-------|
| REQ-01 | ... | P0 | Done | 3 |
| REQ-02 | ... | P1 | In Progress | 5 |
```

If `--all` flag: include completed/deferred requirements.

---

## Traceability: Requirement-to-Phase Mapping

```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js requirement traceability [--phase N])
```

Present the traceability matrix showing which requirements map to which phases, plans, and verification status.

---

## Update Status

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js requirement update-status <REQ-ID> <STATUS>
```

Valid statuses: `Pending`, `In Progress`, `Done`, `Deferred`

After updating, confirm the change:

```
Updated <REQ-ID> status to <STATUS>
```

If `commit_docs` is true in config, commit the change:

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs: update <REQ-ID> status to <STATUS>" --files .planning/REQUIREMENTS.md
```

---

</process>

<success_criteria>
- [ ] Subcommand parsed from arguments
- [ ] Correct CLI command invoked
- [ ] Results presented in readable format
- [ ] Status updates committed if commit_docs enabled
</success_criteria>
</content>

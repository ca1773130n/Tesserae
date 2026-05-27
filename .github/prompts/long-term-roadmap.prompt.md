---
description: Create or display long-term roadmap
argument-hint: [list | add | remove | update | refine | link | unlink | display | init]
---

<purpose>
Manage the flat, ordered list of long-term (LT) milestones. Each LT milestone groups 1+ normal milestones from ROADMAP.md with full traceability. Supports CRUD operations, linking/unlinking normal milestones, and protection of completed work.
</purpose>

<process>

## Step 0: Detect State

Check if LONG-TERM-ROADMAP.md exists:

```bash
PARSED=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap parse --raw 2>&1)
```

Parse `[user-provided arguments]` for subcommand:
- `list` — **List Flow**
- `add` — **Add Flow**
- `remove` — **Remove Flow**
- `update` — **Update Flow**
- `refine` — **Refine Flow**
- `link` — **Link Flow**
- `unlink` — **Unlink Flow**
- `display` — **Display Flow** (default when no subcommand)
- `init` — **Init Flow**
- No subcommand — **Display or Init Flow**

---

## Display or Init Flow

### If LONG-TERM-ROADMAP.md exists:

**Display the current roadmap:**

```bash
DISPLAY=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap display --raw)
```

Present the formatted output to the user, then offer actions:

```
---

## Actions

- `/grd:long-term-roadmap add` — add a new LT milestone
- `/grd:long-term-roadmap update` — update a milestone's name, goal, or status
- `/grd:long-term-roadmap link` — link a normal milestone to an LT milestone
- `/grd:long-term-roadmap refine` — discuss and refine a milestone
- `/grd:new-milestone` — start executing the active milestone

---
```

Done.

### If no LONG-TERM-ROADMAP.md:

**Auto-initialize from existing ROADMAP.md:**

```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap init --project "<project_name>" --raw)
```

If ROADMAP.md exists, this auto-groups all existing milestones into LT-1. Write the result to `.planning/LONG-TERM-ROADMAP.md`.

If no ROADMAP.md either, ask the user to create their first milestone with `/grd:init`.

**Then offer to add more LT milestones:**

Use AskUserQuestion:
- header: "Milestones"
- question: "Would you like to add more long-term milestones beyond the auto-grouped LT-1?"
- options:
  - "Yes" — Ask for milestone details
  - "No" — Keep LT-1 only for now

If "Yes", ask inline (freeform) for each milestone's name and goal. Use the `add` subcommand for each:

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap add --name "<name>" --goal "<goal>"
```

**Validate and commit:**

```bash
VALID=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap validate --raw)
```

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap history \
  --action "Initial roadmap" \
  --details "Created <N> LT milestones"
```

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs: create long-term roadmap" --files .planning/LONG-TERM-ROADMAP.md
```

---

## List Flow

```bash
LIST=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap list --raw)
```

Present the list to the user.

---

## Add Flow

Ask inline (freeform) for the milestone name and goal (if not provided as arguments).

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap add --name "<name>" --goal "<goal>"
```

Write the returned content to `.planning/LONG-TERM-ROADMAP.md`. Log history and commit.

---

## Remove Flow

Parse `[user-provided arguments]` for `--id LT-N`.

```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap remove --id "<LT-N>")
```

**Protection:** The command refuses if the LT milestone has any completed (shipped) normal milestones. Present the error to the user if so.

If successful, write the returned content and commit.

---

## Update Flow

Parse `[user-provided arguments]` for `--id LT-N` and optional `--name`, `--goal`, `--status`.

```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap update \
  --id "<LT-N>" [--name "<name>"] [--goal "<goal>"] [--status "<status>"])
```

Valid statuses: `completed`, `active`, `planned`. Write result and commit.

---

## Refine Flow

Parse `[user-provided arguments]` for `--id LT-N`.

```bash
CONTEXT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap refine --id "<LT-N>" --raw)
```

Present the milestone context to the user. Discuss refinements. After gathering updates, use the `update` subcommand to apply changes. Log history and commit.

---

## Link Flow

Parse `[user-provided arguments]` for `--id LT-N` and `--version vX.Y.Z`.

```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap link --id "<LT-N>" --version "<version>")
```

Write result and commit.

---

## Unlink Flow

Parse `[user-provided arguments]` for `--id LT-N` and `--version vX.Y.Z`.

```bash
RESULT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap unlink --id "<LT-N>" --version "<version>")
```

**Protection:** Refuses to unlink shipped versions. Present error if so. Write result and commit.

---

</process>

<success_criteria>
- [ ] Init creates LONG-TERM-ROADMAP.md from existing ROADMAP.md milestones
- [ ] Display shows formatted LT milestone list with status icons
- [ ] Add/Remove/Update CRUD operations work with protection rules
- [ ] Link/Unlink manages normal milestone associations
- [ ] Shipped milestones cannot be removed or unlinked
- [ ] All changes committed with history logged
- [ ] Validation passes after any modification
</success_criteria>

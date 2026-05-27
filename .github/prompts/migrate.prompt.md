---
description: Migrate legacy flat .planning/ layout to milestone-scoped hierarchy
---

<purpose>
Migrate a legacy flat `.planning/` layout (phases/, research/, todos/, quick/ at root) to the milestone-scoped hierarchy (`.planning/milestones/{milestone}/...`). Note: `codebase/` is project-level and stays at `.planning/codebase/`. Handles both trivial cases (standard dirs) via the deterministic `migrate-dirs` CLI and complex cases (orphan files, flat milestone files, legacy phase dirs) via the `grd-migrator` agent.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>
**Step 1: Load context**

```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js state load)
```

Parse JSON for: `milestone`, `planning_exists`.

**If `planning_exists` is false:** Error — no `.planning/` directory found. Nothing to migrate.

---

**Step 2: Scan for migration needs**

Scan `.planning/` for items that need migration:

**Trivial items** (handled by `migrate-dirs` CLI):
- Old-style root dirs: `phases/`, `research/`, `todos/`, `quick/`
- Note: `codebase/` is project-level and stays at `.planning/codebase/` — not migrated

**Complex items** (need agent):
- Orphan files at `.planning/` root that aren't standard (not STATE.md, ROADMAP.md, PROJECT.md, BASELINE.md, PRODUCT-QUALITY.md, REQUIREMENTS.md, config.json, TRACKER.md, LONG-TERM-ROADMAP.md)
- Flat milestone files in `milestones/` (e.g., `v0.X-ROADMAP.md`, `v0.X-REQUIREMENTS.md`)
- Legacy `v0.X-phases/` dirs in `milestones/`

Classify each item as `trivial` or `complex`.

---

**Step 3: Trivial-only path**

If only trivial items exist (or nothing to migrate):

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js migrate-dirs --raw
```

If output shows `already_migrated: true` or all dirs moved successfully, report success and exit.

---

**Step 4: Complex items — present migration plan**

If complex items were found, build a migration plan table and present to user:

```
AskUserQuestion(
  header: "Migration",
  question: "The following items need migration. Proceed?",
  options: [
    { label: "Yes, migrate all", description: "Run trivial migration + spawn agent for complex items" },
    { label: "Trivial only", description: "Only migrate standard directories, skip complex items" },
    { label: "Cancel", description: "Don't migrate anything" }
  ]
)
```

Display table of items:

| Item | Type | Action |
|------|------|--------|
| phases/ | trivial | Move to milestones/{milestone}/phases/ |
| milestones/v0.1-ROADMAP.md | complex | Move to milestones/v0.1/ROADMAP.md |
| ... | ... | ... |

---

**Step 5: Execute trivial migration**

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js migrate-dirs --raw
```

---

**Step 6: Spawn grd-migrator for complex items**

If user chose to migrate complex items:

```
Task(
  prompt="
<migration_context>

**Milestone:** ${milestone}
**Complex items to migrate:**
${complex_items_list}

**Project State:**
@.planning/STATE.md

</migration_context>

<constraints>
- Move flat milestone files to their proper milestone subdirectories
- Move legacy phase dirs to proper milestone structure
- For orphan docs: read content, infer milestone from content, flag low-confidence for user decision
- Non-standard files: flag for user (keep/move/delete)
- Commit atomically after all moves
- Do NOT modify file contents — only move files
</constraints>
",
  subagent_type="grd:grd-migrator",
  description="Migrate complex .planning/ items"
)
```

---

**Step 7: Validate and commit**

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js validate consistency
```

If validation passes, commit migration results:

```bash
git add .planning/
git commit -m "chore: migrate .planning/ to milestone-scoped hierarchy"
```

Display completion output:
```
---

GRD > MIGRATION COMPLETE

Milestone: ${milestone}
Trivial items: ${trivial_count} migrated
Complex items: ${complex_count} migrated

Validation: PASSED

---
```

</process>

<success_criteria>
- [ ] `.planning/` scanned for migration needs
- [ ] Trivial dirs migrated via `migrate-dirs` CLI
- [ ] Complex items handled by `grd-migrator` agent (if any)
- [ ] `validate consistency` passes after migration
- [ ] Changes committed atomically
</success_criteria>

---
description: Capture an idea or task as a structured todo for later work
argument-hint: <brief description of the todo>
---

<purpose>
Capture an idea, task, or issue that surfaces during a GRD session as a structured todo for later work. Enables "thought -> capture -> continue" flow without losing context.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>

<step name="init_context">
```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init todos)
```

Extract from init JSON: `commit_docs`, `date`, `timestamp`, `todo_count`, `todos`, `pending_dir`, `todos_dir`, `todos_dir_exists`.

```bash
mkdir -p ${todos_dir}/pending ${todos_dir}/done
```
</step>

<step name="extract_content">
**With arguments:** Use as the title/focus.
**Without arguments:** Analyze recent conversation to extract the specific problem, idea, or task.

Formulate: `title`, `problem`, `solution`, `files`.
</step>

<step name="infer_area">
Infer area from file paths (api, ui, auth, database, testing, docs, planning, tooling, general).
</step>

<step name="check_duplicates">
```bash
grep -l -i "[key words from title]" ${todos_dir}/pending/*.md 2>/dev/null
```

If potential duplicate found: offer Skip/Replace/Add anyway.
</step>

<step name="create_file">
```bash
slug=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js generate-slug "$title" --raw)
```

Write to `${todos_dir}/pending/-.md` with frontmatter and Problem/Solution sections.
</step>

<step name="git_commit">
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs: capture todo - [title]" --files ${todos_dir}/pending/[filename] .planning/STATE.md
```
</step>

<step name="confirm">
```
Todo saved: ${todos_dir}/pending/[filename]

  [title]
  Area: [area]
  Files: [count] referenced

---

Would you like to:

1. Continue with current work
2. Add another todo
3. View all todos (/grd:check-todos)
```
</step>

</process>

<success_criteria>
- [ ] Todo file created with valid frontmatter
- [ ] Problem section has enough context for future Claude
- [ ] No duplicates (checked and resolved)
- [ ] STATE.md updated if exists
- [ ] Todo and state committed to git
</success_criteria>

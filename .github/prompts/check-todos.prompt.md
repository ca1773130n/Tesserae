---
description: List pending todos, view details, and route to appropriate action
---

<purpose>
List all pending todos, allow selection, load full context for the selected todo, and route to appropriate action.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>

<step name="init_context">
```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init todos)
```

Extract from init JSON: `todo_count`, `todos`, `pending_dir`, `todos_dir`.

If `todo_count` is 0:
```
No pending todos.

Todos are captured during work sessions with /grd:add-todo.

---

Would you like to:

1. Continue with current phase (/grd:progress)
2. Add a todo now (/grd:add-todo)
```
Exit.
</step>

<step name="list_todos">
Display as numbered list with title, area, and relative age.

```
Pending Todos:

1. Add auth token refresh (api, 2d ago)
2. Fix modal z-index issue (ui, 1d ago)

---

Reply with a number to view details, or `q` to exit.
```
</step>

<step name="handle_selection">
Wait for user to reply with a number. Load selected todo, display full context.
</step>

<step name="offer_actions">
**If todo maps to a roadmap phase:** Offer Work on it now / Add to phase plan / Brainstorm / Put it back.

**If no roadmap match:** Offer Work on it now / Create a phase / Brainstorm / Put it back.
</step>

<step name="execute_action">
**Work on it now:** Move to done/, update STATE.md, begin work.
**Add to phase plan:** Note reference, keep in pending.
**Create a phase:** Display `/grd:add-phase [description]`.
**Brainstorm approach:** Start discussion.
**Put it back:** Return to list.
</step>

<step name="git_commit">
If todo was moved to done/:
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs: start work on todo - [title]" --files ${todos_dir}/done/[filename] .planning/STATE.md
```
</step>

</process>

<success_criteria>
- [ ] All pending todos listed with title, area, age
- [ ] Selected todo's full context loaded
- [ ] Appropriate actions offered
- [ ] Selected action executed
- [ ] STATE.md updated if todo count changed
- [ ] Changes committed to git (if todo moved to done/)
</success_criteria>

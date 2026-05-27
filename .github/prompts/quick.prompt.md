---
description: Execute small ad-hoc tasks with GRD guarantees, skipping optional agents
argument-hint: <task description>
---

<purpose>
Execute small, ad-hoc tasks with GRD guarantees (atomic commits, STATE.md tracking) while skipping optional agents (research, plan-checker, verifier). Quick mode spawns grd-planner (quick mode) + grd-executor(s), tracks tasks in `${quick_dir}/`, and updates STATE.md's "Quick Tasks Completed" table.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>
**Step 1: Get task description**

Prompt user interactively for the task description:

```
AskUserQuestion(
  header: "Quick Task",
  question: "What do you want to do?",
  followUp: null
)
```

Store response as `$DESCRIPTION`.

If empty, re-prompt: "Please provide a task description."

---

**Step 2: Initialize**

```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init quick "$DESCRIPTION")
```

Parse JSON for: `planner_model`, `executor_model`, `commit_docs`, `next_num`, `slug`, `date`, `timestamp`, `quick_dir`, `task_dir`, `roadmap_exists`, `planning_exists`.

**If `roadmap_exists` is false:** Error — Quick mode requires an active project with ROADMAP.md. Run `/grd:init` first.

---

**Step 3: Create task directory**

```bash
mkdir -p "${task_dir}"
```

---

**Step 4: Create quick task directory**

```bash
QUICK_DIR="${quick_dir}/${next_num}-${slug}"
mkdir -p "$QUICK_DIR"
```

Report to user:
```
Creating quick task ${next_num}: ${DESCRIPTION}
Directory: ${QUICK_DIR}
```

---

**Step 5: Spawn planner (quick mode)**

```
Task(
  prompt="
<planning_context>

**Mode:** quick
**Directory:** ${QUICK_DIR}
**Description:** ${DESCRIPTION}

**Project State:**
@.planning/STATE.md

</planning_context>

<constraints>
- Create a SINGLE plan with 1-3 focused tasks
- Quick tasks should be atomic and self-contained
- No research phase, no checker phase
- Target ~30% context usage (simple, focused)
</constraints>

<output>
Write plan to: ${QUICK_DIR}/${next_num}-PLAN.md
Return: ## PLANNING COMPLETE with plan path
</output>
",
  subagent_type="grd:grd-planner",
  model="{planner_model}",
  description="Quick plan: ${DESCRIPTION}"
)
```

---

**Step 6: Spawn executor**

```
Task(
  prompt="
Execute quick task ${next_num}.

Plan: @${QUICK_DIR}/${next_num}-PLAN.md
Project state: @.planning/STATE.md

<constraints>
- Execute all tasks in the plan
- Commit each task atomically
- Create summary at: ${QUICK_DIR}/${next_num}-SUMMARY.md
- Do NOT update ROADMAP.md (quick tasks are separate from planned phases)
</constraints>
",
  subagent_type="grd:grd-executor",
  model="{executor_model}",
  description="Execute: ${DESCRIPTION}"
)
```

---

**Step 7: Update STATE.md**

Update STATE.md with quick task completion record. If "Quick Tasks Completed" section doesn't exist, create it.

---

**Step 8: Final commit and completion**

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs(quick-${next_num}): ${DESCRIPTION}" --files ${QUICK_DIR}/${next_num}-PLAN.md ${QUICK_DIR}/${next_num}-SUMMARY.md .planning/STATE.md
```

Display completion output:
```
---

GRD > QUICK TASK COMPLETE

Quick Task ${next_num}: ${DESCRIPTION}

Summary: ${QUICK_DIR}/${next_num}-SUMMARY.md
Commit: ${commit_hash}

---

Ready for next task: /grd:quick
```

</process>

<success_criteria>
- [ ] ROADMAP.md validation passes
- [ ] User provides task description
- [ ] Directory created at `${quick_dir}/NNN-slug/`
- [ ] `${next_num}-PLAN.md` created by planner
- [ ] `${next_num}-SUMMARY.md` created by executor
- [ ] STATE.md updated with quick task row
- [ ] Artifacts committed
</success_criteria>

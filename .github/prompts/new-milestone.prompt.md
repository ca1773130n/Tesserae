---
description: Start a new milestone cycle with research, requirements, and roadmap
---

<purpose>
Start a new milestone cycle for an existing project. Loads project context, gathers milestone goals (from MILESTONE-CONTEXT.md or conversation), updates PROJECT.md and STATE.md, optionally runs parallel research, defines scoped requirements with REQ-IDs, spawns the roadmapper to create phased execution plan, and commits all artifacts. Brownfield equivalent of new-project.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>

## 1. Load Context

- Read PROJECT.md (existing project, validated requirements, decisions)
- Read MILESTONES.md (what shipped previously)
- Read STATE.md (pending todos, blockers)
- Check for MILESTONE-CONTEXT.md (from /grd:discuss-milestone)

## 2. Gather Milestone Goals

**If MILESTONE-CONTEXT.md exists:** Use features and scope, present for confirmation.
**If no context file:** Present what shipped, ask: "What do you want to build next?"

## 3. Determine Milestone Version

Parse last version from MILESTONES.md. Suggest next version. Confirm with user.

## 4. Update PROJECT.md

Add Current Milestone section with goal and target features. Update Active requirements.

## 5. Update STATE.md

Reset position to "Not started (defining requirements)".

## 6. Cleanup and Commit

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs: start milestone v[X.Y] [Name]" --files .planning/PROJECT.md .planning/STATE.md
```

## 7. Load Context and Resolve Models

```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init new-milestone)
```

Extract: `researcher_model`, `synthesizer_model`, `roadmapper_model`, `commit_docs`, `research_enabled`, `current_milestone`, `suggested_start_phase`, `research_dir`.

If `gate_failed` is true, stop and report the gate errors to the user before proceeding.

## 8. Research Decision

AskUserQuestion: "Research the domain ecosystem for new features before defining requirements?"

**If "Research first":** Spawn 4 parallel grd-project-researcher agents (Stack, Features, Architecture, Pitfalls), then synthesizer. Use subsequent milestone context (focus on NEW features, not existing system).

```bash
mkdir -p ${research_dir}
```

**If "Skip research":** Continue to Step 9.

## 9. Define Requirements

Read PROJECT.md, existing validated requirements. Present features by category via research or conversation. Scope each category via AskUserQuestion.

Generate REQUIREMENTS.md with REQ-IDs continuing from existing numbering.

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs: define milestone v[X.Y] requirements" --files .planning/REQUIREMENTS.md
```

## 10. Create Roadmap

Start phase numbering from `suggested_start_phase` (computed by `init new-milestone` from archived and current phase directories).

```
Task(prompt="
<planning_context>
@.planning/PROJECT.md
@.planning/REQUIREMENTS.md
@${research_dir}/SUMMARY.md (if exists)
@.planning/config.json
@.planning/MILESTONES.md
</planning_context>

<instructions>
Create roadmap for milestone v[X.Y]:
1. Start phase numbering from {suggested_start_phase}
2. Derive phases from THIS MILESTONE's requirements only
3. Map every requirement to exactly one phase
4. Write files immediately (ROADMAP.md, STATE.md, update REQUIREMENTS.md traceability)
5. Return ROADMAP CREATED with summary
</instructions>
", subagent_type="grd:grd-roadmapper", model="{roadmapper_model}", description="Create roadmap")
```

Handle return (BLOCKED or CREATED), present inline, get approval, commit.

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs: create milestone v[X.Y] roadmap ([N] phases)" --files .planning/ROADMAP.md .planning/STATE.md .planning/REQUIREMENTS.md
```

## 10.5. Link to Long-Term Roadmap

Check if LONG-TERM-ROADMAP.md exists:

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap list --raw 2>/dev/null
```

**If LT roadmap exists:**

Find the active LT milestone and offer to link:

Use AskUserQuestion:
- header: "LT Link"
- question: "Link this milestone to an LT milestone?"
- options:
  - "Link to [active LT milestone name]" — Associate v[X.Y] with the active LT milestone
  - "Skip" — Don't link to long-term roadmap

**If "Link":**
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap link --id [LT-N] --version v[X.Y]
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs: link v[X.Y] to [LT-N]" --files .planning/LONG-TERM-ROADMAP.md
```

**If no LT roadmap exists:** Skip silently.

## 11. Done

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► MILESTONE INITIALIZED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Milestone v[X.Y]: [Name]**

**[N] phases** | **[X] requirements** | Ready to build

## Next Up

**Phase [N]: [Phase Name]** — [Goal]

`/grd:discuss-phase [N]` — gather context and clarify approach

<sub>`/clear` first -> fresh context window</sub>

Also: `/grd:plan-phase [N]` — skip discussion, plan directly
```

</process>

<success_criteria>
- [ ] PROJECT.md updated with Current Milestone section
- [ ] STATE.md reset for new milestone
- [ ] Research completed (if selected) — 4 parallel agents, milestone-aware
- [ ] REQUIREMENTS.md created with REQ-IDs
- [ ] grd-roadmapper spawned with phase numbering context
- [ ] ROADMAP.md phases continue from previous milestone
- [ ] All commits made
- [ ] LT roadmap link offered (if LONG-TERM-ROADMAP.md exists)
- [ ] User knows next step: `/grd:discuss-phase [N]`
</success_criteria>

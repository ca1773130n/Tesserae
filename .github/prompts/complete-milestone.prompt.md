---
description: Mark a milestone version as complete and archive it
argument-hint: [--name milestone-name]
---

<purpose>
Mark a shipped version as complete. First runs an automated milestone audit (cross-phase integration checks, requirements coverage, tech debt aggregation), then creates historical record in MILESTONES.md, performs full PROJECT.md evolution review, reorganizes ROADMAP.md with milestone groupings, and tags the release in git.
</purpose>

<required_reading>
1. templates/milestone.md
2. templates/milestone-archive.md
3. `.planning/ROADMAP.md`
4. `.planning/REQUIREMENTS.md`
5. `.planning/PROJECT.md`
</required_reading>

<process>

<step name="init_context" priority="first">
```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init milestone-op)
```

Parse JSON for: `phases_dir`, `commit_docs`, `milestone_version`, `milestone_name`.
</step>

<step name="audit_milestone">
**Run milestone audit as first step of completion.**

This step replaces the standalone `/grd:audit-milestone` command by integrating it directly.

1. Determine milestone scope from init context
2. Read all phase VERIFICATION.md files
3. Spawn integration checker for cross-phase wiring:

```
Task(
  prompt="Check cross-phase integration and E2E flows.
Phases: {phase_dirs}
Phase exports: {from SUMMARYs}
API routes: {routes created}
Verify cross-phase wiring and E2E user flows.",
  subagent_type="grd:grd-integration-checker",
  model="{integration_checker_model}"
)
```

4. Aggregate results: phase-level gaps/tech debt + integration checker report
5. Check requirements coverage: satisfied | partial | unsatisfied

**Route by audit result:**

- **If passed:** Continue to `verify_readiness` step
- **If gaps_found:**
  Present gap summary. Offer:
  - "Continue anyway" — proceed to verify_readiness
  - "Fix first" — offer `/grd:plan-milestone-gaps`, stop completion
- **If tech_debt:**
  Present tech debt summary. Offer:
  - "Accept debt" — proceed to verify_readiness
  - "Plan cleanup" — offer cleanup phase, stop completion

<if mode="yolo">
Auto-approve: if passed or tech_debt_only, proceed. If gaps_found, still proceed but log gaps.
</if>
</step>

<step name="verify_readiness">
```bash
ROADMAP=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js roadmap analyze)
```

Verify all phases complete (`disk_status === 'complete'`), `progress_percent` should be 100%.

Present milestone summary with phase/plan breakdown.

<if mode="yolo">
Auto-approve: proceed to gather_stats.
</if>

<if mode="interactive">
Confirm: "Ready to mark this milestone as shipped? (yes / wait / adjust scope)"
</if>
</step>

<step name="gather_stats">
Calculate milestone statistics: phases, plans, tasks, files modified, LOC, timeline, git range.
</step>

<step name="extract_accomplishments">
```bash
for summary in ${phases_dir}/*-*/*-SUMMARY.md; do
  node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js summary-extract "$summary" --fields one_liner | jq -r '.one_liner'
done
```

Extract 4-6 key accomplishments.
</step>

<step name="evolve_project_full_review">
Full PROJECT.md evolution review: "What This Is" accuracy, Core Value check, Requirements audit (move shipped to Validated, add new to Active), Context update, Key Decisions audit, Constraints check.
</step>

<step name="archive_milestone">
```bash
ARCHIVE=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js milestone complete "v[X.Y]" --name "[Milestone Name]")
```

The CLI handles: creating milestones directory, archiving ROADMAP.md and REQUIREMENTS.md, **archiving all phase directories** to `.planning/milestones/v[X.Y]-phases/`, creating/appending MILESTONES.md entry, updating STATE.md.

After archival, handle: reorganize ROADMAP.md, full PROJECT.md evolution, delete originals.
</step>

<step name="handle_branches">
The `milestone complete` CLI command automatically merges the milestone branch into the base branch (e.g., main) when `branching_strategy` is not `"none"`.

Check the `git_merge` field from the `ARCHIVE` result:

**If `git_merge.merged: true`:**
```
Milestone branch `${git_merge.milestone_branch}` merged into `${git_merge.base_branch}` and deleted.
```

**If `git_merge.error`:**
```
## Merge Conflict

The milestone branch could not be automatically merged into ${git_merge.base_branch}.
Resolve manually:
1. `git checkout ${git_merge.base_branch}`
2. `git merge ${git_merge.milestone_branch}`
3. Resolve conflicts, then `git commit`
4. `git branch -d ${git_merge.milestone_branch}`
```

**If `git_merge.skipped`:**
```
Milestone branch merge skipped: ${git_merge.reason}
```

**If no `git_merge` field:** Branching strategy is `"none"`, no branch handling needed.
</step>

<step name="bump_versions">
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js version bump v[X.Y]
```

Bump VERSION, package.json, and .claude-plugin/plugin.json to match the milestone version.
</step>

<step name="git_tag">
```bash
git tag -a v[X.Y] -m "v[X.Y] [Name] ..."
```
Ask: "Push tag to remote? (y/n)"
</step>

<step name="git_commit_milestone">
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "chore: complete v[X.Y] milestone" --files .planning/milestones/v[X.Y]-ROADMAP.md .planning/milestones/v[X.Y]-REQUIREMENTS.md .planning/MILESTONES.md .planning/PROJECT.md .planning/STATE.md VERSION package.json .claude-plugin/plugin.json
```
</step>

<step name="update_lt_roadmap">
Check if LONG-TERM-ROADMAP.md exists and update LT milestone status:

```bash
LT_LIST=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap list --raw 2>/dev/null || true)
```

**If LT roadmap exists:**
1. Check if v[X.Y] is linked to any LT milestone
2. If linked, check if all normal milestones in that LT milestone are now shipped
3. If all shipped, update LT milestone status to `completed`:
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap update --id [LT-N] --status completed
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap history --action "Completed LT-N" --details "All normal milestones shipped"
```
4. If the next LT milestone exists and is `planned`, update to `active`:
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js long-term-roadmap update --id [LT-N+1] --status active
```
5. Commit changes:
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs: update LT roadmap after v[X.Y] completion" --files .planning/LONG-TERM-ROADMAP.md
```
</step>

<step name="offer_next">
```
Milestone v[X.Y] [Name] complete

---

## Next Up

**Start Next Milestone** — questioning -> research -> requirements -> roadmap

`/grd:new-milestone`

<sub>`/clear` first -> fresh context window</sub>

---
```
</step>

</process>

<success_criteria>
- [ ] Milestone audit completed (integration checks, requirements coverage)
- [ ] MILESTONES.md entry created
- [ ] PROJECT.md full evolution review completed
- [ ] ROADMAP.md reorganized with milestone grouping
- [ ] Archives created
- [ ] Phase directories archived to .planning/milestones/v[X.Y]-phases/
- [ ] ${phases_dir}/ directory is empty
- [ ] REQUIREMENTS.md deleted (fresh for next milestone)
- [ ] STATE.md updated
- [ ] Git tag created
- [ ] Milestone commit made
- [ ] LT roadmap status updated (if applicable)
- [ ] User knows next step (/grd:new-milestone)
</success_criteria>

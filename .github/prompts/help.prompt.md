---
description: Show available GRD commands and usage guide
argument-hint: [command name for detailed help]
---

<purpose>
Complete help reference for all GRD (Get Research Done) commands. Organized by category
with usage examples, workflow diagrams, and quick-start guides. Serves as both a
reference card and onboarding document for the GRD R&D workflow system.
</purpose>

<context>
CLAUDE.md rules: @CLAUDE.md

GRD is a research-grade project automation system for Claude Code. It extends traditional
software development workflows with research survey, paper analysis, tiered evaluation,
iteration loops, and autonomous operation modes.
</context>

<process>

## Step 0: PARSE ARGUMENTS

1. **Check if specific command help requested**: `[user-provided arguments]`
   - If a command name is provided (e.g., "survey"): show detailed help for that command only
   - If empty: show full help listing

---

## Step 1: DISPLAY MAIN HELP (if no specific command requested)

```
╔══════════════════════════════════════════════════════════════════════╗
║                                                                     ║
║   ██████  ██████  ██████                                            ║
║  ██       ██   ██ ██   ██                                           ║
║  ██   ██  ██████  ██   ██    Get Research Done                      ║
║  ██   ██  ██   ██ ██   ██    v{version} — R&D Workflow Automation   ║
║   ██████  ██   ██ ██████                                            ║
║                                                                     ║
╚══════════════════════════════════════════════════════════════════════╝

RESEARCH — Survey, analyze, and compare state-of-the-art
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /grd:survey <topic>           Scan papers, repos, benchmarks for a
                                research topic. Updates LANDSCAPE.md.

  /grd:deep-dive <paper>        Deep analysis of a specific paper —
                                method, code, limitations, verdict.

  /grd:compare-methods [m1,m2]  Compare methods from LANDSCAPE.md.
                                Build performance/complexity matrix.

  /grd:feasibility <method>     Analyze paper-to-production gap.
                                Dependencies, scale, licensing, effort.


PLANNING — Strategic and tactical planning
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /grd:init                      Initialize new R&D project with deep
                                  context gathering and research setup.

  /grd:product-plan <goals>     Product-level roadmap from research
                                findings. Gap analysis, phase breakdown.

  /grd:plan-phase <N>           Create detailed execution plan for a
                                phase with research context injection.
                                Flags: --research-only (research only),
                                       --eval-only (eval plan only).

  /grd:discuss-phase <N>        Gather phase context through adaptive
                                questioning before planning.


EXECUTION — Build and implement
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /grd:execute-phase <N>        Execute all plans in a phase with
                                wave-based parallelization, atomic commits.

  /grd:quick <task>             Quick task with GRD guarantees but
                                skip optional agents. Fast execution.


EVALUATION — Measure, verify, and iterate
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /grd:eval-report <N>          Run evaluations and report results.
                                Compare against baselines and targets.

  /grd:assess-baseline [scope]  Assess current code quality and
                                establish performance baseline.

  /grd:iterate <N>              Iteration loop when eval misses targets.
                                Survey alternatives, adjust, or accept.


VERIFICATION — Quality gates and validation
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /grd:verify-phase <N>         Verify phase completion with tiered
                                evaluation and quantitative metrics.

  /grd:verify-work              Validate built features through
                                conversational UAT with metrics.


NAVIGATION — Project status and configuration
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /grd:progress                 Check project progress with research
                                metrics and route to next action.
    progress dashboard          Full TUI tree view of milestones, phases,
                                and plans with progress bars.
    progress health             Project health: blockers, velocity, stale
                                phases, risk register.
    progress phase <N>          Detailed drill-down for a single phase
                                with plans, metrics, and artifacts.

  /grd:help [command]           Show this help (or detailed command help).

  /grd:settings                 Configure all workflow settings interactively.
                                Subcommands for quick toggles:
                                  settings yolo [on|off] — autonomous mode
                                  settings profile <p>   — model profile
                                  settings ceremony <l>  — ceremony level


PROJECT MANAGEMENT — Phase and task operations
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /grd:add-phase <name>         Add phase to end of current milestone.

  /grd:insert-phase <N.M> <nm>  Insert urgent work between existing
                                phases as decimal phase.

  /grd:remove-phase <N>         Remove future phase and renumber.

  /grd:add-todo <description>   Capture idea or task as todo from
                                current conversation context.

  /grd:check-todos              List pending todos, select one to work on.


LIFECYCLE — Session and milestone management
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /grd:pause-work               Create context handoff when pausing
                                work mid-phase. Saves full state.

  /grd:resume-work              Resume work from previous session with
                                full context restoration.

  /grd:complete-milestone       Audit, archive, and tag a completed
                                milestone. Runs integration checks first.

  /grd:new-milestone            Start a new milestone cycle. Update
                                PROJECT.md and route to requirements.


INTEGRATION — Issue tracker sync
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /grd:sync [roadmap|phase N]   Sync GRD state to issue tracker.
                                Push phases and plans to GitHub/Jira.

  /grd:tracker-setup            Configure issue tracker integration.
                                Supports GitHub Issues and MCP Atlassian.


TOOLS — Analysis and debugging utilities
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /grd:map-codebase             Analyze codebase with parallel mapper
                                agents. Produce codebase analysis docs.

  /grd:debug                    Systematic debugging with persistent
                                state across context resets.

  /grd:list-phase-assumptions   Surface Claude's assumptions about a
                                phase approach before planning.

  /grd:plan-milestone-gaps      Create phases to close all gaps
                                identified by milestone audit.


UPDATE — Self-update and patch management
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  /grd:update                   Check for updates, display changelog,
                                back up modifications, pull latest.

  /grd:reapply-patches          Restore local modifications after a
                                GRD update with conflict resolution.


TOOLING — grd-tools.js CLI reference
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  node bin/grd-tools.js <command> [args]

  State:      state load | get | patch | advance-plan | record-metric | add-decision
  Verify:     verify plan-structure | phase-completeness | references | commits | artifacts | key-links
  Phase:      phase add | insert | remove | complete
  Roadmap:    roadmap get-phase | analyze
  Scaffold:   scaffold context | uat | verification | phase-dir | research-dir | eval | baseline
  Progress:   progress json | table | bar
  Parsers:    phase-plan-index | state-snapshot | summary-extract | history-digest
  Frontmatter: frontmatter get | set | merge | validate
  Tracker:    tracker get-config | sync-roadmap | sync-phase | update-status | add-comment | prepare-*
  Validate:   validate consistency
  Milestone:  milestone complete
```

---

## Step 2: DISPLAY WORKFLOW DIAGRAM

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                         GRD R&D WORKFLOW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  RESEARCH PHASE (understand the problem space)
  ─────────────────────────────────────────────

    Idea/Goal
        │
        ▼
    ┌──────────┐     ┌────────────┐     ┌─────────────┐
    │  survey   │────►│ deep-dive  │────►│  compare-   │
    │          │     │            │     │  methods    │
    └──────────┘     └────────────┘     └──────┬──────┘
                                               │
                                               ▼
                                        ┌─────────────┐
                                        │ feasibility  │
                                        └──────┬──────┘
                                               │
  PLANNING PHASE (design the solution)         │
  ────────────────────────────────────         │
                                               ▼
    ┌──────────────┐     ┌──────────┐     ┌──────────┐
    │assess-baseline│────►│ product- │────►│  plan-   │
    │              │     │  plan    │     │  phase   │
    └──────────────┘     └──────────┘     └────┬─────┘
                                               │
  EXECUTION PHASE (build it)                   │
  ──────────────────────────                   │
                                               ▼
                                        ┌──────────────┐
                                        │execute-phase │
                                        └──────┬───────┘
                                               │
  EVALUATION PHASE (measure it)                │
  ─────────────────────────────                │
                                               ▼
    ┌──────────┐     ┌──────────┐     ┌──────────────┐
    │plan-phase│────►│eval-report│────►│   iterate?   │
    │--eval-only     │            │     │              │
    └──────────┘     └──────────┘     └──────┬───────┘
                                             │
                          ┌──────────────────┤
                          │ targets met      │ targets missed
                          ▼                  ▼
                    ┌──────────┐      ┌──────────┐
                    │ verify-  │      │  iterate  │──── loop back to
                    │  phase   │      │          │     survey/plan/exec
                    └────┬─────┘      └──────────┘
                         │
  INTEGRATION (ship it)  │
  ─────────────────────  │
                         ▼
                    ┌──────────┐     ┌──────────┐
                    │ next     │────►│ complete- │
                    │ phase    │     │ milestone │
                    └──────────┘     └──────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Step 3: DISPLAY QUICK START GUIDE

```
QUICK START
━━━━━━━━━━━

  New R&D project:
    1. /grd:init
    2. /grd:survey "your research topic"
    3. /grd:deep-dive "promising paper"
    4. /grd:feasibility "chosen method"
    5. /grd:assess-baseline
    6. /grd:product-plan "your product goal"
    7. /grd:plan-phase 1
    8. /grd:execute-phase 1
    9. /grd:eval-report 1
   10. /grd:iterate 1  (if needed)

  Quick task (skip research):
    /grd:quick "implement feature X"

  Autonomous batch run:
    /grd:settings yolo on
    /grd:execute-phase 1
    /grd:eval-report 1

  Resume after break:
    /grd:resume-work

  Check where you are:
    /grd:progress
```

---

## Step 4: DISPLAY KEY FILES REFERENCE

```
KEY FILES
━━━━━━━━━

  .planning/
  ├── PROJECT.md              Project definition and goals
  ├── ROADMAP.md              Phase-level roadmap
  ├── BASELINE.md             Current performance baseline
  ├── BENCHMARKS.md           Historical benchmark tracking
  ├── KNOWHOW.md              Production engineering knowledge
  ├── config.json             GRD configuration
  ├── yolo-decisions.log      Autonomous mode decision log
  │
  ├── research/
  │   ├── LANDSCAPE.md        State-of-the-art survey
  │   ├── PAPERS.md           Paper index
  │   ├── COMPARISON-*.md     Method comparison matrices
  │   └── deep-dives/         Individual paper analyses
  │       ├── {paper}.md
  │       └── {paper}-feasibility.md
  │
  ├── phases/
  │   └── {N}-{name}/
  │       ├── PLAN.md         Phase execution plan
  │       └── EVAL.md         Evaluation plan and results
  │
  └── codebase/               Codebase analysis docs
```

---

## Step 5: SPECIFIC COMMAND HELP (if requested)

If `[user-provided arguments]` matches a command name, display detailed help:

```
/grd:{command}
━━━━━━━━━━━━━━

  Description: {from plugin.json}
  Arguments:   {argument-hint}

  What it does:
    {detailed explanation}

  Files it reads:
    {list of input files}

  Files it writes:
    {list of output files}

  Agent spawned:
    {agent name or "none — orchestrator only"}

  Research gates:
    {which gates apply to this command}

  Related commands:
    {commands typically run before/after}

  Example:
    /grd:{command} {example_args}
```

</process>

<output>
**DISPLAY**: Full help listing with command reference, workflow diagram, quick start, and file reference.

No files are created or modified by this command.
</output>

<error_handling>
- **Unknown command in arguments**: Show "Command not found" with closest match suggestion
- **VERSION file missing**: Display "version unknown"
</error_handling>

<success_criteria>
- All GRD commands are listed with descriptions
- Commands are organized by logical category
- Workflow diagram shows the full R&D lifecycle
- Quick start guide enables immediate productivity
- File reference shows project structure at a glance
- Specific command help provides actionable detail
</success_criteria>

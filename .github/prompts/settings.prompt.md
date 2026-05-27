---
description: Configure GRD workflow agents, model profile, git isolation, execution backend (overstory), execution teams, code review, confirmation gates, research gates, autonomous mode, and evolve settings
argument-hint: "[yolo | profile | ceremony | evolve | overstory]"
---

<purpose>
Interactive configuration of all GRD settings via multi-question prompt: workflow agents (research, plan_check, verifier), model profile, git worktree isolation, execution backend (overstory), execution teams, code review (timing, severity, auto-fix), confirmation gates, research gates, autonomous mode, and tracker integration. Updates .planning/config.json with user preferences.

Supports quick toggle subcommands:
- `/grd:settings yolo [on|off|status]` — toggle autonomous mode (same as former /grd:yolo)
- `/grd:settings profile [quality|balanced|budget]` — switch model profile (same as former /grd:set-profile)
- `/grd:settings ceremony [full|standard|minimal]` — set default ceremony level
- `/grd:settings evolve [auto-commit|create-pr] [on|off|status]` — toggle evolve settings
- `/grd:settings overstory [runtime|merge|poll] [value]` — configure execution backend
- `/grd:settings` (no args) — full interactive settings flow
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>

<step name="check_subcommand">
Parse `[user-provided arguments]` to check if the first argument matches a quick toggle subcommand.

```
FIRST_ARG = first word of [user-provided arguments]
REMAINING_ARGS = rest of [user-provided arguments] after first word

if FIRST_ARG == "yolo":
  → jump to <subcommand_yolo> with REMAINING_ARGS
elif FIRST_ARG == "profile":
  → jump to <subcommand_profile> with REMAINING_ARGS
elif FIRST_ARG == "ceremony":
  → jump to <subcommand_ceremony> with REMAINING_ARGS
elif FIRST_ARG == "evolve":
  → jump to <subcommand_evolve> with REMAINING_ARGS
elif FIRST_ARG == "overstory":
  → jump to <subcommand_overstory> with REMAINING_ARGS
else:
  → continue to full settings flow (ensure_and_load_config)
```
</step>

<subcommand_yolo>
## Subcommand: yolo [on | off | status]

Toggle autonomous/headless mode (YOLO mode). When enabled, the agent makes all decisions
without human input — research gates are bypassed, confirmation prompts are auto-approved,
interview questions are self-answered using available context, and decisions are logged
but not paused for approval.

CRITICAL: YOLO mode follows the same workflows but makes decisions itself rather than
asking the human. All decisions MUST be logged for post-hoc review.

### Step Y0: Parse arguments and load config

1. **Parse arguments**:
   - `on`: enable YOLO mode
   - `off`: disable YOLO mode
   - `status`: show current mode without changing
   - Empty: toggle (if on, turn off; if off, turn on)

2. **Load config**:
   - Read `.planning/config.json`
   - If missing: create with defaults (YOLO off)
   - Parse current `autonomous_mode`, `research_gates`, `confirmation_gates`

3. **Determine action**:
   ```
   CURRENT_MODE: {on | off}
   REQUESTED_ACTION: {enable | disable | status | toggle}
   TARGET_MODE: {on | off}
   ```

### Step Y1: Status display (always shown)

```
╔══════════════════════════════════════════════════════════════╗
║  GRD >>> YOLO MODE                                          ║
║                                                             ║
║  Current: {ON — autonomous | OFF — interactive}             ║
║                                                             ║
║  Research gates:                                            ║
║    survey_approval:       {on/off}                          ║
║    deep_dive_approval:    {on/off}                          ║
║    comparison_approval:   {on/off}                          ║
║    feasibility_approval:  {on/off}                          ║
║    verification_design:   {on/off}                          ║
║    product_plan_approval: {on/off}                          ║
║    phase_plan_approval:   {on/off}                          ║
║    execution_approval:    {on/off}                          ║
║                                                             ║
║  Confirmation gates:                                        ║
║    commit_confirmation:   {on/off}                          ║
║    file_deletion:         {on/off}                          ║
║    phase_completion:      {on/off}                          ║
║    target_adjustment:     {on/off}                          ║
║    approach_change:       {on/off}                          ║
║                                                             ║
╚══════════════════════════════════════════════════════════════╝
```

If action is `status`: stop here, display only.

### Step Y2: Enable YOLO mode (if target = on)

1. **Save pre-YOLO gate state** for restoration:
   - Store current `research_gates` as `_saved_research_gates`
   - Store current `confirmation_gates` as `_saved_confirmation_gates`

2. **Set autonomous mode**:
   ```json
   {
     "autonomous_mode": true,
     "research_gates": {
       "survey_approval": false,
       "deep_dive_approval": false,
       "comparison_approval": false,
       "feasibility_approval": false,
       "verification_design": false,
       "product_plan_approval": false,
       "phase_plan_approval": false,
       "execution_approval": false
     },
     "confirmation_gates": {
       "commit_confirmation": false,
       "file_deletion": false,
       "phase_completion": false,
       "target_adjustment": false,
       "approach_change": false
     }
   }
   ```

3. **Initialize decision log**:
   - Create `.planning/yolo-decisions.log` if not exists
   - Append session start entry:
     ```
     === YOLO SESSION START: {YYYY-MM-DD HH:MM:SS} ===
     Previous gates saved. All gates disabled.
     ```

4. **Display activation**:
   ```
   ╔══════════════════════════════════════════════════════════════╗
   ║                                                             ║
   ║    ██    ██  ██████  ██       ██████                        ║
   ║     ██  ██  ██    ██ ██      ██    ██                       ║
   ║      ████   ██    ██ ██      ██    ██                       ║
   ║       ██    ██    ██ ██      ██    ██                       ║
   ║       ██     ██████  ███████  ██████                        ║
   ║                                                             ║
   ║  AUTONOMOUS MODE: ENABLED                                   ║
   ║                                                             ║
   ║  All research gates:      DISABLED                          ║
   ║  All confirmation gates:  DISABLED                          ║
   ║  Decision logging:        ENABLED                           ║
   ║  Decision log:            .planning/yolo-decisions.log      ║
   ║                                                             ║
   ║  The agent will:                                            ║
   ║    - Make all decisions without asking                      ║
   ║    - Self-answer interview questions from context           ║
   ║    - Auto-approve all gates                                 ║
   ║    - Log every decision for post-hoc review                 ║
   ║                                                             ║
   ║  To disable: /grd:settings yolo off                        ║
   ║                                                             ║
   ╚══════════════════════════════════════════════════════════════╝
   ```

### Step Y3: Disable YOLO mode (if target = off)

1. **Restore saved gate states**:
   - Read `_saved_research_gates` from config
   - Read `_saved_confirmation_gates` from config
   - If no saved state: use defaults

2. **Set interactive mode**:
   ```json
   {
     "autonomous_mode": false,
     "research_gates": { ...restored_or_defaults... },
     "confirmation_gates": { ...restored_or_defaults... }
   }
   ```

3. **Close decision log session**:
   - Append to `.planning/yolo-decisions.log`:
     ```
     === YOLO SESSION END: {YYYY-MM-DD HH:MM:SS} ===
     Decisions made this session: {count}
     Gates restored to previous state.
     ===
     ```

4. **Display deactivation**:
   ```
   ╔══════════════════════════════════════════════════════════════╗
   ║  GRD >>> YOLO MODE DISABLED                                 ║
   ║                                                             ║
   ║  AUTONOMOUS MODE: OFF                                       ║
   ║                                                             ║
   ║  Research gates:      RESTORED to previous state            ║
   ║  Confirmation gates:  RESTORED to previous state            ║
   ║                                                             ║
   ║  Decisions made during YOLO session: {count}                ║
   ║  Review log: .planning/yolo-decisions.log                   ║
   ║                                                             ║
   ║  The agent will now pause at gates for human approval.      ║
   ║                                                             ║
   ╚══════════════════════════════════════════════════════════════╝
   ```

### Step Y4: Write config and optional commit

1. **Update `.planning/config.json`**:
   - Merge new settings into existing config
   - Preserve all non-gate settings
   - Write atomically (write temp file, rename)

2. **Validate config after write**:
   - Re-read and parse to confirm valid JSON
   - Verify autonomous_mode matches target

3. **Optional commit** (if changes were made):
   ```bash
   git add .planning/config.json
   git add .planning/yolo-decisions.log 2>/dev/null
   git commit -m "config: {enable|disable} YOLO autonomous mode"
   ```

**DONE — exit command after subcommand completes.**
</subcommand_yolo>

<yolo_decision_logging_protocol>
When YOLO mode is active, ALL other GRD workflows MUST log decisions to
`.planning/yolo-decisions.log` using this format:

```
[{YYYY-MM-DD HH:MM:SS}] WORKFLOW={workflow_name} DECISION={decision_type}
  Context: {brief context for the decision}
  Options: {what options were available}
  Chosen: {what was chosen}
  Rationale: {why this option was selected}
  Confidence: {HIGH|MEDIUM|LOW}
```

Decision types to log:
- GATE_BYPASS: a research or confirmation gate was auto-approved
- SELF_ANSWER: an interview question was answered from context
- APPROACH_SELECT: an approach/method was selected
- TARGET_SET: a target or threshold was decided
- SCOPE_DECISION: scope was expanded or narrowed
- ERROR_RECOVERY: an error was handled automatically
- ITERATION_DECISION: iterate/skip/adjust was decided
</yolo_decision_logging_protocol>

<subcommand_profile>
## Subcommand: profile [quality | balanced | budget]

Switch the model profile used by GRD agents. Controls which Claude model each agent uses, balancing quality vs token spend.

### Step P0: Validate argument

```
if REMAINING_ARGS not in ["quality", "balanced", "budget"]:
  Error: Invalid profile "REMAINING_ARGS"
  Valid profiles: quality, balanced, budget
  EXIT
```

### Step P1: Ensure and load config

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js config-ensure-section
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js state load)
```

### Step P2: Update config

Update `model_profile` field in `.planning/config.json`:
```json
{
  "model_profile": "REMAINING_ARGS"
}
```

### Step P3: Display confirmation

```
Model profile set to: {profile}

Agents will now use:

[Show table from MODEL_PROFILES in grd-tools.js for selected profile]

Example:
| Agent | Model |
|-------|-------|
| grd-planner | opus |
| grd-executor | sonnet |
| grd-verifier | haiku |
| ... | ... |

Next spawned agents will use the new profile.
```

Map profile names:
- quality: use "quality" column from MODEL_PROFILES
- balanced: use "balanced" column from MODEL_PROFILES
- budget: use "budget" column from MODEL_PROFILES

**DONE — exit command after subcommand completes.**
</subcommand_profile>

<subcommand_ceremony>
## Subcommand: ceremony [full | standard | minimal]

Set the default ceremony level for GRD workflows.

### Step C0: Validate argument

```
if REMAINING_ARGS not in ["full", "standard", "minimal"]:
  Error: Invalid ceremony level "REMAINING_ARGS"
  Valid levels: full, standard, minimal
  EXIT
```

### Step C1: Ensure and load config

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js config-ensure-section
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js state load)
```

### Step C2: Update config

Update `ceremony` field in `.planning/config.json`:
```json
{
  "ceremony": "REMAINING_ARGS"
}
```

Ceremony level effects:
- **full**: All agents spawned, all gates active, full documentation
- **standard**: Default agents, standard gates, normal documentation
- **minimal**: Skip optional agents, minimal gates, lean documentation

### Step C3: Display confirmation

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► CEREMONY LEVEL: {LEVEL}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| Aspect          | Setting |
|-----------------|---------|
| Optional Agents | {all/default/skip} |
| Gate Checks     | {all/standard/minimal} |
| Documentation   | {full/normal/lean} |

This applies to future /grd:plan-phase and /grd:execute-phase runs.
```

**DONE — exit command after subcommand completes.**
</subcommand_ceremony>

<subcommand_evolve>
## Subcommand: evolve [auto-commit | create-pr] [on | off | status]

Configure evolve command settings: auto-commit per iteration and PR creation.

### Step E0: Parse arguments

```
SETTING = first word of REMAINING_ARGS (auto-commit | create-pr)
ACTION = second word of REMAINING_ARGS (on | off | status | empty)

if SETTING is empty:
  → show all evolve settings (status mode)
elif SETTING not in ["auto-commit", "create-pr"]:
  Error: Invalid evolve setting "SETTING"
  Valid settings: auto-commit, create-pr
  EXIT
```

### Step E1: Load config

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js config-ensure-section
```

Read `.planning/config.json` and parse `evolve` section (defaults: `auto_commit: true`, `create_pr: true`).

### Step E2: Display status

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► EVOLVE SETTINGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| Setting       | Value |
|---------------|-------|
| Auto-commit   | {ON/OFF} |
| Create PR     | {ON/OFF} |

Auto-commit: Automatically commit changes after each evolve iteration.
Create PR: Push branch and create a pull request after all iterations complete.
```

If ACTION is `status` or SETTING is empty: stop here, display only.

### Step E3: Update setting

Map SETTING to config key:
- `auto-commit` → `evolve.auto_commit`
- `create-pr` → `evolve.create_pr`

Map ACTION:
- `on` → `true`
- `off` → `false`
- empty → toggle current value

Update `.planning/config.json`:
```json
{
  "evolve": {
    "auto_commit": true/false,
    "create_pr": true/false
  }
}
```

### Step E4: Display confirmation

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► EVOLVE: {SETTING} set to {ON/OFF}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Updated .planning/config.json

Quick commands:
- /grd:settings evolve — show all evolve settings
- /grd:settings evolve auto-commit [on|off] — toggle auto-commit
- /grd:settings evolve create-pr [on|off] — toggle PR creation
```

**DONE — exit command after subcommand completes.**
</subcommand_evolve>

<subcommand_overstory>
## Subcommand: overstory [runtime | merge | poll]

Configure Overstory execution backend settings.

### Step O0: Parse arguments

```
SETTING = first word of REMAINING_ARGS (runtime | merge | poll)
VALUE = second word of REMAINING_ARGS

if SETTING is empty:
  → show all overstory settings (status mode)
elif SETTING not in ["runtime", "merge", "poll"]:
  Error: Invalid overstory setting "SETTING"
  Valid settings: runtime, merge, poll
  EXIT
```

### Step O1: Load config

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js config-ensure-section
```

Read `.planning/config.json` and parse `overstory` section (defaults: `runtime: "claude"`, `poll_interval_ms: 5000`, `merge_strategy: "auto"`, `install_prompt: true`, `overlay_template: null`).

### Step O2: Display status

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► OVERSTORY SETTINGS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| Setting        | Value |
|----------------|-------|
| Runtime        | {overstory.runtime} |
| Merge Strategy | {overstory.merge_strategy} |
| Poll Interval  | {overstory.poll_interval_ms}ms |
| Install Prompt | {overstory.install_prompt} |

Runtime: Overstory runtime adapter for workers (claude, codex, pi, copilot, cursor).
Merge Strategy: auto = FIFO merge queue, manual = prompt per agent.
Poll Interval: Status polling frequency during wave execution (ms).
```

If SETTING is empty: stop here, display only.

### Step O3: Update setting

```
if SETTING == "runtime":
  if VALUE is empty: Error: "Usage: /grd:settings overstory runtime <name>"
  → update overstory.runtime = VALUE in config.json

elif SETTING == "merge":
  if VALUE not in ["auto", "manual"]: Error: "Valid values: auto, manual"
  → update overstory.merge_strategy = VALUE in config.json

elif SETTING == "poll":
  if VALUE is not a number or VALUE < 1000: Error: "Poll interval must be >= 1000ms"
  → update overstory.poll_interval_ms = NUMBER(VALUE) in config.json
```

### Step O4: Confirm

Display updated value and confirm:
```
✓ overstory.{SETTING} updated to {VALUE}
```

**DONE — exit command after subcommand completes.**
</subcommand_overstory>

<step name="ensure_and_load_config">
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js config-ensure-section
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js state load)
```
</step>

<step name="read_current">
```bash
cat .planning/config.json
```

Parse current values:
- `workflow.research` — spawn researcher during plan-phase (default: `true`)
- `workflow.plan_check` — spawn plan checker during plan-phase (default: `true`)
- `workflow.verifier` — spawn verifier during execute-phase (default: `true`)
- `model_profile` — which model each agent uses (default: `"balanced"`)
- `git.branching_strategy` — branching approach (default: `"none"`)
- `git.worktree_dir` — worktree directory location (default: `".worktrees/"`)
- `git.default_completion_action` — action after phase execution (default: `"ask"`)
- `autonomous_mode` — YOLO mode, skip all gates (default: `false`)
- `execution.use_teams` — parallel teammate execution (default: `false`)
- `execution.max_concurrent_teammates` — max teammates when teams enabled (default: `4`)
- `code_review.enabled` — whether auto code review is on (default: `true`)
- `code_review.timing` — review timing: per_wave or per_phase (default: `"per_wave"`)
- `code_review.severity_gate` — minimum severity to block (default: `"blocker"`)
- `code_review.auto_fix_warnings` — auto-fix warning-level findings (default: `false`)
- `confirmation_gates.commit_confirmation` — confirm before git commit (default: `false`)
- `confirmation_gates.file_deletion` — confirm before deleting files (default: `false`)
- `confirmation_gates.phase_completion` — confirm before marking phase complete (default: `false`)
- `confirmation_gates.target_adjustment` — confirm before adjusting eval targets (default: `false`)
- `confirmation_gates.approach_change` — confirm before changing approach (default: `false`)
- `research_gates.verification_design` — pause for EVAL.md review (default: `false`)
- `research_gates.method_selection` — pause for method choice review (default: `false`)
- `research_gates.baseline_review` — pause for baseline review (default: `false`)
- `overstory.runtime` — execution backend runtime adapter (default: `"claude"`)
- `overstory.merge_strategy` — merge strategy for agent results (default: `"auto"`)
- `overstory.poll_interval_ms` — status polling frequency in ms (default: `5000`)
</step>

<step name="present_settings">
Use AskUserQuestion with current values pre-selected:

```
AskUserQuestion([
  {
    question: "Which model profile for agents?",
    header: "Model",
    multiSelect: false,
    options: [
      { label: "Quality", description: "Opus everywhere except verification (highest cost)" },
      { label: "Balanced (Recommended)", description: "Opus for planning, Sonnet for execution/verification" },
      { label: "Budget", description: "Sonnet for writing, Haiku for research/verification (lowest cost)" }
    ]
  },
  {
    question: "Spawn Plan Researcher? (researches domain before planning)",
    header: "Research",
    multiSelect: false,
    options: [
      { label: "Yes", description: "Research phase goals before planning" },
      { label: "No", description: "Skip research, plan directly" }
    ]
  },
  {
    question: "Spawn Plan Checker? (verifies plans before execution)",
    header: "Plan Check",
    multiSelect: false,
    options: [
      { label: "Yes", description: "Verify plans meet phase goals" },
      { label: "No", description: "Skip plan verification" }
    ]
  },
  {
    question: "Spawn Execution Verifier? (verifies phase completion)",
    header: "Verifier",
    multiSelect: false,
    options: [
      { label: "Yes", description: "Verify must-haves after execution" },
      { label: "No", description: "Skip post-execution verification" }
    ]
  },
  {
    question: "Use worktree isolation for phase execution?",
    header: "Git Isolation",
    multiSelect: false,
    options: [
      { label: "Yes", description: "Each phase runs in a separate git worktree (recommended for active development)" },
      { label: "No (Default)", description: "Execute directly on current branch, no isolation" }
    ]
  },
  {
    question: "Enable autonomous (YOLO) mode?",
    header: "Autonomous Mode",
    multiSelect: false,
    options: [
      { label: "No (Recommended)", description: "Confirm at decision points" },
      { label: "Yes", description: "Agent makes all decisions, skip all gates" }
    ]
  },
  {
    question: "Use Agent Teams for parallel plan execution?",
    header: "Execution Teams",
    multiSelect: false,
    options: [
      { label: "No (Default)", description: "Execute plans sequentially in a single agent" },
      { label: "Yes", description: "Spawn parallel teammate agents for each wave" }
    ]
  },
  {
    question: "Execution backend for parallel workers?",
    header: "Exec Backend",
    multiSelect: false,
    options: [
      { label: "GRD (Default)", description: "Use GRD's own commands/skills with the configured AI backend" },
      { label: "Superpowers", description: "Use Superpowers plugin system — multi-account rotation across AI backends" },
      { label: "Overstory", description: "Use Overstory multi-agent orchestration (tmux + git worktrees)" },
      { label: "Claude Code", description: "Use Claude Code native teammates directly" },
      { label: "Codex", description: "Use OpenAI Codex CLI" },
      { label: "Gemini", description: "Use Google Gemini CLI" },
      { label: "OpenCode", description: "Use OpenCode CLI (provider-agnostic)" }
    ]
  },
  {
    question: "Automatic code review timing?",
    header: "Code Review",
    multiSelect: false,
    options: [
      { label: "Per Wave (Default)", description: "Review after each execution wave completes" },
      { label: "Per Phase", description: "Review once after entire phase execution" },
      { label: "Disabled", description: "No automatic code review" }
    ]
  },
  {
    question: "Minimum severity to block execution?",
    header: "Review Severity Gate",
    multiSelect: false,
    options: [
      { label: "Blocker (Default)", description: "Only blockers halt execution" },
      { label: "Critical", description: "Blockers and critical issues halt execution" },
      { label: "Warning", description: "All non-info issues halt execution" }
    ]
  },
  {
    question: "Auto-fix warnings found during code review?",
    header: "Auto-fix Warnings",
    multiSelect: false,
    options: [
      { label: "No (Default)", description: "Report warnings but do not auto-fix" },
      { label: "Yes", description: "Automatically fix warning-level review findings" }
    ]
  },
  {
    question: "Confirmation gates — pause for human approval at these points?",
    header: "Confirmation Gates",
    multiSelect: true,
    options: [
      { label: "Commit Confirmation", description: "Confirm before each git commit" },
      { label: "File Deletion", description: "Confirm before deleting files" },
      { label: "Phase Completion", description: "Confirm before marking phase complete" },
      { label: "Target Adjustment", description: "Confirm before adjusting evaluation targets" },
      { label: "Approach Change", description: "Confirm before changing implementation approach" }
    ]
  },
  {
    question: "Research gates — pause for human review at these checkpoints?",
    header: "Research Gates",
    multiSelect: true,
    options: [
      { label: "Verification Design", description: "Review EVAL.md before execution" },
      { label: "Method Selection", description: "Review method choice before planning" },
      { label: "Baseline Review", description: "Review baseline assessment before setting targets" }
    ]
  },
  {
    question: "Issue tracker provider?",
    header: "Tracker",
    multiSelect: false,
    options: [
      { label: "None (Recommended)", description: "No tracker integration" },
      { label: "GitHub Issues", description: "Sync phases/plans to GitHub Issues (requires gh CLI)" },
      { label: "Jira", description: "Sync phases/plans to Jira (requires API token or OAuth)" }
    ]
  }
])
```

**Conditional: Worktree sub-options (if user selected "Yes" for Git Isolation)**

If user selected "Yes" for Git Isolation, ask follow-up questions:

```
AskUserQuestion([
  {
    question: "Worktree directory location?",
    header: "Worktree Directory",
    multiSelect: false,
    options: [
      { label: ".worktrees/ (Default)", description: "Project-local .worktrees/ directory" },
      { label: "Custom", description: "Specify a custom directory path" }
    ]
  },
  {
    question: "Default action when phase execution completes?",
    header: "Completion Action",
    multiSelect: false,
    options: [
      { label: "Ask each time (Default)", description: "Present merge/PR/keep/discard options after execution" },
      { label: "Merge locally", description: "Auto-merge worktree branch into base branch" },
      { label: "Create PR", description: "Auto-push and create pull request" },
      { label: "Keep branch", description: "Leave worktree in place for manual review" }
    ]
  }
])
```

If "Custom" is selected for Worktree Directory, prompt the user for the custom directory path (free-text input).

**Conditional: Execution Teams sub-options (if user selected "Yes" for Execution Teams)**

If user selected "Yes" for Execution Teams, ask follow-up question:

```
AskUserQuestion([
  {
    question: "Maximum concurrent teammates?",
    header: "Team Size",
    multiSelect: false,
    options: [
      { label: "2", description: "Conservative — lower resource usage" },
      { label: "4 (Default)", description: "Balanced concurrency" },
      { label: "6", description: "Higher parallelism for large phases" }
    ]
  }
])
```

**AI Account Configuration (shown when user mentions accounts, rotation, rate limits, or AI service credentials)**

Works regardless of execution backend. Only the user knows which accounts they have — do NOT try to guess from filesystem patterns.

**Step 1: Read current config and detect installed CLIs.**

```bash
# What's currently configured?
cat .planning/config.json 2>/dev/null | node -e "
  const d=require('fs').readFileSync('/dev/stdin','utf-8');
  try { const c=JSON.parse(d);
    if(c.superpowers?.accounts) console.log('CURRENT:', JSON.stringify(c.superpowers.accounts));
    else console.log('CURRENT: none');
  } catch { console.log('CURRENT: none'); }
"

# Which CLIs are installed?
which claude 2>/dev/null && echo "DETECTED: claude"
which codex 2>/dev/null && echo "DETECTED: codex"
which gemini 2>/dev/null && echo "DETECTED: gemini"
which opencode 2>/dev/null && echo "DETECTED: opencode"
```

**Step 2: Show current state, then ask.**

If accounts are already configured, show them first:

```
Current account setup:
  claude: 2 accounts (rotation enabled)
    1. ~/.claude
    2. ~/.claude-personal
  Priority: claude
  Fallback: wait for recovery

Installed CLIs: claude, codex
```

Then ask:

```
AskUserQuestion([
  {
    header: "Account Configuration",
    question: "What would you like to change?",
    multiSelect: false,
    options: [
      { label: "Add an account", description: "Add a new account for an existing or new backend" },
      { label: "Remove an account", description: "Remove a configured account" },
      { label: "Change priority order", description: "Reorder which backend/account is tried first" },
      { label: "Reconfigure from scratch", description: "Start the account setup fresh" },
      { label: "Disable rotation", description: "Go back to single-account mode" }
    ]
  }
])
```

If NO accounts configured yet, use the same interview flow as `gd init` Round 5 (Steps 5b through 5g from init.md).

**Step 3: "Add an account" flow.**

```
AskUserQuestion([
  {
    header: "Add Account",
    question: "Which AI backend is this account for?",
    multiSelect: false,
    options: [
      // Only show installed CLIs:
      { label: "Claude" },
      { label: "Codex" }
    ]
  }
])
```

Then:

```
What's the config directory path for this account?

  [Enter path, e.g. ~/.claude-work]

  ℹ To authenticate this account:
      CLAUDE_CONFIG_DIR=<your-path> claude auth login
```

Validate the path, add to the existing accounts array in config.json, confirm.

**Step 4: "Remove an account" flow.**

Show numbered list of current accounts, let user pick which to remove:

```
AskUserQuestion([
  {
    header: "Remove Account",
    question: "Which account do you want to remove?",
    multiSelect: false,
    options: [
      { label: "claude: ~/.claude", description: "Account 1" },
      { label: "claude: ~/.claude-personal", description: "Account 2" }
    ]
  }
])
```

If removing leaves only 1 account, offer to disable rotation entirely.

**Step 5: Write updated config.**

Merge into existing `.planning/config.json` — only update `superpowers` and `scheduler` sections, preserving everything else.

**Conditional: Overstory sub-options (if user selected "Overstory" for Execution Backend)**

If user selected "Overstory" for Execution Backend, ask follow-up questions:

```
AskUserQuestion([
  {
    question: "Overstory runtime adapter for workers?",
    header: "Runtime",
    multiSelect: false,
    options: [
      { label: "claude (Default)", description: "Claude Code as the worker runtime" },
      { label: "codex", description: "OpenAI Codex as the worker runtime" },
      { label: "cursor", description: "Cursor as the worker runtime" },
      { label: "copilot", description: "GitHub Copilot as the worker runtime" }
    ]
  },
  {
    question: "Merge strategy for completed agent results?",
    header: "Merge Strategy",
    multiSelect: false,
    options: [
      { label: "Auto (Default)", description: "FIFO merge queue — automatically merge results as agents complete" },
      { label: "Manual", description: "Prompt for confirmation before merging each agent's results" }
    ]
  },
  {
    question: "Status polling interval during wave execution?",
    header: "Poll Interval",
    multiSelect: false,
    options: [
      { label: "3000ms", description: "Faster updates, slightly higher overhead" },
      { label: "5000ms (Default)", description: "Balanced polling frequency" },
      { label: "10000ms", description: "Less frequent updates, lower overhead" }
    ]
  }
])
```
</step>

<step name="update_config">
Merge new settings into existing config.json:

```json
{
  ...existing_config,
  "model_profile": "quality" | "balanced" | "budget",
  "autonomous_mode": true|false,
  "workflow": {
    "research": true/false,
    "plan_check": true/false,
    "verifier": true/false
  },
  "git": {
    "branching_strategy": "none" | "phase",
    "worktree_dir": ".worktrees/" | custom_path,
    "default_completion_action": "ask" | "merge" | "pr" | "keep"
  },
  "execution": {
    "use_teams": true/false,
    "max_concurrent_teammates": 2|4|6
  },
  "code_review": {
    "enabled": timing !== "disabled",
    "timing": "per_wave" | "per_phase",
    "severity_gate": "blocker" | "critical" | "warning",
    "auto_fix_warnings": true/false
  },
  "confirmation_gates": {
    "commit_confirmation": true/false,
    "file_deletion": true/false,
    "phase_completion": true/false,
    "target_adjustment": true/false,
    "approach_change": true/false
  },
  "research_gates": {
    "verification_design": true/false,
    "method_selection": true/false,
    "baseline_review": true/false
  },
  "backend": "grd" | "superpowers" | "overstory" | "claude" | "codex" | "gemini" | "opencode",
  "superpowers": {
    "default_backend": "claude" | "codex" | "gemini" | "opencode",
    "account_rotation": true/false,
    "accounts": {
      "claude": [{ "config_dir": "~/.claude-personal" }, { "config_dir": "~/.claude-work" }],
      "codex": [{ "config_dir": "~/.codex-main" }]
    }
  },
  "scheduler": {
    "backend_priority": ["claude", "codex", "gemini", "opencode"],
    "free_fallback": { "backend": "opencode" },
    "prediction": {
      "window_minutes": 15,
      "ewma_alpha": 0.3,
      "safety_margin_tasks": 1.5,
      "min_samples": 3
    }
  },
  "overstory": {
    "runtime": "claude" | "codex" | "cursor" | "copilot",
    "merge_strategy": "auto" | "manual",
    "poll_interval_ms": 3000 | 5000 | 10000
  },
  "tracker": {
    "provider": "none" | "github" | "mcp-atlassian"
  }
}
```

**Mapping rules:**

Git Isolation:
- "Yes" -> `branching_strategy: "phase"`, write `worktree_dir` and `default_completion_action`
- "No" -> `branching_strategy: "none"` (omit worktree_dir and default_completion_action)

Worktree Directory:
- ".worktrees/ (Default)" -> `worktree_dir: ".worktrees/"`
- "Custom" -> `worktree_dir: <user-provided path>`

Completion Action:
- "Ask each time (Default)" -> `default_completion_action: "ask"`
- "Merge locally" -> `default_completion_action: "merge"`
- "Create PR" -> `default_completion_action: "pr"`
- "Keep branch" -> `default_completion_action: "keep"`

Execution Teams:
- "Yes" -> `use_teams: true`, write `max_concurrent_teammates` from follow-up
- "No" -> `use_teams: false`

Execution Backend:
- "GRD (Default)" -> `backend: "grd"`
- "Superpowers" -> `backend: "superpowers"`, write `superpowers` section from follow-ups
- "Overstory" -> `backend: "overstory"`, write `overstory` section with runtime, merge_strategy, poll_interval_ms from follow-ups
- "Claude Code" -> `backend: "claude"`
- "Codex" -> `backend: "codex"`
- "Gemini" -> `backend: "gemini"`
- "OpenCode" -> `backend: "opencode"`

Superpowers AI Backend:
- "Claude Code (Default)" -> `superpowers.default_backend: "claude"`
- "Codex" -> `superpowers.default_backend: "codex"`
- "Gemini" -> `superpowers.default_backend: "gemini"`
- "OpenCode" -> `superpowers.default_backend: "opencode"`

Superpowers Account Rotation:
- "Yes (Recommended)" -> `superpowers.account_rotation: true`
- "No" -> `superpowers.account_rotation: false`

Superpowers Accounts:
- Parse comma-separated paths for each backend
- For each non-empty backend, create `superpowers.accounts.<backend>` array of `{ config_dir: "<path>" }` objects
- Empty entries are omitted from config

Backend Priority:
- Parse comma-separated backend names → `scheduler.backend_priority` array
- Only include backends that have accounts configured

Free Fallback:
- "OpenCode (Default)" → `scheduler.free_fallback: { "backend": "opencode" }`
- "Claude Code" → `scheduler.free_fallback: { "backend": "claude" }`
- "Codex" → `scheduler.free_fallback: { "backend": "codex" }`
- "Gemini" → `scheduler.free_fallback: { "backend": "gemini" }`

Include `scheduler` section only when backend is "superpowers" and account_rotation is true.
Prediction defaults are always included in `scheduler.prediction` — these are sensible defaults that users rarely need to change.

Overstory Runtime:
- "claude (Default)" -> `runtime: "claude"`
- "codex" -> `runtime: "codex"`
- "cursor" -> `runtime: "cursor"`
- "copilot" -> `runtime: "copilot"`

Overstory Merge Strategy:
- "Auto (Default)" -> `merge_strategy: "auto"`
- "Manual" -> `merge_strategy: "manual"`

Overstory Poll Interval:
- "3000ms" -> `poll_interval_ms: 3000`
- "5000ms (Default)" -> `poll_interval_ms: 5000`
- "10000ms" -> `poll_interval_ms: 10000`

Code Review Timing:
- "Per Wave (Default)" -> `timing: "per_wave"`, `enabled: true`
- "Per Phase" -> `timing: "per_phase"`, `enabled: true`
- "Disabled" -> `enabled: false`

Review Severity Gate:
- "Blocker (Default)" -> `severity_gate: "blocker"`
- "Critical" -> `severity_gate: "critical"`
- "Warning" -> `severity_gate: "warning"`

Confirmation Gates (multi-select — each selected label maps to `true`):
- "Commit Confirmation" -> `commit_confirmation: true`
- "File Deletion" -> `file_deletion: true`
- "Phase Completion" -> `phase_completion: true`
- "Target Adjustment" -> `target_adjustment: true`
- "Approach Change" -> `approach_change: true`
- Unselected gates default to `false`

Write updated config to `.planning/config.json`.
</step>

<step name="confirm">
Display:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► SETTINGS UPDATED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| Setting              | Value |
|----------------------|-------|
| Model Profile        | {quality/balanced/budget} |
| Plan Researcher      | {On/Off} |
| Plan Checker         | {On/Off} |
| Execution Verifier   | {On/Off} |
| Git Isolation        | {On/Off} |
| Worktree Directory   | {path or N/A} |
| Completion Action    | {ask/merge/pr/keep or N/A} |
| Agent Teams          | {On/Off} |
| Max Teammates        | {N or N/A} |
| Execution Backend    | {GRD/Superpowers/Overstory/Claude Code/Codex/Gemini/OpenCode} |
| SP Default Backend   | {claude/codex/gemini/opencode or N/A} |
| SP Account Rotation  | {On/Off or N/A} |
| Overstory Runtime    | {runtime or N/A} |
| Overstory Merge      | {auto/manual or N/A} |
| Overstory Poll       | {Nms or N/A} |
| Code Review          | {Per Wave/Per Phase/Off} |
| Severity Gate        | {blocker/critical/warning} |
| Auto-fix Warnings    | {On/Off} |
| Autonomous Mode      | {On/Off} |
| Gate: Eval Design    | {On/Off} |
| Gate: Method Select  | {On/Off} |
| Gate: Baseline Review| {On/Off} |
| Gate: Commit Confirm | {On/Off} |
| Gate: File Deletion  | {On/Off} |
| Gate: Phase Complete | {On/Off} |
| Gate: Target Adjust  | {On/Off} |
| Gate: Approach Change| {On/Off} |
| Tracker              | {None/GitHub/Jira} |

These settings apply to future /grd:plan-phase and /grd:execute-phase runs.

Quick commands:
- /grd:settings profile <profile> — switch model profile
- /grd:settings yolo — toggle autonomous mode
- /grd:settings ceremony <level> — set ceremony level
- /grd:settings overstory [runtime|merge|poll] — configure execution backend
- /grd:plan-phase --research — force research
- /grd:plan-phase --skip-research — skip research
- /grd:plan-phase --skip-verify — skip plan check
```
</step>

</process>

<success_criteria>
- [ ] Current config read with all sections (workflow, git, execution, overstory, code_review, confirmation_gates, research_gates, tracker)
- [ ] User presented with 14+ questions (profile + 3 workflow toggles + git isolation + execution backend + autonomous mode + execution teams + 3 code review + confirmation gates + research gates + tracker)
- [ ] Conditional sub-options asked for worktree (directory + completion action), teams (max teammates), and overstory (runtime + merge strategy + poll interval)
- [ ] Config updated with all sections: git (branching_strategy, worktree_dir, default_completion_action), execution (use_teams, max_concurrent_teammates), overstory (runtime, merge_strategy, poll_interval_ms), code_review (enabled, timing, severity_gate, auto_fix_warnings), confirmation_gates (5 toggles), research_gates, tracker
- [ ] All settings displayed in confirmation summary table
- [ ] Changes confirmed to user
</success_criteria>

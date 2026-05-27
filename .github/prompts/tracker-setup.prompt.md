---
description: Configure issue tracker integration (GitHub Issues or MCP Atlassian)
argument-hint:
---

<purpose>
Interactive setup for issue tracker integration. Walks the user through choosing a provider (GitHub/MCP Atlassian/None), validates prerequisites, writes config, and optionally runs initial sync.
</purpose>

<context>
Tracker integration reference: @${CLAUDE_PLUGIN_ROOT}/references/tracker-integration.md
MCP protocol reference: @${CLAUDE_PLUGIN_ROOT}/references/mcp-tracker-protocol.md
</context>

<process>

## Step 1: Load Current Config

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js config-ensure-section
TRACKER_CONFIG=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker get-config --raw)
```

Parse current provider (may be "none" or already configured).

## Step 2: Choose Provider

Use AskUserQuestion:

```
AskUserQuestion([
  {
    question: "Which issue tracker do you want to use?",
    header: "Tracker",
    multiSelect: false,
    options: [
      { label: "GitHub Issues (Recommended)", description: "Uses gh CLI. Issues, labels, sub-issues." },
      { label: "MCP Atlassian (Jira)", description: "Uses mcp-atlassian MCP server. Epics, tasks, transitions." },
      { label: "None", description: "Disable tracker integration." }
    ]
  }
])
```

**If "None":** Set `tracker.provider = "none"`, write config, display confirmation, exit.

## Step 3: Provider-Specific Setup

### GitHub Setup

**3a. Check prerequisites:**

```bash
gh --version 2>/dev/null && echo "gh_ok" || echo "gh_missing"
gh auth status 2>/dev/null && echo "auth_ok" || echo "auth_missing"
gh extension list 2>/dev/null | grep sub-issue && echo "subissue_ok" || echo "subissue_missing"
```

**If `gh` not installed:**
```
GitHub CLI (gh) is required. Install it:
  brew install gh           # macOS
  sudo apt install gh       # Linux
  winget install GitHub.cli # Windows

Then: gh auth login
```
Exit.

**If not authenticated:**
```
Please authenticate: gh auth login
```
Exit.

**If sub-issue extension missing:** Note as optional:
```
Optional: Install gh-sub-issue for parent/child issue linking:
  gh extension install github/gh-sub-issue
```

**3b. Configure GitHub settings:**

Use AskUserQuestion:

```
AskUserQuestion([
  {
    question: "Default assignee for created issues? (GitHub username, leave blank for none)",
    header: "Assignee",
    multiSelect: false,
    options: [
      { label: "None", description: "Don't auto-assign issues" },
      { label: "Me", description: "Assign to current gh user" }
    ]
  }
])
```

If "Me": get username from `gh api user --jq '.login'`.

**3c. Write config:**

```json
{
  "tracker": {
    "provider": "github",
    "auto_sync": true,
    "github": {
      "project_board": "",
      "default_assignee": "{username or empty}",
      "default_labels": ["research", "implementation", "evaluation", "integration"],
      "auto_issues": true,
      "pr_per_phase": false
    }
  }
}
```

Merge into existing `.planning/config.json`.

### MCP Atlassian Setup

**3a. Validate MCP server connectivity:**

Call MCP tool `get_all_projects` to verify the mcp-atlassian server is available and authenticated.

**If MCP tool call fails:**
```
The mcp-atlassian MCP server is not available or not configured.

To set up mcp-atlassian:
1. Add the mcp-atlassian server to your Claude Code MCP configuration
2. Configure it with your Atlassian credentials (see mcp-atlassian docs)
3. Restart Claude Code
4. Run /grd:tracker-setup again
```
Exit.

**3b. Select project:**

Parse the project list from `get_all_projects` response. Show available projects.

Use AskUserQuestion to let user pick project_key (show up to 4 projects from the response, user can type "Other" for a different key):

```
AskUserQuestion([
  {
    question: "Which Jira project should GRD sync to?",
    header: "Project",
    multiSelect: false,
    options: [
      // Dynamic: up to 4 projects from MCP response
      { label: "{KEY1} — {name1}", description: "Project key: {KEY1}" },
      { label: "{KEY2} — {name2}", description: "Project key: {KEY2}" }
    ]
  }
])
```

**3c. Configure issue types:**

Use AskUserQuestion:

```
AskUserQuestion([
  {
    question: "Issue type for milestone Epics?",
    header: "Epic type",
    multiSelect: false,
    options: [
      { label: "Epic (Recommended)", description: "Standard Jira Epic — maps to GRD milestones" },
      { label: "Story", description: "Use Story instead of Epic for milestones" }
    ]
  },
  {
    question: "Issue type for phase Tasks (children of milestone Epic)?",
    header: "Task type",
    multiSelect: false,
    options: [
      { label: "Task (Recommended)", description: "Standard Jira Task — maps to GRD phases" },
      { label: "Story", description: "Use Story instead of Task for phases" }
    ]
  },
  {
    question: "Issue type for plan Sub-tasks (children of phase Task)?",
    header: "Sub-task type",
    multiSelect: false,
    options: [
      { label: "Sub-task (Recommended)", description: "Standard Jira Sub-task — maps to GRD plans" },
      { label: "Task", description: "Use Task instead of Sub-task for plans" }
    ]
  }
])
```

**3d. Configure schedule fields:**

Use AskUserQuestion:

```
AskUserQuestion([
  {
    question: "Which Jira field stores start dates? (Jira Plans / Advanced Roadmaps uses this for timeline display)",
    header: "Start date",
    multiSelect: false,
    options: [
      { label: "customfield_10015 (Recommended)", description: "Standard Jira Cloud start date field used by most instances" },
      { label: "startDate", description: "Newer Jira Cloud instances use this built-in field" },
      { label: "customfield_10014", description: "Alternative custom field ID (some instances)" }
    ]
  },
  {
    question: "Default phase duration in days? (Used when **Duration:** is not specified in ROADMAP.md)",
    header: "Duration",
    multiSelect: false,
    options: [
      { label: "7 days (Recommended)", description: "One week per phase — good starting default" },
      { label: "14 days", description: "Two weeks per phase — for longer research phases" },
      { label: "5 days", description: "One work week per phase — compact sprints" }
    ]
  }
])
```

**3e. Write config:**

```json
{
  "tracker": {
    "provider": "mcp-atlassian",
    "auto_sync": true,
    "mcp_atlassian": {
      "project_key": "{project_key}",
      "milestone_issue_type": "Epic",
      "phase_issue_type": "Task",
      "plan_issue_type": "Sub-task",
      "start_date_field": "{start_date_field}",
      "default_duration_days": {default_duration_days}
    }
  }
}
```

Merge into existing `.planning/config.json`.

## Step 4: Optional Initial Sync

If ROADMAP.md exists:

Use AskUserQuestion:

```
AskUserQuestion([
  {
    question: "ROADMAP.md found. Run initial sync now?",
    header: "Sync",
    multiSelect: false,
    options: [
      { label: "Yes", description: "Push all phases to tracker now" },
      { label: "No", description: "Sync later with /grd:sync" }
    ]
  }
])
```

If "Yes":

**For GitHub:**
```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker sync-roadmap --raw
```

**For mcp-atlassian:**
Follow MCP protocol from @${CLAUDE_PLUGIN_ROOT}/references/mcp-tracker-protocol.md:
```bash
OPS=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js tracker prepare-roadmap-sync --raw)
```
Execute MCP `create_issue` for each `"create"` operation, then `record-mapping`.

Display results.

## Step 5: Confirmation

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD ► TRACKER CONFIGURED
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| Setting        | Value |
|----------------|-------|
| Provider       | {github/mcp-atlassian} |
| Auto-sync      | On |
| Auth Status    | {authenticated/mcp_server} |
{GitHub:}
| Assignee       | {username or none} |
{MCP Atlassian:}
| Project Key    | {key} |
| Milestone Type | {milestone_issue_type} |
| Phase Type     | {phase_issue_type} |
| Plan Type      | {plan_issue_type} |
| Start Date Field | {start_date_field} |
| Default Duration | {default_duration_days}d |

Tracker will auto-sync during:
- Roadmap creation (/grd:init)
- Phase planning (/grd:plan-phase)
- Phase execution (/grd:execute-phase)
- Verification (/grd:verify-phase)

Manual sync: /grd:sync
```

</process>

<success_criteria>
- [ ] Provider selected (GitHub/MCP Atlassian/None)
- [ ] Prerequisites validated (gh CLI for GitHub, MCP server connectivity for MCP Atlassian)
- [ ] Config written to .planning/config.json
- [ ] Optional initial sync offered and executed
- [ ] Clear confirmation with next steps
</success_criteria>

---
description: Create or edit project PRINCIPLES.md — the constitution that shapes all agent behavior
---

<purpose>
Create or update `.planning/PRINCIPLES.md` — the project constitution that governs how all GRD agents behave. Principles define coding philosophy, testing requirements, architecture constraints, documentation standards, and communication style. Once established, every agent (planner, executor, reviewer, verifier) reads PRINCIPLES.md to align its work with the team's standards.

This is the most leveraged configuration in GRD: a few clear principles prevent hundreds of review comments and rework cycles.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>

## Step 1: Check for existing PRINCIPLES.md

```bash
INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js state load)
```

Parse JSON for: `planning_exists`, `commit_docs`.

Check if `.planning/PRINCIPLES.md` already exists:

```bash
ls .planning/PRINCIPLES.md 2>/dev/null
```

**If file exists:** Go to Step 2 (Edit flow).
**If file does not exist:** Go to Step 3 (Create flow).

---

## Step 2: Edit existing PRINCIPLES.md

Read and display the current file:

```bash
cat .planning/PRINCIPLES.md
```

Present the current principles to the user:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD > CURRENT PRINCIPLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[rendered contents of PRINCIPLES.md]
```

Use AskUserQuestion:
- header: "Principles"
- question: "What would you like to change?"
- options:
  - "Edit a section" -- Modify one of the five sections
  - "Add a principle" -- Add a new rule to a section
  - "Remove a principle" -- Delete a rule from a section
  - "Rewrite from scratch" -- Start over with the guided flow
  - "Looks good" -- No changes needed

**If "Edit a section":**

Use AskUserQuestion:
- header: "Section"
- question: "Which section?"
- options:
  - "Coding Philosophy"
  - "Testing Requirements"
  - "Architecture Constraints"
  - "Documentation Standards"
  - "Communication Style"

Ask inline (freeform): "What should the updated rules be for [section]?"

Apply the changes to PRINCIPLES.md. Go to Step 5 (Commit).

**If "Add a principle":**

Use AskUserQuestion to select the section, then ask inline for the new principle. Append it. Go to Step 5 (Commit).

**If "Remove a principle":**

Use AskUserQuestion to select the section, then list the principles in that section as options. Remove the selected one. Go to Step 5 (Commit).

**If "Rewrite from scratch":**

Go to Step 3 (Create flow).

**If "Looks good":**

Display: "No changes made. Principles remain as-is."

Exit.

---

## Step 3: Create new PRINCIPLES.md

Display stage banner:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD > DEFINING PRINCIPLES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Your project principles shape how every agent works.
Let's define them across 5 categories.
```

**Detect existing context:**

Check for codebase signals that can inform defaults:
- `package.json` or similar manifest -> language, framework
- `.eslintrc*`, `eslint.config.*` -> linting conventions
- `tsconfig.json` -> TypeScript preferences
- `jest.config.*`, `vitest.config.*`, `pytest.ini` -> test framework
- `.prettierrc*` -> formatting conventions
- `README.md` -> project description

Use detected context to offer smart defaults in each category.

---

### Category 1: Coding Philosophy

Use AskUserQuestion:
- header: "Coding Philosophy"
- question: "What principles guide how code should be written?"
- multiSelect: true
- options (adapt based on detected language/framework):
  - "Prefer simplicity over cleverness"
  - "Functions should do one thing well"
  - "Explicit over implicit"
  - "Minimize dependencies"
  - "Fail fast with clear errors"
  - "No premature optimization"
  - "DRY but not at the expense of clarity"
  - "Immutable by default"
  - "Composition over inheritance"
  - "Custom" -- I want to write my own

If "Custom" is selected, ask inline: "What coding principles should agents follow?"

Store selections as `$CODING_PHILOSOPHY`.

---

### Category 2: Testing Requirements

Use AskUserQuestion:
- header: "Testing Requirements"
- question: "What are your testing standards?"
- multiSelect: true
- options (adapt based on detected test framework):
  - "Every new function needs a test"
  - "Tests must be independent and isolated"
  - "No mocking of implementation details"
  - "Integration tests for critical paths"
  - "Maintain existing coverage thresholds"
  - "Test names describe behavior, not implementation"
  - "No test should take more than 5 seconds"
  - "Golden/snapshot tests for CLI output"
  - "Custom" -- I want to write my own

If "Custom" is selected, ask inline: "What testing standards should agents follow?"

Store selections as `$TESTING_REQUIREMENTS`.

---

### Category 3: Architecture Constraints

Use AskUserQuestion:
- header: "Architecture Constraints"
- question: "What architecture rules must agents respect?"
- multiSelect: true
- options (adapt based on detected project structure):
  - "Keep modules loosely coupled"
  - "No circular dependencies"
  - "Pure functions in lib/, side effects only at boundaries"
  - "One responsibility per file"
  - "Public API surface must be intentional"
  - "No global mutable state"
  - "Database access only through repository layer"
  - "Error handling at boundaries, not everywhere"
  - "Custom" -- I want to write my own

If "Custom" is selected, ask inline: "What architecture constraints should agents follow?"

Store selections as `$ARCHITECTURE_CONSTRAINTS`.

---

### Category 4: Documentation Standards

Use AskUserQuestion:
- header: "Documentation Standards"
- question: "What documentation rules apply?"
- multiSelect: true
- options:
  - "Comments explain WHY, not WHAT"
  - "Every public function needs a JSDoc/docstring"
  - "README stays current with changes"
  - "CHANGELOG updated for user-facing changes"
  - "No orphaned documentation"
  - "Examples in docs must be runnable"
  - "API changes require migration notes"
  - "Custom" -- I want to write my own

If "Custom" is selected, ask inline: "What documentation standards should agents follow?"

Store selections as `$DOCUMENTATION_STANDARDS`.

---

### Category 5: Communication Style

Use AskUserQuestion:
- header: "Communication Style"
- question: "How should agents communicate in plans, summaries, and commit messages?"
- multiSelect: true
- options:
  - "Be concise -- no filler words"
  - "Use active voice"
  - "Commit messages: imperative mood, under 72 chars"
  - "Plans: bullet points over paragraphs"
  - "Summaries: lead with outcomes, not process"
  - "No jargon without definition"
  - "Technical precision over approachability"
  - "Custom" -- I want to write my own

If "Custom" is selected, ask inline: "What communication style should agents use?"

Store selections as `$COMMUNICATION_STYLE`.

---

## Step 4: Write PRINCIPLES.md

Create `.planning/PRINCIPLES.md`:

```markdown
# Project Principles

> These principles govern how all GRD agents behave in this project.
> Every planner, executor, reviewer, and verifier reads this file.

## Coding Philosophy

${CODING_PHILOSOPHY — one bullet per principle}

## Testing Requirements

${TESTING_REQUIREMENTS — one bullet per principle}

## Architecture Constraints

${ARCHITECTURE_CONSTRAINTS — one bullet per principle}

## Documentation Standards

${DOCUMENTATION_STANDARDS — one bullet per principle}

## Communication Style

${COMMUNICATION_STYLE — one bullet per principle}

---
*Established: ${date}*
*Last updated: ${date}*
```

Ensure `.planning/` directory exists:

```bash
mkdir -p .planning
```

---

## Step 5: Commit

```bash
node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js commit "docs: ${VERB} project principles" --files .planning/PRINCIPLES.md
```

Where `${VERB}` is:
- `establish` for new files
- `update` for edits

---

## Step 6: Confirmation

Display:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 GRD > PRINCIPLES ${ESTABLISHED|UPDATED}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Coding Philosophy:     ${count} rules
Testing Requirements:  ${count} rules
Architecture:          ${count} rules
Documentation:         ${count} rules
Communication:         ${count} rules

File: .planning/PRINCIPLES.md

These principles now shape all agent behavior.
Edit anytime: /grd:principles
```

</process>

<success_criteria>
- [ ] Existing PRINCIPLES.md detected and presented if it exists
- [ ] All 5 categories covered (Coding Philosophy, Testing Requirements, Architecture Constraints, Documentation Standards, Communication Style)
- [ ] Codebase signals detected and used for smart defaults
- [ ] User selections captured for all categories
- [ ] PRINCIPLES.md written with all sections populated
- [ ] File committed to git
- [ ] Confirmation displayed with rule counts
</success_criteria>
</output>

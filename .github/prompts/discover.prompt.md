---
description: Discover and extract coding standards from existing codebase patterns
argument-hint: [API | Database | Frontend | Testing | Configuration | custom path]
---

<purpose>
Analyze existing codebase files to discover recurring patterns and conventions. Present each discovered pattern interactively so the user can confirm which ones should become enforced standards. Confirmed patterns are written to `.planning/standards/{area}/{pattern-slug}.md` with structured frontmatter and indexed in `.planning/standards/index.yml`.
</purpose>

<process>

<step name="get_focus_area" priority="first">
**Get focus area from argument or prompt user.**

If `[user-provided arguments]` contains a focus area, use it. Otherwise, prompt:

```
AskUserQuestion(
  header: "Standards Discovery",
  question: "Which area of the codebase should I analyze for patterns?",
  options: [
    { label: "API", description: "Routes, controllers, middleware, request/response patterns" },
    { label: "Database", description: "Models, queries, migrations, connection patterns" },
    { label: "Frontend", description: "Components, state management, styling patterns" },
    { label: "Testing", description: "Test structure, fixtures, assertions, mocking patterns" },
    { label: "Configuration", description: "Config loading, environment handling, defaults" },
    { label: "Custom path", description: "Specify a directory or glob pattern to analyze" }
  ]
)
```

Store response as `$AREA`.

If "Custom path" selected, follow up:

```
AskUserQuestion(
  header: "Custom Path",
  question: "Enter the directory path or glob pattern to analyze (e.g., src/services/ or lib/**/*.js):",
  followUp: null
)
```

Store response as `$CUSTOM_PATH`.

Map area to search patterns:
- **API**: `src/**/routes/**`, `src/**/controllers/**`, `src/**/api/**`, `lib/**/routes/**`, `app/**/controllers/**`
- **Database**: `src/**/models/**`, `src/**/migrations/**`, `src/**/db/**`, `lib/**/models/**`, `prisma/**`, `drizzle/**`
- **Frontend**: `src/**/components/**`, `src/**/pages/**`, `src/**/views/**`, `app/**/components/**`
- **Testing**: `tests/**`, `test/**`, `__tests__/**`, `*.test.*`, `*.spec.*`
- **Configuration**: `config/**`, `*.config.*`, `.env*`, `src/**/config/**`
- **Custom path**: Use `$CUSTOM_PATH` directly
</step>

<step name="find_representative_files">
**Find 5-10 representative files using Glob.**

Use the search patterns from the previous step to find candidate files. Select 5-10 files that are:
- Non-trivial (>20 lines)
- Representative of the area (not generated or vendored)
- Diverse (different subdirectories or concerns within the area)

If fewer than 5 files found, widen the search. If the mapped globs find nothing, fall back to a broad search of the project and filter by relevance to the area.

Report to user:
```
Analyzing {N} files in {area}:
{list of file paths}
```
</step>

<step name="analyze_patterns">
**Read each file and extract patterns.**

For each file, analyze and note:

1. **Naming conventions** — file names, function names, variable names, class names, export names
2. **Import/require patterns** — ordering, grouping, aliasing, path conventions
3. **Error handling** — try/catch structure, error classes, error response format, logging
4. **Response/return structures** — consistent shapes, status codes, envelope patterns
5. **Documentation patterns** — JSDoc style, inline comments, header comments, README conventions
6. **Testing patterns** — describe/it structure, setup/teardown, assertion style, mock patterns
7. **Code organization** — module structure, export style, separation of concerns
8. **Configuration patterns** — how defaults are set, env var access, validation

For each pattern category, look for consistency across files. A pattern is significant if it appears in 3+ files or is clearly intentional (e.g., a shared utility enforcing it).

Collect discovered patterns as a list, each with:
- `name`: Short descriptive name (e.g., "Error response envelope")
- `category`: One of the categories above
- `description`: What the pattern is and why it exists
- `examples`: 2-3 code snippets showing the pattern in use
- `files`: Which files exhibit this pattern
- `confidence`: high (5+ files), medium (3-4 files), low (1-2 files but intentional)
</step>

<step name="present_patterns_interactively">
**Present each pattern to the user for confirmation.**

For each discovered pattern (sorted by confidence, highest first):

```
---

## Pattern: {name}
Category: {category} | Confidence: {confidence} | Found in {N} files

{description}

### Examples

{2-3 code snippets with file attribution}

---
```

Then ask:

```
AskUserQuestion(
  header: "Pattern: {name}",
  question: "Should this pattern be enforced as a standard?",
  options: [
    { label: "Yes — enforce as standard", description: "Add to project standards" },
    { label: "Yes — with modifications", description: "I want to adjust the description before saving" },
    { label: "Skip", description: "Not a standard, just coincidence" }
  ]
)
```

If "Yes -- with modifications": ask user for their revised description/rules, then save with their version.

Collect all confirmed patterns as `$CONFIRMED_PATTERNS`.
</step>

<step name="write_standard_files">
**Write confirmed standards to `.planning/standards/{area}/`.**

Compute the area slug:
```bash
AREA_SLUG=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js generate-slug "$AREA" --raw)
```

Create the directory:
```bash
mkdir -p ${standards_dir}/${AREA_SLUG}
```

For each confirmed pattern, generate a slug and write a markdown file:

```bash
PATTERN_SLUG=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js generate-slug "${pattern_name}" --raw)
```

Write to `.planning/standards/${AREA_SLUG}/${PATTERN_SLUG}.md`:

```markdown
---
name: {pattern_name}
category: {category}
area: {area}
confidence: {confidence}
source_files:
  - {file1}
  - {file2}
discovered: {YYYY-MM-DD}
status: active
---

# {pattern_name}

{description}

## Rule

{Concise statement of what the standard requires}

## Examples

### Correct

{code example showing the pattern done right}

### Incorrect

{code example showing what to avoid — inferred from the pattern}

## Rationale

{Why this pattern exists — inferred from codebase context}
```
</step>

<step name="update_index">
**Create or update `.planning/standards/index.yml`.**

If `.planning/standards/index.yml` exists, read it and merge new entries. Otherwise create it.

Format:

```yaml
# Standards Index — auto-generated by /grd:discover
# Last updated: {YYYY-MM-DD}

areas:
  {area_slug}:
    name: {Area}
    standards:
      - slug: {pattern_slug}
        name: {pattern_name}
        category: {category}
        status: active
        file: {area_slug}/{pattern_slug}.md
```

Merge new standards into existing areas. Do not remove existing entries.
</step>

<step name="commit">
**Commit all standards files.**

```bash
git add .planning/standards/
git commit -m "docs(standards): discover {area} patterns — {N} standards extracted"
```

If nothing to commit (no patterns confirmed), inform user and skip.
</step>

<step name="completion">
**Display summary and next steps.**

```
---

GRD > STANDARDS DISCOVERY COMPLETE

Area: {area}
Files analyzed: {N}
Patterns found: {total}
Standards confirmed: {confirmed_count}
Standards skipped: {skipped_count}

Written to: .planning/standards/{area_slug}/

Standards:
{for each confirmed pattern:}
  - {pattern_name} ({category})

---

Next steps:
- /grd:discover {other_area} — discover patterns in another area
- Review standards in .planning/standards/{area_slug}/
- Standards are automatically loaded during /grd:plan-phase and /grd:execute-phase

---
```
</step>

</process>

<error_handling>
- **No files found for area**: Suggest broader search or ask user for specific paths
- **No patterns detected**: Report findings, suggest trying a different area or providing more files
- **generate-slug not available**: Fall back to lowercase-hyphenated manual slug generation
- **Git commit fails**: Report files written but uncommitted, suggest manual commit
- **index.yml parse error**: Back up corrupted file, create fresh index
</error_handling>

<success_criteria>
- [ ] User selects or provides a focus area
- [ ] 5-10 representative files found and analyzed
- [ ] Patterns extracted across multiple categories (naming, imports, errors, etc.)
- [ ] Each pattern presented interactively with examples
- [ ] User confirms/skips each pattern
- [ ] Confirmed patterns written to `.planning/standards/{area}/{slug}.md` with frontmatter
- [ ] `.planning/standards/index.yml` created or updated
- [ ] All standards files committed to git
- [ ] Summary displayed with counts and next steps
</success_criteria>

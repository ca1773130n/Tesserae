---
description: SoTA survey — scan arXiv, GitHub, benchmarks for a research topic and update LANDSCAPE.md
argument-hint: <research topic or question>
---

<purpose>
Survey state-of-the-art for a research topic. Scans papers, GitHub repos, benchmarks and
updates the research LANDSCAPE.md with a structured landscape of methods, results,
and open problems. This is the entry point for any research-driven development workflow.
</purpose>

<context>
CLAUDE.md rules: @CLAUDE.md

**Research directory structure** (paths resolved via init):
- `${research_dir}/LANDSCAPE.md` — master landscape of methods, papers, repos
- `${research_dir}/deep-dives/` — individual paper analyses
- `${research_dir}/PAPERS.md` — index of all reviewed papers
- `${research_dir}/COMPARISON-*.md` — method comparison matrices
- `.planning/config.json` — GRD configuration (research_gates, autonomous_mode)

**Agent available:**
- `grd-surveyor` — specialized research survey agent with web search, paper parsing, repo analysis
</context>

<process>

## Step 0: INITIALIZE — Validate Input and Environment

1. **Parse arguments**: Extract topic from `[user-provided arguments]`
   - If empty: use conversation context or ASK user for topic
   - If URL: treat as seed paper/repo and derive topic

2. **Run initialization**:
   ```bash
   INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init survey "$TOPIC")
   ```

   Parse JSON for: `research_dir`, `landscape_exists`, `papers_exists`, `benchmarks_exists`, `knowhow_exists`, `researcher_model`, `autonomous_mode`, `research_gates`.

3. **Ensure research directory exists**:
   ```bash
   mkdir -p ${research_dir}/deep-dives
   ```

4. **Read existing LANDSCAPE.md** (if present):
   - Parse current methods, papers, benchmarks
   - Store as `EXISTING_LANDSCAPE` for later diff
   - Note timestamp of last survey

5. **Read config**:
   - Load `.planning/config.json` for research_gates and autonomous_mode
   - Check `research_gates.survey_approval` — if true, confirm topic with user before proceeding

**STEP_0_CHECKPOINT:**
- [ ] Topic extracted and validated
- [ ] Research directory exists
- [ ] Existing landscape captured (or noted as first survey)
- [ ] Config loaded

---

## Step 1: DISPLAY BANNER

```
╔══════════════════════════════════════════════════════════════╗
║  GRD >>> SURVEYING                                          ║
║                                                             ║
║  Topic: {topic}                                             ║
║  Scope: papers, repos, benchmarks, datasets                 ║
║  Prior: {N methods known | "first survey"}                  ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 2: SPAWN SURVEYOR AGENT

**Launch `grd-surveyor` agent via Task tool:**

Use Task tool with `subagent_type="grd:grd-surveyor"`:

```
Survey the state-of-the-art for: {topic}

PATHS:
research_dir: ${research_dir}

EXISTING LANDSCAPE (diff against this):
{EXISTING_LANDSCAPE or "No prior survey — this is a fresh scan."}

SEARCH SCOPE:
1. PAPERS: arXiv, Semantic Scholar, Google Scholar — last 2 years priority
   - Find seminal works and most recent advances
   - Extract: title, authors, year, venue, key contribution, reported metrics
   - Note: method type (supervised/unsupervised/hybrid/etc.)

2. CODE: GitHub repos with >50 stars implementing relevant methods
   - Extract: repo URL, stars, language, last commit date, license
   - Note: production-readiness signals (tests, docs, CI, releases)

3. BENCHMARKS: Standard evaluation datasets and leaderboards
   - Extract: benchmark name, task, metrics, current SOTA numbers
   - Note: which methods hold top positions

4. DATASETS: Available training/eval data
   - Extract: name, size, format, license, download URL
   - Note: quality signals and known issues

OUTPUT FORMAT:
Return a structured LANDSCAPE.md with these sections:
- ## Overview (2-3 paragraph summary of the field)
- ## Methods (table: method | paper | year | approach | key_metric | code_available)
- ## Benchmarks (table: benchmark | task | metric | SOTA_method | SOTA_score)
- ## Datasets (table: dataset | size | format | license | notes)
- ## Open Problems (bulleted list of unsolved challenges)
- ## Recommendations (which methods to investigate further and why)
- ## Survey Metadata (date, query terms used, sources checked)
```

**STEP_2_CHECKPOINT:**
- [ ] Surveyor agent launched with full context
- [ ] Agent returned structured LANDSCAPE.md content
- [ ] Content includes all required sections

---

## Step 3: PROCESS RESULTS

1. **Parse agent output**: Extract structured landscape data
2. **Compute diff** against EXISTING_LANDSCAPE:
   - New methods found
   - Updated metrics
   - New benchmarks discovered
   - Removed/deprecated entries
3. **Validate completeness**:
   - At least 5 methods documented
   - At least 2 benchmarks identified
   - Recommendations section is non-empty
   - All table rows have complete data (no TBD placeholders)

**If validation fails**: Log warning but proceed — partial survey is better than none

---

## Step 4: DISPLAY FINDINGS SUMMARY

```
╔══════════════════════════════════════════════════════════════╗
║  SURVEY RESULTS                                             ║
╠══════════════════════════════════════════════════════════════╣
║                                                             ║
║  Methods found:     {N total} ({M new})                     ║
║  Benchmarks:        {B total}                               ║
║  Datasets:          {D total}                               ║
║  Code available:    {C}/{N} methods have implementations    ║
║                                                             ║
║  Top recommendation: {method_name}                          ║
║    Reason: {one-line rationale}                              ║
║                                                             ║
║  DIFF vs previous survey:                                   ║
║    + {count} new methods                                    ║
║    ~ {count} updated entries                                ║
║    - {count} removed/deprecated                             ║
║                                                             ║
╚══════════════════════════════════════════════════════════════╝
```

If this is the first survey, show "INITIAL SURVEY" instead of diff.

---

## Step 5: WRITE LANDSCAPE.md

1. **Write updated LANDSCAPE.md**:
   - Path: `${research_dir}/LANDSCAPE.md`
   - Include survey metadata header with timestamp
   - Preserve any user annotations from previous version (lines starting with `> NOTE:`)

2. **Update PAPERS.md index** (if it exists):
   - Add any newly discovered papers to the index
   - Format: `| title | authors | year | status | deep-dive |`
   - Status: `surveyed` (not yet deep-dived)

---

## Step 6: COMMIT

1. **Stage research files**:
   ```bash
   git add ${research_dir}/LANDSCAPE.md
   git add ${research_dir}/PAPERS.md 2>/dev/null
   ```

2. **Commit with descriptive message**:
   ```bash
   git commit -m "research: survey {topic} — {N} methods, {B} benchmarks"
   ```

3. **Handle commit failure**: If nothing to commit (no changes), inform user

---

## Step 7: ROUTE NEXT ACTION

Based on survey results, suggest next steps:

| Condition | Suggestion |
|-----------|------------|
| Strong candidate method found | `/grd:deep-dive {paper}` — analyze top recommendation |
| Multiple viable methods | `/grd:compare-methods` — build comparison matrix |
| Production readiness unclear | `/grd:feasibility {method}` — analyze paper-to-prod gap |
| Baseline needed first | `/grd:assess-baseline` — establish current performance |
| Ready to plan | `/grd:product-plan` — create development roadmap |

</process>

<output>
**FILES_WRITTEN:**
- `${research_dir}/LANDSCAPE.md` — full survey landscape (created or updated)
- `${research_dir}/PAPERS.md` — paper index (updated if exists)

**DISPLAY**: Survey results summary with diff, recommendations, and next-step routing

**GIT**: Committed with message: `research: survey {topic} — {N} methods, {B} benchmarks`
</output>

<error_handling>
- **No results found**: Broaden search terms, try alternative phrasings, report partial results
- **Agent timeout**: Save partial results, suggest re-running with narrower scope
- **Existing LANDSCAPE.md parse error**: Back up corrupted file, create fresh landscape
- **Git commit fails**: Report files written but uncommitted, suggest manual commit
- **Config missing**: Use defaults (all gates off, balanced profile)
</error_handling>

<success_criteria>
- LANDSCAPE.md contains structured, actionable research overview
- At least 5 methods documented with papers and metrics
- Diff clearly shows what changed from previous survey
- Next action routing is specific and contextual
- All data is traceable to sources (papers, repos, benchmarks)
</success_criteria>

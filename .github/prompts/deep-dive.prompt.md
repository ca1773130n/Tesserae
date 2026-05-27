---
description: Deep analysis of a specific paper — method, code, limitations, production considerations
argument-hint: <paper title, URL, or arXiv ID>
---

<purpose>
Deep analysis of a specific paper — method architecture, code reproduction, limitations,
and production considerations. Creates a structured deep-dive document that feeds into
feasibility analysis and implementation planning. This is the microscope after the
survey's telescope.
</purpose>

<context>
CLAUDE.md rules: @CLAUDE.md

**Research directory structure** (paths resolved via init):
- `${research_dir}/deep-dives/{PAPER-SLUG}.md` — individual paper analysis
- `${research_dir}/PAPERS.md` — master index of all reviewed papers
- `${research_dir}/LANDSCAPE.md` — survey landscape (may reference this paper)
- `.planning/config.json` — GRD configuration

**Agent available:**
- `grd-deep-diver` — specialized paper analysis agent with web search, code reading, critique
</context>

<process>

## Step 0: INITIALIZE — Parse Paper Reference

0. **Run initialization**:
   ```bash
   INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init deep-dive "$PAPER_REF")
   ```
   Parse JSON for: `research_dir`, `landscape_exists`, `papers_exists`, `deep_dives`, `researcher_model`, `autonomous_mode`, `research_gates`.

1. **Parse arguments**: Extract paper reference from `[user-provided arguments]`
   - If arXiv URL (e.g., `arxiv.org/abs/2301.12345`): extract ID, fetch metadata
   - If paper title: search for canonical reference
   - If Semantic Scholar / DOI URL: extract metadata
   - If empty: check LANDSCAPE.md for top recommendation, ASK user to confirm

2. **Normalize paper reference**:
   ```
   PAPER_TITLE: {full title}
   PAPER_AUTHORS: {first author et al.}
   PAPER_YEAR: {year}
   PAPER_URL: {canonical URL}
   PAPER_SLUG: {kebab-case-short-name}  (for filename)
   ```

3. **Check for existing deep-dive**:
   - Look for `${research_dir}/deep-dives/{PAPER-SLUG}.md`
   - If exists: load previous analysis, note it will be updated/extended
   - If not: this is a fresh analysis

4. **Ensure directory exists**:
   ```bash
   mkdir -p ${research_dir}/deep-dives
   ```

5. **Load context from LANDSCAPE.md** (if available):
   - Find this paper's entry in the landscape
   - Extract related methods, benchmarks, known metrics

6. **Read config**: Check `research_gates.deep_dive_approval`

**STEP_0_CHECKPOINT:**
- [ ] Paper reference normalized with title, authors, URL
- [ ] Paper slug generated for filename
- [ ] Existing analysis checked
- [ ] LANDSCAPE.md context loaded

---

## Step 1: DISPLAY BANNER

```
╔══════════════════════════════════════════════════════════════╗
║  GRD >>> DEEP DIVE                                          ║
║                                                             ║
║  Paper: {paper_title}                                       ║
║  Authors: {authors}                                         ║
║  Year: {year}                                               ║
║  URL: {url}                                                 ║
║  Status: {fresh analysis | updating existing}               ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 2: SPAWN DEEP-DIVER AGENT

**Launch `grd-deep-diver` agent via Task tool:**

Use Task tool with `subagent_type="grd:grd-deep-diver"`:

```
Deep-dive analysis of paper: {PAPER_TITLE}

PATHS:
research_dir: ${research_dir}

URL: {PAPER_URL}
Authors: {PAPER_AUTHORS}, {PAPER_YEAR}

LANDSCAPE CONTEXT:
{Related methods and benchmarks from LANDSCAPE.md, or "No landscape yet"}

EXISTING ANALYSIS (if updating):
{Previous deep-dive content, or "Fresh analysis"}

ANALYZE THE FOLLOWING DIMENSIONS:

1. METHOD ARCHITECTURE:
   - Core algorithm / model architecture
   - Key innovations vs prior work
   - Mathematical formulation (simplified)
   - Training procedure and loss functions
   - Inference pipeline

2. REPORTED RESULTS:
   - Benchmarks used and metrics reported
   - Comparison with baselines (table format)
   - Ablation study findings
   - Statistical significance (if reported)

3. CODE AVAILABILITY:
   - Official implementation: URL, language, framework, license
   - Third-party implementations: quality, maintenance status
   - Reproduction difficulty estimate (1-5 scale)
   - Key dependencies and version requirements

4. LIMITATIONS AND ASSUMPTIONS:
   - Explicitly stated limitations
   - Implicit assumptions (data distribution, compute, etc.)
   - Failure modes mentioned or inferable
   - Scalability concerns

5. PRODUCTION CONSIDERATIONS:
   - Inference latency characteristics
   - Memory/compute requirements
   - Data requirements for fine-tuning
   - Serving architecture implications
   - Licensing constraints

6. VERDICT:
   - ADOPT: ready for production use
   - TRIAL: promising, needs evaluation in our context
   - ASSESS: interesting but significant unknowns
   - HOLD: not suitable for current needs (explain why)

   Provide confidence level (HIGH/MEDIUM/LOW) and rationale.

OUTPUT FORMAT:
Return structured markdown with all 6 sections, tables where appropriate,
and a clear verdict section at the top for quick scanning.
```

**STEP_2_CHECKPOINT:**
- [ ] Deep-diver agent launched with full context
- [ ] Agent returned structured analysis
- [ ] All 6 dimensions covered

---

## Step 3: VALIDATE AND ENRICH RESULTS

1. **Validate completeness**: Check all 6 sections are present and substantive
2. **Cross-reference with LANDSCAPE.md**:
   - Verify reported metrics match landscape data
   - Flag any discrepancies
3. **Enrich with metadata**:
   - Add analysis date
   - Add analyst note on confidence
   - Add links to related deep-dives if they exist

---

## Step 4: DISPLAY VERDICT

```
╔══════════════════════════════════════════════════════════════╗
║  DEEP DIVE VERDICT                                          ║
╠══════════════════════════════════════════════════════════════╣
║                                                             ║
║  Paper: {paper_title_short}                                 ║
║  Verdict: {ADOPT | TRIAL | ASSESS | HOLD}                  ║
║  Confidence: {HIGH | MEDIUM | LOW}                          ║
║                                                             ║
║  Key strengths:                                             ║
║    + {strength_1}                                           ║
║    + {strength_2}                                           ║
║                                                             ║
║  Key concerns:                                              ║
║    - {concern_1}                                            ║
║    - {concern_2}                                            ║
║                                                             ║
║  Code: {available | partial | none}                         ║
║  Reproduction: {1-5}/5 difficulty                           ║
║                                                             ║
║  Production readiness: {ready | needs-work | research-only} ║
║                                                             ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 5: WRITE DEEP-DIVE FILE

1. **Write deep-dive document**:
   - Path: `${research_dir}/deep-dives/{PAPER-SLUG}.md`
   - Include YAML frontmatter with metadata:
     ```yaml
     ---
     paper: {title}
     authors: {authors}
     year: {year}
     url: {url}
     verdict: {ADOPT|TRIAL|ASSESS|HOLD}
     confidence: {HIGH|MEDIUM|LOW}
     analyzed: {YYYY-MM-DD}
     ---
     ```

2. **Update PAPERS.md index**:
   - If PAPERS.md exists: update this paper's row, set status to `deep-dived`
   - If PAPERS.md does not exist: create it with header and this paper's entry
   - Format: `| title | authors | year | verdict | confidence | deep-dive-link |`

3. **Update LANDSCAPE.md** (if paper is referenced there):
   - Add verdict annotation next to the method entry
   - Add `[deep-dive](${research_dir}/deep-dives/{PAPER-SLUG}.md)` link

---

## Step 6: COMMIT

1. **Stage files**:
   ```bash
   git add ${research_dir}/deep-dives/{PAPER-SLUG}.md
   git add ${research_dir}/PAPERS.md
   git add ${research_dir}/LANDSCAPE.md 2>/dev/null
   ```

2. **Commit**:
   ```bash
   git commit -m "research: deep-dive {paper_title_short} — verdict: {verdict}"
   ```

---

## Step 7: ROUTE NEXT ACTION

| Verdict | Suggestion |
|---------|------------|
| ADOPT | `/grd:feasibility {paper}` — analyze production gap |
| TRIAL | `/grd:eval-plan` — design evaluation to test in our context |
| ASSESS | `/grd:compare-methods` — compare with alternatives |
| HOLD | `/grd:survey {refined_topic}` — look for better options |

</process>

<output>
**FILES_WRITTEN:**
- `${research_dir}/deep-dives/{PAPER-SLUG}.md` — full paper analysis
- `${research_dir}/PAPERS.md` — updated paper index
- `${research_dir}/LANDSCAPE.md` — annotated with verdict (if applicable)

**DISPLAY**: Verdict summary with strengths, concerns, and next-step routing

**GIT**: Committed with message: `research: deep-dive {paper} — verdict: {verdict}`
</output>

<error_handling>
- **Paper not found**: Ask user to provide URL or clarify title, search with alternate terms
- **No code available**: Note in analysis, increase reproduction difficulty score
- **Agent returns incomplete analysis**: Fill gaps with "UNKNOWN — needs manual review" markers
- **PAPERS.md format corrupted**: Back up and recreate from deep-dives directory listing
- **Conflicting metrics**: Flag discrepancy explicitly, note both sources
</error_handling>

<success_criteria>
- Deep-dive covers all 6 dimensions with substantive analysis
- Verdict is clear with confidence level and rationale
- Production considerations are specific (not generic)
- PAPERS.md index is consistent with deep-dives directory
- Analysis is traceable to paper content (not hallucinated claims)
</success_criteria>

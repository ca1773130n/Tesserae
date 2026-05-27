---
description: Analyze paper-to-production gap for a specific approach — dependencies, scale, licensing, infra
argument-hint: <method name, paper title, or URL>
---

<purpose>
Analyze the gap between a research paper/method and production deployment. Evaluates
dependencies, scaling characteristics, licensing constraints, infrastructure requirements,
and engineering effort. Produces a structured feasibility report and updates KNOWHOW.md
with production engineering knowledge.
</purpose>

<context>
CLAUDE.md rules: @CLAUDE.md

**Research directory structure** (paths resolved via init):
- `${research_dir}/LANDSCAPE.md` — survey landscape
- `${research_dir}/deep-dives/` — paper analyses (may have relevant deep-dive)
- `.planning/KNOWHOW.md` — accumulated production engineering knowledge
- `.planning/BASELINE.md` — current system baseline (if assessed)
- `.planning/config.json` — GRD configuration

**Agent available:**
- `grd-feasibility-analyst` — specialized agent for paper-to-production gap analysis
</context>

<process>

## Step 0: INITIALIZE — Load Method Context

0. **Run initialization**:
   ```bash
   INIT=$(node ${CLAUDE_PLUGIN_ROOT}/bin/grd-tools.js init feasibility "$APPROACH")
   ```
   Parse JSON for: `research_dir`, `landscape_exists`, `deep_dives`, `knowhow_exists`, `baseline_exists`, `autonomous_mode`, `research_gates`.

1. **Parse arguments**: Extract method/paper reference from `[user-provided arguments]`
   - If paper title or URL: resolve to method name
   - If method name: look up in LANDSCAPE.md
   - If empty: check for LANDSCAPE.md recommendation or recent deep-dive verdict of ADOPT/TRIAL

2. **Gather existing research context**:
   - Load LANDSCAPE.md entry for this method
   - Load deep-dive analysis if available (`${research_dir}/deep-dives/{slug}.md`)
   - Load BASELINE.md if available (current system capabilities)
   - Load existing KNOWHOW.md entries (prior production knowledge)

3. **Gather codebase context**:
   - Read project's dependency manifest (package.json, requirements.txt, Cargo.toml, etc.)
   - Identify current tech stack, frameworks, deployment target
   - Note existing infrastructure constraints

4. **Read config**: Check `research_gates.feasibility_approval`

**STEP_0_CHECKPOINT:**
- [ ] Method identified with paper reference
- [ ] Deep-dive loaded (or noted as unavailable)
- [ ] Current tech stack identified
- [ ] KNOWHOW.md context loaded

---

## Step 1: DISPLAY BANNER

```
╔══════════════════════════════════════════════════════════════╗
║  GRD >>> FEASIBILITY ANALYSIS                               ║
║                                                             ║
║  Method: {method_name}                                      ║
║  Paper: {paper_title_short}                                 ║
║  Deep-dive: {available | not available}                     ║
║  Current stack: {language/framework}                        ║
║  Baseline: {available | not assessed}                       ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 2: SPAWN FEASIBILITY ANALYST

**Launch `grd-feasibility-analyst` agent via Task tool:**

Use Task tool with `subagent_type="grd:grd-feasibility-analyst"`:

```
Analyze paper-to-production feasibility for: {method_name}

PATHS:
research_dir: ${research_dir}
codebase_dir: ${codebase_dir}

PAPER CONTEXT:
{Deep-dive content if available, otherwise LANDSCAPE.md entry}

CURRENT SYSTEM:
- Language/Framework: {from codebase}
- Dependencies: {key deps from manifest}
- Deployment: {target environment if known}
- Baseline: {BASELINE.md summary if available}

EXISTING KNOWHOW:
{KNOWHOW.md relevant entries, or "No prior production knowledge"}

ANALYZE THESE DIMENSIONS:

1. DEPENDENCY ANALYSIS:
   - Required libraries and their maturity/maintenance status
   - Version conflicts with existing dependencies
   - Transitive dependency risks
   - Build system compatibility
   - GPU/hardware-specific dependencies

2. SCALING CHARACTERISTICS:
   - Training time estimates at different data scales
   - Inference latency characteristics (batch vs real-time)
   - Memory footprint (training vs inference)
   - Horizontal scaling feasibility
   - Bottleneck analysis (CPU, GPU, memory, I/O, network)

3. DATA REQUIREMENTS:
   - Training data volume and format
   - Data preprocessing pipeline complexity
   - Data quality requirements and cleaning effort
   - Ongoing data needs (retraining frequency)
   - Data privacy and compliance implications

4. INFRASTRUCTURE REQUIREMENTS:
   - Compute requirements (CPU/GPU/TPU, vCPU count, RAM)
   - Storage requirements (model weights, data, artifacts)
   - Serving architecture (REST, gRPC, batch, streaming)
   - Monitoring and observability needs
   - Estimated cloud cost (monthly range)

5. ENGINEERING EFFORT:
   - Implementation complexity (person-weeks estimate)
   - Integration points with existing system
   - Testing complexity (unit, integration, eval)
   - Documentation and knowledge transfer needs
   - Maintenance burden (ongoing)

6. LICENSING AND LEGAL:
   - Model/code license (MIT, Apache, GPL, proprietary, etc.)
   - Data license restrictions
   - Patent considerations
   - Commercial use restrictions
   - Attribution requirements

7. RISK ASSESSMENT:
   - Technical risks with mitigation strategies
   - Schedule risks
   - Quality risks (will it actually work in production?)
   - Organizational risks (skills gap, bus factor)

8. GAP ANALYSIS:
   - PAPER_CLAIMS vs PRODUCTION_REALITY for each key metric
   - What the paper assumes that we cannot assume
   - What the paper omits that production requires

9. VERDICT:
   - FEASIBLE: can go to production with reasonable effort
   - FEASIBLE_WITH_CAVEATS: possible but significant work needed (list caveats)
   - RESEARCH_ONLY: not production-ready, needs fundamental work
   - INFEASIBLE: blocking issues identified (list blockers)

   Provide effort estimate: {weeks} and confidence: {HIGH/MEDIUM/LOW}

OUTPUT FORMAT:
Return structured markdown with all 9 sections, gap analysis table,
and clear verdict with effort estimate.
```

**STEP_2_CHECKPOINT:**
- [ ] Feasibility analyst launched with full context
- [ ] Agent returned structured analysis
- [ ] All 9 dimensions covered
- [ ] Verdict includes effort estimate

---

## Step 3: DISPLAY FEASIBILITY VERDICT

```
╔══════════════════════════════════════════════════════════════╗
║  FEASIBILITY VERDICT                                        ║
╠══════════════════════════════════════════════════════════════╣
║                                                             ║
║  Method: {method_name}                                      ║
║  Verdict: {FEASIBLE | FEASIBLE_WITH_CAVEATS |               ║
║            RESEARCH_ONLY | INFEASIBLE}                      ║
║  Effort: {N} weeks                                          ║
║  Confidence: {HIGH | MEDIUM | LOW}                          ║
║                                                             ║
║  Key gaps:                                                  ║
║    Paper assumes → Production requires                      ║
║    {assumption}  → {reality}                                ║
║    {assumption}  → {reality}                                ║
║                                                             ║
║  Blockers: {count} ({list if any})                          ║
║  Caveats:  {count} ({list if any})                          ║
║  Est. cloud cost: ${min}-${max}/month                       ║
║                                                             ║
╚══════════════════════════════════════════════════════════════╝
```

---

## Step 4: UPDATE KNOWHOW.md

1. **Load or create** `.planning/KNOWHOW.md`
2. **Append feasibility findings** under a new section:
   ```markdown
   ## {method_name} — Feasibility ({YYYY-MM-DD})

   **Verdict**: {verdict}
   **Effort**: {weeks} weeks
   **Key insight**: {one-line summary of most important finding}

   ### Production gaps
   - {gap_1}: {mitigation}
   - {gap_2}: {mitigation}

   ### Engineering notes
   - {note_1}
   - {note_2}
   ```

3. **Preserve existing KNOWHOW.md content** — append, do not overwrite

---

## Step 5: WRITE FEASIBILITY REPORT

1. **Write detailed report**:
   - Path: `${research_dir}/deep-dives/{PAPER-SLUG}-FEASIBILITY.md`
   - Include all 9 dimensions from agent analysis
   - Include gap analysis table
   - Include verdict and effort estimate

2. **Update PAPERS.md** (if exists):
   - Add feasibility status column or annotation
   - Link to feasibility report

---

## Step 6: COMMIT

```bash
git add .planning/KNOWHOW.md
git add ${research_dir}/deep-dives/{PAPER-SLUG}-FEASIBILITY.md
git add ${research_dir}/PAPERS.md 2>/dev/null
git commit -m "research: feasibility {method_name} — {verdict}, {N}wk effort"
```

---

## Step 7: ROUTE NEXT ACTION

| Verdict | Suggestion |
|---------|------------|
| FEASIBLE | `/grd:product-plan` or `/grd:plan-phase` — start building |
| FEASIBLE_WITH_CAVEATS | `/grd:eval-plan` — design evaluation to validate caveats |
| RESEARCH_ONLY | `/grd:compare-methods` — look at production-ready alternatives |
| INFEASIBLE | `/grd:survey {broader_topic}` — find alternative approaches |

</process>

<output>
**FILES_WRITTEN:**
- `${research_dir}/deep-dives/{PAPER-SLUG}-FEASIBILITY.md` — full feasibility report
- `.planning/KNOWHOW.md` — updated with production engineering knowledge
- `${research_dir}/PAPERS.md` — annotated with feasibility status

**DISPLAY**: Feasibility verdict with gaps, blockers, effort estimate, and routing

**GIT**: Committed: `research: feasibility {method} — {verdict}, {N}wk effort`
</output>

<error_handling>
- **No deep-dive available**: Warn that feasibility analysis will be less accurate, suggest deep-dive first
- **No LANDSCAPE.md**: Proceed with paper reference alone, but note limited context
- **Method has no code**: Flag as major risk, increase effort estimate accordingly
- **Cannot determine tech stack**: Ask user for deployment target and constraints
- **KNOWHOW.md format corrupted**: Back up and append new section at end
</error_handling>

<success_criteria>
- Gap analysis is specific (paper assumption vs production reality, not generic)
- Effort estimate includes confidence level and assumptions
- Infrastructure costs are estimated with ranges
- KNOWHOW.md captures reusable production engineering knowledge
- Verdict is actionable with clear next step
</success_criteria>

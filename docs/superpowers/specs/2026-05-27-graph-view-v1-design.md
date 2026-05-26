# Graph View v1 — "sophisticated + production-ready" Design

**Date:** 2026-05-27
**Status:** approved (brainstormed with codex), pre-implementation
**Reference:** HypePaper graph view (`/tmp/graphview/hypepaper-map.md`); gap analysis (`/tmp/graphview/PORT-PLAN.md`)

## Problem

Tesserae's static-site graph (`tesserae/site/js.py`, 189 KB, `3d-force-graph`) has the engine + basic interactions (color, size, sprite labels, particles, legend, search, filter, focus, hover) but reads "flat": every node similar size, 36 node types but no legible color scheme, all 46 edge types styled alike, and no narrative surface. HypePaper (same engine) feels production-ready because of *meaningful visual encoding* + *narrative side panels*. Port that.

## Locked design (from brainstorm)

### A. Node size — single `importance` metric
Per-type raw signal, one global render formula:
- `CommunitySummary` → count of outgoing `summarizes` edges (member count)
- code symbols (`CodeFunction`/`CodeMethod`/`CodeClass`/…) → fan-in (incoming `calls`/`references`/`imports`/`declared_in`)
- session findings (`SessionInsight`/`SessionDecision`/…) → `decay_score` if present else weighted degree
- everything else → weighted degree

Render: `nodeVal = clamp(2.5, 12, 2 + log2(importance + 1) * 1.8)`. **No PPR in v1** (keeps "why is this big" explainable; PPR is a v2 option). `importance` + `family` + (for CommunitySummary) `member_count` are **precomputed at build time** into each node in the page's graph payload.

### B. Node color — 8 families + lightness tier (NOT 36 colors)
| Family | Color | Types (match exact enum names in research_graph.py) |
|---|---|---|
| Taxonomy | pink | ResearchField, ResearchTopic, ProblemArea, ApproachFamily, Trend |
| Sources | blue | SourceDocument, Paper, Repository, Project, Model, Dataset, Benchmark, Metric, Result |
| Code | cyan | CodeProject, SourceFile, CodeFile, CodeModule, CodeClass, CodeFunction, CodeMethod, CodeInterface, CodeTrait, CodeStruct, CodeEnum, CodeEnumMember, CodeTypeAlias, CodeVariable, CodeConstant, CodeRoute, CodeComponent, CodeField, CodeParameter, CodeNamespace, CodeSymbol, Dependency |
| Concepts | violet | Concept, TechnicalTerm, MathematicalConcept, MethodologicalConcept, Algorithm, ObjectiveFunction, ArchitecturePattern, TrainingParadigm, InferenceStrategy, EvaluationProtocol, Task, Capability |
| Claims | amber | Claim, ContributionClaim, PerformanceClaim, ComparisonClaim, LimitationClaim, CausalClaim, OpenQuestion, EvidenceSpan |
| Synthesis | emerald | Synthesis, CommunitySummary |
| Sessions | rose | Session, SessionInsight, SessionDecision, SessionQuestion, SessionTODO, SessionHypothesis, SessionTakeaway |
| Actors | slate | Person, Organization |
| (hidden) | — | Stub |

Tier within family: brighten focused / selected / CommunitySummary / high-importance; desaturate low-degree leaves. **Legend renders families, not 36 types.** The family→types map is the single source of truth; any enum value not listed falls back to a neutral "other" gray (so new node types degrade gracefully).

### C. Two edge classes
Principle: **structural** = schema / containment / provenance / code mechanics / measured fact (faint, thin, slate). **semantic** = interpretive / argumentative / topical / narrative / human-authored association (brighter indigo, thicker).
- structural: `is_a`, `part_of`, `subfield_of`, `contains`, `defines`, `imports`, `calls`, `documents`, `mentioned_in`, `authored_by`, `released_by`, `implemented_in`, `uses_dataset`, `evaluated_on`, `uses_metric`, `reports_result`, `achieves_score`, `derived_from_session`, `inherits_from`, `declared_in`, `implements`, `exports`, `instantiates`, `overrides`, `decorates`, `type_of`, `returns`
- semantic: `introduces`, `uses`, `extends`, `improves_on`, `compares_against`, `criticizes`, `addresses`, `optimizes_for`, `belongs_to_approach_family`, `shares_concept_with`, `derived_from`, `supports_claim`, `contradicts_claim`, `attributes_improvement_to`, `has_limitation`, `evidenced_by`, `rising_in`, `declining_in`, `emerged_after`, `synthesizes`, `summarizes`, `user_link`, `discussed_in`, `references`, `supersedes`, `discusses`
- Any edge type not listed → structural (conservative default). Particles remain **interaction-only** (incident-on-hover/focus) — must keep `test_site_js` particle assertions green.

### D. Node-detail drawer (one flexible, typed sections)
- Header: family kicker · type · title · importance · source/path pill (when present).
- Body fallback ladder: `abstract` → `description` → `evidence` → `metadata.summary` → first 240 chars of name.
- Sections (max 5 items each, grouped chips — never a raw 46-edge dump):
  - **Why it matters** — importance explanation + top incident *semantic* edges
  - **Evidence / context** — EvidenceSpan, claims, source document, source path
  - **Related** — top neighbors grouped by edge class + type
  - **Session memory** — incident `discussed_in` / `references` / `discusses` / `supersedes`
  - **Code** — file/module/path, callers, callees (for code nodes)
  - **Community** — summarized members (CommunitySummary) or parent summaries (members)
- Opens on node SELECT (click). Does not inline-expand the graph.

### E. Drawer data path (static site)
Build client-side index maps at page load from the embedded graph: `nodeById`, `incidentLinksByNode`, `incomingByType`, `outgoingByType`. Compute drawer content at click-time from those (small + instant at 2470 nodes / 6515 edges). Precompute only cheap scalars into the payload (`family`, `importance`, `member_count`) — do NOT bloat the payload with per-node drawer blobs.

### F. Production scene + density controls
HypePaper dark theme (`#060A14`-ish bg) BUT density-tuned for 2470 nodes (a glowing knot otherwise):
- hide most labels until zoom/focus; always show selected, hovered, top-importance nodes
- initial fit once; never auto-refit after user input
- faint default lines; restrained semantic-edge brightness
- settle fast (cap cooldown; higher velocity decay than HypePaper if needed)
- visual family/community grouping; NO progressive expand in v1

### G. js.py
Add encoding + drawer IN PLACE for v1. Do NOT extract the JS into a vendored asset now (visualization redesign + extraction = two risks at once). The 189KB single-file is a real liability → file a separate **v1.1 "extract graph JS asset + snapshot tests"** refactor.

## Highest risk + guardrail
Risk: "sophisticated" becomes "more marks on the canvas" (bigger nodes + brighter edges + more labels + drawer all competing). **Guardrail: the default overview MUST be sparse** — only top-importance labels visible, semantic edges restrained, drawer opens on select (not inline), and every hover/focus behavior asserted by `tests/test_site_js.py` stays dominant over the new base styling.

## Out of scope (v2+)
Cross-community bridge panel; progressive expand-on-click; cytoscape 2D timeline; PPR-based sizing; the js.py→vendored-asset extraction (v1.1).

## Testing
- Python build tests (`tests/test_site_js.py`, `tests/test_site_exports.py`) stay green — the 4 existing behavioral assertions plus new ones for: family-color map present, importance field emitted, structural/semantic edge partition present, drawer container + section scaffold present.
- **UI verification (mandatory, CLAUDE.md): Playwright screenshot of the built site BEFORE claiming done** — confirm sparse default overview, family colors legible, drawer opens on click with correct sections, labels gated until zoom. Measure: default-view label count is bounded; drawer bounding box is on-screen.
- Pixel-space (drawer DOM/CSS) vs world-space (node size, camera) tuning kept distinct.

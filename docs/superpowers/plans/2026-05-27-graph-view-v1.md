# Graph View v1 Implementation Plan

> **For agentic workers:** implement task-by-task; keep `tests/test_site_js.py` + `tests/test_site_exports.py` green throughout; UI changes REQUIRE a Playwright screenshot before "done" (CLAUDE.md).

**Goal:** Port HypePaper's visual encoding + node-detail drawer onto Tesserae's existing `3d-force-graph` static site so it reads as production-grade.

**Spec:** `docs/superpowers/specs/2026-05-27-graph-view-v1-design.md` (authoritative for the family map, edge partition, drawer sections, formulas).

**Where it lives:** `tesserae/site/js.py` emits the JS as a string (the `# Graph view` section ~line 747+). The build serializer that writes the node/edge payload + the legend lives in the same `tesserae/site/` package — find the node/edge serialization (where `nodeColor`/`nodeVal` data originate). `tests/test_site_js.py` + `tests/test_site_exports.py` define the contract.

---

## Task 1: Build-time `family` + `importance` on each node
- Find the site payload serializer in `tesserae/site/` (where graph.json→page nodes are built). Add per-node scalars: `family` (from the spec's family→types map; unknown type → "other"), `importance` (per-type raw metric per spec §A), and `member_count` for CommunitySummary.
- **Test** (`tests/test_site_exports.py` or new `tests/test_site_payload_encoding.py`): build a small typed graph (a CommunitySummary with 3 `summarizes` edges, a CodeFunction with 2 incoming `calls`, a SessionInsight with `decay_score`, a Paper with degree 4), serialize, assert each node has correct `family`, a positive `importance`, and the CommunitySummary has `member_count == 3`. Assert an unknown node type → `family == "other"`.
- Commit: `feat(site): precompute family + importance scalars on graph nodes`.

## Task 2: Family color map + tier + legend
- In `js.py`, replace the node color logic with the 8-family map (spec §B) keyed on `node.family`, with lightness tier (brighten focused/selected/CommunitySummary/high-importance; desaturate low-degree). Update the legend to render the 8 families (+ "other"), not per-type.
- Node size: `nodeVal = clamp(2.5, 12, 2 + log2(importance+1)*1.8)`.
- **Test** (`tests/test_site_js.py`): assert the emitted JS contains the family palette (8 family keys), references `node.family` for color, references `importance` for size, and the legend lists families. Keep existing color/size assertions consistent.
- Commit: `feat(site): 8-family node color + importance-scaled size + family legend`.

## Task 3: Structural vs semantic edge classes
- In `js.py`, add the edge-class partition (spec §C) as a JS lookup (structural set / semantic set; unknown → structural). Style: structural = faint thin slate; semantic = brighter indigo, thicker. Keep directional particles INTERACTION-ONLY (on hover/focus) so the 4 existing particle/edge assertions stay green.
- **Test** (`tests/test_site_js.py`): assert the emitted JS contains both edge-class sets, distinct linkColor/linkWidth per class, AND re-run the 4 existing assertions (edges-visible-as-lines, particles-only-on-incident, hover-thickens, focus-label-scale) — all must still pass.
- Commit: `feat(site): structural vs semantic edge styling (typed-edge moat made visible)`.

## Task 4: Node-detail drawer — scaffold + data maps
- In `js.py`, add a drawer DOM container (hidden by default) + CSS, and at page load build `nodeById`, `incidentLinksByNode`, `incomingByType`, `outgoingByType` from the embedded graph (spec §E). On node click → populate + show drawer; on background click / Esc → hide.
- Header: family kicker · type · title · importance · source pill. Body fallback ladder `abstract→description→evidence→metadata.summary→name(240)`.
- **Test** (`tests/test_site_js.py`): assert JS builds the index maps, has a drawer container id, wires onNodeClick → show, and renders the header fields + fallback ladder.
- Commit: `feat(site): node-detail drawer scaffold + client-side index maps`.

## Task 5: Drawer typed sections
- Implement the sections (spec §D), max 5 items each, grouped chips: Why-it-matters (importance + top semantic edges), Evidence/context, Related (grouped by edge class+type), Session memory (`discusses`/`references`/`supersedes`), Code (callers/callees for code nodes), Community (members or parent summary). Sections render only when they have content.
- **Test** (`tests/test_site_js.py`): assert each section's render fn exists and the ≤5-item cap + grouping is applied; a CommunitySummary node shows the Community section; a CodeFunction shows the Code section.
- Commit: `feat(site): drawer typed sections (why-matters / evidence / related / session / code / community)`.

## Task 6: Production scene + density controls
- Apply dark theme + density controls (spec §F): label gating (hide until zoom/focus except selected/hovered/top-importance), fit-once-no-refit, faint default lines, fast settle, family/community visual grouping. NO progressive expand.
- **Test** (`tests/test_site_js.py`): assert label-gating logic present (labels conditional on zoom/importance/focus), fit-once flag, and the dark bg. Keep all prior assertions green.
- Commit: `feat(site): dark theme + density controls for 2.4k-node legibility`.

## Task 7: UI verification (MANDATORY — not optional)
- Build the site against the real project graph: `tesserae project build-site` (or the narrowest build entry). Serve it; drive Playwright (`browser_navigate` + `browser_take_screenshot` + `browser_snapshot`):
  1. **Default overview screenshot** — confirm SPARSE (only top-importance labels; not a glowing knot). Count visible labels via `browser_evaluate`; assert bounded.
  2. **Click a CommunitySummary node** — drawer opens, shows Community section with members. Screenshot.
  3. **Click a CodeFunction** — drawer shows Code section (callers/callees). Screenshot.
  4. **Zoom in** — labels reveal. Screenshot.
  5. Measure drawer `getBoundingClientRect` — on-screen, not clipped.
- Fix any "more marks on canvas" regression (the top risk) before declaring done. Attach/describe screenshots in the PR.
- Commit: `test(site): Playwright screenshot verification of graph v1`.

## Task 8: Full suite + PR
- `PYTHONPATH=$PWD .venv/bin/pytest -q tests/` → zero new failures vs baseline (~16 with registry-leak; site_js/site_exports MUST pass since we touched them).
- Push branch `feat/graph-view-v1`; open PR linking the spec + screenshots.

## Self-review
- Spec coverage: §A→T1+T2, §B→T2, §C→T3, §D→T4+T5, §E→T4, §F→T6, guardrail→T7. ✓
- Green-test invariant called out in T2/T3/T6. ✓
- Screenshot gate is its own task (T7), not an afterthought. ✓

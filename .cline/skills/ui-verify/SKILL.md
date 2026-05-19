---
name: ui-verify
description: ---
---
---
name: ui-verify
description: Use whenever you make a visual change (CSS, layout, component placement, graph rendering, billboard, GIF screencast) and need to confirm it actually looks right before claiming done. Encodes the read-then-screenshot-then-measure loop that prevents "it's fucking too big" rounds.
---

# UI Verification

Triggered whenever a user request involves "make X look like Y", "move Z next
to W", "shrink", "expand", "fix the layout", or any other change visible in a
browser/screenshot. ALWAYS run this loop — never claim a visual change is done
without finishing all six steps.

## 1. Read first

- Grep for the existing component / style: `grep -rn "<class-name>" src/` or
  the equivalent. Understand how it's currently rendered.
- Identify whether the value you're about to change is **CSS pixels**,
  **viewport units**, **3D world units** (three.js / 3d-force-graph), or
  **device pixels**. Picking the wrong unit space is the #1 cause of "off by
  10x" UI bugs.
- If the user said "adjacent to" or "next to" or "inline with", find the
  DOM container of the target element and place the new element **inside the
  same container** — never at opposite ends of a parent flex bar.

## 2. Baseline screenshot (before)

- Start the dev server (Vite / `tesserae serve` / etc) on a known port if not
  already running.
- Navigate via `mcp__plugin_playwright_playwright__browser_navigate` to the
  exact page that contains the affected component.
- Take a baseline screenshot:
  `mcp__plugin_playwright_playwright__browser_take_screenshot`.
- Capture the affected element's bounding box via
  `mcp__plugin_playwright_playwright__browser_evaluate`:
  ```js
  document.querySelector('<selector>').getBoundingClientRect()
  ```
- Record both. The baseline is the comparison anchor.

## 3. Make the change

- Edit the CSS/component file.
- Save. If a dev server is involved, confirm HMR picked it up (or restart).

## 4. After screenshot

- Reload the page (or rely on HMR).
- Take an "after" screenshot.
- Re-measure the bounding box.

## 5. Compare against the user's request

- Compute the delta from before → after.
- Sanity-check against the literal request:
  - "Shrink to ~half" → after height ≤ before × 0.6.
  - "Move adjacent to title" → after element's `x` is within a few px of the
    title element's `x + width`, and they share the same `y`.
  - "Hide on mobile" → after `display: none` at viewport ≤ 640.
- If the delta does NOT match the request, GO BACK TO STEP 3 and iterate
  before responding to the user. Up to 3 iterations; if you're still off,
  describe what you saw and ASK before guessing again.

## 6. Report

When responding to the user:
- Attach the before + after screenshots (or describe what changed if the
  screenshots are too large).
- Quote the bounding-box delta (e.g. "heatmap height 480 → 220, width
  unchanged").
- ONLY THEN claim the task is complete.

## Anti-patterns to avoid

- "I changed the value, looks correct" — without a screenshot.
- Editing a 3D-graph `nodeRelSize` to fix a 2D-view rendering issue (wrong
  space).
- Placing a new "pill" at `justify-content: flex-end` when the user asked
  for it inline with the title (opposite ends of the same bar).
- Skipping the after-screenshot because the before "looked fine".


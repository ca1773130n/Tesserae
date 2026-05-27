"""Structural assertions on the client JS bundle.

We have no headless browser; these tests are intentionally cheap checks that
the assembled ``JS_BUNDLE`` carries the right hooks for the wiki frontend's
keyboard shortcuts, theme toggle, search palette, and 3D graph wiring.

If ``node`` is on PATH the bundle is also piped through ``node --check`` to
catch template-literal / string-escaping mistakes that would otherwise only
surface in a browser.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tesserae.site.js import (
    JS_BUNDLE,
    JS_BUNDLE_BASE,
    JS_BUNDLE_GRAPH,
    JS_DOC_TREE,
    JS_GRAPH,
    JS_MERMAID_RENDER,
    JS_SEARCH_PALETTE,
    JS_SESSION_TURN_SCROLLSPY,
    JS_THEME_TOGGLE,
    JS_TOC_SCROLLSPY,
)


# ---------------------------------------------------------------------------
# Theme toggle
# ---------------------------------------------------------------------------

def test_bundle_wires_data_toggle_theme_clicks():
    assert "data-toggle-theme" in JS_BUNDLE
    # The handler closes on the click target with that selector and toggles.
    assert "closest" in JS_THEME_TOGGLE
    assert "data-theme" in JS_THEME_TOGGLE


def test_bundle_uses_tesserae_theme_localstorage_key():
    assert "tesserae-theme" in JS_BUNDLE
    assert "localStorage" in JS_BUNDLE


def test_bundle_follows_prefers_color_scheme():
    assert "prefers-color-scheme" in JS_BUNDLE


def test_bundle_updates_aria_label_on_theme_toggle():
    assert "aria-label" in JS_THEME_TOGGLE


# ---------------------------------------------------------------------------
# Search palette
# ---------------------------------------------------------------------------

def test_bundle_renders_mermaid_diagrams():
    assert "cdn.jsdelivr.net/npm/mermaid" in JS_MERMAID_RENDER
    assert "document.querySelectorAll('.mermaid[data-mermaid-source]')" in JS_MERMAID_RENDER
    assert "mermaid.render" in JS_MERMAID_RENDER
    assert "data-mermaid-rendered" in JS_MERMAID_RENDER
    assert "data-mermaid-error" in JS_MERMAID_RENDER
    assert JS_MERMAID_RENDER in JS_BUNDLE_BASE


def test_bundle_fetches_search_index_json():
    assert "search-index.json" in JS_BUNDLE
    assert "fetch(" in JS_SEARCH_PALETTE


def test_bundle_keyboard_shortcuts_present():
    # cmd+k / ctrl+k variants
    assert "metaKey" in JS_BUNDLE and "ctrlKey" in JS_BUNDLE
    # the literal '/' shortcut
    assert "'/'" in JS_BUNDLE
    # Escape
    assert "'Escape'" in JS_BUNDLE
    # Graph view shortcuts
    assert "'f'" in JS_BUNDLE
    assert "'r'" in JS_BUNDLE
    assert "'2'" in JS_BUNDLE
    assert "'3'" in JS_BUNDLE


def test_bundle_handles_data_open_search_buttons():
    assert "data-open-search" in JS_BUNDLE


def test_bundle_palette_arrow_navigation():
    assert "ArrowDown" in JS_SEARCH_PALETTE
    assert "ArrowUp" in JS_SEARCH_PALETTE
    assert "Enter" in JS_SEARCH_PALETTE


def test_bundle_palette_recents_storage():
    assert "tesserae-recents" in JS_BUNDLE


# ---------------------------------------------------------------------------
# 3D graph view
# ---------------------------------------------------------------------------

def test_bundle_zoom_uses_canonical_before_after_translate_algorithm():
    """Issue 3 (v16) — cursor-anchored zoom uses the canonical THREE
    algorithm: capture cursor world point BEFORE the dolly, apply a
    pure dolly, re-capture AFTER, then translate BOTH camera AND target
    by ``(before - after)`` so the world point under the cursor sticks.

    Previous rounds used ``Math.exp(event.deltaY * 0.001)`` for the
    dolly factor. That's platform-dependent — Mac trackpad sends
    ``deltaY`` ~ 1-5, Windows wheel mouse sends ~ 100 — so the zoom was
    imperceptible on the Mac and a wild jump on Windows. v16 keys off
    only the SIGN of ``deltaY`` (10% per click, identical on every
    device). The previous lerpVectors-on-position-and-target pattern
    is also forbidden.
    """
    # Wheel handler is exclusive (no library zoom).
    assert "addEventListener('wheel'" in JS_GRAPH
    assert "controls.enableZoom = false" in JS_GRAPH
    # Issue 3 (v16) — damping is OFF (it interpolates camera between
    # frames, fighting our manual position mutations).
    assert "controls.enableDamping = false" in JS_GRAPH
    assert "controls.dampingFactor = 0.08" in JS_GRAPH
    # Console signal so the user can confirm the new path is loaded.
    # We log v16 first; v15 is also logged for back-compat with anyone
    # grepping the console for the previous tag.
    assert '[graph] cursor zoom v16 active' in JS_GRAPH
    assert '[graph] cursor zoom v15 active' in JS_GRAPH
    # The canonical algorithm signatures (per spec).
    assert "intersectPlane" in JS_GRAPH
    assert "function cursorWorldOnTargetPlane" in JS_GRAPH
    assert "controls.target.add(delta)" in JS_GRAPH
    assert "camera.position.add(delta)" in JS_GRAPH
    # Sign-only factor: 10% per click, identical on every device.
    assert "event.deltaY > 0 ? 1.10 : 0.90" in JS_GRAPH
    # FORBIDDEN: the broken Math.exp(deltaY * k) pattern that scaled
    # with the platform-dependent magnitude of deltaY.
    assert "Math.exp(event.deltaY" not in JS_GRAPH
    # The wheel listener attaches with ``capture: true`` and
    # ``passive: false`` so we receive the event before any library
    # handler and can preventDefault.
    assert "{ passive: false, capture: true }" in JS_GRAPH
    # The listener attaches to the actual canvas element returned by
    # the renderer (not the wrapper).
    assert "renderer.domElement" in JS_GRAPH
    # Reused primitives — single THREE.Raycaster / THREE.Plane / etc.
    # declared once outside the wheel listener to avoid GC churn.
    assert "var raycaster = new THREE.Raycaster()" in JS_GRAPH
    assert "var plane = new THREE.Plane()" in JS_GRAPH
    # FORBIDDEN: the old lerpVectors-of-position-and-target pattern that
    # got the math wrong in earlier rounds.
    assert "camera.position.lerpVectors(cursor, camera.position, factor)" not in JS_GRAPH
    assert "controls.target.lerpVectors(cursor, controls.target, factor)" not in JS_GRAPH
    # Aggressive logging on the first wheel event so the user can
    # confirm in DevTools that the sign-only factor + before/after
    # anchor are firing as expected.
    assert "[graph] wheel #" in JS_GRAPH
    assert "deltaY=" in JS_GRAPH
    assert "factor=" in JS_GRAPH


def test_bundle_uses_scalar_node_and_link_opacity():
    """3d-force-graph's ``nodeOpacity`` / ``linkOpacity`` accept ONLY a
    scalar number — passing a function silently corrupts the material
    opacity to NaN and renders every node invisible. Verified empirically.

    A previous round attempted a smooth per-node opacity tween via the
    accessor pattern; that broke node rendering completely. Forbid the
    accessor form so we never regress. Selective dimming uses
    ``nodeColor`` / ``linkColor`` accessors (which DO accept functions)
    and ``nodeVisibility`` / ``linkVisibility`` for binary on/off.
    """
    assert "inst.nodeOpacity(0.95)" in JS_GRAPH
    # F-6 — linkOpacity is pinned to 1.0 because the per-link rgba already
    # encodes the alpha (0.5 default, 0.05 dim, 0.5 hot). The previous
    # 0.35 scalar double-multiplied the alpha and washed edges out far
    # below the documented spec.
    assert "inst.linkOpacity(1.0)" in JS_GRAPH
    assert "inst.linkOpacity(0.35)" not in JS_GRAPH
    assert "inst.nodeOpacity(function(n)" not in JS_GRAPH
    assert "inst.linkOpacity(function(l)" not in JS_GRAPH


def test_bundle_disposes_previous_graph_before_rebuild():
    """Mode switching (2D <-> 3D) calls ``buildGraph`` which previously
    leaked a ``THREE.WebGLRenderer`` per call. Browsers cap WebGL
    contexts (~16 in Chrome) and after a few switches Chrome started
    refusing new contexts:
        "THREE.WebGLRenderer: A WebGL context could not be created.
         Reason: Web page caused context loss and was blocked"
    The fix disposes the prior ``Graph`` instance via ``_destructor()``
    (the undocumented but stable 3d-force-graph teardown hook) at the
    top of every ``buildGraph`` call, BEFORE the container is emptied
    so the renderer and scene get a chance to release GL state.
    """
    assert "function buildGraph(" in JS_GRAPH
    # The disposal block must reference _destructor and pre-date the
    # container.removeChild loop in the same function.
    body_start = JS_GRAPH.index("function buildGraph(")
    body = JS_GRAPH[body_start:body_start + 4000]
    assert "Graph._destructor" in body
    dispose_idx = body.index("Graph._destructor")
    clear_idx = body.index("container.removeChild")
    assert dispose_idx < clear_idx, (
        "_destructor must run BEFORE the container is cleared"
    )


def test_bundle_link_hover_wired():
    assert "linkHoverPrecision" in JS_GRAPH
    assert "onLinkHover" in JS_GRAPH
    assert "onLinkClick" in JS_GRAPH


def test_graph_node_activation_zooms_before_navigation():
    assert "function activateNode" in JS_GRAPH
    assert "var samePinned = pinnedNode" in JS_GRAPH
    assert "focusOnNode(node)" in JS_GRAPH
    assert "if (node.href) window.location.href = node.href" in JS_GRAPH
    assert "if (node.href) window.location.href = node.href;\n        })" not in JS_GRAPH


def test_graph_link_activation_focuses_relationship_before_navigation():
    assert "function activateLink" in JS_GRAPH
    assert "var samePinned = pinnedLink" in JS_GRAPH
    assert "focusOnLink(link)" in JS_GRAPH
    # Issue 2 — the bottom-right info panel is gone; the cursor-following
    # tooltip + focused-node label sprite cover hover preview + focus
    # display. Click activation hides the tooltip so the focused label
    # can carry the focus details inline.
    assert "showLinkInfoPanel" not in JS_GRAPH
    assert "applyLinkHighlight(link)" in JS_GRAPH


def test_graph_static_fallback_is_explorable_not_anchor_navigation():
    assert "focusFallbackNode" in JS_GRAPH
    assert "focusFallbackLink" in JS_GRAPH
    assert "activateNode(n, evt)" in JS_GRAPH
    assert "activateLink(e, evt)" in JS_GRAPH
    assert "createElementNS(NS, 'a')" not in JS_GRAPH


def test_graph_selection_fades_and_deprioritizes_non_neighbors():
    assert "function isDimmedNode" in JS_GRAPH
    assert "function isDimmedLink" in JS_GRAPH
    # Non-incident nodes snap to a desaturated grey at alpha 0.25. The
    # smooth-lerp variant was reverted because the per-frame nodeColor
    # re-poke caused render hangs on the live corpus.
    assert "rgba(120,116,108,0.25)" in JS_GRAPH
    assert "EDGE_COLOR_DIM" in JS_GRAPH
    assert "if (isDimmedNode(node)) return" in JS_GRAPH
    assert "if (isDimmedLink(link)) return" in JS_GRAPH
    assert "pointerEvents = dim ? 'none' : 'auto'" in JS_GRAPH


def test_graph_edges_are_visible_lines_not_only_particles():
    # HypePaper-aligned edge palette: WHITE at 0.18 alpha for the resting
    # state (subtle webbing over the deep-dark canvas), YELLOW at 0.85
    # alpha for hover/focus-incident (clearly lit cue against the dim
    # background — same gold-amber the focus label uses). The previous
    # round used 0.5/0.5 which read as "too lit" under the new #060A14
    # backdrop. Forbid the prior light-blue overlay.
    assert "rgba(255,255,255,0.18)" in JS_GRAPH
    assert "rgba(250,204,21,0.85)" in JS_GRAPH
    assert "rgba(191,219,254,0.34)" not in JS_GRAPH
    # F-6 — linkOpacity is now pinned to 1.0 (alpha lives in the rgba).
    assert "if (inst.linkOpacity) inst.linkOpacity(1.0);" in JS_GRAPH
    # Issue 4 — edges thinner everywhere; widths now scale by camera
    # distance so they grow when zoomed out and shrink when zoomed in.
    # Defaults: 0.25, incident: 0.9. Multiplied by ``camScale``.
    assert "function isHoverIncidentLink" in JS_GRAPH
    assert "if (highlightLinks.has(l)) return 0.9 * camScale;" in JS_GRAPH
    assert "if (isHoverIncidentLink(l)) return 0.9 * camScale;" in JS_GRAPH
    assert "return 0.25 * camScale;" in JS_GRAPH
    # Forbid the previous round's thicker widths.
    assert "if (highlightLinks.has(l)) return 2.0;" not in JS_GRAPH
    assert "if (isHoverIncidentLink(l)) return 0.75;" not in JS_GRAPH
    assert "line.setAttribute('stroke-width', '0.24');" in JS_GRAPH
    assert "el.setAttribute('stroke-width', hot ? '0.85' : '0.28');" in JS_GRAPH
    assert "if (inst.linkThreeObjectExtend) inst.linkThreeObjectExtend(true);" in JS_GRAPH
    # The linkColor function branches on hover-incident too. It picks a
    # base from the focus/hover ladder; the dim transition is a snap
    # (the per-frame opacity lerp was removed in F-12 — re-poking the
    # accessors every frame hung the page on the 388-node corpus).
    assert "if (highlightLinks.has(l)) return EDGE_COLOR_HOT;" in JS_GRAPH
    assert "if (isHoverIncidentLink(l)) return EDGE_COLOR_HOT;" in JS_GRAPH


def test_graph_dimmed_labels_are_hidden_with_dimmed_nodes_and_edges():
    assert "if (isDimmedNode(n)) return null" in JS_GRAPH
    assert "if (isDimmedNode(n)) return;" in JS_GRAPH
    assert "if (isDimmedLink(l)) return null" in JS_GRAPH
    assert "if (isDimmedLink(l)) return;" in JS_GRAPH


def test_graph_focus_zoom_is_moderate():
    assert "var distance = 300" in JS_GRAPH
    assert "Math.max(240, Math.hypot" in JS_GRAPH
    assert "Graph.zoom(1.8" in JS_GRAPH
    assert "Graph.zoom(4" not in JS_GRAPH
    assert "var box = 420" in JS_GRAPH


def test_bundle_node_labels_present_in_both_modes():
    """Issue 1 + 2 — both render paths apply the same variant hierarchy
    (default / neighbor / hover / focused) so the relative prominence of
    each label matches between 2D and 3D for the same node. Issue 1
    additionally requires NO text stroke on either path — the pill is
    the indicator, never an outlined letterform."""
    assert "nodeThreeObject" in JS_GRAPH
    assert "nodeCanvasObject" in JS_GRAPH
    assert "function shouldShowOverviewLabel" in JS_GRAPH
    assert "Math.floor(vals.length * 0.86)" in JS_GRAPH
    # 2D variant decision tree mirrors the 3D nodeThreeObject group.
    assert "var isHovered = (hoverNode === n) && !isFocused;" in JS_GRAPH
    assert "var isFocusedNeighbor = focusedNode" in JS_GRAPH
    # 2D mode now renders ALL labels (the previous filter via
    # shouldShowOverviewLabel was removed — the user wants every node
    # named in the flat layout). Importance drives alpha instead.
    assert "function degreeImportanceAlpha" in JS_GRAPH
    # Issue 1 — NO ``ctx.strokeText`` calls anywhere in the 2D painter.
    assert "ctx.strokeText(label" not in JS_GRAPH


def test_graph_node_colors_vary_within_type_family():
    assert "var GROUP_HSL" in JS_GRAPH
    assert "function hashString" in JS_GRAPH
    assert "function nodeColorVariant" in JS_GRAPH
    assert "n.color = n.color || nodeColorVariant(n);" in JS_GRAPH
    assert "return 'hsl(' + hue + ' ' + sat + '% ' + light + '%)'" in JS_GRAPH


def test_graph_static_fallback_labels_follow_focus_state():
    assert "data-node-label-id" in JS_GRAPH
    assert "text.textContent = nodeLabelText(n);" in JS_GRAPH
    assert "querySelectorAll('text[data-node-label-id]')" in JS_GRAPH
    assert "el.setAttribute('opacity', dim ? '0' : (hot ? '1' : '0.72'))" in JS_GRAPH


def test_bundle_edge_labels_present():
    assert "linkThreeObject" in JS_GRAPH or "linkCanvasObject" in JS_GRAPH


def test_bundle_fit_uses_engine_stop_and_camera_position():
    assert "onEngineStop" in JS_GRAPH
    assert "if (pinnedNode || pinnedLink) return;" in JS_GRAPH
    assert "function scheduleCenteredFit" in JS_GRAPH
    assert "function sizeGraphToContainer" in JS_GRAPH
    assert "if (inst.width) inst.width(w);" in JS_GRAPH
    assert "if (inst.height) inst.height(h);" in JS_GRAPH
    assert "setTimeout(scheduleCenteredFit, 350)" in JS_GRAPH
    assert "controls.target.set(center.x, center.y, center.z)" in JS_GRAPH
    assert "cameraPosition" in JS_GRAPH


def test_graph_auto_fit_runs_exactly_once_via_has_initial_fit_flag():
    """The polish pass replaced the multi-pass auto-fit (which fired on
    every onEngineStop) with a single-shot ``hasInitialFit`` guard."""
    assert "var hasInitialFit = false" in JS_GRAPH
    # Guard inside scheduleCenteredFit: returns early once the flag flips.
    assert "if (hasInitialFit || pinnedNode || pinnedLink) return;" in JS_GRAPH
    assert "hasInitialFit = true;" in JS_GRAPH
    # onEngineStop bails out unconditionally once the first fit has run.
    assert "if (hasInitialFit) return;" in JS_GRAPH
    # Mode switch resets the flag so the new projection still gets framed.
    assert "hasInitialFit = false;" in JS_GRAPH
    # The old 5-pass [250, 900, 1800, 3600, 6200] fade-in is gone.
    assert "[250, 900, 1800, 3600, 6200]" not in JS_GRAPH


def test_graph_resize_handler_does_not_auto_refit():
    """The resize handler used to auto-fit on every resize, which felt
    like the camera was zooming-out on its own. Now it just resizes the
    canvas; the user re-fits on demand via ``f`` or the Fit button."""
    assert "function installGraphResize" in JS_GRAPH
    assert "addEventListener('resize'" in JS_GRAPH
    # Inside the resize callback we resize the canvas; we do NOT call
    # fitAll — that's the load-bearing change.
    assert "sizeGraphToContainer(inst);\n        }, 120);" in JS_GRAPH


def test_graph_initial_camera_position_is_known():
    """The first frame parks the camera at z=600 so we don't see a wild
    zoom-out from the origin before the simulation settles."""
    assert "inst.cameraPosition({ x: 0, y: 0, z: 1000 }, { x: 0, y: 0, z: 0 }, 0)" in JS_GRAPH


def test_graph_labels_are_truncated():
    assert "function shortLabel" in JS_GRAPH
    assert "function nodeLabelText" in JS_GRAPH
    assert "shortLabel(n && (n.name || n.id), 24)" in JS_GRAPH
    assert "function edgeLabelText" in JS_GRAPH
    assert "shortLabel(l && (l.label || l.type), 18)" in JS_GRAPH
    # Issue 1 — the label factory is now ``makeLabel(text, opts)`` with a
    # ``variant`` switch (default / neighbor / hover / focused / edge).
    # ``makeLabelSprite`` / ``makeSpriteLabel`` survive as back-compat
    # shims that delegate to ``makeLabel``.
    assert "function makeLabel(" in JS_GRAPH
    assert "function makeLabelSprite" in JS_GRAPH
    assert "function makeSpriteLabel" in JS_GRAPH
    # 3D nodeThreeObject uses the new factory directly.
    assert "makeLabel(nodeLabelText(n)" in JS_GRAPH
    assert "ctx.fillText(text" in JS_GRAPH


def test_graph_3d_sprite_labels_render_above_nodes():
    # Issue 2 — every label sprite (base/neighbor/hover/focused) renders
    # with depth disabled so they always sit on top of nodes/edges.
    assert "depthWrite: false" in JS_GRAPH
    assert "depthTest: false" in JS_GRAPH
    # Render-order ladder: focused/hover (999) > neighbor (998) > base (990).
    assert "sprite.renderOrder = VARIANT_RENDER_ORDER" in JS_GRAPH
    assert "VARIANT_RENDER_ORDER" in JS_GRAPH
    assert "focused: 999" in JS_GRAPH
    assert "neighbor: 998" in JS_GRAPH


def test_graph_3d_labels_use_camera_distance_opacity():
    assert "function cameraDistanceOpacity" in JS_GRAPH
    assert "function applySpriteOpacity" in JS_GRAPH
    assert "if (d < 120) return 0.26" in JS_GRAPH
    assert "inst.nodePositionUpdate" in JS_GRAPH
    assert "applySpriteOpacity(sprite, cameraDistanceOpacity" in JS_GRAPH


def test_bundle_day_filter_listener():
    assert "data-graph-filter-day" in JS_GRAPH or "data-day-click" in JS_GRAPH


# ---------------------------------------------------------------------------
# TOC scrollspy
# ---------------------------------------------------------------------------


def test_bundle_base_includes_toc_scrollspy():
    """Every page loads the scrollspy as part of the base bundle."""
    assert "IntersectionObserver" in JS_BUNDLE_BASE
    assert "data-toc-target" in JS_BUNDLE_BASE
    # The scrollspy module itself.
    assert "IntersectionObserver" in JS_TOC_SCROLLSPY
    assert "data-toc-target" in JS_TOC_SCROLLSPY


def test_toc_scrollspy_uses_top_band_root_margin():
    """rootMargin ``-20% 0px -70% 0px`` puts the active band in the top
    fifth of the viewport so the highlight follows the heading you're
    actually reading."""
    assert "-20% 0px -70% 0px" in JS_TOC_SCROLLSPY


def test_toc_scrollspy_handles_intersection_observer_absence():
    """Falls back gracefully when the browser lacks IntersectionObserver."""
    assert "typeof IntersectionObserver === 'undefined'" in JS_TOC_SCROLLSPY


def test_toc_scrollspy_smooth_scrolls_on_anchor_click():
    """TOC item click smoothly scrolls to the heading."""
    assert "scrollIntoView" in JS_TOC_SCROLLSPY
    assert "behavior: 'smooth'" in JS_TOC_SCROLLSPY
    assert "block: 'start'" in JS_TOC_SCROLLSPY


def test_session_turn_scrollspy_highlights_visible_turn():
    assert "data-session-turn-target" in JS_SESSION_TURN_SCROLLSPY
    assert "aria-current" in JS_SESSION_TURN_SCROLLSPY
    assert "li.is-active" not in JS_SESSION_TURN_SCROLLSPY  # CSS owns styling
    assert "data-session-turn-target" in JS_BUNDLE_BASE


def test_toc_scrollspy_targets_article_body_h2_h3():
    """Scrollspy keys off h2/h3 inside the canonical .article-body container."""
    assert ".article-body h2[id], .article-body h3[id]" in JS_TOC_SCROLLSPY


def test_bundle_graph_alias_matches_js_graph():
    """JS_BUNDLE_GRAPH (used by the graph route) is the JS_GRAPH module."""
    assert JS_BUNDLE_GRAPH is JS_GRAPH or JS_BUNDLE_GRAPH == JS_GRAPH


# ---------------------------------------------------------------------------
# Doc-tree filter (Issue 3)
# ---------------------------------------------------------------------------


def test_bundle_includes_doc_tree_search_filter():
    """Every page ships the doc-tree filter as part of the base bundle."""
    assert "data-doc-tree-search" in JS_BUNDLE_BASE
    assert "data-doc-tree-search" in JS_DOC_TREE
    # Filter targets the .doc-tree leaves and their <details> ancestors.
    assert ".doc-tree" in JS_DOC_TREE
    assert ".doc-tree-leaf" in JS_DOC_TREE
    assert "details.doc-tree-folder" in JS_DOC_TREE
    # Auto-expand <details> ancestors of every match so the tree reveals
    # the matching leaves without the user clicking through folders.
    assert "matchedFolders" in JS_DOC_TREE
    # Substring match against data-doc-path (case-insensitive lower-case).
    assert "data-doc-path" in JS_DOC_TREE
    assert "toLowerCase" in JS_DOC_TREE
    # Debounce so typing doesn't thrash the DOM.
    assert "setTimeout" in JS_DOC_TREE
    # Escape clears the filter — UX nicety.
    assert "'Escape'" in JS_DOC_TREE


# ---------------------------------------------------------------------------
# Graph rebuild — Bugs 3-7 (size-by-degree, focused-label, orbit, dpr, etc.)
# ---------------------------------------------------------------------------


def test_graph_uses_node_rel_size_for_perceptible_radius_differences():
    """Bug 3 — bump nodeRelSize from default 4 to 6 so the sqrt-scaled
    sphere volume actually reads as different sizes in the canvas."""
    assert "nodeRelSize" in JS_BUNDLE_GRAPH
    assert "nodeRelSize(6)" in JS_BUNDLE_GRAPH


def test_graph_focused_node_label_scales_up_with_outline():
    """Issue 2 + 3 — selecting a node swaps in a larger 22px white label on
    a slightly-more-opaque dark pill. NO color border, NO accent stroke,
    NO ``[Enter] Open page`` hint. The Enter-key handler still works."""
    # The dual-sprite group keys off node.__focused (a per-node flag).
    assert "__focused" in JS_BUNDLE_GRAPH
    assert "function makeFocusedSpriteLabel" in JS_BUNDLE_GRAPH
    assert "function markFocused" in JS_BUNDLE_GRAPH
    # Focused label uses the unified factory with variant=focused.
    assert "function makeLabel(" in JS_BUNDLE_GRAPH
    assert "variant: 'focused'" in JS_BUNDLE_GRAPH
    assert "isFocusedLabel" in JS_BUNDLE_GRAPH
    # Issue 2 — hover drops to 18px (down from 22) and focused drops to
    # 22px (down from 26) because the pill itself is the focus
    # indicator, so the font no longer has to do all the work.
    # Edge label font dropped from 10 to 7 per user request — much
    # smaller, white, no pill behind.
    assert "{ default: 11, edge: 7, neighbor: 14, hover: 18, focused: 22 }" in JS_BUNDLE_GRAPH
    # Issue 1 — explicit "NO text stroke. NO outline. NO border." on
    # every variant. F-12 deleted the previously-zeroed VARIANT_STROKE
    # table outright (nothing read it); the regression guard against the
    # table coming back lives in test_graph_f12_dead_state_cleanup_removed.
    assert "VARIANT_STROKE" not in JS_BUNDLE_GRAPH
    # The focused sprite anchors above the node (positive +y offset based
    # on node.val so it never overlaps the sphere itself).
    assert "n.val * 1.2 + 8" in JS_BUNDLE_GRAPH
    # nodeThreeObject builds a Group so the focused / hover / neighbor /
    # default / glow sprites can be toggled individually per frame.
    assert "new THREE.Group()" in JS_BUNDLE_GRAPH
    assert "nodeThreeObject" in JS_BUNDLE_GRAPH
    # Issue 3 — the visible "[Enter] Open page" hint sprite is GONE.
    assert "'[Enter] Open page'" not in JS_BUNDLE_GRAPH
    # The Enter-key handler still navigates focused-node href on press.
    assert "if (e.key === 'Enter' && focusedNode && focusedNode.href)" in JS_BUNDLE_GRAPH


def test_graph_label_pills_are_transparent_with_no_accent_border():
    """User spec — every label variant renders WITHOUT a background pill
    (alpha 0 across the board). Text is the only visual; highlighted
    variants (hover/focused/neighbor) tint gold on dark and amber on
    light, every other variant is white on dark / near-black on light.
    NO color border. NO text stroke. NO gray.
    """
    # Per-variant pill alpha table (the source of truth).
    assert "VARIANT_PILL_ALPHA" in JS_BUNDLE_GRAPH
    # Every variant is transparent — no background pill anywhere.
    assert (
        "var VARIANT_PILL_ALPHA = { default: 0, edge: 0, neighbor: 0, hover: 0, focused: 0 }"
        in JS_BUNDLE_GRAPH
    )
    # Pill is rendered for EVERY variant (not gated on hasPill any more).
    assert "var hasPill = variant === 'focused'" not in JS_BUNDLE_GRAPH
    # Issue 1 — NO accent stroke / NO color border on the pill any more.
    # The previous round used ``ctx.strokeStyle = accent`` to paint the
    # focused-pill border; that's gone.
    assert "ctx.strokeStyle = accent" not in JS_BUNDLE_GRAPH
    # Default text is PURE WHITE on dark, PURE DARK on light. NO gray.
    assert "'rgb(255, 255, 255)'" in JS_BUNDLE_GRAPH
    assert "'rgb(20, 20, 20)'" in JS_BUNDLE_GRAPH
    assert "rgba(220,225,235,0.85)" not in JS_BUNDLE_GRAPH
    assert "rgba(40,40,50,0.85)" not in JS_BUNDLE_GRAPH
    # Every label variant is wired through the unified factory.
    assert "isFocusedLabel" in JS_BUNDLE_GRAPH
    assert "isHoverLabel" in JS_BUNDLE_GRAPH
    assert "isNeighborLabel" in JS_BUNDLE_GRAPH
    assert "isDefaultLabel" in JS_BUNDLE_GRAPH
    assert "isEdgeLabel" in JS_BUNDLE_GRAPH
    # Per-variant text opacity table (kept for back-compat — text colors
    # are now pure rgb regardless of variant alpha).
    assert "{ default: 0.85, edge: 0.78, neighbor: 0.92, hover: 1.0, focused: 1.0 }" in JS_BUNDLE_GRAPH


def test_graph_label_pill_alpha_is_zero_for_every_variant():
    """User spec: no pill behind any label, every variant transparent."""
    assert (
        "var VARIANT_PILL_ALPHA = { default: 0, edge: 0, neighbor: 0, hover: 0, focused: 0 }"
        in JS_BUNDLE_GRAPH
    )
    # The 2D path gates the pill draw on ``pillAlpha > 0`` so the now-zero
    # alpha actually skips the rect/fill calls rather than emitting an
    # invisible-but-still-rendered shape.
    assert "if (pillAlpha > 0) {" in JS_BUNDLE_GRAPH


def test_graph_highlighted_labels_use_gold_text():
    """User spec: yellow is reserved for the user's interaction TARGET
    (hover/focused). Neighbors stay white as context — they are not the
    target, so tinting them yellow collapsed the visual hierarchy. The
    same HIGHLIGHT_VARIANTS table drives both render paths."""
    # Shared variant table at module scope — neighbor is OUT.
    assert "var HIGHLIGHT_VARIANTS = { hover: 1, focused: 1 }" in JS_BUNDLE_GRAPH
    # New spec yellow: rgb(250, 204, 21) (gold-yellow / amber-yellow,
    # legible on dark canvas; the previous rgb(255, 215, 0) bled into
    # bright node spheres).
    assert "'rgb(250, 204, 21)'" in JS_BUNDLE_GRAPH
    # Light theme highlight — burnt amber for legibility on white.
    assert "'rgb(180, 83, 9)'" in JS_BUNDLE_GRAPH
    # The previous gold value is GONE everywhere (was both 3D and 2D).
    assert "rgb(255, 215, 0)" not in JS_BUNDLE_GRAPH
    assert "rgba(255, 215, 0" not in JS_BUNDLE_GRAPH


def test_graph_default_labels_render_at_full_opacity():
    """Visibility-as-importance: low-importance labels are CULLED, not
    faded. When a default label IS visible it renders at material
    opacity 1.0 with pure white text — no alpha modulation tied to
    degree. The earlier ``impAlpha`` modulation produced gray-looking
    labels on dark canvas and is gone for good."""
    # The 3D sprite material opacity is pinned to 1.0 for every variant.
    assert "applySpriteOpacity(child, 1.0)" in JS_BUNDLE_GRAPH
    # The combined alpha formula (distAlpha * 0.6 + impAlpha * 0.6) is
    # eliminated everywhere.
    assert "distAlpha * 0.6 + impAlpha * 0.6" not in JS_BUNDLE_GRAPH
    # No remaining ``impAlpha`` symbol in the bundle (the variable, the
    # rgba-with-importance string templates, all gone).
    assert "impAlpha" not in JS_BUNDLE_GRAPH
    # The cull helper is wired in and named consistently.
    assert "function computeImportanceCutoff(camDistance)" in JS_BUNDLE_GRAPH
    # 3D path consults it and gates on ``defaultPassesCull``.
    assert "defaultPassesCull" in JS_BUNDLE_GRAPH
    # 2D path early-returns when the node fails the synthetic-distance cull.
    assert "syntheticCamDist" in JS_BUNDLE_GRAPH


def test_graph_focused_neighbor_labels_stay_white():
    """Neighbors are context, not target. They keep the default white
    text fill — the yellow tint is reserved for hover and focused so the
    visual hierarchy survives a focused-with-many-neighbors layout."""
    # Source-of-truth table excludes neighbor.
    assert "var HIGHLIGHT_VARIANTS = { hover: 1, focused: 1 }" in JS_BUNDLE_GRAPH
    # Neighbor variant must NOT appear in the highlight table even as a
    # parenthetical / comment-style override anywhere.
    assert "HIGHLIGHT_VARIANTS = { hover: 1, focused: 1, neighbor: 1 }" not in JS_BUNDLE_GRAPH


def test_graph_highlight_labels_have_glow_shadow():
    """Hover gets a 6px canvas-shadow glow, focused gets 10px. Defaults
    get a subtle 2px drop-shadow so white-on-bright-sphere stays
    readable; the glow is the only legibility separator now that pills
    are transparent across the board."""
    # Glow color in the spec yellow.
    assert "rgba(250, 204, 21," in JS_BUNDLE_GRAPH
    # Both blur magnitudes show up (focused 10, hover 6).
    assert "shadowBlur" in JS_BUNDLE_GRAPH
    assert "isFocused ? 10 : 6" in JS_BUNDLE_GRAPH or "isFocusedLocal ? 10 : 6" in JS_BUNDLE_GRAPH
    # Subtle default drop-shadow present on dark theme.
    assert "rgba(0, 0, 0, 0.7)" in JS_BUNDLE_GRAPH


def test_graph_variant_scale_and_weight_ladder():
    """Hover scales to 1.1×, focused to 1.2× — composed with camera
    distance, not replacing it. Font weight ladder: 500 default,
    600 hover, 700 focused."""
    # 3D sprite per-frame composition multiplies camScale by the bump.
    assert "camScale * 1.1" in JS_BUNDLE_GRAPH
    assert "camScale * 1.2" in JS_BUNDLE_GRAPH
    # 2D path multiplies the base font size by the scale bump.
    assert "isFocusedLocal ? 1.2 : (variant === 'hover' ? 1.1 : 1.0)" in JS_BUNDLE_GRAPH
    # Font weight ladder appears in BOTH the makeLabel canvas factory
    # and the 2D nodeCanvasObject path.
    assert "isFocused ? 700 : (variant === 'hover' ? 600 : 500)" in JS_BUNDLE_GRAPH
    assert "isFocusedLocal ? 700 : (variant === 'hover' ? 600 : 500)" in JS_BUNDLE_GRAPH


def test_graph_fullscreen_button_and_listener_present():
    """Issue 4 — toolbar Fullscreen button toggles fullscreen on the
    wrapper (not the canvas alone) and listens to ``fullscreenchange``
    to apply the ``is-fullscreen`` class to the wrapper."""
    assert "data-graph-action=\"fullscreen\"" in JS_BUNDLE_GRAPH or "btnFullscreen" in JS_BUNDLE_GRAPH
    assert "function toggleGraphFullscreen" in JS_BUNDLE_GRAPH
    assert "wrapper.requestFullscreen" in JS_BUNDLE_GRAPH
    assert "fullscreenchange" in JS_BUNDLE_GRAPH
    assert "is-fullscreen" in JS_BUNDLE_GRAPH
    # Resize on fullscreen toggle re-fits the canvas to the WRAPPER, not viewport.
    assert "sizeGraphToContainer" in JS_BUNDLE_GRAPH


def test_graph_hover_uses_cursor_tooltip_not_overlay_panel():
    """Issue 2 — the bottom-right ``#graph-info-panel`` overlay is GONE.
    Hover preview lives in a single cursor-following ``#graph-tooltip``
    element. Per-frame DOM mutations are bounded to ``style.left`` /
    ``style.top`` + ``textContent`` updates inside the existing element
    (no display:none thrashing — that's what made the page blink)."""
    # The tooltip element is the only piece of right-side UI the bundle
    # touches now.
    assert "getElementById('graph-tooltip')" in JS_BUNDLE_GRAPH
    # The dead overlay-panel IDs must not be referenced anywhere.
    assert "getElementById('graph-info-panel')" not in JS_BUNDLE_GRAPH
    assert "getElementById('graph-info-empty')" not in JS_BUNDLE_GRAPH
    assert "getElementById('graph-info-content')" not in JS_BUNDLE_GRAPH
    assert "getElementById('graph-info-neighbors')" not in JS_BUNDLE_GRAPH
    assert "function renderNeighborList" not in JS_BUNDLE_GRAPH
    assert "function showInfoPanel" not in JS_BUNDLE_GRAPH
    assert "function showLinkInfoPanel" not in JS_BUNDLE_GRAPH
    # Tooltip is positioned by cursor offset (+12, +14) per spec.
    assert "function positionTooltip" in JS_BUNDLE_GRAPH
    assert "(x + 12) + 'px'" in JS_BUNDLE_GRAPH
    assert "(y + 14) + 'px'" in JS_BUNDLE_GRAPH
    # Hover populates name + meta + clamped description + click hint.
    assert "function showNodeTooltip" in JS_BUNDLE_GRAPH
    assert "function showLinkTooltip" in JS_BUNDLE_GRAPH
    assert "function clampDesc" in JS_BUNDLE_GRAPH
    assert "TOOLTIP_DESC_LIMIT = 120" in JS_BUNDLE_GRAPH
    # The "click to focus" hint string was removed — the user found it
    # noise. Forbid it from regressing.
    assert "'click to focus'" not in JS_BUNDLE_GRAPH
    # No display:none thrashing — the only DOM mutation per frame is the
    # ``hidden`` attribute toggle + style.left/top + textContent.
    assert "tooltip.hidden = false" in JS_BUNDLE_GRAPH
    assert "tooltip.hidden = true" in JS_BUNDLE_GRAPH


def test_graph_camera_orbits_focused_node_via_engine_tick():
    """Bug 5 — clicking a node animates the camera 200u away in +Z, sets
    controls.target to the node, and starts an auto-orbit driven by
    onEngineTick (no separate requestAnimationFrame loop)."""
    # The orbit hook is wired through the library's per-frame callback.
    assert "onEngineTick" in JS_BUNDLE_GRAPH
    # The orbit state lives in module-scope vars.
    assert "var autoOrbitEnabled" in JS_BUNDLE_GRAPH
    assert "var orbitAngle" in JS_BUNDLE_GRAPH
    assert "var orbitRadius" in JS_BUNDLE_GRAPH
    assert "var focusedNode" in JS_BUNDLE_GRAPH
    # Camera position is recomputed each tick using sin/cos of orbitAngle.
    assert "Math.sin(orbitAngle)" in JS_BUNDLE_GRAPH
    assert "Math.cos(orbitAngle)" in JS_BUNDLE_GRAPH
    # focusOnNode sets controls.target so manual orbit pivots around the
    # focused node (not the world origin).
    # Orbit target now snaps to the cluster centroid (cx,cy,cz) — and is
    # deferred via setTimeout so the cameraPosition tween is visible
    # rather than instantly overridden by a sync controls.update().
    assert "controls.target.set(cx, cy, cz)" in JS_BUNDLE_GRAPH
    # ``cameraPosition(`` is the library's animation hook for the fly-in.
    assert "cameraPosition(" in JS_BUNDLE_GRAPH


def test_graph_orbit_disengages_on_user_drag():
    """Bug 5 — once the user grabs the camera, auto-orbit must stop
    fighting them. OrbitControls fires ``start`` on mouse-down."""
    assert "_controls.addEventListener('start'" in JS_BUNDLE_GRAPH
    assert "autoOrbitEnabled = false" in JS_BUNDLE_GRAPH


def test_graph_keyboard_shortcuts_include_orbit_and_unfocus():
    """Bug 5 — ``o`` toggles auto-orbit; ``Esc`` unfocuses + auto-fits."""
    assert "if (e.key === 'o')" in JS_BUNDLE_GRAPH
    # Esc resets focus state (focusedNode + markFocused(null)).
    assert "focusedNode = null" in JS_BUNDLE_GRAPH
    assert "markFocused(null)" in JS_BUNDLE_GRAPH


def test_graph_label_variants_unified_across_2d_and_3d():
    """Issue 1 + 4 — the same five-variant hierarchy (default / neighbor
    / hover / focused / edge) drives label rendering in BOTH 2D and 3D
    so the relative prominence of a node's label matches between modes."""
    # 2D variant decision tree mirrors the 3D nodeThreeObject group.
    # The user wants ALL labels in 2D — the prior overview-filter
    # ``else if (showDefault)`` was replaced by an unconditional
    # ``else variant = 'default'`` and per-node alpha modulation via
    # ``degreeImportanceAlpha``.
    assert "if (isFocused) variant = 'focused';" in JS_BUNDLE_GRAPH
    assert "else if (isHovered) variant = 'hover';" in JS_BUNDLE_GRAPH
    assert "else if (isFocusedNeighbor) variant = 'neighbor';" in JS_BUNDLE_GRAPH
    assert "else variant = 'default';" in JS_BUNDLE_GRAPH
    # Variant tables drive both paths.
    assert "VARIANT_FONT" in JS_BUNDLE_GRAPH
    assert "VARIANT_OPACITY" in JS_BUNDLE_GRAPH
    # F-12 — VARIANT_STROKE was kept for back-compat after the user
    # banned text strokes (Issue 1). The cleanup pass removed it because
    # nothing read it; the regression guard is in
    # ``test_graph_dead_state_cleanup_removed`` below.
    # Edge labels use the 'edge' variant in both render paths.
    assert "variant: 'edge'" in JS_BUNDLE_GRAPH
    assert "VARIANT_FONT.edge" in JS_BUNDLE_GRAPH
    # Edge labels only render for edges incident to the focused (or
    # hover) node — the same rule in both 2D and 3D.
    assert "var incidentToFocus = focusedNode && (focusedNode === s || focusedNode === t);" in JS_BUNDLE_GRAPH


def test_graph_2d_uses_library_default_cursor_zoom():
    """Issue 3 — in 2D mode (``force-graph``, not ``3d-force-graph``)
    the library zooms toward the cursor by default. We just confirm
    enableNodeDrag is on so the user can rearrange the layout."""
    assert "if (inst.enableNodeDrag) inst.enableNodeDrag(true)" in JS_BUNDLE_GRAPH


def test_graph_pixel_ratio_capped_at_two_for_retina():
    """Bug 7 — uncapped DPR (4x retina, 5x mobile) burns the GPU. Cap at 2."""
    assert "Math.min(window.devicePixelRatio || 1, 2)" in JS_BUNDLE_GRAPH
    assert "setPixelRatio" in JS_BUNDLE_GRAPH


def test_graph_size_uses_sqrt_scaling_via_node_val():
    """Bug 3 — node radius uses sqrt of the val accessor, which build_graph_payload
    seeds with ``2 + sqrt(degree) * 1.6``. The JS uses Math.sqrt to size sprites
    and compute orbit radii."""
    assert "Math.sqrt" in JS_BUNDLE_GRAPH
    # nodeVal accessor is wired through to the library so val drives volume.
    assert "nodeVal(function(n)" in JS_BUNDLE_GRAPH


# ---------------------------------------------------------------------------
# Polish round — Issues 1-6
# ---------------------------------------------------------------------------


def test_graph_label_text_uses_theme_foreground_not_node_color():
    """Issue 1 + Issue 2 — label text fill is PURE WHITE rgb(255, 255, 255)
    on dark theme and PURE DARK rgb(20, 20, 20) on light theme for
    EVERY variant. NO accent color. NO gray. NO stroke. The pill is
    the focus indicator; text never picks up the node accent and never
    carries a per-variant alpha or a text border."""
    # Pure white text on dark theme for every variant.
    assert "'rgb(255, 255, 255)'" in JS_BUNDLE_GRAPH
    # Light theme inverts to a near-black rgb(20, 20, 20).
    assert "'rgb(20, 20, 20)'" in JS_BUNDLE_GRAPH
    # Issue 1 — no text-stroke calls any more (the explicit "NO outline"
    # rule). The factory does not call strokeText at all.
    assert "ctx.strokeText" not in JS_BUNDLE_GRAPH
    # The legacy per-variant text-opacity fills are GONE — text is now
    # rgb(...) (no per-variant alpha) on every variant.
    assert "rgba(255,255,255,' + textOpacity + ')" not in JS_BUNDLE_GRAPH
    assert "rgba(20,20,28,' + textOpacity + ')" not in JS_BUNDLE_GRAPH


def test_graph_theme_toggle_invalidates_label_cache():
    """Issue 1 — the theme toggle exposes ``window.__graphRefreshLabels``
    so the graph view can re-tint label sprites when the user switches
    theme. The graph bundle defines the function (cache-clear + rebuild
    nodeThreeObject), and the theme-toggle JS calls it."""
    assert "window.__graphRefreshLabels" in JS_BUNDLE_GRAPH
    assert "labelSpriteCache.clear()" in JS_BUNDLE_GRAPH
    # The base bundle's theme toggle pokes the hook (no-op on every
    # other route).
    from tesserae.site.js import JS_THEME_TOGGLE
    assert "__graphRefreshLabels" in JS_THEME_TOGGLE


def test_graph_hover_grows_node_and_thickens_incident_edges():
    """Issue 2 + Issue 4 — hovered node sphere grows to ``val * 1.25``
    and incident edges thicken to 0.9 (from the calmer 0.25 baseline)."""
    # nodeVal accessor multiplies the base by 1.25 when hovered.
    assert "if (hoverNode === n) return base * 1.25" in JS_BUNDLE_GRAPH
    # Hover-incident link width = 0.9 (Issue 4 — thinner overall).
    assert "if (isHoverIncidentLink(l)) return 0.9" in JS_BUNDLE_GRAPH
    # Hover handler re-pokes the accessors so the change is visible
    # immediately (without waiting for the next sim tick).
    assert "Graph.nodeVal(Graph.nodeVal())" in JS_BUNDLE_GRAPH
    assert "Graph.linkWidth(Graph.linkWidth())" in JS_BUNDLE_GRAPH


def test_graph_particles_only_on_incident_edges_pure_yellow_smaller():
    """Issue 4 — particles render PURE YELLOW (Material yellow 500) only
    on edges incident to the hovered or focused node. Default state
    (nothing focused, nothing hovered): ZERO particles on every edge —
    the canvas reads as calm. Width drops from 2.5 to 1.5 (smaller)."""
    # Pure yellow particles (Material yellow 500). Not white.
    assert "'rgb(255, 235, 59)'" in JS_BUNDLE_GRAPH
    assert "linkDirectionalParticleColor" in JS_BUNDLE_GRAPH
    # Smaller particle width — dropped to 0.6 per user request.
    assert "linkDirectionalParticleWidth(0.6)" in JS_BUNDLE_GRAPH
    # Speed is now a constant 0.005 (no per-link speed bump on focus).
    assert "linkDirectionalParticleSpeed(0.005)" in JS_BUNDLE_GRAPH
    # Particles are 2 on incident edges (focus or hover), 0 otherwise.
    # Default state has ZERO particles — the canvas is calm by default.
    assert "if (highlightLinks.has(l)) return 2;" in JS_BUNDLE_GRAPH
    assert "if (isHoverIncidentLink(l)) return 2;" in JS_BUNDLE_GRAPH
    assert "return 0;" in JS_BUNDLE_GRAPH
    # Forbid the previous round's "always-on 2 particles" baseline and
    # the "white" particle color.
    assert "return highlightLinks.has(l) ? 4 : 2" not in JS_BUNDLE_GRAPH
    assert "'rgba(255,255,255,0.95)'" not in JS_BUNDLE_GRAPH


def test_graph_auto_browse_wired():
    """Issue 6 — Auto-browse toolbar button cycles the graph through
    high-degree nodes hands-free. Toggleable via the toolbar button or
    the ``b`` keyboard shortcut. Stops on Esc, manual node click, or
    manual mouse-drag."""
    # Toolbar button query selector + click handler.
    assert "[data-graph-action=\"auto-browse\"]" in JS_BUNDLE_GRAPH
    assert "btnAutoBrowse" in JS_BUNDLE_GRAPH
    # State machine.
    assert "autoBrowseActive" in JS_BUNDLE_GRAPH
    assert "function startAutoBrowse" in JS_BUNDLE_GRAPH
    assert "function stopAutoBrowse" in JS_BUNDLE_GRAPH
    assert "function toggleAutoBrowse" in JS_BUNDLE_GRAPH
    assert "function autoBrowseStep" in JS_BUNDLE_GRAPH
    # Recursive setTimeout chain (5s dwell) so cancellation is clean.
    assert "setTimeout" in JS_BUNDLE_GRAPH
    # Per-node dwell bumped from 5s → 9s for a calmer cadence; the user
    # said the previous 5s was too fast to absorb each focus stop.
    assert "AUTO_BROWSE_DWELL_MS = 9000" in JS_BUNDLE_GRAPH
    assert "AUTO_BROWSE_MAX_HOPS = 8" in JS_BUNDLE_GRAPH
    # Highest-degree picker + most-connected unvisited neighbor picker.
    assert "function pickStartNode" in JS_BUNDLE_GRAPH
    assert "function pickNextNeighbor" in JS_BUNDLE_GRAPH
    # Button label flip + aria.
    assert "'Stop browse'" in JS_BUNDLE_GRAPH
    assert "'Auto-browse'" in JS_BUNDLE_GRAPH
    # Cursor cue on the wrapper while a tour is running.
    assert "is-auto-browsing" in JS_BUNDLE_GRAPH
    # Keyboard shortcut + Esc cancellation.
    assert "if (e.key === 'b')" in JS_BUNDLE_GRAPH
    assert "if (autoBrowseActive) stopAutoBrowse()" in JS_BUNDLE_GRAPH


# ---------------------------------------------------------------------------
# Split payload — graph route fetches core first, then rest
# ---------------------------------------------------------------------------


def test_graph_bundle_fetches_split_payload():
    """The graph bundle fetches ``payload-core.json`` first and
    ``payload-rest.json`` second. The literal filenames live in default
    fallbacks for the ``data-payload-(core|rest)-url`` attributes, and the
    fetch calls take the resolved variables — so we assert both the literal
    fallback strings and the variable-based fetch shape."""
    import re as _re
    # Default URL fallbacks the JS reads off the canvas attrs.
    assert "'payload-core.json'" in JS_BUNDLE_GRAPH
    assert "'payload-rest.json'" in JS_BUNDLE_GRAPH
    # Acceptance criterion: ``fetch(.*payload-core.json)`` /
    # ``fetch(.*payload-rest.json)`` match across literal-string or
    # variable-bound forms (``fetch(coreUrl)`` resolves to either at
    # runtime).
    assert _re.search(r"fetch\([^)]*\bcoreUrl\b", JS_BUNDLE_GRAPH) or _re.search(
        r"fetch\([^)]*payload-core\.json", JS_BUNDLE_GRAPH
    ), "graph bundle must fetch payload-core.json on first paint"
    assert _re.search(r"fetch\([^)]*\brestUrl\b", JS_BUNDLE_GRAPH) or _re.search(
        r"fetch\([^)]*payload-rest\.json", JS_BUNDLE_GRAPH
    ), "graph bundle must fetch payload-rest.json after core renders"
    # Promise.all wrapper around the rest fetch keeps the merge path
    # symmetric for future sharding.
    assert "Promise.all([fetch(restUrl)])" in JS_BUNDLE_GRAPH


def test_graph_bundle_merges_rest_via_graph_data():
    """Once ``payload-rest.json`` lands, the merge path calls
    ``forceGraph.graphData(...)`` so the rest fade in without a re-init."""
    assert "__graphMergeRestPayload" in JS_BUNDLE_GRAPH
    assert "Graph.graphData({ nodes: payload.nodes, links: payload.links })" in JS_BUNDLE_GRAPH


def test_graph_bundle_auto_browse_gated_on_rest_loaded():
    """``startAutoBrowse`` must wait for ``__graphRestLoaded`` before seeding
    the tour so the start node picker sees the real top-degree hubs (not
    just the core's local maximum)."""
    assert "if (!window.__graphRestLoaded)" in JS_BUNDLE_GRAPH
    assert "function startAutoBrowse" in JS_BUNDLE_GRAPH


def test_graph_bundle_has_loading_indicator_hooks():
    """The graph wrapper hosts a ``#graph-loading-rest`` element while the
    rest payload is in flight; the bundle toggles its ``.is-visible`` state."""
    assert "getElementById('graph-loading-rest')" in JS_BUNDLE_GRAPH
    assert "function setRestLoading" in JS_BUNDLE_GRAPH


# ---------------------------------------------------------------------------
# Codex review F-1..F-10 regression tests
# ---------------------------------------------------------------------------


def test_graph_f1_rest_merge_refits_camera_when_user_has_not_interacted():
    """F-1 — after ``__graphMergeRestPayload`` runs ``Graph.graphData``
    against the union, it must re-fit the camera so the new nodes get
    framed. The fit is gated on a ``userInteracted`` flag so a click
    that lands before the rest payload arrives doesn't get its camera
    stolen back."""
    assert "var userInteracted = false" in JS_BUNDLE_GRAPH
    # Merge path schedules a fit when the user hasn't touched anything.
    assert "if (!userInteracted && !pinnedNode && !pinnedLink && !focusedNode)" in JS_BUNDLE_GRAPH
    # Resets the single-shot flag and calls scheduleCenteredFit again.
    assert "hasInitialFit = false;" in JS_BUNDLE_GRAPH
    assert "scheduleCenteredFit()" in JS_BUNDLE_GRAPH
    # User actions claim camera control: clicks, drags, wheel zoom, search.
    assert "userInteracted = true" in JS_BUNDLE_GRAPH


def test_graph_f2_legend_rebuilds_after_rest_merge_from_union():
    """F-2 — legend chips render against ``payload.nodes``. Since the
    core payload misses entire groups (e.g. ``repos``), the legend must
    be rebuilt from the union AFTER the rest merge so chips reflect
    the WHOLE graph."""
    assert "function rebuildLegend" in JS_BUNDLE_GRAPH
    # Initial call still happens at startGraph time.
    assert "rebuildLegend();" in JS_BUNDLE_GRAPH
    # The merge path must call rebuildLegend after the graphData update.
    merge_idx = JS_BUNDLE_GRAPH.index("__graphMergeRestPayload")
    rebuild_after_merge = JS_BUNDLE_GRAPH.index("rebuildLegend()", merge_idx)
    assert rebuild_after_merge > merge_idx
    # Hidden-group state preserved across the rebuild.
    assert "if (hiddenGroups.has(group)) chip.classList.add('is-off')" in JS_BUNDLE_GRAPH


def test_graph_f3_orbit_target_is_cluster_centroid_consistently():
    """F-3 — the cluster-centroid is used for BOTH the cameraPosition
    fly-to AND the per-frame onEngineTick auto-orbit. The previous code
    flew to the centroid but orbited around the focused node, which made
    the camera fight itself."""
    assert "var orbitTarget" in JS_BUNDLE_GRAPH
    # Centroid coords are written to orbitTarget inside focusOnNode.
    assert "orbitTarget = { x: cx, y: cy, z: cz }" in JS_BUNDLE_GRAPH
    # The auto-orbit tick reads orbitTarget for both camX/camZ and look-at.
    assert "var tx = orbitTarget.x" in JS_BUNDLE_GRAPH
    assert "var camX = tx + Math.sin(orbitAngle) * orbitRadius" in JS_BUNDLE_GRAPH
    # Ensure we no longer derive the camera target from the focused node's
    # raw position inside the tick (regression guard for the old code).
    assert "{ x: camX, y: ty, z: camZ }, { x: tx, y: ty, z: tz }" in JS_BUNDLE_GRAPH


def test_graph_f4_hover_tooltip_still_shows_when_focused():
    """F-4 — hover-driven highlight/dim stays off when a node is focused
    (the focused neighbourhood is already lit), but the cursor tooltip
    must still appear so the user can read names of other nodes without
    deselecting first."""
    # Inside the focus-active branch of onNodeHover, showNodeTooltip is
    # called when the node is non-null and not dimmed.
    assert "if (focusedNode || pinnedNode || pinnedLink) {" in JS_BUNDLE_GRAPH
    # The two key behaviours: NO applyHighlight inside the focus-active
    # branch (would replace focus highlight) but DO showNodeTooltip.
    focus_branch_start = JS_BUNDLE_GRAPH.index("if (focusedNode || pinnedNode || pinnedLink) {")
    focus_branch_end = JS_BUNDLE_GRAPH.index("hoverNode = node || null;", focus_branch_start)
    branch = JS_BUNDLE_GRAPH[focus_branch_start:focus_branch_end]
    assert "showNodeTooltip(node, lastMouseX, lastMouseY)" in branch
    assert "applyHighlight" not in branch


def test_graph_f5_focus_panel_repopulated_on_focus_and_cleared_on_unfocus():
    """F-5 — the floating focus-detail panel surfaces the currently
    focused node's title/type/degree/description plus an Open page link.
    Populated on every focus path; cleared (hidden) on unfocus."""
    assert "function populateFocusPanel" in JS_BUNDLE_GRAPH
    assert "getElementById('graph-focus-panel')" in JS_BUNDLE_GRAPH
    assert "getElementById('graph-focus-panel-title')" in JS_BUNDLE_GRAPH
    assert "getElementById('graph-focus-panel-meta')" in JS_BUNDLE_GRAPH
    assert "getElementById('graph-focus-panel-desc')" in JS_BUNDLE_GRAPH
    assert "getElementById('graph-focus-panel-open')" in JS_BUNDLE_GRAPH
    # populateFocusPanel(null) hides the panel, populateFocusPanel(node)
    # shows + fills it.
    assert "focusPanel.hidden = true" in JS_BUNDLE_GRAPH
    assert "focusPanel.hidden = false" in JS_BUNDLE_GRAPH
    # Wired into the activate path.
    assert "populateFocusPanel(node)" in JS_BUNDLE_GRAPH
    # Wired into the unfocus path via clearInfoPanel().
    assert "populateFocusPanel(null)" in JS_BUNDLE_GRAPH


def test_graph_f6_link_opacity_pinned_to_one_so_rgba_alpha_is_authoritative():
    """F-6 — edges are described as 0.5 alpha in the spec but the previous
    ``linkOpacity(0.35)`` scalar multiplied with the rgba alpha brought
    the visible opacity down to ~0.175. Pinning ``linkOpacity(1.0)`` keeps
    the rgba alpha as the single source of truth."""
    assert "inst.linkOpacity(1.0)" in JS_BUNDLE_GRAPH
    assert "inst.linkOpacity(0.35)" not in JS_BUNDLE_GRAPH


def test_graph_f7_2d_edge_labels_skip_pill_when_alpha_is_zero():
    """F-7 — VARIANT_PILL_ALPHA.edge is 0 (text-only edge labels) but
    the 2D path used ``(VARIANT_PILL_ALPHA.edge || 0.5)`` which fell back
    to 0.5 because JS treats 0 as falsy. The fix uses a numeric typeof
    check and skips the pill draw when the alpha is 0."""
    # Strict numeric check (no || fallback).
    assert "typeof VARIANT_PILL_ALPHA.edge === 'number'" in JS_BUNDLE_GRAPH
    # Skip the pill draw entirely when the alpha is 0.
    assert "if (epillAlpha > 0) {" in JS_BUNDLE_GRAPH
    # Forbid the buggy fallback expression as actual code (it survives in
    # the explanatory comment so we look for the assignment shape only).
    assert "var epillAlpha = (VARIANT_PILL_ALPHA.edge || 0.5)" not in JS_BUNDLE_GRAPH


def test_graph_f8_search_dims_non_matches_instead_of_hiding():
    """F-8 — typing in the search box used to call ``nodeVisibility(false)``
    for non-matching nodes, which made them disappear. The new behaviour
    is a soft dim (still rendered, still clickable) via the same dim
    predicate the focus highlight uses."""
    # New helper that drives the dim path.
    assert "function matchesSearch" in JS_BUNDLE_GRAPH
    # isVisible no longer references searchQuery (search drives dim, not visibility).
    is_visible_def = JS_BUNDLE_GRAPH.index("function isVisible(node)")
    is_visible_end = JS_BUNDLE_GRAPH.index("\n    }", is_visible_def)
    is_visible_body = JS_BUNDLE_GRAPH[is_visible_def:is_visible_end]
    assert "searchQuery" not in is_visible_body
    # isDimmedNode now factors in matchesSearch.
    assert "if (searchQuery && !matchesSearch(node)) return true" in JS_BUNDLE_GRAPH


def test_graph_f9_touch_first_tap_hovers_second_tap_focuses():
    """F-9 — touch devices never fire onNodeHover (no mouse). The bundle
    installs a pointerdown listener that gates on ``pointerType === 'touch'``
    and splits a tap-on-node into a hover preview (first tap) and a
    focus action (second tap on the same node)."""
    assert "addEventListener('pointerdown'" in JS_BUNDLE_GRAPH
    assert "event.pointerType !== 'touch'" in JS_BUNDLE_GRAPH
    # The first-tap branch shows the tooltip + applies highlight.
    assert "showNodeTooltip(hitNode, px, py)" in JS_BUNDLE_GRAPH
    assert "applyHighlight(hitNode)" in JS_BUNDLE_GRAPH
    # Tap on background unfocuses (mirroring onBackgroundClick).
    assert "_lastTouchNodeId = null" in JS_BUNDLE_GRAPH


def test_graph_f10_reset_button_calls_fit_all_not_hardcoded_camera():
    """F-10 — the Reset button (and the ``r`` keyboard shortcut, which
    dispatches a synthetic click on the same button) re-frames every
    visible node via ``fitAll`` instead of hard-coding ``z = 400``."""
    # The hardcoded camera position must be gone from the reset path.
    btn_reset_idx = JS_BUNDLE_GRAPH.index("if (btnReset) btnReset.addEventListener")
    btn_reset_end = JS_BUNDLE_GRAPH.index("});", btn_reset_idx)
    reset_handler = JS_BUNDLE_GRAPH[btn_reset_idx:btn_reset_end]
    assert "fitAll(reduceMotion ? 0 : 600)" in reset_handler
    # Forbid the previous hardcoded camera reset.
    assert "{ x: 0, y: 0, z: 400 }" not in reset_handler
    # ``r`` keyboard shortcut dispatches a click on the Reset button so
    # the same fitAll path runs there too.
    assert "if (e.key === 'r') { if (btnReset) btnReset.click(); }" in JS_BUNDLE_GRAPH


def test_graph_f11_help_button_and_keyboard_shortcut_wired():
    """F-11 — the toolbar carries a ``?`` help button. Clicking it (or
    pressing ``?``) toggles the ``[data-graph-help-open]`` attribute on
    the wrapper so the popover slides in. Esc closes it."""
    assert "querySelector('[data-graph-help]')" in JS_GRAPH
    assert "getElementById('graph-help-popover')" in JS_GRAPH
    # The wrapper attribute drives the popover visibility (the CSS rule
    # is asserted in test_graph_compact_toolbar_styles_present).
    assert "setAttribute('data-graph-help-open', '')" in JS_GRAPH
    assert "removeAttribute('data-graph-help-open')" in JS_GRAPH
    # ``?`` keyboard shortcut is wired in the same keydown listener as
    # the other graph hotkeys.
    assert "if (e.key === '?')" in JS_GRAPH


def test_graph_f12_dead_state_cleanup_removed():
    """F-12 — the per-frame opacity lerp was deleted (it hung on the
    388-node corpus); the ``__opacity`` / ``__opacityTarget`` per-node
    state and the ``VARIANT_STROKE`` table that fed it were all dead.
    Regression guard so a future round doesn't put them back."""
    # Per-node / per-link tween state.
    assert "__opacityTarget" not in JS_GRAPH, (
        "F-12 — per-node opacity tween state must stay deleted"
    )
    assert ".__opacity " not in JS_GRAPH, (
        "F-12 — per-node opacity tween state must stay deleted"
    )
    # Stroke-variant table (kept-for-back-compat -> outright dead).
    assert "VARIANT_STROKE" not in JS_GRAPH, (
        "F-12 — VARIANT_STROKE table was unreferenced and must stay deleted"
    )
    # The function that wrote __opacityTarget went with it.
    assert "refreshOpacityTargets" not in JS_GRAPH
    # Top-of-bundle interaction-state comment block exists so future
    # readers can locate the live state machine.
    assert "Interaction state machine" in JS_GRAPH
    assert "focusedNode" in JS_GRAPH
    assert "userInteracted" in JS_GRAPH
    assert "orbitTarget" in JS_GRAPH


# ---------------------------------------------------------------------------
# HypePaper-aligned dark palette (GRAPH_FORCE_DARK)
# ---------------------------------------------------------------------------

def test_graph_force_dark_constant():
    """The graph view is dark-only by design. ``GRAPH_FORCE_DARK = true``
    is the single switch that gates every ``theme === 'light'`` branch
    inside the graph block — flipping it back to ``false`` would restore
    the legacy light-theme palette without any further code changes."""
    assert "var GRAPH_FORCE_DARK = true" in JS_BUNDLE_GRAPH
    # The constant is consulted at every theme branch — at least one of
    # each label/pill/text-fill site references it. We assert the
    # presence of the combined gate so a future refactor that drops it
    # accidentally trips the test.
    assert "!GRAPH_FORCE_DARK && theme === 'light'" in JS_BUNDLE_GRAPH


def test_graph_uses_dark_background_color():
    """HypePaper's CitationGraph paints over ``#060A14``. We pin the
    same hex on both the WebGL canvas (via ``.backgroundColor``) and
    the surrounding CSS surface so the wrapper matches the canvas."""
    # The graph init wires backgroundColor to the GRAPH_BG_COLOR constant.
    assert "var GRAPH_BG_COLOR = '#060A14'" in JS_BUNDLE_GRAPH
    assert ".backgroundColor(GRAPH_BG_COLOR)" in JS_BUNDLE_GRAPH
    # And the previous transparent background is gone.
    assert ".backgroundColor('rgba(0,0,0,0)')" not in JS_BUNDLE_GRAPH


def test_graph_palette_uses_hypepaper_category_colors():
    """``GROUP_COLORS`` is ported from HypePaper's CitationGraph.vue
    legend dots — purple-500 / blue-500 / cyan-400 / amber-400 /
    emerald-400 / pink-400 / gray-400 / gray-500. At least the three
    seed colors should be present so a future palette regression that
    silently reverts to the pre-HypePaper rose/orange/lime trips
    here."""
    # Purple-500 (concepts / entities — seed).
    assert "'#a855f7'" in JS_BUNDLE_GRAPH
    # Blue-500 (papers — highly cited).
    assert "'#3b82f6'" in JS_BUNDLE_GRAPH
    # Cyan-400 (repos).
    assert "'#22d3ee'" in JS_BUNDLE_GRAPH
    # The previous "papers = rose-400" anchor (`#fb7185`) is GONE for
    # the papers category. (It may still appear elsewhere in other JS
    # blocks, but not as the papers GROUP_COLORS entry.)
    assert "papers:    '#fb7185'" not in JS_BUNDLE_GRAPH


def test_graph_label_pill_alpha_is_zero_still_holds_under_force_dark():
    """The GRAPH_FORCE_DARK refactor must not regress the pill-alpha
    contract — every variant stays at alpha 0 regardless of theme."""
    assert (
        "var VARIANT_PILL_ALPHA = { default: 0, edge: 0, neighbor: 0, hover: 0, focused: 0 }"
        in JS_BUNDLE_GRAPH
    )


def test_graph_highlighted_labels_still_use_gold_text_under_force_dark():
    """The yellow `rgb(250, 204, 21)` highlight is preserved through
    the GRAPH_FORCE_DARK gate — even when the site theme is light the
    graph stays dark and the highlight stays gold."""
    assert "'rgb(250, 204, 21)'" in JS_BUNDLE_GRAPH


# ---------------------------------------------------------------------------
# JS parses
# ---------------------------------------------------------------------------

def test_bundle_parses_with_node_if_available():
    if not shutil.which("node"):
        pytest.skip("node binary not on PATH")
    proc = subprocess.run(
        ["node", "--check", "-"],
        input=JS_BUNDLE,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert proc.returncode == 0, (
        "node --check rejected JS_BUNDLE:\n" + proc.stderr
    )

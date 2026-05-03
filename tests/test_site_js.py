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

from llm_wiki.site.js import (
    JS_BUNDLE,
    JS_BUNDLE_BASE,
    JS_BUNDLE_GRAPH,
    JS_DOC_TREE,
    JS_GRAPH,
    JS_SEARCH_PALETTE,
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


def test_bundle_uses_llm_wiki_theme_localstorage_key():
    assert "llm-wiki-theme" in JS_BUNDLE
    assert "localStorage" in JS_BUNDLE


def test_bundle_follows_prefers_color_scheme():
    assert "prefers-color-scheme" in JS_BUNDLE


def test_bundle_updates_aria_label_on_theme_toggle():
    assert "aria-label" in JS_THEME_TOGGLE


# ---------------------------------------------------------------------------
# Search palette
# ---------------------------------------------------------------------------

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
    assert "llm-wiki-recents" in JS_BUNDLE


# ---------------------------------------------------------------------------
# 3D graph view
# ---------------------------------------------------------------------------

def test_bundle_zoom_uses_library_default_orbitcontrols():
    """Issue 3 — three rounds of bespoke cursor-anchored zoom all
    stuttered or inverted direction. ``THREE.OrbitControls`` ships a
    built-in ``zoomToCursor`` flag — flipping it on (and letting the
    library own the wheel handler) is the entire fix. Asserts the flag
    is set exactly once, evaluates to ``true``, and that the custom
    wheel handler / raycaster zoom path is still gone."""
    assert "controls.enableZoom = true" in JS_GRAPH
    # Issue 3 — the load-bearing line. Set exactly once on the controls
    # object so the library reads cursor position and zooms toward it.
    assert JS_GRAPH.count("controls.zoomToCursor") == 1, (
        "controls.zoomToCursor must be assigned exactly once in the bundle"
    )
    assert "controls.zoomToCursor = true" in JS_GRAPH
    assert "controls.zoomSpeed = 1.0" in JS_GRAPH
    assert "controls.enableDamping = true" in JS_GRAPH
    assert "controls.dampingFactor = 0.08" in JS_GRAPH
    # The custom wheel handler / raycaster zoom path is GONE — never
    # reintroduce it without a working physical UX trial.
    assert "addEventListener('wheel'" not in JS_GRAPH
    assert "installCursorWheelZoom" not in JS_GRAPH
    assert "controls.enableZoom = false" not in JS_GRAPH
    assert "intersectPlane(plane, intersectionPoint)" not in JS_GRAPH


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
    # Non-incident nodes drop to 25% opacity (per spec) — visibly faded,
    # still legible if the user mouses over them.
    assert "rgba(120,116,108,0.25)" in JS_GRAPH
    assert "EDGE_COLOR_DIM" in JS_GRAPH
    assert "if (isDimmedNode(node)) return" in JS_GRAPH
    assert "if (isDimmedLink(link)) return" in JS_GRAPH
    assert "pointerEvents = dim ? 'none' : 'auto'" in JS_GRAPH


def test_graph_edges_are_visible_lines_not_only_particles():
    assert "rgba(191,219,254,0.34)" in JS_GRAPH
    assert "if (inst.linkOpacity) inst.linkOpacity(0.8);" in JS_GRAPH
    assert "linkWidth(function(l){ return isDimmedLink(l) ? 0.001 : (highlightLinks.has(l) ? 2.0 : 0.5); })" in JS_GRAPH
    assert "line.setAttribute('stroke-width', '0.24');" in JS_GRAPH
    assert "el.setAttribute('stroke-width', hot ? '0.85' : '0.28');" in JS_GRAPH
    assert "if (inst.linkThreeObjectExtend) inst.linkThreeObjectExtend(true);" in JS_GRAPH
    assert "return highlightLinks.has(l) ? EDGE_COLOR_HOT : EDGE_COLOR_DIM" in JS_GRAPH


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
    """Issue 1 + 4 — both render paths apply the same variant hierarchy
    (default / neighbor / hover / focused) so the relative prominence of
    each label matches between 2D and 3D for the same node."""
    assert "nodeThreeObject" in JS_GRAPH
    assert "nodeCanvasObject" in JS_GRAPH
    assert "function shouldShowOverviewLabel" in JS_GRAPH
    assert "Math.floor(vals.length * 0.86)" in JS_GRAPH
    # 2D variant decision tree mirrors the 3D nodeThreeObject group.
    assert "var isHovered = (hoverNode === n) && !isFocused;" in JS_GRAPH
    assert "var isFocusedNeighbor = focusedNode" in JS_GRAPH
    assert "var showDefault = shouldShowOverviewLabel(n);" in JS_GRAPH
    assert "ctx.strokeText(label, n.x, n.y + 7);" in JS_GRAPH


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
    assert "inst.cameraPosition({ x: 0, y: 0, z: 600 }, { x: 0, y: 0, z: 0 }, 0)" in JS_GRAPH


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
    """Issue 1 — selecting a node should swap in a larger label sprite
    rendered above the scene with a thick stroke + accent fill on a solid
    background pill so it pops over any background, anchored above the
    node sphere. The focused label is the ONE label the user explicitly
    asked to keep a pill background on."""
    # The dual-sprite group keys off node.__focused (a per-node flag).
    assert "__focused" in JS_BUNDLE_GRAPH
    assert "function makeFocusedSpriteLabel" in JS_BUNDLE_GRAPH
    assert "function markFocused" in JS_BUNDLE_GRAPH
    # Focused label uses the unified factory with variant=focused.
    assert "function makeLabel(" in JS_BUNDLE_GRAPH
    assert "variant: 'focused'" in JS_BUNDLE_GRAPH
    assert "isFocusedLabel" in JS_BUNDLE_GRAPH
    # Spec font sizes: focused 26, hover 16, neighbor 14, default 11, edge 10.
    assert "{ default: 11, edge: 10, neighbor: 14, hover: 16, focused: 26 }" in JS_BUNDLE_GRAPH
    # 4-px stroke under the focused text fill.
    assert "{ default: 1, edge: 1, neighbor: 1, hover: 2, focused: 4 }" in JS_BUNDLE_GRAPH
    # The focused sprite anchors above the node (positive +y offset based
    # on node.val so it never overlaps the sphere itself).
    assert "n.val * 1.2 + 8" in JS_BUNDLE_GRAPH
    # nodeThreeObject builds a Group so the focused / hover / neighbor /
    # default / glow sprites can be toggled individually per frame.
    assert "new THREE.Group()" in JS_BUNDLE_GRAPH
    assert "nodeThreeObject" in JS_BUNDLE_GRAPH
    # Focused sprite carries an "[Enter] Open page" hint when href is set.
    assert "'[Enter] Open page'" in JS_BUNDLE_GRAPH
    # Pressing Enter while a node is focused navigates to its href.
    assert "if (e.key === 'Enter' && focusedNode && focusedNode.href)" in JS_BUNDLE_GRAPH


def test_graph_focused_label_has_solid_pill_default_does_not():
    """Issue 1 — ONLY the focused variant paints a solid background pill
    (white in light theme, near-black in dark theme) with a 2-px accent
    border. Default / neighbor / hover / edge labels render translucent
    text-only with NO pill (the user explicitly does not want white pills
    on every label — the previous build painted one on every node)."""
    # The pill is gated on the focused variant only.
    assert "var hasPill = variant === 'focused'" in JS_BUNDLE_GRAPH
    # Focused pill colors live in the bundle so the node's accent border
    # can read against any backdrop.
    assert "rgba(255,255,255,0.95)" in JS_BUNDLE_GRAPH  # light pill
    assert "rgba(0,0,0,0.85)" in JS_BUNDLE_GRAPH         # dark pill
    # The 2-px border picks up the node's accent color.
    assert "ctx.lineWidth = 2 * pxScale" in JS_BUNDLE_GRAPH
    assert "ctx.strokeStyle = accent" in JS_BUNDLE_GRAPH
    # Every label variant is wired through the unified factory.
    assert "isFocusedLabel" in JS_BUNDLE_GRAPH
    assert "isHoverLabel" in JS_BUNDLE_GRAPH
    assert "isNeighborLabel" in JS_BUNDLE_GRAPH
    assert "isDefaultLabel" in JS_BUNDLE_GRAPH
    assert "isEdgeLabel" in JS_BUNDLE_GRAPH
    # Default / edge labels keep depth testing on so peers can occlude
    # them (Issue 1); hover / neighbor / focused turn it off so they
    # always sit on top.
    assert "var depthTest = (variant === 'default' || variant === 'edge')" in JS_BUNDLE_GRAPH
    # Default label opacity (0.55) lives in the variant table.
    assert "{ default: 0.55, edge: 0.7, neighbor: 0.95, hover: 1.0, focused: 1.0 }" in JS_BUNDLE_GRAPH


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
    assert "'click to focus'" in JS_BUNDLE_GRAPH
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
    assert "controls.target.set(nx, ny, nz)" in JS_BUNDLE_GRAPH
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
    assert "if (isFocused) variant = 'focused';" in JS_BUNDLE_GRAPH
    assert "else if (isHovered) variant = 'hover';" in JS_BUNDLE_GRAPH
    assert "else if (isFocusedNeighbor) variant = 'neighbor';" in JS_BUNDLE_GRAPH
    assert "else if (showDefault) variant = 'default';" in JS_BUNDLE_GRAPH
    # Variant tables drive both paths.
    assert "VARIANT_FONT" in JS_BUNDLE_GRAPH
    assert "VARIANT_OPACITY" in JS_BUNDLE_GRAPH
    assert "VARIANT_STROKE" in JS_BUNDLE_GRAPH
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

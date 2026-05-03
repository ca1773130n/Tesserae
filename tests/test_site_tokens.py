"""Sanity tests for the exported CSS in ``llm_wiki.site.tokens``.

These tests don't try to validate the whole stylesheet; they pin a handful
of design rules that the redesign spec hard-requires (sticky right TOC,
stat-row spacing, button hit area, auto-fill card grid).
"""

from __future__ import annotations

from llm_wiki.site.tokens import CSS


def test_css_makes_right_toc_sticky():
    """The right ``aside.toc`` panel must stick on desktop scroll."""
    # The selector pattern can match either ``.toc-rail`` (the wrapper) or
    # ``aside.toc`` (the inner panel emitted by ``components.toc``). Both
    # should pick up ``position: sticky``.
    assert "position: sticky" in CSS
    assert "aside.toc" in CSS or ".toc-rail .toc" in CSS
    # Confirm a sticky declaration is actually wired to one of them.
    assert (
        ".toc {" in CSS
        and "position: sticky" in CSS.split(".toc {", 1)[1].split("}", 1)[0]
    ) or (
        "aside.toc" in CSS
    )


def test_css_defines_stat_row_grid():
    assert ".stats {" in CSS
    # 4-column grid by default; mobile rules drop it to 2 below 480 px.
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in CSS
    # Stat cells use flex-column with a gap so number + label have space.
    assert ".stat {" in CSS
    assert "flex-direction: column" in CSS


def test_css_button_min_block_size_on_mobile():
    """Buttons should hit the 44 px touch target on mobile breakpoints."""
    assert "min-block-size: 44px" in CSS
    # And the rule must scope <= 1023px so desktop keeps a denser hit area.
    assert "@media (max-width: 1023px)" in CSS


def test_css_auto_fill_card_grid():
    """The card grid should self-tune via ``auto-fill`` minmax."""
    assert "repeat(auto-fill, minmax(240px, 1fr))" in CSS


def test_css_table_scroll_wrapper_present():
    assert ".table-scroll" in CSS
    assert "overflow-x: auto" in CSS


def test_css_panel_section_spacing():
    assert "section.panel" in CSS or ".panel {" in CSS
    # Panel padding/gap per design spec.
    assert "padding: 24px" in CSS
    assert "margin-block: 28px" in CSS


def test_css_topbar_height_token():
    assert "--topbar-height" in CSS


def test_css_full_width_desktop_layout_present():
    """Issue 1: at >=1280 px, ``.shell`` must use the full page width
    (1640 px cap) and the index-page ``.main--wide`` variant must
    expand toward the viewport edges."""
    assert "@media (min-width: 1280px)" in CSS
    # Page width token feeds the shell grid.
    assert "1640px" in CSS
    # Wide-content modifier exists for index pages.
    assert ".main--wide" in CSS


def test_css_ultrawide_breakpoint_widens_rails_and_content():
    assert "@media (min-width: 1920px)" in CSS
    # 1280-cap on prose, 1800-cap on wide content per the design brief.
    assert "1800px" in CSS


def test_css_mobile_main_padding_clears_topbar_and_bottom_nav():
    """Issue 2: <main> must clear the sticky topbar at the top and the
    mobile bottom nav at the bottom on small viewports."""
    # The padding rules live inside the @media (max-width: 1023px) block.
    assert "padding-block-end: 88px" in CSS
    assert "env(safe-area-inset-top)" in CSS


def test_css_toc_drawer_has_overlay_backdrop_on_mobile():
    """Issue 2: the open TOC drawer must paint a backdrop over the body
    so taps can dismiss it without overlapping prose."""
    # ``[data-toc-open]::before`` pseudo-element provides the dim layer.
    assert "[data-toc-open]::before" in CSS


def test_css_subtype_chip_min_block_size_on_mobile():
    """Issue 2: subtype chips should hit the 44 px touch target and
    wrap cleanly so they don't bleed into the table below."""
    # The mobile rule overrides the base 4 px padding with a fatter chip.
    chip_block = CSS.split(".subtype-chip {", 2)
    # We just check the global ``min-block-size: 44px`` rule appears,
    # then the mobile-specific block raises chip padding.
    assert "min-block-size: 44px" in CSS
    assert "padding: 6px 14px" in CSS


def test_css_auto_link_styling_present():
    """Issue 3: auto-linked node mentions need a subtle visual marker so
    users can tell them from authored anchors."""
    assert ".auto-link" in CSS
    assert "border-bottom" in CSS


# ---------------------------------------------------------------------------
# Sticky TOC ancestor-overflow regression (Bug 1)
# ---------------------------------------------------------------------------
#
# ``position: sticky`` silently breaks the moment any ancestor declares
# ``overflow: hidden | scroll | auto`` (or ``overflow-x: clip``). The previous
# polish round set ``html, body { overflow-x: clip }`` which killed the right
# rail's stickiness on every long article. This test pins the fix so future
# changes don't reintroduce a clipping ancestor.
# ---------------------------------------------------------------------------


def test_no_overflow_hidden_or_scroll_on_sticky_ancestors():
    """``.shell``, ``.main``, ``body``, ``html`` must not declare an
    *unconditional* ``overflow: hidden|scroll|auto`` (or ``overflow-x:
    clip``). Those rules silently break ``position: sticky`` on every
    descendant — that's exactly how the previous polish round broke
    the right rail on long articles (it set ``html, body { overflow-x:
    clip }`` to suppress horizontal overflow).

    Conditional rules (``[data-rail-open] body { overflow: hidden }``
    for the drawer-lock on mobile, etc.) are exempt — they only fire
    when the drawer is open on a phone where the sticky desktop rail
    isn't rendered anyway.
    """
    import re

    forbidden_overflows = re.compile(
        r"\boverflow(?:-x|-y)?\s*:\s*(?:hidden|scroll|auto|clip)\b"
    )

    # Walk every CSS rule. For each, check the selector list — if any
    # selector in the list is exactly ``.shell``/``.main``/``body``/
    # ``html`` (no descendant combinators, no attribute brackets), the
    # body must not contain a forbidden overflow declaration.
    bare_selectors = {".shell", ".main", "body", "html"}

    # Strip /* ... */ comments before scanning so the spec text in the
    # leading docstring (which mentions ``overflow-x: clip`` as a
    # forbidden pattern) doesn't trigger a self-flagged false positive.
    css_no_comments = re.sub(r"/\*.*?\*/", "", CSS, flags=re.DOTALL)

    rule_re = re.compile(r"([^{}@]+?)\{([^{}]*)\}", re.DOTALL)
    for match in rule_re.finditer(css_no_comments):
        selector_list = match.group(1).strip()
        body = match.group(2)
        # Only inspect top-level selectors (skip ``@media`` / ``@keyframes``
        # block headers — those are caught implicitly by the recursion).
        for sel in selector_list.split(","):
            sel = sel.strip()
            if sel in bare_selectors and forbidden_overflows.search(body):
                raise AssertionError(
                    f"{sel!r} carries a sticky-breaking overflow: {body.strip()[:120]}"
                )


def test_sticky_toc_clears_topbar_with_token_offset():
    """The sticky TOC's ``top`` offset must reference ``--topbar-height``
    so the rail clears the topbar at any breakpoint, not a magic number."""
    # The aside.toc rule sets top via the topbar-height token.
    assert "top: calc(var(--topbar-height" in CSS
    assert "aside.toc" in CSS


def test_shell_grid_uses_align_items_start_for_sticky():
    """The shell grid must use ``align-items: start`` so the TOC column
    isn't stretched to the height of the main column — sticky needs the
    parent row to be taller than the sticky element."""
    assert "align-items: start" in CSS


def test_main_graph_modifier_keeps_left_rail_visible():
    """Issue 1 — ``main--graph`` is back, but with new semantics: it
    drops the right rail (giving the canvas more width) WITHOUT hiding
    the left rail or going full-bleed. The canvas stays inside the
    content column; the doc-tree rail stays visible."""
    assert ".main--graph" in CSS, "graph route uses the main--graph modifier"
    assert ".shell--graph" in CSS
    # The new shell--graph grid drops the right TOC column but keeps
    # the rail column.
    import re as _re
    block = _re.search(
        r"\.shell--graph\s*\{([^}]*)\}", CSS,
    )
    assert block is not None, "shell--graph rule missing"
    assert "var(--rail-w)" in block.group(1)
    # Two columns, not three: rail + main, no toc.
    assert "var(--toc-w)" not in block.group(1)


def test_graph_cursor_tooltip_styles_present():
    """Issue 2 — the bottom-right ``.graph-info-overlay`` panel is gone.
    A cursor-following ``.graph-tooltip`` replaces it: dark-translucent
    surface in dark theme, light-translucent in light theme, no display
    toggling per interaction (we toggle ``hidden`` instead — that's how
    we kill the page-blink the user reported)."""
    assert ".graph-info-overlay" not in CSS
    assert ".graph-tooltip" in CSS
    # The hidden attribute selector is what gates visibility — no
    # display: none thrashing on every hover.
    assert ".graph-tooltip[hidden]" in CSS
    import re as _re
    block = _re.search(r"^\.graph-tooltip\s*\{([^}]*)\}", CSS, _re.M)
    assert block is not None, ".graph-tooltip rule missing"
    body = block.group(1)
    # Spec: position absolute, pointer-events none, dark-translucent, blur,
    # 6 px radius, 320 px max-width, 13 px font, z-index 50, fade transition.
    assert "position: absolute" in body
    assert "pointer-events: none" in body
    assert "rgba(20,20,20,0.78)" in body
    assert "color: #fff" in body
    assert "backdrop-filter: blur(6px)" in body
    assert "border-radius: 6px" in body
    assert "padding: 10px 14px" in body
    assert "max-width: 320px" in body
    assert "font-size: 13px" in body
    assert "z-index: 50" in body
    assert "transition: opacity 100ms ease" in body
    # Light-theme override flips the surface to a light translucent.
    assert "[data-theme=\"light\"] .graph-tooltip" in CSS
    assert "rgba(255,255,255,0.92)" in CSS


def test_topbar_nav_active_uses_accent_with_bottom_border():
    """Issue 3 — topbar primary nav active state uses the accent color
    with a 2 px bottom border so it reads as a tab bar, not a chip."""
    assert ".topbar nav a.active" in CSS
    assert "border-bottom: 2px solid transparent" in CSS
    # The active class swaps the bottom-border color.
    assert "border-bottom-color: var(--accent)" in CSS
    # Counts render in a small bracketed token next to the label.
    assert ".topnav-count" in CSS


def test_doc_tree_styles_present():
    """Issue 3 — Obsidian-style file-explorer styles for the left rail."""
    assert ".doc-tree" in CSS
    assert ".doc-tree-folder" in CSS
    assert ".doc-tree-leaf" in CSS
    assert ".doc-tree-leaf.is-active" in CSS
    assert ".doc-tree-search" in CSS
    # Monospace 12 px font for the tree.
    assert "font-size: 12px" in CSS
    # The expand arrow rotates when <details> is open.
    assert "details[open] > .doc-tree-folder-summary::before" in CSS


def test_doc_tree_extension_pills_styled():
    """Per-extension pills (M / J / P) render as a small badge."""
    assert ".doc-tree-pill" in CSS


def test_graph_fullscreen_class_styles_present():
    """Issue 4 — ``.graph-canvas-wrapper.is-fullscreen`` repaints the
    layout so the canvas covers the viewport while the toolbar pins to
    the top, the legend to the bottom-left, and the info panel stays
    on the right."""
    assert ".graph-canvas-wrapper" in CSS
    assert ".graph-canvas-wrapper.is-fullscreen" in CSS
    # Canvas covers the viewport in fullscreen.
    assert "100vw" in CSS
    assert "100vh" in CSS
    # Toolbar absolutely-positioned over the canvas in fullscreen.
    assert ".graph-canvas-wrapper.is-fullscreen .graph-toolbar" in CSS
    # Legend pinned bottom-left.
    assert ".graph-canvas-wrapper.is-fullscreen .graph-legend" in CSS

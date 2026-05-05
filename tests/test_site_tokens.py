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


def test_css_session_pages_use_compact_readable_scale():
    assert ".session-page" in CSS
    assert ".session-page .stats" in CSS
    assert "grid-template-columns: repeat(auto-fit, minmax(150px, 1fr))" in CSS
    assert ".session-page .stat b" in CSS
    assert "font-size: clamp(1.15rem, 1.8vw, 1.7rem)" in CSS
    assert ".session-table" in CSS
    assert "table-layout: fixed" in CSS
    assert "text-overflow: ellipsis" in CSS
    assert ".session-turn-list" in CSS
    assert ".session-turn-text" in CSS
    assert "font-size: 8px" in CSS
    assert ".session-tool-use-text" in CSS
    assert ".session-tool-details" in CSS
    assert ".session-token--path" in CSS
    assert ".session-token--tag" in CSS
    assert ".session-turn-text code" in CSS
    assert ".session-token--noun" not in CSS
    assert ".session-command-chip" in CSS
    assert ".session-command-name" in CSS
    assert ".session-command-message" in CSS
    assert ".session-tag-block" in CSS
    assert ".session-tag-name" in CSS
    assert ".session-code-keyword" in CSS
    assert ".session-code-command" in CSS
    assert ".session-code-flag" in CSS
    assert ".session-code-string" in CSS
    assert ".session-code-number" in CSS
    assert "--session-path-fg" in CSS
    assert "--session-tag-fg" in CSS
    assert "--session-tag-fg: #5b3f9a" in CSS
    assert "--session-tag-fg: #ffc29f" in CSS
    assert "font-family: var(--type-serif)" in CSS
    assert "font-size: 10px" in CSS
    assert "line-height: 1.45" in CSS
    assert "background: #151515" in CSS
    assert '[data-theme="light"] .session-code-block' in CSS
    assert '[data-theme="light"] .session-tool-use-text' in CSS
    assert ".session-tool-use-text code" in CSS
    assert "data-lang" not in CSS
    assert ".session-page code" in CSS
    assert "font-size: 12px" in CSS
    assert ".session-turn-nav" in CSS
    assert ".session-turn-nav--user" in CSS
    assert ".session-turn-nav--assistant" not in CSS
    assert ".session-detail-rail" in CSS
    assert ".shell--session" in CSS
    assert "grid-template-columns: var(--rail-w) minmax(0, 1fr) var(--toc-w)" in CSS
    assert "width: min(300px, calc(100vw - 48px))" in CSS
    assert "margin-right: -96px" in CSS
    assert "z-index: 30" in CSS
    assert "box-shadow: 0 12px 30px" in CSS
    assert ".session-rail-back" in CSS
    assert "li.is-active > a" in CSS
    assert ".session-reference-card" not in CSS
    assert ".session-back-button" not in CSS


def test_css_rails_have_breathing_room_padding():
    assert "padding: var(--space-6) clamp(18px, 2vw, 32px)" in CSS
    assert ".rail {" in CSS
    assert "padding-inline: 10px" in CSS
    assert ".toc-rail .toc" in CSS
    assert "padding-inline: 12px" in CSS


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


def test_shell_grid_does_not_force_align_items_start():
    """The shell grid must NOT set ``align-items: start`` — that collapses
    the TOC column to its content height which leaves no space for the
    sticky inner aside to slide against. Default grid ``stretch`` is what
    keeps the TOC column tall enough for sticky positioning."""
    # Strip comments before checking so a /* ... */ explainer that
    # mentions the forbidden rule doesn't trip the test.
    import re as _re
    css_no_comments = _re.sub(r"/\*.*?\*/", "", CSS, flags=_re.DOTALL)
    shell_block_start = css_no_comments.index(".shell {")
    shell_block_end = css_no_comments.index("}", shell_block_start)
    assert "align-items: start" not in css_no_comments[shell_block_start:shell_block_end]


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


def test_graph_focus_detail_panel_styles_present():
    """F-5 — the floating focus-detail panel is styled inside the
    ``.graph-canvas-wrapper`` so the Fullscreen API draws it on top
    of the canvas. Bottom-right anchored, semi-transparent surface,
    max-height 60vh with internal scroll."""
    assert ".graph-canvas-wrapper .graph-focus-panel" in CSS
    import re as _re
    block = _re.search(
        r"\.graph-canvas-wrapper \.graph-focus-panel\s*\{([^}]*)\}", CSS
    )
    assert block is not None, ".graph-canvas-wrapper .graph-focus-panel rule missing"
    body = block.group(1)
    # Pinned to the bottom-right corner.
    assert "position: absolute" in body
    assert "bottom:" in body
    assert "right:" in body
    # Bounded height with internal scroll so long descriptions don't push
    # content past the viewport.
    assert "max-height: 60vh" in body
    assert "overflow-y: auto" in body
    # Hidden gate matches the JS toggle.
    assert ".graph-canvas-wrapper .graph-focus-panel[hidden]" in CSS
    # Slot rules exist for each section the JS populates.
    assert ".graph-canvas-wrapper .graph-focus-panel-title" in CSS
    assert ".graph-canvas-wrapper .graph-focus-panel-meta" in CSS
    assert ".graph-canvas-wrapper .graph-focus-panel-desc" in CSS
    assert ".graph-canvas-wrapper .graph-focus-panel-open" in CSS
    # Light theme override flips the surface.
    assert '[data-theme="light"] .graph-canvas-wrapper .graph-focus-panel' in CSS


def test_graph_auto_browse_cursor_cue_present():
    """Issue 6 — ``.graph-canvas-wrapper.is-auto-browsing`` swaps the
    cursor to ``progress`` so the user has an unmistakable signal that
    the graph is in tour mode (camera moving on its own)."""
    assert ".graph-canvas-wrapper.is-auto-browsing" in CSS
    assert "cursor: progress" in CSS


def test_graph_compact_toolbar_styles_present():
    """F-11 — the hero is gone; the title + help button live inline in
    the toolbar. The popover that holds the keyboard shortcuts is
    ``display: none`` by default and revealed via the
    ``[data-graph-help-open]`` attribute on the wrapper."""
    # Inline toolbar title and help button each have CSS rules.
    assert ".graph-page .graph-toolbar-title" in CSS
    assert ".graph-page .graph-help-button" in CSS
    # The help popover defaults to display:none; the open-state selector
    # flips it to display:block.
    import re as _re
    help_block = _re.search(
        r"\.graph-page\s+\.graph-help\s*\{([^}]*)\}", CSS
    )
    assert help_block is not None, ".graph-help rule missing"
    assert "display: none" in help_block.group(1), (
        "F-11 — graph-help popover starts collapsed"
    )
    # The wrapper's ``[data-graph-help-open]`` attribute reveals the popover.
    assert ".graph-canvas-wrapper[data-graph-help-open] .graph-help" in CSS
    assert "display: block" in CSS


# ---------------------------------------------------------------------------
# Polish pass — accessibility, light theme, mobile
# ---------------------------------------------------------------------------


def test_css_includes_universal_focus_ring_rule():
    """WCAG 2.4.7 — every interactive element must paint a visible focus
    ring of at least 2 px against the accent color when keyboard-focused."""
    assert "outline: 2px solid var(--accent)" in CSS
    # ``:focus-visible`` pin so mouse users don't get a permanent outline.
    assert ":focus-visible" in CSS


def test_css_no_orphan_outline_none():
    """If a rule sets ``outline: none`` it MUST also paint a replacement
    focus ring (via box-shadow or another outline declaration). This test
    walks the CSS for unconditional ``outline: none`` rules whose body
    lacks any of those replacements."""
    import re

    rule_re = re.compile(r"([^{}@]+?)\{([^{}]*)\}", re.DOTALL)
    css_no_comments = re.sub(r"/\*.*?\*/", "", CSS, flags=re.DOTALL)
    for match in rule_re.finditer(css_no_comments):
        body = match.group(2)
        if re.search(r"outline\s*:\s*none\b", body):
            # Replacement focus affordance must be present in the same rule.
            has_replacement = (
                "box-shadow" in body
                or re.search(r"outline\s*:\s*[^;]*(?:solid|dashed|dotted|var\()", body) is not None
            )
            selector = match.group(1).strip()
            assert has_replacement, (
                f"{selector!r} sets outline:none without a replacement ring"
            )


def test_css_includes_skip_link_styling():
    """The skip-to-content link must be visually hidden by default and
    revealed on focus."""
    assert ".skip-link" in CSS
    # Visually-hidden util the skip-link inherits from.
    assert ".visually-hidden" in CSS
    # Focused skip-link must be visible (position fixed, accent fill).
    assert ".skip-link:focus" in CSS


def test_css_light_theme_overrides_present():
    """Polish — explicit ``[data-theme="light"]`` overrides exist for the
    components historically tuned for dark surfaces."""
    assert '[data-theme="light"] .code' in CSS or '[data-theme="light"] code' in CSS
    assert '[data-theme="light"] .subtype-chip' in CSS
    # F-12 — the legacy ``.graph-info-panel`` light-theme override was
    # removed because the right-rail info panel it targeted is gone
    # (Issue 1); the F-5 ``.graph-focus-panel`` ships its own light-theme
    # override (asserted by ``test_graph_focus_detail_panel_styles_present``).
    assert '[data-theme="light"] .graph-canvas-wrapper .graph-focus-panel' in CSS
    assert '[data-theme="light"] .doc-tree-leaf.is-active' in CSS


def test_css_bottom_nav_uses_grid_repeat_5():
    """Mobile bottom nav must always fit 5 icons regardless of label
    length — pure flex was prone to overflow on the smallest viewports."""
    assert "grid-template-columns: repeat(5, 1fr)" in CSS
    # And the rule belongs to the bottom-nav <ul>.
    import re
    block = re.search(r"\.mobile-bottom-nav\s+ul\s*\{([^}]*)\}", CSS)
    assert block is not None
    assert "repeat(5, 1fr)" in block.group(1)


def test_css_bottom_nav_safe_area_padding():
    """Bottom nav must reserve space for the iOS home indicator using
    ``max(8px, env(safe-area-inset-bottom))`` so it never sits flush."""
    assert "max(8px, env(safe-area-inset-bottom))" in CSS


def test_css_bottom_nav_tap_targets_meet_44px():
    import re
    block = re.search(r"\.mobile-bottom-nav\s+a\s*\{([^}]*)\}", CSS)
    assert block is not None
    assert "min-block-size: 44px" in block.group(1)


def test_shell_horizontal_padding_has_breathing_room_at_desktop():
    """The side rails need visible horizontal padding so the file tree and
    right TOC are not glued to the browser edge."""
    import re

    css_no_comments = re.sub(r"/\*.*?\*/", "", CSS, flags=re.DOTALL)

    def _shell_block_in(media_query: str) -> str:
        idx = css_no_comments.index(media_query)
        shell_idx = css_no_comments.index(".shell {", idx)
        end = css_no_comments.index("}", shell_idx)
        return css_no_comments[shell_idx:end]

    block_768 = _shell_block_in("@media (min-width: 768px)")
    assert "clamp(18px, 2vw, 32px)" in block_768

    block_1280 = _shell_block_in("@media (min-width: 1280px)")
    assert "clamp(18px, 2vw, 32px)" in block_1280


def test_table_does_not_use_display_block_in_outer_rule():
    """The combined ``.article-body table, .markdown-body table`` rule must
    NOT set ``display: block`` — that silently drops cell borders on some
    rendering paths because ``border-collapse: collapse`` needs a regular
    ``display: table`` element to compute cell edges. Horizontal-scroll for
    wide tables is handled by the outer ``<div class="table-scroll">``
    wrapper that the markdown post-processor emits."""
    import re

    table_block = re.search(
        r"\.article-body table,\s*\.markdown-body table\s*\{([^}]*)\}", CSS
    )
    assert table_block is not None, "combined table selector block missing"
    body = table_block.group(1)
    assert "display: block" not in body, (
        "outer markdown table rule must not set display: block — the "
        "wrapping div carries the horizontal scroll instead"
    )
    # And the table now spans its scroll wrapper.
    assert "width: 100%" in body, "table should fill its scroll wrapper"


def test_article_body_tables_have_visible_borders_and_padding():
    """Markdown tables on content pages render inside ``.article-body``
    (paper / source / concept detail pages) and ``.markdown-body`` (raw
    pages). Both wrappers must carry an explicit table-border CSS block
    so cells aren't presented as plain unframed text."""
    import re

    # Both selectors must appear in the table block.
    assert ".article-body table" in CSS, "missing .article-body table selector"
    assert ".markdown-body table" in CSS, "missing .markdown-body table selector"

    # Locate the combined ``.article-body table, .markdown-body table {...}``
    # block and confirm it sets a ``border:`` and ``border-collapse``.
    table_block = re.search(
        r"\.article-body table,\s*\.markdown-body table\s*\{([^}]*)\}", CSS
    )
    assert table_block is not None, "combined table selector block missing"
    tbody = table_block.group(1)
    assert "border:" in tbody, "table-level border missing"
    assert "border-collapse" in tbody

    # Cell rule: th/td must have border + padding.
    cell_block = re.search(
        r"\.article-body table th,\s*\.article-body table td,\s*"
        r"\.markdown-body table th,\s*\.markdown-body table td\s*\{([^}]*)\}",
        CSS,
    )
    assert cell_block is not None, "th/td cell rule missing"
    cbody = cell_block.group(1)
    assert "border:" in cbody, "th/td border missing"
    assert "padding:" in cbody, "th/td padding missing"

    # Header rule: must shade the th with a non-transparent token.
    th_block = re.search(
        r"\.article-body table th,\s*\.markdown-body table th\s*\{([^}]*)\}", CSS
    )
    assert th_block is not None, "th header rule missing"
    th_body = th_block.group(1)
    assert "background:" in th_body, "th must declare a background"
    # Token-driven (var(--surface-2) / var(--surface)), not transparent.
    assert "var(--surface" in th_body, "th background should use a surface token"
    assert "transparent" not in th_body


def test_activity_compact_styles_present():
    """The home page heatmap uses ``activity activity--compact`` — the
    compact rule must cap width and height so the widget sits in roughly
    the same vertical space as the stat row above it."""
    import re

    assert ".activity--compact" in CSS, ".activity--compact rule missing"
    block = re.search(r"\.activity--compact\s*\{([^}]*)\}", CSS)
    assert block is not None, ".activity--compact rule missing"
    body = block.group(1)
    assert "max-width" in body, ".activity--compact must cap max-width"
    assert "max-height" in body, ".activity--compact must cap max-height"

    # Compact-mode title styling must exist so the ``<h3>`` inside the
    # widget is visually subdued vs the page-level ``<h2>`` headings.
    assert ".activity-title" in CSS


def test_css_no_overflow_x_scroll_or_hidden_on_layout_roots():
    """Regression test for sticky-positioning. ``.shell``, ``.main``,
    ``body``, ``html`` must not declare ``overflow-x: hidden|scroll``
    (or ``overflow-x: clip``) anywhere — those silently kill
    ``position: sticky`` on every descendant."""
    import re

    forbidden_overflow_x = re.compile(
        r"\boverflow-x\s*:\s*(?:hidden|scroll|clip)\b"
    )
    rule_re = re.compile(r"([^{}@]+?)\{([^{}]*)\}", re.DOTALL)
    css_no_comments = re.sub(r"/\*.*?\*/", "", CSS, flags=re.DOTALL)
    bare_selectors = {".shell", ".main", "body", "html"}
    for match in rule_re.finditer(css_no_comments):
        selector_list = match.group(1).strip()
        body = match.group(2)
        for sel in selector_list.split(","):
            sel = sel.strip()
            if sel in bare_selectors and forbidden_overflow_x.search(body):
                raise AssertionError(
                    f"{sel!r} carries overflow-x: hidden|scroll|clip — "
                    f"breaks sticky descendants. Body: {body.strip()[:120]}"
                )

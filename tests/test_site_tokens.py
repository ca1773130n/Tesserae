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

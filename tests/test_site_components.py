"""Unit tests for ``llm_wiki.site.components``.

Each component renderer is asserted to:
  - Start with the expected outermost tag (so callers can chain them safely).
  - Emit the design-spec class names so the stylesheet hooks them up.
  - Behave gracefully on empty inputs.
"""

from __future__ import annotations

import re

from llm_wiki.site.components import (
    ai_siblings_footer,
    badge,
    breadcrumbs,
    card,
    edge_list,
    heatmap_svg,
    node_table,
    page_shell,
    sparkline_svg,
    tag_chip,
    toc,
)


# ---------------------------------------------------------------------------
# breadcrumbs
# ---------------------------------------------------------------------------

def test_breadcrumbs_starts_with_nav_and_contains_class():
    out = breadcrumbs([("Home", "../index.html"), ("Sources", "index.html"), ("digest.md", "digest.html")])
    assert out.startswith("<nav")
    assert 'class="breadcrumbs"' in out
    assert "Home" in out and "Sources" in out and "digest.md" in out
    assert 'aria-current="page"' in out  # last crumb marked current
    assert out.count('<span class="sep"') == 2


def test_breadcrumbs_empty_list_returns_empty_nav():
    out = breadcrumbs([])
    assert out.startswith("<nav")
    assert 'class="breadcrumbs"' in out
    # No crumbs at all means no separators / aria-current entries.
    assert "aria-current" not in out


# ---------------------------------------------------------------------------
# badge / tag chip
# ---------------------------------------------------------------------------

def test_badge_default_tone_is_neutral():
    out = badge("Paper")
    assert out.startswith("<span")
    assert 'class="badge tone-neutral"' in out
    assert "Paper" in out


def test_badge_warm_tone_class_emitted():
    out = badge("Synthesis", tone="warm")
    assert 'class="badge tone-warm"' in out


def test_badge_unknown_tone_falls_back_to_neutral():
    out = badge("Concept", tone="rainbow")
    assert 'class="badge tone-neutral"' in out


def test_badge_escapes_html():
    out = badge("<script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_tag_chip_with_href_is_anchor():
    out = tag_chip("2026", href="../topics/2026.html")
    assert out.startswith("<a")
    assert 'class="tag-chip"' in out
    assert 'href="../topics/2026.html"' in out


def test_tag_chip_without_href_is_span():
    out = tag_chip("2026")
    assert out.startswith("<span")
    assert 'class="tag-chip"' in out


# ---------------------------------------------------------------------------
# card
# ---------------------------------------------------------------------------

def test_card_starts_with_anchor_and_carries_class():
    out = card(
        title="Gaussian Splatting",
        href="../concepts/gaussian-splatting.html",
        kind_label="Concept",
        description="A point-based 3D reconstruction method.",
        footer="12 mentions · 2026-04-20",
    )
    assert out.startswith("<a")
    assert 'class="card"' in out
    assert 'class="card-kind"' in out
    assert 'class="card-title"' in out
    assert 'class="card-desc"' in out
    assert 'class="card-footer"' in out
    assert "Gaussian Splatting" in out


def test_card_without_description_or_footer_skips_those_blocks():
    out = card("Title", "x.html", "Concept")
    assert "card-desc" not in out
    assert "card-footer" not in out


# ---------------------------------------------------------------------------
# tables / edges
# ---------------------------------------------------------------------------

def test_node_table_empty_returns_muted_paragraph():
    assert node_table([]) == '<p class="muted">No nodes.</p>'


def test_node_table_renders_rows_and_includes_header():
    rows = [
        {"title": "Foo", "href": "concepts/foo.html", "kind": "Concept", "mentions": 4, "source": "data/x.md"},
        {"title": "Bar", "href": "papers/bar.html", "kind": "Paper", "tone": "warm", "mentions": 1, "source": ""},
    ]
    out = node_table(rows)
    assert out.startswith("<table")
    assert 'class="node-table"' in out
    assert "<thead>" in out and "<tbody>" in out
    assert "Foo" in out and "Bar" in out
    assert 'class="badge tone-neutral"' in out  # first row default
    assert 'class="badge tone-warm"' in out  # second row override


def test_node_table_depth_prefixes_hrefs():
    rows = [{"title": "Foo", "href": "concepts/foo.html", "kind": "Concept"}]
    out = node_table(rows, depth=2)
    assert 'href="../../concepts/foo.html"' in out


def test_edge_list_empty_returns_muted_paragraph():
    assert edge_list([]) == '<p class="muted">No edges.</p>'


def test_edge_list_renders_relation_badge_and_link():
    rows = [{"relation": "uses", "other_title": "Algo", "other_href": "concepts/algo.html"}]
    out = edge_list(rows)
    assert out.startswith("<ul")
    assert 'class="edge-list"' in out
    assert 'class="badge tone-neutral"' in out
    assert "uses" in out and "Algo" in out
    assert 'href="concepts/algo.html"' in out


def test_edge_list_depth_prefix_applied():
    rows = [{"relation": "uses", "other_title": "Algo", "other_href": "concepts/algo.html"}]
    out = edge_list(rows, depth=1)
    assert 'href="../concepts/algo.html"' in out


# ---------------------------------------------------------------------------
# SVG widgets
# ---------------------------------------------------------------------------

def test_sparkline_empty_returns_stub_svg():
    out = sparkline_svg([])
    assert out.startswith("<svg")
    assert 'class="sparkline"' in out
    assert "No data" in out


def test_sparkline_renders_polyline_for_values():
    out = sparkline_svg([1, 4, 2, 8, 5], width=120, height=28)
    assert out.startswith("<svg")
    assert 'class="sparkline"' in out
    assert "<polyline" in out
    assert "<polygon" in out


def test_sparkline_handles_single_value():
    out = sparkline_svg([5])
    assert "<polyline" in out


def test_heatmap_empty_returns_stub_svg():
    out = heatmap_svg([])
    assert out.startswith("<svg")
    assert 'class="heatmap"' in out
    assert "No activity yet" in out


def test_heatmap_renders_cells_with_levels():
    weeks = [[0, 1, 2, 0, 0, 0, 0]] * 4 + [[3, 4, 0, 1, 2, 4, 0]]
    out = heatmap_svg(weeks)
    assert out.startswith("<svg")
    assert 'class="heatmap"' in out
    assert '<rect class="day' in out
    # Highest values should pick up the strongest level class somewhere.
    assert "l-4" in out


def test_heatmap_caps_to_weeks_back():
    weeks = [[1] * 7] * 40
    out = heatmap_svg(weeks, weeks_back=10)
    # Each week column emits 7 rects -> 10 weeks * 7 rects = 70.
    assert out.count("<rect") == 70


# ---------------------------------------------------------------------------
# AI siblings footer
# ---------------------------------------------------------------------------

def test_ai_siblings_footer_links_txt_and_json_siblings():
    out = ai_siblings_footer("papers/foo.html")
    assert out.startswith("<footer")
    assert 'class="ai-siblings"' in out
    assert 'href="papers/foo.txt"' in out
    assert 'href="papers/foo.json"' in out
    assert 'href="papers/foo.html"' in out


def test_ai_siblings_footer_handles_paths_without_html_suffix():
    out = ai_siblings_footer("papers/foo")
    assert 'href="papers/foo.txt"' in out
    assert 'href="papers/foo.json"' in out


# ---------------------------------------------------------------------------
# table of contents
# ---------------------------------------------------------------------------

def test_toc_empty_returns_aside_with_muted_message():
    out = toc([])
    assert out.startswith("<aside")
    assert 'class="toc"' in out
    assert "No sections." in out


def test_toc_renders_levels_and_anchors():
    out = toc([(2, "Overview", "overview"), (3, "Method", "method"), (4, "Tiny detail", "tiny")])
    assert out.startswith("<aside")
    assert 'href="#overview"' in out
    assert 'class="toc-l-2"' in out
    assert 'class="toc-l-3"' in out
    assert 'class="toc-l-4"' in out


def test_toc_clamps_out_of_range_levels():
    out = toc([(1, "h1", "a"), (7, "deep", "b")])
    # Levels < 2 clamp to 2, > 4 clamp to 4.
    assert 'class="toc-l-2"' in out
    assert 'class="toc-l-4"' in out


# ---------------------------------------------------------------------------
# page shell
# ---------------------------------------------------------------------------

def _shell(**overrides):
    defaults = {
        "head": "",
        "body": "<h1>Hello</h1>",
        "depth": 0,
        "active": "home",
        "counts": {"sources": 12, "concepts": 34},
    }
    defaults.update(overrides)
    return page_shell("Hello", **defaults)


def test_page_shell_starts_with_doctype_and_html():
    out = _shell()
    assert out.startswith("<!doctype html>")
    assert "<html " in out
    assert "<head>" in out and "<body>" in out
    assert "</html>" in out.strip()[-32:]  # closes near the end


def test_page_shell_depth_zero_uses_no_prefix():
    out = _shell(depth=0)
    assert 'href="assets/style.css"' in out
    assert 'src="assets/app.js"' in out
    # No accidental "../assets/..." anywhere.
    assert "../assets/style.css" not in out
    assert "../assets/app.js" not in out


def test_page_shell_depth_two_prefixes_assets_with_dotdot():
    out = _shell(depth=2)
    assert 'href="../../assets/style.css"' in out
    assert 'src="../../assets/app.js"' in out
    # The brand link climbs back to root too.
    assert 'href="../../index.html"' in out


def test_page_shell_marks_active_rail_link():
    out = _shell(active="papers")
    # The rail entry should have the ``active`` class on its anchor.
    match = re.search(r'<a class="active" href="[^"]*papers/index.html"', out)
    assert match is not None


def test_page_shell_includes_title_and_site_title():
    out = page_shell(
        "My Page",
        head="",
        body="<p>x</p>",
        site_title="LLM-Wiki",
    )
    assert "<title>My Page · LLM-Wiki</title>" in out


def test_page_shell_injects_head_breadcrumbs_toc_and_siblings():
    out = page_shell(
        "Detail",
        head='<meta name="x" content="y">',
        body="<p>body</p>",
        breadcrumbs_html="<nav class=\"breadcrumbs\">crumbs</nav>",
        toc_html="<aside class=\"toc\">toc</aside>",
        ai_siblings_html="<footer class=\"ai-siblings\">siblings</footer>",
    )
    assert '<meta name="x" content="y">' in out
    assert "crumbs" in out
    assert "toc" in out
    assert "siblings" in out


def test_page_shell_renders_left_rail_with_section_headers():
    out = _shell()
    assert '<aside class="rail"' in out
    assert "Library" in out
    assert "Sources" in out and "Concepts" in out
    # Counts are rendered when non-zero.
    assert "<span class=\"count\">12</span>" in out

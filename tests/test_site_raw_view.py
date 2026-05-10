"""Tests for the raw-view TOC extraction + table-scroll wrapping fixes.

The user reported two bugs against ``/raw/<safe>.html`` pages:

1. The right-rail "On this page" TOC was always hidden because
   ``render_raw_view`` never threaded ``toc_html`` through to ``page_shell``.
2. Markdown tables rendered without visible cell borders because the outer
   ``.markdown-body table`` rule used to set ``display: block`` (so the table
   could scroll horizontally on narrow viewports), which silently broke
   ``border-collapse: collapse`` on some rendering paths.

These tests pin the fixes so the next refactor doesn't regress them.
"""

from __future__ import annotations

from pathlib import Path

from llm_wiki.site.raw_view import (
    _unique_heading_anchors,
    _wrap_tables_in_scroll,
    is_markdown_source_path,
    render_raw_view,
)


def test_raw_view_renders_root_readme_language_files_as_markdown(tmp_path: Path) -> None:
    """Root ``README.md.<lang>`` files should render like GitHub README docs."""
    md = tmp_path / "README.md.ko"
    md.write_text("# 한국어 README\n\n- 하나\n- 둘\n", encoding="utf-8")

    assert is_markdown_source_path(md)

    out = render_raw_view(
        site_title="LLM-Wiki",
        project_relative_path="README.md.ko",
        absolute_path=md,
    )

    assert '<section class="markdown-body raw-markdown">' in out
    assert "<h1" in out
    assert "한국어 README" in out
    assert "<li>하나</li>" in out
    assert "<pre class=\"raw-text\">" not in out


def test_raw_view_renders_toc_when_body_has_headings(tmp_path: Path) -> None:
    """Two H2 sections + one H3 should produce a real ``<aside class="toc">``
    with one ``data-toc-target`` per heading."""
    md = tmp_path / "doc.md"
    md.write_text(
        "# Doc\n\n"
        "## Alpha\n\nSome alpha text.\n\n"
        "### Alpha sub\n\nNested.\n\n"
        "## Beta\n\nMore body text.\n",
        encoding="utf-8",
    )
    out = render_raw_view(
        site_title="LLM-Wiki",
        project_relative_path="docs/doc.md",
        absolute_path=md,
    )
    # The TOC aside is present (not the hidden placeholder).
    assert '<aside class="toc"' in out
    # Each heading appears as a scrollspy target in the rail.
    assert 'data-toc-target="alpha"' in out
    assert 'data-toc-target="alpha-sub"' in out
    assert 'data-toc-target="beta"' in out


def test_raw_view_unique_heading_anchors_when_slugger_collides(
    tmp_path: Path,
) -> None:
    """Two ``## Section`` headings slug to the same id; the dedup helper
    must rewrite the second to ``section-2`` and the TOC must use both."""
    md = tmp_path / "dup.md"
    md.write_text(
        "# Top\n\n"
        "## Section\n\nFirst body.\n\n"
        "## Section\n\nSecond body.\n",
        encoding="utf-8",
    )
    out = render_raw_view(
        site_title="LLM-Wiki",
        project_relative_path="docs/dup.md",
        absolute_path=md,
    )
    # Both anchors exist in the rendered body so deep-links from the rail
    # land on the right section.
    assert 'id="section"' in out
    assert 'id="section-2"' in out
    # And the TOC references both as scrollspy targets.
    assert 'data-toc-target="section"' in out
    assert 'data-toc-target="section-2"' in out


def test_raw_view_no_toc_when_body_has_no_anchored_headings(
    tmp_path: Path,
) -> None:
    """A markdown body with no h2/h3 must fall back to the hidden TOC
    placeholder (matches pre-fix behavior for empty bodies)."""
    md = tmp_path / "flat.md"
    md.write_text(
        "# Just a Title\n\nSome paragraph with no sub-sections.\n",
        encoding="utf-8",
    )
    out = render_raw_view(
        site_title="LLM-Wiki",
        project_relative_path="docs/flat.md",
        absolute_path=md,
    )
    # No body H2/H3 → page_shell emits the hidden placeholder, not a real toc.
    assert 'class="toc-rail" id="toc" hidden' in out


def test_raw_markdown_wraps_tables_in_scroll_div(tmp_path: Path) -> None:
    """A markdown table must end up inside ``<div class="table-scroll">``
    so the outer wrapper carries the horizontal-scroll affordance and the
    table itself can keep ``border-collapse: collapse`` without losing
    cell borders to the legacy ``display: block`` rule."""
    md = tmp_path / "table.md"
    md.write_text(
        "# Table doc\n\n"
        "| a | b |\n"
        "|---|---|\n"
        "| 1 | 2 |\n",
        encoding="utf-8",
    )
    out = render_raw_view(
        site_title="LLM-Wiki",
        project_relative_path="docs/table.md",
        absolute_path=md,
    )
    # Either the wrapper sits directly before <table, or the table appears
    # somewhere inside a table-scroll div. We check both shapes so a future
    # whitespace tweak in the renderer doesn't break the assertion.
    assert '<div class="table-scroll">' in out
    # Confirm the wrapper is paired with the table — find the wrapper open
    # and check the table tag follows before the wrapper closes.
    idx = out.index('<div class="table-scroll">')
    after = out[idx:]
    close_idx = after.index("</div>")
    chunk = after[:close_idx]
    assert "<table" in chunk


def test_wrap_tables_idempotent_on_already_wrapped_html() -> None:
    """``_wrap_tables_in_scroll`` must NOT double-wrap a table that already
    sits inside ``<div class="table-scroll">``."""
    html = (
        '<div class="table-scroll">'
        '<table class="md-table"><tbody><tr><td>1</td></tr></tbody></table>'
        "</div>"
    )
    out = _wrap_tables_in_scroll(html)
    # The wrapper appears exactly once.
    assert out.count('<div class="table-scroll">') == 1


def test_unique_heading_anchors_skips_headings_without_id() -> None:
    """Headings without an ``id`` attribute have no scrollspy target so
    they should be skipped (no entry in the returned heading list)."""
    rewritten, headings = _unique_heading_anchors(
        '<h2>No anchor here</h2>'
        '<h2 id="real">Real one</h2>'
    )
    # The id-less heading is skipped.
    anchors = [a for _, _, a in headings]
    assert "real" in anchors
    assert all(text != "No anchor here" for _, text, _ in headings)
    # The body is unmodified for headings we skipped.
    assert "<h2>No anchor here</h2>" in rewritten


def test_unique_heading_anchors_strips_inline_html_for_label() -> None:
    """``<code>foo</code>`` inside a heading should reduce to ``foo`` in
    the TOC label so the rail doesn't render literal angle brackets."""
    _, headings = _unique_heading_anchors(
        '<h3 id="config"><code>foo.bar</code> config</h3>'
    )
    assert headings == [(3, "foo.bar config", "config")]

"""Tests for the dependency-light site markdown renderer."""

from __future__ import annotations

from llm_wiki.site.markdown import render_markdown


def test_markdown_preserves_safe_readme_html_blocks() -> None:
    html, _ = render_markdown(
        '<h1 align="center">LLM-Wiki</h1>\n\n'
        '<p align="center">\n'
        '  <strong>Turn docs into a graph.</strong>\n'
        '  <br />\n'
        '  <em>Local-first.</em>\n'
        '</p>\n\n'
        '<p align="center"><img src="https://img.shields.io/badge/Demo-green" alt="Demo" /></p>'
    )

    assert '<h1 align="center">LLM-Wiki</h1>' in html
    assert '<p align="center">' in html
    assert '<strong>Turn docs into a graph.</strong>' in html
    assert '<br />' in html or '<br>' in html
    assert '<em>Local-first.</em>' in html
    assert '<img src="https://img.shields.io/badge/Demo-green" alt="Demo" />' in html or '<img src="https://img.shields.io/badge/Demo-green" alt="Demo">' in html
    assert '&lt;h1' not in html
    assert '&lt;p' not in html


def test_mermaid_fence_renders_as_diagram_container() -> None:
    html, _ = render_markdown(
        '```mermaid\n'
        'flowchart TB\n'
        '  A["Raw project sources<br/>README · docs"]\n'
        '  A --> B\n'
        '```\n'
    )

    assert '<div class="mermaid" data-mermaid-source="fence">' in html
    assert '<pre><code class="language-mermaid"' not in html
    assert 'flowchart TB' in html
    # Mermaid labels need literal <br/> text after browser entity decoding, not
    # raw HTML tags that the parser would turn into real <br> nodes.
    assert '&lt;br/&gt;' in html
    assert '<br/>' not in html


def test_markdown_strips_unsafe_raw_html() -> None:
    html, _ = render_markdown('<script>alert(1)</script>\n\n<a href="javascript:alert(1)">bad</a>')

    assert '<script>' not in html
    assert 'javascript:alert' not in html
    assert '&lt;script&gt;' in html
    assert '<a>bad</a>' in html


def test_github_admonition_renders_as_callout() -> None:
    html, _ = render_markdown(
        '> [!TIP]\n'
        '> **Use compile first.**\n'
        '> Then run `build-site`.\n'
    )

    assert '<div class="admonition admonition-tip">' in html
    assert '<p class="admonition-title">Tip</p>' in html
    assert '<strong>Use compile first.</strong>' in html
    assert '<code>build-site</code>' in html
    assert '[!TIP]' not in html

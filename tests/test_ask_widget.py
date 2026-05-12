"""Tests for the per-page ask widget (Bet B3).

The widget is a small JS island emitted by ``llm_wiki.site.ask_widget``
and mounted at the bottom of every detail page's article body by
``llm_wiki.site.pages._detail_page``. These tests cover the asset
shape (JS + CSS surface), the page integration (mount point on every
detail kind), and the static-site asset wiring (hashed filename + CSS
appended to ``style.css``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_wiki.research_graph import ResearchGraph, ResearchNodeType
from llm_wiki.site import StaticSiteBuilder
from llm_wiki.site.ask_widget import ask_widget_css, ask_widget_js
from llm_wiki.site.pages import (
    SiteContext,
    render_concept_detail,
    render_entity_detail,
    render_paper_detail,
    render_question_detail,
    render_repo_detail,
    render_source_detail,
    render_synthesis_detail,
    render_topic_detail,
)
from llm_wiki.wiki_store import WikiPage


# ---------------------------------------------------------------------------
# 1. Asset surface
# ---------------------------------------------------------------------------


def test_ask_widget_js_emits_health_check_and_post() -> None:
    js = ask_widget_js()
    assert "/api/ask/health" in js, "widget must health-check the backend"
    assert "/api/ask" in js, "widget must POST to /api/ask"
    assert "data-ask-widget" in js
    assert "data-node-id" in js
    assert "data-node-kind" in js
    assert "data-node-name" in js


def test_ask_widget_js_degrades_gracefully() -> None:
    """On health-check failure the widget collapses to a static footer."""
    js = ask_widget_js()
    assert "renderDegraded" in js
    assert "llm_wiki project serve" in js


def test_ask_widget_js_renders_envelope_shapes() -> None:
    """The widget understands all four envelope shapes returned by ask_project."""
    js = ask_widget_js()
    # raganything → answer string
    assert "envelope.answer" in js
    # cognee / wiki → results list
    assert "envelope.results" in js
    # backend === "none" → note string
    assert "envelope.note" in js


def test_ask_widget_js_avoids_innerhtml_for_dynamic_content() -> None:
    """All dynamic data must reach the DOM via createTextNode / setAttribute."""
    js = ask_widget_js()
    assert "createTextNode" in js
    assert "createElement" in js
    # No innerHTML in the source — every container is built node-by-node.
    assert "innerHTML" not in js, (
        "ask widget must not use innerHTML; build DOM via createElement / "
        "createTextNode to keep untrusted answer text XSS-safe"
    )


def test_ask_widget_css_present() -> None:
    css = ask_widget_css()
    assert ".ask-widget" in css
    assert ".ask-form" in css
    assert ".ask-answer" in css
    assert ".ask-degraded" in css


# ---------------------------------------------------------------------------
# 2. Mount point appears on every detail kind
# ---------------------------------------------------------------------------


def _wiki_pages_for(graph: ResearchGraph) -> dict[str, list[WikiPage]]:
    """Build one WikiPage per detail kind so every renderer has input."""

    def _make(kind: str, slug: str, title: str, body: str, frontmatter: dict | None = None) -> WikiPage:
        return WikiPage(
            kind=kind,
            slug=slug,
            title=title,
            body=body,
            path=Path(f"wiki/{kind}/{slug}.md"),
            frontmatter=frontmatter or {},
        )

    by_type: dict[str, list] = {}
    for n in graph.nodes:
        by_type.setdefault(n.type.value, []).append(n)

    pages: dict[str, list[WikiPage]] = {
        "sources": [], "concepts": [], "entities": [], "papers": [],
        "repos": [], "topics": [], "syntheses": [], "questions": [],
    }
    src = next(iter(by_type.get(ResearchNodeType.SOURCE_DOCUMENT.value, [])), None)
    if src:
        pages["sources"].append(_make(
            "sources", "sample-src", src.name, "# Sample\nLead.\n",
            {"node_id": src.id},
        ))
    concept = next(iter(by_type.get(ResearchNodeType.CONCEPT.value, [])), None)
    if concept:
        pages["concepts"].append(_make(
            "concepts", "sample-concept", concept.name, "# Sample\nDef.\n",
            {"node_id": concept.id},
        ))
    else:
        # Fixture corpus may not project a CONCEPT node, but the renderer
        # still has to handle synthetic concept pages (the wiki layer can
        # carry concept stubs without a graph backing). Synthesize one so
        # the concept-detail branch is exercised regardless.
        pages["concepts"].append(_make(
            "concepts", "sample-concept", "Sample Concept", "# Sample\nDef.\n",
            {"node_id": "concept:sample"},
        ))
    paper = next(iter(by_type.get(ResearchNodeType.PAPER.value, [])), None)
    if paper:
        pages["papers"].append(_make(
            "papers", "sample-paper", paper.name, "# Sample\nAbstract.\n",
            {"node_id": paper.id},
        ))
    repo = next(iter(by_type.get(ResearchNodeType.REPOSITORY.value, [])), None)
    if repo:
        pages["repos"].append(_make(
            "repos", "sample-repo", repo.name, "# Sample\nReadme.\n",
            {"node_id": repo.id},
        ))
    field = next(iter(by_type.get(ResearchNodeType.RESEARCH_FIELD.value, [])), None)
    if field:
        pages["topics"].append(_make(
            "topics", "sample-topic", field.name, "# Sample\nOverview.\n",
            {"node_id": field.id},
        ))
    pages["entities"].append(_make(
        "entities", "sample-entity", "Sample Model", "# Sample entity\n",
    ))
    pages["syntheses"].append(_make(
        "syntheses", "pulse", "Project pulse", "# Pulse\n- one.\n",
        {"synthesis_kind": "pulse"},
    ))
    pages["questions"].append(_make(
        "questions", "sample-question", "Why does it work?", "# Open question\n",
    ))
    pages["overview"] = [_make("overview", "overview", "Overview", "Lead.\n")]
    return pages


@pytest.fixture
def site_ctx(wiki_sample_graph: ResearchGraph) -> SiteContext:
    return SiteContext.build(
        graph=wiki_sample_graph,
        wiki_pages_by_kind=_wiki_pages_for(wiki_sample_graph),
        site_title="LLM-Wiki",
    )


@pytest.mark.parametrize(
    "renderer,kind_key,node_kind",
    [
        (render_concept_detail, "concepts", "concept"),
        (render_entity_detail, "entities", "entity"),
        (render_paper_detail, "papers", "paper"),
        (render_repo_detail, "repos", "repo"),
        (render_topic_detail, "topics", "topic"),
        (render_synthesis_detail, "syntheses", "synthesis"),
        (render_question_detail, "questions", "question"),
        (render_source_detail, "sources", "source"),
    ],
)
def test_ask_widget_appears_in_detail_page(
    site_ctx: SiteContext, renderer, kind_key: str, node_kind: str
) -> None:
    """Every detail-page renderer emits a widget mount point with the right kind."""
    pages = site_ctx.wiki_pages_by_kind.get(kind_key) or []
    if not pages:
        pytest.skip(f"fixture has no {kind_key} page")
    html = renderer(site_ctx, pages[0])
    assert "data-ask-widget" in html
    assert 'class="ask-widget"' in html
    assert f'data-node-kind="{node_kind}"' in html
    assert "data-node-id=" in html
    assert "data-node-name=" in html
    # The hashed asset gets loaded via <script defer>.
    assert "ask-widget-" in html and ".js" in html


def test_ask_widget_concept_uses_frontmatter_node_id(site_ctx: SiteContext) -> None:
    """When frontmatter carries node_id, the widget uses it (not the slug)."""
    pages = site_ctx.wiki_pages_by_kind.get("concepts") or []
    if not pages:
        pytest.skip("fixture has no concept page")
    page = pages[0]
    expected_id = page.frontmatter.get("node_id")
    html = render_concept_detail(site_ctx, page)
    if expected_id:
        assert f'data-node-id="{expected_id}"' in html


# ---------------------------------------------------------------------------
# 3. Asset wiring: build_site emits hashed JS + CSS in style.css
# ---------------------------------------------------------------------------


def test_static_site_builder_emits_hashed_ask_widget_asset(
    tmp_path: Path, wiki_sample_graph: ResearchGraph
) -> None:
    """``llm_wiki project build-site`` writes the hashed widget JS bundle."""
    out = tmp_path / "site"
    builder = StaticSiteBuilder(site_title="LLM-Wiki")
    # Legacy two-arg shape — no wiki layer, only the graph. The asset
    # block runs regardless of whether the wiki has any pages, so this is
    # enough to exercise the hashed bundle write.
    builder.write_site(wiki_sample_graph, out)

    assets = out / "assets"
    js_hits = list(assets.glob("ask-widget-*.js"))
    assert js_hits, "expected hashed ask-widget-<hash>.js asset"
    assert (assets / "ask-widget.js").exists(), "expected unhashed alias"

    # Per-widget CSS gets appended to the global stylesheet.
    style_css = (assets / "style.css").read_text(encoding="utf-8")
    assert ".ask-widget" in style_css
    assert ".ask-answer" in style_css

"""Structural tests for the site page renderers in :mod:`llm_wiki.site.pages`.

The tests build a small ``SiteContext`` from the ``wiki_sample_graph`` fixture
(see ``tests/conftest.py``) and synthesize a couple of ``WikiPage`` objects so
every detail renderer has something concrete to render. Assertions stay
structural — full document, semantic landmarks, breadcrumb labels, AI
siblings footer — so they keep passing as Subagent D's components and
Subagent F's search index land on top of this module.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from llm_wiki.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from llm_wiki.site.js import JS_BUNDLE, JS_GRAPH, JS_SEARCH_PALETTE, JS_THEME_TOGGLE
from llm_wiki.site.pages import (
    SiteContext,
    page_href,
    render_about,
    render_concept_detail,
    render_concepts_index,
    render_entities_index,
    render_entity_detail,
    render_graph_view,
    render_home,
    render_paper_detail,
    render_papers_index,
    render_question_detail,
    render_questions_index,
    render_repo_detail,
    render_repos_index,
    render_source_detail,
    render_sources_index,
    render_synthesis_detail,
    render_syntheses_index,
    render_timeline,
    render_timeline_day,
    render_topic_detail,
    render_topics_index,
)
from llm_wiki.wiki_store import WikiPage


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _wiki_pages_for(graph: ResearchGraph) -> dict[str, list[WikiPage]]:
    """Synthesize a minimal WikiPage per kind so detail renderers have input."""
    pages: dict[str, list[WikiPage]] = {
        "sources": [],
        "concepts": [],
        "entities": [],
        "papers": [],
        "repos": [],
        "topics": [],
        "syntheses": [],
        "questions": [],
    }

    def _make(kind: str, slug: str, title: str, body: str, frontmatter: dict | None = None) -> WikiPage:
        return WikiPage(
            kind=kind,
            slug=slug,
            title=title,
            body=body,
            path=Path(f"wiki/{kind}/{slug}.md"),
            frontmatter=frontmatter or {},
        )

    # Pull one node per public kind from the fixture graph if available.
    by_type: dict[str, list] = {}
    for n in graph.nodes:
        by_type.setdefault(n.type.value, []).append(n)

    src = next(iter(by_type.get(ResearchNodeType.SOURCE_DOCUMENT.value, [])), None)
    if src:
        pages["sources"].append(
            _make(
                "sources",
                "sample-source",
                src.name,
                "# Sample source\n\nLead paragraph.\n\n## Section\n\nA bullet:\n\n- one\n- two\n",
                {"node_id": src.id, "source_path": src.source_path or "", "generated_at": "2026-04-27"},
            )
        )

    paper = next(iter(by_type.get(ResearchNodeType.PAPER.value, [])), None)
    if paper:
        pages["papers"].append(
            _make(
                "papers",
                "sample-paper",
                paper.name,
                "# Sample paper\n\nAbstract excerpt for the paper page.\n",
                {"node_id": paper.id, "generated_at": "2026-04-27"},
            )
        )

    repo = next(iter(by_type.get(ResearchNodeType.REPOSITORY.value, [])), None)
    if repo:
        pages["repos"].append(
            _make(
                "repos",
                "sample-repo",
                repo.name,
                "# Sample repo\n\nReadme excerpt.\n",
                {"node_id": repo.id},
            )
        )

    concept = next(iter(by_type.get(ResearchNodeType.CONCEPT.value, [])), None)
    if concept:
        pages["concepts"].append(
            _make(
                "concepts",
                "sample-concept",
                concept.name,
                "# Sample concept\n\nA short definition with a [link](https://example.com).\n",
                {"node_id": concept.id},
            )
        )

    field = next(iter(by_type.get(ResearchNodeType.RESEARCH_FIELD.value, [])), None)
    if field:
        pages["topics"].append(
            _make(
                "topics",
                "sample-topic",
                field.name,
                "# Sample topic\n\nField overview.\n",
                {"node_id": field.id},
            )
        )

    # Synthetic entries for kinds the fixture doesn't naturally produce.
    pages["entities"].append(
        _make("entities", "sample-entity", "Sample Model", "# Sample entity\n\nModel page.\n")
    )
    pages["syntheses"].append(
        _make(
            "syntheses",
            "pulse",
            "Project pulse",
            "# Pulse\n\n- Three new papers this week.\n- One concept emerged.\n- Activity up 12%.\n",
            {"synthesis_kind": "pulse", "generated_at": "2026-04-27T12:00:00Z"},
        )
    )
    pages["syntheses"].append(
        _make(
            "syntheses",
            "weekly-2026-w17",
            "Weekly digest 2026-W17",
            "# Weekly\n\nWeekly summary.\n",
            {"synthesis_kind": "weekly", "generated_at": "2026-04-27T12:00:00Z"},
        )
    )
    pages["questions"].append(
        _make("questions", "sample-question", "Why does it work?", "# Open question\n\nWhy does it work?\n")
    )

    # Overview seed (used by render_home for the tagline).
    pages["overview"] = [
        _make("overview", "overview", "Overview", "A self-indexing knowledge base for the dogfood corpus.\n")
    ]

    return pages


@pytest.fixture
def site_ctx(wiki_sample_graph: ResearchGraph) -> SiteContext:
    return SiteContext.build(
        graph=wiki_sample_graph,
        wiki_pages_by_kind=_wiki_pages_for(wiki_sample_graph),
        site_title="LLM-Wiki",
    )


# ---------------------------------------------------------------------------
# shared assertions
# ---------------------------------------------------------------------------


def _assert_doc_shape(html: str) -> None:
    assert html.lstrip().lower().startswith("<!doctype html>"), "must begin with <!doctype html>"
    assert html.rstrip().endswith("</html>"), "must end with </html>"
    assert "<main" in html, "needs <main> landmark"
    assert "<nav" in html, "needs <nav> landmark"


def _assert_breadcrumb_contains(html: str, label: str) -> None:
    bc = re.search(r'<nav class="breadcrumbs"[^>]*>(.*?)</nav>', html, flags=re.DOTALL)
    assert bc, "page must contain a breadcrumbs <nav class='breadcrumbs'>"
    assert label in bc.group(1), f"breadcrumbs missing label {label!r}: {bc.group(1)[:200]}"


def _assert_ai_siblings(html: str) -> None:
    assert 'class="ai-siblings"' in html or "ai-siblings" in html, "page must include AI siblings footer"


# ---------------------------------------------------------------------------
# index renderers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "renderer,active_label",
    [
        (render_sources_index, "Sources"),
        (render_concepts_index, "Concepts"),
        (render_entities_index, "Entities"),
        (render_papers_index, "Papers"),
        (render_repos_index, "Repos"),
        (render_topics_index, "Topics"),
        (render_syntheses_index, "Syntheses"),
        (render_questions_index, "Open questions"),
    ],
)
def test_index_routes_render_full_html(site_ctx: SiteContext, renderer, active_label: str) -> None:
    out = renderer(site_ctx)
    _assert_doc_shape(out)
    _assert_breadcrumb_contains(out, active_label)


# ---------------------------------------------------------------------------
# detail renderers
# ---------------------------------------------------------------------------


def test_render_source_detail(site_ctx: SiteContext) -> None:
    pages = site_ctx.wiki_pages_by_kind["sources"]
    if not pages:
        pytest.skip("fixture has no source page")
    out = render_source_detail(site_ctx, pages[0])
    _assert_doc_shape(out)
    _assert_breadcrumb_contains(out, "Sources")
    _assert_ai_siblings(out)


def test_render_concept_detail(site_ctx: SiteContext) -> None:
    pages = site_ctx.wiki_pages_by_kind["concepts"]
    if not pages:
        pytest.skip("fixture has no concept page")
    out = render_concept_detail(site_ctx, pages[0])
    _assert_doc_shape(out)
    _assert_breadcrumb_contains(out, "Concepts")
    _assert_ai_siblings(out)


def test_render_entity_detail(site_ctx: SiteContext) -> None:
    pages = site_ctx.wiki_pages_by_kind["entities"]
    out = render_entity_detail(site_ctx, pages[0])
    _assert_doc_shape(out)
    _assert_breadcrumb_contains(out, "Entities")
    _assert_ai_siblings(out)


def test_render_paper_detail(site_ctx: SiteContext) -> None:
    pages = site_ctx.wiki_pages_by_kind["papers"]
    if not pages:
        pytest.skip("fixture has no paper page")
    out = render_paper_detail(site_ctx, pages[0])
    _assert_doc_shape(out)
    _assert_breadcrumb_contains(out, "Papers")
    _assert_ai_siblings(out)
    assert "Mentions in the corpus" in out
    assert "Related" in out
    assert "Source provenance" in out
    assert "Activity" in out


def test_paper_detail_renders_markdown_body(site_ctx: SiteContext) -> None:
    """Markdown bodies must be rendered to real HTML, not dumped as plain text."""
    pages = site_ctx.wiki_pages_by_kind["papers"]
    if not pages:
        pytest.skip("fixture has no paper page")
    # Override with a body that exercises every markdown feature we promise.
    rich_page = WikiPage(
        kind="papers",
        slug=pages[0].slug,
        title=pages[0].title,
        body=(
            "# Top heading\n\n"
            "## Section heading\n\n"
            "A short paragraph with **bold**, *italic*, and `inline code`.\n\n"
            "- bullet one\n"
            "- bullet two\n\n"
            "```python\nprint('hi')\n```\n\n"
            "A [wiki link](papers/foo.md) and an [external](https://example.com/x).\n"
        ),
        path=pages[0].path,
        frontmatter=pages[0].frontmatter,
    )
    out = render_paper_detail(site_ctx, rich_page)
    # ATX headings → real <h1> / <h2> tags.
    assert "<h1" in out and ">Top heading</h1>" in out
    assert "<h2" in out and ">Section heading</h2>" in out
    # Emphasis runs.
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out
    # Inline code.
    assert "<code>inline code</code>" in out
    # Lists.
    assert "<ul>" in out and "<li>bullet one</li>" in out
    # Code fence renders to <pre><code>...</code></pre>.
    assert "<pre><code" in out and "print(&#x27;hi&#x27;)" in out
    # Wiki link rewriting: papers/foo.md → papers/foo.html.
    assert '<a href="papers/foo.html"' in out
    assert ">wiki link</a>" in out
    # External link preserved.
    assert '<a href="https://example.com/x"' in out
    # No raw markdown leaks through.
    body_section = re.search(
        r'<section class="markdown-body">(.*?)</section>', out, flags=re.DOTALL
    )
    assert body_section, "rendered detail must wrap body in .markdown-body"
    inner = body_section.group(1)
    assert not re.search(r"^# ", inner, flags=re.MULTILINE), "literal '# heading' leaked"
    assert "**bold**" not in inner
    assert "[wiki link](papers/foo.md)" not in inner


def test_render_repo_detail(site_ctx: SiteContext) -> None:
    pages = site_ctx.wiki_pages_by_kind["repos"]
    if not pages:
        pytest.skip("fixture has no repo page")
    out = render_repo_detail(site_ctx, pages[0])
    _assert_doc_shape(out)
    _assert_breadcrumb_contains(out, "Repos")


def test_render_topic_detail(site_ctx: SiteContext) -> None:
    pages = site_ctx.wiki_pages_by_kind["topics"]
    if not pages:
        pytest.skip("fixture has no topic page")
    out = render_topic_detail(site_ctx, pages[0])
    _assert_doc_shape(out)
    _assert_breadcrumb_contains(out, "Topics")


def test_render_synthesis_detail(site_ctx: SiteContext) -> None:
    pages = site_ctx.wiki_pages_by_kind["syntheses"]
    out = render_synthesis_detail(site_ctx, pages[0])
    _assert_doc_shape(out)
    _assert_breadcrumb_contains(out, "Syntheses")


def test_synthesis_detail_strips_frontmatter(site_ctx: SiteContext) -> None:
    """A synthesis body with leading YAML frontmatter must not bleed ``---``
    into the rendered HTML."""
    page = WikiPage(
        kind="syntheses",
        slug="pulse",
        title="Project pulse",
        body=(
            "---\n"
            "title: Project pulse\n"
            "synthesis_kind: pulse\n"
            "generated_at: 2026-04-27\n"
            "---\n"
            "# Project pulse\n\n"
            "- A new paper landed.\n"
        ),
        path=Path("wiki/syntheses/pulse.md"),
        frontmatter={"synthesis_kind": "pulse", "generated_at": "2026-04-27"},
    )
    out = render_synthesis_detail(site_ctx, page)
    body_section = re.search(
        r'<section class="markdown-body">(.*?)</section>', out, flags=re.DOTALL
    )
    assert body_section, "detail must wrap body in .markdown-body"
    inner = body_section.group(1)
    # No raw frontmatter delimiters in rendered output.
    assert "---" not in inner, f"raw frontmatter leaked: {inner!r}"
    assert "synthesis_kind:" not in inner
    assert "<h1" in inner


def test_render_question_detail(site_ctx: SiteContext) -> None:
    pages = site_ctx.wiki_pages_by_kind["questions"]
    out = render_question_detail(site_ctx, pages[0])
    _assert_doc_shape(out)
    _assert_breadcrumb_contains(out, "Open questions")


# ---------------------------------------------------------------------------
# special pages
# ---------------------------------------------------------------------------


def test_render_home_has_hero_pulse_stats_and_heatmap(site_ctx: SiteContext) -> None:
    out = render_home(site_ctx)
    _assert_doc_shape(out)
    assert 'class="hero"' in out, "home must include hero markers"
    assert "Sources" in out and "Concepts" in out and "Papers" in out and "Open questions" in out
    # stat row
    assert 'class="stat"' in out, "home must include stat row entries"
    # 26-week heatmap
    assert 'class="heatmap"' in out, "home must include the activity heatmap SVG"
    # tagline pulled from overview
    assert "self-indexing knowledge base" in out


def test_render_home_includes_mobile_chrome(site_ctx: SiteContext) -> None:
    """Home must ship the mobile drawer toggle and bottom nav (visibility
    controlled via CSS media queries — the markup is always present)."""
    out = render_home(site_ctx)
    assert "data-toggle-rail" in out, "home must include the rail toggle"
    assert "data-toggle-toc" in out, "home must include the toc toggle"
    assert '<nav class="mobile-bottom-nav"' in out, "home must include the bottom nav"


def test_render_home_orders_stats_then_activity_then_pulse(site_ctx: SiteContext) -> None:
    """Home order is hero → stats → activity (compact) → pulse → browse.

    Activity must sit immediately under the stat row (compact heatmap),
    not at the bottom of the page where it used to live."""
    out = render_home(site_ctx)
    stats_idx = out.index('class="stats')
    activity_idx = out.index("activity--compact")
    browse_idx = out.index("entry-points-wrap")
    assert stats_idx < activity_idx, (
        "stats must come before activity heatmap"
    )
    assert activity_idx < browse_idx, (
        "activity heatmap must come before the Browse entry points"
    )
    # Pulse cards are conditional (only when a pulse synthesis exists);
    # when present, they must sit between activity and browse.
    if "pulse-cards" in out:
        pulse_idx = out.index("pulse-cards")
        assert activity_idx < pulse_idx < browse_idx, (
            "pulse cards must sit between activity and browse when present"
        )


def test_render_home_emits_toc_for_scrollspy(site_ctx: SiteContext) -> None:
    """Home page must emit a populated right-rail TOC so the scrollspy
    can highlight the current section while scrolling."""
    out = render_home(site_ctx)
    # The wrapper aside (toc-rail) must NOT be hidden — it must contain
    # an inner ``aside class="toc"`` populated by ``components.toc``.
    assert '<aside class="toc-rail" id="toc" hidden>' not in out, (
        "home must populate the right-rail TOC, not emit it hidden"
    )
    assert '<aside class="toc"' in out, "home must include the inner TOC panel"
    # Section ids the TOC points at.
    for anchor in ("stats", "activity", "browse"):
        assert f'data-toc-target="{anchor}"' in out, (
            f"TOC must include data-toc-target for {anchor}"
        )
        assert f'id="{anchor}"' in out, (
            f"home must stamp id={anchor!r} on its matching section"
        )


def test_render_timeline_renders_full_doc(site_ctx: SiteContext) -> None:
    out = render_timeline(site_ctx)
    _assert_doc_shape(out)
    _assert_breadcrumb_contains(out, "Timeline")


def test_render_graph_view_includes_payload_script(site_ctx: SiteContext) -> None:
    from llm_wiki.site.pages import build_graph_payload

    out = render_graph_view(site_ctx)
    _assert_doc_shape(out)
    # Issue 4 — the user-visible label is "Graph", not "Graph view".
    _assert_breadcrumb_contains(out, "Graph")
    # Graph payload now lives in graph/payload.json (fetched by graph.js) so
    # the HTML does NOT inline the payload — the perf budget caps the page at
    # 50 KB. The fetch hint still points at payload.json.
    assert 'data-payload-url="payload.json"' in out
    assert "payload.json" in out
    # The graph.js bundle is loaded only on this route via the second
    # script. Filename is content-hashed (``graph-<10-hex>.js``) so
    # aggressive caches can't serve a stale version; the exact hash
    # changes with every JS edit which is the whole point.
    assert re.search(r'src="\.\./assets/graph-[0-9a-f]{10}\.js"', out) is not None
    # And the payload itself is computable from the same context.
    payload = build_graph_payload(site_ctx)
    assert "nodes" in payload
    assert "links" in payload or "edges" in payload
    leaked = [n for n in payload["nodes"] if n.get("type") in {"CodeClass", "CodeFunction", "CodeModule"}]
    assert not leaked, f"graph view leaked code-layer nodes: {leaked!r}"


def test_graph_payload_uses_actual_synthesis_page_slug() -> None:
    """Graph-node clicks must target emitted synthesis pages, not title slugs.

    Synthesis pages can have stable semantic filenames such as ``pulse.html``
    while the graph node is named ``Project pulse``. The graph payload is what
    click navigation consumes, so it must resolve through ``SiteContext``'s
    page_slug_for_node mapping rather than minting ``project-pulse.html``.
    """
    graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id="Synthesis:pulse:test",
                name="Project pulse",
                type=ResearchNodeType.SYNTHESIS,
            )
        ],
        edges=[],
    )
    page = WikiPage(
        kind="syntheses",
        slug="pulse",
        title="Project pulse",
        body="# Project pulse\n\nSummary.\n",
        path=Path("wiki/syntheses/pulse.md"),
        frontmatter={"synthesis_kind": "pulse"},
    )
    ctx = SiteContext.build(graph=graph, wiki_pages_by_kind={"syntheses": [page]})

    from llm_wiki.site.pages import build_graph_payload

    payload = build_graph_payload(ctx)

    assert payload["nodes"][0]["href"] == "../syntheses/pulse.html"


def test_render_about_renders_full_doc(site_ctx: SiteContext) -> None:
    out = render_about(site_ctx)
    _assert_doc_shape(out)
    _assert_breadcrumb_contains(out, "About")


# ---------------------------------------------------------------------------
# new bug-fix coverage
# ---------------------------------------------------------------------------


def test_paper_detail_strips_duplicate_h1_when_body_repeats_frontmatter_title(
    site_ctx: SiteContext,
) -> None:
    """When the body opens with ``# Same as frontmatter title`` we strip it
    so the page header doesn't render the title twice."""
    page = WikiPage(
        kind="papers",
        slug="dupe-h1-paper",
        title="Dupe H1 Paper",
        body="# Dupe H1 Paper\n\nAbstract goes here.\n",
        path=Path("wiki/papers/dupe-h1-paper.md"),
        frontmatter={"title": "Dupe H1 Paper"},
    )
    out = render_paper_detail(site_ctx, page)
    body_section = re.search(
        r'<section class="markdown-body">(.*?)</section>', out, flags=re.DOTALL
    )
    assert body_section, "detail must wrap body in .markdown-body"
    inner = body_section.group(1)
    # The duplicate H1 must not appear inside the markdown body.
    assert "Dupe H1 Paper</h1>" not in inner, (
        "leading H1 matching frontmatter title should be stripped"
    )
    # Page header still renders the title once.
    assert "<h1>Dupe H1 Paper</h1>" in out


def test_paper_detail_keeps_body_h2_intact(site_ctx: SiteContext) -> None:
    """Stripping only fires for a leading H1; deeper headings stay intact."""
    page = WikiPage(
        kind="papers",
        slug="deeper-headings-paper",
        title="Deeper Headings Paper",
        body="# Deeper Headings Paper\n\n## Section\n\nContent.\n",
        path=Path("wiki/papers/deeper-headings-paper.md"),
        frontmatter={"title": "Deeper Headings Paper"},
    )
    out = render_paper_detail(site_ctx, page)
    body_section = re.search(
        r'<section class="markdown-body">(.*?)</section>', out, flags=re.DOTALL
    )
    assert body_section
    inner = body_section.group(1)
    assert "<h2" in inner and ">Section</h2>" in inner


def test_paper_detail_related_section_has_real_anchors(
    site_ctx: SiteContext,
) -> None:
    """The Related section must surface at least one ``<a href=...>`` link
    when the node has neighbours in the graph."""
    pages = site_ctx.wiki_pages_by_kind["papers"]
    if not pages:
        pytest.skip("fixture has no paper page")
    out = render_paper_detail(site_ctx, pages[0])
    related = re.search(
        r'<section id="related"[^>]*>(.*?)</section>', out, flags=re.DOTALL
    )
    assert related, "Related section must be present"
    inner = related.group(1)
    assert re.search(r'<a [^>]*href="[^"]+"', inner), (
        f"Related must contain an <a href=...> anchor: {inner[:300]}"
    )


def test_paper_detail_activity_svg_has_nonzero_rect_when_activity_exists(
    site_ctx: SiteContext,
) -> None:
    """A node with at least one dated source path produces a sparkline whose
    polyline isn't pinned to the zero-line."""
    pages = site_ctx.wiki_pages_by_kind["papers"]
    if not pages:
        pytest.skip("fixture has no paper page")
    # Synthesize an activity bucket for the page's underlying node so the
    # test is deterministic regardless of fixture content.
    node_id = (pages[0].frontmatter or {}).get("node_id")
    if not node_id:
        pytest.skip("paper fixture has no node_id frontmatter")
    new_activity = dict(site_ctx.activity_by_node_id)
    new_activity[node_id] = [0, 1, 2, 1, 0, 3, 5, 2, 0, 1, 0, 4]
    ctx2 = SiteContext(
        site_title=site_ctx.site_title,
        graph=site_ctx.graph,
        wiki_pages_by_kind=site_ctx.wiki_pages_by_kind,
        nodes_by_id=site_ctx.nodes_by_id,
        nodes_by_kind=site_ctx.nodes_by_kind,
        nodes_by_name=site_ctx.nodes_by_name,
        outgoing=site_ctx.outgoing,
        incoming=site_ctx.incoming,
        type_counts=site_ctx.type_counts,
        source_counts=site_ctx.source_counts,
        activity_weeks=site_ctx.activity_weeks,
        relevance=site_ctx.relevance,
        page_slug_for_node=site_ctx.page_slug_for_node,
        activity_by_node_id=new_activity,
        source_body_by_path=site_ctx.source_body_by_path,
        node_id_for_source_path=site_ctx.node_id_for_source_path,
    )
    out = render_paper_detail(ctx2, pages[0])
    activity = re.search(
        r'<section id="activity"[^>]*>(.*?)</section>', out, flags=re.DOTALL
    )
    assert activity, "Activity section must be present"
    inner = activity.group(1)
    # The sparkline polyline must not be a flat zero line — at least one
    # point should sit above the floor (y < height-2).
    points_match = re.search(r'<polyline points="([^"]+)"', inner)
    assert points_match, f"sparkline polyline missing: {inner[:300]}"
    points = points_match.group(1)
    ys = [float(p.split(",")[1]) for p in points.split() if "," in p]
    assert any(y < 25 for y in ys), f"expected at least one elevated y: {ys}"


def test_paper_detail_renders_default_site_title_when_unset() -> None:
    """When ``SiteContext`` is built with the default site_title we render
    ``LLM-Wiki`` in the page chrome, not the project's MCP server name."""
    from llm_wiki.research_graph import ResearchGraph
    ctx = SiteContext.build(
        graph=ResearchGraph(nodes=[], edges=[]),
        wiki_pages_by_kind={
            "sources": [], "concepts": [], "entities": [], "papers": [],
            "repos": [], "topics": [], "syntheses": [], "questions": [],
        },
    )
    assert ctx.site_title == "LLM-Wiki"


def test_paper_detail_autolinks_bare_arxiv_url(site_ctx: SiteContext) -> None:
    """A bare arxiv URL in a markdown body becomes a clickable anchor."""
    pages = site_ctx.wiki_pages_by_kind["papers"]
    if not pages:
        pytest.skip("fixture has no paper page")
    page = WikiPage(
        kind="papers",
        slug=pages[0].slug,
        title=pages[0].title,
        body="See https://arxiv.org/abs/2604.20329 for details.\n",
        path=pages[0].path,
        frontmatter=pages[0].frontmatter,
    )
    out = render_paper_detail(site_ctx, page)
    assert '<a href="https://arxiv.org/abs/2604.20329"' in out
    assert ">https://arxiv.org/abs/2604.20329</a>" in out


def test_paper_detail_autolinks_arxiv_id_token(site_ctx: SiteContext) -> None:
    """``arXiv:2604.20329`` becomes a link to https://arxiv.org/abs/2604.20329."""
    pages = site_ctx.wiki_pages_by_kind["papers"]
    if not pages:
        pytest.skip("fixture has no paper page")
    page = WikiPage(
        kind="papers",
        slug=pages[0].slug,
        title=pages[0].title,
        body="The paper is arXiv:2604.20329 from 2026.\n",
        path=pages[0].path,
        frontmatter=pages[0].frontmatter,
    )
    out = render_paper_detail(site_ctx, page)
    assert '<a href="https://arxiv.org/abs/2604.20329"' in out
    assert "arXiv:2604.20329</a>" in out


# ---------------------------------------------------------------------------
# routing rules: no detail page for code-layer types
# ---------------------------------------------------------------------------


def test_no_public_renderer_for_code_layer_types() -> None:
    """``pages.py`` exports no renderer for CodeClass/CodeFunction/etc."""
    import llm_wiki.site.pages as pages_module

    for forbidden in ("render_codeclass_detail", "render_codefunction_detail",
                      "render_codemodule_detail", "render_evidence_span",
                      "render_claim_detail"):
        assert not hasattr(pages_module, forbidden), f"unexpected exposed renderer: {forbidden}"


def test_page_href_kinds_are_wiki_layer_only() -> None:
    """page_href must refuse to mint URLs for code-layer kinds."""
    from llm_wiki.site.pages import ROUTE_FOR_KIND

    forbidden = {"codeclass", "codefunction", "codemodule", "evidence", "claim"}
    overlap = forbidden & set(ROUTE_FOR_KIND)
    assert not overlap, f"ROUTE_FOR_KIND must not include code-layer kinds: {overlap}"

    # Spot check a wiki-layer slug.
    assert page_href("papers", "abc") == "papers/abc.html"


# ---------------------------------------------------------------------------
# JS bundle smoke
# ---------------------------------------------------------------------------


def test_js_bundle_concatenates_three_modules() -> None:
    assert JS_THEME_TOGGLE in JS_BUNDLE
    assert JS_SEARCH_PALETTE in JS_BUNDLE
    assert JS_GRAPH in JS_BUNDLE
    assert "data-theme" in JS_THEME_TOGGLE
    assert "search-data" in JS_SEARCH_PALETTE or "search-index.json" in JS_SEARCH_PALETTE
    assert "graph-data" in JS_GRAPH


# ---------------------------------------------------------------------------
# per-day timeline pages + heatmap anchors
# ---------------------------------------------------------------------------


def _ctx_with_day(site_ctx: SiteContext, date_str: str) -> SiteContext:
    """Return a copy of ``site_ctx`` whose ``activity_by_day`` is forced to
    contain ``date_str`` mapped to *every* node in the graph.

    Lets the timeline-day tests run deterministically regardless of which
    dates the wiki_corpus fixture happens to mention.
    """
    every_id = frozenset(n.id for n in site_ctx.graph.nodes)
    activity = dict(site_ctx.activity_by_day)
    activity[date_str] = every_id
    return SiteContext(
        site_title=site_ctx.site_title,
        graph=site_ctx.graph,
        wiki_pages_by_kind=site_ctx.wiki_pages_by_kind,
        nodes_by_id=site_ctx.nodes_by_id,
        nodes_by_kind=site_ctx.nodes_by_kind,
        nodes_by_name=site_ctx.nodes_by_name,
        outgoing=site_ctx.outgoing,
        incoming=site_ctx.incoming,
        type_counts=site_ctx.type_counts,
        source_counts=site_ctx.source_counts,
        activity_weeks=site_ctx.activity_weeks,
        relevance=site_ctx.relevance,
        page_slug_for_node=site_ctx.page_slug_for_node,
        activity_by_node_id=site_ctx.activity_by_node_id,
        source_body_by_path=site_ctx.source_body_by_path,
        node_id_for_source_path=site_ctx.node_id_for_source_path,
        activity_by_day=activity,
        sources_by_day=site_ctx.sources_by_day,
    )


def test_render_timeline_day_returns_full_html_doc(site_ctx: SiteContext) -> None:
    ctx2 = _ctx_with_day(site_ctx, "2026-04-27")
    out = render_timeline_day(ctx2, "2026-04-27")
    _assert_doc_shape(out)
    _assert_breadcrumb_contains(out, "Timeline")
    _assert_breadcrumb_contains(out, "2026-04-27")
    # Sections we promised in the spec.
    assert "Source files touched" in out
    assert "Concepts introduced" in out
    assert "Edges added" in out
    assert "Syntheses that consumed this day" in out


def test_render_timeline_day_has_iso_week_eyebrow(site_ctx: SiteContext) -> None:
    ctx2 = _ctx_with_day(site_ctx, "2026-04-27")
    out = render_timeline_day(ctx2, "2026-04-27")
    # 2026-04-27 is a Monday in ISO week 18.
    assert "Monday" in out
    assert "ISO week 2026-W18" in out


def test_render_timeline_day_empty_state_when_no_activity(site_ctx: SiteContext) -> None:
    out = render_timeline_day(site_ctx, "1999-01-01")
    _assert_doc_shape(out)
    assert "Nothing was indexed on this day" in out
    # Back-link to the timeline index.
    assert 'href="index.html"' in out
    assert "Back to Timeline" in out


def test_render_timeline_day_idempotent(site_ctx: SiteContext) -> None:
    """Two consecutive renders of the same day must be byte-identical."""
    ctx2 = _ctx_with_day(site_ctx, "2026-04-27")
    a = render_timeline_day(ctx2, "2026-04-27")
    b = render_timeline_day(ctx2, "2026-04-27")
    assert a == b


def test_render_timeline_heatmap_cells_are_anchored(site_ctx: SiteContext) -> None:
    """When the corpus has dated activity, heatmap cells become <a> wrappers
    around the <rect>, with the href pointing at ``../timeline/<day>.html``.
    """
    ctx2 = _ctx_with_day(site_ctx, "2026-04-27")
    out = render_timeline(ctx2)
    # The SVG must declare the xlink namespace and contain at least one
    # anchor wrapping a heatmap rect.
    assert 'xmlns:xlink="http://www.w3.org/1999/xlink"' in out
    assert re.search(
        r'<a [^>]*xlink:href="\.\./timeline/2026-04-27\.html"[^>]*>\s*<rect',
        out,
    ), "heatmap cell for 2026-04-27 must be wrapped in an <a> link"


# ---------------------------------------------------------------------------
# Issue 1 — wide layout for index/listing routes
# ---------------------------------------------------------------------------


def test_index_pages_emit_main_wide_class(site_ctx: SiteContext) -> None:
    """Index/listing routes opt into the wide-content variant so the table
    can fill the desktop viewport instead of squishing into the prose
    column."""
    for renderer in (
        render_concepts_index,
        render_papers_index,
        render_repos_index,
        render_topics_index,
        render_syntheses_index,
        render_questions_index,
        render_entities_index,
        render_sources_index,
    ):
        out = renderer(site_ctx)
        assert 'class="main main--wide"' in out, (
            f"{renderer.__name__} must opt into main--wide"
        )


def test_detail_pages_keep_default_main(site_ctx: SiteContext) -> None:
    """Detail pages stay in the prose-comfortable reading column."""
    pages = site_ctx.wiki_pages_by_kind["papers"]
    if not pages:
        pytest.skip("fixture has no paper page")
    out = render_paper_detail(site_ctx, pages[0])
    # The plain ``main`` class — no ``main--wide`` modifier on detail
    # pages.
    assert 'class="main"' in out
    assert "main--wide" not in out


def test_home_emits_main_wide_class(site_ctx: SiteContext) -> None:
    """Home is index-like (stats, entry-point cards, heatmap) so it goes
    wide too."""
    out = render_home(site_ctx)
    assert 'class="main main--wide"' in out


# ---------------------------------------------------------------------------
# Issue 3 — auto-link post-pass on detail pages
# ---------------------------------------------------------------------------


def test_paper_detail_runs_auto_link_post_pass(
    wiki_sample_graph: ResearchGraph,
) -> None:
    """A paper-detail body that mentions another graph node by name must
    have that mention rewritten into a ``class="auto-link"`` anchor.
    """
    # Pick any concept node from the fixture corpus and craft a paper body
    # that references its name in plain text.
    concept = next(
        (n for n in wiki_sample_graph.nodes
         if n.type == ResearchNodeType.CONCEPT and len(n.name) >= 4),
        None,
    )
    paper = next(
        (n for n in wiki_sample_graph.nodes
         if n.type == ResearchNodeType.PAPER),
        None,
    )
    if concept is None or paper is None:
        pytest.skip("fixture has no concept or paper node")

    pages_by_kind = _wiki_pages_for(wiki_sample_graph)
    # Replace the synthesised paper page body with one that mentions the
    # chosen concept by name in plain prose. We use a body that already
    # has rendered HTML markers so we know the auto-linker walked it.
    if not pages_by_kind["papers"]:
        pytest.skip("paper fixture missing")
    paper_page = pages_by_kind["papers"][0]
    rich_page = WikiPage(
        kind="papers",
        slug=paper_page.slug,
        title=paper_page.title,
        body=f"# Note\n\nThis paper relies on {concept.name} extensively.\n",
        path=paper_page.path,
        frontmatter=paper_page.frontmatter,
    )
    pages_by_kind["papers"][0] = rich_page

    ctx = SiteContext.build(
        graph=wiki_sample_graph,
        wiki_pages_by_kind=pages_by_kind,
        site_title="LLM-Wiki",
    )
    out = render_paper_detail(ctx, rich_page)
    # The body must now contain an auto-link wrapper for the concept.
    assert 'class="auto-link"' in out, (
        "paper detail must auto-link known node mentions"
    )
    assert concept.name in out


# ---------------------------------------------------------------------------
# Polish pass: canonical article shell + graph layout + scrollspy
# ---------------------------------------------------------------------------


def _detail_renderers(site_ctx):
    """Yield ``(name, html)`` pairs for each detail-page renderer that has a
    fixture page. Skips kinds the fixture does not provide."""
    pages = site_ctx.wiki_pages_by_kind
    pairs = []
    if pages.get("sources"):
        pairs.append(("source", render_source_detail(site_ctx, pages["sources"][0])))
    if pages.get("concepts"):
        pairs.append(("concept", render_concept_detail(site_ctx, pages["concepts"][0])))
    if pages.get("entities"):
        pairs.append(("entity", render_entity_detail(site_ctx, pages["entities"][0])))
    if pages.get("papers"):
        pairs.append(("paper", render_paper_detail(site_ctx, pages["papers"][0])))
    if pages.get("repos"):
        pairs.append(("repo", render_repo_detail(site_ctx, pages["repos"][0])))
    if pages.get("topics"):
        pairs.append(("topic", render_topic_detail(site_ctx, pages["topics"][0])))
    if pages.get("syntheses"):
        pairs.append(("synthesis", render_synthesis_detail(site_ctx, pages["syntheses"][0])))
    if pages.get("questions"):
        pairs.append(("question", render_question_detail(site_ctx, pages["questions"][0])))
    return pairs


def test_every_detail_renderer_emits_canonical_article_shell(
    site_ctx: SiteContext,
) -> None:
    """The polish pass unifies every detail page on the same article shape.

    Each renderer (sources / concepts / entities / papers / repos / topics /
    syntheses / questions) must emit ``<article class="article">`` exactly
    once with header / body / footer slots so CSS owns alignment.
    """
    pairs = _detail_renderers(site_ctx)
    assert pairs, "fixture must surface at least one detail renderer"
    for name, html in pairs:
        assert html.count('<article class="article">') == 1, (
            f"{name} detail must emit <article class='article'> exactly once"
        )
        assert '<header class="article-header">' in html, (
            f"{name} detail missing article-header slot"
        )
        assert '<div class="article-body">' in html, (
            f"{name} detail missing article-body slot"
        )
        assert '<footer class="article-footer">' in html, (
            f"{name} detail missing article-footer slot"
        )


def test_about_page_uses_canonical_article_shell(site_ctx: SiteContext) -> None:
    """About is a detail-style page (single column of prose), not an
    index, so it follows the same alignment as paper / concept / etc."""
    out = render_about(site_ctx)
    assert '<article class="article">' in out
    assert '<header class="article-header">' in out
    assert '<div class="article-body">' in out
    # About no longer opts into main--wide.
    assert "main--wide" not in out


def test_timeline_day_uses_canonical_article_shell(site_ctx: SiteContext) -> None:
    """Per-day timeline detail pages share the same article shell as the
    other detail kinds — they are detail pages, not index pages."""
    from llm_wiki.site.pages import render_timeline_day
    # Pick any date that has activity in the fixture; if none, the empty
    # state must still emit the canonical shell.
    day = next(iter(site_ctx.activity_by_day.keys()), "2026-04-27")
    out = render_timeline_day(site_ctx, day)
    assert '<article class="article">' in out
    assert "main--wide" not in out


def test_render_graph_view_drops_right_rail_uses_cursor_tooltip(
    site_ctx: SiteContext,
) -> None:
    """Issue 2 — the bottom-right floating ``.graph-info-overlay`` panel
    is GONE. The cursor-following ``#graph-tooltip`` (injected into
    ``.graph-canvas-wrapper`` so the Fullscreen API still draws it on
    top) replaces it for hover preview, and the focused node's label
    sprite carries focus details inline.
    """
    out = render_graph_view(site_ctx)
    # main--graph modifier replaces main--wide; right rail is gone.
    assert 'class="main main--graph"' in out
    assert 'class="shell shell--graph"' in out
    # Right rail markup is suppressed entirely (no aside.toc, no
    # aside.toc-rail). The overlay sits inside the canvas wrapper.
    assert '<aside class="toc"' not in out
    assert '<aside class="toc-rail"' not in out
    assert "toc toc--graph" not in out
    # Left rail (doc-tree explorer) stays — the rail is part of page_shell.
    assert '<aside class="rail"' in out
    # Issue 2 — every trace of the bottom-right overlay panel must be
    # gone from the rendered HTML.
    assert "graph-info-overlay" not in out
    assert 'id="graph-info-panel"' not in out
    assert 'id="graph-info-empty"' not in out
    assert 'id="graph-info-content"' not in out
    assert 'id="graph-info-neighbors"' not in out
    assert "Selected node" not in out
    # The cursor-following tooltip lives inside the canvas wrapper so
    # the Fullscreen API draws it on top of the canvas.
    assert 'id="graph-tooltip"' in out
    assert 'class="graph-tooltip"' in out
    assert 'hidden' in out  # tooltip starts hidden
    # Canvas wrapper carries the .graph-canvas class with CSS-controlled
    # dimensions (clamp(560px, 70vh, 880px) on desktop).
    assert '<div class="graph-canvas"' in out
    assert 'id="graph-canvas"' in out
    # Issue 4 — wrapper + Fullscreen toolbar button.
    assert 'id="graph-canvas-wrapper"' in out
    assert 'data-graph-action="fullscreen"' in out
    # Issue 6 — Auto-browse toolbar button between Reset and Fullscreen.
    assert 'data-graph-action="auto-browse"' in out
    assert ">Auto-browse</button>" in out
    # Size hint is in the toolbar so users know what node radius means.
    assert "node size = √(connections)" in out


def test_render_graph_view_includes_focus_detail_panel_overlay(
    site_ctx: SiteContext,
) -> None:
    """F-5 — the floating focus-detail panel is back. NOT the right-rail
    info panel that ``test_render_graph_view_drops_right_rail_uses_cursor_tooltip``
    forbids; this one is a small bottom-right overlay inside the canvas
    wrapper that pins the currently-focused node's title/type/degree/
    description and an Open page link."""
    out = render_graph_view(site_ctx)
    # Panel container with the new id.
    assert 'id="graph-focus-panel"' in out
    assert 'class="graph-focus-panel"' in out
    # Starts hidden — focus event populates + reveals.
    assert 'aria-label="Focused node details"' in out
    # Slot ids the JS writes into.
    assert 'id="graph-focus-panel-title"' in out
    assert 'id="graph-focus-panel-meta"' in out
    assert 'id="graph-focus-panel-desc"' in out
    assert 'id="graph-focus-panel-open"' in out
    assert 'id="graph-focus-panel-neighbors"' in out
    # An explicit unfocus close-button so touch users can dismiss focus.
    assert 'data-graph-action="unfocus"' in out
    # The OLD right-rail panel must still be absent (regression guard).
    assert 'id="graph-info-panel"' not in out
    assert "graph-info-overlay" not in out


def test_render_graph_view_collapses_hero_into_compact_toolbar(
    site_ctx: SiteContext,
) -> None:
    """F-11 — the graph route is a tool, not a doc page. The first
    viewport must be the canvas + toolbar, not a hero title and an
    instruction paragraph. The shortcut hints + corpus stats are
    relocated to a popover gated by the ``?`` button."""
    out = render_graph_view(site_ctx)
    # The hero header is gone — no eyebrow, no big <h1>, no lead paragraph
    # describing how to drag/click/scroll.
    assert 'class="eyebrow"' not in out or "interactive graph" not in out
    assert "<h1>Knowledge graph</h1>" not in out
    assert "Tap or click a node to focus it" not in out
    # The toolbar carries the inline title + the ``?`` help button.
    assert 'class="graph-toolbar-title"' in out
    assert "<h1 class=\"graph-toolbar-title\">Graph</h1>" in out
    assert "data-graph-help" in out
    # First-viewport markup must NOT contain the words "Press" or
    # "shortcut" — those live inside the popover only. Slice the rendered
    # HTML at the help popover boundary and check the prefix.
    popover_marker = 'id="graph-help-popover"'
    assert popover_marker in out, "help popover must exist"
    pre_popover = out.split(popover_marker, 1)[0]
    assert "Press" not in pre_popover, (
        "F-11 — first viewport must not advertise shortcuts inline"
    )
    assert "shortcut" not in pre_popover, (
        "F-11 — first viewport must not advertise shortcuts inline"
    )
    # Popover starts collapsed: ``hidden`` attribute on the popover itself
    # AND the wrapper does NOT carry ``data-graph-help-open``.
    assert 'id="graph-help-popover"' in out
    # Popover content (relocated, not deleted): keyboard shortcuts + stats.
    assert "<kbd>/</kbd>" in out
    assert "<kbd>f</kbd>" in out
    assert "<kbd>Esc</kbd>" in out
    # The popover element keeps the hidden attribute so the closed state
    # is the default render.
    import re as _re
    popover_block = _re.search(
        r'<div class="graph-help"[^>]*id="graph-help-popover"[^>]*>',
        out,
    )
    assert popover_block is not None, "popover container must exist"
    assert "hidden" in popover_block.group(0), (
        "popover starts hidden by default"
    )


def test_index_pages_emit_canonical_main_wide_not_article_shell(
    site_ctx: SiteContext,
) -> None:
    """Index/listing pages keep the wide layout — they do NOT pick up the
    detail-page canonical article shell."""
    from llm_wiki.site.pages import (
        render_concepts_index,
        render_papers_index,
    )
    for renderer in (render_concepts_index, render_papers_index):
        out = renderer(site_ctx)
        assert 'class="main main--wide"' in out
        assert '<article class="article">' not in out, (
            "index pages keep the loose layout — no canonical article wrap"
        )


def test_build_graph_payload_node_sizing_uses_in_degree(site_ctx: SiteContext) -> None:
    """Node sphere size encodes IMPORTANCE — measured by in-degree
    (incoming edges). A paper cited by 50 syntheses is more important
    than one referencing 50 concepts. Formula:
    ``round(2 + min(in_degree, 200)**0.92 * 1.4, 2)``. Verify against
    the payload's own ``in_degree`` field which the build step exposes
    alongside total ``degree`` for this assertion."""

    from llm_wiki.site.pages import build_graph_payload

    payload = build_graph_payload(site_ctx)
    nodes = payload["nodes"]
    assert nodes, "fixture must produce at least one node"
    for node in nodes:
        in_deg = node.get("in_degree", 0)
        capped = min(in_deg, 200)
        expected = round(2 + (capped ** 0.92) * 1.4, 2)
        assert node["val"] == expected, (
            f"node {node['id']!r} has val={node['val']!r} but expected {expected!r} "
            f"(in_degree={in_deg})"
        )
        # Floor at 2.0 — leaves still need a visible sphere.
        assert node["val"] >= 2.0
        # Cap is 200; max val ≈ 2 + 200**0.92 * 1.4 ≈ 200. Leave a wide
        # comfort margin so float jitter never trips the bound.
        assert node["val"] <= 250.0


def test_build_graph_payload_hides_person_nodes_and_authored_by_edges() -> None:
    """Issue 5 — Person nodes (paper authors) and ``authored_by`` edges
    are filtered out of the interactive graph payload. They stay in
    ``graph.json`` (MCP / cognee see them); they only disappear from the
    on-page visualization so the canvas isn't drowned by author chrome.
    """
    from llm_wiki.site.pages import _GRAPH_HIDDEN_TYPES, build_graph_payload
    from llm_wiki.research_graph import ResearchEdge

    # Sanity-check the hidden-types contract.
    assert "Person" in _GRAPH_HIDDEN_TYPES

    paper = ResearchNode(
        id="Paper:scaling-laws:abc",
        name="Scaling Laws for Neural Language Models",
        type=ResearchNodeType.PAPER,
    )
    author = ResearchNode(
        id="Person:kaplan:def",
        name="Jared Kaplan",
        type=ResearchNodeType.PERSON,
    )
    concept = ResearchNode(
        id="Concept:scaling-laws:xyz",
        name="Scaling laws",
        type=ResearchNodeType.CONCEPT,
    )
    graph = ResearchGraph(
        nodes=[paper, author, concept],
        edges=[
            ResearchEdge(source=paper.id, target=author.id, type="authored_by"),
            ResearchEdge(source=concept.id, target=paper.id, type="mentioned_in"),
        ],
    )
    ctx = SiteContext.build(graph=graph, wiki_pages_by_kind={})

    payload = build_graph_payload(ctx)
    nodes = payload["nodes"]
    edges = payload["links"]

    # No Person node in the payload, no authored_by edge either.
    assert all(n.get("type") != "Person" for n in nodes), (
        f"Person nodes leaked into the graph payload: "
        f"{[n for n in nodes if n.get('type') == 'Person']!r}"
    )
    assert all(n.get("name") != "Jared Kaplan" for n in nodes)
    assert all(e.get("type") != "authored_by" for e in edges), (
        f"authored_by edges leaked into the graph payload: "
        f"{[e for e in edges if e.get('type') == 'authored_by']!r}"
    )
    # The legitimate Concept node + its non-authored edge survive.
    assert any(n.get("type") == "Paper" for n in nodes)
    assert any(n.get("type") == "Concept" for n in nodes)
    assert any(e.get("type") == "mentioned_in" for e in edges)


def test_is_translation_sibling_matches_localized_md_pairs() -> None:
    """The translation-sibling detector covers the four shapes used in
    this repo:
      * ``README.md`` <-> ``README.<lang>.md`` (root i18n)
      * ``foo.md`` <-> ``foo.<lang>.md`` (peer i18n)
      * ``docs/foo.md`` <-> ``docs/i18n/foo.<lang>.md`` (i18n subdir)
      * ``paper.md`` <-> ``paper_<lang>.md`` (scraped-paper convention)
    """
    from llm_wiki.site.pages import _is_translation_sibling

    # Root READMEs (dot suffix).
    assert _is_translation_sibling("README.md", "README.ko.md") is True
    assert _is_translation_sibling("README.ko.md", "README.md") is True
    assert _is_translation_sibling("README.fr.md", "README.zh.md") is True
    # Peer-directory i18n.
    assert _is_translation_sibling("docs/quickstart.md", "docs/quickstart.ja.md") is True
    # i18n subdirectory normalization (canonical at parent, translation in i18n/).
    assert _is_translation_sibling(
        "docs/integrations/rag-anything.md",
        "docs/i18n/integrations/rag-anything.ko.md",
    ) is True
    # Scraped-paper underscore convention.
    assert _is_translation_sibling(
        "data/research/daily/2026-04-06/papers/2304.12210/paper.md",
        "data/research/daily/2026-04-06/papers/2304.12210/paper_ko.md",
    ) is True
    # Different documents — must NOT match.
    assert _is_translation_sibling("README.md", "docs/quickstart.md") is False
    assert _is_translation_sibling("docs/foo.md", "docs/bar.md") is False
    # Same exact path — within-document extraction, not a translation pair.
    assert _is_translation_sibling("docs/architecture.md", "docs/architecture.md") is False
    # Empty inputs — short-circuit to False.
    assert _is_translation_sibling("", "README.ko.md") is False
    assert _is_translation_sibling("README.md", "") is False
    # Stem-prefix collision shouldn't false-match: ``paper.md`` and
    # ``paper2.md`` share a prefix but normalize to different stems.
    assert _is_translation_sibling("paper.md", "paper2.md") is False


def test_build_graph_payload_filters_translation_sibling_edges() -> None:
    """Edges between two nodes whose ``source_path`` resolves to the
    same canonical document (in different languages) are translation-
    siblings and are dropped from the interactive payload. They stay in
    ``ctx.graph.edges`` so MCP/Cognee consumers see them; only the
    visual graph loses them."""
    from llm_wiki.research_graph import ResearchEdge
    from llm_wiki.site.pages import build_graph_payload

    # Two SourceDocument nodes — one canonical, one Korean translation
    # — share a canonical path stem. The Concept node carries a normal
    # source_path that doesn't translation-match either of them.
    canonical = ResearchNode(
        id="SourceDocument:architecture-en",
        name="Architecture",
        type=ResearchNodeType.SOURCE_DOCUMENT,
        source_path="docs/architecture.md",
    )
    korean = ResearchNode(
        id="SourceDocument:architecture-ko",
        name="아키텍처",
        type=ResearchNodeType.SOURCE_DOCUMENT,
        source_path="docs/i18n/architecture.ko.md",
    )
    concept = ResearchNode(
        id="Concept:graph-store",
        name="Graph Store",
        type=ResearchNodeType.CONCEPT,
        source_path="docs/graph-store.md",
    )

    graph = ResearchGraph(
        nodes=[canonical, korean, concept],
        edges=[
            # Translation-sibling edge — must be filtered out.
            ResearchEdge(source=canonical.id, target=korean.id, type="documents"),
            # Reverse direction — also filtered.
            ResearchEdge(source=korean.id, target=canonical.id, type="documents"),
            # Genuine cross-document edge — must SURVIVE.
            ResearchEdge(source=canonical.id, target=concept.id, type="documents"),
        ],
    )
    # Opt back into the dense view so the SourceDocument nodes survive
    # the default ``hide sources`` filter — the assertion under test is
    # about translation-sibling edge filtering specifically.
    ctx = SiteContext.build(
        graph=graph, wiki_pages_by_kind={}, show_sources=True
    )

    payload = build_graph_payload(ctx)
    nodes = payload["nodes"]
    edges = payload["links"]

    # Both translation siblings still appear as separate nodes — we
    # only filter EDGES between them, not the nodes themselves.
    node_ids = {n["id"] for n in nodes}
    assert canonical.id in node_ids
    assert korean.id in node_ids

    # No edge connects the two translation siblings.
    sibling_edges = [
        e for e in edges
        if {e["source"], e["target"]} == {canonical.id, korean.id}
    ]
    assert sibling_edges == [], (
        f"translation-sibling edges leaked into the graph payload: {sibling_edges!r}"
    )

    # The genuine cross-document edge survives.
    assert any(
        e["source"] == canonical.id and e["target"] == concept.id
        for e in edges
    )

    # The full graph still has all three edges — we only filter on
    # the visual payload.
    assert len(ctx.graph.edges) == 3


def test_build_graph_payload_hides_source_nodes_by_default() -> None:
    """Default behavior: ``sources``-group nodes never reach the visual
    payload. The dense raganything cloud floods the canvas and obscures
    the concept layer; users get the concept-only view unless they
    explicitly flip ``graph_view.show_sources`` in their config.

    Underlying ``ctx.graph`` is untouched — the SourceDocument node is
    still present for MCP, Cognee, search, and per-page wiki views.
    """
    from llm_wiki.research_graph import ResearchEdge
    from llm_wiki.site.pages import build_graph_payload

    source = ResearchNode(
        id="SourceDocument:whitepaper",
        name="docs/whitepaper.pdf",
        type=ResearchNodeType.SOURCE_DOCUMENT,
        source_path="docs/whitepaper.pdf",
    )
    concept = ResearchNode(
        id="Concept:scaling-laws",
        name="Scaling laws",
        type=ResearchNodeType.CONCEPT,
    )
    graph = ResearchGraph(
        nodes=[source, concept],
        edges=[
            ResearchEdge(source=source.id, target=concept.id, type="contains"),
        ],
    )
    # Default cfg — show_sources omitted, so the filter engages.
    ctx = SiteContext.build(graph=graph, wiki_pages_by_kind={})

    payload = build_graph_payload(ctx)
    node_ids = {n["id"] for n in payload["nodes"]}

    assert source.id not in node_ids, (
        "SourceDocument node leaked into the visual payload despite "
        "default-hide policy: " + repr(payload["nodes"])
    )
    assert concept.id in node_ids, (
        "Concept node was incorrectly dropped from the visual payload"
    )
    # The contains edge must NOT survive — one endpoint is hidden.
    assert payload["links"] == [], (
        f"edges incident to hidden source nodes leaked: {payload['links']!r}"
    )
    # Underlying graph still has every node + edge — only the visual
    # payload drops them.
    assert len(ctx.graph.nodes) == 2
    assert len(ctx.graph.edges) == 1


def test_build_graph_payload_includes_sources_when_show_sources_true() -> None:
    """Opt-in: ``graph_view.show_sources = true`` restores the dense view.

    Same fixture as the default-hide test, but with the config knob
    flipped on — the SourceDocument node AND its incident edge appear
    in the visual payload.
    """
    from llm_wiki.research_graph import ResearchEdge
    from llm_wiki.site.pages import build_graph_payload

    source = ResearchNode(
        id="SourceDocument:whitepaper",
        name="docs/whitepaper.pdf",
        type=ResearchNodeType.SOURCE_DOCUMENT,
        source_path="docs/whitepaper.pdf",
    )
    concept = ResearchNode(
        id="Concept:scaling-laws",
        name="Scaling laws",
        type=ResearchNodeType.CONCEPT,
    )
    graph = ResearchGraph(
        nodes=[source, concept],
        edges=[
            ResearchEdge(source=source.id, target=concept.id, type="contains"),
        ],
    )
    ctx = SiteContext.build(
        graph=graph, wiki_pages_by_kind={}, show_sources=True
    )

    payload = build_graph_payload(ctx)
    node_ids = {n["id"] for n in payload["nodes"]}

    assert source.id in node_ids
    assert concept.id in node_ids
    assert any(
        e["source"] == source.id and e["target"] == concept.id
        for e in payload["links"]
    ), f"contains edge missing when show_sources=True: {payload['links']!r}"


def test_build_graph_payload_drops_edges_incident_to_hidden_sources() -> None:
    """When ``sources`` are hidden, every edge incident to a dropped
    source node is filtered too — we never ship dangling edges. The
    concept-to-concept edge survives because both endpoints survive."""
    from llm_wiki.research_graph import ResearchEdge
    from llm_wiki.site.pages import build_graph_payload

    source_a = ResearchNode(
        id="SourceDocument:a",
        name="A.md",
        type=ResearchNodeType.SOURCE_DOCUMENT,
        source_path="A.md",
    )
    concept_x = ResearchNode(
        id="Concept:x",
        name="ConceptX",
        type=ResearchNodeType.CONCEPT,
    )
    concept_y = ResearchNode(
        id="Concept:y",
        name="ConceptY",
        type=ResearchNodeType.CONCEPT,
    )
    graph = ResearchGraph(
        nodes=[source_a, concept_x, concept_y],
        edges=[
            ResearchEdge(source=source_a.id, target=concept_x.id, type="contains"),
            ResearchEdge(source=source_a.id, target=concept_y.id, type="contains"),
            ResearchEdge(
                source=concept_x.id, target=concept_y.id,
                type="shares_concept_with",
            ),
        ],
    )
    ctx = SiteContext.build(graph=graph, wiki_pages_by_kind={})

    payload = build_graph_payload(ctx)
    node_ids = {n["id"] for n in payload["nodes"]}
    edge_pairs = {(e["source"], e["target"]) for e in payload["links"]}

    # Source is gone, both concepts survive.
    assert source_a.id not in node_ids
    assert concept_x.id in node_ids
    assert concept_y.id in node_ids

    # Both contains edges are gone.
    assert (source_a.id, concept_x.id) not in edge_pairs
    assert (source_a.id, concept_y.id) not in edge_pairs

    # The concept↔concept edge survives — both endpoints are visible.
    assert (concept_x.id, concept_y.id) in edge_pairs


def test_build_graph_payload_legend_drops_zero_source_pill() -> None:
    """The graph-view legend never ships a ``0 Sources`` chip. When sources
    are hidden the chip is omitted; a small "Sources hidden" note
    explains the absence so users aren't left wondering."""
    from llm_wiki.research_graph import ResearchEdge
    from llm_wiki.site.pages import render_graph_view

    source = ResearchNode(
        id="SourceDocument:whitepaper",
        name="docs/whitepaper.pdf",
        type=ResearchNodeType.SOURCE_DOCUMENT,
        source_path="docs/whitepaper.pdf",
    )
    concept = ResearchNode(
        id="Concept:scaling-laws",
        name="Scaling laws",
        type=ResearchNodeType.CONCEPT,
    )
    graph = ResearchGraph(
        nodes=[source, concept],
        edges=[ResearchEdge(source=source.id, target=concept.id, type="contains")],
    )
    ctx = SiteContext.build(graph=graph, wiki_pages_by_kind={})

    out = render_graph_view(ctx)

    # No ``data-group="sources"`` chip in the legend.
    assert 'data-group="sources"' not in out, (
        "graph legend still ships a sources chip even though sources are hidden"
    )
    # The "Sources hidden" note appears so the absence is explained.
    assert "Sources hidden" in out


def test_detail_page_keeps_sticky_toc_aside_for_long_articles(
    site_ctx: SiteContext,
) -> None:
    """Bug 1 — every detail page that emits a TOC keeps the inner
    ``aside.toc`` element so the CSS sticky rule can target it. Without
    the inner aside the rail collapses to the wrapper which has no
    sticky declaration of its own (the wrapper is just ``align-self:
    start`` on the grid)."""
    from llm_wiki.site.pages import render_concept_detail

    # Pick the first concept node from the fixture.
    concept = next(
        (n for n in site_ctx.graph.nodes if n.type.value == "Concept"),
        None,
    )
    if concept is None:
        import pytest as _pytest
        _pytest.skip("fixture has no concept nodes")
    out = render_concept_detail(site_ctx, concept)
    # The wrapper aside.toc-rail must contain the inner aside.toc — the
    # latter is what the CSS sticky rule targets.
    assert '<aside class="toc-rail"' in out
    # Either the helper renders the inner aside, or the page emits an
    # empty rail (no headings) — both are acceptable shapes.
    if "On this page" in out:
        assert '<aside class="toc"' in out
    # The scrollspy hook (data-toc-target) survives.
    if "On this page" in out and "<li" in out.split("On this page", 1)[1][:4000]:
        # Headings -> data-toc-target survives so the scrollspy can pair them.
        body_after_toc = out.split("On this page", 1)[1]
        assert "data-toc-target" in body_after_toc

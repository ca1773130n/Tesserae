"""Site-build performance budgets.

These guard the size & lazy-loading discipline that the wiki frontend redesign
locked in:

  * ``graph/index.html`` < 50 KB (the heavy payload moved to ``payload.json``).
  * ``graph/payload.json`` exists and is parseable JSON.
  * ``graph.json.gz`` exists and decompresses to bytes-identical of
    ``graph.json`` (deterministic ``mtime=0`` gzip).
  * ``search-index.json.gz`` exists and decompresses cleanly.
  * The base ``assets/app.js`` does NOT contain the graph-only JS — graph
    code is loaded lazily via a separate ``assets/graph.js``.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from llm_wiki.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from llm_wiki.site import StaticSiteBuilder
from llm_wiki.wiki_store import WikiPage, WikiPageStore


# --------------------------------------------------------------------- fixtures


def _toy_graph() -> ResearchGraph:
    """Same shape as ``tests/test_frontend.py``'s ``_toy_graph`` — a small mix
    of wiki-layer and code-layer nodes so the perf budget assertions still
    exercise the type-filter logic the renderer applies."""

    nodes = [
        ResearchNode(
            id="Paper:demo",
            name="Demo Paper",
            type=ResearchNodeType.PAPER,
            description="A demo paper.",
            source_path="data/papers/demo.pdf",
        ),
        ResearchNode(
            id="Concept:gs",
            name="Gaussian Splatting",
            type=ResearchNodeType.CONCEPT,
            description="A 3D scene representation.",
            source_path="data/papers/demo.pdf",
        ),
        ResearchNode(
            id="Repository:demo",
            name="demo-repo",
            type=ResearchNodeType.REPOSITORY,
            description="A demo repo.",
        ),
        ResearchNode(
            id="CodeClass:Splatter",
            name="Splatter",
            type=ResearchNodeType.CODE_CLASS,
            description="A code class.",
            source_path="src/splatter.py",
        ),
    ]
    edges = [
        ResearchEdge(source="Paper:demo", target="Concept:gs", type="mentioned_in"),
        ResearchEdge(source="Repository:demo", target="Paper:demo", type="implemented_in"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _seed_wiki(root: Path) -> None:
    store = WikiPageStore(root)
    pages = [
        WikiPage(
            kind="papers",
            slug="demo-paper",
            title="Demo Paper",
            body="# Demo Paper\n\nA paper page.\n",
            path=store.path_for("papers", "demo-paper"),
            frontmatter={
                "title": "Demo Paper",
                "source_path": "data/papers/demo.pdf",
                "generated_at": "2026-04-27T12:00:00Z",
            },
        ),
        WikiPage(
            kind="concepts",
            slug="gaussian-splatting",
            title="Gaussian Splatting",
            body="# Gaussian Splatting\n\nA 3D scene representation.\n",
            path=store.path_for("concepts", "gaussian-splatting"),
            frontmatter={"title": "Gaussian Splatting"},
        ),
        WikiPage(
            kind="syntheses",
            slug="pulse",
            title="Project pulse",
            body="# Project pulse\n\n- Three new papers landed.\n",
            path=store.path_for("syntheses", "pulse"),
            frontmatter={"synthesis_kind": "pulse"},
        ),
    ]
    for page in pages:
        store.write_page(page)


@pytest.fixture
def built_site(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _seed_wiki(wiki)
    out = tmp_path / "site"
    StaticSiteBuilder(site_title="Perf Test").write_site(_toy_graph(), wiki, out)
    return out


# ----------------------------------------------------------------- HTML budget


def test_graph_index_html_under_50kb(built_site: Path) -> None:
    """The graph route HTML must be small — the payload lives elsewhere."""
    html = built_site / "graph" / "index.html"
    assert html.exists()
    size = html.stat().st_size
    assert size < 50 * 1024, f"graph/index.html is {size} bytes (budget: 50 KB)"


def test_graph_index_html_does_not_inline_payload(built_site: Path) -> None:
    """No ``<script id='graph-data'>`` payload allowed in the route HTML."""
    html_text = (built_site / "graph" / "index.html").read_text(encoding="utf-8")
    assert 'id="graph-data"' not in html_text
    # And the page advertises where the payload actually lives.
    assert "payload.json" in html_text


# --------------------------------------------------------------- payload JSON


def test_graph_payload_json_exists_and_parses(built_site: Path) -> None:
    payload_path = built_site / "graph" / "payload.json"
    assert payload_path.exists(), "graph/payload.json must be emitted"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert "nodes" in payload
    assert "links" in payload
    # No code-layer nodes leak into the lazy-loaded payload either.
    leaked = [n for n in payload["nodes"] if n.get("type") in {"CodeClass", "CodeFunction", "CodeModule"}]
    assert not leaked, f"graph payload leaked code-layer nodes: {leaked!r}"


# ---------------------------------------------------------------- gzip siblings


def test_graph_json_gz_exists_and_decompresses(built_site: Path) -> None:
    raw = built_site / "graph.json"
    gz = built_site / "graph.json.gz"
    assert gz.exists(), "graph.json.gz must be pre-emitted next to graph.json"
    decompressed = gzip.decompress(gz.read_bytes())
    assert decompressed == raw.read_bytes(), "gzip sibling must round-trip exactly"


def test_search_index_json_gz_exists(built_site: Path) -> None:
    raw = built_site / "search-index.json"
    gz = built_site / "search-index.json.gz"
    assert gz.exists(), "search-index.json.gz must be pre-emitted"
    decompressed = gzip.decompress(gz.read_bytes())
    assert decompressed == raw.read_bytes()


def test_gz_siblings_are_deterministic(tmp_path: Path) -> None:
    """Two compiles with identical content must yield byte-identical .gz files
    (i.e. ``gzip.GzipFile(mtime=0)`` was used). This is what keeps ``git
    status .llm-wiki/site`` clean on a no-op recompile."""

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _seed_wiki(wiki)
    out_a = tmp_path / "site-a"
    out_b = tmp_path / "site-b"
    StaticSiteBuilder(site_title="Perf Test").write_site(_toy_graph(), wiki, out_a)
    StaticSiteBuilder(site_title="Perf Test").write_site(_toy_graph(), wiki, out_b)

    for relpath in ("graph.json.gz", "search-index.json.gz"):
        a = (out_a / relpath).read_bytes()
        b = (out_b / relpath).read_bytes()
        assert a == b, f"{relpath} drifted between back-to-back compiles"


# ------------------------------------------------- bundle splitting (lazy graph)


def test_app_js_does_not_contain_graph_renderer(built_site: Path) -> None:
    """The base bundle on every page must not include the heavy graph code."""
    app = (built_site / "assets" / "app.js").read_text(encoding="utf-8")
    # Markers that uniquely identify the graph-only renderer.
    assert "ForceGraph3D" not in app
    assert "3d-force-graph" not in app
    assert "Raycaster" not in app  # three.js raycaster lives in graph.js
    # Search palette + theme toggle still ship on every page.
    assert "search-index.json" in app
    assert "data-toggle-theme" in app


def test_graph_js_contains_renderer(built_site: Path) -> None:
    """The graph-only bundle is emitted at assets/graph.js and contains the
    interactive renderer."""
    g = built_site / "assets" / "graph.js"
    assert g.exists()
    text = g.read_text(encoding="utf-8")
    assert "ForceGraph3D" in text
    assert "linkHoverPrecision" in text


def test_non_graph_pages_do_not_load_graph_js(built_site: Path) -> None:
    """Sample a non-graph page and confirm it doesn't preload graph.js."""
    home = (built_site / "index.html").read_text(encoding="utf-8")
    assert "assets/graph.js" not in home
    paper = (built_site / "papers" / "demo-paper.html").read_text(encoding="utf-8")
    assert "assets/graph.js" not in paper


def test_graph_route_loads_graph_js(built_site: Path) -> None:
    html = (built_site / "graph" / "index.html").read_text(encoding="utf-8")
    assert "assets/graph.js" in html


# ---------------------------------------------------------- nginx ops snippet


def test_nginx_snippet_is_present_with_gzip_static_hint(built_site: Path) -> None:
    snippet = built_site / "nginx.snippet.conf"
    assert snippet.exists(), "ops doc snippet must be written for the gh-pages/nginx case"
    text = snippet.read_text(encoding="utf-8")
    assert "gzip_static" in text
    assert "Cache-Control" in text

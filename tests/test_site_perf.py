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

from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.site import StaticSiteBuilder
from tesserae.wiki_store import WikiPage, WikiPageStore


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
    # Graph View v1 (spec §B): the visual payload deliberately includes
    # ``Code*`` node types as one of the 8 colour families. The previous
    # assertion ("no code-layer leak") reflected the pre-v1 invariant
    # when the visual layer mirrored ``WIKI_LAYER_TYPES``; search/SEO
    # exporters still filter on WIKI_LAYER_TYPES, but ``payload.json``
    # is now the visualization payload. The v1 invariant we still want
    # to enforce here is that any code node carries the ``code`` family
    # so the legend + colour map render correctly.
    for node in payload["nodes"]:
        if node.get("type") in {"CodeClass", "CodeFunction", "CodeModule", "SourceFile"}:
            assert node.get("family") == "code", (
                f"code-layer node {node.get('id')!r} missing/incorrect family "
                f"(got {node.get('family')!r}, expected 'code')"
            )


# ------------------------------------------- split payload (core + rest)
#
# The graph route fetches ``payload-core.json`` (top-degree subgraph) first
# so the canvas paints almost immediately, then merges ``payload-rest.json``
# in the background. The legacy combined ``payload.json`` is still emitted
# so back-compat consumers (and any cached bookmarks) keep working.


def test_graph_payload_core_under_size_budget(built_site: Path) -> None:
    """``payload-core.json`` is the blocking fetch on first paint, so it
    has tight size budgets: < 100 KB raw, < 30 KB gzipped."""
    core_path = built_site / "graph" / "payload-core.json"
    assert core_path.exists(), "graph/payload-core.json must be emitted"
    raw_size = core_path.stat().st_size
    assert raw_size < 100 * 1024, f"payload-core.json is {raw_size} bytes (budget: 100 KB)"

    core_gz = built_site / "graph" / "payload-core.json.gz"
    assert core_gz.exists(), "graph/payload-core.json.gz must be pre-emitted"
    gz_size = core_gz.stat().st_size
    assert gz_size < 30 * 1024, f"payload-core.json.gz is {gz_size} bytes (budget: 30 KB)"


def test_graph_payload_rest_exists_and_compresses_well(built_site: Path) -> None:
    rest_path = built_site / "graph" / "payload-rest.json"
    assert rest_path.exists(), "graph/payload-rest.json must be emitted"
    rest = json.loads(rest_path.read_text(encoding="utf-8"))
    assert "nodes" in rest
    assert "links" in rest
    rest_gz = built_site / "graph" / "payload-rest.json.gz"
    assert rest_gz.exists(), "graph/payload-rest.json.gz must be pre-emitted"
    raw = rest_path.read_bytes()
    if len(raw) > 200:
        ratio = rest_gz.stat().st_size / max(1, len(raw))
        # JSON gzips to ~0.3-0.5x; require at least 0.7x as a sanity floor
        # to catch regressions where the file is mistakenly stored
        # uncompressed or double-compressed. (On tiny payloads gzip
        # overhead dominates so we skip the ratio check via the size guard
        # above.)
        assert ratio < 0.7, f"payload-rest.json gzip ratio {ratio:.2f} > 0.7"


def test_graph_payload_split_union_matches_combined(built_site: Path) -> None:
    """Core + rest must equal the combined ``payload.json`` — same node and
    link counts, same id sets. This is the contract that keeps every
    existing consumer of ``payload.json`` and every JS code path that walks
    the union correct."""
    full = json.loads((built_site / "graph" / "payload.json").read_text(encoding="utf-8"))
    core = json.loads((built_site / "graph" / "payload-core.json").read_text(encoding="utf-8"))
    rest = json.loads((built_site / "graph" / "payload-rest.json").read_text(encoding="utf-8"))

    assert len(core["nodes"]) + len(rest["nodes"]) == len(full["nodes"]), (
        f"node count mismatch: core={len(core['nodes'])} + rest={len(rest['nodes'])} "
        f"!= full={len(full['nodes'])}"
    )
    assert len(core["links"]) + len(rest["links"]) == len(full["links"]), (
        f"link count mismatch: core={len(core['links'])} + rest={len(rest['links'])} "
        f"!= full={len(full['links'])}"
    )
    full_ids = {n["id"] for n in full["nodes"]}
    split_ids = {n["id"] for n in core["nodes"]} | {n["id"] for n in rest["nodes"]}
    assert full_ids == split_ids, "split node ids do not equal full node ids"


def test_legacy_combined_payload_still_emitted(built_site: Path) -> None:
    """The legacy combined ``payload.json`` must still exist for back-compat."""
    assert (built_site / "graph" / "payload.json").exists()


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


def test_search_index_gz_under_one_megabyte(built_site: Path) -> None:
    """The gzipped search index must stay under 1 MB even with the body
    content the synthesis + raw-markdown indexer pulls in. The fixture corpus
    is small, but this guards against accidental token-cap regressions."""

    gz = built_site / "search-index.json.gz"
    assert gz.exists()
    size = gz.stat().st_size
    assert size < 1024 * 1024, f"search-index.json.gz is {size} bytes (budget: 1 MB)"
    # Sanity: decompresses to valid JSON.
    payload = json.loads(gzip.decompress(gz.read_bytes()).decode("utf-8"))
    assert isinstance(payload, list)


def test_search_index_indexes_synthesis_body(built_site: Path) -> None:
    """The fixture seeds a synthesis whose body says "Three new papers landed".
    A search-index entry should carry tokens like ``papers`` from the body."""

    payload = json.loads((built_site / "search-index.json").read_text(encoding="utf-8"))
    syntheses = [e for e in payload if e["kind"] == "syntheses"]
    assert syntheses, "expected at least one synthesis entry in the index"
    # ``Three new papers landed`` → tokens should include ``papers`` / ``landed``.
    body_tokens = set(syntheses[0]["tokens"])
    assert "papers" in body_tokens
    assert "landed" in body_tokens


def test_gz_siblings_are_deterministic(tmp_path: Path) -> None:
    """Two compiles with identical content must yield byte-identical .gz files
    (i.e. ``gzip.GzipFile(mtime=0)`` was used). This is what keeps ``git
    status .tesserae/site`` clean on a no-op recompile."""

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
    import re as _re
    pat = _re.compile(r"assets/graph-[0-9a-f]{10}\.js|assets/graph\.js")
    home = (built_site / "index.html").read_text(encoding="utf-8")
    assert not pat.search(home)
    paper = (built_site / "papers" / "demo-paper.html").read_text(encoding="utf-8")
    assert not pat.search(paper)


def test_graph_route_loads_graph_js(built_site: Path) -> None:
    html = (built_site / "graph" / "index.html").read_text(encoding="utf-8")
    # Content-hashed filename: ``graph-<10-hex>.js``. Match the shape;
    # the literal hash changes with every JS edit which is the point.
    import re as _re
    assert _re.search(r"assets/graph-[0-9a-f]{10}\.js", html) is not None


# ---------------------------------------------------------- nginx ops snippet


def test_nginx_snippet_is_present_with_gzip_static_hint(built_site: Path) -> None:
    snippet = built_site / "nginx.snippet.conf"
    assert snippet.exists(), "ops doc snippet must be written for the gh-pages/nginx case"
    text = snippet.read_text(encoding="utf-8")
    assert "gzip_static" in text
    assert "Cache-Control" in text

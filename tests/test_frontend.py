"""Smoke tests for the redesigned ``StaticSiteBuilder``.

Each test exercises a small toy graph + an in-memory wiki layer and asserts
that the new information architecture from §3.1 of the redesign spec lands
on disk:

  * the canonical routes all exist,
  * no per-``CodeClass`` / ``CodeFunction`` HTML pages slip through,
  * AI siblings (.txt + .json) ship next to detail HTML,
  * the AI/agent export bundle (llms.txt, llms-full.txt, graph.json,
    graph.jsonld, manifest.json, search-index.json, sitemap.xml, rss.xml,
    robots.txt) lands at the root,
  * the home page contains the hero + activity heatmap markers,
  * re-running ``write_site`` over the same input is byte-identical.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.frontend import StaticSiteBuilder  # legacy import path still works
from llm_wiki.site import StaticSiteBuilder as NewStaticSiteBuilder
from llm_wiki.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from llm_wiki.wiki_store import WikiPage, WikiPageStore


def _toy_graph() -> ResearchGraph:
    """A small graph mixing wiki-layer types with code-layer types.

    The code-layer types (CodeClass / CodeFunction) are deliberately included
    so the test can assert they do *not* get HTML pages.
    """
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
        ResearchNode(
            id="CodeFunction:render",
            name="render",
            type=ResearchNodeType.CODE_FUNCTION,
            description="The render function.",
            source_path="src/splatter.py",
        ),
    ]
    edges = [
        ResearchEdge(source="Paper:demo", target="Concept:gs", type="mentioned_in"),
        ResearchEdge(source="Repository:demo", target="Paper:demo", type="implemented_in"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _seed_wiki(root: Path) -> None:
    """Write a small wiki layer covering every public kind."""
    store = WikiPageStore(root)
    pages = [
        WikiPage(
            kind="sources",
            slug="demo-source",
            title="Demo Source",
            body="# Demo Source\n\nA short blurb.\n",
            path=store.path_for("sources", "demo-source"),
            frontmatter={
                "title": "Demo Source",
                "source_path": "data/sources/demo.md",
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
            kind="entities",
            slug="demo-model",
            title="Demo Model",
            body="# Demo Model\n\nA model entry.\n",
            path=store.path_for("entities", "demo-model"),
            frontmatter={"title": "Demo Model"},
        ),
        WikiPage(
            kind="papers",
            slug="demo-paper",
            title="Demo Paper",
            body="# Demo Paper\n\nA paper page with a [link](concepts/gaussian-splatting.html).\n",
            path=store.path_for("papers", "demo-paper"),
            frontmatter={
                "title": "Demo Paper",
                "source_path": "data/papers/demo.pdf",
                "generated_at": "2026-04-27T12:00:00Z",
            },
        ),
        WikiPage(
            kind="repos",
            slug="demo-repo",
            title="demo-repo",
            body="# demo-repo\n\nA demo repo page.\n",
            path=store.path_for("repos", "demo-repo"),
            frontmatter={"title": "demo-repo"},
        ),
        WikiPage(
            kind="topics",
            slug="3d-reconstruction",
            title="3D Reconstruction",
            body="# 3D Reconstruction\n\nA topic page.\n",
            path=store.path_for("topics", "3d-reconstruction"),
            frontmatter={"title": "3D Reconstruction"},
        ),
        WikiPage(
            kind="syntheses",
            slug="pulse",
            title="Project pulse",
            body=(
                "# Project pulse\n\n"
                "- Three new papers landed this week.\n"
                "- One new concept emerged.\n"
            ),
            path=store.path_for("syntheses", "pulse"),
            frontmatter={
                "synthesis_kind": "pulse",
                "generated_at": "2026-04-27T12:00:00Z",
            },
        ),
        WikiPage(
            kind="questions",
            slug="open-q-1",
            title="Why does X work?",
            body="# Why does X work?\n\nAn open question.\n",
            path=store.path_for("questions", "open-q-1"),
            frontmatter={"title": "Why does X work?"},
        ),
    ]
    for page in pages:
        store.write_page(page)


def test_static_site_builder_emits_redesigned_ia(tmp_path: Path) -> None:
    out = tmp_path / "site"
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _seed_wiki(wiki)

    result = StaticSiteBuilder(site_title="Demo Wiki").write_site(_toy_graph(), wiki, out)

    # Same builder is re-exported from llm_wiki.site.
    assert StaticSiteBuilder is NewStaticSiteBuilder

    assert result["site_path"] == str(out)
    assert result["page_count"] >= 12

    # ---- §3.1 routes all exist ------------------------------------------------
    expected_routes = [
        "index.html",
        "sources/index.html",
        "concepts/index.html",
        "entities/index.html",
        "papers/index.html",
        "repos/index.html",
        "topics/index.html",
        "syntheses/index.html",
        "questions/index.html",
        "timeline/index.html",
        "graph/index.html",
        "about.html",
    ]
    for route in expected_routes:
        assert (out / route).exists(), f"missing route: {route}"

    # ---- no code-layer detail pages ------------------------------------------
    nodes_dir = out / "nodes"
    assert not nodes_dir.exists(), "legacy nodes/ directory must not be emitted"
    assert not list(out.rglob("codeclass-*.html")), "no CodeClass HTML pages allowed"
    assert not list(out.rglob("codefunction-*.html")), "no CodeFunction HTML pages allowed"

    # ---- per-page AI siblings for at least one paper -------------------------
    paper_html = out / "papers" / "demo-paper.html"
    paper_txt = out / "papers" / "demo-paper.txt"
    paper_json = out / "papers" / "demo-paper.json"
    assert paper_html.exists()
    assert paper_txt.exists()
    assert paper_json.exists()
    sibling_record = json.loads(paper_json.read_text(encoding="utf-8"))
    assert sibling_record["title"] == "Demo Paper"
    assert sibling_record["kind"] == "papers"
    assert "Demo Paper" in sibling_record.get("body", sibling_record.get("body_text", ""))

    # ---- AI/agent export bundle ----------------------------------------------
    for export_file in (
        "llms.txt",
        "llms-full.txt",
        "graph.json",
        "graph.jsonld",
        "manifest.json",
        "search-index.json",
        "sitemap.xml",
        "rss.xml",
        "robots.txt",
        "assets/style.css",
        "assets/app.js",
    ):
        assert (out / export_file).exists(), f"missing export: {export_file}"

    # ---- search index excludes code-layer entries ----------------------------
    search_entries = json.loads((out / "search-index.json").read_text(encoding="utf-8"))
    titles = {entry["title"] for entry in search_entries}
    assert "Splatter" not in titles
    assert "render" not in titles

    # ---- home page surfaces the hero + activity heatmap ---------------------
    home_html = (out / "index.html").read_text(encoding="utf-8")
    assert 'class="hero"' in home_html
    assert 'class="heatmap"' in home_html

    # ---- manifest is a real inventory ----------------------------------------
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generator"] == "llm_wiki.site.StaticSiteBuilder"
    paths = {entry["path"] for entry in manifest["files"]}
    assert "index.html" in paths
    assert "papers/demo-paper.html" in paths
    assert "graph.json" in paths
    for entry in manifest["files"]:
        assert isinstance(entry["sha256"], str) and len(entry["sha256"]) == 64
        assert isinstance(entry["size"], int) and entry["size"] >= 0


def test_static_site_builder_is_byte_identical_across_runs(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _seed_wiki(wiki)
    out_a = tmp_path / "site-a"
    out_b = tmp_path / "site-b"
    builder = StaticSiteBuilder(site_title="Demo Wiki")
    builder.write_site(_toy_graph(), wiki, out_a)
    builder.write_site(_toy_graph(), wiki, out_b)

    files_a = {p.relative_to(out_a): p.read_bytes() for p in out_a.rglob("*") if p.is_file()}
    files_b = {p.relative_to(out_b): p.read_bytes() for p in out_b.rglob("*") if p.is_file()}

    assert set(files_a.keys()) == set(files_b.keys())
    for relpath, payload in files_a.items():
        assert payload == files_b[relpath], f"byte drift in {relpath}"


def test_static_site_builder_legacy_two_arg_call(tmp_path: Path) -> None:
    """The legacy ``write_site(graph, output_dir)`` shape stays supported."""
    out = tmp_path / "site"
    result = StaticSiteBuilder(site_title="Demo Wiki").write_site(_toy_graph(), out)

    assert result["site_path"] == str(out)
    assert (out / "index.html").exists()
    assert (out / "graph.json").exists()
    assert (out / "search-index.json").exists()
    # Empty wiki layer: index pages still render (with empty-state copy),
    # but no detail pages are emitted.
    assert not list((out / "papers").glob("[!i]*.html"))


def test_static_site_builder_handles_empty_wiki(tmp_path: Path) -> None:
    """A wiki dir with no markdown files renders all index pages safely."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    out = tmp_path / "site"
    StaticSiteBuilder(site_title="Demo Wiki").write_site(_toy_graph(), wiki, out)

    for route in (
        "index.html",
        "sources/index.html",
        "concepts/index.html",
        "papers/index.html",
        "syntheses/index.html",
        "graph/index.html",
        "about.html",
    ):
        assert (out / route).exists(), f"missing route under empty wiki: {route}"

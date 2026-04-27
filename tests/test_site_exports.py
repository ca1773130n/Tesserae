"""Tests for :mod:`llm_wiki.site.exports`."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from llm_wiki.research_graph import (
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNodeType,
)
from llm_wiki.site.exports import (
    ExportContext,
    render_ai_readme,
    render_graph_jsonld,
    render_llms_full_txt,
    render_llms_txt,
    render_robots_txt,
    render_rss_xml,
    render_sitemap_xml,
    write_siblings,
)
from llm_wiki.wiki_store import WikiPage


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def mixed_graph() -> ResearchGraph:
    builder = ResearchGraphBuilder()
    builder.add_node("Gaussian Splatting", ResearchNodeType.METHODOLOGICAL_CONCEPT, description="3DGS rendering.")
    builder.add_node("Karen Koto", ResearchNodeType.PERSON)
    builder.add_node("OpenAI", ResearchNodeType.ORGANIZATION)
    builder.add_node("NeRF", ResearchNodeType.PAPER, description="The neural radiance field paper.")
    builder.add_node("nerfstudio", ResearchNodeType.REPOSITORY, description="A NeRF research framework.")
    builder.add_node("3D Reconstruction", ResearchNodeType.RESEARCH_TOPIC)
    # Excluded
    builder.add_node("MyClass", ResearchNodeType.CODE_CLASS)
    builder.add_node("Claim: foo", ResearchNodeType.CLAIM)
    builder.add_node("Evidence: bar", ResearchNodeType.EVIDENCE_SPAN)
    return builder.build()


@pytest.fixture
def sample_pages(tmp_path: Path) -> dict[str, list[WikiPage]]:
    sources = [
        WikiPage(
            kind="sources",
            slug="sample",
            title="Sample Doc",
            body="# Sample Doc\n\nA short raw source document about diffusion.\n",
            path=tmp_path / "sources" / "sample.md",
            frontmatter={"title": "Sample Doc", "summary": "A sample source document."},
        )
    ]
    syntheses = [
        WikiPage(
            kind="syntheses",
            slug="weekly-2026-w17",
            title="Weekly 2026-W17",
            body="# Weekly 2026-W17\n\nThree papers landed.\n",
            path=tmp_path / "syntheses" / "weekly-2026-w17.md",
            frontmatter={
                "title": "Weekly 2026-W17",
                "summary": "Three papers landed.",
                "synthesis_kind": "weekly",
                "generated_at": "2026-04-27T12:00:00Z",
            },
        ),
        WikiPage(
            kind="syntheses",
            slug="pulse",
            title="Project Pulse",
            body="# Project Pulse\n\nThe project is ticking along.\n",
            path=tmp_path / "syntheses" / "pulse.md",
            frontmatter={"title": "Project Pulse", "synthesis_kind": "pulse"},
        ),
    ]
    return {"sources": sources, "syntheses": syntheses}


@pytest.fixture
def ctx(mixed_graph: ResearchGraph, sample_pages: dict[str, list[WikiPage]]) -> ExportContext:
    routes = [
        ("/", datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)),
        ("/sources/sample.html", datetime(2026, 4, 26, tzinfo=timezone.utc)),
        ("/syntheses/weekly-2026-w17.html", None),
    ]
    return ExportContext(
        site_title="LLM-Wiki Test",
        graph=mixed_graph,
        wiki_pages_by_kind=sample_pages,
        routes=routes,
    )


# ----------------------------------------------------------------- llms.txt


def test_render_llms_txt_contains_site_title_and_pages(ctx: ExportContext):
    text = render_llms_txt(ctx.site_title, ctx)
    assert text.strip()
    assert ctx.site_title in text
    assert "Sample Doc" in text
    assert "Weekly 2026-W17" in text
    # link table groups by kind
    assert "## Sources" in text
    assert "## Syntheses" in text


def test_render_llms_full_txt_contains_full_bodies(ctx: ExportContext):
    text = render_llms_full_txt(ctx.site_title, ctx)
    assert text.strip()
    assert ctx.site_title in text
    assert "# Full content" in text
    # Body content from a wiki page must appear.
    assert "Three papers landed." in text


# --------------------------------------------------------------- graph.jsonld


def test_render_graph_jsonld_parses_and_excludes_code_class(ctx: ExportContext):
    payload = json.loads(render_graph_jsonld(ctx.graph, ctx))
    assert payload["@context"] == "https://schema.org"
    assert payload["@type"] == "Dataset"
    assert "hasPart" in payload
    parts = payload["hasPart"]
    assert isinstance(parts, list)
    assert parts, "expected at least one wiki-layer node"

    titles = {part.get("name") for part in parts}
    assert "MyClass" not in titles
    assert "Claim: foo" not in titles
    assert "Evidence: bar" not in titles
    assert "NeRF" in titles

    # @type mapping spot-check.
    types_by_name = {part["name"]: part["@type"] for part in parts}
    assert types_by_name["NeRF"] == "ScholarlyArticle"
    assert types_by_name["nerfstudio"] == "SoftwareSourceCode"
    assert types_by_name["Gaussian Splatting"] == "DefinedTerm"
    assert types_by_name["Karen Koto"] == "Person"
    assert types_by_name["OpenAI"] == "Organization"

    # additionalType keeps the original ResearchNodeType for fidelity.
    assert any(part.get("additionalType") == "ResearchTopic" for part in parts)


def test_render_graph_jsonld_works_without_explicit_ctx(mixed_graph: ResearchGraph):
    payload = json.loads(render_graph_jsonld(mixed_graph))
    assert payload["@context"] == "https://schema.org"
    assert isinstance(payload["hasPart"], list)


# --------------------------------------------------------------- sitemap.xml


def test_render_sitemap_xml_is_well_formed_and_has_urls(ctx: ExportContext):
    xml = render_sitemap_xml(ctx.routes)
    root = ET.fromstring(xml)
    # urlset is the root, with the sitemaps.org namespace.
    assert root.tag.endswith("urlset")
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    urls = root.findall(f"{ns}url")
    assert len(urls) == len(ctx.routes)
    locs = [el.text for url in urls for el in url.findall(f"{ns}loc")]
    assert "/" in locs
    # last-mod is present where supplied.
    lastmods = [el.text for url in urls for el in url.findall(f"{ns}lastmod")]
    assert any(lastmod and lastmod.startswith("2026-04-27") for lastmod in lastmods)


# --------------------------------------------------------------- rss.xml


def test_render_rss_xml_is_well_formed(ctx: ExportContext):
    xml = render_rss_xml(ctx.site_title, ctx.wiki_pages_by_kind["syntheses"])
    assert '<rss version="2.0">' in xml
    root = ET.fromstring(xml)
    assert root.tag == "rss"
    channel = root.find("channel")
    assert channel is not None
    titles = [el.text for el in channel.findall("item/title")]
    assert "Weekly 2026-W17" in titles
    assert "Project Pulse" in titles


def test_render_rss_xml_caps_at_30_items(ctx: ExportContext, tmp_path: Path):
    many = []
    for i in range(45):
        many.append(
            WikiPage(
                kind="syntheses",
                slug=f"daily-{i:02d}",
                title=f"Daily {i:02d}",
                body=f"# Daily {i:02d}\n\nbody\n",
                path=tmp_path / f"syntheses/daily-{i:02d}.md",
                frontmatter={"title": f"Daily {i:02d}"},
            )
        )
    xml = render_rss_xml(ctx.site_title, many)
    root = ET.fromstring(xml)
    channel = root.find("channel")
    assert channel is not None
    items = channel.findall("item")
    assert len(items) == 30


# --------------------------------------------------------------- robots / readme


def test_render_robots_txt_is_ai_friendly():
    text = render_robots_txt()
    assert "User-agent: *" in text
    assert "Allow: /" in text
    assert "Sitemap: /sitemap.xml" in text


def test_render_ai_readme_describes_the_routes(ctx: ExportContext):
    text = render_ai_readme(ctx.site_title, ctx)
    assert ctx.site_title in text
    assert "llms.txt" in text
    assert "graph.jsonld" in text
    assert "sitemap.xml" in text
    for kind in ("sources", "concepts", "entities", "papers", "repos", "topics", "syntheses", "questions"):
        assert f"/{kind}/" in text


# ------------------------------------------------------------------- siblings


def test_write_siblings_creates_txt_and_json(tmp_path: Path):
    html_path = tmp_path / "concepts" / "diffusion.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text("<html><body>Diffusion</body></html>", encoding="utf-8")

    record = {
        "title": "Diffusion",
        "kind": "concepts",
        "body_text": "Diffusion is a generative method.",
        "source_path": "data/research/foo.md",
        "links": ["concepts/scoring.html", "papers/ddim.html"],
    }
    write_siblings(html_path, record)

    txt_path = html_path.with_suffix(".txt")
    json_path = html_path.with_suffix(".json")
    assert txt_path.exists()
    assert json_path.exists()

    txt = txt_path.read_text(encoding="utf-8")
    assert "Diffusion" in txt
    assert "Diffusion is a generative method." in txt

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    for key in ("title", "kind", "body_text", "source_path", "links"):
        assert key in payload, f"missing {key} in JSON sibling"
    assert payload["title"] == "Diffusion"
    assert payload["kind"] == "concepts"
    assert payload["links"] == ["concepts/scoring.html", "papers/ddim.html"]


def test_write_siblings_creates_parent_dir_when_missing(tmp_path: Path):
    nested = tmp_path / "deep" / "nested" / "foo.html"
    record = {"title": "Foo", "kind": "concepts", "body_text": "Foo body.", "source_path": "", "links": []}
    write_siblings(nested, record)
    assert nested.with_suffix(".txt").exists()
    assert nested.with_suffix(".json").exists()

"""Tests for :mod:`tesserae.site.exports`."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tesserae.research_graph import (
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNodeType,
)
from tesserae.site.exports import (
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
from tesserae.wiki_store import WikiPage


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def mixed_graph() -> ResearchGraph:
    builder = ResearchGraphBuilder()
    builder.add_node("Gaussian Splatting", ResearchNodeType.METHODOLOGICAL_CONCEPT, description="3DGS rendering.")
    builder.add_node("Karen Koto", ResearchNodeType.PERSON)
    builder.add_node("OpenAI", ResearchNodeType.ORGANIZATION)
    paper = builder.add_node(
        "NeRF",
        ResearchNodeType.PAPER,
        description="The neural radiance field paper.",
        metadata={"arxiv_id": "2003.08934"},
    )
    repo = builder.add_node(
        "nerfstudio",
        ResearchNodeType.REPOSITORY,
        description="A NeRF research framework.",
        metadata={
            "github_repo": "nerfstudio-project/nerfstudio",
            "repo_url": "https://github.com/nerfstudio-project/nerfstudio",
            "programming_language": "Python",
        },
    )
    builder.add_node("3D Reconstruction", ResearchNodeType.RESEARCH_TOPIC)
    builder.add_node(
        "weekly-2026-w17",
        ResearchNodeType.SYNTHESIS,
        description="Weekly digest.",
        metadata={"synthesis_kind": "weekly", "input_ids": [paper.id]},
    )
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
        site_title="Tesserae Test",
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
    assert "@graph" in payload
    parts = payload["@graph"]
    assert isinstance(parts, list)
    assert parts, "expected at least one wiki-layer node"

    titles = {part.get("name") for part in parts}
    assert "MyClass" not in titles
    assert "Claim: foo" not in titles
    assert "Evidence: bar" not in titles
    assert "NeRF" in titles

    # Person nodes are private by default (PRIVATE_PUBLIC_RESEARCH_TYPES in
    # research_graph.py) — author names from bibliographic Authors: blocks
    # would otherwise flood the public projection with biblio noise. So a
    # Person like "Karen Koto" must NOT appear in the wiki-layer JSON-LD,
    # even though it stays in graph.json for MCP/Cognee. (Organizations are
    # still public.)
    assert "Karen Koto" not in titles

    # @type mapping spot-check.
    types_by_name = {part["name"]: part["@type"] for part in parts}
    assert types_by_name["NeRF"] == "ScholarlyArticle"
    assert types_by_name["nerfstudio"] == "SoftwareSourceCode"
    assert types_by_name["Gaussian Splatting"] == "DefinedTerm"
    assert types_by_name["OpenAI"] == "Organization"

    # additionalType keeps the original ResearchNodeType for fidelity.
    assert any(part.get("additionalType") == "ResearchTopic" for part in parts)

    # Every entry has a fully qualified @id.
    for part in parts:
        assert isinstance(part.get("@id"), str)
        assert part["@id"].startswith("#")

    # Top-level Dataset metadata.
    assert payload["name"] == ctx.site_title
    assert payload["creator"] == {"@type": "Organization", "name": "Tesserae"}


def test_render_graph_jsonld_inflates_paper_repo_synthesis(ctx: ExportContext):
    payload = json.loads(render_graph_jsonld(ctx.graph, ctx))
    by_name = {p["name"]: p for p in payload["@graph"]}

    # Paper: identifier + sameAs to arxiv.
    nerf = by_name["NeRF"]
    assert nerf["identifier"] == "2003.08934"
    assert any(
        isinstance(s, str) and "arxiv.org/abs/2003.08934" in s for s in nerf["sameAs"]
    )
    assert nerf.get("datePublished") == "2020-03"
    assert nerf.get("headline") == "NeRF"

    # Repository: codeRepository + programmingLanguage.
    repo = by_name["nerfstudio"]
    assert repo["codeRepository"] == "https://github.com/nerfstudio-project/nerfstudio"
    assert repo["programmingLanguage"] == "Python"

    # DefinedTerm carries termCode + inDefinedTermSet.
    gs = by_name["Gaussian Splatting"]
    assert gs["termCode"] == "gaussian-splatting"
    assert gs["inDefinedTermSet"] == "concepts/index.html"

    # Synthesis carries articleSection + mentions array.
    synth = next(p for p in payload["@graph"] if p["@type"] == "Article")
    assert synth["articleSection"] == "weekly"
    assert isinstance(synth["mentions"], list)
    assert synth["mentions"], "expected synthesis to cite the NeRF paper"
    assert all("@id" in m for m in synth["mentions"])


def test_render_graph_jsonld_works_without_explicit_ctx(mixed_graph: ResearchGraph):
    payload = json.loads(render_graph_jsonld(mixed_graph))
    assert payload["@context"] == "https://schema.org"
    assert isinstance(payload["@graph"], list)


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


_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_VALID_CHANGEFREQ = {"always", "hourly", "daily", "weekly", "monthly", "yearly", "never"}


def test_render_sitemap_xml_every_url_has_changefreq_and_priority():
    routes = [
        ("index.html", None),
        ("about.html", None),
        ("timeline/index.html", None),
        ("timeline/2026-04-27.html", None),
        ("papers/index.html", None),
        ("papers/nerf.html", None),
        ("repos/index.html", None),
        ("repos/nerfstudio.html", None),
        ("concepts/index.html", None),
        ("concepts/diffusion.html", None),
        ("entities/openai.html", None),
        ("topics/3d-reconstruction.html", None),
        ("syntheses/weekly-2026-w17.html", None),
        ("questions/q-1.html", None),
        ("sources/sample.html", None),
        ("raw/foo.html", None),
    ]
    xml = render_sitemap_xml(routes)
    root = ET.fromstring(xml)
    urls = root.findall(f"{_SITEMAP_NS}url")
    assert len(urls) == len(routes)

    cf_by_loc: dict[str, str] = {}
    pri_by_loc: dict[str, float] = {}
    for url in urls:
        loc = url.find(f"{_SITEMAP_NS}loc").text
        cf = url.find(f"{_SITEMAP_NS}changefreq")
        pri = url.find(f"{_SITEMAP_NS}priority")
        last = url.find(f"{_SITEMAP_NS}lastmod")
        assert cf is not None and cf.text in _VALID_CHANGEFREQ, f"bad changefreq for {loc}"
        assert pri is not None
        priority_value = float(pri.text)
        assert 0.0 <= priority_value <= 1.0
        assert last is not None and last.text
        cf_by_loc[loc] = cf.text
        pri_by_loc[loc] = priority_value

    # Family-specific spot checks.
    assert cf_by_loc["index.html"] == "daily"
    assert pri_by_loc["index.html"] == 1.0
    assert cf_by_loc["about.html"] == "monthly"
    assert pri_by_loc["about.html"] == 0.3
    assert cf_by_loc["papers/index.html"] == "daily"
    assert pri_by_loc["papers/index.html"] == 0.9
    assert cf_by_loc["papers/nerf.html"] == "weekly"
    assert pri_by_loc["papers/nerf.html"] == 0.8
    assert cf_by_loc["repos/nerfstudio.html"] == "weekly"
    assert pri_by_loc["repos/nerfstudio.html"] == 0.8
    assert cf_by_loc["concepts/diffusion.html"] == "weekly"
    assert pri_by_loc["concepts/diffusion.html"] == 0.6
    assert cf_by_loc["raw/foo.html"] == "monthly"
    assert pri_by_loc["raw/foo.html"] == 0.4


def test_render_sitemap_xml_lastmod_fallback_is_deterministic():
    routes = [("papers/nerf.html", None), ("concepts/diffusion.html", None)]
    a = render_sitemap_xml(routes)
    b = render_sitemap_xml(routes)
    assert a == b


# --------------------------------------------------------------- rss.xml


def test_render_rss_xml_is_well_formed(ctx: ExportContext):
    xml = render_rss_xml(ctx.site_title, ctx.wiki_pages_by_kind["syntheses"])
    assert '<rss version="2.0"' in xml
    root = ET.fromstring(xml)
    assert root.tag == "rss"
    channel = root.find("channel")
    assert channel is not None
    titles = [el.text for el in channel.findall("item/title")]
    assert "Weekly 2026-W17" in titles
    assert "Project Pulse" in titles


def test_render_rss_xml_has_full_metadata(ctx: ExportContext):
    history = [
        {
            "slug": "weekly-2026-w17",
            "content_hash": "deadbeef",
            "generated_at": "2026-04-27T12:00:00Z",
            "generator": "llm-claude-sonnet-4-6",
        }
    ]
    xml = render_rss_xml(
        ctx.site_title,
        ctx.wiki_pages_by_kind["syntheses"],
        history,
        ctx=ctx,
    )
    # Namespaces declared on <rss>.
    assert 'xmlns:dc="http://purl.org/dc/elements/1.1/"' in xml
    assert 'xmlns:atom="http://www.w3.org/2005/Atom"' in xml
    # Channel-level metadata.
    assert "<generator>tesserae</generator>" in xml
    assert "<language>" in xml
    assert "<copyright>" in xml
    assert "atom:link" in xml
    # Item-level metadata: CDATA description, guid, dc:creator, category.
    root = ET.fromstring(xml)
    channel = root.find("channel")
    assert channel is not None
    first_item = channel.find("item")
    assert first_item is not None
    desc = first_item.find("description")
    assert desc is not None
    # The CDATA wrapping is stripped on parse — just confirm content rendered.
    assert desc.text and desc.text.strip()
    # Raw text should still contain CDATA markers in the serialized output.
    assert "<![CDATA[" in xml
    guid = first_item.find("guid")
    assert guid is not None
    assert guid.attrib.get("isPermaLink") == "false"
    # dc:creator under DC namespace.
    creator = first_item.find("{http://purl.org/dc/elements/1.1/}creator")
    assert creator is not None
    # First item is the LLM-generated one (newer in the history ledger),
    # so its creator should be the model id.
    assert creator.text == "llm-claude-sonnet-4-6"
    category = first_item.find("category")
    assert category is not None
    assert category.text in {"weekly", "pulse"}


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


# ----------------------------------------------------------------- idempotence


def test_export_artifacts_are_byte_idempotent(ctx: ExportContext):
    """RSS / sitemap / JSON-LD must be byte-stable across two consecutive runs."""
    history = [
        {
            "slug": "weekly-2026-w17",
            "content_hash": "deadbeef",
            "generated_at": "2026-04-27T12:00:00Z",
            "generator": "llm-claude-sonnet-4-6",
        }
    ]
    rss_a = render_rss_xml(ctx.site_title, ctx.wiki_pages_by_kind["syntheses"], history, ctx=ctx)
    rss_b = render_rss_xml(ctx.site_title, ctx.wiki_pages_by_kind["syntheses"], history, ctx=ctx)
    sm_a = render_sitemap_xml(ctx.routes)
    sm_b = render_sitemap_xml(ctx.routes)
    jl_a = render_graph_jsonld(ctx.graph, ctx)
    jl_b = render_graph_jsonld(ctx.graph, ctx)
    assert rss_a == rss_b
    assert sm_a == sm_b
    assert jl_a == jl_b

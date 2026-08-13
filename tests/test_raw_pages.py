"""Tests for the raw-document viewer (``raw/<safe>.html`` route)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

import pytest

from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.site import StaticSiteBuilder
from tesserae.site.raw_view import (
    artifact_asset_name,
    copy_artifact_asset,
    raw_href,
    relativize_source_path,
    render_raw_view,
    safe_raw_slug,
)
from tesserae.wiki_store import WikiPage, WikiPageStore


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_project(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Lay out a fake project with one paper.md + one .json source.

    Returns ``(project_root, wiki_root, site_root, paper_md_path)``.
    """
    project = tmp_path / "myproject"
    data = project / "data" / "research" / "daily" / "2026-04-25" / "papers" / "2603.24725"
    data.mkdir(parents=True)
    paper_md = data / "paper.md"
    paper_md.write_text(
        "# Confidence-Based Mesh Extraction\n\n"
        "## Method\n\n"
        "We use a Gaussian splat with a [link](https://example.com).\n\n"
        "- Bullet one\n"
        "- Bullet two\n",
        encoding="utf-8",
    )
    data_json = data / "metadata.json"
    data_json.write_text('{"key": "value", "nested": {"a": 1}}', encoding="utf-8")

    wiki_root = project / ".tesserae" / "wiki"
    wiki_root.mkdir(parents=True)
    site_root = project / ".tesserae" / "site"
    return project, wiki_root, site_root, paper_md


# ---------------------------------------------------------------------------
# unit helpers
# ---------------------------------------------------------------------------


def test_relativize_source_path_strips_project_root(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    abs_path = str(project / "data" / "x.md")
    out = relativize_source_path(abs_path, project_root=project)
    assert out == "data/x.md"


def test_relativize_source_path_passes_through_relative() -> None:
    out = relativize_source_path("data/research/foo.md", project_root=Path("/tmp/p"))
    assert out == "data/research/foo.md"


def test_relativize_source_path_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    once = relativize_source_path(str(project / "data/x.md"), project_root=project)
    twice = relativize_source_path(once, project_root=project)
    assert once == twice


def test_relativize_source_path_handles_empty_and_none() -> None:
    assert relativize_source_path("", project_root=Path("/tmp")) == ""
    assert relativize_source_path(None, project_root=Path("/tmp")) == ""


def test_safe_raw_slug_only_contains_url_safe_chars() -> None:
    slug = safe_raw_slug("data/research/daily/2026-04-25/papers/2603.24725/paper.md")
    assert slug == "data-research-daily-2026-04-25-papers-2603-24725-paper-md"
    assert re.fullmatch(r"[a-z0-9\-]+", slug)


def test_raw_href_returns_none_for_missing_file(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    out = raw_href(project, "data/missing.md", depth=1)
    assert out is None


def test_raw_href_returns_relative_url_for_existing_file(tmp_path: Path) -> None:
    project, _wiki, _site, paper_md = _seed_project(tmp_path)
    href = raw_href(project, str(paper_md), depth=1)
    assert href is not None
    assert href.startswith("../raw/")
    assert href.endswith(".html")


def test_raw_href_strips_absolute_prefix(tmp_path: Path) -> None:
    project, _wiki, _site, paper_md = _seed_project(tmp_path)
    href = raw_href(project, str(paper_md), depth=1)
    # Slug encodes the project-relative path (no absolute machine path).
    assert "myproject" not in (href or "")


# ---------------------------------------------------------------------------
# render_raw_view: text / data
# ---------------------------------------------------------------------------


def test_render_raw_view_markdown_emits_real_headings(tmp_path: Path) -> None:
    project, _wiki, _site, paper_md = _seed_project(tmp_path)
    rel = "data/research/daily/2026-04-25/papers/2603.24725/paper.md"
    out = render_raw_view(
        site_title="Tesserae",
        project_relative_path=rel,
        absolute_path=paper_md,
    )
    assert "<h1" in out and ">Confidence-Based Mesh Extraction</h1>" in out
    assert "<h2" in out and ">Method</h2>" in out
    assert "<ul>" in out and "<li>Bullet one</li>" in out
    # Literal markdown must not leak through.
    assert "\n# " not in out
    assert "\n## " not in out


def test_render_raw_view_markdown_preserves_readme_html_and_admonitions(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    readme = project / "README.md"
    readme.write_text(
        '<h1 align="center">Tesserae</h1>\n\n'
        '<p align="center">\n'
        '  <strong>Turn docs into a graph.</strong>\n'
        '  <br />\n'
        '  <em>Local-first.</em>\n'
        '</p>\n\n'
        '<p align="center"><img src="docs/assets/wiki-graph-screenshot.png" alt="Graph" /></p>\n\n'
        '> [!TIP]\n'
        '> **Compile first.** Then run `build-site`.\n',
        encoding="utf-8",
    )
    asset = project / "docs" / "assets" / "wiki-graph-screenshot.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"fake png bytes")

    out = render_raw_view(
        site_title="Tesserae",
        project_relative_path="README.md",
        absolute_path=readme,
    )

    assert '<h1 align="center">Tesserae</h1>' in out
    assert '<p align="center">' in out
    assert '<strong>Turn docs into a graph.</strong>' in out
    assert '../raw-assets/docs-assets-wiki-graph-screenshot-png.png' in out
    assert 'src="docs/assets/wiki-graph-screenshot.png"' not in out
    assert '&lt;h1' not in out
    assert '&lt;p' not in out
    assert '<div class="admonition admonition-tip">' in out
    assert '<p class="admonition-title">Tip</p>' in out
    assert '[!TIP]' not in out


def test_render_raw_view_json_emits_pretty_printed_pre(tmp_path: Path) -> None:
    project, _wiki, _site, _paper = _seed_project(tmp_path)
    json_path = project / "data" / "research" / "daily" / "2026-04-25" / "papers" / "2603.24725" / "metadata.json"
    out = render_raw_view(
        site_title="Tesserae",
        project_relative_path="data/.../metadata.json",
        absolute_path=json_path,
    )
    assert "<pre" in out and 'class="language-json"' in out
    # Pretty-printed JSON inside escaped <pre><code>: quote chars get
    # html-escaped to ``&quot;`` but the field labels are still discoverable.
    assert "&quot;key&quot;: &quot;value&quot;" in out
    assert "&quot;nested&quot;" in out


# ---------------------------------------------------------------------------
# end-to-end through StaticSiteBuilder
# ---------------------------------------------------------------------------


def _toy_graph_with_real_source(paper_md: Path) -> ResearchGraph:
    return ResearchGraph(
        nodes=[
            ResearchNode(
                id="Paper:demo",
                name="Confidence-Based Mesh Extraction",
                type=ResearchNodeType.PAPER,
                description="Demo paper.",
                source_path=str(paper_md),
            ),
            ResearchNode(
                id="Concept:gs",
                name="Gaussian Splatting",
                type=ResearchNodeType.CONCEPT,
                source_path=str(paper_md),
            ),
        ],
        edges=[
            ResearchEdge(
                source="Paper:demo", target="Concept:gs", type="mentioned_in"
            )
        ],
    )


def _seed_wiki_for_paper(wiki_root: Path) -> None:
    store = WikiPageStore(wiki_root)
    page = WikiPage(
        kind="papers",
        slug="confidence-based-mesh-extraction",
        title="Confidence-Based Mesh Extraction",
        body="Abstract excerpt.\n",
        path=store.path_for("papers", "confidence-based-mesh-extraction"),
        frontmatter={
            "node_id": "Paper:demo",
            "title": "Confidence-Based Mesh Extraction",
            "node_type": "Paper",
        },
    )
    store.write_page(page)
    concept_page = WikiPage(
        kind="concepts",
        slug="gaussian-splatting",
        title="Gaussian Splatting",
        body="A 3D scene representation.\n",
        path=store.path_for("concepts", "gaussian-splatting"),
        frontmatter={
            "node_id": "Concept:gs",
            "title": "Gaussian Splatting",
            "node_type": "Concept",
        },
    )
    store.write_page(concept_page)


def test_paper_detail_has_one_source_line_and_relative_path(tmp_path: Path) -> None:
    project, wiki_root, site_root, paper_md = _seed_project(tmp_path)
    _seed_wiki_for_paper(wiki_root)
    StaticSiteBuilder().write_site(_toy_graph_with_real_source(paper_md), wiki_root, site_root)
    paper_html = (site_root / "papers" / "confidence-based-mesh-extraction.html").read_text(encoding="utf-8")

    # Exactly one "Source:" label in the metadata bar (Issue 1.b).
    assert paper_html.count("<b>Source:</b>") == 1
    # The path on display is project-relative, never absolute.
    assert "data/research/daily/2026-04-25/papers/2603.24725/paper.md" in paper_html
    assert str(project) not in paper_html


def test_paper_detail_source_line_links_to_raw_page(tmp_path: Path) -> None:
    project, wiki_root, site_root, paper_md = _seed_project(tmp_path)
    _seed_wiki_for_paper(wiki_root)
    StaticSiteBuilder().write_site(_toy_graph_with_real_source(paper_md), wiki_root, site_root)
    paper_html = (site_root / "papers" / "confidence-based-mesh-extraction.html").read_text(encoding="utf-8")

    # The Source: line wraps the project-relative path in a link to /raw/.
    match = re.search(
        r'<span class="meta-source"><b>Source:</b> <a href="([^"]+)"',
        paper_html,
    )
    assert match, "Source: line must be a clickable link"
    href = match.group(1)
    assert href.startswith("../raw/")
    assert href.endswith(".html")

    # And the raw page actually exists under the site output.
    rel = href.lstrip("./").lstrip("/")
    target = site_root / "papers" / href.replace("..", "").lstrip("/") if href.startswith("..") else site_root / rel
    # Resolve the relative href against the page location.
    target = (site_root / "papers" / href).resolve()
    assert target.exists(), f"raw page not emitted: {target}"


def test_bundled_corpus_emits_n_raw_pages(tmp_path: Path) -> None:
    """N raw pages == count of unique source files referenced by the graph."""
    project, wiki_root, site_root, paper_md = _seed_project(tmp_path)
    _seed_wiki_for_paper(wiki_root)
    StaticSiteBuilder().write_site(_toy_graph_with_real_source(paper_md), wiki_root, site_root)

    raw_dir = site_root / "raw"
    raw_pages = sorted(raw_dir.glob("*.html"))
    # Exactly one unique source path in the toy graph.
    assert len(raw_pages) == 1


def test_builder_copies_raw_markdown_embedded_image_assets(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    wiki_root = project / ".tesserae" / "wiki"
    wiki_root.mkdir(parents=True)
    site_root = project / ".tesserae" / "site"
    readme = project / "README.md"
    asset = project / "docs" / "assets" / "demo.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"fake png bytes")
    linked_doc = project / "README.ko.md"
    linked_doc.write_text("# 한국어 README\n\n- 하나\n", encoding="utf-8")
    readme.write_text(
        '<h1 align="center">Demo</h1>\n\n'
        '<p align="center"><a href="./README.ko.md">한국어</a></p>\n\n'
        '<p align="center"><img src="docs/assets/demo.png" alt="Demo graph" /></p>\n',
        encoding="utf-8",
    )
    graph = ResearchGraph(
        nodes=[
            ResearchNode(
                id="Concept:readme",
                name="README",
                type=ResearchNodeType.CONCEPT,
                source_path=str(readme),
            )
        ],
        edges=[],
    )

    StaticSiteBuilder().write_site(graph, wiki_root, site_root)

    raw = (site_root / "raw" / "readme-md.html").read_text(encoding="utf-8")
    assert '../raw-assets/docs-assets-demo-png.png' in raw
    assert '../raw/readme-ko-md.html' in raw
    assert 'src="docs/assets/demo.png"' not in raw
    assert 'href="./README.ko.md"' not in raw
    assert (site_root / "raw-assets" / "docs-assets-demo-png.png").exists()
    localized_raw = site_root / "raw" / "readme-ko-md.html"
    assert localized_raw.exists()
    localized = localized_raw.read_text(encoding="utf-8")
    assert '<section class="markdown-body raw-markdown">' in localized
    assert "<li>하나</li>" in localized
    assert "raw-text" not in localized


# ---------------------------------------------------------------------------
# index page subtype chips + data-type rows
# ---------------------------------------------------------------------------


def test_concepts_index_has_subtype_chip_strip(tmp_path: Path) -> None:
    """The chip strip on /concepts/ must surface every concept-layer subtype."""
    project, wiki_root, site_root, paper_md = _seed_project(tmp_path)
    # Add several concept-layer node_types to the wiki.
    store = WikiPageStore(wiki_root)
    for slug, title, node_type in [
        ("gaussian-splatting", "Gaussian Splatting", "Concept"),
        ("nerf", "NeRF", "TechnicalTerm"),
        ("adam", "Adam", "Algorithm"),
    ]:
        page = WikiPage(
            kind="concepts",
            slug=slug,
            title=title,
            body=f"# {title}\n\nDef.\n",
            path=store.path_for("concepts", slug),
            frontmatter={"title": title, "node_type": node_type, "kind": "concepts"},
        )
        store.write_page(page)
    StaticSiteBuilder().write_site(_toy_graph_with_real_source(paper_md), wiki_root, site_root)

    out = (site_root / "concepts" / "index.html").read_text(encoding="utf-8")

    assert "subtype-chips" in out
    # The chip strip carries an ``All`` chip plus one per subtype.
    assert 'data-filter-type=""' in out, "chip strip must include All chip"
    for subtype in ("Concept", "TechnicalTerm", "Algorithm"):
        assert f'data-filter-type="{subtype}"' in out, (
            f"chip strip must include {subtype}"
        )


def test_concepts_index_table_rows_carry_data_type(tmp_path: Path) -> None:
    project, wiki_root, site_root, paper_md = _seed_project(tmp_path)
    store = WikiPageStore(wiki_root)
    for slug, title, node_type in [
        ("gaussian-splatting", "Gaussian Splatting", "Concept"),
        ("nerf", "NeRF", "TechnicalTerm"),
    ]:
        page = WikiPage(
            kind="concepts",
            slug=slug,
            title=title,
            body=f"# {title}\n\nDef.\n",
            path=store.path_for("concepts", slug),
            frontmatter={"title": title, "node_type": node_type, "kind": "concepts"},
        )
        store.write_page(page)
    StaticSiteBuilder().write_site(_toy_graph_with_real_source(paper_md), wiki_root, site_root)

    out = (site_root / "concepts" / "index.html").read_text(encoding="utf-8")

    # Every rendered row must carry a ``data-type`` attribute.
    for row_match in re.finditer(r"<tr ([^>]*)>", out):
        attrs = row_match.group(1)
        # Skip the header row (it has no attrs).
        if "data-type=" in attrs or "data-row-header" in attrs:
            continue
    # At least one row carries each subtype we seeded.
    assert 'data-type="Concept"' in out
    assert 'data-type="TechnicalTerm"' in out


# ---------------------------------------------------------------------------
# every raw href on a detail page resolves under site root
# ---------------------------------------------------------------------------


def test_every_raw_link_on_detail_pages_resolves(tmp_path: Path) -> None:
    project, wiki_root, site_root, paper_md = _seed_project(tmp_path)
    _seed_wiki_for_paper(wiki_root)
    StaticSiteBuilder().write_site(_toy_graph_with_real_source(paper_md), wiki_root, site_root)

    issues: list[str] = []
    for html_path in sorted(site_root.rglob("*.html")):
        text = html_path.read_text(encoding="utf-8")
        for match in re.finditer(r'href="([^"]*raw/[^"]+\.html)"', text):
            href = match.group(1)
            if href.startswith(("http://", "https://")):
                continue
            target = (html_path.parent / href.split("#", 1)[0].split("?", 1)[0]).resolve()
            if not target.exists():
                issues.append(
                    f"{html_path.relative_to(site_root)} -> {href} missing"
                )
    assert not issues, "broken raw hrefs:\n" + "\n".join(issues)


# ---------------------------------------------------------------------------
# raw markdown rewrites curated cross-page refs onto canonical analysis URLs
# ---------------------------------------------------------------------------


def _seed_digest_corpus(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Lay out a tiny corpus: one digest with cross-page refs, one repo md.

    The digest is a curated weekly summary that uses the same shorthand the
    real ``data/research/weekly/.../digest.md`` files do:

        [GitHub 분석](papers/2509.23563/repo.md)
        [분석](repos/OpenDriveLab_WorldEngine.md)

    Neither link points at a file that exists on disk — they point at the
    canonical analysis pages the site emits at ``repos/<slug>.html``.
    """
    project = tmp_path / "myproject"
    digest_dir = project / "data" / "research" / "weekly" / "2026-W17"
    digest_dir.mkdir(parents=True)
    digest_md = digest_dir / "digest.md"
    digest_md.write_text(
        "# Weekly digest\n\n"
        "- 코드: [GitHub 분석](papers/2509.23563/repo.md) | repo\n"
        "- WorldEngine: [분석](repos/OpenDriveLab_WorldEngine.md)\n",
        encoding="utf-8",
    )

    repo_dir = project / "data" / "research" / "daily" / "2026-04-23" / "repos"
    repo_dir.mkdir(parents=True)
    repo_md = repo_dir / "facebookresearch_map-anything.md"
    repo_md.write_text("# GitHub: facebookresearch/map-anything\n", encoding="utf-8")
    repo2_md = (
        project
        / "data"
        / "research"
        / "daily"
        / "2026-04-13"
        / "repos"
        / "OpenDriveLab_WorldEngine.md"
    )
    repo2_md.parent.mkdir(parents=True)
    repo2_md.write_text("# GitHub: OpenDriveLab/WorldEngine\n", encoding="utf-8")

    wiki_root = project / ".tesserae" / "wiki"
    wiki_root.mkdir(parents=True)
    site_root = project / ".tesserae" / "site"
    return project, wiki_root, site_root, digest_md


def _digest_graph(digest_md: Path, repo_md: Path, repo2_md: Path) -> ResearchGraph:
    return ResearchGraph(
        nodes=[
            ResearchNode(
                id="Source:digest",
                name="Weekly digest 2026-W17",
                type=ResearchNodeType.SOURCE_DOCUMENT,
                source_path=str(digest_md),
            ),
            ResearchNode(
                id="Repository:map-anything",
                name="GitHub 분석: facebookresearch/map-anything",
                type=ResearchNodeType.REPOSITORY,
                source_path=str(repo_md),
                metadata={
                    "arxiv_id": "2509.23563",
                    "github_repo": "facebookresearch/map-anything",
                },
            ),
            ResearchNode(
                id="Repository:worldengine",
                name="GitHub 분석: OpenDriveLab/WorldEngine",
                type=ResearchNodeType.REPOSITORY,
                source_path=str(repo2_md),
                metadata={"github_repo": "opendrivelab/worldengine"},
            ),
        ],
        edges=[],
    )


def _seed_wiki_for_repos(wiki_root: Path) -> None:
    """Seed wiki pages so the StaticSiteBuilder emits ``repos/<slug>.html``."""
    store = WikiPageStore(wiki_root)
    pages = [
        (
            "github-분석-facebookresearch-map-anything",
            "GitHub 분석: facebookresearch/map-anything",
            "Repository:map-anything",
        ),
        (
            "github-분석-opendrivelab-worldengine",
            "GitHub 분석: OpenDriveLab/WorldEngine",
            "Repository:worldengine",
        ),
    ]
    for slug, title, node_id in pages:
        page = WikiPage(
            kind="repos",
            slug=slug,
            title=title,
            body="Repo analysis stub.\n",
            path=store.path_for("repos", slug),
            frontmatter={"node_id": node_id, "title": title, "node_type": "Repository"},
        )
        store.write_page(page)


def test_raw_markdown_rewrites_papers_arxiv_repo_md_to_repo_page(
    tmp_path: Path,
) -> None:
    """``papers/<arxiv>/repo.md`` in a raw markdown body resolves to the
    canonical ``../repos/<slug>.html`` analysis page, not a 404."""
    _project, wiki_root, site_root, digest_md = _seed_digest_corpus(tmp_path)
    repo_md = digest_md.parent.parent.parent / "daily" / "2026-04-23" / "repos" / "facebookresearch_map-anything.md"
    repo2_md = digest_md.parent.parent.parent / "daily" / "2026-04-13" / "repos" / "OpenDriveLab_WorldEngine.md"
    _seed_wiki_for_repos(wiki_root)
    graph = _digest_graph(digest_md, repo_md, repo2_md)
    StaticSiteBuilder().write_site(graph, wiki_root, site_root)

    raw_html = next(
        p for p in (site_root / "raw").glob("*.html") if "digest" in p.name
    ).read_text(encoding="utf-8")

    # The arxiv-shorthand link redirects onto the repo analysis page.
    expected_repo_slug = "github-분석-facebookresearch-map-anything"
    assert f'href="../repos/{expected_repo_slug}.html"' in raw_html, raw_html
    # And the rendered page actually exists at that path — i.e. the rewrite
    # points at a real file the build wrote, not a 404.
    assert (site_root / "repos" / f"{expected_repo_slug}.html").exists()


def test_raw_markdown_rewrites_repos_owner_repo_md_to_repo_page(
    tmp_path: Path,
) -> None:
    """``repos/<owner>_<repo>.md`` shorthand resolves to ``../repos/<slug>.html``."""
    _project, wiki_root, site_root, digest_md = _seed_digest_corpus(tmp_path)
    repo_md = digest_md.parent.parent.parent / "daily" / "2026-04-23" / "repos" / "facebookresearch_map-anything.md"
    repo2_md = digest_md.parent.parent.parent / "daily" / "2026-04-13" / "repos" / "OpenDriveLab_WorldEngine.md"
    _seed_wiki_for_repos(wiki_root)
    graph = _digest_graph(digest_md, repo_md, repo2_md)
    StaticSiteBuilder().write_site(graph, wiki_root, site_root)

    raw_html = next(
        p for p in (site_root / "raw").glob("*.html") if "digest" in p.name
    ).read_text(encoding="utf-8")

    expected_slug = "github-분석-opendrivelab-worldengine"
    assert f'href="../repos/{expected_slug}.html"' in raw_html, raw_html
    assert (site_root / "repos" / f"{expected_slug}.html").exists()


# ---------------------------------------------------------------------------
# content-addressed artifact assets
# ---------------------------------------------------------------------------


def _artifact_source(root: Path, rel: str, payload: bytes) -> tuple[Path, str]:
    """Write ``payload`` at ``root/rel`` and return it with its sha256."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def test_copy_artifact_asset_names_by_content_not_path(tmp_path: Path) -> None:
    """The filename is a function of the bytes alone: the same figure reached
    through two parsed_dirs is one asset, and different bytes never collide."""
    assets = tmp_path / "raw-assets"
    same_a, digest = _artifact_source(tmp_path, "parsed/aaa/p1.png", b"\x89PNG-figure")
    same_b, digest_b = _artifact_source(tmp_path, "parsed/bbb/p7.png", b"\x89PNG-figure")
    other, other_digest = _artifact_source(tmp_path, "parsed/ccc/p1.png", b"\x89PNG-other")

    name_a = copy_artifact_asset(same_a, digest, assets)
    name_b = copy_artifact_asset(same_b, digest_b, assets)
    name_other = copy_artifact_asset(other, other_digest, assets)

    assert name_a == name_b == f"{digest[:16]}.png"
    assert name_other == f"{other_digest[:16]}.png"
    assert name_other != name_a
    assert (assets / name_a).read_bytes() == b"\x89PNG-figure"
    assert sorted(p.name for p in assets.iterdir()) == sorted({name_a, name_other})


def test_copy_artifact_asset_ignores_source_mtime(tmp_path: Path) -> None:
    """mtime never reaches the name or the decision to copy — ``git checkout``
    and friends preserve it across a content change, so it lies in both
    directions."""
    src, digest = _artifact_source(tmp_path, "parsed/aaa/p1.png", b"\x89PNG-figure")
    first = copy_artifact_asset(src, digest, tmp_path / "run-a")

    os.utime(src, (1_000_000_000, 1_000_000_000))
    second = copy_artifact_asset(src, digest, tmp_path / "run-b")

    assert first == second
    assert (tmp_path / "run-a" / first).read_bytes() == (
        tmp_path / "run-b" / second
    ).read_bytes()


def test_copy_artifact_asset_does_not_rewrite_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A name that IS the content makes existence the whole freshness test."""
    assets = tmp_path / "raw-assets"
    src, digest = _artifact_source(tmp_path, "parsed/aaa/p1.png", b"\x89PNG-figure")
    assert copy_artifact_asset(src, digest, assets) == f"{digest[:16]}.png"

    writes: list[Path] = []
    real_write_bytes = Path.write_bytes

    def _counting_write_bytes(self: Path, data: bytes) -> int:
        writes.append(self)
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", _counting_write_bytes)
    again = copy_artifact_asset(src, digest, assets)

    assert again == f"{digest[:16]}.png"
    assert writes == []


def test_prefix_collision_falls_back_to_full_hash_and_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Two different figures declaring the same hash must never resolve to the
    same served file — serving the wrong figure as evidence is the worst
    outcome this system has."""
    assets = tmp_path / "raw-assets"
    digest = "a" * 64
    first_src = tmp_path / "parsed" / "aaa" / "p1.png"
    first_src.parent.mkdir(parents=True)
    first_src.write_bytes(b"\x89PNG-short")
    second_src = tmp_path / "parsed" / "bbb" / "p1.png"
    second_src.parent.mkdir(parents=True)
    second_src.write_bytes(b"\x89PNG-a-considerably-longer-payload")

    assert copy_artifact_asset(first_src, digest, assets) == f"{digest[:16]}.png"
    with caplog.at_level(logging.ERROR, logger="tesserae.site.raw_view"):
        collided = copy_artifact_asset(second_src, digest, assets)

    assert collided == f"{digest}.png"
    assert (assets / collided).read_bytes() == b"\x89PNG-a-considerably-longer-payload"
    assert (assets / f"{digest[:16]}.png").read_bytes() == b"\x89PNG-short"
    assert any("collision" in r.message for r in caplog.records)


def test_artifact_asset_name_rejects_malformed_hash(tmp_path: Path) -> None:
    """A garbage filename would be worse than no asset: the declared hash is
    the artifact's identity, so a bad one means the node is wrong."""
    with pytest.raises(ValueError, match="hex"):
        artifact_asset_name("deadbeef", ".png")          # too short
    with pytest.raises(ValueError, match="hex"):
        artifact_asset_name("z" * 64, ".png")            # not hex
    with pytest.raises(ValueError, match="hex"):
        artifact_asset_name("", ".png")

    assets = tmp_path / "raw-assets"
    src = tmp_path / "parsed" / "aaa" / "p1.png"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\x89PNG-figure")
    assert copy_artifact_asset(src, "not-a-hash", assets) is None
    assert not assets.exists()


def _artifact_graph(asset_rel: str | None, content_hash: str, *, kind: str = "image") -> ResearchGraph:
    """One Artifact node shaped exactly as ``raganything_adapter`` mints it."""
    metadata: dict = {"parser": "raganything", "kind": kind, "content_hash": content_hash}
    if asset_rel is not None:
        metadata["asset_path"] = asset_rel
    return ResearchGraph(
        nodes=[
            ResearchNode(
                id=f"Artifact:{content_hash[:12]}",
                name="Figure: Pipeline",
                type=ResearchNodeType.ARTIFACT,
                description="Pipeline",
                # An Artifact's source_path is its OWNING DOCUMENT, which is
                # why the asset needs an inlet of its own.
                source_path=None,
                metadata=metadata,
            )
        ],
        edges=[],
    )


def test_builder_serves_artifact_asset_bytes_content_addressed(tmp_path: Path) -> None:
    """The graph asserts these bytes exist; the site must actually carry them,
    under a name derived from the hash the graph already declared."""
    project, wiki_root, site_root, _paper = _seed_project(tmp_path)
    asset_rel = ".tesserae/external/raganything/parsed/deadbeef/x.png"
    _src, digest = _artifact_source(project, asset_rel, b"\x89PNG\r\n\x1a\npipeline")

    StaticSiteBuilder().write_site(_artifact_graph(asset_rel, digest), wiki_root, site_root)

    asset_name = f"{digest[:16]}.png"
    assert (site_root / "raw-assets" / asset_name).read_bytes() == b"\x89PNG\r\n\x1a\npipeline"
    raw_page = site_root / "raw" / f"{safe_raw_slug(asset_rel)}.html"
    assert f'../raw-assets/{asset_name}' in raw_page.read_text(encoding="utf-8")
    # The path-derived name the non-artifact copier would have used is absent.
    assert not (site_root / "raw-assets" / f"{safe_raw_slug(asset_rel)}.png").exists()


def test_artifact_with_missing_asset_does_not_break_the_build(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The parser's cache is gitignored, so a fresh clone compiles with every
    artifact asset absent — warn once, never fail, never spam per asset."""
    project, wiki_root, site_root, _paper = _seed_project(tmp_path)
    graph = _artifact_graph(
        ".tesserae/external/raganything/parsed/deadbeef/x.png", "b" * 64
    )

    with caplog.at_level(logging.WARNING, logger="tesserae.site"):
        StaticSiteBuilder().write_site(graph, wiki_root, site_root)

    assert (site_root / "index.html").exists()
    assert not (site_root / "raw-assets").exists()
    missing_warnings = [r for r in caplog.records if "missing on disk" in r.message]
    assert len(missing_warnings) == 1
    assert missing_warnings[0].args == (1,)


def test_artifact_without_asset_path_is_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Tables and equations ARE their description — no bytes to serve, and
    that is not a missing asset."""
    project, wiki_root, site_root, _paper = _seed_project(tmp_path)
    graph = _artifact_graph(None, "c" * 64, kind="table")

    with caplog.at_level(logging.WARNING, logger="tesserae.site"):
        StaticSiteBuilder().write_site(graph, wiki_root, site_root)

    assert not (site_root / "raw-assets").exists()
    assert not [r for r in caplog.records if "missing on disk" in r.message]

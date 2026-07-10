"""Regression tests for frontmatter-driven Paper/Repository/Synthesis typing.

The Tesserae demo corpus annotates every curated research file with a YAML
frontmatter block (``type: Paper`` / ``type: Repository`` / ``type: Synthesis``
/ ``type: OpenQuestion`` / ``type: ResearchDigest``). Before this fix, the
extractor:

  * Did not recognize the ``arxiv-NNNN-NNNNN`` slug shape the demo corpus
    uses for paper subfolders (only the legacy daily-feed ``NNNN.NNNNN``
    shape was matched), so all 50 demo ``paper.md`` files fell through to
    the looser ``"papers" in path`` keyword heuristic.
  * Picked the literal YAML token ``type: Paper`` as the Paper node name
    because ``extract_title`` did not skip the frontmatter block.

The end result: the live demo site's ``/papers/`` index contained a single
``type: Paper``-named row instead of the expected ~50 typed Paper rows.
These tests pin the new frontmatter-driven path so we never silently
regress.
"""

from __future__ import annotations

from pathlib import Path

from tesserae.research_graph import (
    ResearchGraphExtractor,
    ResearchNodeType,
    extract_arxiv_id_from_path,
    is_public_research_node,
    parse_research_frontmatter,
    source_kind_to_node_type,
)


PAPER_MD = """---
type: Paper
arxiv: "2308.04079"
arxiv_url: https://arxiv.org/abs/2308.04079
title: "3D Gaussian Splatting for Real-Time Radiance Field Rendering"
authors:
  - "Bernhard Kerbl"
  - "Georgios Kopanas"
date: 2023-08-08
sub_topic: 3D Gaussian Splatting
methods: [GaussianSplatting, RadianceField]
metrics: [FPS]
oss_repo: graphdeco-inria/gaussian-splatting
---

# 3D Gaussian Splatting for Real-Time Radiance Field Rendering

Kerbl et al. introduce 3D GaussianSplatting, a primitive-based representation
for novel-view synthesis.
"""


REPO_MD = """---
type: Repository
repo: graphdeco-inria/gaussian-splatting
canonical_paper: arxiv-2308-04079
---

# About graphdeco-inria/gaussian-splatting

The official implementation of *3D Gaussian Splatting for Real-Time Radiance
Field Rendering* (Kerbl et al., 2023).
"""


SYNTHESIS_MD = """---
type: Synthesis
period: weekly
iso_week: 2026-W17
---

# Weekly Synthesis — 2026-W17

A roll-up of the daily digests covering 3D Gaussian Splatting work.
"""


# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------


def test_parse_research_frontmatter_handles_scalars_and_lists():
    fm = parse_research_frontmatter(PAPER_MD)
    assert fm["type"] == "Paper"
    assert fm["arxiv"] == "2308.04079"
    assert fm["title"] == "3D Gaussian Splatting for Real-Time Radiance Field Rendering"
    # Block list (``authors:`` followed by ``  - "..."`` items)
    assert fm["authors"] == ["Bernhard Kerbl", "Georgios Kopanas"]
    # Inline list (``methods: [a, b]``)
    assert fm["methods"] == ["GaussianSplatting", "RadianceField"]
    assert fm["metrics"] == ["FPS"]
    # Date kept as raw scalar string (we don't parse YAML dates)
    assert fm["date"] == "2023-08-08"


def test_parse_research_frontmatter_returns_empty_when_absent():
    assert parse_research_frontmatter("# Just a heading\n\nNo frontmatter here.") == {}
    assert parse_research_frontmatter("") == {}


# ---------------------------------------------------------------------------
# source_kind_to_node_type — frontmatter ``type:`` overrides path heuristics
# ---------------------------------------------------------------------------


def test_source_kind_to_node_type_uses_frontmatter_type_for_paper():
    fm = {"type": "Paper"}
    # Demo-corpus slug shape (``arxiv-2308-04079``) — not matched by the
    # legacy daily-feed regex; frontmatter is the deciding signal.
    path = "data/research/papers/arxiv-2308-04079/paper.md"
    assert source_kind_to_node_type("Repository", path, fm) == ResearchNodeType.PAPER


def test_source_kind_to_node_type_uses_frontmatter_type_for_repo_and_synthesis():
    assert source_kind_to_node_type("Repository", "x.md", {"type": "Repository"}) == ResearchNodeType.REPOSITORY
    assert source_kind_to_node_type("Repository", "x.md", {"type": "Synthesis"}) == ResearchNodeType.SYNTHESIS
    assert source_kind_to_node_type("Repository", "x.md", {"type": "ResearchDigest"}) == ResearchNodeType.SYNTHESIS
    assert source_kind_to_node_type("Repository", "x.md", {"type": "OpenQuestion"}) == ResearchNodeType.OPEN_QUESTION


def test_source_kind_to_node_type_falls_back_when_no_frontmatter():
    # An explicit known source_kind maps EXACTLY — path heuristics never
    # override it (the CLI validates --source-kind with choices=).
    assert source_kind_to_node_type("Repository", "src/foo.py", None) == ResearchNodeType.REPOSITORY
    assert (
        source_kind_to_node_type("Repository", "data/research/papers/arxiv-2308-04079/paper.md", None)
        == ResearchNodeType.REPOSITORY
    )
    assert source_kind_to_node_type("Paper", "src/foo.py", None) == ResearchNodeType.PAPER
    assert source_kind_to_node_type("ResearchDigest", "x.md", None) == ResearchNodeType.SYNTHESIS
    # The generic default keeps path-based detection: a hyphen-slug paper
    # folder is recognized even without frontmatter.
    assert (
        source_kind_to_node_type("SourceDocument", "data/research/papers/arxiv-2308-04079/paper.md", None)
        == ResearchNodeType.PAPER
    )


# ---------------------------------------------------------------------------
# Path-shape arxiv id recognition (demo corpus uses ``arxiv-NNNN-NNNNN`` slugs)
# ---------------------------------------------------------------------------


def test_extract_arxiv_id_from_hyphen_slug_path():
    p = "examples/demo-corpus/data/research/papers/arxiv-2308-04079/paper.md"
    assert extract_arxiv_id_from_path(p) == "2308.04079"


def test_extract_arxiv_id_from_legacy_dot_slug_path():
    # Pre-existing daily-feed shape must keep working.
    p = "data/research/2024-01-15/papers/2308.04079/paper.md"
    assert extract_arxiv_id_from_path(p) == "2308.04079"


# ---------------------------------------------------------------------------
# End-to-end extraction: a paper.md with frontmatter becomes one Paper node
# ---------------------------------------------------------------------------


def test_paper_md_with_frontmatter_becomes_paper_node(tmp_path):
    src = tmp_path / "data" / "research" / "papers" / "arxiv-2308-04079" / "paper.md"
    src.parent.mkdir(parents=True)
    src.write_text(PAPER_MD)

    graph = ResearchGraphExtractor().extract_file(src, source_kind="Repository")
    papers = [n for n in graph.nodes if n.type == ResearchNodeType.PAPER]
    assert len(papers) == 1
    paper = papers[0]
    # Display name comes from frontmatter ``title:`` — not the YAML token.
    assert paper.name == "3D Gaussian Splatting for Real-Time Radiance Field Rendering"
    # arXiv id is parsed from the hyphen-slug path / frontmatter.
    assert paper.metadata.get("arxiv_id") == "2308.04079"
    # ``paper_file`` quality means the projector will publish a wiki page.
    assert paper.metadata.get("title_quality") == "paper_file"
    # Authors landed in frontmatter metadata for downstream consumers.
    assert paper.metadata.get("frontmatter_authors") == ["Bernhard Kerbl", "Georgios Kopanas"]


def test_paper_node_passes_is_public_research_node(tmp_path):
    src = tmp_path / "data" / "research" / "papers" / "arxiv-2308-04079" / "paper.md"
    src.parent.mkdir(parents=True)
    src.write_text(PAPER_MD)
    graph = ResearchGraphExtractor().extract_file(src, source_kind="Repository")
    papers = [n for n in graph.nodes if n.type == ResearchNodeType.PAPER]
    assert is_public_research_node(papers[0]) is True


def test_repository_md_with_frontmatter_becomes_repository_node(tmp_path):
    src = tmp_path / "data" / "research" / "repos" / "graphdeco-inria-gaussian-splatting" / "about.md"
    src.parent.mkdir(parents=True)
    src.write_text(REPO_MD)
    graph = ResearchGraphExtractor().extract_file(src, source_kind="Repository")
    repos = [n for n in graph.nodes if n.type == ResearchNodeType.REPOSITORY]
    assert len(repos) >= 1
    repo = repos[0]
    # Repo identity comes from the frontmatter ``repo:`` field.
    assert repo.metadata.get("github_repo") == "graphdeco-inria/gaussian-splatting"
    # The repo name must NOT be the literal YAML token ``type: Repository``.
    assert repo.name != "type: Repository"
    assert is_public_research_node(repo) is True


def test_synthesis_md_with_frontmatter_becomes_synthesis_node(tmp_path):
    src = tmp_path / "data" / "research" / "weekly" / "2026-W17" / "synthesis.md"
    src.parent.mkdir(parents=True)
    src.write_text(SYNTHESIS_MD)
    graph = ResearchGraphExtractor().extract_file(src, source_kind="Repository")
    syns = [n for n in graph.nodes if n.type == ResearchNodeType.SYNTHESIS]
    assert len(syns) == 1
    syn = syns[0]
    assert syn.name == "Weekly Synthesis — 2026-W17"
    assert is_public_research_node(syn) is True

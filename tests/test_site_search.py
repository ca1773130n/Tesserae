"""Tests for :mod:`llm_wiki.site.search`."""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from llm_wiki.research_graph import (
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNodeType,
)
from llm_wiki.site.search import (
    EXCLUDED_TYPES,
    WIKI_LAYER_TYPES,
    build_search_index,
    is_wiki_layer,
)
from llm_wiki.wiki_store import WikiPage


# --------------------------------------------------------- type-set guarantees


_DESIGN_SPEC_EXCLUDED = {
    "CodeClass",
    "CodeFunction",
    "CodeModule",
    "Dependency",
    "EvidenceSpan",
    "SourceFile",
    "Claim",
    "ContributionClaim",
    "PerformanceClaim",
    "ComparisonClaim",
    "LimitationClaim",
    "CausalClaim",
}


def test_wiki_layer_types_excludes_every_design_spec_type():
    """§3.1 lists the types that must NEVER get a route or search entry."""

    for excluded in _DESIGN_SPEC_EXCLUDED:
        assert excluded not in WIKI_LAYER_TYPES, excluded
        assert excluded in EXCLUDED_TYPES, excluded


def test_wiki_layer_types_includes_the_eight_published_kinds():
    # At minimum, the eight surfaced kinds (sources/concepts/entities/papers/
    # repos/topics/syntheses/questions) must each have at least one underlying
    # ResearchNodeType in the allow-list.
    expected_anchors = {
        ResearchNodeType.SOURCE_DOCUMENT.value,
        ResearchNodeType.CONCEPT.value,
        ResearchNodeType.MODEL.value,  # entities
        ResearchNodeType.PAPER.value,
        ResearchNodeType.REPOSITORY.value,
        ResearchNodeType.RESEARCH_TOPIC.value,
        ResearchNodeType.SYNTHESIS.value,
        ResearchNodeType.OPEN_QUESTION.value,
    }
    for value in expected_anchors:
        assert value in WIKI_LAYER_TYPES, value


# ----------------------------------------------------------------- fixture graph


@pytest.fixture
def mixed_graph() -> ResearchGraph:
    """A graph that combines wiki-layer types with explicitly excluded types."""

    builder = ResearchGraphBuilder()

    # Wiki-layer nodes
    builder.add_node("Gaussian Splatting", ResearchNodeType.METHODOLOGICAL_CONCEPT, description="3DGS rendering.")
    builder.add_node("Diffusion Models", ResearchNodeType.CONCEPT, description="Generative diffusion.")
    builder.add_node("CIFAR-10", ResearchNodeType.DATASET, description="A benchmark image set.")
    builder.add_node("ScanNet", ResearchNodeType.BENCHMARK)
    builder.add_node("Neural Radiance Fields", ResearchNodeType.PAPER, description="The canonical NeRF paper.")
    builder.add_node("nerfstudio", ResearchNodeType.REPOSITORY, description="A NeRF research framework.")
    builder.add_node("3D Reconstruction", ResearchNodeType.RESEARCH_TOPIC)
    builder.add_node("Why does GS scale?", ResearchNodeType.OPEN_QUESTION)

    # Excluded types — must NOT appear in the index.
    builder.add_node("MyClass", ResearchNodeType.CODE_CLASS)
    builder.add_node("my_func", ResearchNodeType.CODE_FUNCTION)
    builder.add_node("foo_module", ResearchNodeType.CODE_MODULE)
    builder.add_node("foo.py", ResearchNodeType.SOURCE_FILE)
    builder.add_node("numpy", ResearchNodeType.DEPENDENCY)
    builder.add_node("Evidence: foo bar baz", ResearchNodeType.EVIDENCE_SPAN)
    builder.add_node("Claim: GS is fast", ResearchNodeType.CLAIM)
    builder.add_node("Claim: SOTA on ScanNet", ResearchNodeType.PERFORMANCE_CLAIM)
    builder.add_node("Claim: NeRF beats GS", ResearchNodeType.COMPARISON_CLAIM)
    builder.add_node("Claim: limited at scale", ResearchNodeType.LIMITATION_CLAIM)
    builder.add_node("Claim: because dense", ResearchNodeType.CAUSAL_CLAIM)
    builder.add_node("Claim: introduces 3DGS", ResearchNodeType.CONTRIBUTION_CLAIM)

    return builder.build()


# ------------------------------------------------------- build_search_index shape


def test_build_search_index_excludes_every_excluded_type(mixed_graph: ResearchGraph):
    index = build_search_index(mixed_graph, wiki_pages_by_kind={})
    seen_titles = {entry["title"] for entry in index}

    forbidden_titles = {
        "MyClass",
        "my_func",
        "foo_module",
        "foo.py",
        "numpy",
    }
    forbidden_prefixes = ("Evidence:", "Claim:")

    assert seen_titles.isdisjoint(forbidden_titles)
    assert not any(any(str(t).startswith(p) for p in forbidden_prefixes) for t in seen_titles)

    # And the underlying ids — make sure no excluded type name slipped in.
    for entry in index:
        assert "CodeClass" not in str(entry.get("id", ""))
        assert "CodeFunction" not in str(entry.get("id", ""))
        assert "EvidenceSpan" not in str(entry.get("id", ""))
        assert "Claim" not in str(entry.get("id", "")) or "OpenQuestion" in str(entry.get("id", ""))


def test_build_search_index_entries_have_required_keys(mixed_graph: ResearchGraph):
    index = build_search_index(mixed_graph, wiki_pages_by_kind={})
    assert index, "expected at least one wiki-layer entry"
    required = {"id", "title", "kind", "href", "summary", "source_path"}
    for entry in index:
        missing = required - set(entry.keys())
        assert not missing, f"missing keys: {missing} on {entry}"
        assert entry["kind"] in {"sources", "papers", "repos", "concepts", "entities", "topics", "syntheses", "questions"}
        assert isinstance(entry["summary"], str)
        assert len(entry["summary"]) <= 200


def test_build_search_index_summary_is_capped_at_200_chars():
    builder = ResearchGraphBuilder()
    long_desc = "lorem ipsum " * 100
    builder.add_node("Big Concept", ResearchNodeType.CONCEPT, description=long_desc)
    graph = builder.build()
    index = build_search_index(graph, wiki_pages_by_kind={})
    assert len(index) == 1
    assert len(index[0]["summary"]) <= 200


def test_build_search_index_uses_wiki_page_for_sources_and_syntheses(tmp_path: Path):
    builder = ResearchGraphBuilder()
    builder.add_node("Some Source", ResearchNodeType.SOURCE_DOCUMENT, description="ignored if wiki page wins")
    graph = builder.build()

    page = WikiPage(
        kind="syntheses",
        slug="weekly-2026-w17",
        title="Weekly 2026-W17",
        body="# Weekly 2026-W17\n\nThree papers landed this week.\n",
        path=tmp_path / "syntheses" / "weekly-2026-w17.md",
        frontmatter={"title": "Weekly 2026-W17", "summary": "Three papers landed this week."},
    )
    pages = {"syntheses": [page]}

    index = build_search_index(graph, wiki_pages_by_kind=pages)
    syntheses = [e for e in index if e["kind"] == "syntheses"]
    assert syntheses, "synthesis page must surface in the index"
    assert syntheses[0]["title"] == "Weekly 2026-W17"
    assert syntheses[0]["summary"] == "Three papers landed this week."
    assert syntheses[0]["href"] == "syntheses/weekly-2026-w17.html"


def test_is_wiki_layer_helper(mixed_graph: ResearchGraph):
    for node in mixed_graph.nodes:
        if node.type.value in EXCLUDED_TYPES:
            assert not is_wiki_layer(node)
        elif node.type.value in WIKI_LAYER_TYPES:
            assert is_wiki_layer(node)

"""Single-source-of-truth tests for the public-node predicate (F-10).

The codex extraction review found that ``is_public_research_node()`` was
returning ``True`` for code-graph nodes (``CodeFunction``, ``SourceFile``,
``Dependency``) and assertion-layer nodes (``EvidenceSpan``, ``Claim``
variants), so each consumer (projector, search, exports) was layering its
own ad-hoc filter on top.

The fix is to funnel every consumer through one helper:
:func:`llm_wiki.wiki_projector.kind_for_node`. It returns the public wiki
kind for a node or ``None`` (private). These tests pin that contract by
exercising every entry in :class:`ResearchNodeType` plus the documented
edge cases (paper title quality, social-feed source path, code-project vs
repository, synthesis is always public, etc.).
"""

from __future__ import annotations

from typing import Optional

import pytest

from llm_wiki.research_graph import (
    ResearchNode,
    ResearchNodeType,
    is_public_research_node,
)
from llm_wiki.wiki_projector import (
    ASSERTION_LAYER_TYPES,
    CODE_GRAPH_TYPES,
    is_assertion_node,
    is_code_graph_node,
    kind_for_node,
)


def _node(
    node_type: ResearchNodeType,
    *,
    name: str = "Test Node",
    metadata: Optional[dict] = None,
    source_path: Optional[str] = None,
) -> ResearchNode:
    return ResearchNode(
        id=f"{node_type.value}:{name}",
        name=name,
        type=node_type,
        metadata=metadata or {},
        source_path=source_path,
    )


# ---------------------------------------------------------- public types

# (node_type, expected_kind) — the canonical public taxonomy.
PUBLIC_TYPE_CASES = [
    (ResearchNodeType.SOURCE_DOCUMENT, "sources"),
    (ResearchNodeType.PAPER, "papers"),
    (ResearchNodeType.REPOSITORY, "repos"),
    (ResearchNodeType.PROJECT, "repos"),
    (ResearchNodeType.CONCEPT, "concepts"),
    (ResearchNodeType.TECHNICAL_TERM, "concepts"),
    (ResearchNodeType.MATHEMATICAL_CONCEPT, "concepts"),
    (ResearchNodeType.METHODOLOGICAL_CONCEPT, "concepts"),
    (ResearchNodeType.ALGORITHM, "concepts"),
    (ResearchNodeType.OBJECTIVE_FUNCTION, "concepts"),
    (ResearchNodeType.ARCHITECTURE_PATTERN, "concepts"),
    (ResearchNodeType.TRAINING_PARADIGM, "concepts"),
    (ResearchNodeType.INFERENCE_STRATEGY, "concepts"),
    (ResearchNodeType.EVALUATION_PROTOCOL, "concepts"),
    (ResearchNodeType.TASK, "concepts"),
    (ResearchNodeType.CAPABILITY, "concepts"),
    (ResearchNodeType.MODEL, "entities"),
    (ResearchNodeType.DATASET, "entities"),
    (ResearchNodeType.BENCHMARK, "entities"),
    (ResearchNodeType.METRIC, "entities"),
    (ResearchNodeType.ORGANIZATION, "entities"),
    (ResearchNodeType.PERSON, "entities"),
    (ResearchNodeType.RESEARCH_FIELD, "topics"),
    (ResearchNodeType.RESEARCH_TOPIC, "topics"),
    (ResearchNodeType.PROBLEM_AREA, "topics"),
    (ResearchNodeType.APPROACH_FAMILY, "topics"),
    (ResearchNodeType.TREND, "topics"),
    (ResearchNodeType.OPEN_QUESTION, "questions"),
    # Synthesis is always public (deterministic higher-order content the wiki
    # owns; no per-node validity gate).
    (ResearchNodeType.SYNTHESIS, "syntheses"),
]


@pytest.mark.parametrize("node_type,expected_kind", PUBLIC_TYPE_CASES)
def test_public_types_map_to_public_kind(
    node_type: ResearchNodeType, expected_kind: str
) -> None:
    node = _node(node_type)
    assert kind_for_node(node) == expected_kind
    assert not is_code_graph_node(node)
    assert not is_assertion_node(node)


# ---------------------------------------------------------- code-graph types

CODE_GRAPH_CASES = [
    ResearchNodeType.CODE_PROJECT,
    ResearchNodeType.SOURCE_FILE,
    ResearchNodeType.CODE_MODULE,
    ResearchNodeType.CODE_CLASS,
    ResearchNodeType.CODE_FUNCTION,
    ResearchNodeType.DEPENDENCY,
]


@pytest.mark.parametrize("node_type", CODE_GRAPH_CASES)
def test_code_graph_types_are_private(node_type: ResearchNodeType) -> None:
    """F-9 / F-11: code-graph types never get a public wiki page."""
    node = _node(node_type)
    assert kind_for_node(node) is None
    assert is_code_graph_node(node)
    assert node.type in CODE_GRAPH_TYPES


def test_code_project_is_always_private() -> None:
    """F-9 explicit: ``CodeProject`` is the *internal* code-graph node.

    The user-facing repo type is ``Repository``; ``CodeProject`` lives only in
    ``code-graph.json``. Even with a research-looking name and source path, it
    must never get a ``/repos/`` page.
    """
    node = _node(
        ResearchNodeType.CODE_PROJECT,
        name="LLM-Wiki",
        source_path="/Users/neo/Developer/Projects/LLM-Wiki",
        metadata={"layer": "project", "source_kind": "CodeProject"},
    )
    assert kind_for_node(node) is None
    assert is_code_graph_node(node)


# ---------------------------------------------------------- assertion layer

ASSERTION_CASES = [
    ResearchNodeType.CLAIM,
    ResearchNodeType.CONTRIBUTION_CLAIM,
    ResearchNodeType.PERFORMANCE_CLAIM,
    ResearchNodeType.COMPARISON_CLAIM,
    ResearchNodeType.LIMITATION_CLAIM,
    ResearchNodeType.CAUSAL_CLAIM,
    ResearchNodeType.EVIDENCE_SPAN,
]


@pytest.mark.parametrize("node_type", ASSERTION_CASES)
def test_assertion_layer_types_are_private(node_type: ResearchNodeType) -> None:
    """F-10: claims and evidence spans render inline, not at their own URL."""
    node = _node(node_type)
    assert kind_for_node(node) is None
    assert is_assertion_node(node)
    assert node.type in ASSERTION_LAYER_TYPES


def test_evidence_span_is_always_private() -> None:
    """Spec callout: ``EvidenceSpan`` is *always* private."""
    node = _node(ResearchNodeType.EVIDENCE_SPAN, name="some span of text")
    assert kind_for_node(node) is None


# ---------------------------------------------------------- paper title quality


def test_paper_with_invalid_title_is_private() -> None:
    """F-10 spec: ``Paper`` with ``title_quality`` outside the verified set is private."""
    node = _node(
        ResearchNodeType.PAPER,
        name="not a real paper",
        metadata={"title_quality": "invalid"},
    )
    assert kind_for_node(node) is None
    assert not is_public_research_node(node)


def test_paper_with_arxiv_only_title_quality_is_private() -> None:
    node = _node(
        ResearchNodeType.PAPER,
        name="arXiv:1234.5678",
        metadata={"title_quality": "arxiv_only"},
    )
    assert kind_for_node(node) is None


@pytest.mark.parametrize("quality", ["paper_file", "verified"])
def test_paper_with_verified_quality_is_public(quality: str) -> None:
    node = _node(
        ResearchNodeType.PAPER,
        name="A Real Paper",
        metadata={"title_quality": quality},
    )
    assert kind_for_node(node) == "papers"


def test_paper_without_title_quality_metadata_is_public() -> None:
    """No metadata at all means we cannot reject — keep the legacy default
    of public so simple synthetic tests / minimal corpora still work."""
    node = _node(ResearchNodeType.PAPER, name="A Paper")
    assert kind_for_node(node) == "papers"


# ---------------------------------------------------------- social feed source


def test_social_feed_source_path_is_private() -> None:
    """Social feed captures are evidence inputs, not public entities."""
    node = _node(
        ResearchNodeType.SOURCE_DOCUMENT,
        source_path="data/research/daily/2026-04-27/feeds/twitter.md",
    )
    assert kind_for_node(node) is None
    assert not is_public_research_node(node)


def test_non_feed_source_document_is_public() -> None:
    node = _node(
        ResearchNodeType.SOURCE_DOCUMENT,
        source_path="data/research/daily/2026-04-27/digest.md",
    )
    assert kind_for_node(node) == "sources"


# ---------------------------------------------------------- enum coverage


def test_every_node_type_classifies() -> None:
    """Every value in :class:`ResearchNodeType` lands in exactly one bucket.

    Public buckets are ``sources``/``concepts``/``entities``/``papers``/
    ``repos``/``topics``/``syntheses``/``questions``. Private buckets are
    code-graph and assertion-layer; nothing falls through unclassified.
    """
    seen = set()
    for node_type in ResearchNodeType:
        node = _node(node_type)
        kind = kind_for_node(node)
        if kind is not None:
            seen.add(node_type)
            continue
        # Either code-graph or assertion-layer, otherwise we have an
        # un-classified type the predicate dropped silently.
        assert is_code_graph_node(node) or is_assertion_node(node), (
            f"{node_type.value} is neither public nor explicitly private"
        )
        seen.add(node_type)
    # Sanity: covered every enum value.
    assert seen == set(ResearchNodeType)


def test_partitions_are_disjoint() -> None:
    """Code-graph and assertion-layer partitions never overlap."""
    assert CODE_GRAPH_TYPES.isdisjoint(ASSERTION_LAYER_TYPES)

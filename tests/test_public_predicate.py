"""Single-source-of-truth tests for the public-node predicate (F-10).

The codex extraction review found that ``is_public_research_node()`` was
returning ``True`` for code-graph nodes (``CodeFunction``, ``SourceFile``,
``Dependency``) and assertion-layer nodes (``EvidenceSpan``, ``Claim``
variants), so each consumer (projector, search, exports) was layering its
own ad-hoc filter on top.

The fix is to funnel every consumer through one helper:
:func:`tesserae.wiki_projector.kind_for_node`. It returns the public wiki
kind for a node or ``None`` (private). These tests pin that contract by
exercising every entry in :class:`ResearchNodeType` plus the documented
edge cases (paper title quality, social-feed source path, code-project vs
repository, synthesis is always public, etc.).
"""

from __future__ import annotations

from typing import Optional

import pytest

from tesserae.research_graph import (
    ResearchNode,
    ResearchNodeType,
    is_public_research_node,
)
from tesserae.wiki_projector import (
    ASSERTION_LAYER_TYPES,
    CODE_GRAPH_TYPES,
    is_assertion_node,
    is_code_graph_node,
    is_private_research_node,
    is_session_finding_node,
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
    # NB: PERSON was here, but it is now a private-research type (paper-author
    # biblio noise would flood /entities/). It is covered by
    # PRIVATE_RESEARCH_CASES / test_private_research_types below instead.
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
        name="Tesserae",
        source_path="/Users/neo/Developer/Projects/Tesserae",
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


# -------------------------------------------------- private-research (4th bucket)

# Research-layer types that are otherwise public-shaped but intentionally
# suppressed from the public projection for noise/relevance reasons (the
# fourth classification bucket, distinct from code-graph + assertion-layer).
PRIVATE_RESEARCH_CASES = [
    ResearchNodeType.PERSON,   # paper-author biblio noise
    ResearchNodeType.STUB,     # vault-only wikilink tombstones
    ResearchNodeType.SESSION,  # session envelopes (findings are the surface)
]


@pytest.mark.parametrize("node_type", PRIVATE_RESEARCH_CASES)
def test_private_research_types_are_suppressed(node_type: ResearchNodeType) -> None:
    """Person/Stub/Session get no public wiki kind, and are flagged by the
    first-class ``is_private_research_node`` predicate — NOT by being
    misclassified as code-graph or assertion-layer."""
    node = _node(node_type)
    assert kind_for_node(node) is None
    assert is_private_research_node(node)
    assert not is_code_graph_node(node)
    assert not is_assertion_node(node)
    assert not is_session_finding_node(node)


# ------------------------------------------------- session findings (5th bucket)

# Session findings are PUBLIC project-memory, but surfaced on the dedicated
# /sessions/ route rather than one of the eight wiki kinds — so kind_for_node
# is None yet they are emphatically not private.
SESSION_FINDING_CASES = [
    ResearchNodeType.SESSION_INSIGHT,
    ResearchNodeType.SESSION_DECISION,
    ResearchNodeType.SESSION_QUESTION,
    ResearchNodeType.SESSION_TODO,
    ResearchNodeType.SESSION_HYPOTHESIS,
    ResearchNodeType.SESSION_TAKEAWAY,
]


@pytest.mark.parametrize("node_type", SESSION_FINDING_CASES)
def test_session_findings_are_their_own_bucket(node_type: ResearchNodeType) -> None:
    """Session findings have no wiki kind (they live on /sessions/) but are
    flagged by ``is_session_finding_node`` — not private, not code, not
    assertion."""
    node = _node(node_type)
    assert kind_for_node(node) is None
    assert is_session_finding_node(node)
    assert not is_private_research_node(node)
    assert not is_code_graph_node(node)
    assert not is_assertion_node(node)


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

    The partition is FIVE explicit categories:
      * public wiki projection (``kind_for_node`` returns one of the eight
        wiki kinds), OR
      * session findings (public, but on the dedicated /sessions/ route — no
        wiki kind), OR
      * private-research / suppressed (Person / Stub / Session envelope), OR
      * code-graph layer, OR
      * assertion layer.
    Nothing may fall through unclassified — a new type with no home here is a
    bug, not a silent drop.
    """
    seen = set()
    for node_type in ResearchNodeType:
        node = _node(node_type)
        kind = kind_for_node(node)
        if kind is not None:
            seen.add(node_type)
            continue
        # No wiki kind → must land in exactly one explicit non-wiki bucket,
        # otherwise we have an un-classified type the predicate dropped
        # silently.
        assert (
            is_session_finding_node(node)
            or is_private_research_node(node)
            or is_code_graph_node(node)
            or is_assertion_node(node)
        ), f"{node_type.value} is neither public nor explicitly private"
        seen.add(node_type)
    # Sanity: covered every enum value.
    assert seen == set(ResearchNodeType)


def test_partitions_are_disjoint() -> None:
    """Code-graph and assertion-layer partitions never overlap."""
    assert CODE_GRAPH_TYPES.isdisjoint(ASSERTION_LAYER_TYPES)

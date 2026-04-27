"""Unit tests for the four-signal relevance scorer."""

from __future__ import annotations

import math

from llm_wiki.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from llm_wiki.site.relevance import (
    RelevanceContext,
    WEIGHT_ADAMIC_ADAR,
    WEIGHT_LINK,
    WEIGHT_SOURCE,
    WEIGHT_TYPE,
    score,
    top_related,
)


# ---------------------------------------------------------------------------
# small graph builders
# ---------------------------------------------------------------------------

def _node(node_id: str, node_type: ResearchNodeType, source_path: str = "") -> ResearchNode:
    return ResearchNode(
        id=node_id,
        name=node_id,
        type=node_type,
        source_path=source_path or None,
    )


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_directly_linked_nodes_score_at_least_three():
    a = _node("a", ResearchNodeType.CONCEPT)
    b = _node("b", ResearchNodeType.PAPER)
    graph = ResearchGraph(
        nodes=[a, b],
        edges=[ResearchEdge(source="a", target="b", type="uses")],
    )
    ctx = RelevanceContext.from_graph(graph)
    assert score("a", "b", ctx) >= WEIGHT_LINK
    # No type affinity (different types), no shared source, no shared neighbours.
    assert score("a", "b", ctx) == WEIGHT_LINK


def test_shared_source_path_scores_at_least_four_without_edges():
    a = _node("a", ResearchNodeType.CONCEPT, source_path="data/x.md")
    b = _node("b", ResearchNodeType.PAPER, source_path="data/x.md")
    graph = ResearchGraph(nodes=[a, b], edges=[])
    ctx = RelevanceContext.from_graph(graph)
    assert score("a", "b", ctx) >= WEIGHT_SOURCE


def test_multiple_shared_sources_via_metadata_stack():
    a = _node("a", ResearchNodeType.CONCEPT, source_path="data/x.md")
    b = ResearchNode(
        id="b",
        name="b",
        type=ResearchNodeType.PAPER,
        source_path="data/x.md",
        metadata={"source_paths": ["data/y.md"]},
    )
    a2 = ResearchNode(
        id="a",
        name="a",
        type=ResearchNodeType.CONCEPT,
        source_path="data/x.md",
        metadata={"source_paths": ["data/y.md"]},
    )
    graph = ResearchGraph(nodes=[a2, b], edges=[])
    ctx = RelevanceContext.from_graph(graph)
    # Two shared paths -> 2 * 4.0 from the source signal.
    assert score("a", "b", ctx) >= 2 * WEIGHT_SOURCE


def test_adamic_adar_contributes_when_sharing_a_popular_neighbour():
    # a — h, b — h, h connects to several others (so degree(h) > 2).
    # No direct edge a<->b, no shared source, different types.
    nodes = [
        _node("a", ResearchNodeType.CONCEPT),
        _node("b", ResearchNodeType.PAPER),
        _node("h", ResearchNodeType.RESEARCH_TOPIC),
        _node("x", ResearchNodeType.CONCEPT),
        _node("y", ResearchNodeType.CONCEPT),
    ]
    edges = [
        ResearchEdge(source="a", target="h", type="uses"),
        ResearchEdge(source="b", target="h", type="uses"),
        ResearchEdge(source="h", target="x", type="uses"),
        ResearchEdge(source="h", target="y", type="uses"),
    ]
    graph = ResearchGraph(nodes=nodes, edges=edges)
    ctx = RelevanceContext.from_graph(graph)

    # Sanity: a and b share neighbour h.
    s = score("a", "b", ctx)
    assert s > 0
    # All four edges of h give it degree 4. Expected AA contribution =
    # 1 / log(1 + 4) = 1 / log 5.
    expected_aa = WEIGHT_ADAMIC_ADAR * (1.0 / math.log(5))
    assert s == expected_aa  # no type affinity, no link, no source


def test_type_affinity_adds_one_when_types_match():
    a = _node("a", ResearchNodeType.CONCEPT)
    b = _node("b", ResearchNodeType.CONCEPT)
    graph = ResearchGraph(nodes=[a, b], edges=[])
    ctx = RelevanceContext.from_graph(graph)
    assert score("a", "b", ctx) == WEIGHT_TYPE


def test_score_is_zero_for_identical_or_unknown_ids():
    a = _node("a", ResearchNodeType.CONCEPT)
    graph = ResearchGraph(nodes=[a], edges=[])
    ctx = RelevanceContext.from_graph(graph)
    assert score("a", "a", ctx) == 0.0
    assert score("a", "ghost", ctx) == 0.0
    assert score("ghost", "a", ctx) == 0.0


def test_top_related_returns_descending_scores_and_respects_limit():
    nodes = [
        _node("home", ResearchNodeType.CONCEPT, source_path="s.md"),
        _node("strong", ResearchNodeType.CONCEPT, source_path="s.md"),  # source overlap
        _node("linked", ResearchNodeType.PAPER),                        # direct link
        _node("typed", ResearchNodeType.CONCEPT),                       # type affinity only
        _node("orphan", ResearchNodeType.MODEL),                        # unrelated
    ]
    edges = [ResearchEdge(source="home", target="linked", type="uses")]
    graph = ResearchGraph(nodes=nodes, edges=edges)
    ctx = RelevanceContext.from_graph(graph)

    ranked = top_related("home", ctx, limit=10)
    # Orphan should be missing entirely (score == 0).
    ids = [nid for nid, _ in ranked]
    assert "orphan" not in ids

    # Descending by score.
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)

    # Highest is the source-overlap pairing (which also picks up type
    # affinity since both are Concepts), > the direct-link pair.
    assert ranked[0][0] == "strong"
    assert ranked[0][1] >= ranked[1][1]

    # Limit shrinks the result.
    limited = top_related("home", ctx, limit=2)
    assert len(limited) == 2
    assert [nid for nid, _ in limited] == ids[:2]


def test_top_related_returns_empty_for_unknown_node():
    graph = ResearchGraph(nodes=[_node("a", ResearchNodeType.CONCEPT)], edges=[])
    ctx = RelevanceContext.from_graph(graph)
    assert top_related("ghost", ctx) == []


def test_relevance_context_indexes_neighbors_and_degrees():
    nodes = [
        _node("a", ResearchNodeType.CONCEPT),
        _node("b", ResearchNodeType.PAPER),
        _node("c", ResearchNodeType.PAPER),
    ]
    edges = [
        ResearchEdge(source="a", target="b", type="uses"),
        ResearchEdge(source="b", target="c", type="uses"),
    ]
    ctx = RelevanceContext.from_graph(ResearchGraph(nodes=nodes, edges=edges))
    assert ctx.neighbors["a"] == {"b"}
    assert ctx.neighbors["b"] == {"a", "c"}
    assert ctx.degrees["b"] == 2

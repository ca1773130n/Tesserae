"""Tests for the pure on-demand context compiler (CTX-01 / CTX-04).

All tests are CI-safe and deterministic: ``synthesize=False`` everywhere, no
network/LLM, and an explicit :class:`HashEmbeddingBackend` so hybrid-search
results are isolation-stable. ``project_root=None`` so node bodies come from
``node.description`` (no wiki layer needed).
"""

from __future__ import annotations

from typing import List

from tesserae.context_compiler import (
    ContextBundle,
    ContextCitation,
    compile_context,
)
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.retrieval.hybrid import HashEmbeddingBackend


def _backend() -> HashEmbeddingBackend:
    return HashEmbeddingBackend()


def _connected_graph() -> ResearchGraph:
    """A small connected graph with sizeable, distinct bodies."""
    nodes: List[ResearchNode] = [
        ResearchNode(
            id="splat",
            name="Gaussian Splatting",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            description="Gaussian splatting renders radiance fields with "
            "anisotropic 3D gaussians. " * 8,
        ),
        ResearchNode(
            id="nerf",
            name="Neural Radiance Fields",
            type=ResearchNodeType.PAPER,
            description="NeRF represents a scene as a continuous volumetric "
            "function queried by a neural network. " * 8,
        ),
        ResearchNode(
            id="claim",
            name="Realtime Rendering Claim",
            type=ResearchNodeType.PERFORMANCE_CLAIM,
            description="Achieves realtime framerates at high resolution "
            "on commodity GPUs. " * 8,
        ),
        ResearchNode(
            id="okapi",
            name="Okapi BM25",
            type=ResearchNodeType.CONCEPT,
            description="BM25 is a bag-of-words ranking function used in "
            "lexical information retrieval. " * 8,
        ),
    ]
    edges = [
        ResearchEdge(source="splat", target="nerf", type="references"),
        ResearchEdge(source="splat", target="claim", type="supports_claim"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _disconnected_graph() -> ResearchGraph:
    """Nodes with NO edges -> PPR returns [] for any seed."""
    nodes = [
        ResearchNode(
            id="lonely",
            name="Gaussian Splatting",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            description="Isolated gaussian splatting concept with no edges.",
        ),
        ResearchNode(
            id="lonely2",
            name="Diffusion Models",
            type=ResearchNodeType.CONCEPT,
            description="Isolated diffusion concept with no edges.",
        ),
    ]
    return ResearchGraph(nodes=nodes, edges=[])


def test_bundle_shape() -> None:
    graph = _connected_graph()
    bundle = compile_context(
        graph, project_root=None, query="gaussian splatting", backend=_backend()
    )
    assert isinstance(bundle, ContextBundle)
    assert bundle.body.startswith("# Context:")
    assert "## Citations" in bundle.body
    assert isinstance(bundle.citations, list)
    assert all(isinstance(c, ContextCitation) for c in bundle.citations)


def test_citation_integrity() -> None:
    graph = _connected_graph()
    bundle = compile_context(
        graph, project_root=None, query="gaussian splatting", backend=_backend()
    )
    node_ids = {n.id for n in graph.nodes}
    assert bundle.citations  # non-empty
    assert all(c.node_id in node_ids for c in bundle.citations)
    assert len(bundle.citations) == len(bundle.selected_nodes)
    # Every citation node_id is also surfaced in the rendered citation block.
    for c in bundle.citations:
        assert f"node_id={c.node_id}" in bundle.body


def test_deterministic_no_llm() -> None:
    graph = _connected_graph()
    b1 = compile_context(
        graph, project_root=None, query="gaussian splatting",
        depth=2, budget=8000, synthesize=False, backend=_backend(),
    )
    b2 = compile_context(
        graph, project_root=None, query="gaussian splatting",
        depth=2, budget=8000, synthesize=False, backend=_backend(),
    )
    assert b1.body == b2.body  # byte-identical: no timestamp embedded
    assert b1.selected_nodes == b2.selected_nodes
    assert b1.ranked_nodes == b2.ranked_nodes


def test_budget_bound() -> None:
    graph = _connected_graph()
    small = compile_context(
        graph, project_root=None, query="gaussian splatting",
        budget=200, backend=_backend(),
    )
    assert small.char_budget_used <= 200
    # Total bodies far exceed 200 chars, so selection must be bounded.
    assert len(small.selected_nodes) < len(graph.nodes)

    uncapped = compile_context(
        graph, project_root=None, query="gaussian splatting",
        budget=0, backend=_backend(),
    )
    # budget<=0 -> uncapped: selects everything reachable from the seeds.
    assert len(uncapped.selected_nodes) >= len(small.selected_nodes)
    assert len(uncapped.selected_nodes) == len(uncapped.ranked_nodes)


def test_ppr_fallback() -> None:
    graph = _disconnected_graph()
    bundle = compile_context(
        graph, project_root=None, query="gaussian splatting", backend=_backend()
    )
    # PPR returns [] for edge-less seeds -> fall back to hybrid/seed ids.
    assert bundle.ranked_nodes
    assert bundle.citations
    node_ids = {n.id for n in graph.nodes}
    assert all(c.node_id in node_ids for c in bundle.citations)


def test_explicit_seeds() -> None:
    graph = _connected_graph()
    bundle = compile_context(
        graph, project_root=None, query="", seeds=["okapi"], backend=_backend()
    )
    assert "okapi" in bundle.seeds_used
    assert bundle.selected_nodes  # non-empty
    assert bundle.citations


def _two_hop_graph() -> ResearchGraph:
    """A->B->C chain: ``C`` is only reachable from ``A`` in 2 hops."""
    nodes = [
        ResearchNode(
            id="a",
            name="Gaussian Splatting",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            description="Seed node A about gaussian splatting. " * 8,
        ),
        ResearchNode(
            id="b",
            name="Neural Radiance Fields",
            type=ResearchNodeType.PAPER,
            description="One hop from A. " * 8,
        ),
        ResearchNode(
            id="c",
            name="Two Hop Only",
            type=ResearchNodeType.CONCEPT,
            description="Two hops from A; unreachable at depth 1. " * 8,
        ),
    ]
    edges = [
        ResearchEdge(source="a", target="b", type="references"),
        ResearchEdge(source="b", target="c", type="references"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_depth_bounds_hop_distance() -> None:
    """depth must bound hop-distance, not just scale top_k (codex major)."""
    graph = _two_hop_graph()
    shallow = compile_context(
        graph, project_root=None, query="", seeds=["a"],
        depth=1, budget=0, backend=_backend(),
    )
    deep = compile_context(
        graph, project_root=None, query="", seeds=["a"],
        depth=2, budget=0, backend=_backend(),
    )
    # Node "c" is only reachable from "a" in 2 hops.
    assert "c" not in shallow.ranked_nodes
    assert "c" not in shallow.selected_nodes
    assert "c" in deep.ranked_nodes
    # Determinism preserved.
    again = compile_context(
        graph, project_root=None, query="", seeds=["a"],
        depth=1, budget=0, backend=_backend(),
    )
    assert again.ranked_nodes == shallow.ranked_nodes


def test_first_body_over_budget_still_returns_one_node() -> None:
    """A budget smaller than the first body returns 1 truncated cited node, not zero."""
    graph = _connected_graph()
    bundle = compile_context(
        graph, project_root=None, query="gaussian splatting",
        budget=10, backend=_backend(),
    )
    # Without the fix this would select ZERO nodes (first body overflows).
    assert len(bundle.selected_nodes) == 1
    assert len(bundle.citations) == 1
    # Budget pressure is reported: the truncated body fills the budget.
    assert bundle.char_budget_used == 10
    assert "## Citations" in bundle.body


def test_synthesize_without_key_falls_back(monkeypatch) -> None:
    """synthesize=True with NO API key returns the deterministic body (no raise)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    graph = _connected_graph()
    det = compile_context(
        graph, project_root=None, query="gaussian splatting",
        budget=8000, synthesize=False, backend=_backend(),
    )
    syn = compile_context(
        graph, project_root=None, query="gaussian splatting",
        budget=8000, synthesize=True, backend=_backend(),
    )
    # No exception, synthesis disabled, deterministic body preserved.
    assert syn.synthesized is False
    assert syn.body == det.body


def test_empty_query_no_seeds_is_valid() -> None:
    graph = _connected_graph()
    bundle = compile_context(graph, project_root=None, query="", backend=_backend())
    assert isinstance(bundle, ContextBundle)
    assert bundle.body.startswith("# Context:")
    assert bundle.char_budget_used == 0
    assert bundle.citations == []

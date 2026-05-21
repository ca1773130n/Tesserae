"""Tests for the hybrid retrieval module and the upgraded MCP search_nodes."""

from __future__ import annotations

from typing import List

import pytest

from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.retrieval.hybrid import (
    HashEmbeddingBackend,
    active_embedding_backend,
    hybrid_search,
    reset_embedding_backend,
)


def _eight_node_graph() -> ResearchGraph:
    """Eight-node fixture exercising each retrieval lane independently."""
    nodes: List[ResearchNode] = [
        ResearchNode(
            id="Paper:dual-splat",
            name="DualSplat",
            type=ResearchNodeType.PAPER,
            description=(
                "Robust 3D Gaussian splatting for novel-view synthesis with "
                "improved shape regularisation across many scenes."
            ),
            metadata={"arxiv_id": "2601.17835"},
        ),
        ResearchNode(
            id="MethodologicalConcept:gaussian-splatting",
            name="Gaussian Splatting",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            aliases=["3DGS"],
            description="Point-based differentiable rendering technique.",
        ),
        ResearchNode(
            id="PerformanceClaim:best-shape",
            name="Best shape reconstruction claim",
            type=ResearchNodeType.PERFORMANCE_CLAIM,
            description="DualSplat reports best-in-class shape reconstruction.",
        ),
        ResearchNode(
            id="Paper:nerf",
            name="NeRF",
            type=ResearchNodeType.PAPER,
            description=(
                "Neural Radiance Fields representing scenes as continuous "
                "volumetric functions optimised from posed images."
            ),
            metadata={"arxiv_id": "2003.08934"},
        ),
        ResearchNode(
            id="MethodologicalConcept:bm25",
            name="BM25",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            aliases=["Okapi BM25"],
            description=(
                "Probabilistic ranking function widely used in information "
                "retrieval; the Okapi variant ships in nearly every search "
                "engine and remains a strong baseline."
            ),
        ),
        ResearchNode(
            id="MethodologicalConcept:rrf",
            name="Reciprocal Rank Fusion",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            aliases=["RRF"],
            description=(
                "Score-free rank aggregation used by LightRAG and other "
                "hybrid retrievers; k=60 is the canonical damping constant."
            ),
        ),
        ResearchNode(
            id="Concept:obsidian-vault",
            name="Obsidian Vault",
            type=ResearchNodeType.CONCEPT,
            description=(
                "Local-first markdown knowledge base that Tesserae projects "
                "the compiled wiki into for offline browsing."
            ),
        ),
        ResearchNode(
            id="OpenQuestion:hybrid-vs-graph",
            name="Hybrid retrieval vs pure graph traversal",
            type=ResearchNodeType.OPEN_QUESTION,
            description=(
                "When should we prefer graph neighbourhood expansion over "
                "BM25 + embedding fusion for knowledge-graph QA?"
            ),
        ),
    ]
    edges = [
        ResearchEdge(source=nodes[0].id, target=nodes[1].id, type="uses"),
        ResearchEdge(source=nodes[0].id, target=nodes[2].id, type="supports_claim"),
        ResearchEdge(source=nodes[5].id, target=nodes[4].id, type="references"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# Direct hybrid_search() unit tests
# ---------------------------------------------------------------------------


def test_hybrid_search_returns_reasonable_top_k_with_default_mode():
    graph = _eight_node_graph()
    result = hybrid_search(graph, "gaussian splatting", top_k=3, backend=HashEmbeddingBackend())

    assert result.mode == "hybrid"
    assert 1 <= len(result.scored) <= 3
    top_ids = [item.node.id for item in result.scored]
    # The two splatting-related nodes should both be in the top window.
    assert "MethodologicalConcept:gaussian-splatting" in top_ids
    assert "Paper:dual-splat" in top_ids
    # Sanity: scores strictly decreasing
    scores = [item.score for item in result.scored]
    assert scores == sorted(scores, reverse=True)


def test_bm25_lane_prefers_lexical_matches():
    graph = _eight_node_graph()
    result = hybrid_search(
        graph, "okapi bm25", top_k=5, backend=HashEmbeddingBackend(), mode="bm25"
    )
    top_ids = [item.node.id for item in result.scored]
    assert top_ids[0] == "MethodologicalConcept:bm25"


def test_lexical_and_legacy_modes_match_substring_behaviour():
    graph = _eight_node_graph()
    lex = hybrid_search(graph, "obsidian", top_k=5, backend=HashEmbeddingBackend(), mode="lexical")
    legacy_ids = {item.node.id for item in lex.scored}
    assert "Concept:obsidian-vault" in legacy_ids


def test_embedding_lane_returns_something_for_paraphrase():
    """The hash backend has no semantics, but the embedding lane still has to
    *run* and produce a deterministic ranking that is non-empty."""
    graph = _eight_node_graph()
    result = hybrid_search(
        graph,
        "fusing search results from multiple ranked lists",
        top_k=5,
        backend=HashEmbeddingBackend(),
        mode="embedding",
    )
    assert len(result.scored) >= 1
    # Re-running yields the same ordering (determinism guarantee).
    again = hybrid_search(
        graph,
        "fusing search results from multiple ranked lists",
        top_k=5,
        backend=HashEmbeddingBackend(),
        mode="embedding",
    )
    assert [s.node.id for s in result.scored] == [s.node.id for s in again.scored]


def test_modes_produce_distinguishable_orderings():
    """Different lanes should favour different docs for the same query,
    otherwise the fusion is buying us nothing."""
    graph = _eight_node_graph()
    query = "ranking baseline used in search engines"
    backend = HashEmbeddingBackend()
    bm25_top = [s.node.id for s in hybrid_search(graph, query, top_k=8, backend=backend, mode="bm25").scored]
    lex_top = [s.node.id for s in hybrid_search(graph, query, top_k=8, backend=backend, mode="lexical").scored]
    emb_top = [s.node.id for s in hybrid_search(graph, query, top_k=8, backend=backend, mode="embedding").scored]
    hyb_top = [s.node.id for s in hybrid_search(graph, query, top_k=8, backend=backend, mode="hybrid").scored]

    # At least one ordering must differ from another — proves the lanes are
    # independent signals rather than three copies of the same scorer.
    orderings = {tuple(bm25_top), tuple(lex_top), tuple(emb_top), tuple(hyb_top)}
    assert len(orderings) >= 2


def test_empty_query_returns_first_top_k_without_failing():
    graph = _eight_node_graph()
    result = hybrid_search(graph, "", top_k=4, backend=HashEmbeddingBackend())
    assert len(result.scored) == 4
    assert [s.node.id for s in result.scored] == [n.id for n in graph.nodes[:4]]


def test_weights_override_disables_a_lane():
    graph = _eight_node_graph()
    # Force bm25-only via weights; result should mirror the bm25 mode for a
    # query that BM25 actually scores positively.
    res_weighted = hybrid_search(
        graph,
        "okapi bm25",
        top_k=3,
        backend=HashEmbeddingBackend(),
        weights={"bm25": 1.0, "lexical": 0.0, "embedding": 0.0},
    )
    res_bm25 = hybrid_search(
        graph, "okapi bm25", top_k=3, backend=HashEmbeddingBackend(), mode="bm25"
    )
    assert [s.node.id for s in res_weighted.scored] == [s.node.id for s in res_bm25.scored]


def test_unknown_mode_raises():
    graph = _eight_node_graph()
    with pytest.raises(ValueError):
        hybrid_search(graph, "x", mode="nope", backend=HashEmbeddingBackend())


# ---------------------------------------------------------------------------
# MCP integration tests
# ---------------------------------------------------------------------------


def _server_with_fixture(tmp_path) -> LLMWikiMCPServer:
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(_eight_node_graph().to_json(indent=2), encoding="utf-8")
    return LLMWikiMCPServer(default_graph_path=graph_path)


def test_mcp_search_nodes_default_mode_is_hybrid(tmp_path):
    server = _server_with_fixture(tmp_path)
    result = server.call_tool("search_nodes", {"q": "gaussian splatting"})
    assert result["mode"] == "hybrid"
    assert result["total_matches"] >= 1
    # Public contract preserved.
    assert {"query", "total_matches", "nodes"}.issubset(result.keys())
    for node in result["nodes"]:
        assert "id" in node and "name" in node and "type" in node


def test_mcp_search_nodes_legacy_mode_matches_old_substring_contract(tmp_path):
    server = _server_with_fixture(tmp_path)
    result = server.call_tool(
        "search_nodes",
        {"query": "3dgs shape", "types": ["MethodologicalConcept", "PerformanceClaim"], "mode": "legacy"},
    )
    names = [node["name"] for node in result["nodes"]]
    assert names == ["Gaussian Splatting", "Best shape reconstruction claim"]
    assert result["mode"] == "legacy"


def test_mcp_search_nodes_mode_changes_ordering(tmp_path):
    server = _server_with_fixture(tmp_path)
    # "splatting reconstruction" — BM25 weights "reconstruction" highly (rare
    # term in this corpus); lexical / legacy only counts term-presence so it
    # rewards both splatting docs equally and resolves ties by node order.
    bm25 = server.call_tool("search_nodes", {"q": "splatting reconstruction", "mode": "bm25", "limit": 8})
    lex = server.call_tool("search_nodes", {"q": "splatting reconstruction", "mode": "legacy", "limit": 8})
    bm25_ids = [n["id"] for n in bm25["nodes"]]
    lex_ids = [n["id"] for n in lex["nodes"]]
    # Both lanes should surface splatting-related material.
    assert any("splat" in nid.lower() for nid in bm25_ids)
    # The orderings or the candidate sets should differ — proves the lanes
    # are genuinely independent signals, not just two copies of one scorer.
    assert bm25_ids != lex_ids or set(bm25_ids) != set(lex_ids)


def test_mcp_embedding_status_tool(tmp_path):
    server = _server_with_fixture(tmp_path)
    status = server.call_tool("embedding_status", {})
    assert status["available"] is True
    assert isinstance(status["backend"], str) and status["backend"]
    assert "hybrid" in status["modes"] and "legacy" in status["modes"]


def test_mcp_search_nodes_tool_listed_with_mode(tmp_path):
    server = _server_with_fixture(tmp_path)
    tools = {tool["name"]: tool for tool in server.list_tools()}
    assert "embedding_status" in tools
    schema = tools["search_nodes"]["inputSchema"]["properties"]
    assert "mode" in schema
    assert schema["mode"]["enum"] == ["hybrid", "bm25", "lexical", "embedding", "legacy"]


def test_active_backend_resolver_returns_something(tmp_path):
    backend = active_embedding_backend()
    assert backend is not None
    assert hasattr(backend, "embed")
    sample = backend.embed(["hello world"])
    assert len(sample) == 1
    assert all(isinstance(x, float) for x in sample[0])


# ---------------------------------------------------------------------------
# Codex review fixes (3xP2) — regression tests
# ---------------------------------------------------------------------------


def test_partial_weight_override_only_disables_named_lane():
    """A caller passing ``weights={"embedding": 0}`` should disable *only*
    embeddings — BM25 and lexical must retain their defaults so the hybrid
    candidate-generation gate still has lexical evidence to admit results.

    Regression for codex P2: previously ``selected_weights`` was initialized
    from the override dict directly, so omitted lanes silently got weight 0
    and the gate found no candidates → empty results.
    """
    graph = _eight_node_graph()
    result = hybrid_search(
        graph,
        "gaussian splatting",
        top_k=5,
        backend=HashEmbeddingBackend(),
        weights={"embedding": 0},
    )
    # Must return real results — BM25 + lexical still active.
    assert len(result.scored) >= 1
    top_ids = [item.node.id for item in result.scored]
    assert "MethodologicalConcept:gaussian-splatting" in top_ids
    # The merged weights must show the override applied on top of defaults.
    assert result.weights["embedding"] == 0
    assert result.weights["bm25"] > 0
    assert result.weights["lexical"] > 0


def test_total_matches_reports_pre_slice_candidate_count(tmp_path):
    """``total_matches`` must reflect every candidate that survived the
    candidate-generation gate, not just the page size returned to the caller.

    Regression for codex P2: the MCP server previously set
    ``total_matches = len(nodes_out)`` (the limit-bounded slice), which hid
    the real match count from clients implementing pagination.
    """
    server = _server_with_fixture(tmp_path)
    # "splatting" hits both Paper:dual-splat and MethodologicalConcept:
    # gaussian-splatting (plus the PerformanceClaim that mentions DualSplat
    # via the description). Cap the page below that count.
    full = server.call_tool("search_nodes", {"q": "splatting", "limit": 100})
    expected_total = full["total_matches"]
    assert expected_total >= 2, "fixture must produce >=2 splatting matches"

    paged = server.call_tool("search_nodes", {"q": "splatting", "limit": 1})
    assert len(paged["nodes"]) == 1  # page size honoured
    assert paged["total_matches"] == expected_total  # but total is unbounded


def test_active_embedding_backend_is_cached_across_calls():
    """``active_embedding_backend()`` must memoise its result so the
    expensive ``SentenceTransformer`` model load only happens once per
    process. ``reset_embedding_backend()`` should clear the cache for tests.

    Regression for codex P2: previously each default-mode ``search_nodes``
    call constructed a fresh backend, reloading hundreds of MB of weights.
    """
    reset_embedding_backend()
    first = active_embedding_backend()
    second = active_embedding_backend()
    assert first is second  # identity, not just equality

    # The reset helper must drop the cache so tests that swap deps work.
    reset_embedding_backend()
    third = active_embedding_backend()
    assert third is not first  # post-reset yields a fresh instance
    # And the fresh resolution is itself memoised.
    assert active_embedding_backend() is third
    # Restore cache hygiene for any later tests in the suite.
    reset_embedding_backend()

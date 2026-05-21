"""Tests for tesserae.retrieval.ppr.personalized_pagerank."""

from __future__ import annotations

import pytest

from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.retrieval.ppr import (
    DEFAULT_EDGE_TYPE_WEIGHTS,
    personalized_pagerank,
)


def _make_graph() -> ResearchGraph:
    """5-node fixture: a Session with two Insights and one Decision, plus
    an isolated Paper that nothing points to.

    Topology (undirected for PPR purposes):

        session ---(derived_from_session)--- insight_a
        session ---(derived_from_session)--- insight_b
        insight_a -(references)--- decision
        paper (disconnected)
    """

    nodes = [
        ResearchNode(id="session", name="Session 1", type=ResearchNodeType.SESSION),
        ResearchNode(
            id="insight_a", name="Insight A", type=ResearchNodeType.SESSION_INSIGHT
        ),
        ResearchNode(
            id="insight_b", name="Insight B", type=ResearchNodeType.SESSION_INSIGHT
        ),
        ResearchNode(
            id="decision", name="Decision 1", type=ResearchNodeType.SESSION_DECISION
        ),
        ResearchNode(id="paper", name="Orphan Paper", type=ResearchNodeType.PAPER),
    ]
    edges = [
        ResearchEdge(source="insight_a", target="session", type="derived_from_session"),
        ResearchEdge(source="insight_b", target="session", type="derived_from_session"),
        ResearchEdge(source="insight_a", target="decision", type="references"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_ppr_scores_sum_to_approximately_one() -> None:
    graph = _make_graph()
    ranked = personalized_pagerank(graph, seed_ids=["insight_a"], top_k=10)
    total = sum(score for _node_id, score in ranked)
    assert total == pytest.approx(1.0, abs=1e-3)


def test_seed_is_top_ranked() -> None:
    graph = _make_graph()
    ranked = personalized_pagerank(graph, seed_ids=["insight_a"], top_k=5)
    assert ranked, "expected non-empty ranking"
    assert ranked[0][0] == "insight_a", f"seed not first: {ranked}"


def test_connected_nodes_outrank_disconnected_paper() -> None:
    graph = _make_graph()
    ranked = personalized_pagerank(graph, seed_ids=["insight_a"], top_k=10)
    by_id = {node_id: score for node_id, score in ranked}
    # The disconnected ``paper`` node must not appear at all — PPR mass
    # never reached it. (See ``test_top_k_excludes_unreachable_zero_score_nodes``
    # for the explicit regression on this behavior.)
    assert "paper" not in by_id, f"disconnected paper leaked into results: {by_id}"
    # Every reachable node should have positive score.
    for connected in ("insight_a", "session", "decision", "insight_b"):
        assert by_id.get(connected, 0.0) > 0.0, (
            f"{connected} missing or zero in results: {by_id}"
        )


def test_unknown_seed_returns_empty() -> None:
    graph = _make_graph()
    assert personalized_pagerank(graph, seed_ids=["does-not-exist"]) == []


def test_multi_seed_balances_mass_across_components() -> None:
    graph = _make_graph()
    multi = personalized_pagerank(
        graph, seed_ids=["insight_a", "paper"], top_k=5
    )
    by_id = {node_id: score for node_id, score in multi}
    # Both seeds should be in the top ranks because mass starts there.
    assert by_id["insight_a"] > 0.0
    assert by_id["paper"] > 0.0


def test_default_edge_type_weights_upweight_session_edges() -> None:
    # Spec quality bar: defaults must favor session-finding edges so PPR
    # from an Insight tends to revisit related Insights/Decisions/Sessions.
    assert DEFAULT_EDGE_TYPE_WEIGHTS["derived_from_session"] > 1.0
    assert DEFAULT_EDGE_TYPE_WEIGHTS["references"] > 1.0


def test_top_k_truncates_results() -> None:
    graph = _make_graph()
    ranked = personalized_pagerank(graph, seed_ids=["insight_a"], top_k=2)
    assert len(ranked) == 2


def test_top_k_excludes_unreachable_zero_score_nodes() -> None:
    """Regression for codex P2: when ``top_k`` exceeds the seed's connected
    component, the disconnected ``paper`` node would be returned with a
    score of 0.0. PPR must only return nodes that actually received mass.
    """
    graph = _make_graph()
    # top_k (10) > seed-component size (4: insight_a, session, insight_b,
    # decision); the orphan ``paper`` is unreachable and must be excluded.
    ranked = personalized_pagerank(graph, seed_ids=["insight_a"], top_k=10)
    node_ids_returned = {node_id for node_id, _score in ranked}
    assert "paper" not in node_ids_returned, (
        f"unreachable disconnected node leaked into results: {ranked}"
    )
    assert all(score > 0.0 for _node_id, score in ranked), (
        f"zero-score node returned: {ranked}"
    )
    # Reachable component size is 4; we must return exactly those 4 even
    # though ``top_k`` was 10.
    assert len(ranked) == 4


# -- MCP tool wiring ---------------------------------------------------------


def _write_graph_json(tmp_path, graph: ResearchGraph):
    path = tmp_path / "graph.json"
    path.write_text(graph.to_json(), encoding="utf-8")
    return path


def test_graph_ppr_listed_in_mcp_tool_registry() -> None:
    from tesserae.mcp_server import LLMWikiMCPServer

    tools = LLMWikiMCPServer().list_tools()
    names = {tool["name"] for tool in tools}
    assert "graph_ppr" in names
    ppr_tool = next(tool for tool in tools if tool["name"] == "graph_ppr")
    assert "seed_node_id" in ppr_tool["inputSchema"]["properties"]
    assert ppr_tool["inputSchema"]["required"] == ["seed_node_id"]


def test_graph_ppr_mcp_call_returns_ranked_results(tmp_path) -> None:
    from tesserae.mcp_server import LLMWikiMCPServer

    graph_path = _write_graph_json(tmp_path, _make_graph())
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    payload = server.call_tool(
        "graph_ppr", {"seed_node_id": "insight_a", "top_k": 5}
    )

    assert payload["seed_ids"] == ["insight_a"]
    assert payload["results"], "expected non-empty results"
    assert payload["results"][0]["node_id"] == "insight_a"
    # Each result carries the decorated metadata an agent needs.
    for item in payload["results"]:
        assert {"node_id", "name", "type", "score"} <= set(item)


def test_graph_ppr_mcp_call_accepts_list_seeds(tmp_path) -> None:
    from tesserae.mcp_server import LLMWikiMCPServer

    graph_path = _write_graph_json(tmp_path, _make_graph())
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    payload = server.call_tool(
        "graph_ppr",
        {"seed_node_id": ["insight_a", "decision"], "top_k": 3},
    )
    assert sorted(payload["seed_ids"]) == ["decision", "insight_a"]
    assert len(payload["results"]) == 3


def test_graph_ppr_mcp_schema_excludes_zero_alpha() -> None:
    """Regression for codex P3: schema must declare ``alpha > 0`` so MCP
    clients can't pass ``alpha: 0`` past the contract."""
    from tesserae.mcp_server import LLMWikiMCPServer

    tools = LLMWikiMCPServer().list_tools()
    ppr_tool = next(tool for tool in tools if tool["name"] == "graph_ppr")
    alpha_schema = ppr_tool["inputSchema"]["properties"]["alpha"]
    assert alpha_schema.get("exclusiveMinimum") == 0.0, (
        f"alpha schema must use exclusiveMinimum=0 (not inclusive minimum=0): "
        f"{alpha_schema}"
    )
    # And inclusive ``minimum: 0`` must be gone so the contract is unambiguous.
    assert "minimum" not in alpha_schema or alpha_schema["minimum"] > 0


def test_graph_ppr_mcp_call_preserves_explicit_alpha(tmp_path) -> None:
    """Regression for codex P3: an explicit ``alpha=0.05`` must not be
    silently swapped for the 0.15 default by ``alpha or 0.15``."""
    from tesserae.mcp_server import LLMWikiMCPServer

    graph_path = _write_graph_json(tmp_path, _make_graph())
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    payload_low = server.call_tool(
        "graph_ppr",
        {"seed_node_id": "insight_a", "top_k": 5, "alpha": 0.05},
    )
    payload_default = server.call_tool(
        "graph_ppr",
        {"seed_node_id": "insight_a", "top_k": 5},
    )
    # With a much smaller teleport probability the walk wanders further from
    # the seed, so the seed's own score must be strictly lower than it is
    # under the default alpha=0.15. If alpha were silently overridden the
    # two payloads would be identical.
    seed_low = next(
        item["score"] for item in payload_low["results"]
        if item["node_id"] == "insight_a"
    )
    seed_default = next(
        item["score"] for item in payload_default["results"]
        if item["node_id"] == "insight_a"
    )
    assert seed_low < seed_default, (
        f"explicit alpha=0.05 appears to have been overridden: "
        f"seed_low={seed_low}, seed_default={seed_default}"
    )


def test_graph_ppr_mcp_call_rejects_zero_alpha(tmp_path) -> None:
    """Regression for codex P3: ``alpha=0`` must be rejected end-to-end,
    not silently coerced to the default."""
    from tesserae.mcp_server import LLMWikiMCPServer

    graph_path = _write_graph_json(tmp_path, _make_graph())
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    with pytest.raises(ValueError, match="alpha"):
        server.call_tool(
            "graph_ppr",
            {"seed_node_id": "insight_a", "top_k": 5, "alpha": 0},
        )

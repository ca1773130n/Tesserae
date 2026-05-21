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
    # Every reachable node should beat the disconnected Paper.
    for connected in ("insight_a", "session", "decision", "insight_b"):
        assert by_id[connected] > by_id["paper"], (
            f"{connected} did not outrank disconnected paper: {by_id}"
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

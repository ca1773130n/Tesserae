"""Phase 05-05: MCP suppression of superseded nodes + access bump on read.

Covers KB-03 (search_nodes/node_context/fresh_insights all suppress the
loser of a ``supersedes`` edge by default, with an ``include_superseded``
escape hatch) and KB-02 (node reads atomically bump access_count in the
node_memory SQLite sidecar, never graph.json).
"""

import pytest

from tesserae.mcp_server import LLMWikiMCPServer, _superseded_ids
from tesserae.memory.store import read_memory
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


def _supersede_graph():
    """winner --supersedes--> loser; loser is the suppressed target."""
    winner = ResearchNode(
        id="SessionInsight:winner",
        name="Current insight about caching",
        type=ResearchNodeType.SESSION_INSIGHT,
        description="The up-to-date understanding of the cache layer",
    )
    loser = ResearchNode(
        id="SessionInsight:loser",
        name="Stale insight about caching",
        type=ResearchNodeType.SESSION_INSIGHT,
        description="An obsolete understanding of the cache layer",
    )
    graph = ResearchGraph(
        nodes=[winner, loser],
        edges=[
            ResearchEdge(
                source=winner.id,
                target=loser.id,
                type="supersedes",
                evidence="winner replaces loser",
            )
        ],
    )
    return graph, winner, loser


def _write_project(tmp_path):
    graph, winner, loser = _supersede_graph()
    tesserae_dir = tmp_path / ".tesserae"
    tesserae_dir.mkdir(parents=True, exist_ok=True)
    graph_path = tesserae_dir / "graph.json"
    graph_path.write_text(graph.to_json(indent=2), encoding="utf-8")
    return graph_path, winner, loser


def test_superseded_ids_returns_edge_targets():
    graph, _winner, loser = _supersede_graph()
    assert _superseded_ids(graph) == {loser.id}


def test_search_nodes_suppresses_superseded_by_default(tmp_path):
    graph_path, winner, loser = _write_project(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool("search_nodes", {"query": "caching", "mode": "legacy"})
    ids = {node["id"] for node in result["nodes"]}
    assert winner.id in ids
    assert loser.id not in ids


def test_search_nodes_include_superseded_returns_loser(tmp_path):
    graph_path, winner, loser = _write_project(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool(
        "search_nodes",
        {"query": "caching", "mode": "legacy", "include_superseded": True},
    )
    ids = {node["id"] for node in result["nodes"]}
    assert {winner.id, loser.id}.issubset(ids)


def test_node_context_filters_superseded_neighbors(tmp_path):
    graph_path, winner, loser = _write_project(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    context = server.call_tool("node_context", {"node_id": winner.id})
    neighbor_ids = {n["id"] for n in context["neighbors"]}
    assert loser.id not in neighbor_ids

    context_all = server.call_tool(
        "node_context", {"node_id": winner.id, "include_superseded": True}
    )
    neighbor_ids_all = {n["id"] for n in context_all["neighbors"]}
    assert loser.id in neighbor_ids_all


def test_node_context_returns_requested_superseded_node_flagged(tmp_path):
    graph_path, _winner, loser = _write_project(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    # Caller asked for the loser explicitly: it is still returned, flagged.
    context = server.call_tool("node_context", {"node_id": loser.id})
    assert context["node"]["id"] == loser.id
    assert context["node"]["superseded"] is True


def test_node_context_bumps_access_count_atomically(tmp_path):
    graph_path, winner, _loser = _write_project(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    db_path = tmp_path / ".tesserae" / "sqlite.db"

    for _ in range(3):
        server.call_tool("node_context", {"node_id": winner.id})

    rows = read_memory(db_path)
    assert rows[winner.id].access_count == 3


def test_node_context_degrades_without_project_root():
    # In-memory default (no project root) must not raise on read.
    graph, winner, _loser = _supersede_graph()

    class _Store:
        def materialize(self):
            return graph

    # Construct server with no graph path/root; call node_context directly
    # with project_root=None to assert graceful degradation.
    server = LLMWikiMCPServer()
    result = server.node_context(graph, None, node_id=winner.id)
    assert result["node"]["id"] == winner.id

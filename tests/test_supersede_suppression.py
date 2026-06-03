"""KB-03 consumer side: search_nodes + node_context suppress superseded losers.

A ``supersedes`` edge points winner -> loser (``target`` == the superseded
loser, the orientation shared by ``_superseded_ids`` and fresh_insights).
By default both ``search_nodes`` and ``node_context`` omit the loser; the
``include_superseded=True`` escape hatch returns it.

Deterministic: a hand-minted graph on disk, ``legacy`` search mode (pure
substring, no embeddings/wall-clock), no LLM.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


def _insight(node_id: str, name: str) -> ResearchNode:
    return ResearchNode(
        id=f"SessionInsight:{node_id}",
        name=name,
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"session_id": "sess-1"},
    )


@pytest.fixture
def server(tmp_path: Path) -> LLMWikiMCPServer:
    winner = _insight("winner", "Atomic writes need a PID plus random tmp suffix")
    loser = _insight("loser", "Atomic writes need PID plus random suffix for tmp")
    hub = _insight("hub", "Atomic write guidance hub")

    graph = ResearchGraph(
        nodes=[winner, loser, hub],
        edges=[
            # winner supersedes loser: target == loser (the suppressed node).
            ResearchEdge(
                source=winner.id,
                target=loser.id,
                type="supersedes",
                metadata={"kind": "SessionInsight"},
            ),
            # hub neighbours BOTH winner and loser so node_context(hub) can show
            # which neighbour is filtered.
            ResearchEdge(source=hub.id, target=winner.id, type="mentioned_in"),
            ResearchEdge(source=hub.id, target=loser.id, type="mentioned_in"),
        ],
    )
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(graph.to_json(), encoding="utf-8")
    return LLMWikiMCPServer(default_graph_path=graph_path)


def _graph(server: LLMWikiMCPServer) -> ResearchGraph:
    return server._load_graph_cached(server.default_graph_path)


def test_search_nodes_default_excludes_superseded(server: LLMWikiMCPServer) -> None:
    graph = _graph(server)
    payload = server.search_nodes(graph, query="atomic writes", mode="legacy")
    ids = {n["id"] for n in payload["nodes"]}
    assert "SessionInsight:winner" in ids
    assert "SessionInsight:loser" not in ids


def test_search_nodes_include_superseded_returns_loser(
    server: LLMWikiMCPServer,
) -> None:
    graph = _graph(server)
    payload = server.search_nodes(
        graph, query="atomic writes", mode="legacy", include_superseded=True
    )
    ids = {n["id"] for n in payload["nodes"]}
    assert "SessionInsight:loser" in ids
    assert "SessionInsight:winner" in ids


def test_node_context_default_filters_superseded_neighbor(
    server: LLMWikiMCPServer,
) -> None:
    graph = _graph(server)
    payload = server.node_context(graph, node_id="SessionInsight:hub")
    neighbor_ids = {n["id"] for n in payload["neighbors"]}
    assert "SessionInsight:winner" in neighbor_ids
    assert "SessionInsight:loser" not in neighbor_ids


def test_node_context_include_superseded_keeps_neighbor(
    server: LLMWikiMCPServer,
) -> None:
    graph = _graph(server)
    payload = server.node_context(
        graph, node_id="SessionInsight:hub", include_superseded=True
    )
    neighbor_ids = {n["id"] for n in payload["neighbors"]}
    assert "SessionInsight:loser" in neighbor_ids


def test_node_context_returns_requested_superseded_node_flagged(
    server: LLMWikiMCPServer,
) -> None:
    # Asking for the loser BY ID always returns it, flagged superseded=True;
    # only NEIGHBOURS are filtered.
    graph = _graph(server)
    payload = server.node_context(graph, node_id="SessionInsight:loser")
    assert payload["node"]["id"] == "SessionInsight:loser"
    assert payload["node"]["superseded"] is True

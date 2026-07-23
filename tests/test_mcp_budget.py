"""CTX-01 budget enforcement on MCP read tools (§5.3).

``search_nodes`` / ``node_context`` clamp each returned ``model_dump`` payload
to the per-entry cap (``budget_chars // 8``); ``search_facts`` / ``timeline``
clamp per-fact evidence blocks. ``node_context``'s edges array is additionally
greedily admitted against ``budget_chars`` (both the default and ``use_ppr``
paths — per-item evidence clamps alone cannot bound a hub's edge payload),
reporting drops via ``edges_continuation`` so the neighbours' ``continuation``
line keeps its format. ``budget_chars=0`` is the uncapped passthrough
everywhere, and dropping items yields exactly one ``+N more, cursor=K``
continuation per list.
"""

from __future__ import annotations

import json

from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

_LONG = "evidence tokens repeated for budget pressure " * 1_000  # ~44k chars


def _graph_path(tmp_path):
    hub = ResearchNode(
        id="Concept:hub",
        name="Budget Hub",
        type=ResearchNodeType.CONCEPT,
        description="hub node " + _LONG,
    )
    nodes = [hub]
    edges = []
    for i in range(12):
        node = ResearchNode(
            id=f"Concept:spoke-{i:02d}",
            name=f"Budget Spoke {i:02d}",
            type=ResearchNodeType.CONCEPT,
            description=f"spoke {i:02d} " + _LONG,
        )
        nodes.append(node)
        edges.append(
            ResearchEdge(
                source=hub.id,
                target=node.id,
                type="shares_concept_with",
                evidence=f"spoke {i:02d} evidence " + _LONG,
            )
        )
    graph = ResearchGraph(nodes=nodes, edges=edges)
    path = tmp_path / "graph.json"
    path.write_text(graph.to_json(indent=2), encoding="utf-8")
    return path


def _server(tmp_path) -> LLMWikiMCPServer:
    return LLMWikiMCPServer(default_graph_path=_graph_path(tmp_path))


def _item_size(item) -> int:
    return len(json.dumps(item, ensure_ascii=False, default=str))


def test_search_nodes_clamps_each_item_to_per_entry_cap(tmp_path) -> None:
    server = _server(tmp_path)
    budget = 16_000
    result = server.call_tool(
        "search_nodes",
        {"query": "budget", "mode": "legacy", "limit": 50, "budget_chars": budget},
    )
    assert result["nodes"], "expected matches"
    for item in result["nodes"]:
        assert _item_size(item) <= budget // 8
        assert item["description"].endswith("…[truncated]")


def test_search_nodes_budget_zero_is_uncapped(tmp_path) -> None:
    server = _server(tmp_path)
    result = server.call_tool(
        "search_nodes",
        {"query": "budget", "mode": "legacy", "limit": 50, "budget_chars": 0},
    )
    assert result["nodes"]
    assert "continuation" not in result
    assert any(_LONG in item["description"] for item in result["nodes"])


def test_search_nodes_drops_overflow_with_continuation(tmp_path) -> None:
    server = _server(tmp_path)
    # 13 matches, per-entry cap 1000 -> only a prefix fits under 8000 - reserve.
    result = server.call_tool(
        "search_nodes",
        {"query": "budget", "mode": "legacy", "limit": 50, "budget_chars": 8_000},
    )
    kept = len(result["nodes"])
    assert 0 < kept < 13
    assert result["continuation"] == f"+{13 - kept} more, cursor={kept}"
    # total_matches still reports the true pre-drop match count.
    assert result["total_matches"] == 13


def test_node_context_clamps_focal_node_and_neighbors(tmp_path) -> None:
    server = _server(tmp_path)
    budget = 16_000
    context = server.call_tool(
        "node_context", {"node_id": "Concept:hub", "budget_chars": budget}
    )
    assert _item_size(context["node"]) <= budget // 8
    for neighbor in context["neighbors"]:
        assert _item_size(neighbor) <= budget // 8


def test_node_context_drops_neighbors_with_continuation(tmp_path) -> None:
    server = _server(tmp_path)
    context = server.call_tool(
        "node_context", {"node_id": "Concept:hub", "budget_chars": 8_000}
    )
    kept = len(context["neighbors"])
    assert 0 < kept < 12
    assert context["continuation"] == f"+{12 - kept} more, cursor={kept}"


def test_node_context_budget_zero_is_uncapped(tmp_path) -> None:
    server = _server(tmp_path)
    context = server.call_tool(
        "node_context", {"node_id": "Concept:hub", "budget_chars": 0}
    )
    assert _LONG in context["node"]["description"]
    assert len(context["neighbors"]) == 12
    assert "continuation" not in context
    # Edges pass through uncapped too: all present, evidence untruncated.
    assert len(context["edges"]) == 12
    assert all(_LONG in edge["evidence"] for edge in context["edges"])
    assert "edges_continuation" not in context


def test_node_context_edges_admitted_against_budget(tmp_path) -> None:
    """CTX-01: the edges array is greedily admitted against ``budget_chars``,
    not merely per-item clamped — 12 long-evidence edges must NOT all survive
    an 8k budget."""
    server = _server(tmp_path)
    budget = 8_000
    context = server.call_tool(
        "node_context", {"node_id": "Concept:hub", "budget_chars": budget}
    )
    kept = len(context["edges"])
    assert 0 < kept < 12
    for edge in context["edges"]:
        assert _item_size(edge) <= budget // 8
    assert sum(_item_size(edge) for edge in context["edges"]) <= budget
    assert context["edges_continuation"] == f"+{12 - kept} more, cursor={kept}"


def test_node_context_ppr_edges_admitted_against_budget(tmp_path) -> None:
    """The ``use_ppr`` path rebuilds incident edges from the FULL edge list
    over the selected neighbour set; those edges obey the same greedy
    admission as the default path."""
    server = _server(tmp_path)
    budget = 8_000
    context = server.call_tool(
        "node_context",
        {"node_id": "Concept:hub", "use_ppr": True, "budget_chars": budget},
    )
    kept = len(context["edges"])
    assert 0 < kept < 12
    for edge in context["edges"]:
        assert _item_size(edge) <= budget // 8
    assert sum(_item_size(edge) for edge in context["edges"]) <= budget
    assert context["edges_continuation"] == f"+{12 - kept} more, cursor={kept}"


def test_search_facts_truncates_evidence_blocks(tmp_path) -> None:
    server = _server(tmp_path)
    budget = 16_000
    result = server.call_tool(
        "search_facts", {"query": "spoke", "limit": 100, "budget_chars": budget}
    )
    assert result["facts"], "expected facts"
    for fact in result["facts"]:
        assert _item_size(fact) <= budget // 8
        assert fact["evidence"].endswith("…[truncated]")


def test_timeline_truncates_events_and_reports_continuation(tmp_path) -> None:
    server = _server(tmp_path)
    result = server.call_tool(
        "timeline", {"query": "spoke", "limit": 200, "budget_chars": 8_000}
    )
    kept = len(result["events"])
    assert 0 < kept < 12
    assert result["continuation"] == f"+{12 - kept} more, cursor={kept}"
    for event in result["events"]:
        assert _item_size(event) <= 8_000 // 8


def test_timeline_budget_zero_is_uncapped(tmp_path) -> None:
    server = _server(tmp_path)
    result = server.call_tool(
        "timeline", {"query": "spoke", "limit": 200, "budget_chars": 0}
    )
    assert len(result["events"]) == 12
    assert "continuation" not in result
    assert all(_LONG in event["evidence"] for event in result["events"])


def test_budget_props_declared_in_tool_schemas() -> None:
    tools = {tool["name"]: tool for tool in LLMWikiMCPServer().list_tools()}
    for name in ("search_nodes", "node_context", "search_facts", "timeline"):
        props = tools[name]["inputSchema"]["properties"]
        assert props["budget_chars"]["type"] == "integer"

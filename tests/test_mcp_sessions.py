"""Smoke tests for the two session-graph MCP tools.

Builds a tiny in-memory graph with a Session, a Paper, two findings
that reference the Paper, and one finding that doesn't. Confirms that
``list_sessions`` and ``find_session_findings`` return the expected
payloads. Uses the MCP server's internal tool-dispatch path
(``call_tool``) so we exercise the same code path the JSON-RPC layer
hits in production.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


def _fixture_graph() -> ResearchGraph:
    """Session + 1 Paper + 3 findings (2 reference the Paper, 1 doesn't)."""
    paper = ResearchNode(
        id="Paper:foo",
        name="Foo Paper",
        type=ResearchNodeType.PAPER,
        source_path="docs/foo.md",
    )
    session = ResearchNode(
        id="Session:sess-A",
        name="2026-05-19 — paper deep dive",
        type=ResearchNodeType.SESSION,
        metadata={
            "session_id": "sess-A",
            "started_at": "2026-05-19T10:00:00Z",
            "ended_at": "2026-05-19T11:00:00Z",
            "title": "Paper deep dive",
            "harness": "claude-code",
            "model": "claude-sonnet-4-7",
            "files_touched": ["docs/foo.md"],
        },
    )
    insight = ResearchNode(
        id="SessionInsight:sess-A:insight:abc12345abcd",
        name="Foo Paper makes assumption X about Y",
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"session_id": "sess-A", "turn_ids": [3, 5], "extractor": "session-llm"},
    )
    decision = ResearchNode(
        id="SessionDecision:sess-A:decision:def67890dead",
        name="Use atomic writes everywhere",
        type=ResearchNodeType.SESSION_DECISION,
        metadata={"session_id": "sess-A", "turn_ids": [7], "extractor": "session-llm"},
    )
    takeaway = ResearchNode(
        id="SessionTakeaway:sess-A:takeaway:1111aaaa2222",
        name="Path indexes need multi-key fallbacks",
        type=ResearchNodeType.SESSION_TAKEAWAY,
        metadata={"session_id": "sess-A", "turn_ids": [9], "extractor": "session-llm"},
    )
    edges = [
        # Paper was discussed during this session.
        ResearchEdge(source="Paper:foo", target="Session:sess-A", type="discussed_in"),
        # Insight + Decision reference the Paper directly.
        ResearchEdge(source=insight.id, target="Paper:foo", type="references"),
        ResearchEdge(source=decision.id, target="Paper:foo", type="references"),
        # All three findings derive from the session.
        ResearchEdge(source=insight.id, target="Session:sess-A", type="derived_from_session"),
        ResearchEdge(source=decision.id, target="Session:sess-A", type="derived_from_session"),
        ResearchEdge(source=takeaway.id, target="Session:sess-A", type="derived_from_session"),
    ]
    return ResearchGraph(
        nodes=[paper, session, insight, decision, takeaway],
        edges=edges,
    )


def _server(tmp_path: Path, graph: ResearchGraph) -> LLMWikiMCPServer:
    """Construct an MCP server pinned to a graph file we write to tmp_path."""
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(graph.to_json(indent=2), encoding="utf-8")
    return LLMWikiMCPServer(default_graph_path=graph_path)


def test_list_sessions_returns_envelope_with_finding_counts(tmp_path: Path):
    server = _server(tmp_path, _fixture_graph())
    result = server.call_tool("list_sessions", {})
    assert result["total"] == 1
    [envelope] = result["sessions"]
    assert envelope["session_id"] == "sess-A"
    assert envelope["title"] == "Paper deep dive"
    assert envelope["harness"] == "claude-code"
    counts = envelope["finding_counts"]
    # 1 insight + 1 decision + 1 takeaway
    assert counts.get("SessionInsight") == 1
    assert counts.get("SessionDecision") == 1
    assert counts.get("SessionTakeaway") == 1


def test_list_sessions_limit_is_honored(tmp_path: Path):
    server = _server(tmp_path, _fixture_graph())
    result = server.call_tool("list_sessions", {"limit": 0})  # 0 → clamped to 1
    assert len(result["sessions"]) == 1
    assert result["total"] == 1


def test_find_session_findings_returns_all_findings_for_paper(tmp_path: Path):
    server = _server(tmp_path, _fixture_graph())
    result = server.call_tool(
        "find_session_findings",
        {"node_id": "Paper:foo"},
    )
    # All 3 findings come back: 2 via direct `references`, 1 via the
    # broader `discussed_in` recall path.
    kinds = sorted(f["kind"] for f in result["findings"])
    assert kinds == ["SessionDecision", "SessionInsight", "SessionTakeaway"]
    # The 2 that directly reference the Paper are flagged.
    direct = sorted(f["kind"] for f in result["findings"] if f["directly_references_node"])
    assert direct == ["SessionDecision", "SessionInsight"]


def test_find_session_findings_kind_filter(tmp_path: Path):
    server = _server(tmp_path, _fixture_graph())
    result = server.call_tool(
        "find_session_findings",
        {"node_id": "Paper:foo", "kinds": ["decision"]},
    )
    kinds = [f["kind"] for f in result["findings"]]
    assert kinds == ["SessionDecision"]


def test_find_session_findings_requires_node_id(tmp_path: Path):
    server = _server(tmp_path, _fixture_graph())
    with pytest.raises(ValueError, match="node_id"):
        server.call_tool("find_session_findings", {})

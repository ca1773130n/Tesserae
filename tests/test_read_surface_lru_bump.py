"""Regression: LRU access bump on the ``find_session_findings`` and
``graph_ppr`` read surfaces.

Both tools return finding nodes but historically skipped the sidecar access
bump, so an agent that repeatedly read old findings through them never
refreshed ``last_accessed_at`` — the next ``distill_agent`` LRU pass scored
those findings by creation age and absorbed/demoted them despite active use
(the exact forgetting-by-disuse the LRU core must prevent). These tests
exercise the real ``call_tool`` dispatch path and assert the bump now lands,
while ``graph.json`` stays byte-identical (access state is sidecar-only).
"""

from __future__ import annotations

from pathlib import Path

from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.memory.store import read_memory
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

PAPER_ID = "Paper:foo"
INSIGHT_ID = "SessionInsight:sess-A:insight:abc12345abcd"
DECISION_ID = "SessionDecision:sess-A:decision:def67890dead"


def _fixture_graph() -> ResearchGraph:
    """A Paper discussed in a Session, plus two findings referencing it."""
    paper = ResearchNode(
        id=PAPER_ID,
        name="Foo Paper on atomic writes",
        type=ResearchNodeType.PAPER,
        description="A paper about atomic writes and durability.",
        source_path="docs/foo.md",
    )
    session = ResearchNode(
        id="Session:sess-A",
        name="2026-05-19 — paper deep dive",
        type=ResearchNodeType.SESSION,
        metadata={"session_id": "sess-A", "started_at": "2026-05-19T10:00:00Z"},
    )
    insight = ResearchNode(
        id=INSIGHT_ID,
        name="Foo Paper assumes atomic writes everywhere",
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"session_id": "sess-A", "turn_ids": [3], "extractor": "session-llm"},
    )
    decision = ResearchNode(
        id=DECISION_ID,
        name="Use atomic writes everywhere for durability",
        type=ResearchNodeType.SESSION_DECISION,
        metadata={"session_id": "sess-A", "turn_ids": [7], "extractor": "session-llm"},
    )
    edges = [
        ResearchEdge(source=PAPER_ID, target="Session:sess-A", type="discussed_in"),
        ResearchEdge(source=INSIGHT_ID, target=PAPER_ID, type="references"),
        ResearchEdge(source=DECISION_ID, target=PAPER_ID, type="references"),
        ResearchEdge(source=INSIGHT_ID, target="Session:sess-A", type="derived_from_session"),
        ResearchEdge(source=DECISION_ID, target="Session:sess-A", type="derived_from_session"),
    ]
    return ResearchGraph(nodes=[paper, session, insight, decision], edges=edges)


def _project(tmp_path: Path) -> tuple[LLMWikiMCPServer, Path, Path]:
    root = tmp_path
    tess = root / ".tesserae"
    tess.mkdir(parents=True, exist_ok=True)
    graph_path = tess / "graph.json"
    graph_path.write_text(_fixture_graph().to_json(indent=2), encoding="utf-8")
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    return server, root, graph_path


def _access(root: Path) -> dict:
    db = root / ".tesserae" / "sqlite.db"
    if not db.exists():
        return {}
    return read_memory(db)


def test_find_session_findings_bumps_surfaced_findings(tmp_path: Path):
    server, root, _ = _project(tmp_path)
    result = server.call_tool("find_session_findings", {"node_id": PAPER_ID})
    surfaced = {f["node_id"] for f in result["findings"]}
    # Both findings are reachable (direct `references` + `derived_from_session`).
    assert surfaced == {INSIGHT_ID, DECISION_ID}

    rows = _access(root)
    for node_id in surfaced:
        assert node_id in rows, f"surfaced finding {node_id} was not access-bumped"
        assert rows[node_id].access_count >= 1
        assert rows[node_id].last_accessed_at is not None


def test_graph_ppr_bumps_ranked_results(tmp_path: Path):
    server, root, _ = _project(tmp_path)
    result = server.call_tool("graph_ppr", {"seed_node_id": PAPER_ID, "top_k": 20})
    returned = {r["node_id"] for r in result["results"]}
    assert returned, "graph_ppr should rank at least one node"

    rows = _access(root)
    for node_id in returned:
        assert node_id in rows, f"ranked {node_id} was not access-bumped"
        assert rows[node_id].access_count >= 1


def test_neither_surface_touches_graph_json(tmp_path: Path):
    server, root, graph_path = _project(tmp_path)
    before = graph_path.read_bytes()
    server.call_tool("find_session_findings", {"node_id": PAPER_ID})
    server.call_tool("graph_ppr", {"seed_node_id": PAPER_ID})
    # graph.json is byte-identical: all access state lives in the sidecar.
    assert graph_path.read_bytes() == before
    assert _access(root), "sidecar rows were written (access state lives there)"

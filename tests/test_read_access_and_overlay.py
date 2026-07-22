"""Wire-reads coverage: LRU access recording + discovered-link overlay.

Two consolidation cores are wired into the MCP read surfaces here:

* **(A) LRU access recording** — every meaningful read surface bumps
  ``access_count`` / ``last_accessed_at`` in the ``node_memory`` SQLite
  sidecar for the nodes it actually surfaces, so unretrieved findings decay
  while recently-read ones stay alive. Writes go to the sidecar ONLY, never
  ``graph.json``.
* **(B) discovered-connections overlay** — the accumulated
  ``discovered_links.json`` overlay is merged (in-memory) at the shared
  graph-load point, so discovered ``shares_concept_with`` edges are
  traversable by every read tool without ever mutating ``graph.json``.

These tests exercise the real ``call_tool`` dispatch path so they hit the
same code the JSON-RPC layer runs in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.mcp_server import LLMWikiMCPServer, _hit_node_ids
from tesserae.memory.associate import SHARES_CONCEPT_EDGE, persist_links
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
    """A Paper + a Session + two findings that reference the Paper."""
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
    return ResearchGraph(
        nodes=[paper, session, insight, decision],
        edges=edges,
    )


def _project(tmp_path: Path) -> tuple[LLMWikiMCPServer, Path, Path]:
    """Write graph.json into the canonical ``<root>/.tesserae/`` layout.

    Returns ``(server, project_root, graph_path)``. The canonical layout is
    what lets the server resolve a project root (and therefore the sidecar
    db) from ``default_graph_path``.
    """
    root = tmp_path
    tess = root / ".tesserae"
    tess.mkdir(parents=True, exist_ok=True)
    graph_path = tess / "graph.json"
    graph_path.write_text(_fixture_graph().to_json(indent=2), encoding="utf-8")
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    return server, root, graph_path


def _access(root: Path) -> dict:
    """Return ``{node_id: NodeMemoryRow}`` from the sidecar (empty if absent)."""
    db = root / ".tesserae" / "sqlite.db"
    if not db.exists():
        return {}
    return read_memory(db)


# --------------------------------------------------------------------------- #
# (A) LRU access recording per read surface                                   #
# --------------------------------------------------------------------------- #


def test_search_nodes_bumps_access_for_returned_nodes(tmp_path: Path):
    server, root, _ = _project(tmp_path)
    result = server.call_tool("search_nodes", {"query": "atomic writes", "limit": 10})
    returned = {n["id"] for n in result["nodes"]}
    assert returned, "search should surface at least one node"

    rows = _access(root)
    for node_id in returned:
        assert node_id in rows, f"{node_id} was returned but not access-bumped"
        assert rows[node_id].access_count >= 1
        assert rows[node_id].last_accessed_at is not None


def test_node_context_bumps_access_for_primary_and_neighbors(tmp_path: Path):
    server, root, _ = _project(tmp_path)
    server.call_tool("node_context", {"node_id": PAPER_ID})

    rows = _access(root)
    # The primary node and at least one neighbor surfaced by the context.
    assert rows.get(PAPER_ID) is not None
    assert rows[PAPER_ID].access_count >= 1
    assert INSIGHT_ID in rows or DECISION_ID in rows


def test_compile_context_bumps_selected_nodes(tmp_path: Path):
    server, root, _ = _project(tmp_path)
    result = server.call_tool(
        "compile_context", {"query": "atomic writes", "depth": 2}
    )
    selected = set(result["selected_node_ids"])
    assert selected, "compile_context should select at least one node"

    rows = _access(root)
    for node_id in selected:
        assert node_id in rows, f"selected {node_id} was not access-bumped"
        assert rows[node_id].access_count >= 1


def test_drill_down_bumps_the_resolved_node(tmp_path: Path):
    server, root, _ = _project(tmp_path)
    result = server.call_tool("drill_down", {"node_id": PAPER_ID})
    assert result["status"] != "gone"  # node is present in L0

    rows = _access(root)
    assert rows.get(PAPER_ID) is not None
    assert rows[PAPER_ID].access_count >= 1


def test_fresh_insights_bumps_surfaced_findings(tmp_path: Path):
    server, root, _ = _project(tmp_path)
    result = server.call_tool("fresh_insights", {"limit": 10})
    surfaced = {f["node_id"] for f in result["findings"]}
    assert surfaced, "fresh_insights should surface the session findings"

    rows = _access(root)
    for node_id in surfaced:
        assert rows.get(node_id) is not None
        assert rows[node_id].access_count >= 1


def test_access_recording_never_touches_graph_json(tmp_path: Path):
    server, root, graph_path = _project(tmp_path)
    before = graph_path.read_bytes()
    server.call_tool("search_nodes", {"query": "atomic writes"})
    server.call_tool("node_context", {"node_id": PAPER_ID})
    server.call_tool("compile_context", {"query": "atomic writes"})
    server.call_tool("fresh_insights", {})
    # graph.json is byte-identical: all access state lives in the sidecar.
    assert graph_path.read_bytes() == before
    assert _access(root), "sidecar rows were written (access state lives there)"


def test_hit_node_ids_extracts_and_degrades():
    # Extracts node_id off each hit; drops hits without one; tolerates junk.
    assert _hit_node_ids({"hits": [{"node_id": "a"}, {"node_id": None}, {"x": 1}]}) == ["a"]
    assert _hit_node_ids({"hits": []}) == []
    assert _hit_node_ids({}) == []
    assert _hit_node_ids(None) == []
    assert _hit_node_ids({"hits": "nope"}) == []


def test_bump_nodes_access_is_best_effort_and_dedups(tmp_path: Path):
    server, root, _ = _project(tmp_path)
    # A None/empty project root is a silent no-op (never raises).
    server._bump_nodes_access(None, [PAPER_ID])
    assert _access(root) == {}

    # Duplicate ids and falsy entries are handled; only one bump lands.
    server._bump_nodes_access(root, [PAPER_ID, PAPER_ID, "", None])
    rows = _access(root)
    assert rows[PAPER_ID].access_count == 1


# --------------------------------------------------------------------------- #
# (B) discovered-connections overlay merged into read views                   #
# --------------------------------------------------------------------------- #


def test_overlay_edge_is_absent_without_a_seeded_overlay(tmp_path: Path):
    server, _root, _ = _project(tmp_path)
    graph, root = server._load_base_graph_with_root({})
    assert root is not None
    assert not any(e.type == SHARES_CONCEPT_EDGE for e in graph.edges)


def test_seeded_overlay_adds_edges_to_read_view_without_touching_graph_json(
    tmp_path: Path,
):
    server, root, graph_path = _project(tmp_path)
    before = graph_path.read_bytes()

    # Seed the accumulating overlay with a discovered link between two nodes
    # that both exist in the graph (the persist path the daemon uses).
    persist_links(root, [(PAPER_ID, INSIGHT_ID, 0.91)])

    graph, resolved_root = server._load_base_graph_with_root({})
    assert resolved_root == root
    overlay_edges = [e for e in graph.edges if e.type == SHARES_CONCEPT_EDGE]
    assert len(overlay_edges) == 1
    edge = overlay_edges[0]
    assert (edge.source, edge.target) == (PAPER_ID, INSIGHT_ID)
    # The overlay marker rides along so surfaces can tell it from a real edge.
    assert edge.metadata.get("associate_overlay") is True
    assert edge.metadata.get("federation_semantic") is True

    # graph.json is byte-identical — the overlay is a read-time projection only.
    assert graph_path.read_bytes() == before


def test_overlay_with_absent_endpoint_is_skipped(tmp_path: Path):
    server, root, _ = _project(tmp_path)
    # A link to a node id that isn't in this graph must not fabricate an edge.
    persist_links(root, [(PAPER_ID, "Paper:not-in-graph", 0.99)])
    graph, _ = server._load_base_graph_with_root({})
    assert not any(e.type == SHARES_CONCEPT_EDGE for e in graph.edges)


def test_overlay_edge_is_traversable_by_search(tmp_path: Path):
    """A seeded overlay makes the extra edge visible to a public read tool."""
    server, root, _ = _project(tmp_path)
    persist_links(root, [(PAPER_ID, INSIGHT_ID, 0.91)])
    summary = server.call_tool("graph_summary", {})
    # graph_summary counts the overlaid edge among the traversable edges.
    edge_types = summary.get("edge_type_counts") or summary.get("edge_types") or {}
    if isinstance(edge_types, dict):
        assert edge_types.get(SHARES_CONCEPT_EDGE, 0) >= 1
    else:
        # Fallback: total edge count grew by the overlay edge.
        assert summary.get("edge_count", 0) >= 6

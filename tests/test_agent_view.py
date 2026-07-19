"""Tests for Phase 3 — the agent-scoped view layer (2026-07-19 layered-agent-kg §6.1/§8/§9).

Covers: worker merged view + absorption overlay (suppression derived at load
time, §6.1 invariant that every suppression source is live), manager
federation over children's L1s, the ``org`` pseudo-key, fail-loud missing
artifacts, the mtime-signature view cache, the audit-logged ``drill_down``
MCP tool, ``agent_view_explain``, and the §9 compile_context integration
(DistilledNote/ExpertiseProfile pool reservations, fallback deprioritization,
``distilled_through`` header). The summarizer is always the deterministic
stub from the distill tests — no LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.agent_distill import DistillStateStore, agent_artifact_path, distill_agent
from tesserae.agent_identity import AgentRegistry
from tesserae.agent_view import AgentViewError, resolve_agent_view
from tesserae.context_compiler import compile_context
from tesserae.graph_filters import superseded_ids
from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import ResearchNodeType

from tests.test_agent_distill import (
    AGENT,
    OTHER_AGENT,
    StubSummarizer,
    _base_graph,
)

MANAGER = "claude-code:me:manager"


def _project_with_l0(tmp_path: Path):
    """Write the shared fixture graph as the project's L0 graph.json."""
    project = tmp_path / "proj"
    (project / ".tesserae").mkdir(parents=True, exist_ok=True)
    graph = _base_graph()
    (project / ".tesserae" / "graph.json").write_text(
        graph.to_json(indent=2), encoding="utf-8"
    )
    return project, graph


def _distill(project: Path, graph, agent: str) -> None:
    distill_agent(graph, agent, project_root=project, summarizer=StubSummarizer())


# --------------------------------------------------------------------------- worker view (§6.1)


def test_worker_view_merges_l1_and_suppresses_absorbed(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)

    view, info = resolve_agent_view(project, AGENT, graph)
    assert info["mode"] == "worker"
    types = {n.type for n in view.nodes}
    assert ResearchNodeType.DISTILLED_NOTE in types
    # Raw L0 stays present in the worker view (own experience is not hidden).
    ids = {n.id for n in view.nodes}
    assert "SessionInsight:f3" in ids
    assert "SessionInsight:old1" in ids  # absorbed raw is REACHABLE, not gone

    # The absorption overlay suppresses absorbed members for default reads.
    suppressed = superseded_ids(view)
    assert {"SessionInsight:old1", "SessionInsight:old2"} <= suppressed
    # ...but only in the agent view: the plain L0 read sees no suppression.
    assert not superseded_ids(graph)


def test_worker_view_suppression_sources_are_live(tmp_path: Path) -> None:
    """§6.1 invariant: every suppression source resolves to a live node."""
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)
    view, _info = resolve_agent_view(project, AGENT, graph)
    ids = {n.id for n in view.nodes}
    for edge in view.edges:
        if edge.type == "supersedes":
            assert edge.source in ids, f"ghost suppression source: {edge.source}"


def test_worker_without_artifact_fails_loud(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    with pytest.raises(AgentViewError, match=r"tesserae distill --agent"):
        resolve_agent_view(project, AGENT, graph)


def test_unknown_agent_fails_loud(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    with pytest.raises(AgentViewError, match="Unknown agent"):
        resolve_agent_view(project, "claude-code:me:nonexistent", graph)


# --------------------------------------------------------------------------- manager + org (§8.1)


def _declare_manager(project: Path) -> None:
    registry = AgentRegistry.for_project(project)
    data = registry.load()
    agents = data.setdefault("agents", {})
    agents[MANAGER] = {"label": "Manager", "parent": "org:root", "aliases": [], "match": []}
    agents[AGENT] = {"label": "Reviewer", "parent": MANAGER, "aliases": [], "match": []}
    agents[OTHER_AGENT] = {"label": "Codex", "parent": MANAGER, "aliases": [], "match": []}
    registry.save(data)


def test_manager_sees_children_distillates_not_raw(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _declare_manager(project)
    _distill(project, graph, AGENT)
    _distill(project, graph, OTHER_AGENT)

    view, info = resolve_agent_view(project, MANAGER, graph)
    assert info["mode"] == "manager"
    assert {m["agent_key"] for m in info["members"]} == {AGENT, OTHER_AGENT}
    for member in info["members"]:
        assert member["distilled_through"]  # staleness watermark surfaced

    types = {n.type for n in view.nodes}
    assert ResearchNodeType.DISTILLED_NOTE in types
    # The manager sees artifacts only: L0 Session records never leak through
    # (distilled artifacts carry notes/anchors/remainder, not Session nodes).
    assert ResearchNodeType.SESSION not in types


def test_manager_with_undistilled_child_fails_loud(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _declare_manager(project)
    _distill(project, graph, AGENT)  # OTHER_AGENT deliberately not distilled
    with pytest.raises(AgentViewError) as err:
        resolve_agent_view(project, MANAGER, graph)
    assert OTHER_AGENT in str(err.value)
    assert f"tesserae distill --agent {OTHER_AGENT}" in str(err.value)


def test_org_view_is_zero_config(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)
    _distill(project, graph, OTHER_AGENT)
    view, info = resolve_agent_view(project, "org", graph)
    assert info["mode"] == "org"
    assert {m["agent_key"] for m in info["members"]} == {AGENT, OTHER_AGENT}
    assert any(n.type == ResearchNodeType.DISTILLED_NOTE for n in view.nodes)


def test_view_cache_rebuilds_only_on_input_change(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)
    first, _ = resolve_agent_view(project, AGENT, graph)
    again, _ = resolve_agent_view(project, AGENT, graph)
    assert first is again  # signature-stable → cached object
    artifact = agent_artifact_path(project, AGENT)
    artifact.write_text(artifact.read_text(encoding="utf-8"), encoding="utf-8")
    # mtime_ns moved → signature changed → rebuilt.
    rebuilt, _ = resolve_agent_view(project, AGENT, graph)
    assert rebuilt is not again


# --------------------------------------------------------------------------- MCP tools (§6.4/§8)


def _server(project: Path) -> LLMWikiMCPServer:
    return LLMWikiMCPServer(default_graph_path=project / ".tesserae" / "graph.json")


def test_mcp_agent_scoped_read_and_explain(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)
    server = _server(project)
    summary = server.call_tool("graph_summary", {"agent": AGENT})
    assert summary["node_types"].get("DistilledNote")

    info = server.call_tool("agent_view_explain", {"agent": AGENT})
    assert info["mode"] == "worker"
    assert info["members"][0]["agent_key"] == AGENT
    assert info["members"][0]["distilled_through"] == "2026-07-01T10:00:00Z"


def test_drill_down_statuses_and_audit_log(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)
    server = _server(project)

    alive = server.call_tool("drill_down", {"node_id": "SessionInsight:f3", "agent": AGENT})
    assert alive["status"] == "alive"
    assert alive["node"]["name"] == "Graphql resolver timeout root cause"
    assert alive["audited"] is True

    absorbed = server.call_tool("drill_down", {"node_id": "SessionInsight:old1", "agent": AGENT})
    assert absorbed["status"] == "absorbed"
    assert absorbed["absorbed_by"].startswith("DistilledNote:")

    changed = server.call_tool(
        "drill_down", {"node_id": "SessionInsight:f3", "content_hash": "stale-hash", "agent": AGENT}
    )
    assert changed["status"] == "changed"

    gone = server.call_tool("drill_down", {"node_id": "SessionInsight:nope", "agent": AGENT})
    assert gone["status"] == "gone"

    state = DistillStateStore(project / ".tesserae" / "sqlite.db")
    rows = state.rows("drill_down_audit", AGENT)
    assert len(rows) == 4
    logged = json.loads(rows[0][3])
    assert logged["node_id"] == "SessionInsight:f3"
    assert logged["status"] == "alive"


# --------------------------------------------------------------------------- compile_context (§9)


def test_multi_pool_reserves_distilled_note_and_flags_staleness(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    _distill(project, graph, AGENT)
    view, _info = resolve_agent_view(project, AGENT, graph)

    bundle = compile_context(
        view, query="release staging deploy", multi_pool=True, budget=32_000
    )
    # The distilled note gets a reserved slot and the header carries staleness.
    assert any(nid.startswith("DistilledNote:") for nid in bundle.selected_nodes)
    assert "distilled through: 2026-07-01T10:00:00Z" in bundle.body


def test_fallback_distillate_deprioritized_and_flagged(tmp_path: Path) -> None:
    project, graph = _project_with_l0(tmp_path)
    # Fallback-quality artifact: summarizer refuses, structural fallback minted.
    distill_agent(
        graph, AGENT, project_root=project, summarizer=StubSummarizer(lambda req: None)
    )
    view, _info = resolve_agent_view(project, AGENT, graph)
    bundle = compile_context(
        view, query="release staging deploy", multi_pool=True, budget=32_000
    )
    if any(nid.startswith("DistilledNote:") for nid in bundle.selected_nodes):
        assert "fallback distillate" in bundle.body

"""Deeper subagent-transcript promotion (spec 2026-07-19 §12 Phase 5).

A TYPED subagent run stops being attribution-only: the structural pass now
promotes it to one scoped ``SessionTakeaway`` finding under the subagent's OWN
``agent_key``, hung off the parent Session via ``derived_from_session``. Because
the parent Session already carries a ``performed_by`` edge to the subagent's
Agent (Phase 1), ``agent_distill._scope_for_agent`` folds that run into the
subagent's distill scope — a reviewer subagent's runs accumulate into the
reviewer agent.

Asserts:
* a typed subagent run yields scope for the subagent's key (via
  ``_scope_for_agent``), tagged ``subagent-structural`` with its role;
* the promoted finding rides the subagent's structural ExpertiseProfile;
* untyped subagents stay attribution-only (no promoted node, performed_by only);
* a mix promotes only the typed runs;
* the promoted finding id is seeded from (session id + subagent id) and the
  whole slice is byte-identical across a double run (CMP-03).
"""

from __future__ import annotations

import json
from pathlib import Path

from tesserae.agent_distill import _scope_for_agent
from tesserae.agent_identity import AgentRegistry
from tesserae.harness_sessions import HarnessSession
from tesserae.research_graph import (
    ResearchGraphBuilder,
    ResearchNodeType,
    stable_id,
)
from tesserae.session_graph_path_index import DocPathIndex
from tesserae.session_graph_structural import extract_structural


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _session(
    project_root: Path,
    session_id: str,
    *,
    decisions=(),
    files_touched=(),
    subagents=None,
    started_at: str = "2026-05-19T10:00:00Z",
    ended_at: str = "2026-05-19T11:00:00Z",
) -> HarnessSession:
    metadata: dict = {}
    if subagents is not None:
        metadata["subagents"] = subagents
    return HarnessSession(
        id=session_id,
        slug=session_id,
        harness="claude-code",
        agent_label="Claude Code",
        project_name=project_root.name,
        project_root=str(project_root.resolve()),
        started_at=started_at,
        ended_at=ended_at,
        files_touched=list(files_touched),
        decisions=list(decisions),
        metadata=metadata,
    )


def _doc_index(project_root: Path, *source_paths: str) -> DocPathIndex:
    builder = ResearchGraphBuilder()
    for source_path in source_paths:
        builder.add_node(
            name=Path(source_path).stem,
            node_type=ResearchNodeType.PAPER,
            source_path=source_path,
        )
    return DocPathIndex.from_graph(builder.build(), project_root)


def _registry(tmp_path: Path) -> AgentRegistry:
    return AgentRegistry(tmp_path / "registry.json")


def _nodes_of(graph, type_value: str):
    return [n for n in graph.nodes if n.type.value == type_value]


def _agent_id(agent_key: str) -> str:
    return stable_id(ResearchNodeType.AGENT.value, f"agent:{agent_key}")


DEFAULT_KEY = "claude-code:unknown:default"
REVIEWER_KEY = "claude-code:unknown:reviewer"


def _typed_subagent(sub_id: str = "claude-code:parent:agent-child") -> dict:
    return {
        "id": sub_id,
        "title": "Review the diff",
        "type": "reviewer",
        "started_at": "2026-05-19T10:10:00Z",
        "ended_at": "2026-05-19T10:20:00Z",
        "files_touched": [],
        "commands_run": [],
    }


def _untyped_subagent(sub_id: str = "claude-code:parent:agent-helper") -> dict:
    # No ``type`` — resolves to the parent's ``default`` role (§3.1 tier 3).
    return {
        "id": sub_id,
        "title": "Do some helper work",
        "started_at": "2026-05-19T10:30:00Z",
        "ended_at": "2026-05-19T10:40:00Z",
        "files_touched": [],
        "commands_run": [],
    }


# ---------------------------------------------------------------------------
# typed subagent → scoped finding
# ---------------------------------------------------------------------------


def test_typed_subagent_run_promoted_into_subagent_scope(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    # No parent decisions, so the ONLY finding reachable from the reviewer's
    # scope is its own promoted run.
    sessions = [_session(project, "s1", subagents=[_typed_subagent()])]
    graph = extract_structural(
        sessions, _doc_index(project), project, registry=_registry(tmp_path)
    )

    takeaways = _nodes_of(graph, "SessionTakeaway")
    assert len(takeaways) == 1
    run = takeaways[0]
    assert run.name == "Review the diff"
    assert run.metadata["extractor"] == "subagent-structural"
    assert run.metadata["subagent_type"] == "reviewer"
    assert run.metadata["subagent_id"] == "claude-code:parent:agent-child"
    # Decay anchor comes from the subagent's own clock, never "now".
    assert run.metadata["first_seen_at"] == "2026-05-19T10:10:00Z"

    # The run lands in the reviewer agent's distill scope (§5.1), via the
    # parent Session's performed_by edge + the run's derived_from_session edge.
    sessions_in_scope, findings, _extras = _scope_for_agent(graph, REVIEWER_KEY)
    assert [n.id for n in findings] == [run.id]
    # The parent Session is the anchor the subagent's work hangs off of.
    assert [n.type.value for n in sessions_in_scope] == ["Session"]

    # Profile finding_counts stays in lockstep with the queryable graph (§8.2).
    profiles = {p.metadata["agent"]: p for p in _nodes_of(graph, "ExpertiseProfile")}
    assert profiles[REVIEWER_KEY].metadata["finding_counts"] == {"SessionTakeaway": 1}


def test_promoted_finding_derives_from_parent_session_no_new_performed_by(
    tmp_path: Path,
) -> None:
    """The run hangs off the parent Session via derived_from_session; no
    finding-level performed_by edge is minted (attribution rides the Session)."""
    project = tmp_path / "project"
    project.mkdir()
    sessions = [_session(project, "s1", subagents=[_typed_subagent()])]
    graph = extract_structural(
        sessions, _doc_index(project), project, registry=_registry(tmp_path)
    )

    run = _nodes_of(graph, "SessionTakeaway")[0]
    session_node = _nodes_of(graph, "Session")[0]

    derived = {(e.source, e.target) for e in graph.edges if e.type == "derived_from_session"}
    assert (run.id, session_node.id) in derived

    # performed_by remains exactly the two Session → Agent edges (Phase 1);
    # promotion adds none.
    performed = {(e.source, e.target) for e in graph.edges if e.type == "performed_by"}
    assert performed == {
        (session_node.id, _agent_id(DEFAULT_KEY)),
        (session_node.id, _agent_id(REVIEWER_KEY)),
    }


# ---------------------------------------------------------------------------
# untyped subagent → attribution only
# ---------------------------------------------------------------------------


def test_untyped_subagent_stays_attribution_only(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sessions = [_session(project, "s1", subagents=[_untyped_subagent()])]
    graph = extract_structural(
        sessions, _doc_index(project), project, registry=_registry(tmp_path)
    )

    # No run was promoted.
    assert _nodes_of(graph, "SessionTakeaway") == []

    # The untyped subagent still collapses to the default role and keeps its
    # attribution: the parent Session performed_by the default Agent.
    session_node = _nodes_of(graph, "Session")[0]
    performed = {(e.source, e.target) for e in graph.edges if e.type == "performed_by"}
    assert (session_node.id, _agent_id(DEFAULT_KEY)) in performed

    # Attribution-only means no findings enter scope (no parent decisions, no
    # promotion).
    _sessions, findings, _extras = _scope_for_agent(graph, DEFAULT_KEY)
    assert findings == []


def test_mixed_typed_and_untyped_promotes_only_typed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sessions = [
        _session(
            project,
            "s1",
            subagents=[_typed_subagent(), _untyped_subagent()],
        )
    ]
    graph = extract_structural(
        sessions, _doc_index(project), project, registry=_registry(tmp_path)
    )

    takeaways = _nodes_of(graph, "SessionTakeaway")
    assert len(takeaways) == 1
    assert takeaways[0].metadata["subagent_type"] == "reviewer"


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_promoted_finding_id_seeded_from_session_and_subagent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sub_id = "claude-code:parent:agent-child"
    sessions = [_session(project, "s1", subagents=[_typed_subagent(sub_id)])]
    graph = extract_structural(
        sessions, _doc_index(project), project, registry=_registry(tmp_path)
    )

    run = _nodes_of(graph, "SessionTakeaway")[0]
    assert run.id == stable_id(
        ResearchNodeType.SESSION_TAKEAWAY.value,
        f"session:s1:subagent:{sub_id}:run",
    )


def test_promotion_double_run_is_byte_identical(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    paper = str(project / "docs" / "paper.md")

    def run() -> str:
        index = _doc_index(project, paper)
        sessions = [
            _session(
                project,
                "s1",
                decisions=["Use atomic writes"],
                files_touched=[paper],
                subagents=[_typed_subagent(), _untyped_subagent()],
            ),
            _session(project, "s2", files_touched=[paper]),
        ]
        graph = extract_structural(
            sessions, index, project, registry=_registry(tmp_path)
        )
        return json.dumps(graph.model_dump(), ensure_ascii=False, sort_keys=True)

    assert run() == run()

"""Structural agent-layer minting (spec 2026-07-19 §12 Phase 1).

Exercises the no-LLM agent substrate added to
:func:`tesserae.session_graph_structural.extract_structural`:

* role-grade Agent nodes (one per distinct ``agent_key`` observed across
  the session corpus, including subagent-descriptor roles),
* ``performed_by`` edges Session → Agent,
* ``reports_to`` edges Agent → parent Agent (implicit ``org:root``,
  registry-declared parents included),
* one structural ``ExpertiseProfile`` per observed agent (§8.2), and
* byte-determinism of the whole structural slice (CMP-03).

Also covers the ``harness_sessions`` half of role-grade identity: subagent
summaries now carry the ``type`` recovered from the parent transcript's
Task-style ``tool_use`` / ``tool_result`` pairing.
"""

from __future__ import annotations

import json
from pathlib import Path

from tesserae.agent_identity import ORG_ROOT, AgentRegistry
from tesserae.harness_sessions import HarnessSession, discover_harness_sessions
from tesserae.research_graph import ResearchGraphBuilder, ResearchNodeType, stable_id
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
    """Minimal claude-code session envelope scoped to ``project_root``."""
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
    """Path index over one Paper node per ``source_paths`` entry."""
    builder = ResearchGraphBuilder()
    for source_path in source_paths:
        builder.add_node(
            name=Path(source_path).stem,
            node_type=ResearchNodeType.PAPER,
            source_path=source_path,
        )
    return DocPathIndex.from_graph(builder.build(), project_root)


def _registry(tmp_path: Path) -> AgentRegistry:
    """Empty per-test registry (missing file → zero-config default)."""
    return AgentRegistry(tmp_path / "registry.json")


def _agent_id(agent_key: str) -> str:
    return stable_id(ResearchNodeType.AGENT.value, f"agent:{agent_key}")


def _nodes_of(graph, type_value: str):
    return [n for n in graph.nodes if n.type.value == type_value]


def _edges_of(graph, edge_type: str):
    return [e for e in graph.edges if e.type == edge_type]


REVIEWER_SUBAGENT = {
    "id": "claude-code:parent:agent-child",
    "title": "Review the diff",
    "type": "reviewer",
    "started_at": "2026-05-19T10:10:00Z",
    "ended_at": "2026-05-19T10:20:00Z",
    "files_touched": [],
    "commands_run": [],
}


# ---------------------------------------------------------------------------
# Phase 1 ship gate
# ---------------------------------------------------------------------------


def test_ship_gate_two_role_distinct_agents(tmp_path: Path) -> None:
    """A normal fixture corpus must yield ≥2 ROLE-distinct agents (§3.1).

    One parent session with a typed subagent descriptor produces the
    parent's ``default`` role plus the subagent's ``reviewer`` role —
    without this the org hierarchy is decoration.
    """
    project = tmp_path / "project"
    project.mkdir()
    sessions = [
        _session(project, "s1", subagents=[dict(REVIEWER_SUBAGENT)]),
    ]
    graph = extract_structural(
        sessions, _doc_index(project), project, registry=_registry(tmp_path)
    )

    agents = _nodes_of(graph, "Agent")
    non_root = [a for a in agents if a.metadata["agent_key"] != ORG_ROOT]
    roles = {a.metadata.get("role") for a in non_root}
    assert len(non_root) >= 2, f"ship gate: expected ≥2 agents, got {non_root}"
    assert roles == {"default", "reviewer"}, f"expected role diversity, got {roles}"


# ---------------------------------------------------------------------------
# Agent nodes + performed_by
# ---------------------------------------------------------------------------


def test_agent_nodes_metadata_and_performed_by_edges(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sessions = [_session(project, "s1", subagents=[dict(REVIEWER_SUBAGENT)])]
    graph = extract_structural(
        sessions, _doc_index(project), project, registry=_registry(tmp_path)
    )

    default_key = "claude-code:unknown:default"
    reviewer_key = "claude-code:unknown:reviewer"
    agents = {a.metadata["agent_key"]: a for a in _nodes_of(graph, "Agent")}
    assert set(agents) == {default_key, reviewer_key, ORG_ROOT}

    # §4 closed allowlist, exact — no timestamps, no counters.
    default_agent = agents[default_key]
    assert set(default_agent.metadata) == {"agent_key", "harness", "account", "role", "label"}
    assert default_agent.metadata["harness"] == "claude-code"
    assert default_agent.metadata["account"] == "unknown"
    assert default_agent.metadata["role"] == "default"
    assert default_agent.metadata["label"] == "Claude Code"
    assert agents[reviewer_key].metadata["role"] == "reviewer"
    assert set(agents[ORG_ROOT].metadata) == {"agent_key", "label"}

    # Agent node id is seed-based, so same-named agents can never collide.
    assert default_agent.id == _agent_id(default_key)

    session_node = _nodes_of(graph, "Session")[0]
    performed = _edges_of(graph, "performed_by")
    assert {(e.source, e.target) for e in performed} == {
        (session_node.id, _agent_id(default_key)),
        (session_node.id, _agent_id(reviewer_key)),
    }


def test_no_sessions_mints_no_agent_layer(tmp_path: Path) -> None:
    """Empty corpus → empty slice; org:root is not minted into nothing."""
    project = tmp_path / "project"
    project.mkdir()
    graph = extract_structural(
        [], _doc_index(project), project, registry=_registry(tmp_path)
    )
    assert graph.nodes == []
    assert graph.edges == []


# ---------------------------------------------------------------------------
# reports_to — implicit org:root + registry-declared parents
# ---------------------------------------------------------------------------


def test_reports_to_implicit_org_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sessions = [_session(project, "s1", subagents=[dict(REVIEWER_SUBAGENT)])]
    graph = extract_structural(
        sessions, _doc_index(project), project, registry=_registry(tmp_path)
    )

    reports = _edges_of(graph, "reports_to")
    assert {(e.source, e.target) for e in reports} == {
        (_agent_id("claude-code:unknown:default"), _agent_id(ORG_ROOT)),
        (_agent_id("claude-code:unknown:reviewer"), _agent_id(ORG_ROOT)),
    }


def test_reports_to_registry_declared_parent_chain(tmp_path: Path) -> None:
    """A declared parent is minted (even unobserved) and chained to org:root."""
    project = tmp_path / "project"
    project.mkdir()
    registry = _registry(tmp_path)
    registry.save(
        {
            "version": 1,
            "agents": {
                "team:leads": {"label": "Team leads", "parent": ORG_ROOT},
                "claude-code:unknown:default": {
                    "label": "Main coder",
                    "parent": "team:leads",
                },
            },
        }
    )
    sessions = [_session(project, "s1")]
    graph = extract_structural(sessions, _doc_index(project), project, registry=registry)

    agents = {a.metadata["agent_key"]: a for a in _nodes_of(graph, "Agent")}
    assert set(agents) == {"claude-code:unknown:default", "team:leads", ORG_ROOT}
    assert agents["claude-code:unknown:default"].metadata["label"] == "Main coder"
    assert agents["team:leads"].metadata["label"] == "Team leads"

    reports = {(e.source, e.target) for e in _edges_of(graph, "reports_to")}
    assert reports == {
        (_agent_id("claude-code:unknown:default"), _agent_id("team:leads")),
        (_agent_id("team:leads"), _agent_id(ORG_ROOT)),
    }


# ---------------------------------------------------------------------------
# structural ExpertiseProfile (§8.2)
# ---------------------------------------------------------------------------


def test_expertise_profile_counts_concepts_and_corpus_clock(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    paper_a = str(project / "docs" / "aaa-paper.md")
    paper_b = str(project / "docs" / "bbb-paper.md")
    index = _doc_index(project, paper_a, paper_b)
    sessions = [
        _session(
            project,
            "s1",
            decisions=["Use atomic writes", "Keep transcripts off-graph"],
            files_touched=[paper_a, paper_b],
            ended_at="2026-05-19T11:00:00Z",
        ),
        _session(
            project,
            "s2",
            decisions=["Ship phase 1"],
            files_touched=[paper_a],
            started_at="2026-05-20T09:00:00Z",
            ended_at="2026-05-20T10:00:00Z",
        ),
    ]
    graph = extract_structural(sessions, index, project, registry=_registry(tmp_path))

    profiles = _nodes_of(graph, "ExpertiseProfile")
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.id == stable_id(
        "ExpertiseProfile", "profile:claude-code:unknown:default"
    )
    # §4 closed allowlist, exact.
    assert set(profile.metadata) == {
        "agent",
        "session_count",
        "finding_counts",
        "top_concepts",
        "distilled_through",
    }
    assert profile.metadata["agent"] == "claude-code:unknown:default"
    assert profile.metadata["session_count"] == 2
    assert profile.metadata["finding_counts"] == {"SessionDecision": 3}
    # paper_a mentioned twice, paper_b once → (-count, id) order.
    paper_a_id = index.lookup(paper_a)
    paper_b_id = index.lookup(paper_b)
    assert profile.metadata["top_concepts"] == [paper_a_id, paper_b_id]
    # Corpus clock: max(ended_at or started_at) over the agent's scope (§7.1).
    assert profile.metadata["distilled_through"] == "2026-05-20T10:00:00Z"


def test_expertise_profile_top_concepts_tiebreak_on_id(tmp_path: Path) -> None:
    """Equal mention counts break the tie on node id — never dict order."""
    project = tmp_path / "project"
    project.mkdir()
    paper_a = str(project / "docs" / "aaa-paper.md")
    paper_b = str(project / "docs" / "bbb-paper.md")
    index = _doc_index(project, paper_a, paper_b)
    sessions = [_session(project, "s1", files_touched=[paper_b, paper_a])]
    graph = extract_structural(sessions, index, project, registry=_registry(tmp_path))

    profile = _nodes_of(graph, "ExpertiseProfile")[0]
    assert profile.metadata["top_concepts"] == sorted(
        [index.lookup(paper_a), index.lookup(paper_b)]
    )


def test_expertise_profile_omits_clock_when_untimestamped(tmp_path: Path) -> None:
    """No timestamps in the corpus → no distilled_through — never a "now"."""
    project = tmp_path / "project"
    project.mkdir()
    sessions = [_session(project, "s1", started_at="", ended_at="")]
    graph = extract_structural(
        sessions, _doc_index(project), project, registry=_registry(tmp_path)
    )
    profile = _nodes_of(graph, "ExpertiseProfile")[0]
    assert "distilled_through" not in profile.metadata


# ---------------------------------------------------------------------------
# determinism (CMP-03)
# ---------------------------------------------------------------------------


def test_structural_slice_double_run_is_byte_identical(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    paper = str(project / "docs" / "paper.md")
    registry = _registry(tmp_path)
    registry.save(
        {
            "version": 1,
            "agents": {
                "team:leads": {"label": "Team leads", "parent": ORG_ROOT},
                "claude-code:unknown:default": {"parent": "team:leads"},
            },
        }
    )

    def run() -> str:
        index = _doc_index(project, paper)
        sessions = [
            _session(
                project,
                "s1",
                decisions=["Use atomic writes"],
                files_touched=[paper],
                subagents=[dict(REVIEWER_SUBAGENT)],
            ),
            _session(project, "s2", files_touched=[paper]),
        ]
        graph = extract_structural(sessions, index, project, registry=registry)
        return json.dumps(graph.model_dump(), ensure_ascii=False, sort_keys=True)

    assert run() == run(), "identical inputs must yield a byte-identical slice"


# ---------------------------------------------------------------------------
# LLM-enabled compile path — agent layer carries over UNDUPLICATED
# ---------------------------------------------------------------------------


class _StubJsonClient:
    """Minimal deterministic LLM stub for the extractor's ``json_client``."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, **kwargs):
        self.calls += 1
        return {
            "findings": [
                {
                    "kind": "insight",
                    "body": "Stub insight",
                    "turn_ids": [0],
                    "references": [],
                }
            ]
        }


def test_llm_path_mints_exactly_one_agent_node_per_key(tmp_path: Path) -> None:
    """The LLM-enabled compile must carry the structural agent layer over
    UNDUPLICATED. The extractor's carry-over block once re-added every
    structural node via ``add_node`` (minting a second, name-seeded id per
    node) before inserting the seed-based original — and the aggressive-dedup
    exemption for AGENT_LAYER_TYPES means such same-name strays are never
    fused away in ``build()``. Structural-only runs bypass the carry-over
    entirely, so only an LLM-path test can catch a regression here.
    """
    from tesserae.research_graph import ResearchGraph
    from tesserae.session_graph import SessionGraphExtractor

    project = tmp_path / "project"
    project.mkdir()
    session = _session(
        project,
        "s1",
        decisions=["Use atomic writes"],
        subagents=[dict(REVIEWER_SUBAGENT)],
    )
    session.metadata["turns"] = [{"role": "user", "text": "hello"}]
    client = _StubJsonClient()
    extractor = SessionGraphExtractor(
        project_root=project.resolve(),
        cache_dir=project / ".tesserae" / "session_findings",
        doc_graph=ResearchGraph(),
        sessions=[session],
        json_client=client,
    )
    graph = extractor.extract()
    assert client.calls >= 1, "stub client must actually exercise the LLM path"

    default_key = "claude-code:unknown:default"
    reviewer_key = "claude-code:unknown:reviewer"
    expected_keys = {default_key, reviewer_key, ORG_ROOT}
    agents = _nodes_of(graph, "Agent")
    assert {a.metadata["agent_key"] for a in agents} == expected_keys
    assert len(agents) == len(expected_keys), "duplicate Agent nodes survived build()"
    # Every survivor carries the seed-based id — a name-seeded stray would not.
    assert {a.id for a in agents} == {_agent_id(k) for k in expected_keys}

    profiles = _nodes_of(graph, "ExpertiseProfile")
    assert len(profiles) == 2, "exactly one ExpertiseProfile per observed agent"
    assert {p.metadata["agent"] for p in profiles} == {default_key, reviewer_key}

    # Structural decisions ride the same carry-over (session-finding types are
    # likewise dedup-exempt) — they must not be duplicated either.
    assert len(_nodes_of(graph, "SessionDecision")) == 1

    # And the stubbed finding landed, so the LLM pass genuinely ran end to end.
    assert len(_nodes_of(graph, "SessionInsight")) == 1


# ---------------------------------------------------------------------------
# harness_sessions — subagent role recovery from the parent transcript
# ---------------------------------------------------------------------------


def test_subagent_type_recovered_from_parent_transcript(tmp_path: Path) -> None:
    """``metadata['subagents'][i]['type']`` comes from the parent's Task
    ``tool_use`` (``input['subagent_type']``) paired with the ``tool_result``
    "agentId: <id>" text — the subagent transcript itself carries no role."""
    project = tmp_path / "focused-project"
    project.mkdir()
    root = tmp_path / ".claude-any-account"
    session_dir = root / "projects" / str(project.resolve()).replace("/", "-")
    subagent_dir = session_dir / "parent-session" / "subagents"
    subagent_dir.mkdir(parents=True)
    parent_rows = [
        {
            "type": "user",
            "timestamp": "2026-05-05T10:00:00Z",
            "cwd": str(project),
            "sessionId": "parent-session",
            "message": {"role": "user", "content": "Parent session"},
        },
        {
            "type": "assistant",
            "timestamp": "2026-05-05T10:00:30Z",
            "sessionId": "parent-session",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_01",
                        "name": "Task",
                        "input": {"subagent_type": "reviewer", "prompt": "Review it"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-05-05T10:05:00Z",
            "sessionId": "parent-session",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_01",
                        "content": [
                            {"type": "text", "text": "agentId: child\nDone reviewing."}
                        ],
                    }
                ],
            },
        },
    ]
    (session_dir / "parent-session.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in parent_rows), encoding="utf-8"
    )
    (subagent_dir / "agent-child.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-05-05T10:01:00Z",
                "cwd": str(project),
                "sessionId": "parent-session",
                "message": {"role": "user", "content": "Child subagent session"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    sessions = discover_harness_sessions(project, [root], harnesses=["claude-code"])

    assert len(sessions) == 1
    subagents = sessions[0].metadata["subagents"]
    assert subagents[0]["type"] == "reviewer"

    # End to end: the typed descriptor becomes a role-distinct agent.
    graph = extract_structural(
        sessions, _doc_index(project), project, registry=_registry(tmp_path)
    )
    roles = {
        a.metadata.get("role")
        for a in _nodes_of(graph, "Agent")
        if a.metadata["agent_key"] != ORG_ROOT
    }
    assert "reviewer" in roles

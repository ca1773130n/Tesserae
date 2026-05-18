"""Tests for the structural (LLM-free) session graph extractor.

Asserts the deterministic invariants:
* Sessions outside ``project_root`` are filtered (privacy invariant).
* ``Session`` nodes mint correctly with sanitised envelope metadata.
* ``discussed_in`` edges resolve through the multi-key path index.
* ``decisions`` entries become ``SessionDecision`` nodes with
  ``derived_from_session`` edges.
* Two calls produce equal graphs (idempotence).
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from tesserae.harness_sessions import HarnessSession
from tesserae.research_graph import (
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.session_graph_path_index import DocPathIndex
from tesserae.session_graph_structural import extract_structural


def _doc_graph(project_root: Path) -> ResearchGraph:
    return ResearchGraph(
        nodes=[
            ResearchNode(
                id="Paper:abs",
                name="Paper Abs",
                type=ResearchNodeType.PAPER,
                source_path=str(
                    (project_root / "data/research/papers/abs/paper.md").resolve()
                ),
            ),
            ResearchNode(
                id="Paper:rel",
                name="Paper Rel",
                type=ResearchNodeType.PAPER,
                source_path="data/research/papers/rel/paper.md",
            ),
        ],
        edges=[],
    )


def _session(
    *,
    id: str,
    project_root: Path,
    files_touched: List[str],
    decisions: List[str] = (),
) -> HarnessSession:
    return HarnessSession(
        id=id,
        slug=id,
        harness="claude-code",
        agent_label="Claude Code",
        project_name="demo",
        project_root=str(Path(project_root).resolve()),
        started_at="2026-05-19T10:00:00Z",
        ended_at="2026-05-19T11:00:00Z",
        files_touched=list(files_touched),
        decisions=list(decisions),
    )


def test_in_project_session_mints_session_node(tmp_path: Path):
    doc_graph = _doc_graph(tmp_path)
    idx = DocPathIndex.from_graph(doc_graph, tmp_path)
    session = _session(
        id="sess-a",
        project_root=tmp_path,
        files_touched=[
            str((tmp_path / "data/research/papers/abs/paper.md").resolve()),
            "data/research/papers/rel/paper.md",
            "data/research/papers/missing/paper.md",  # unresolvable
        ],
        decisions=["Cache findings by content hash", "Skip stale caches"],
    )

    graph = extract_structural([session], idx, project_root=tmp_path)

    # 1 Session + 2 SessionDecision = 3 nodes
    types = sorted(n.type.value for n in graph.nodes)
    assert types == ["Session", "SessionDecision", "SessionDecision"]

    # 2 `discussed_in` edges (the missing path doesn't bind) + 2
    # `derived_from_session` edges from the decisions.
    edge_types = sorted(e.type for e in graph.edges)
    assert edge_types == [
        "derived_from_session",
        "derived_from_session",
        "discussed_in",
        "discussed_in",
    ]

    # Session metadata is sanitised — must include session_id, must NOT
    # include raw_transcript_path.
    session_node = next(n for n in graph.nodes if n.type == ResearchNodeType.SESSION)
    assert session_node.metadata["session_id"] == "sess-a"
    assert "raw_transcript_path" not in session_node.metadata

    # Decisions carry session_id + extractor tag for downstream queries.
    decisions = [
        n for n in graph.nodes if n.type == ResearchNodeType.SESSION_DECISION
    ]
    for d in decisions:
        assert d.metadata["session_id"] == "sess-a"
        assert d.metadata["extractor"] == "session-structural"


def test_session_outside_project_is_filtered(tmp_path: Path):
    sibling = tmp_path.parent / "other-project"
    doc_graph = _doc_graph(tmp_path)
    idx = DocPathIndex.from_graph(doc_graph, tmp_path)

    in_project = _session(
        id="sess-good",
        project_root=tmp_path,
        files_touched=["data/research/papers/rel/paper.md"],
    )
    out_of_project = _session(
        id="sess-leaked",
        project_root=sibling,
        files_touched=["data/research/papers/rel/paper.md"],
        decisions=["This decision must not leak into this project's graph."],
    )

    graph = extract_structural(
        [in_project, out_of_project], idx, project_root=tmp_path
    )

    # Only the in-project session survives.
    session_ids = {
        n.metadata.get("session_id")
        for n in graph.nodes
        if n.type == ResearchNodeType.SESSION
    }
    assert session_ids == {"sess-good"}

    # The leaked session's decision must not appear under any session_id.
    leaked_decision_text = "This decision must not leak into this project's graph."
    assert all(
        n.name != leaked_decision_text
        for n in graph.nodes
        if n.type == ResearchNodeType.SESSION_DECISION
    )


def test_extract_is_idempotent(tmp_path: Path):
    doc_graph = _doc_graph(tmp_path)
    idx = DocPathIndex.from_graph(doc_graph, tmp_path)
    session = _session(
        id="sess-idem",
        project_root=tmp_path,
        files_touched=["data/research/papers/rel/paper.md"],
        decisions=["foo", "bar"],
    )

    a = extract_structural([session], idx, project_root=tmp_path)
    b = extract_structural([session], idx, project_root=tmp_path)

    a_ids = sorted(n.id for n in a.nodes)
    b_ids = sorted(n.id for n in b.nodes)
    assert a_ids == b_ids

    a_edges = sorted((e.source, e.type, e.target) for e in a.edges)
    b_edges = sorted((e.source, e.type, e.target) for e in b.edges)
    assert a_edges == b_edges

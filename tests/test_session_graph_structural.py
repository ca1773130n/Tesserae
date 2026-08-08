"""Tests for the structural (LLM-free) session graph extractor.

Asserts the deterministic invariants:
* Sessions outside ``project_root`` are filtered (privacy invariant).
* ``Session`` nodes mint correctly with sanitised envelope metadata.
* ``discussed_in`` edges resolve through the multi-key path index.
* ``decisions`` entries become ``SessionDecision`` nodes with
  ``derived_from_session`` edges.
* ``files_touched`` survives into the envelope verbatim, including the
  paths no doc node resolves — the sole surviving record of what a
  session worked on now that no compile mints ``SourceFile`` nodes.
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

    # 1 Session + 2 SessionDecision, plus the Phase-1 agent layer: the
    # session's Agent + the implicit org:root Agent + one structural
    # ExpertiseProfile for the observed agent.
    types = sorted(n.type.value for n in graph.nodes)
    assert types == [
        "Agent",
        "Agent",
        "ExpertiseProfile",
        "Session",
        "SessionDecision",
        "SessionDecision",
    ]

    # 2 `discussed_in` edges (the missing path doesn't bind) + 2
    # `derived_from_session` edges from the decisions + the agent-layer
    # `performed_by` (Session → Agent) and `reports_to` (Agent → org:root).
    edge_types = sorted(e.type for e in graph.edges)
    assert edge_types == [
        "derived_from_session",
        "derived_from_session",
        "discussed_in",
        "discussed_in",
        "performed_by",
        "reports_to",
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


def test_unresolvable_paths_survive_in_the_envelope(tmp_path: Path):
    """Every ``files_touched`` entry lands in metadata, resolved or not.

    Source files are exactly the paths that resolve to nothing now: the
    compile no longer mints ``SourceFile`` nodes, so ``path_index`` cannot
    bind ``tesserae/project.py`` to anything and no edge is drawn for it.
    The envelope is what is left, so it has to carry the path itself —
    a filter that kept only the paths the index resolved would silently
    reduce the session record to its documents.
    """
    doc_graph = _doc_graph(tmp_path)
    idx = DocPathIndex.from_graph(doc_graph, tmp_path)
    session = _session(
        id="sess-files",
        project_root=tmp_path,
        files_touched=[
            "data/research/papers/rel/paper.md",  # resolves to Paper:rel
            "tesserae/project.py",  # no node: source code is out of scope
            "src/lib.rs",
        ],
    )

    graph = extract_structural([session], idx, project_root=tmp_path)

    session_node = next(n for n in graph.nodes if n.type == ResearchNodeType.SESSION)
    assert session_node.metadata["files_touched"] == [
        "data/research/papers/rel/paper.md",
        "tesserae/project.py",
        "src/lib.rs",
    ]
    # Plain strings, not node ids or wikilinks — anything richer would have
    # to be a graph node, which is what this replaces.
    assert all(
        isinstance(item, str) for item in session_node.metadata["files_touched"]
    )
    # Only the resolvable path draws an edge. The other two are recoverable
    # from the envelope alone, which is the whole point.
    assert [
        (e.source, e.type) for e in graph.edges if e.type == "discussed_in"
    ] == [("Paper:rel", "discussed_in")]


def test_a_session_that_touched_only_source_files_still_records_them(tmp_path: Path):
    """The degenerate case the retired code layer used to own.

    A session that edited nothing but source files binds no doc node at
    all, so it contributes zero ``discussed_in`` edges. Before the code
    layer was retired those paths became ``SourceFile -> Session`` edges
    inside ``code-graph.json``, which nothing read. If the envelope also
    dropped them, such a session would compile to a node that knows it
    happened and nothing about what it did.
    """
    doc_graph = _doc_graph(tmp_path)
    idx = DocPathIndex.from_graph(doc_graph, tmp_path)
    session = _session(
        id="sess-code-only",
        project_root=tmp_path,
        files_touched=["tesserae/cli.py", "tests/test_cli.py"],
    )

    graph = extract_structural([session], idx, project_root=tmp_path)

    assert not [e for e in graph.edges if e.type == "discussed_in"]
    session_node = next(n for n in graph.nodes if n.type == ResearchNodeType.SESSION)
    assert session_node.metadata["files_touched"] == [
        "tesserae/cli.py",
        "tests/test_cli.py",
    ]


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

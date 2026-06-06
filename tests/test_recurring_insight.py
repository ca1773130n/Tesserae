"""KB-05: cross-session recurrence reinforces confidence to "high".

``compute_recurring_confidence`` clusters near-duplicate session findings
(via supersedes chains + Jaccard) and counts DISTINCT ``session_id``s per
cluster. A finding restated across ``>= threshold`` (default 3) distinct
sessions is reinforced to confidence ``"high"`` on the cluster's canonical
(smallest) id. A 2-session restatement is NOT reinforced.

Deterministic: fixed node bodies + session ids, no wall-clock, no LLM.
"""

from __future__ import annotations

import pytest

from tesserae.memory.reinforce import compute_recurring_confidence
from tesserae.research_graph import (
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

_BODY = "Atomic writes need a PID plus a random tmp suffix to avoid clobbering"


def _insight(node_id: str, body: str, session_id: str) -> ResearchNode:
    return ResearchNode(
        id=f"SessionInsight:{node_id}",
        name=body,
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"session_id": session_id},
    )


def test_three_session_recurrence_reinforces_high() -> None:
    # The SAME insight restated (near-duplicate wording) across 3 DISTINCT
    # sessions clusters via Jaccard and reinforces to "high".
    nodes = [
        _insight("s1", _BODY, "sess-1"),
        _insight("s2", "Atomic writes need a PID plus random tmp suffix to avoid clobber", "sess-2"),
        _insight("s3", "Atomic writes need PID and a random tmp suffix to avoid clobbering", "sess-3"),
    ]
    graph = ResearchGraph(nodes=nodes, edges=[])

    out = compute_recurring_confidence(graph)
    # Canonical (smallest id) carries the numeric recurrence confidence.
    # KB-05: 3 distinct sessions → min(1.0, (3-1)/(2*3-2)) = 0.5.
    canonical = min(n.id for n in nodes)
    assert out.get(canonical) == 0.5
    assert set(out.values()) == {0.5}


def test_two_session_recurrence_does_not_reinforce() -> None:
    nodes = [
        _insight("s1", _BODY, "sess-1"),
        _insight("s2", "Atomic writes need a PID plus random tmp suffix to avoid clobber", "sess-2"),
    ]
    graph = ResearchGraph(nodes=nodes, edges=[])

    out = compute_recurring_confidence(graph)
    assert out == {}, "2 distinct sessions is below the default threshold of 3"


def test_same_session_repeats_do_not_reinforce() -> None:
    # Three restatements but all from ONE session -> distinct-session count
    # is 1, below threshold.
    nodes = [
        _insight("s1a", _BODY, "sess-1"),
        _insight("s1b", "Atomic writes need a PID plus random tmp suffix to avoid clobber", "sess-1"),
        _insight("s1c", "Atomic writes need PID and a random tmp suffix to avoid clobbering", "sess-1"),
    ]
    graph = ResearchGraph(nodes=nodes, edges=[])

    out = compute_recurring_confidence(graph)
    assert out == {}


def test_supersedes_chain_clusters_across_sessions() -> None:
    # Distinct wording linked by supersedes edges still counts as one cluster
    # spanning 3 sessions -> reinforced.
    a = _insight("a", "Use flock for cache writes", "sess-1")
    b = _insight("b", "Wrap session-graph cache writes in flock", "sess-2")
    c = _insight("c", "Cache writes must hold an flock lock", "sess-3")
    graph = ResearchGraph(
        nodes=[a, b, c],
        edges=[
            ResearchEdge_supersedes(b.id, a.id),
            ResearchEdge_supersedes(c.id, b.id),
        ],
    )

    out = compute_recurring_confidence(graph)
    canonical = min(n.id for n in (a, b, c))
    # 3 sessions linked via supersedes → numeric 0.5 (KB-05 scheme).
    assert out.get(canonical) == 0.5


def ResearchEdge_supersedes(source: str, target: str):
    from tesserae.research_graph import ResearchEdge

    return ResearchEdge(source=source, target=target, type="supersedes")

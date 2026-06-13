"""Tests for the per-transition ``Event`` memory layer.

Covers the deterministic invariants of ``tesserae.session_event.extract_events``:

* significant tool / assistant turns mint exactly one ``Event`` node each with
  a content-derived (stable) id;
* consecutive events are chained with ``precedes`` edges in turn order;
* session-finding nodes link to events at their ``turn_ids`` via
  ``derived_from`` edges;
* a stub ``json_client`` enriches the one-line description (and a failing /
  absent client degrades to the deterministic template, never raising);
* a RERUN is byte-identical (the byte-idempotence blind spot).
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from tesserae.harness_sessions import HarnessSession
from tesserae.research_graph import (
    ResearchEdge,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.session_event import (
    DERIVED_FROM_EDGE,
    PRECEDES_EDGE,
    extract_events,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _session(turns: List[dict], *, session_id: str = "sess-evt") -> HarnessSession:
    return HarnessSession(
        id=session_id,
        slug=session_id,
        harness="claude-code",
        agent_label="Claude Code",
        project_name="demo",
        project_root=str(Path("/tmp/demo").resolve()),
        started_at="2026-06-13T10:00:00Z",
        ended_at="2026-06-13T11:00:00Z",
        metadata={"turns": turns},
    )


def _default_turns() -> List[dict]:
    # turn_id 0 user chatter (skipped), 1 assistant action, 2 tool call,
    # 3 trivial assistant ack (skipped), 4 tool call.
    return [
        {"role": "user", "timestamp": "2026-06-13T10:00:01Z", "text": "please fix the bug"},
        {
            "role": "assistant",
            "timestamp": "2026-06-13T10:00:02Z",
            "text": "I'll inspect the failing module and patch the off-by-one.",
        },
        {
            "role": "tool",
            "name": "Edit",
            "timestamp": "2026-06-13T10:00:03Z",
            "text": "patched tesserae/foo.py line 42",
        },
        {"role": "assistant", "timestamp": "2026-06-13T10:00:04Z", "text": "done"},
        {
            "role": "tool",
            "name": "Bash",
            "timestamp": "2026-06-13T10:00:05Z",
            "text": "pytest -q -> 12 passed",
        },
    ]


class _StubClient:
    """Deterministic stub ``LLMJsonClient`` — enriches via a fixed transform."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def complete_text(self, *, system: str, user: str, max_retries: int = 2) -> str:
        self.calls.append(user)
        return f"ENRICHED: {user}"


class _BoomClient:
    """Stub whose enrichment always raises — exercises degrade-never-raise."""

    def complete_text(self, *, system: str, user: str, max_retries: int = 2) -> str:
        raise RuntimeError("llm unavailable")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_or_none_session_returns_empty():
    assert extract_events(None) == ([], [])
    assert extract_events(_session([])) == ([], [])
    # Only chatter -> no significant turns.
    chatter = _session([{"role": "user", "text": "hi"}, {"role": "assistant", "text": "ok"}])
    assert extract_events(chatter) == ([], [])


def test_significant_turns_mint_events_with_stable_ids():
    session = _session(_default_turns())
    nodes, edges = extract_events(session)

    assert [n.type for n in nodes] == [ResearchNodeType.EVENT] * 3
    # turn_ids 1 (assistant action), 2 (Edit), 4 (Bash). turns 0 and 3 skipped.
    assert [n.metadata["turn_id"] for n in nodes] == [1, 2, 4]
    assert [n.metadata["actor"] for n in nodes] == ["assistant", "tool", "tool"]
    assert nodes[1].metadata["tool"] == "Edit"
    assert nodes[2].metadata["tool"] == "Bash"

    # Ids are content-derived: stable id scheme, all distinct, no RNG.
    ids = [n.id for n in nodes]
    assert len(set(ids)) == 3
    for n in nodes:
        assert n.id.startswith("Event:")
        # first_seen_at derives from the turn timestamp, never wall-clock.
        assert n.metadata["first_seen_at"].startswith("2026-06-13T10:00:")


def test_precedes_chain_in_turn_order():
    session = _session(_default_turns())
    nodes, edges = extract_events(session)

    precedes = [e for e in edges if e.type == PRECEDES_EDGE]
    assert len(precedes) == len(nodes) - 1
    # Chain links node[i] -> node[i+1] in turn order.
    for i, edge in enumerate(precedes):
        assert edge.source == nodes[i].id
        assert edge.target == nodes[i + 1].id


def test_findings_derive_from_events_by_turn_id():
    session = _session(_default_turns())
    finding = ResearchNode(
        id="SessionDecision:patch:abcd",
        name="Patched the off-by-one in foo.py",
        type=ResearchNodeType.SESSION_DECISION,
        metadata={"session_id": "sess-evt", "turn_ids": [1, 2]},
    )
    nodes, edges = extract_events(session, findings=[finding])

    by_turn = {n.metadata["turn_id"]: n.id for n in nodes}
    derived = [e for e in edges if e.type == DERIVED_FROM_EDGE]
    # turn 1 and turn 2 both minted events -> two derived_from edges.
    assert len(derived) == 2
    assert {(e.source, e.target) for e in derived} == {
        (finding.id, by_turn[1]),
        (finding.id, by_turn[2]),
    }
    for e in derived:
        assert e.source == finding.id  # finding --derived_from--> event

    # A turn id with no minted event (0, chatter) yields no edge.
    finding_chatter = ResearchNode(
        id="SessionTODO:x:0000",
        name="x",
        type=ResearchNodeType.SESSION_TODO,
        metadata={"turn_ids": [0]},
    )
    _, edges2 = extract_events(session, findings=[finding_chatter])
    assert [e for e in edges2 if e.type == DERIVED_FROM_EDGE] == []


def test_same_named_events_from_two_sessions_survive_merge():
    """Aggressive same-name dedup must NOT collapse Event nodes from different
    sessions: identical turns in two sessions yield same display names but
    distinct (session-scoped) ids — both must survive merge_graphs()."""
    from tesserae.project import merge_graphs
    from tesserae.research_graph import ResearchGraph

    a_nodes, _ = extract_events(_session(_default_turns(), session_id="sA"))
    b_nodes, _ = extract_events(_session(_default_turns(), session_id="sB"))
    assert a_nodes and b_nodes
    # Same display names across sessions, different ids.
    assert {n.name for n in a_nodes} == {n.name for n in b_nodes}
    assert {n.id for n in a_nodes}.isdisjoint({n.id for n in b_nodes})

    merged = merge_graphs([
        ResearchGraph(nodes=a_nodes, edges=[]),
        ResearchGraph(nodes=b_nodes, edges=[]),
    ])
    surviving = {n.id for n in merged.nodes if n.type == ResearchNodeType.EVENT}
    expected = {n.id for n in a_nodes} | {n.id for n in b_nodes}
    assert surviving == expected, "events from different sessions must all survive merge"


def test_event_description_is_deterministic_regardless_of_client():
    """Event descriptions are serialized into graph.json, so they must be fully
    deterministic — a json_client must NOT change the bytes (it is accepted for
    API symmetry but unused). This is the byte-idempotence guard for the Event
    layer: an uncached LLM enrichment here would diverge graph.json across two
    identical compiles."""
    session = _session(_default_turns())
    with_client, _ = extract_events(session, json_client=_StubClient())
    without_client, _ = extract_events(session)

    assert [n.description for n in with_client] == [
        n.description for n in without_client
    ], "a client must not alter Event descriptions"
    assert all(not n.description.startswith("ENRICHED: ") for n in with_client)


def test_failing_or_absent_client_degrades_to_template():
    session = _session(_default_turns())
    # Absent client: deterministic template, mentions the tool/actor.
    base_nodes, _ = extract_events(session)
    assert any("invoked Edit" in n.description for n in base_nodes)

    # Failing client: never raises, falls back to the SAME template bytes.
    boom_nodes, _ = extract_events(session, json_client=_BoomClient())
    assert [n.description for n in boom_nodes] == [n.description for n in base_nodes]


def _dump(nodes: List[ResearchNode], edges: List[ResearchEdge]):
    return (
        [n.model_dump() for n in nodes],
        [e.model_dump() for e in edges],
    )


def test_rerun_is_byte_identical():
    session = _session(_default_turns())
    finding = ResearchNode(
        id="SessionInsight:k:1111",
        name="insight",
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"turn_ids": [2, 4]},
    )

    first = extract_events(session, findings=[finding])
    second = extract_events(session, findings=[finding])

    # Node ids / edges identical across runs (determinism).
    assert [n.id for n in first[0]] == [n.id for n in second[0]]
    assert _dump(*first) == _dump(*second)

    # And identical even when an LLM client enriched (description is the only
    # LLM-touched field; ids/edges never depend on it).
    enriched = extract_events(session, findings=[finding], json_client=_StubClient())
    assert [n.id for n in enriched[0]] == [n.id for n in first[0]]
    assert _dump(enriched[0], enriched[1])[1] == _dump(*first)[1]  # edges identical

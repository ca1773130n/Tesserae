"""LRU forgetting-by-disuse for the per-agent distill pass (Phase-5 KB-01).

The distill FORGETTING pass scores every raw L0 finding through
:func:`tesserae.memory.decay.compute_decay_score` at two sites — the absorption
gate (``_absorbable``) and remainder ranking. Before Phase-5 that score anchored
on the finding's CREATION age (``first_seen_at``) alone: a finding nobody had
looked at in months decayed exactly like one read yesterday.

These tests exercise the LRU merge. ``distill_agent`` now loads the
``node_memory`` sidecar (``.tesserae/sqlite.db``) ONCE and overlays each node's
live ``last_accessed_at`` / ``access_count`` — accumulated by the MCP read
surfaces — onto a COPY of its metadata before scoring (mirroring the compile-time
merge in ``project.py``). Effect: a finding RETRIEVED recently survives absorb /
demote, while an equally-old but never-retrieved one is forgotten.

Determinism contract (the hard gate): the merge is a no-op when the sidecar has
no matching row, so a project with no relevant ``node_memory`` state produces a
BYTE-IDENTICAL distillate to the pre-LRU code — the byte-parity guards in
``test_agent_distill.py`` / ``test_agent_distill_manager.py`` stay green. This
pass NEVER records access (that is the read surfaces' job) and NEVER stamps
sidecar state onto ``graph.json``.

Summarization is the deterministic stub — no live LLM. The sidecar is seeded via
``memory.store.bump_access`` (the exact atomic write the MCP read surfaces use),
so the fixtures faithfully simulate "this finding was read recently".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List, Optional

from tesserae.agent_distill import (
    DistillOptions,
    DistillRequest,
    DistillStateStore,
    agent_artifact_path,
    distill_agent,
)
from tesserae.memory.store import bump_access
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    stable_id,
)

AGENT = "claude-code:me:reviewer"
AGENT_ID = stable_id("Agent", f"agent:{AGENT}")

# A read that lands one day before the corpus clock: age ~= 1 day, half-life 14d
# -> base decay ~= 0.95, well above both the 0.2 absorb bar and 0.3 remainder
# ENTER bar. "Never accessed" old findings (first_seen 2026-01-01, ~181 days
# before the corpus clock) decay to ~1e-4 and fall under both bars.
_RECENT_READ = "2026-06-30T10:00:00Z"
_CORPUS_END = "2026-07-01T10:00:00Z"
_OLD_SEEN = "2026-01-01T00:00:00Z"


# --------------------------------------------------------------------------- fixture helpers


def _session(sid: str, ended: str) -> ResearchNode:
    return ResearchNode(
        id=f"Session:{sid}",
        name=f"session {sid}",
        type=ResearchNodeType.SESSION,
        metadata={
            "session_id": sid,
            "agent_label": "Claude Code",
            "started_at": "2026-06-20T10:00:00Z",
            "ended_at": ended,
        },
    )


def _finding(fid: str, name: str, session: ResearchNode, first_seen: str) -> ResearchNode:
    return ResearchNode(
        id=fid,
        name=name,
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={
            "session_id": session.metadata["session_id"],
            "content_hash": f"ch-{fid}",
            "first_seen_at": first_seen,
        },
    )


def _agent_node(agent_key: str, node_id: str) -> ResearchNode:
    harness, account, role = agent_key.split(":")
    return ResearchNode(
        id=node_id,
        name=agent_key,
        type=ResearchNodeType.AGENT,
        metadata={
            "agent_key": agent_key,
            "harness": harness,
            "account": account,
            "role": role,
            "label": agent_key,
        },
    )


class StubSummarizer:
    """Deterministic injected summarizer (no LLM) — yields an llm-quality note."""

    def __init__(self, fn: Optional[Callable[[DistillRequest], Optional[dict]]] = None) -> None:
        self.calls: List[DistillRequest] = []
        self._fn = fn if fn is not None else self._default

    @staticmethod
    def _default(request: DistillRequest) -> dict:
        ids = [member[0] for member in request.members]
        names = "; ".join(member[1] for member in request.members)
        return {
            "kind": "runbook",
            "title": f"Runbook over {len(ids)} findings",
            "body": f"Steps distilled from: {names}",
            "citations": ids,
        }

    def __call__(self, request: DistillRequest) -> Optional[dict]:
        self.calls.append(request)
        return self._fn(request)


def _absorb_graph() -> ResearchGraph:
    """Two OLD near-dup findings that cluster into one llm-quality distillate.

    With no sidecar both decay far below the 0.2 absorb bar and are absorbed
    (the pre-LRU behavior). Seeding a recent read on one keeps it.
    """
    s1 = _session("s1", _CORPUS_END)
    old_a = _finding(
        "SessionInsight:olda", "Legacy rollback script juggling dance", s1, _OLD_SEEN
    )
    old_b = _finding(
        "SessionInsight:oldb", "Legacy rollback script juggling dances", s1, "2026-01-02T00:00:00Z"
    )
    nodes = [_agent_node(AGENT, AGENT_ID), s1, old_a, old_b]
    edges = [
        ResearchEdge(source=s1.id, target=AGENT_ID, type="performed_by"),
        ResearchEdge(source=old_a.id, target=s1.id, type="derived_from_session"),
        ResearchEdge(source=old_b.id, target=s1.id, type="derived_from_session"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _singleton_graph() -> ResearchGraph:
    """Three OLD, mutually-distinct singletons (no clustering, none absorbed).

    All decay below the 0.3 remainder ENTER bar, so with no sidecar every one
    demotes to the Index note. A recent read lifts one back into the remainder.
    """
    s1 = _session("s1", _CORPUS_END)
    findings = [
        _finding("SessionInsight:aa", "Alpha topic entirely unique", s1, _OLD_SEEN),
        _finding("SessionInsight:bb", "Beta subject wholly distinct", s1, _OLD_SEEN),
        _finding("SessionInsight:cc", "Gamma matter fully separate", s1, _OLD_SEEN),
    ]
    nodes = [_agent_node(AGENT, AGENT_ID), s1, *findings]
    edges = [ResearchEdge(source=s1.id, target=AGENT_ID, type="performed_by")]
    edges += [
        ResearchEdge(source=f.id, target=s1.id, type="derived_from_session")
        for f in findings
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _seed_access(project: Path, node_id: str, accessed_at: str) -> None:
    """Record a read of ``node_id`` in the node_memory sidecar (as MCP reads do)."""
    (project / ".tesserae").mkdir(parents=True, exist_ok=True)
    bump_access(project / ".tesserae" / "sqlite.db", node_id, accessed_at)


def _distill(project: Path, graph: ResearchGraph, options: Optional[DistillOptions] = None):
    project.mkdir(parents=True, exist_ok=True)
    return distill_agent(
        graph, AGENT, project_root=project, summarizer=StubSummarizer(), options=options
    )


def _payload(project: Path) -> dict:
    return json.loads(agent_artifact_path(project, AGENT).read_text(encoding="utf-8"))


def _absorbed_ids(payload: dict) -> set:
    return {
        ref["node_id"]
        for note in payload["nodes"]
        if note["type"] == "DistilledNote"
        for ref in note["metadata"].get("absorbed_refs", [])
    }


def _remainder_insight_ids(payload: dict) -> set:
    """Findings copied verbatim into the artifact as remainder SessionInsight nodes."""
    return {n["id"] for n in payload["nodes"] if n["type"] == "SessionInsight"}


def _index_member_ids(payload: dict) -> set:
    notes = [
        n
        for n in payload["nodes"]
        if n["type"] == "DistilledNote" and n["metadata"].get("kind") == "index"
    ]
    assert len(notes) == 1, "exactly one Index note is minted per pass"
    return {ref["node_id"] for ref in notes[0]["metadata"].get("member_refs", [])}


# --------------------------------------------------------------------------- absorption (§6.1)


def test_recent_access_prevents_absorption(tmp_path: Path) -> None:
    graph = _absorb_graph()

    # Baseline: no sidecar -> LRU merge is a no-op -> BOTH old near-dups decay on
    # creation age below 0.2 and are absorbed (the pre-LRU behavior).
    base = _distill(tmp_path / "base", graph)
    base_payload = _payload(tmp_path / "base")
    assert base.absorbed_count == 2
    assert _absorbed_ids(base_payload) == {"SessionInsight:olda", "SessionInsight:oldb"}
    assert _remainder_insight_ids(base_payload) == set()  # both gone from the artifact

    # LRU: record a recent read of old_a BEFORE distilling. Its decay now anchors
    # on last_accessed_at (recent) instead of first_seen_at (ancient), lifting it
    # above the absorb bar. old_b was never read and is still absorbed.
    proj = tmp_path / "lru"
    _seed_access(proj, "SessionInsight:olda", _RECENT_READ)
    lru = _distill(proj, graph)
    lru_payload = _payload(proj)

    assert lru.absorbed_count == 1
    assert _absorbed_ids(lru_payload) == {"SessionInsight:oldb"}
    # The recently-read finding survives as a full node in the artifact.
    assert "SessionInsight:olda" in _remainder_insight_ids(lru_payload)


# --------------------------------------------------------------------------- remainder ranking (§5.5)


def test_access_recency_changes_remainder_ranking(tmp_path: Path) -> None:
    graph = _singleton_graph()

    # Baseline: no sidecar -> all three old singletons decay below the 0.3 ENTER
    # bar -> none is remainder-eligible -> all demote to the Index note.
    _distill(tmp_path / "base", graph)
    base_payload = _payload(tmp_path / "base")
    assert _remainder_insight_ids(base_payload) == set()
    assert _index_member_ids(base_payload) == {
        "SessionInsight:aa",
        "SessionInsight:bb",
        "SessionInsight:cc",
    }

    # LRU: a recent read of bb lifts its decay over the ENTER bar, so it is
    # promoted into the remainder while the untouched aa / cc stay in the Index.
    proj = tmp_path / "lru"
    _seed_access(proj, "SessionInsight:bb", _RECENT_READ)
    _distill(proj, graph)
    lru_payload = _payload(proj)

    assert _remainder_insight_ids(lru_payload) == {"SessionInsight:bb"}
    assert _index_member_ids(lru_payload) == {"SessionInsight:aa", "SessionInsight:cc"}


# --------------------------------------------------------------------------- determinism (§7.2)


def test_empty_or_irrelevant_sidecar_is_byte_identical(tmp_path: Path) -> None:
    graph = _absorb_graph()

    # (A) No sidecar state at all — the reference "today" bytes.
    _distill(tmp_path / "a", graph)
    a_bytes = agent_artifact_path(tmp_path / "a", AGENT).read_bytes()

    # (B) A sidecar that EXISTS but only carries a row for a node absent from the
    # scored graph. read_memory returns a row, yet _decay_view matches nothing, so
    # the artifact must be byte-identical to (A): the merge is per-node, and the
    # mere presence of the sidecar never perturbs the distillate.
    proj_b = tmp_path / "b"
    _seed_access(proj_b, "SessionInsight:not-in-this-graph", _RECENT_READ)
    _distill(proj_b, graph)
    b_bytes = agent_artifact_path(proj_b, AGENT).read_bytes()
    assert b_bytes == a_bytes

    # (C) A MEANINGFUL row (recent read of a scored member) DOES change the bytes
    # — proof the LRU merge is actually wired and not a dead no-op.
    proj_c = tmp_path / "c"
    _seed_access(proj_c, "SessionInsight:olda", _RECENT_READ)
    _distill(proj_c, graph)
    c_bytes = agent_artifact_path(proj_c, AGENT).read_bytes()
    assert c_bytes != a_bytes


def test_lru_pass_never_writes_node_memory(tmp_path: Path) -> None:
    # The forgetting pass is a READER of access state — it must never record or
    # mutate node_memory (that is the MCP read surfaces' job). Distilling with no
    # sidecar must leave no node_memory rows behind, even though it opens the db.
    proj = tmp_path / "proj"
    _distill(proj, _absorb_graph())

    state = DistillStateStore(proj / ".tesserae" / "sqlite.db")
    # distill writes its own bookkeeping (watermark, ledger) but zero access rows.
    from tesserae.memory.store import read_memory

    assert read_memory(proj / ".tesserae" / "sqlite.db") == {}
    # Sanity: the pass did run and persisted its own state.
    assert state.get(DistillStateStore.SCOPE_WATERMARK, AGENT, "") != ""

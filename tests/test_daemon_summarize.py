"""Deterministic tests for the SUMMARIZE (pre-warm) leg of the daemon's
sleep-cycle consolidation (Descent §6.4, PR7).

The idle-consolidation tick already runs DISTILL (compress/forget) and
ASSOCIATE (discover connections); this suite covers the third op wired into
the SAME tick, under the SAME compile gate, AFTER associate: within a
per-tick LLM-call budget, pre-warm community-summary caches for the
communities ranked by demand (the scope's own cid ``access_count`` row —
``graph_map`` bumps every surfaced card's scope_id — plus Σ
``node_memory.access_count`` over members), tie-broken by size then degree,
coarsest level first. Determinism reuses the constructor seams from
``tests/test_daemon_associate.py`` — an injected monotonic clock
(``monotonic=``), injected distill/associate callables — plus the new
``summary_client=`` seam (a fake LLM JSON client, so no test shells out to a
real model) and ``summarize_budget=``. The synthetic multi-level fixture is
the ``tests/test_graph_map.py`` family: a hand-written hierarchy sidecar
over a small two-community graph.

Run with the project venv (NOT the shim)::

    .venv/bin/python -m pytest tests/test_daemon_summarize.py -q
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

import tesserae.project as project_mod
from tesserae.community_summaries import community_id, level_cache_path
from tesserae.engine.daemon import Daemon
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

A_MEMBERS = ["Concept:a1", "Concept:a2", "Concept:a3", "Concept:a4"]
B_MEMBERS = ["Concept:b1", "Concept:b2", "Concept:b3", "Concept:b4", "Concept:b5", "Concept:b6"]
A1_MEMBERS = ["Concept:a1", "Concept:a2"]
A2_MEMBERS = ["Concept:a3", "Concept:a4"]
B1_MEMBERS = ["Concept:b1", "Concept:b2", "Concept:b3"]
B2_MEMBERS = ["Concept:b4", "Concept:b5"]

CID_A = community_id(A_MEMBERS)
CID_B = community_id(B_MEMBERS)
CID_A1 = community_id(A1_MEMBERS)
CID_A2 = community_id(A2_MEMBERS)
CID_B1 = community_id(B1_MEMBERS)
CID_B2 = community_id(B2_MEMBERS)


class FakeClock:
    """A hand-advanced stand-in for ``time.monotonic`` (seconds, float)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class RecordingDistill:
    """Stub for ``maybe_distill_on_refresh``: records calls into a shared log."""

    def __init__(self, order: list) -> None:
        self._order = order

    def __call__(self, project_root, graph, *, cfg=None, env=None):
        self._order.append("distill")
        return {"distilled": [], "skipped": [], "failed": []}


class RecordingAssociate:
    """Stub for ``consolidate_associations``: records call order."""

    def __init__(self, order: list) -> None:
        self._order = order

    def __call__(self, project_root, graph, *, backend=None, **kwargs):
        self._order.append("associate")
        return {"associate_added": 0}


class RecordingSummaryClient:
    """Fake LLM JSON client for the SUMMARIZE op.

    Returns a valid summary payload whose description cites the first child
    community id listed in the prompt (so the §5.2 citation discipline
    accepts it). Optionally raises (to prove a failing client never breaks
    the tick) and optionally probes the compile gate (to prove the op runs
    UNDER it, serialized with any compile).
    """

    def __init__(
        self,
        order: list | None = None,
        *,
        raises: BaseException | None = None,
        gate: threading.Semaphore | None = None,
    ) -> None:
        self._order = order
        self._raises = raises
        self._gate = gate
        self.calls: list = []
        self.gate_was_held: bool | None = None

    def complete_json(self, *, system, user, schema_name, cache_key=None, **_):  # noqa: ANN001
        self.calls.append({"system": system, "user": user, "cache_key": cache_key})
        if self._order is not None:
            self._order.append("summarize")
        if self._gate is not None:
            # Non-blocking probe: if the caller holds the gate, acquire() fails.
            acquired = self._gate.acquire(blocking=False)
            self.gate_was_held = not acquired
            if acquired:
                self._gate.release()
        if self._raises is not None:
            raise self._raises
        cited = re.findall(r"CommunitySummary:[0-9a-f]{16}", user)
        description = "Pre-warmed community summary"
        if cited:
            description += f" spanning {cited[0]}"
        return {
            "title": "Warm Title",
            "description": description + ".",
            "tags": ["warm", "cache", "descent"],
        }

    def prompt_member_counts(self) -> list:
        """Materialization order fingerprint: prompt sizes per LLM call."""
        return [int(str(c["cache_key"]).rsplit("::", 1)[1]) for c in self.calls]


def _fixture_graph() -> ResearchGraph:
    """Two coarse communities; A carries an in-graph COMMUNITY_SUMMARY node."""

    def _concept(nid: str) -> ResearchNode:
        return ResearchNode(
            id=nid,
            name=f"Node {nid.split(':')[1].upper()}",
            type=ResearchNodeType.CONCEPT,
            description=f"description of {nid}",
        )

    nodes = [_concept(nid) for nid in A_MEMBERS + B_MEMBERS]
    nodes.append(
        ResearchNode(
            id=CID_A,
            name="Alpha Systems",
            type=ResearchNodeType.COMMUNITY_SUMMARY,
            description="LLM-written summary of the alpha community.",
            metadata={
                "member_ids": list(A_MEMBERS),
                "member_count": len(A_MEMBERS),
                "tags": ["alpha", "systems", "graph", "kg", "llm"],
            },
        )
    )
    edges = [
        ResearchEdge(source="Concept:a1", target="Concept:a2", type="shares_concept_with"),
        ResearchEdge(source="Concept:a3", target="Concept:a4", type="shares_concept_with"),
        ResearchEdge(source="Concept:b1", target="Concept:b2", type="shares_concept_with"),
        ResearchEdge(source="Concept:b1", target="Concept:b3", type="shares_concept_with"),
        ResearchEdge(source="Concept:b1", target="Concept:b4", type="shares_concept_with"),
        ResearchEdge(source="Concept:b1", target="Concept:b5", type="shares_concept_with"),
        ResearchEdge(source="Concept:b1", target="Concept:b6", type="shares_concept_with"),
        ResearchEdge(source="Concept:b2", target="Concept:b3", type="shares_concept_with"),
    ]
    edges.extend(
        ResearchEdge(source=CID_A, target=mid, type="summarizes", metadata={"community_id": CID_A})
        for mid in A_MEMBERS
    )
    return ResearchGraph(nodes=nodes, edges=edges)


def _hierarchy_payload() -> dict:
    return {
        "schema_version": 1,
        "levels": [
            # finest: A1 identical to the middle level; b6 loose under B.
            {CID_A1: A1_MEMBERS, CID_A2: A2_MEMBERS, CID_B1: B1_MEMBERS, CID_B2: B2_MEMBERS},
            # middle: A splits, B passes through byte-identically.
            {CID_A1: A1_MEMBERS, CID_A2: A2_MEMBERS, CID_B: B_MEMBERS},
            # coarsest: the root card set.
            {CID_A: A_MEMBERS, CID_B: B_MEMBERS},
        ],
        "hubs": ["Concept:b1"],
    }


def _make_project(tmp_path: Path, *, with_hierarchy: bool = True) -> Path:
    root = tmp_path / "proj"
    tess = root / ".tesserae"
    tess.mkdir(parents=True)
    (tess / "graph.json").write_text(_fixture_graph().to_json(indent=2), encoding="utf-8")
    if with_hierarchy:
        (tess / "hierarchy.json").write_text(
            json.dumps(_hierarchy_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return root


def _make_daemon(root: Path, clock: FakeClock, order: list, **kwargs) -> Daemon:
    return Daemon(
        root,
        consolidate_idle_seconds=300.0,
        monotonic=clock,
        distill=RecordingDistill(order),
        associate=RecordingAssociate(order),
        **kwargs,
    )


def _cache_dir(root: Path) -> Path:
    return root / ".tesserae" / "community_summaries"


def _bump(root: Path, node_id: str, times: int) -> None:
    """Deterministic demand: repeated graph_map-style access bumps."""
    from tesserae.memory.store import bump_access

    for _ in range(times):
        bump_access(root / ".tesserae" / "sqlite.db", node_id, "2026-01-01T00:00:00Z")


@pytest.fixture(autouse=True)
def _reset_community_client():
    # Reset BEFORE (in case a prior test leaked) and AFTER (so a client
    # injected here can never leak into another test's compiles).
    project_mod.set_community_summaries_test_client(None)
    yield
    project_mod.set_community_summaries_test_client(None)


# --------------------------------------------------------------------------- #
# summarize runs after associate and warms every cold community                #
# --------------------------------------------------------------------------- #


def test_summarize_runs_after_associate_and_warms_cold_caches(tmp_path):
    """One due tick runs distill, associate, THEN summarize; within budget it
    warms every cold multi-member community at its canonical level and never
    touches the in-graph-summarized coarse community."""
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    order: list = []
    client = RecordingSummaryClient(order)
    d = _make_daemon(root, clock, order, summary_client=client)

    clock.advance(301)  # idle window elapsed -> tick is due
    d._consolidation_tick()

    assert order[:2] == ["distill", "associate"], "summarize must run LAST"
    assert set(order[2:]) == {"summarize"}
    # Five cold communities (A has an in-graph COMMUNITY_SUMMARY -> skipped):
    # B, A1, A2, B1, B2 — each pays exactly one LLM call.
    assert len(client.calls) == 5
    cache = _cache_dir(root)
    assert level_cache_path(cache, 2, CID_B).is_file()  # coarsest occurrence
    assert level_cache_path(cache, 1, CID_A1).is_file()
    assert level_cache_path(cache, 1, CID_A2).is_file()
    assert level_cache_path(cache, 0, CID_B1).is_file()
    assert level_cache_path(cache, 0, CID_B2).is_file()
    assert not list(cache.rglob(f"*{CID_A.split(':')[1]}*")), "A is compile-owned"
    # No demand yet -> size ranks first: B (6 members) then B1 (3) lead.
    assert client.prompt_member_counts()[:2] == [6, 3]
    # B's children are communities -> the §5.2 citation prompt lists them.
    b_call = client.calls[0]
    assert CID_B1 in b_call["user"] and CID_B2 in b_call["user"]
    # Cached payload carries the citation-compliant summary.
    payload = json.loads(level_cache_path(cache, 2, CID_B).read_text(encoding="utf-8"))
    assert payload["community_id"] == CID_B
    assert CID_B1 in payload["summary"]["description"]


def test_summarize_runs_under_the_compile_gate(tmp_path):
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    gate = threading.Semaphore(1)
    order: list = []
    client = RecordingSummaryClient(order, gate=gate)
    d = _make_daemon(root, clock, order, summary_client=client, compile_gate=gate)

    clock.advance(301)
    d._consolidation_tick()

    assert client.calls, "summarize ran"
    assert client.gate_was_held is True, "summarize must run under the compile gate"


# --------------------------------------------------------------------------- #
# demand ranking + budget                                                      #
# --------------------------------------------------------------------------- #


def test_demand_ranking_prefers_accessed_members(tmp_path):
    """graph_map access bumps steer the budget: with demand on B2's members,
    B (which contains them) and then B2 outrank the larger-but-cold B1."""
    root = _make_project(tmp_path)
    _bump(root, "Concept:b4", 3)
    _bump(root, "Concept:b5", 2)
    clock = FakeClock(1000.0)
    order: list = []
    client = RecordingSummaryClient(order)
    d = _make_daemon(root, clock, order, summary_client=client, summarize_budget=2)

    clock.advance(301)
    d._consolidation_tick()

    assert len(client.calls) == 2, "budget caps the tick at 2 LLM calls"
    # Demand 5 ties B (6 members) and B2 (2) -> size breaks it: B first.
    assert client.prompt_member_counts() == [6, 2]
    cache = _cache_dir(root)
    assert level_cache_path(cache, 2, CID_B).is_file()
    assert level_cache_path(cache, 0, CID_B2).is_file()
    assert not level_cache_path(cache, 0, CID_B1).is_file(), "cold B1 lost to demand"


def test_demand_ranking_sees_spine_traversal_bumps(tmp_path):
    """``graph_map`` bumps land on surfaced cards' scope_ids — community cids
    that below the coarsest level are pseudo-ids, not graph nodes and never
    members of any community. The demand ranking must read those cid rows
    directly, so an agent that browses the spine without ever reaching leaf
    node cards still steers the pre-warm budget toward the hot branch."""
    root = _make_project(tmp_path)
    _bump(root, CID_B2, 3)  # spine traversal: B2's card surfaced repeatedly
    clock = FakeClock(1000.0)
    order: list = []
    client = RecordingSummaryClient(order)
    d = _make_daemon(root, clock, order, summary_client=client, summarize_budget=2)

    clock.advance(301)
    d._consolidation_tick()

    assert len(client.calls) == 2, "budget caps the tick at 2 LLM calls"
    # Demand 3 on the B2 cid row itself ranks B2 first; the runner-up falls
    # back to size order (B leads with 6 members).
    assert client.prompt_member_counts() == [2, 6]
    cache = _cache_dir(root)
    assert level_cache_path(cache, 0, CID_B2).is_file()
    assert not level_cache_path(cache, 0, CID_B1).is_file(), (
        "cold B1 lost the budget to spine demand"
    )


def test_budget_zero_disables_summarize(tmp_path):
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    order: list = []
    client = RecordingSummaryClient(order)
    d = _make_daemon(root, clock, order, summary_client=client, summarize_budget=0)

    clock.advance(301)
    d._consolidation_tick()

    assert order == ["distill", "associate"], "budget=0 is a no-op"
    assert client.calls == []
    assert not _cache_dir(root).exists()


def test_warm_caches_cost_no_budget_on_later_ticks(tmp_path):
    """A second due tick finds every cache warm (digest-valid) and pays zero
    further LLM calls — pre-warming is idempotent."""
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    order: list = []
    client = RecordingSummaryClient(order)
    d = _make_daemon(root, clock, order, summary_client=client)

    clock.advance(301)
    d._consolidation_tick()
    calls_after_first = len(client.calls)
    clock.advance(301)  # a fresh idle window
    d._consolidation_tick()

    assert calls_after_first == 5
    assert len(client.calls) == calls_after_first, "warm caches re-invoked the LLM"


# --------------------------------------------------------------------------- #
# honest no-ops                                                                #
# --------------------------------------------------------------------------- #


def test_no_client_is_honest_noop(tmp_path):
    """Without an LLM client (no seam; conftest pins the default builder to
    None) the op skips honestly: tick completes, nothing is written."""
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    order: list = []
    d = _make_daemon(root, clock, order)

    clock.advance(301)
    d._consolidation_tick()  # must NOT raise

    assert order == ["distill", "associate"]
    assert not _cache_dir(root).exists()


def test_missing_hierarchy_sidecar_is_honest_noop(tmp_path):
    """A project that predates the Descent sidecar skips SUMMARIZE without
    building a client or raising into the tick."""
    root = _make_project(tmp_path, with_hierarchy=False)
    clock = FakeClock(1000.0)
    order: list = []
    client = RecordingSummaryClient(order)
    d = _make_daemon(root, clock, order, summary_client=client)

    clock.advance(301)
    d._consolidation_tick()

    assert order == ["distill", "associate"]
    assert client.calls == []


def test_injected_test_client_seam_is_used(tmp_path):
    """Without a constructor override the op resolves the client the same way
    the compile pass and graph_map do — the community-summaries test seam wins."""
    root = _make_project(tmp_path)
    client = RecordingSummaryClient()
    project_mod.set_community_summaries_test_client(client)
    clock = FakeClock(1000.0)
    order: list = []
    d = _make_daemon(root, clock, order)

    clock.advance(301)
    d._consolidation_tick()

    assert len(client.calls) == 5


# --------------------------------------------------------------------------- #
# safety: a summarize failure never breaks the tick / daemon                   #
# --------------------------------------------------------------------------- #


def test_client_failure_never_breaks_the_tick(tmp_path):
    """A throwing client is contained (failed communities stay structural),
    ``_last_consolidation`` is stamped, and a later due tick retries — the
    failure was never cached."""
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    order: list = []
    client = RecordingSummaryClient(order, raises=RuntimeError("summarize boom"))
    d = _make_daemon(root, clock, order, summary_client=client)

    clock.advance(301)
    before = d._last_consolidation
    d._consolidation_tick()  # must NOT raise

    assert order[:2] == ["distill", "associate"]
    assert d._last_consolidation != before, "_last_consolidation stamped in finally"
    cache = _cache_dir(root)
    assert not cache.exists() or not list(cache.rglob("CommunitySummary_*.json"))

    clock.advance(301)
    calls_after_first = len(client.calls)
    d._consolidation_tick()
    assert len(client.calls) > calls_after_first, "daemon survives and retries"

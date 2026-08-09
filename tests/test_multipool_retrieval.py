"""Tests for AgentRunbook multi-pool retrieval in ``compile_context``.

CI-safe + deterministic: ``synthesize=False``, ``project_root=None`` (bodies
come from ``node.description``), explicit ``HashEmbeddingBackend`` so
hybrid-search ranking is isolation-stable, no network/LLM.

The feature is opt-in via ``multi_pool=True``: when on, the query is decomposed
into sub-queries and the most relevant distilled-memory node of each pool
(``Runbook`` / ``Gotcha`` / ``Event``) in the neighbourhood is reserved a budget
slot. When off, behaviour is byte-identical to the legacy single-pool path.
"""

from __future__ import annotations

from typing import List

import pytest

from tesserae.context_compiler import compile_context
from tesserae.research_graph import (
    PROCEDURAL_POOL_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.retrieval.hybrid import HashEmbeddingBackend


def _backend() -> HashEmbeddingBackend:
    return HashEmbeddingBackend()


def _graph_with_runbook() -> ResearchGraph:
    """Raw findings about deploying, plus a distilled ``Runbook`` linked to them.

    The findings carry the query-matching tokens so hybrid search seeds on them;
    the Runbook is reachable only via ``derived_from`` edges (one hop), so it
    surfaces in the neighbourhood but is NOT a direct hybrid hit — exactly the
    case multi-pool reservation is meant to rescue.
    """
    nodes: List[ResearchNode] = [
        ResearchNode(
            id="f1",
            name="Deploy step: run migrations first",
            type=ResearchNodeType.SESSION_DECISION,
            description="When deploying the service we run database migrations "
            "before restarting workers. " * 6,
        ),
        ResearchNode(
            id="f2",
            name="Deploy step: drain the queue",
            type=ResearchNodeType.SESSION_INSIGHT,
            description="Deploying safely means draining the work queue before "
            "rolling the deployment. " * 6,
        ),
        ResearchNode(
            id="rb",
            name="Runbook: Deploying the service",
            type=ResearchNodeType.RUNBOOK,
            description="Procedure for deploying: migrate, drain, roll, verify. "
            * 6,
            # Producer provenance: a reserved procedural slot is earned by the
            # pass that made the node, not by its type (roadmap step 4). Without
            # this the fixture is a document extraction and reservation would
            # correctly refuse it.
            metadata={
                "extractor": "memory.distill.run_distillation_pass",
                "member_ids": ["f1", "f2", "f3"],
            },
        ),
        # A filler raw finding to compete for budget slots.
        ResearchNode(
            id="f3",
            name="Deploy step: verify health checks",
            type=ResearchNodeType.SESSION_INSIGHT,
            description="After deploying, verify health checks pass on every "
            "node before declaring success. " * 6,
        ),
    ]
    edges = [
        ResearchEdge(source="rb", target="f1", type="derived_from"),
        ResearchEdge(source="rb", target="f2", type="derived_from"),
        ResearchEdge(source="rb", target="f3", type="derived_from"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_multi_pool_surfaces_runbook() -> None:
    graph = _graph_with_runbook()
    bundle = compile_context(
        graph,
        project_root=None,
        query="how do we deploy the service",
        depth=2,
        budget=600,  # tight: only a couple of bodies fit
        backend=_backend(),
        multi_pool=True,
    )
    selected_ids = {c.node_id for c in bundle.citations}
    assert "rb" in selected_ids, (
        "multi-pool should reserve a slot for the in-neighbourhood Runbook"
    )


def test_single_pool_default_unchanged() -> None:
    """The default (multi_pool=False) path is byte-identical with/without the
    new keyword — the feature is purely additive and off by default."""
    graph = _graph_with_runbook()
    common = dict(
        project_root=None,
        query="how do we deploy the service",
        depth=2,
        budget=600,
        backend=_backend(),
    )
    legacy = compile_context(graph, **common)  # multi_pool defaults to False
    explicit_off = compile_context(graph, multi_pool=False, **common)
    assert legacy.body == explicit_off.body
    assert [c.node_id for c in legacy.citations] == [
        c.node_id for c in explicit_off.citations
    ]


def test_multi_pool_empty_query_is_safe() -> None:
    """Empty query + no seeds returns the empty-but-valid bundle, multi_pool on."""
    graph = _graph_with_runbook()
    bundle = compile_context(
        graph, project_root=None, query="", backend=_backend(), multi_pool=True
    )
    assert bundle.citations == []
    assert bundle.char_budget_used == 0


# --------------------------------------------------------------------------
# Producer-scoped pools (roadmap step 4)
#
# The five pool types are PRODUCER-OWNED: Runbook/Gotcha/DistilledNote come
# from the distillation passes, Event from the session-event pass,
# ExpertiseProfile from agent distillation. Document extraction is allowed to
# mint the same type names, so a conference deadline can land typed `Event`.
# A reserved procedural slot belongs to the producer's node, never to the
# document twin that merely shares its type.
# --------------------------------------------------------------------------

# (pool type, metadata proving the producer made it, the twin's decoy text)
_PRODUCER_CASES = [
    pytest.param(
        ResearchNodeType.RUNBOOK,
        {
            "extractor": "memory.distill.run_distillation_pass",
            "member_ids": ["f1", "f2"],
        },
        id="runbook-memory-distill",
    ),
    pytest.param(
        ResearchNodeType.GOTCHA,
        {"lineage_key": "abc123", "member_refs": [{"node_id": "f1"}],
         "distill_quality": "llm"},
        id="gotcha-agent-distill",
    ),
    pytest.param(
        ResearchNodeType.EVENT,
        {"extractor": "session-event", "session_id": "s1", "turn_id": 4},
        id="event-session-event-pass",
    ),
    pytest.param(
        ResearchNodeType.EXPERTISE_PROFILE,
        {"agent": "claude-code:acct:implementer", "session_count": 12},
        id="profile-agent-key",
    ),
]


def _graph_with_twin_pool_nodes(
    pool: ResearchNodeType, provenance: dict
) -> ResearchGraph:
    """Two same-typed pool nodes: a document extraction that WINS on relevance,
    and the real producer output that loses on relevance.

    Both hang one ``derived_from`` hop off the query-matching findings, so both
    are in-neighbourhood. The document twin repeats the query tokens, so plain
    PPR ranks it above the producer's node — meaning a type-only reservation
    picks the document twin, and only a producer-scoped one picks the real node.
    """
    nodes: List[ResearchNode] = [
        ResearchNode(
            id="f1",
            name="Deploy step: run migrations first",
            type=ResearchNodeType.SESSION_DECISION,
            description="When deploying the service we run database migrations "
            "before restarting workers. " * 6,
        ),
        ResearchNode(
            id="f2",
            name="Deploy step: drain the queue",
            type=ResearchNodeType.SESSION_INSIGHT,
            description="Deploying safely means draining the work queue before "
            "rolling the deployment. " * 6,
        ),
        # The document extraction: no producer ever made it, it just landed on
        # a procedural type name while the LLM read a document.
        ResearchNode(
            id="doc_twin",
            name="Deploying the service at the deploy conference",
            type=pool,
            description="Deploy deploy deploying the service deployment "
            "migrations queue workers. " * 8,
            metadata={"source_kind": "document"},
        ),
        # The real thing, worded so it does NOT win on token overlap.
        ResearchNode(
            id="real",
            name="Procedure: ship a build",
            type=pool,
            description="Migrate, drain, roll, verify. " * 8,
            metadata=dict(provenance),
        ),
    ]
    edges = [
        ResearchEdge(source="doc_twin", target="f1", type="derived_from"),
        ResearchEdge(source="doc_twin", target="f2", type="derived_from"),
        ResearchEdge(source="real", target="f1", type="derived_from"),
        ResearchEdge(source="real", target="f2", type="derived_from"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


@pytest.mark.parametrize("pool,provenance", _PRODUCER_CASES)
def test_reserved_slot_goes_to_the_producer_not_the_document_twin(
    pool: ResearchNodeType, provenance: dict
) -> None:
    """The reserved slot must be earned by provenance, not by type.

    Type-only admission gives the slot to ``doc_twin`` because it out-ranks the
    real node on relevance; with a budget that fits one pool body, the producer's
    node then never reaches the bundle at all.
    """
    graph = _graph_with_twin_pool_nodes(pool, provenance)
    bundle = compile_context(
        graph,
        project_root=None,
        query="how do we deploy the service",
        depth=2,
        budget=400,  # tight: one reserved body plus change
        backend=_backend(),
        multi_pool=True,
    )
    selected_ids = [c.node_id for c in bundle.citations]

    assert "real" in selected_ids, (
        f"the reserved {pool.value} slot must go to the producer's node; "
        f"got {selected_ids}"
    )
    assert "doc_twin" not in selected_ids, (
        f"a document extraction typed {pool.value} must not occupy the "
        f"reserved procedural slot; got {selected_ids}"
    )


def test_unprovenanced_pool_node_does_not_displace_a_relevant_finding() -> None:
    """When a pool holds only document extractions the slot is not spent.

    Reservation is additive and jumps the queue: it moves its pick to the FRONT
    of the budget walk from anywhere in the neighbourhood. So an unearned
    reservation does not merely add noise, it evicts the most relevant finding
    from a tight budget. With the pool empty of real producer output the budget
    must be released back to relevance ranking.
    """
    graph = _graph_with_runbook()
    # A conference deadline the document extractor typed `Event`: reachable in
    # one hop (so it is in-neighbourhood) but worded nothing like the query, so
    # relevance ranking puts it last and only reservation can surface it. This
    # is the shape the live graph is entirely made of.
    graph.nodes.append(
        ResearchNode(
            id="conf",
            name="CVPR 2026",
            type=ResearchNodeType.EVENT,
            description="Paper submission deadline for the conference. " * 8,
            metadata={"source_kind": "document"},
        )
    )
    graph.edges.append(ResearchEdge(source="conf", target="f1", type="derived_from"))

    off = compile_context(
        graph,
        project_root=None,
        query="how do we deploy the service",
        depth=2,
        budget=700,
        backend=_backend(),
        multi_pool=False,
    )
    on = compile_context(
        graph,
        project_root=None,
        query="how do we deploy the service",
        depth=2,
        budget=700,
        backend=_backend(),
        multi_pool=True,
    )

    assert [c.node_id for c in on.citations] == [c.node_id for c in off.citations], (
        "with no producer-made node in any pool, multi_pool must not change "
        "which nodes get the budget"
    )
    assert "conf" not in [c.node_id for c in on.citations], (
        "a document-extracted Event must not be promoted to the front of the "
        "budget walk; it evicts the finding that earned the slot"
    )


# --------------------------------------------------------------------------
# The producer list must be read off EVERY writer, not off the distillation
# ones only. ``graph_write`` (``tesserae.agent_write``) is the fourth producer:
# an agent deliberately authoring a Runbook or Gotcha through the typed write
# path. Its output is the most explicitly procedural content in the system and
# was excluded by the first cut of the predicate.
# --------------------------------------------------------------------------


def _agent_written_runbook(tmp_path, *, name: str, description: str) -> ResearchNode:
    """Mint a Runbook through the REAL ``graph_write`` path.

    Not a hand-written metadata dict: the point of this test is that the
    predicate agrees with what the writer actually stamps, so the node has to
    come from the writer. ``record_agent_write`` validates + appends, and
    ``replay_agent_writes`` rebuilds exactly what a compile would fold in.
    """
    from tesserae.agent_write import record_agent_write, replay_agent_writes

    path = tmp_path / "agent-writes.jsonl"
    record_agent_write(
        path,
        {
            "nodes": [
                {
                    "name": name,
                    "type": ResearchNodeType.RUNBOOK.value,
                    "description": description,
                }
            ],
            "edges": [],
            "provenance": {
                "agent": "claude-code:acct:implementer",
                "file": "docs/deploy.md",
            },
        },
        "claude-code:acct:implementer",
    )
    replayed = replay_agent_writes(path)
    written = [
        node for node in replayed.nodes if node.type is ResearchNodeType.RUNBOOK
    ]
    assert len(written) == 1, f"expected one written Runbook; got {written}"
    return written[0]


def test_reserved_slot_goes_to_an_agent_written_runbook(tmp_path) -> None:
    """A Runbook an agent wrote on purpose must be able to take its own slot.

    ``graph_write`` is a procedural producer: ``agent_write._graph_from_record``
    stamps ``agent_write_id`` / ``agent_key`` / ``agent_write_provenance`` onto
    every node it mints, and ``DENIED_NODE_TYPES`` lets an agent mint exactly
    Runbook and Gotcha among the five pool types. A provenance predicate read
    only off the distillation writers rejects it — locking the most deliberate
    procedural content in the system out of its own pool, which is the
    wrong-inclusion defect with the sign flipped.
    """
    runbook = _agent_written_runbook(
        tmp_path,
        name="Procedure: ship a build",
        description="Migrate, drain, roll, verify. " * 8,
    )
    graph = _graph_with_twin_pool_nodes(ResearchNodeType.RUNBOOK, {})
    # Swap the fixture's stand-in for the genuinely agent-written node, keeping
    # the id the writer minted (``ResearchNode`` is frozen, and the real id is
    # part of what is under test).
    graph.nodes = [node for node in graph.nodes if node.id != "real"]
    graph.edges = [edge for edge in graph.edges if edge.source != "real"]
    graph.nodes.append(runbook)
    graph.edges.extend(
        ResearchEdge(source=runbook.id, target=target, type="derived_from")
        for target in ("f1", "f2")
    )

    bundle = compile_context(
        graph,
        project_root=None,
        query="how do we deploy the service",
        depth=2,
        budget=400,
        backend=_backend(),
        multi_pool=True,
    )
    selected_ids = [c.node_id for c in bundle.citations]

    assert runbook.id in selected_ids, (
        "an agent-written Runbook is producer-made and must be able to take "
        f"the reserved Runbook slot; got {selected_ids}"
    )
    assert "doc_twin" not in selected_ids, selected_ids
    assert bundle.pool_reservations["Runbook"] is not None, (
        f"the Runbook pool must report its reservation; got "
        f"{bundle.pool_reservations!r}"
    )


# --------------------------------------------------------------------------
# ``pool_reservations`` is the honesty field. It has to be honest itself.
# --------------------------------------------------------------------------


def _graph_with_two_producer_pools() -> ResearchGraph:
    """Producer-made Runbook AND Gotcha, both one hop off the query findings.

    Two pools reserve, reservation puts both at the FRONT of the budget walk,
    and a budget that fits only one body means the second reserved node never
    reaches the bundle.
    """
    graph = _graph_with_twin_pool_nodes(
        ResearchNodeType.RUNBOOK,
        {
            "extractor": "memory.distill.run_distillation_pass",
            "member_ids": ["f1", "f2"],
        },
    )
    graph.nodes = [node for node in graph.nodes if node.id != "doc_twin"]
    graph.edges = [edge for edge in graph.edges if edge.source != "doc_twin"]
    graph.nodes.append(
        ResearchNode(
            id="gotcha",
            name="Pitfall: rolling before the drain finishes",
            type=ResearchNodeType.GOTCHA,
            description="Never roll workers before the queue drain completes. " * 8,
            metadata={
                "extractor": "memory.distill.run_distillation_pass",
                "member_ids": ["f1"],
            },
        )
    )
    graph.edges.append(
        ResearchEdge(source="gotcha", target="f1", type="derived_from")
    )
    return graph


def test_pool_reservations_does_not_claim_a_pool_the_budget_dropped() -> None:
    """A pool whose reserved node never reached the bundle is not a served pool.

    The field exists so an operator can tell an empty pool from a working one.
    Reporting "Runbook: <id>" for a node the budget walk truncated away is the
    same silent-degradation defect, relocated into the reporting: the caller
    reads procedural memory as delivered when nothing from that pool was.
    """
    graph = _graph_with_two_producer_pools()
    bundle = compile_context(
        graph,
        project_root=None,
        query="how do we deploy the service",
        depth=2,
        budget=200,  # fits the first reserved body only
        backend=_backend(),
        multi_pool=True,
    )
    delivered = set(bundle.selected_nodes)
    reserved = {"real", "gotcha"}
    assert reserved - delivered, (
        "fixture no longer exercises truncation: both reserved nodes reached "
        f"the bundle ({sorted(delivered)})"
    )

    reservations = bundle.pool_reservations
    assert reservations is not None
    for pool, entry in reservations.items():
        if entry is None:
            continue
        node_id = entry["node_id"] if isinstance(entry, dict) else entry
        was_delivered = entry.get("delivered") if isinstance(entry, dict) else None
        assert was_delivered is not None, (
            f"pool {pool} reports {entry!r}: a reservation that says nothing "
            "about delivery cannot be told apart from a served pool"
        )
        assert was_delivered == (node_id in delivered), (
            f"pool {pool} reports delivered={was_delivered} for {node_id}, but "
            f"the bundle selected {sorted(delivered)}"
        )


def test_pool_reservations_none_means_only_that_reservation_never_ran() -> None:
    """One value, one meaning.

    ``None`` is documented as "multi_pool was off". The empty-seed early return
    also produced it with ``multi_pool=True``, so a caller reading ``None``
    could not tell "you did not ask for pools" from "you asked and the query
    resolved to nothing".
    """
    graph = _graph_with_runbook()
    off = compile_context(
        graph,
        project_root=None,
        query="",
        seeds=[],
        backend=_backend(),
        multi_pool=False,
    )
    on = compile_context(
        graph,
        project_root=None,
        query="",
        seeds=[],
        backend=_backend(),
        multi_pool=True,
    )

    assert off.pool_reservations is None
    assert on.pool_reservations is not None, (
        "multi_pool was on, so the caller asked about the pools and must be "
        "told about them — None is reserved for 'reservation never ran'"
    )
    assert set(on.pool_reservations) == {
        item.value for item in PROCEDURAL_POOL_TYPES
    }
    assert all(entry is None for entry in on.pool_reservations.values())


def test_the_compiler_reserves_exactly_the_declared_pool_types(monkeypatch) -> None:
    """One list of pool types, in ``research_graph``, with nothing duplicating it.

    ``compile_context`` carried its own literal copy, so the two could disagree
    silently — a type added to the vocabulary's pool set would keep no slot,
    and a type dropped from it would keep one. Narrowing the declared order has
    to narrow what the compiler reserves.
    """
    import tesserae.research_graph as rg

    monkeypatch.setattr(
        rg, "PROCEDURAL_POOL_ORDER", (ResearchNodeType.GOTCHA,), raising=False
    )
    bundle = compile_context(
        _graph_with_runbook(),
        project_root=None,
        query="how do we deploy the service",
        depth=2,
        budget=4000,
        backend=_backend(),
        multi_pool=True,
    )
    assert set(bundle.pool_reservations) == {ResearchNodeType.GOTCHA.value}, (
        "the compiler must read its pool list from research_graph, not from a "
        f"second literal; got {bundle.pool_reservations!r}"
    )

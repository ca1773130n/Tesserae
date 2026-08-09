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

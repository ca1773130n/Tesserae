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

"""LocalScope — a set-valued boundary between lexical retrieval and the walk.

The walk is corpus-linear on every axis that matters: ``hybrid_search``
defaults its candidate set to the whole graph, and ``personalized_pagerank``
rebuilds whole-graph adjacency per call. Both stop being corpus-linear the
moment the graph handed to them is already small. ``compile_context`` has the
seam for that — ``restrict``, an arbitrary node-id set that rebinds
``graph = _induced_subgraph(graph, restrict)`` BEFORE seed resolution, PPR,
the depth BFS and selection. ``scope=`` and ``strategy="hierarchical"`` are two
producers of that set. A :class:`LocalScope` is a third, sourced from lexical
retrieval instead of the hierarchy sidecar, so no new walk plumbing exists.

**Why the types are what they are.** ``node_ids`` is a set and
``seed_weights`` is a mapping, deliberately. ``_induced_subgraph`` filters
``graph.nodes`` in GRAPH order, so membership alone determines the induced
graph; ``personalized_pagerank`` looks weights up BY SEED ID, so relevance mass
is position-free. First-stage rank ORDER is therefore discarded at this
boundary *by construction* rather than by care — which is the invariant a
previous walk/seed fusion broke and was reverted for.

**Why ``hops`` is a bound and not a tunable.** Measured on the real
62,366-node graph, 1-hop induction from top-N lexical DOCUMENTS is a
per-document structural constant (~7.4 nodes/doc, sublinear in N because
neighbourhoods overlap): N=200 induces 1,478 nodes, 2.4% of the graph. Two hops
is a hub explosion in the same measurement — N=10 already reaches 13,636 nodes
and N=200 reaches 38,848, i.e. 62% of the entire graph. So ``hops`` above 1 is
refused here rather than left to a caller's judgement. This does not conflict
with ``compile_context(depth=2)``: that depth is measured from the PPR seeds
AFTER induction and is bounded by the induced graph. Order of operations is
load-bearing.

**``hops=1`` is necessary and NOT sufficient — one hub anchor is a second
hop.** Retrieval does not hand back documents; it hands back whatever scored,
and on this graph that includes ``CommunitySummary`` nodes of degree 6,105.
Measured, same graph, top-200 hybrid hits for a real query: all 200 anchors
induce 31,111 nodes (49.9% of the graph), but the 168 anchors of degree <= 200
induce 758 (1.2%) — the five anchors above degree 1,000 add 15,760 nodes
between them. A boundary that admits half the corpus costs the recall ceiling
and buys nothing, so ``max_nodes`` bounds the INDUCED SIZE directly rather than
trusting the hop count: anchors are expanded in ascending-degree order until
the budget is spent, and the rest contribute themselves but not their
neighbourhoods. That is the same trade ``personalized_pagerank(tame_hubs=True)``
already makes — a hub is kept as a destination and refused as a thoroughfare —
and it is why the bound is corpus-size-independent by construction instead of
by hope.

This module ships the IN-MEMORY producer, which reads the already-loaded graph
and therefore buys no latency in the first stage itself — it exists to answer
the quality question (does restricting the walk to lexical candidates hold up?)
on the corpora that exist today. A store-backed producer that never loads the
graph is a separate piece of work with its own storage prerequisites.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, FrozenSet, List, Mapping, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from ..research_graph import ResearchGraph
    from .bm25_index import Bm25Index
    from .hybrid import EmbeddingBackend
    from .vector_cache import VectorCache

__all__ = ["LocalScope", "local_scope_from_graph"]


@dataclass(frozen=True)
class LocalScope:
    """A first-stage retrieval result on its way into the walk.

    :param node_ids: every node the walk may see — the retrieved anchors plus
        their ``hops``-hop neighbourhood. Empty means "the first stage matched
        nothing", which :func:`~tesserae.context_compiler.compile_context`
        treats as no restriction at all rather than as an empty graph.
    :param seed_weights: relevance mass per ANCHOR id, for PPR's personalization
        vector. Anchors only; the neighbours in ``node_ids`` carry none.
    :param hops: how far the induction reached from the anchors. 0 or 1 only —
        see the module docstring for the measurement that makes 2 a defect.
    """

    node_ids: FrozenSet[str]
    seed_weights: Mapping[str, float]
    hops: int = 1

    def __post_init__(self) -> None:
        if self.hops not in (0, 1):
            raise ValueError(
                f"LocalScope: hops={self.hops!r} — only 0 or 1 is admissible. "
                f"Measured on the real 62,366-node graph, 2-hop induction from "
                f"200 lexical documents reaches 38,848 nodes (62% of the "
                f"graph), so the bound the scope exists to provide evaporates. "
                f"Use compile_context(depth=...) to walk further INSIDE the "
                f"induced subgraph."
            )
        if any(w < 0.0 for w in self.seed_weights.values()):
            raise ValueError("LocalScope: seed_weights must be non-negative")


def local_scope_from_graph(
    graph: "ResearchGraph",
    query: str,
    *,
    top_n: int = 200,
    hops: int = 1,
    max_nodes: int = 4_000,
    backend: Optional["EmbeddingBackend"] = None,
    vector_cache: Optional["VectorCache"] = None,
    bm25_index: Optional["Bm25Index"] = None,
    source_root: Optional["Path"] = None,
) -> LocalScope:
    """Build a :class:`LocalScope` from an already-loaded graph.

    Runs one ``hybrid_search`` over the whole graph for the anchors, then
    induces their ``hops``-hop neighbourhood with ``compile_context``'s own
    :func:`~tesserae.context_compiler._neighborhood_within_depth` — the same
    BFS the walk uses downstream, not a parallel one.

    Anchors that scored zero are dropped: they contribute no teleport mass, and
    keeping them would widen the boundary on the strength of a non-match. A
    query that matches nothing yields an EMPTY scope, which the caller reads as
    "do not restrict" — degrading to the unrestricted walk, exactly as
    ``strategy="hierarchical"`` degrades when no summary matches.

    ``max_nodes`` caps the induced size (see the module docstring for the
    measurement that makes it necessary). Anchors are expanded cheapest-first by
    graph DEGREE, tie-broken by id — a property of the graph and the anchor set,
    never of the retrieval order, so the boundary stays position-free. The
    budget is spent against cumulative degree, which over-counts overlap and is
    therefore conservative: the realised scope is at or under ``max_nodes``.
    Every anchor is admitted to ``node_ids`` whether or not it was expanded.
    """
    if top_n < 1:
        raise ValueError(f"local_scope_from_graph: top_n={top_n!r} must be >= 1")
    if max_nodes < 1:
        raise ValueError(
            f"local_scope_from_graph: max_nodes={max_nodes!r} must be >= 1"
        )

    from .hybrid import hybrid_search

    result = hybrid_search(
        graph,
        query,
        top_k=top_n,
        backend=backend,
        vector_cache=vector_cache,
        bm25_index=bm25_index,
        source_root=source_root,
    )
    weights: Dict[str, float] = {
        scored.node.id: float(scored.score)
        for scored in result.scored
        if scored.score > 0.0
    }
    if not weights:
        return LocalScope(node_ids=frozenset(), seed_weights={}, hops=hops)

    # Degree over the UNDIRECTED edge set, matching the BFS below and PPR's
    # default ``directed=False``. One O(E) pass, the same order of work the BFS
    # already does to build its own adjacency.
    degree: Dict[str, int] = {}
    for edge in graph.edges:
        degree[edge.source] = degree.get(edge.source, 0) + 1
        degree[edge.target] = degree.get(edge.target, 0) + 1

    # Cheapest-first by (degree, id). Nothing here reads the retrieval order.
    ordered = sorted(weights, key=lambda nid: (degree.get(nid, 0), nid))
    expandable: List[str] = []
    spent = len(ordered)  # the anchors themselves are already in the scope
    for nid in ordered:
        cost = degree.get(nid, 0)
        if spent + cost > max_nodes:
            # Ascending degree: nothing after this one is cheaper, and the
            # spend only grows, so no later anchor can fit either.
            break
        expandable.append(nid)
        spent += cost

    # Local import: context_compiler imports this module, so a module-level
    # import here would close the cycle. Same pattern the charter and hierarchy
    # imports use in context_compiler.
    from ..context_compiler import _neighborhood_within_depth

    reachable = set(weights)
    if expandable:
        reachable |= _neighborhood_within_depth(graph, expandable, max(0, hops))
    return LocalScope(
        node_ids=frozenset(reachable), seed_weights=dict(weights), hops=hops
    )

"""Shared read-path node filters over a :class:`ResearchGraph`.

Home of the *suppression set* used by every read surface (MCP tools,
``compile_context``): nodes that lost to a newer/winning claim must not be
served as current knowledge unless the caller opts in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Set

from .research_graph import RETRACTION_EDGE_TYPES, UNTRUSTED_EDGE_STATUSES

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers
    from .research_graph import ResearchGraph

__all__ = [
    "SUPPRESSION_EDGE_TYPES",
    "asserts_suppression",
    "invalidations",
    "retracted_ids",
    "superseded_ids",
    "suppressed_ids",
]

#: Every edge type that can mint a suppressed node. Derived here rather than
#: spelled out again by callers so a future suppression class is picked up by
#: the keyed store's probe the same day it is picked up by
#: :func:`suppressed_ids` — the two must never disagree about which edges
#: matter, because one is used to decide which edges to FETCH for the other.
SUPPRESSION_EDGE_TYPES: frozenset = (
    frozenset({"supersedes", "resolved_by"}) | RETRACTION_EDGE_TYPES
)


def asserts_suppression(edge) -> bool:
    """Whether a suppression-typed edge actually suppresses (0.42, T4).

    An edge whose own ``status`` is ``candidate`` / ``refuted`` / ``retracted``
    is a proposal or a withdrawn assertion — letting it hide its target would
    let an unvalidated "this replaces that" silence curated knowledge, which is
    exactly what the default read path refuses. Edges without a status (every
    0.41 edge) suppress as before.
    """
    return getattr(edge, "status", None) not in UNTRUSTED_EDGE_STATUSES


def superseded_ids(graph: "ResearchGraph") -> Set[str]:
    """Ids of nodes that lost — superseded or arbitration losers.

    Two edge types mint losers, with opposite orientations:

    - ``supersedes``: ``source supersedes target`` (canonical per
      ``tesserae.memory.supersede``) — the *target* is the older loser.
    - ``resolved_by``: ``source resolved_by target`` (canonical per
      ``tesserae.memory.contradiction``) — the *source* is the losing claim.

    All read paths (search_nodes, fresh_insights, node_context,
    compile_context) suppress the same set so a claim that lost LLM
    arbitration is never cited identically to its winner.
    """
    return {
        edge.target
        for edge in graph.edges
        if edge.type == "supersedes" and asserts_suppression(edge)
    } | {
        edge.source
        for edge in graph.edges
        if edge.type == "resolved_by" and asserts_suppression(edge)
    }


def retracted_ids(graph: "ResearchGraph") -> Set[str]:
    """Ids of nodes an agent has retracted — "this is wrong" (step 10).

    ``source retracts target``: the TARGET is the retracted node, the same
    orientation ``supersedes`` uses. Kept separate from
    :func:`superseded_ids` because the two say different things — superseded
    means *a winner replaced it*, retracted means *nobody replaced it and it
    should not have been asserted* — and a caller that wants one rarely wants
    the distinction blurred.

    Nothing is deleted. The node and the retraction edge both stay in the
    graph, so ``include_superseded=True`` still reaches them and the
    retraction itself remains readable evidence.
    """
    return {
        edge.target
        for edge in graph.edges
        if edge.type in RETRACTION_EDGE_TYPES and asserts_suppression(edge)
    }


def invalidations(graph: "ResearchGraph") -> Dict[str, List[Dict[str, object]]]:
    """``{node_id: [{invalidated_by, invalid_at, asserted_by}, ...]}``.

    An invalidation is a ``supersedes`` edge that says WHEN its target stopped
    holding — it carries ``invalid_at`` (Graphiti's valid-time end). The
    opt-in write-time reconciliation pass (:mod:`tesserae.memory.reconcile`)
    is what writes them. Edges whose own ``status`` is untrusted are skipped,
    as in :func:`superseded_ids`.

    This is for the surfaces that must DEMOTE rather than FILTER an
    invalidated fact (``raw_source``, ``verify_claim``): they return the fact
    and attach this record, because a wrong invalidation must never be able to
    make a correct fact unreadable. Entries are sorted per node by
    ``(invalid_at, invalidated_by)``.
    """
    out: Dict[str, List[Dict[str, object]]] = {}
    for edge in graph.edges:
        if edge.type != "supersedes" or getattr(edge, "invalid_at", None) is None:
            continue
        if not asserts_suppression(edge):
            continue
        out.setdefault(edge.target, []).append(
            {
                "invalidated_by": edge.source,
                "invalid_at": edge.invalid_at,
                "asserted_by": getattr(edge, "asserted_by", None),
            }
        )
    for items in out.values():
        items.sort(key=lambda item: (str(item["invalid_at"]), str(item["invalidated_by"])))
    return out


def suppressed_ids(graph: "ResearchGraph") -> Set[str]:
    """Every node a default read must not serve as current knowledge.

    The union of :func:`superseded_ids` and :func:`retracted_ids` — ONE call
    for every read surface, so a future suppression class is picked up by
    search_nodes / fresh_insights / node_context / compile_context the day it
    is added here rather than in six places that drift apart.
    """
    return superseded_ids(graph) | retracted_ids(graph)

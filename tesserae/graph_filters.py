"""Shared read-path node filters over a :class:`ResearchGraph`.

Home of the *suppression set* used by every read surface (MCP tools,
``compile_context``): nodes that lost to a newer/winning claim must not be
served as current knowledge unless the caller opts in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Set

from .research_graph import RETRACTION_EDGE_TYPES

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers
    from .research_graph import ResearchGraph

__all__ = [
    "SUPPRESSION_EDGE_TYPES",
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
    return {edge.target for edge in graph.edges if edge.type == "supersedes"} | {
        edge.source for edge in graph.edges if edge.type == "resolved_by"
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
        if edge.type in RETRACTION_EDGE_TYPES
    }


def suppressed_ids(graph: "ResearchGraph") -> Set[str]:
    """Every node a default read must not serve as current knowledge.

    The union of :func:`superseded_ids` and :func:`retracted_ids` — ONE call
    for every read surface, so a future suppression class is picked up by
    search_nodes / fresh_insights / node_context / compile_context the day it
    is added here rather than in six places that drift apart.
    """
    return superseded_ids(graph) | retracted_ids(graph)

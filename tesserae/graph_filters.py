"""Shared read-path node filters over a :class:`ResearchGraph`.

Home of the *suppression set* used by every read surface (MCP tools,
``compile_context``): nodes that lost to a newer/winning claim must not be
served as current knowledge unless the caller opts in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Set

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for type checkers
    from .research_graph import ResearchGraph

__all__ = ["superseded_ids"]


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

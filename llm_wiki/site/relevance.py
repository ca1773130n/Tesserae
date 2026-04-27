"""Four-signal relevance scoring used by ``Related`` sections.

Reference: §3.3 of the redesign design spec —

  - Direct link weight 3.0 — any edge between the two nodes.
  - Source-overlap weight 4.0 — per shared ``source_path``.
  - Adamic-Adar weight 1.5 — sum over shared neighbours of
    ``1 / log(1 + degree(neighbour))``.
  - Type affinity weight 1.0 — both nodes share a ``ResearchNodeType``.

The functions are pure and depend only on the standard library; they take a
prebuilt ``RelevanceContext`` so the per-graph adjacency / source-path
indices are computed once and shared across many ``score`` / ``top_related``
calls.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, MutableMapping, Set, Tuple

from ..research_graph import ResearchGraph


# Signal weights (kept module-level so tests / callers can introspect).
WEIGHT_LINK: float = 3.0
WEIGHT_SOURCE: float = 4.0
WEIGHT_ADAMIC_ADAR: float = 1.5
WEIGHT_TYPE: float = 1.0


@dataclass(frozen=True)
class RelevanceContext:
    """Pre-computed indices over a ``ResearchGraph``.

    Build it once per graph; reuse it across many ``score`` / ``top_related``
    calls. The dataclass is ``frozen`` for safe sharing — internal mappings
    are plain dicts (assigned via ``object.__setattr__`` in ``__init__``).
    """

    nodes_by_id: Mapping[str, object]
    types: Mapping[str, str]
    neighbors: Mapping[str, Set[str]]      # node_id -> set of neighbouring ids
    degrees: Mapping[str, int]             # node_id -> degree (len(neighbors))
    sources_by_node: Mapping[str, Set[str]]  # node_id -> set of source paths
    nodes_by_source: Mapping[str, Set[str]]  # source path -> set of node ids
    edge_pairs: Set[Tuple[str, str]]       # unordered pairs as (a, b) with a<=b

    @classmethod
    def from_graph(cls, graph: ResearchGraph) -> "RelevanceContext":
        nodes_by_id = {node.id: node for node in graph.nodes}
        neighbors: MutableMapping[str, Set[str]] = {nid: set() for nid in nodes_by_id}
        edge_pairs: Set[Tuple[str, str]] = set()
        for edge in graph.edges:
            if edge.source == edge.target:
                continue
            if edge.source in neighbors:
                neighbors[edge.source].add(edge.target)
            if edge.target in neighbors:
                neighbors[edge.target].add(edge.source)
            a, b = sorted((edge.source, edge.target))
            edge_pairs.add((a, b))

        degrees = {nid: len(adj) for nid, adj in neighbors.items()}

        types = {node.id: node.type.value for node in graph.nodes}

        sources_by_node: MutableMapping[str, Set[str]] = {}
        nodes_by_source: MutableMapping[str, Set[str]] = {}
        for node in graph.nodes:
            paths: Set[str] = set()
            if node.source_path:
                paths.add(node.source_path)
            extra = node.metadata.get("source_paths") if isinstance(node.metadata, dict) else None
            if isinstance(extra, (list, tuple, set)):
                for path in extra:
                    if isinstance(path, str) and path:
                        paths.add(path)
            sources_by_node[node.id] = paths
            for path in paths:
                nodes_by_source.setdefault(path, set()).add(node.id)

        return cls(
            nodes_by_id=nodes_by_id,
            types=types,
            neighbors=neighbors,
            degrees=degrees,
            sources_by_node=sources_by_node,
            nodes_by_source=nodes_by_source,
            edge_pairs=edge_pairs,
        )


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

def _link_signal(a: str, b: str, ctx: RelevanceContext) -> float:
    if a == b:
        return 0.0
    pair = (a, b) if a <= b else (b, a)
    return 1.0 if pair in ctx.edge_pairs else 0.0


def _source_signal(a: str, b: str, ctx: RelevanceContext) -> float:
    sa = ctx.sources_by_node.get(a, set())
    sb = ctx.sources_by_node.get(b, set())
    if not sa or not sb:
        return 0.0
    return float(len(sa & sb))


def _adamic_adar_signal(a: str, b: str, ctx: RelevanceContext) -> float:
    na = ctx.neighbors.get(a, set())
    nb = ctx.neighbors.get(b, set())
    shared = na & nb
    if not shared:
        return 0.0
    total = 0.0
    for neighbour in shared:
        deg = ctx.degrees.get(neighbour, 0)
        # ``log(1 + 1) == log(2)`` so a neighbour with a single connection
        # still contributes a finite value.
        total += 1.0 / math.log(1.0 + max(deg, 1))
    return total


def _type_signal(a: str, b: str, ctx: RelevanceContext) -> float:
    ta = ctx.types.get(a)
    tb = ctx.types.get(b)
    if ta is None or tb is None:
        return 0.0
    return 1.0 if ta == tb else 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score(node_a_id: str, node_b_id: str, ctx: RelevanceContext) -> float:
    """Compute the weighted four-signal relevance between two nodes.

    Identical ids return ``0.0``; unknown ids are treated as missing
    everywhere and just yield ``0.0``.
    """
    if node_a_id == node_b_id:
        return 0.0
    total = 0.0
    total += WEIGHT_LINK * _link_signal(node_a_id, node_b_id, ctx)
    total += WEIGHT_SOURCE * _source_signal(node_a_id, node_b_id, ctx)
    total += WEIGHT_ADAMIC_ADAR * _adamic_adar_signal(node_a_id, node_b_id, ctx)
    total += WEIGHT_TYPE * _type_signal(node_a_id, node_b_id, ctx)
    return total


def top_related(
    node_id: str,
    ctx: RelevanceContext,
    *,
    limit: int = 8,
) -> list[tuple[str, float]]:
    """Return up to ``limit`` related nodes, sorted by descending score.

    Nodes scoring ``0.0`` are dropped. Ties are broken by node id so the
    output is deterministic for tests / idempotent rebuilds.
    """
    if node_id not in ctx.nodes_by_id:
        return []
    scored: list[tuple[str, float]] = []
    for other_id in ctx.nodes_by_id:
        if other_id == node_id:
            continue
        s = score(node_id, other_id, ctx)
        if s > 0:
            scored.append((other_id, s))
    scored.sort(key=lambda item: (-item[1], item[0]))
    if limit is None or limit < 0:
        return scored
    return scored[:limit]


__all__ = [
    "RelevanceContext",
    "WEIGHT_ADAMIC_ADAR",
    "WEIGHT_LINK",
    "WEIGHT_SOURCE",
    "WEIGHT_TYPE",
    "score",
    "top_related",
]

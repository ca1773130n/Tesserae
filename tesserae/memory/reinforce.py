"""Recurring-insight reinforcement pass (KB-05).

The legacy ``temporal.infer_confidence`` is a 4-line string heuristic that
ignores how often an insight recurs across sessions. This pass turns
cross-session *frequency* into a confidence signal: a session finding (or
its near-duplicate cluster) that surfaces in ``>= threshold`` DISTINCT
sessions is reinforced to confidence ``"high"``.

Clustering of near-duplicates is two-fold and deterministic:

1. ``supersedes`` edge chains — successive refinements of the same finding
   across sessions are one cluster (reuses the supersede pass's output).
2. Jaccard near-duplicate on node names (reuses ``supersede.jaccard``) —
   independently-emitted restatements of the same insight cluster together.

The surviving / canonical node id of a reinforced cluster (the smallest id
for stability) carries the ``"high"`` confidence. The orchestrator (05-03)
writes ``{node_id: confidence}`` into ``node_memory``; downstream
``infer_confidence`` reads it back as an override.

Pure + deterministic: no ``datetime.now()`` / RNG, threshold configurable,
output ordering irrelevant (a dict keyed on node id).
"""

from __future__ import annotations

import logging
from typing import Dict, List, Sequence, Set

from ..research_graph import SESSION_FINDING_TYPES, ResearchGraph, ResearchNode
from .supersede import jaccard

logger = logging.getLogger(__name__)

# Names whose Jaccard similarity exceeds this are treated as the same insight.
_NEAR_DUP_THRESHOLD = 0.55


def _kind(node: ResearchNode) -> str:
    return node.type.value if hasattr(node.type, "value") else str(node.type)


def _session_id(node: ResearchNode) -> str:
    raw = (node.metadata or {}).get("session_id")
    return str(raw) if raw not in (None, "") else ""


class _UnionFind:
    """Tiny deterministic union-find keyed on string ids."""

    def __init__(self) -> None:
        self._parent: Dict[str, str] = {}

    def add(self, node_id: str) -> None:
        self._parent.setdefault(node_id, node_id)

    def find(self, node_id: str) -> str:
        root = node_id
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression.
        while self._parent[node_id] != root:
            self._parent[node_id], node_id = root, self._parent[node_id]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # Smaller id becomes the canonical root for stability.
        lo, hi = (ra, rb) if ra < rb else (rb, ra)
        self._parent[hi] = lo


def compute_recurring_confidence(
    graph: ResearchGraph, *, threshold: int = 3
) -> Dict[str, str]:
    """Reinforce insights recurring across ``>= threshold`` distinct sessions.

    Returns ``{canonical_node_id: "high"}`` for each qualifying cluster;
    nodes that do not qualify are omitted. Pure / deterministic.
    """
    finding_values = {t.value for t in SESSION_FINDING_TYPES}
    findings: List[ResearchNode] = sorted(
        (n for n in graph.nodes if _kind(n) in finding_values),
        key=lambda n: n.id,
    )
    if not findings:
        return {}

    uf = _UnionFind()
    for node in findings:
        uf.add(node.id)

    finding_ids: Set[str] = {n.id for n in findings}

    # 1. supersedes chains -> same cluster.
    for edge in graph.edges:
        if edge.type != "supersedes":
            continue
        if edge.source in finding_ids and edge.target in finding_ids:
            uf.union(edge.source, edge.target)

    # 2. Jaccard near-duplicate on names (within finding set), deterministic
    #    pairwise scan ordered by id.
    for i, a in enumerate(findings):
        for b in findings[i + 1 :]:
            if uf.find(a.id) == uf.find(b.id):
                continue
            if jaccard(a.name, b.name) > _NEAR_DUP_THRESHOLD:
                uf.union(a.id, b.id)

    # Gather distinct session ids per cluster root.
    sessions_by_root: Dict[str, Set[str]] = {}
    for node in findings:
        root = uf.find(node.id)
        sid = _session_id(node)
        if not sid:
            continue
        sessions_by_root.setdefault(root, set()).add(sid)

    reinforced: Dict[str, str] = {}
    for root, sessions in sessions_by_root.items():
        if len(sessions) >= threshold:
            reinforced[root] = "high"

    if reinforced:
        logger.info(
            "memory.reinforce: reinforced %d recurring insights (threshold=%d)",
            len(reinforced),
            threshold,
        )
    return reinforced

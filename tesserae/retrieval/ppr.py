"""Personalized PageRank over the typed ResearchGraph.

Inspired by HippoRAG 2 (arXiv:2502.14802): given one or more "seed" nodes
extracted from a query, run PPR over the knowledge graph so that nodes
multiple hops away from the seed (but well-connected to it) score high,
not just the seed's immediate 1-hop neighborhood.

Implementation notes
--------------------
- Pure-Python power iteration. We avoid a ``networkx`` dependency because
  Tesserae's runtime dependency set is intentionally small (``pyproject.toml``
  pins only ``pydantic>=2``).
- Edges are aggregated by ``(source, target)`` so multiple typed edges
  between the same pair add up — a common pattern in Tesserae once a
  Session finding both ``derived_from_session`` and ``references`` another
  node.
- ``edge_type_weights`` lets callers re-tune which relationships matter.
  The defaults upweight Tesserae's session-finding edges so that PPR
  seeded at a ``SessionInsight`` tends to re-surface other Insights /
  Decisions / Sessions in the same conversational thread — the canonical
  HippoRAG-style memory-recall behaviour described in feature B of
  ``/tmp/tesserae-innovation/SYNTHESIS.md``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from tesserae.research_graph import CAUSAL_EDGE_TYPES, ResearchGraph


# Edge types Tesserae emits that carry session-memory provenance.
# Listed in ``research_graph.ALLOWED_EDGE_TYPES``; kept inline so this
# module does not have to re-import the full set.
_SESSION_EDGE_TYPES = {
    "derived_from_session",
    "discussed_in",
    "references",
    "supersedes",
}

# Default per-edge-type weight multipliers. >1.0 means "treat this edge
# as more important when spreading PPR mass"; <1.0 down-weights.
# Heuristic rationale:
#   * Session-provenance edges: the whole point of feature B is to make
#     Insights re-surface other Insights/Decisions/Sessions, so all four
#     get a healthy bump.
#   * ``supports_claim`` / ``contradicts_claim`` / ``derived_from``: the
#     strongest "this concept relies on that one" edges in the assertion
#     layer, so PPR should flow easily across them.
#   * ``user_link``: bidirectional Obsidian sync — semantically neutral,
#     keep at 1.0.
DEFAULT_EDGE_TYPE_WEIGHTS: Dict[str, float] = {
    "derived_from_session": 2.0,
    "discussed_in": 1.5,
    "references": 1.5,
    "supersedes": 1.75,
    "supports_claim": 1.5,
    "contradicts_claim": 1.5,
    "derived_from": 1.25,
    "synthesizes": 1.25,
    "summarizes": 1.25,
}

# The causal layer walks ABOVE the assertion layer. A ``recovers`` edge is the
# only edge in the vocabulary derived from two OBSERVED outcomes rather than
# from text, and "the thing that fixed this" is the most retrievable fact a
# session leaves behind — so it must not walk at the unlisted default of 1.0,
# below even ``references``. Derived from ``CAUSAL_EDGE_TYPES`` so the weight
# is not something the next causal type has to remember to ask for.
DEFAULT_EDGE_TYPE_WEIGHTS.update({edge_type: 2.0 for edge_type in CAUSAL_EDGE_TYPES})

# Edge classes that carry provenance/bookkeeping ("where did this come
# from") rather than semantic relatedness. Under ``tame_hubs`` they are
# downweighted relative to semantic edges so a mega-hub Session/Paper node
# reached mostly through provenance links stops dominating the walk
# (Descent design §5.4; the deg-1,257 leaked-prompt Session node).
PROVENANCE_EDGE_TYPES = frozenset({
    "authored_by",
    "discussed_in",
    "evidenced_by",
    "mentioned_in",
    "part_of",
})

# Multiplier applied on top of the resolved edge weight for provenance
# classes when ``tame_hubs`` is on.
PROVENANCE_EDGE_DOWNWEIGHT = 0.25

# Max neighbours a single node may spread PPR mass to when ``tame_hubs``
# is on. 200 per the Descent design doc: comfortably above any legitimate
# community's fanout, far below the pathological hubs.
HUB_DEGREE_CAP = 200


def personalized_pagerank(
    graph: ResearchGraph,
    seed_ids: Sequence[str],
    alpha: float = 0.15,
    top_k: int = 20,
    edge_type_weights: Optional[Mapping[str, float]] = None,
    directed: bool = False,
    max_iter: int = 100,
    tol: float = 1.0e-6,
    tame_hubs: bool = False,
    hub_ids: Optional[Sequence[str]] = None,
) -> List[Tuple[str, float]]:
    """Run Personalized PageRank seeded at ``seed_ids``.

    Args:
        graph: The compiled typed graph.
        seed_ids: Node ids the random walker teleports back to.
            Unknown ids are dropped silently; if none survive, returns ``[]``.
        alpha: Teleport probability (a.k.a. damping = ``1 - alpha``).
            ``0.15`` is the classic PageRank default and matches HippoRAG.
        top_k: Number of (node_id, score) pairs to return, sorted by score.
        edge_type_weights: Optional override map. Edge types not in the
            map fall back to ``1.0``; missing types are not penalised.
        directed: If ``True`` use edges as-is; otherwise add an implicit
            reverse edge (typical for relevance walks over a Tesserae graph).
        max_iter: Power-iteration cap.
        tol: Convergence tolerance on L1 score-vector delta.
        tame_hubs: Hub-poisoning mitigation (Descent PR1), default OFF so
            existing consumers are byte-for-byte unchanged. When ``True``:
            (a) ``PROVENANCE_EDGE_TYPES`` weights are multiplied by
            ``PROVENANCE_EDGE_DOWNWEIGHT`` relative to semantic edges, and
            (b) each node's fanout on the aggregated projection is capped
            at ``HUB_DEGREE_CAP`` — only its strongest ties keep spreading
            mass (deterministic tie-break by node index).
        hub_ids: Optional precomputed hub list (the ``hubs`` field of the
            ``.tesserae/hierarchy.json`` sidecar, Descent PR8). Only read
            when ``tame_hubs`` is on: the degree cap is then applied to
            EXACTLY these nodes instead of scanning every node's fanout —
            the sidecar already knows who the hubs are. ``None`` keeps the
            PR1 scan-everything behaviour; unknown ids are dropped silently
            (same contract as ``seed_ids``).

    Returns:
        ``[(node_id, score), ...]`` sorted descending. Scores over all
        nodes sum to ~1.0 modulo dangling-node correction.
    """

    if alpha <= 0.0 or alpha > 1.0:
        raise ValueError(f"alpha must be in (0, 1], got {alpha}")
    if top_k <= 0:
        raise ValueError(f"top_k must be positive, got {top_k}")

    weights = dict(DEFAULT_EDGE_TYPE_WEIGHTS)
    if edge_type_weights:
        weights.update(edge_type_weights)

    node_ids: List[str] = [node.id for node in graph.nodes]
    if not node_ids:
        return []
    node_index = {node_id: i for i, node_id in enumerate(node_ids)}
    n = len(node_ids)

    # Aggregate edges by (src_idx, dst_idx) summing typed weights so
    # multiple edges between the same pair reinforce each other.
    out_weights: Dict[int, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for edge in graph.edges:
        src = node_index.get(edge.source)
        dst = node_index.get(edge.target)
        if src is None or dst is None:
            continue
        w = float(weights.get(edge.type, 1.0))
        if tame_hubs and edge.type in PROVENANCE_EDGE_TYPES:
            w *= PROVENANCE_EDGE_DOWNWEIGHT
        if w <= 0.0:
            continue
        out_weights[src][dst] += w
        if not directed:
            out_weights[dst][src] += w

    if tame_hubs:
        # Degree-cap the projection: a hub with more than HUB_DEGREE_CAP
        # neighbours keeps only its strongest ties (highest aggregated
        # weight, ties broken by node index for determinism). This bounds
        # how far a single mega-hub can smear PPR mass across the graph.
        # With a sidecar hub list, only the listed nodes are candidates —
        # the hierarchy pass already measured degrees, so the per-node
        # fanout scan collapses to a membership check.
        capped: Optional[frozenset] = (
            frozenset(node_index[h] for h in hub_ids if h in node_index)
            if hub_ids is not None
            else None
        )
        for src, dst_map in out_weights.items():
            if capped is not None and src not in capped:
                continue
            if len(dst_map) <= HUB_DEGREE_CAP:
                continue
            kept = sorted(dst_map.items(), key=lambda item: (-item[1], item[0]))
            out_weights[src] = defaultdict(float, kept[:HUB_DEGREE_CAP])

    # Row-normalize so each node's out-weights sum to 1 (or stay empty
    # for dangling nodes; we redistribute their mass via teleport below).
    out_norm: Dict[int, List[Tuple[int, float]]] = {}
    for src, dst_map in out_weights.items():
        total = sum(dst_map.values())
        if total <= 0.0:
            continue
        out_norm[src] = [(dst, w / total) for dst, w in dst_map.items()]

    # Personalization vector: uniform over surviving seeds.
    seed_indices = [node_index[s] for s in seed_ids if s in node_index]
    if not seed_indices:
        return []
    p = [0.0] * n
    seed_mass = 1.0 / len(seed_indices)
    for idx in seed_indices:
        p[idx] += seed_mass

    # Start from the personalization vector — converges faster than uniform.
    rank = list(p)

    for _ in range(max_iter):
        new_rank = [alpha * p_i for p_i in p]
        dangling_mass = 0.0
        for src, score in enumerate(rank):
            if score == 0.0:
                continue
            spread = (1.0 - alpha) * score
            edges = out_norm.get(src)
            if not edges:
                dangling_mass += spread
                continue
            for dst, w in edges:
                new_rank[dst] += spread * w
        if dangling_mass > 0.0:
            # Redistribute dangling mass over the personalization vector
            # (HippoRAG / standard PR convention).
            for idx in seed_indices:
                new_rank[idx] += dangling_mass * seed_mass

        delta = sum(abs(a - b) for a, b in zip(new_rank, rank))
        rank = new_rank
        if delta < tol:
            break

    # Filter out non-positive scores so disconnected components don't
    # pollute results when the seed's component is smaller than ``top_k``.
    # A score of 0.0 means PPR mass never reached that node — it isn't a
    # "relevant" result, just filler that would happen to fit in the slice.
    ranked = sorted(
        ((node_ids[i], rank[i]) for i in range(n) if rank[i] > 0.0),
        key=lambda item: item[1],
        reverse=True,
    )
    return ranked[:top_k]

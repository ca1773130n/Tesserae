"""Personalized PageRank over the typed ResearchGraph.

Inspired by HippoRAG 2 (arXiv:2502.14802): given one or more "seed" nodes
extracted from a query, run PPR over the knowledge graph so that nodes
multiple hops away from the seed (but well-connected to it) score high,
not just the seed's immediate 1-hop neighborhood.

Implementation notes
--------------------
- Power iteration, with a numpy fast path and a pure-Python fallback. We
  avoid a ``networkx``/``scipy`` dependency because Tesserae's runtime
  dependency set is intentionally small (``pyproject.toml`` pins only
  ``pydantic>=2``); numpy ships only in the ``semantic`` extra, so it is
  imported behind a guard exactly like the cosine lane in
  ``retrieval/hybrid.py``. The two paths are BIT-IDENTICAL — see
  ``_power_iteration_numpy`` for why that is achievable and what it costs.
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


_NUMPY_CHECKED = False
_NUMPY = None


def _numpy():
    """Return the ``numpy`` module, or ``None`` when it is not installed.

    numpy is an OPTIONAL dependency (the ``semantic`` extra), so PPR cannot
    assume it. Memoised because a FAILING import is not cached by the
    interpreter — without the memo every call would re-walk ``sys.path``.
    Same shape as ``retrieval/hybrid.py``'s ``_numpy``.
    """
    global _NUMPY_CHECKED, _NUMPY
    if not _NUMPY_CHECKED:
        try:
            import numpy  # type: ignore

            _NUMPY = numpy
        except Exception:
            _NUMPY = None
        _NUMPY_CHECKED = True
    return _NUMPY


def _power_iteration_numpy(
    n: int,
    out_norm: Mapping[int, List[Tuple[int, float]]],
    p: List[float],
    seed_indices: List[int],
    alpha: float,
    max_iter: int,
    tol: float,
) -> Optional[List[float]]:
    """Vectorised power iteration, or ``None`` when numpy is unavailable.

    Returns the same ``rank`` vector the scalar loop below produces —
    BIT-IDENTICAL, not merely close. That is a hard requirement rather than
    a nicety: on the real 62k-node graph 36% of scored nodes sit on EXACTLY
    tied scores (blocks of up to 141 nodes), and ``context_compiler`` asks
    for ``top_k=len(graph.nodes)``, so a 1-ULP difference re-orders ~78% of
    the ranking even though every score is right to 15 digits. Callers pin
    result ORDER, so "close enough" is a behaviour change.

    Three constructions are load-bearing; none may be simplified:

    1. ``np.bincount(weights=)`` is a sequential ``out[idx[i]] += w[i]`` C
       loop, so it reproduces the scalar ``new_rank[dst] += ...``
       association PROVIDED the edges are flattened in the order the scalar
       double loop visits them (src ascending, then ``out_norm[src]`` order).
       The ``alpha * p_i`` base terms are PREPENDED into the SAME bincount
       as ``n`` synthetic self-entries so each bin accumulates
       base-then-contributions. Adding the base afterwards re-associates and
       diverges in ~94% of trials.
    2. Linear reductions (dangling mass, L1 delta) use ``np.cumsum(...)[-1]``
       (``np.add.accumulate``), which is sequential left-to-right like
       Python's ``sum()``. ``np.sum`` uses pairwise summation and differs in
       ~99.9% of trials.
    3. The final ``sorted()`` stays in pure Python (in the caller), so
       tie-breaking by node index is unchanged.

    Skipping the scalar loop's ``if score == 0.0: continue`` is safe: every
    quantity here is non-negative and ``x + 0.0 == x`` bitwise.
    """
    np = _numpy()
    if np is None:
        return None

    # Flatten in EXACTLY the scalar visit order.
    f_src: List[int] = []
    f_dst: List[int] = []
    f_w: List[float] = []
    dangling_list: List[int] = []
    for src in range(n):
        edges = out_norm.get(src)
        if not edges:
            dangling_list.append(src)
            continue
        for dst, w in edges:
            f_src.append(src)
            f_dst.append(dst)
            f_w.append(w)

    flat_src = np.asarray(f_src, dtype=np.intp)
    flat_w = np.asarray(f_w, dtype=np.float64)
    dangling = np.asarray(dangling_list, dtype=np.intp)
    e = len(f_dst)
    # Constant across iterations: [0..n-1] then the edge destinations.
    idx_all = np.empty(n + e, dtype=np.intp)
    idx_all[:n] = np.arange(n, dtype=np.intp)
    idx_all[n:] = np.asarray(f_dst, dtype=np.intp)
    wbuf = np.empty(n + e, dtype=np.float64)

    p_arr = np.asarray(p, dtype=np.float64)
    wbuf[:n] = alpha * p_arr  # base term, constant across iterations
    contrib = wbuf[n:]
    rank = p_arr.copy()
    has_dangling = dangling.size > 0

    for _ in range(max_iter):
        spread = (1.0 - alpha) * rank
        np.multiply(spread[flat_src], flat_w, out=contrib)
        new_rank = np.bincount(idx_all, weights=wbuf, minlength=n)

        if has_dangling:
            dangling_mass = float(np.cumsum(spread[dangling])[-1])
            if dangling_mass > 0.0:
                for idx in seed_indices:
                    new_rank[idx] += dangling_mass * p[idx]

        delta = float(np.cumsum(np.abs(new_rank - rank))[-1])
        rank = new_rank
        if delta < tol:
            break

    return rank.tolist()


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
    seed_weights: Optional[Mapping[str, float]] = None,
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
        seed_weights: Optional relevance mass per seed id. ``None`` (the
            default) spreads mass uniformly and is byte-for-byte the previous
            behaviour. Supplying weights is what makes BROAD seeding meaningful:
            uniform mass over every node is not a personalized walk at all, it
            is plain PageRank, so "seed everything" only works when the seeds
            carry how relevant each one is. Ids absent from the map get ZERO
            mass rather than a fallback share — topping them up would make a
            broad seed set quietly behave like a narrow one. Negative weights
            raise, as does a set whose weights all sum to zero. Weights are
            normalised, so only their ratios matter.
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

        The power iteration runs vectorised when numpy is importable and
        falls back to the scalar loop otherwise. The two paths are
        BIT-IDENTICAL — same scores, same order, no tolerance — which is
        what makes the optional dependency safe to ship. See
        ``_power_iteration_numpy``; ``tests/test_ppr.py`` pins the parity.
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

    # Personalization vector: uniform over surviving seeds, or proportional to
    # ``seed_weights`` when given.
    surviving = [s for s in seed_ids if s in node_index]
    seed_indices = [node_index[s] for s in surviving]
    if not seed_indices:
        return []
    p = [0.0] * n
    if seed_weights is None:
        seed_mass = 1.0 / len(seed_indices)
        for idx in seed_indices:
            p[idx] += seed_mass
    else:
        raw = [float(seed_weights.get(s, 0.0)) for s in surviving]
        if any(w < 0.0 for w in raw):
            raise ValueError("seed_weights must be non-negative")
        total = sum(raw)
        if total <= 0.0:
            # Every named weight was zero. Falling back to uniform would
            # silently answer a different question than the caller asked, and
            # returning [] would read as "the graph knows nothing". Neither is
            # honest, so refuse.
            raise ValueError(
                "seed_weights summed to zero over the surviving seeds — "
                "no node would receive teleport mass"
            )
        for idx, w in zip(seed_indices, raw):
            p[idx] += w / total

    # numpy fast path — bit-identical to the loop below, ~26x faster on the
    # iteration itself. Returns None when numpy is not installed, and the
    # scalar loop takes over.
    rank = _power_iteration_numpy(
        n, out_norm, p, seed_indices, alpha, max_iter, tol
    )

    if rank is None:
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
                # (HippoRAG / standard PR convention). Read the mass straight
                # off ``p`` rather than recomputing it as 1/len(seeds): the two
                # agree only in the uniform case, and under ``seed_weights``
                # the uniform form would hand every seed an equal share of the
                # dangling mass while the teleport term gave them unequal
                # shares — a walk personalised two different ways at once.
                for idx in seed_indices:
                    new_rank[idx] += dangling_mass * p[idx]

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

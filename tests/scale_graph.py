"""Synthetic ``ResearchGraph`` generation at arbitrary node counts.

Exists to answer one question with numbers instead of opinion: at what corpus
size does the JSON-artifact-plus-SQLite-sidecar model stop being adequate? That
question can only be answered against a graph whose SHAPE matches a real one —
a uniform random graph of the right node count would answer a different and
much easier question, because every cost that actually bites here comes from
the shape rather than the count:

* the degree tail. The live graph's busiest node touches 29% of all edges (a
  ``CommunitySummary`` that summarizes the whole corpus). PPR and the
  depth-2 neighbourhood walk are priced by that hub, not by the mean degree
  of 4.4.
* per-type field sizes. ``EvidenceSpan`` is 25% of the nodes and carries a
  90-character description; ``Session`` is 0.3% of the nodes and carries 5.5 KB
  each. Serialization cost is dominated by the second group, so a generator
  that gave every node the mean size would understate it.
* token vocabulary. BM25 posting-list length is a function of how often a term
  repeats across the corpus. Nodes filled with random unique strings would give
  every term a posting list of one and make the lane look free.

So the generator replays a profile measured off the real graph
(``tests/fixtures/graph_shape_47k.json``, derived from this project's own
47,132-node ``.tesserae/graph.json``) rather than inventing a shape. Everything
is deterministic given ``seed`` and involves no LLM, no network and no disk —
the guard test in ``test_scale_graph.py`` runs it at a size that costs
milliseconds.

Scaling rule: node-type shares, edge-type shares, the edges-per-node ratio and
the degree histogram are held CONSTANT as the node count grows, and hub degree
is held constant as a share of total edges. That last one is the load-bearing
assumption and it is deliberately the pessimistic reading: it says a corpus ten
times the size still gets summarized into community summaries that each point
at everything beneath them, so the busiest node's degree grows linearly with
the corpus. If a real 1M-node deployment instead grew the NUMBER of community
summaries and held each one's fanout roughly fixed, the hub-driven costs
reported here would be overstated.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from tesserae.research_graph import (
    ALLOWED_EDGE_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

SHAPE_PROFILE_PATH = Path(__file__).parent / "fixtures" / "graph_shape_47k.json"

#: Degree histogram of the live graph, as ``(low, high, share, mean)`` over the
#: NON-hub population. Hubs are placed separately from ``profile["hubs"]`` — a
#: configuration model cannot produce a node holding 29% of all stubs without
#: swamping everything else, and the hubs are the whole reason the tail matters.
#:
#: The per-bucket ``mean`` is carried because the distribution inside a bucket
#: is steeply skewed toward its floor and drawing uniformly across the range
#: gets the total badly wrong: uniform draws over ``10-49`` average 29.5 against
#: a measured 19.2, and over ``50-499`` average 274.5 against a measured 78.1.
#: That inflates total stub supply by ~38%, and since the surplus is discarded
#: at pairing time it lands as isolated nodes — 8% of the graph instead of 4%.
DEGREE_BUCKETS: Tuple[Tuple[int, int, float, float], ...] = (
    (0, 0, 0.039061, 0.0),
    (1, 1, 0.166999, 1.0),
    (2, 4, 0.636489, 2.599),
    (5, 9, 0.102902, 6.154),
    (10, 49, 0.051748, 19.199),
    (50, 499, 0.002716, 78.117),
)

#: Word-like tokens are drawn from a fixed vocabulary with a Zipf frequency, so
#: the corpus has the repeated-term structure BM25 is priced by. The size is
#: chosen to give the same order of posting-list length as the real graph at
#: comparable node counts; the exact number is not load-bearing, its being
#: finite is.
_VOCAB_SIZE = 4096


def _bucket_exponent(low: int, high: int, target_mean: float) -> float:
    """Solve for ``k`` in ``d = low + (high - low + 1) * u**k`` hitting ``target_mean``.

    Bisection on a closed-form expectation would need the floor, so the mean is
    evaluated by quadrature over ``u`` instead — it runs once at import and the
    accuracy needed is "within a few hundredths of a stub per node".
    """
    if high <= low:
        return 1.0
    span = high - low + 1

    def mean_for(k: float) -> float:
        steps = 512
        total = 0.0
        for i in range(steps):
            u = (i + 0.5) / steps
            total += min(high, low + int(span * (u**k)))
        return total / steps

    lo_k, hi_k = 0.05, 60.0
    for _ in range(60):
        mid = (lo_k + hi_k) / 2
        if mean_for(mid) > target_mean:
            lo_k = mid
        else:
            hi_k = mid
    return (lo_k + hi_k) / 2


_BUCKET_EXPONENTS: Tuple[float, ...] = tuple(
    _bucket_exponent(low, high, mean) for low, high, _, mean in DEGREE_BUCKETS
)


def _load_profile(path: Optional[Path] = None) -> Dict[str, object]:
    return json.loads((path or SHAPE_PROFILE_PATH).read_text(encoding="utf-8"))


def _build_vocab(rng: random.Random) -> List[str]:
    syllables = ("ra", "ten", "mo", "sil", "ka", "vex", "dor", "lin", "pau", "tri", "quen", "zar")
    seen: Dict[str, None] = {}
    while len(seen) < _VOCAB_SIZE:
        word = "".join(rng.choice(syllables) for _ in range(rng.randint(2, 4)))
        seen[word] = None
    return list(seen)


class _TextFiller:
    """Deterministic word-like text of a requested character length.

    Zipf-weighted draws from a fixed vocabulary. ``random.choices`` with a
    cumulative-weight table is used rather than one draw per call because at 1M
    nodes this runs tens of millions of times and the weight table is otherwise
    rebuilt on every call.
    """

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self._vocab = _build_vocab(rng)
        self._cum: List[float] = []
        total = 0.0
        for rank in range(1, len(self._vocab) + 1):
            total += 1.0 / rank
            self._cum.append(total)

    def text(self, length: int) -> str:
        if length <= 0:
            return ""
        parts: List[str] = []
        size = 0
        while size < length:
            word = self._rng.choices(self._vocab, cum_weights=self._cum, k=1)[0]
            parts.append(word)
            size += len(word) + 1
        return " ".join(parts)[:length]


def _jitter(rng: random.Random, mean: float) -> int:
    """Draw a field length around ``mean``.

    The profile stores only per-type means, so the spread is reconstructed
    rather than measured: a triangular draw over ``[0.4x, 1.8x]`` keeps the mean
    while giving the serializer a realistic mix of short and long fields. Costs
    here are close to linear in total bytes, so the exact spread matters much
    less than the total, which is preserved.
    """
    if mean <= 0:
        return 0
    return max(0, int(rng.triangular(mean * 0.4, mean * 1.8, mean)))


def _metadata(filler: _TextFiller, rng: random.Random, target_json_len: int) -> Dict[str, object]:
    """A metadata dict whose ``json.dumps`` length is about ``target_json_len``.

    Real metadata is a handful of short keys plus occasionally one large value
    (``Session`` nodes carry multi-KB turn summaries). Reproducing the SIZE
    matters for serialization and SQLite blob cost; reproducing the exact key
    names does not, and inventing plausible-looking ones would only make the
    fixture look more authoritative than it is.
    """
    if target_json_len <= 2:
        return {}
    budget = target_json_len - 2
    meta: Dict[str, object] = {}
    idx = 0
    while budget > 12:
        chunk = min(budget - 8, max(8, int(budget * rng.uniform(0.3, 0.9))))
        meta[f"k{idx}"] = filler.text(chunk)
        budget -= chunk + 10
        idx += 1
    return meta


def _allocate(total: int, shares: Sequence[Tuple[str, float]]) -> List[Tuple[str, int]]:
    """Split ``total`` across ``shares`` with the remainder going to the largest.

    Rounding each share independently loses or gains items; the sweep compares
    sizes across a curve, so an exact count at every size is worth the fixup.
    """
    out = [(name, int(total * share)) for name, share in shares]
    shortfall = total - sum(count for _, count in out)
    if shortfall and out:
        name, count = out[0]
        out[0] = (name, count + shortfall)
    return out


def generate_graph(
    n_nodes: int,
    *,
    seed: int = 0,
    profile: Optional[Dict[str, object]] = None,
) -> ResearchGraph:
    """Build a synthetic :class:`ResearchGraph` of ``n_nodes`` nodes.

    Edge count follows from the profile's edges-per-node ratio (2.2209 in the
    live graph), so the caller sizes the graph by nodes alone and the edge
    budget scales with it.
    """
    if n_nodes < 1:
        raise ValueError("n_nodes must be >= 1")
    prof = profile or _load_profile()
    rng = random.Random(seed)
    filler = _TextFiller(rng)

    node_types: Dict[str, Dict[str, float]] = prof["node_types"]  # type: ignore[assignment]
    shares = [(name, float(spec["share"])) for name, spec in node_types.items()]
    nodes: List[ResearchNode] = []
    for type_name, count in _allocate(n_nodes, shares):
        spec = node_types[type_name]
        node_type = ResearchNodeType(type_name)
        for i in range(count):
            slug = filler.text(_jitter(rng, float(spec["slug"]))).replace(" ", "-") or f"n{i}"
            nodes.append(
                ResearchNode(
                    id=f"{type_name}:{slug}:{rng.getrandbits(48):012x}",
                    name=filler.text(_jitter(rng, float(spec["name"]))),
                    type=node_type,
                    aliases=[
                        filler.text(12) for _ in range(int(rng.random() < float(spec["aliases"])))
                    ],
                    description=filler.text(_jitter(rng, float(spec["desc"]))),
                    source_path=filler.text(_jitter(rng, float(spec["src"]))).replace(" ", "/"),
                    metadata=_metadata(filler, rng, _jitter(rng, float(spec["meta"]))),
                )
            )

    n_edges = int(round(n_nodes * float(prof["edges_per_node"])))
    edges = _generate_edges(nodes, n_edges, prof, rng, filler)
    return ResearchGraph(nodes=nodes, edges=edges)


def _generate_edges(
    nodes: Sequence[ResearchNode],
    n_edges: int,
    prof: Dict[str, object],
    rng: random.Random,
    filler: _TextFiller,
) -> List[ResearchEdge]:
    edge_types: Dict[str, float] = prof["edge_types"]  # type: ignore[assignment]
    type_names = list(edge_types)
    type_cum: List[float] = []
    running = 0.0
    for name in type_names:
        running += edge_types[name]
        type_cum.append(running)
    unknown = [name for name in type_names if name not in ALLOWED_EDGE_TYPES]
    if unknown:
        raise ValueError(f"profile names edge types the schema rejects: {sorted(unknown)}")

    by_type: Dict[str, List[int]] = {}
    for idx, node in enumerate(nodes):
        by_type.setdefault(node.type.value, []).append(idx)

    # Hubs first. They are placed by node TYPE because that is what makes them
    # hubs in the real graph — a CommunitySummary points at everything it
    # summarizes — and the dominant edge type is pinned so a view/edge-weight
    # filter sees the same concentration the real graph has.
    endpoints: List[int] = []
    edges: List[ResearchEdge] = []
    used = 0
    for hub in prof["hubs"]:  # type: ignore[union-attr]
        candidates = by_type.get(str(hub["type"]))
        if not candidates:
            continue
        hub_idx = candidates[rng.randrange(len(candidates))]
        degree = min(int(n_edges * float(hub["degree_share"])), n_edges - used)
        for _ in range(degree):
            edges.append(
                _make_edge(
                    nodes[hub_idx],
                    nodes[rng.randrange(len(nodes))],
                    "summarizes",
                    rng,
                    filler,
                    prof,
                )
            )
        used += degree

    # Everything else via a configuration model over the measured degree
    # histogram: draw a stub count per node, shuffle the stub list, pair it up.
    # This reproduces the histogram directly instead of hoping a growth process
    # converges to it.
    remaining = n_edges - used
    if remaining > 0:
        bucket_cum: List[float] = []
        running = 0.0
        for _, _, share, _mean in DEGREE_BUCKETS:
            running += share
            bucket_cum.append(running)
        for idx in range(len(nodes)):
            draw = rng.random() * running
            for (low, high, _, _mean), ceiling, exponent in zip(
                DEGREE_BUCKETS, bucket_cum, _BUCKET_EXPONENTS
            ):
                if draw <= ceiling:
                    degree = min(high, low + int((high - low + 1) * (rng.random() ** exponent)))
                    endpoints.extend([idx] * degree)
                    break
        rng.shuffle(endpoints)
        # Stub supply rarely lands exactly on 2 * remaining; top up by sampling
        # so the edge count is exact at every size on the curve.
        while len(endpoints) < remaining * 2:
            endpoints.append(rng.randrange(len(nodes)))
        for i in range(remaining):
            source = nodes[endpoints[2 * i]]
            target = nodes[endpoints[2 * i + 1]]
            draw = rng.random()
            type_name = type_names[-1]
            for name, ceiling in zip(type_names, type_cum):
                if draw <= ceiling:
                    type_name = name
                    break
            edges.append(_make_edge(source, target, type_name, rng, filler, prof))
    return edges


def _make_edge(
    source: ResearchNode,
    target: ResearchNode,
    edge_type: str,
    rng: random.Random,
    filler: _TextFiller,
    prof: Dict[str, object],
) -> ResearchEdge:
    has_evidence = rng.random() < float(prof["evidence_share"])
    return ResearchEdge(
        source=source.id,
        target=target.id,
        type=edge_type,
        evidence=filler.text(_jitter(rng, float(prof["evidence_len"]))) if has_evidence else None,
        metadata=_metadata(filler, rng, _jitter(rng, float(prof["edge_meta"]))),
    )

"""Reuse a node when an existing one means the same thing, not only when it is
spelled the same.

WHY. Node identity in this codebase is exact-name: ``ResNet-50`` and ``ResNet
50`` are two nodes, and every consumer that resolves by name then sees an
ambiguity or a miss. Measured on a compiled 148-paper corpus, 2,317 names were
owned by more than one node and ``verify_claim`` refused 226 of 426 claims —
declining claims whose supporting edge was present.

Mem0 credits this mechanism for a large share of its 71.4 -> 92.5 on LoCoMo:
"compute embeddings for both source and destination entities, then search for
existing nodes with semantic similarity above a defined threshold". The survey
literature agrees the bottleneck is upstream of the graph search — these systems
"work exactly as well as their open information extraction does".

WHAT IT IS NOT. This does not touch the verdict path. ``verify_claim`` still
resolves exactly and still refuses an ambiguous name rather than guessing; what
changes is the GRAPH it resolves against, built once, deterministically, at
compile time. A fuzzy match at query time would make the verdict a function of a
similarity threshold instead of the graph bytes, which is the one thing that
module promises not to be.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .research_graph import (ResearchEdge, ResearchGraph, ResearchNode,
                             _CROSS_TYPE_MERGE_PRIORITY,
                             _init_cross_type_priority)

#: Cosine above which two entity names are treated as one entity. Measured, not
#: picked — the curve on a 148-paper corpus, scoring how many of 213 asserted
#: claims ``verify_claim`` could confirm:
#:
#:     threshold   merged   refused   confirmed/213   specificity
#:          none        0       226      26  (0.122)        1.000
#:          0.98      387       182      36  (0.169)        1.000
#:          0.95      588       183      36  (0.169)        1.000
#:          0.90    1,254       204      35  (0.164)        1.000
#:          0.85    2,211       208      40  (0.188)        1.000
#:          0.80    3,291       216      40  (0.188)        1.000
#:
#: 0.98 buys 71% of the total gain for 18% of the merges, and refusals FALL
#: because the merged-away name survives as an alias. Specificity held at 1.000
#: at every threshold tested — no merge manufactured support the corpus does not
#: make — but that was one corpus, so the default is the conservative end of a
#: curve rather than its maximum. ``0`` disables the pass entirely.
DEFAULT_SIMILARITY = float(os.environ.get("TESSERAE_ENTITY_SIMILARITY", "0.98"))

#: Only these types are candidates. Merging two ``EvidenceSpan``s that read alike
#: would destroy the provenance the spans exist to carry; merging two spellings
#: of a dataset is the entire point.
ENTITY_TYPES = frozenset({
    "Algorithm", "Model", "Dataset", "Benchmark", "Metric", "Task",
    "ApproachFamily", "Concept", "Capability", "ArchitecturePattern",
    "ProblemArea", "ResearchTopic",
})

#: A token appearing in more than this many candidate names blocks nothing, so
#: pairing on it would cost O(n^2) for no recall. Purely a cost guard.
_BLOCK_MAX = 60
_WORD = re.compile(r"[0-9a-z]+")


def _tokens(name: str) -> Set[str]:
    return {w for w in _WORD.findall(name.lower()) if len(w) > 3}


def _unit(vec: Sequence[float]) -> List[float]:
    mag = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / mag for x in vec]


def resolve_entities(
    graph: ResearchGraph,
    *,
    backend=None,
    threshold: Optional[float] = None,
) -> Tuple[ResearchGraph, int]:
    """Collapse entity nodes whose names mean the same thing.

    Returns ``(graph, merged_count)``. Returns the SAME graph object when the
    pass is disabled or finds nothing, so a caller that does not want this pays
    nothing for it.

    Deterministic: candidate pairs are sorted by (similarity, id, id) and the
    winner of each merge is chosen by ranked priority, then degree, then id —
    the same rule the same-name collapse uses. The compile is byte-idempotent
    and a merge that depended on dict order would break that.

    The loser's name and aliases move onto the winner. Dropping them was
    measured to make things WORSE: a query spelling the merged-away name stopped
    resolving at all, so refusals rose from 226 to 254 even as recall-of-decided
    improved. Carrying them forward turns that into a fall to 182.
    """
    thr = DEFAULT_SIMILARITY if threshold is None else threshold
    if thr <= 0:
        return graph, 0
    if not _CROSS_TYPE_MERGE_PRIORITY:
        _init_cross_type_priority()

    cands = [n for n in graph.nodes if n.type.value in ENTITY_TYPES and n.name.strip()]
    if len(cands) < 2:
        return graph, 0

    if backend is None:
        from .retrieval.hybrid import active_embedding_backend
        backend = active_embedding_backend()

    names = [n.name.strip() for n in cands]
    try:
        vectors = [_unit(v) for v in _embed(backend, names)]
    except Exception:
        # A missing or failing embedder must not fail a compile. The graph is
        # simply not entity-resolved, which is the behaviour before this pass.
        return graph, 0

    blocks: Dict[str, List[int]] = defaultdict(list)
    for i, name in enumerate(names):
        for tok in _tokens(name):
            blocks[tok].append(i)

    pairs: Set[Tuple[int, int]] = set()
    for idxs in blocks.values():
        if len(idxs) > _BLOCK_MAX:
            continue
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                pairs.add((idxs[a], idxs[b]))
    if not pairs:
        return graph, 0

    scored = []
    for i, j in pairs:
        sim = sum(x * y for x, y in zip(vectors[i], vectors[j]))
        if sim >= thr:
            scored.append((sim, cands[i].id, cands[j].id))
    if not scored:
        return graph, 0
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))

    degree: Counter = Counter()
    for edge in graph.edges:
        degree[edge.source] += 1
        degree[edge.target] += 1
    by_id = {n.id: n for n in graph.nodes}

    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    def rank(node: ResearchNode):
        return (-_CROSS_TYPE_MERGE_PRIORITY.get(node.type, 0), -degree[node.id], node.id)

    for _sim, a_id, b_id in scored:
        a, b = find(a_id), find(b_id)
        if a == b:
            continue
        na, nb = by_id.get(a), by_id.get(b)
        if na is None or nb is None:
            continue
        win, lose = (na, nb) if rank(na) <= rank(nb) else (nb, na)
        parent[lose.id] = win.id

    redirect = {k: find(k) for k in parent}
    redirect = {k: v for k, v in redirect.items() if k != v}
    if not redirect:
        return graph, 0

    carried: Dict[str, Set[str]] = defaultdict(set)
    for loser, winner in redirect.items():
        ln = by_id.get(loser)
        if ln is None:
            continue
        carried[winner].add(ln.name.strip())
        for alias in ln.aliases:
            carried[winner].add(str(alias).strip())

    kept: List[ResearchNode] = []
    for node in graph.nodes:
        if node.id in redirect:
            continue
        extra = carried.get(node.id)
        if extra:
            aliases = list(dict.fromkeys(list(node.aliases) + sorted(extra)))
            node = ResearchNode(id=node.id, name=node.name, type=node.type,
                                aliases=aliases, description=node.description,
                                source_path=node.source_path, metadata=node.metadata)
        kept.append(node)

    seen: Set[Tuple[str, str, str]] = set()
    edges: List[ResearchEdge] = []
    for edge in graph.edges:
        src = redirect.get(edge.source, edge.source)
        dst = redirect.get(edge.target, edge.target)
        if src == dst:
            continue  # the merge made this a self-loop; it says nothing
        key = (src, edge.type, dst)
        if key in seen:
            continue
        seen.add(key)
        edges.append(ResearchEdge(source=src, target=dst, type=edge.type,
                                  evidence=edge.evidence, metadata=edge.metadata))
    return ResearchGraph(nodes=kept, edges=edges), len(redirect)


def _embed(backend, texts: Sequence[str]) -> Sequence[Sequence[float]]:
    for attr in ("embed", "encode", "embed_texts"):
        fn = getattr(backend, attr, None)
        if callable(fn):
            return fn(list(texts))
    from .retrieval.vector_cache import embed_texts
    return embed_texts(backend, list(texts))

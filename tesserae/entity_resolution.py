"""Reuse a node when an existing one means the same thing, not only when it is
spelled the same.

WHY. Node identity in this codebase is exact-name: ``ResNet-50`` and ``ResNet
50`` are two nodes, and every consumer that resolves by name then sees an
ambiguity or a miss. Measured on a compiled 148-paper corpus, ``verify_claim``
refused 226 of 426 claims — declining claims whose supporting edge was present.

WHAT IT BUYS, measured on that same graph and claim set by running this pass
in memory and re-deciding every claim (2026-08-29):

    refused (NOT_RESOLVABLE)      226/426  ->  182/426
    correct                       117/426  ->  149/426
    claims the corpus asserts      26/213  ->   36/213

387 merges. Of the 44 claims that stopped being refused, 31 got the right
answer and 13 the wrong one. Crucially, the count of claims wrongly called
SUPPORTED stayed at **zero** on both sides: resolution never manufactured
support the corpus does not give, which is the property that makes it safe to
run before a verdict function that is meant to be an audit.

It is not the dominant problem. Even afterwards, 95 of the 213 claims the
papers actually make are answered ABSENT, because the graph does not hold the
edge — extraction density, not entity identity. Accuracy among ANSWERED claims
moves only 58.5% -> 61.1%. Resolution removes a refusal; it cannot add a fact.

An earlier draft of this docstring reported 2,317 colliding names here. That
was measured before same-name collapse landed in chunked extraction; on a graph
carrying that fix the figure is 449 by exact name and 662 by normalised name,
and the pre-fix number should not be read as describing current output.

TWO PASSES. Names merge first when they are the SAME string (casefolded), and
only then when embeddings say they mean the same thing. The exact pass exists
because three earlier mechanisms all missed 'PDB' the Benchmark in one paper
and 'PDB' the Dataset in another: ``merge_cross_type_duplicates`` merges
same-name nodes only when every type is in its priority table (papers, claims,
fields); the within-document collapse never sees two papers; and the
similarity pass blocks candidates on tokens of four letters or more, so a
three-letter name is never compared at all. Every consumer that resolves by
name then refused the ambiguity. Measured on the same graph and claim set,
similarity pass alone against the two passes as shipped (2026-08-29):

    refused (NOT_RESOLVABLE)         182/426  ->  164/426
    correct                          149/426  ->  162/426
    false SUPPORTED, 426 negatives         0  ->        0
    names owned by >1 node               361  ->       73
    merges                               387  ->  586 exact + 128 similarity

The 73 that remain each involve a type the pass excludes, by construction: a
source anchor, a span, a claim, a code symbol, a session, a person or an
organisation. 'mip-NeRF 360' is a Model and the Paper that introduced it, and
making a document out of a method is worse than an ambiguity; two ``main``
functions sharing a name is the normal case, not a duplicate.

What identity cannot see is two things that share a name. A model shown every
group the pass joins on that graph (302) called 38 of them different things —
12.6%, an upper bound, since most of those are one concept described in two
papers' words: 'Beam search' as an ApproachFamily and as an InferenceStrategy,
'2-Wasserstein distance' as a MathematicalConcept and as a Metric. The
deterministic cost of those merges was zero fabricated verdicts on 426
negatives; the residual risk is a claim about one meaning answered by the
other's edge, and it is bounded by that 12.6%.

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
                             _init_cross_type_priority, is_source_anchor)

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

#: Types the EXACT-name pass covers: the research-entity family. Wider than
#: ``ENTITY_TYPES`` because a same-string name is far stronger evidence than a
#: similar embedding, so types too risky to merge on similarity — an
#: ``ObjectiveFunction`` against an ``Algorithm`` — are safe to merge on
#: identity. Never anchors, spans, claims, code, sessions, people.
EXACT_NAME_TYPES = ENTITY_TYPES | frozenset({
    "ObjectiveFunction", "InferenceStrategy", "MethodologicalConcept",
    "ResearchField", "MathematicalConcept", "TechnicalTerm",
    "TrainingParadigm", "EvaluationProtocol",
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

    Two passes, in this order: :func:`merge_exact_names` joins nodes whose
    names are the same string, then the similarity pass joins names whose
    embeddings sit above ``threshold``. Returns ``(graph, merged_count)`` with
    the count summed over both. Returns the SAME graph object when the pass is
    disabled or finds nothing, so a caller that does not want this pays
    nothing for it. A threshold at or below zero disables BOTH passes — one
    knob turns the function off.

    Deterministic: candidate pairs are sorted by (similarity, id, id) and the
    winner of each merge is chosen by ranked priority, then degree, then id —
    the same rule the same-name collapse uses. The compile is byte-idempotent
    and a merge that depended on dict order would break that.

    The loser's name and aliases move onto the winner. Dropping them was
    measured to make things WORSE: a query spelling the merged-away name stopped
    resolving at all, so refusals rose from 226 to 254 even as recall-of-decided
    improved. Carrying them forward turns that into a fall to 182.

    A missing or failing embedder skips only the similarity pass. The exact
    pass needs no model, and a broken one must not take it down too.
    """
    thr = DEFAULT_SIMILARITY if threshold is None else threshold
    if thr <= 0:
        return graph, 0
    if not _CROSS_TYPE_MERGE_PRIORITY:
        _init_cross_type_priority()

    graph, n_exact = merge_exact_names(graph)
    graph, n_similar = _merge_similar_names(graph, backend=backend, threshold=thr)
    return graph, n_exact + n_similar


def merge_exact_names(graph: ResearchGraph) -> Tuple[ResearchGraph, int]:
    """Join nodes of :data:`EXACT_NAME_TYPES` whose names are the same string.

    Casefolded, whitespace-stripped, nothing fuzzier — the point of this pass is
    that identity needs no threshold. It runs across documents, which is the
    one place no earlier pass looked (see the module docstring), and needs no
    embedder. Same graph object back when nothing merges.
    """
    if not _CROSS_TYPE_MERGE_PRIORITY:
        _init_cross_type_priority()
    groups: Dict[str, List[ResearchNode]] = defaultdict(list)
    for node in graph.nodes:
        if is_source_anchor(node) or node.type.value not in EXACT_NAME_TYPES:
            continue
        key = node.name.strip().casefold()
        if key:
            groups[key].append(node)
    if all(len(v) < 2 for v in groups.values()):
        return graph, 0

    rank = _rank_by(_degree(graph))
    redirect: Dict[str, str] = {}
    for key in sorted(groups):
        nodes = sorted(groups[key], key=rank)
        for lose in nodes[1:]:
            redirect[lose.id] = nodes[0].id
    return _apply_redirect(graph, redirect)


def _merge_similar_names(
    graph: ResearchGraph,
    *,
    backend,
    threshold: float,
) -> Tuple[ResearchGraph, int]:
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
        # simply not similarity-resolved, which is the behaviour before this pass.
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
        if sim >= threshold:
            scored.append((sim, cands[i].id, cands[j].id))
    if not scored:
        return graph, 0
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))

    by_id = {n.id: n for n in graph.nodes}
    rank = _rank_by(_degree(graph))
    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            x = parent[x]
        return x

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
    return _apply_redirect(graph, redirect)


def _degree(graph: ResearchGraph) -> Counter:
    degree: Counter = Counter()
    for edge in graph.edges:
        degree[edge.source] += 1
        degree[edge.target] += 1
    return degree


def _rank_by(degree: Counter):
    """Winner order shared by both passes: priority, then degree, then id."""
    def rank(node: ResearchNode):
        return (-_CROSS_TYPE_MERGE_PRIORITY.get(node.type, 0), -degree[node.id], node.id)
    return rank


def _apply_redirect(graph: ResearchGraph, redirect: Dict[str, str]) -> Tuple[ResearchGraph, int]:
    """Point every loser at its winner: names and aliases carried, edges
    rewritten, self-loops dropped, duplicate edges collapsed."""
    if not redirect:
        return graph, 0
    by_id = {n.id: n for n in graph.nodes}

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

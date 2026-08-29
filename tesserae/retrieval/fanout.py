"""Query fan-out with a document-disjoint merge, above :func:`hybrid_search`.

This module sits strictly ABOVE the lanes, exactly where
:mod:`tesserae.retrieval.rerank` sits, and does not modify ``hybrid_search`` by
a single line. It exists because on a conversational corpus two independent
mechanisms conspire to hold the SECOND gold session of a multi-hop question at
hit-rank 13-22, outside a K=10 budget. This removes one each.

**Mechanism 1 — the budget is spent twice on the same session.** The lanes
score NODES, and one session contributes dozens of them, so a ten-hit budget
routinely resolves to four or five distinct documents. ``source_cap`` admits at
most N hits per document into the head of the result. Measured on LoCoMo
conv-26, K=10, multi-hop ALL-gold@10: baseline 0.469 -> 0.562 from this alone.
It is a cliff and not a slope — cap 1 scores 68.8%, cap 2 53.1%, cap 3 50.0% —
so the value is 1 wherever the corpus is one node-set per document.

**Mechanism 2 — the ranking is driven by the ubiquitous term.** conv-26 has two
speakers who appear in all 19 sessions (``caroline`` at document-frequency
ratio 0.577, ``melanie`` 0.336), so "What types of pottery have Melanie and her
kids made?" ranks on ``melanie`` rather than on ``pottery`` (DF 0.075). A
second pass over the same candidate set, with the ubiquitous terms stripped,
ranks on the topic. See
:func:`~tesserae.retrieval.query_decompose.discriminative_subquery`.

**The two are multiplicative, not additive, and that ablation is the strongest
evidence here.** Pooled over all ten conversations and 1,982 gradeable
questions, multi-hop ALL-gold@10: baseline 0.312, fan-out alone 0.372,
``source_cap=1`` alone 0.440, both 0.504. **Report the cap-only control beside
every headline**: two thirds of the gain is the merge, and attributing the
merge's work to the decomposition would be the same error as attributing
per-session dedup to a graph walk.

Full pooled result at ``overfetch=2, source_cap=1, extra_facets=0``:
ALL-gold@10 0.823 -> 0.883, recall@10 0.874 -> 0.924, MRR 0.670 -> 0.692, no
category regressing.

WHY THERE IS NO GRAPH WALK HERE
-------------------------------
The obvious design for a multi-hop miss is graph expansion — seed
:func:`~tesserae.retrieval.ppr.personalized_pagerank` with the lanes' hits and
let the edges find the second session. That hypothesis was tested three
independent ways on this corpus and failed every time, so this module ships the
seam (``extra_rankings``) and not the walk. The next person must not re-derive
the null:

* **BFS reachability carries no information here.** The second gold session is
  2 hops from the first for 26 of 28 conv-26 multi-hop questions and 1 hop for
  the other 2, none unreachable — because at depth 2 EVERYTHING reaches
  everything. ``Person:caroline`` has undirected degree 101 and
  ``Person:melanie`` 70 out of 650 edge endpoints, both present in all 19
  sessions, and :data:`~tesserae.retrieval.ppr.HUB_DEGREE_CAP` (200) never
  fires at that degree.
* **Oracle-seed gate (a).** Seed PPR with the ENTIRE node set of one TRUE gold
  session and ask where the other ranks among the remaining 18. On conv-26:
  median rank 8.5 against a chance mean of 9.5 — about one rank of signal out
  of eighteen, under a seed no retriever can produce. That is the ceiling of
  any PPR-seeded design here. Unless that median is comfortably under 4, the
  edges are not there and the walk will not help.
* **Shuffle control (b).** Permute the graph ranking with a fixed RNG and
  re-run. On conv-26 the shuffle BEAT the real walk (51.9% vs 50.0%).

Both gates are cheap (seconds) and both must pass before graph expansion is
revisited. When one does, ``extra_rankings`` takes a PPR ranking as one more
entry in the merge with zero new machinery: map ids through
``{n.id: n for n in graph.nodes}``, keep the ``source_path``, and the same
``group_key`` applies. One trap is pre-disarmed there — ``personalized_pagerank``
raises ``ValueError`` on an all-zero ``seed_weights`` map, and a node can be
admitted by hybrid's candidate gate at fused score 0.0 (the gate reads RAW lane
scores while ``_fuse`` only sums positively-weighted lanes), so seed weights
must be floored: ``max(s.score, MIN_SEED_WEIGHT)``, the guard
``evals/selfimprove/curve.py:346`` already carries.

THE ``group_key`` HOLE, MEASURED
--------------------------------
A node whose key is ``""`` is NEVER capped, by design: a node that came from no
document cannot be a redundant re-read of one. On conv-26 that is 1 node of
345 and invisible. On THIS repository's own compiled graph it is not: 49,261 of
62,366 nodes (79.0%) carry a ``source_path`` across 2,522 distinct paths, and
the other 21% are 10,824 ``Event`` nodes plus SessionInsight / Decision / TODO
/ Takeaway / Failure / Synthesis. Those 10,824 keyless Events could flood a
budget the cap cannot touch — the exact inverse of the intent. **A real-graph
caller MUST pass a ``group_key`` with a fallback.**

The mirror-image hazard is a corpus where one ``source_path`` legitimately
holds thousands of nodes: every ``CodeSymbol`` in a file shares one path, so
``source_cap=1`` would cap an honest "every function in this file" query at ONE
hit. That is why ``source_cap`` defaults to ``None`` at the library level even
though 1 is the winning LoCoMo value, why this function is NOT wired into
``compile_context``, ``mcp_server`` or ``charter_route`` in this change, and why
callers opt in one at a time — the discipline ``source_root`` established
(commit 2fd862e0).

COST
----
Measured on this machine (``.venv/bin/python``, numpy 2.4.6, ``rank_bm25``
absent), conv-26 (345 nodes, 650 edges, 19 documents), warm, per query: one
``hybrid_search`` 18.6 ms, two 35.0 ms, ``_lexical_texts`` 1.2 ms, the corpus
tokenise for the DF table 2.5 ms — about 38 ms total, 2.05x baseline. Free at
this scale.

At 62k nodes the cost is ESTIMATED at ~1.0-1.2 s/query (~2.5x) from
``bm25_index.py``'s own recorded 196 ms corpus tokenise + 146 ms DF table + 65
ms scoring at 46,926 candidates, and is NOT measured end to end. The fix if a
real caller needs it is already a ``hybrid_search`` parameter: with
``bm25_index`` passed, ``prepare(lex_texts, _tokenize)`` serves the tokenisation
and ``postings(query_terms)`` gives DF for only the query's <=8 content terms
instead of a whole-vocabulary ``Counter``. Ship the in-memory table first — it
is correct everywhere — and add that branch behind the same
``bm25_index is not None and not _rank_bm25_available()`` gate the BM25 lane
already uses.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import (
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Tuple,
)

from ..research_graph import ResearchGraph, ResearchNode
from .bm25_index import Bm25Index
from .hybrid import (
    RRF_K,
    EmbeddingBackend,
    HybridSearchResult,
    ScoredNode,
    _lexical_texts,
    _node_text,
    _tokenize,
    hybrid_search,
)
from .query_decompose import DEFAULT_UBIQUITY_DF_RATIO, discriminative_subquery
from .vector_cache import VectorCache

__all__ = [
    "DEFAULT_EXTRA_FACETS",
    "DEFAULT_OVERFETCH",
    "DEFAULT_SOURCE_CAP",
    "MIN_SEED_WEIGHT",
    "fanout_search",
]


#: How many candidates each sub-query is asked for, per unit of budget.
#:
#: The merge can only choose among what the passes returned, so this is the
#: recall ceiling of the stage — at overfetch 1 a document one pass ranked
#: 11th can never reach a top-10 budget. It SATURATES immediately: measured on
#: conv-26 multi-hop ALL-gold@10, overfetch 1 scores 65.6%, 1.5 scores 75.0%,
#: and 2, 3 and 4 all score 75.0%. 2 is therefore the cheapest value that buys
#: the whole effect; higher only costs sort time.
DEFAULT_OVERFETCH = 2

#: Hits per document admitted into the HEAD of the merged result.
#:
#: A cliff, not a slope: conv-26 multi-hop ALL-gold@10 is 68.8% at cap 1, 53.1%
#: at 2 and 50.0% at 3. One hit per document is the value for a corpus that is
#: one node-set per document; see the ``group_key`` hole above for the corpus
#: shapes where it is wrong. Exported for callers that want the winning LoCoMo
#: value by name — :func:`fanout_search` itself defaults to ``None``.
DEFAULT_SOURCE_CAP = 1

#: Extra single-token sub-queries beyond the stripped one, off by default.
#:
#: Pooled multi-hop ALL-gold@10: width 2 (i.e. this at 0) scores 50.4%, width 3
#: scores 53.2%, width 4 scores 50.0%. Width 3 wins the multi-hop number and
#: loses on single-hop and temporal, which the objective forbids regressing, so
#: the default is the width that holds every category. The knob exists because
#: the trade is corpus-shaped, not because 0 is uninteresting.
DEFAULT_EXTRA_FACETS = 0

#: Floor for a seed weight handed to ``personalized_pagerank`` through the
#: ``extra_rankings`` seam. Unused here; see the module docstring's PPR seam
#: note. ``personalized_pagerank`` RAISES on an all-zero weight map, and a node
#: can legitimately arrive from ``hybrid_search`` at fused score 0.0, so an
#: unguarded ``{id: score}`` map blows up on some queries.
MIN_SEED_WEIGHT = 1e-9


def _source_path_key(node: ResearchNode) -> str:
    """Default grouping key: the node's ``source_path``, ``""`` when it has none.

    ``""`` means "not from a document", and such a node is never capped — see
    the module docstring's measured hole.
    """
    return str(getattr(node, "source_path", "") or "")


def _doc_freq(lex_texts: Sequence[str]) -> Tuple[Counter, int]:
    """Document frequency per token over the strings the LANES score.

    Counted over ``_lexical_texts`` output rather than node summaries, so a
    term's DF reflects the raw source text that the BM25 and lexical lanes
    actually rank on when ``source_root`` is passed. Counting a different
    corpus would strip terms that are rare in the text being searched.
    """
    doc_freq: Counter = Counter()
    for text in lex_texts:
        doc_freq.update(set(_tokenize(text)))
    return doc_freq, len(lex_texts)


def _facets(
    sub: str,
    doc_freq: Counter,
    extra_facets: int,
    existing: Sequence[str],
) -> List[str]:
    """Up to ``extra_facets`` single-token sub-queries, rarest term first.

    Tie-broken on the token string so the same corpus always produces the same
    facets — the merge's determinism depends on the sub-query list being fixed.
    """
    if extra_facets <= 0 or not sub:
        return []
    tokens = sorted(set(_tokenize(sub)), key=lambda t: (doc_freq.get(t, 0), t))
    seen = set(existing)
    out: List[str] = []
    for token in tokens:
        if len(out) >= extra_facets:
            break
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def fanout_search(
    graph: ResearchGraph,
    query: str,
    *,
    top_k: int = 20,
    weights: Optional[Dict[str, float]] = None,
    mode: str = "hybrid",
    backend: Optional[EmbeddingBackend] = None,
    candidate_filter: Optional[Iterable[ResearchNode]] = None,
    vector_cache: Optional[VectorCache] = None,
    bm25_index: Optional[Bm25Index] = None,
    source_root: Optional[Path] = None,
    profile: bool = False,
    document_first: bool = False,
    overfetch: int = DEFAULT_OVERFETCH,
    source_cap: Optional[int] = None,
    ubiquity_df_ratio: float = DEFAULT_UBIQUITY_DF_RATIO,
    extra_facets: int = DEFAULT_EXTRA_FACETS,
    group_key: Optional[Callable[[ResearchNode], str]] = None,
    extra_rankings: Optional[Sequence[Sequence[ScoredNode]]] = None,
) -> HybridSearchResult:
    """:func:`~tesserae.retrieval.hybrid.hybrid_search`, fanned out and merged.

    Runs the lanes once for ``query`` and once more for the ubiquity-stripped
    sub-query (plus ``extra_facets`` single-token facets), then merges the
    rankings round-robin by rank, admitting at most ``source_cap`` hits per
    document into the head. Every pass sees the identical candidate set, so
    nothing here can surface a node the caller's ``candidate_filter``
    excluded.

    THE NO-OP PATH, stated precisely because "the default is a no-op" is the
    easy misreading: when the query has nothing to strip AND ``source_cap`` is
    ``None`` AND there are no ``extra_rankings``, this returns
    ``hybrid_search``'s own result object — not an equal copy — which
    ``test_fanout_with_no_split_and_no_cap_is_byte_identical_to_hybrid_search``
    pins by object identity. A query that DOES split still fans out under the
    default ``source_cap=None``; what the default buys is that the cap, the
    part whose correctness depends on the corpus's shape, is never chosen on a
    caller's behalf. Nothing in this repo calls this function yet, so the real
    opt-in is calling it at all. :data:`DEFAULT_SOURCE_CAP` carries the winning
    LoCoMo value for the callers whose corpus is one node-set per document.

    ``group_key`` decides what "the same document" means. The default is the
    node's ``source_path``, and a node with none is never capped — on a graph
    where a fifth of the nodes carry no path, pass a key with a fallback or
    those nodes flood the budget. See the module docstring.

    ``extra_rankings`` are merged as further lists, competing under the same
    cap. It is the seam for graph expansion and ships unused; the two gates
    that must pass before feeding it are in the module docstring.

    ``total_matches`` is the ORIGINAL query's: what that query admitted, not
    the size of the union. The fan-out changes which of them reach the budget,
    not how many exist, so the MCP "X of N matches" string keeps a stable
    meaning. It therefore UNDER-reports the union — a documented choice, not an
    accident.
    """
    nodes = list(candidate_filter) if candidate_filter is not None else list(graph.nodes)

    # The two short-circuits stay hybrid_search's rather than being
    # reimplemented here: their bounds differ subtly (the empty-query path
    # slices `nodes[:max(1, top_k)]` with NO `min(..., len(nodes))`, and
    # `charter_route.py:399` depends on exactly that), so a second copy would
    # be a second contract.
    if not nodes or not query.strip():
        return hybrid_search(
            graph, query, top_k=top_k, weights=weights, mode=mode, backend=backend,
            candidate_filter=nodes, vector_cache=vector_cache,
            bm25_index=bm25_index, source_root=source_root, profile=profile,
            document_first=document_first,
        )

    # Materialised here for the DF table ONLY, and through this exact call so
    # the table is counted over precisely the strings the BM25/lexical lanes
    # score — raw source included when `source_root` is passed. `hybrid_search`
    # rebuilds it internally; that duplication is deliberate (1.2 ms on
    # conv-26) and is the cost line in the module docstring.
    texts = [_node_text(node) for node in nodes]
    lex_texts = _lexical_texts(nodes, texts, source_root)
    doc_freq, n_docs = _doc_freq(lex_texts)

    sub = discriminative_subquery(
        query, doc_freq=doc_freq, n_docs=n_docs,
        ubiquity_df_ratio=ubiquity_df_ratio,
    )
    subqueries: List[str] = [query] + ([sub] if sub else [])
    subqueries.extend(_facets(sub, doc_freq, extra_facets, subqueries))

    # THE NO-OP PATH. Nothing was stripped, no cap, no extra rankings: there is
    # one ranking and it is hybrid_search's, returned with the ORIGINAL top_k
    # so the result object is byte-identical to not having this stage.
    if len(subqueries) == 1 and source_cap is None and not extra_rankings:
        return hybrid_search(
            graph, query, top_k=top_k, weights=weights, mode=mode, backend=backend,
            candidate_filter=nodes, vector_cache=vector_cache,
            bm25_index=bm25_index, source_root=source_root, profile=profile,
            document_first=document_first,
        )

    fetch = max(1, int(top_k) * max(1, int(overfetch)))
    results = [
        hybrid_search(
            graph, subquery, top_k=fetch, weights=weights, mode=mode,
            backend=backend, candidate_filter=nodes, vector_cache=vector_cache,
            bm25_index=bm25_index, source_root=source_root, profile=profile,
            document_first=document_first,
        )
        for subquery in subqueries
    ]
    rankings: List[Sequence[ScoredNode]] = [r.scored for r in results]
    rankings.extend(extra_rankings or [])

    merged = _merge_document_disjoint(
        rankings,
        top_k=top_k,
        source_cap=source_cap,
        group_key=group_key or _source_path_key,
    )
    return HybridSearchResult(
        query=query,
        mode=results[0].mode,
        backend=results[0].backend,
        weights=results[0].weights,
        scored=merged,
        total_matches=results[0].total_matches,
        profile=results[0].profile,
    )


def _merge_document_disjoint(
    rankings: Sequence[Sequence[ScoredNode]],
    *,
    top_k: int,
    source_cap: Optional[int],
    group_key: Callable[[ResearchNode], str],
) -> List[ScoredNode]:
    """Round-robin merge of ``rankings`` with at most ``source_cap`` per group.

    Deterministic by construction: fixed list order, fixed rank order, no
    dependence on dict iteration. Same inputs, byte-identical output.
    """
    budget = max(1, int(top_k))
    taken_ids: set = set()
    group_counts: Dict[str, int] = {}
    chosen: List[Tuple[ScoredNode, int]] = []  # (node, source list index)
    depth = max((len(r) for r in rankings), default=0)

    # PASS 1 — round-robin by rank, honouring the cap.
    for rank_i in range(depth):
        for list_i, ranking in enumerate(rankings):
            if len(chosen) >= budget:
                break
            if rank_i >= len(ranking):
                continue
            item = ranking[rank_i]
            if item.node.id in taken_ids:
                continue
            key = group_key(item.node)
            if source_cap is not None and key and group_counts.get(key, 0) >= source_cap:
                continue
            taken_ids.add(item.node.id)
            if key:
                group_counts[key] = group_counts.get(key, 0) + 1
            chosen.append((item, list_i))
        if len(chosen) >= budget:
            break

    # PASS 2 — the cap must never SHRINK the result. When the union holds fewer
    # distinct groups than the budget (19 sessions on conv-26, and any k > 19
    # asks for more), fill from what pass 1 skipped, in the same round-robin
    # order, ignoring the cap. The HEAD stays document-disjoint, which is where
    # the metric is decided, and the length contract every hybrid_search caller
    # has is preserved. Without this, K=40 would return 19 items and fire
    # LocomoMemory.shortfalls on every query.
    if len(chosen) < budget:
        for rank_i in range(depth):
            for ranking in rankings:
                if len(chosen) >= budget:
                    break
                if rank_i >= len(ranking):
                    continue
                item = ranking[rank_i]
                if item.node.id in taken_ids:
                    continue
                taken_ids.add(item.node.id)
                chosen.append((item, -1))
            if len(chosen) >= budget:
                break

    # Re-stamp, mirroring `rerank_nodes` exactly, so the list is monotonically
    # descending in `score` and any downstream re-sort is a no-op. `fanout` is
    # added to per_lane/ranks ONLY on objects this function returns — contained
    # to opt-in callers, as `rerank` is — and never to `lane_scores`, which
    # would change `RetrievalProfile.lanes` for every caller in the repo.
    #
    # `per_lane["fanout"]` is the INDEX of the ranking that supplied the item,
    # not a score: which pass found a hit is the diagnostic that says whether
    # the sub-query earned its cost, and -1 marks an item the no-shrink second
    # pass admitted over the cap.
    out: List[ScoredNode] = []
    for rank, (item, list_i) in enumerate(chosen, start=1):
        out.append(
            replace(
                item,
                score=1.0 / (RRF_K + rank),
                per_lane={**item.per_lane, "fanout": float(list_i)},
                ranks={**item.ranks, "fanout": rank},
            )
        )
    return out

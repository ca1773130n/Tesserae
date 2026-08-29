"""Tests for :mod:`tesserae.retrieval.fanout`.

Everything here runs on a normal install: no torch, no network, no compiled
graph. The embedding lane is driven by ``HashEmbeddingBackend`` so no model is
downloaded and the ordering is deterministic across machines.

The load-bearing test is
:func:`test_fanout_with_no_split_and_no_cap_is_byte_identical_to_hybrid_search`,
which pins the opt-in convention ``source_root`` established at
``tests/test_hybrid_search.py:1095``: absent the new parameters, this stage is
byte-identical to not having it.
"""

from __future__ import annotations

from typing import List

from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.retrieval.fanout import (
    DEFAULT_OVERFETCH,
    DEFAULT_SOURCE_CAP,
    _merge_document_disjoint,
    _source_path_key,
    fanout_search,
)
from tesserae.retrieval.hybrid import (
    HashEmbeddingBackend,
    ScoredNode,
    hybrid_search,
)
from tesserae.retrieval.query_decompose import discriminative_subquery


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _node(node_id: str, description: str, source_path: str = "") -> ResearchNode:
    return ResearchNode(
        id=node_id,
        name=node_id,
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        description=description,
        source_path=source_path or None,
    )


def _conversation_graph() -> ResearchGraph:
    """Five sessions, four nodes each, one speaker in all of them.

    The shape the fan-out exists for: ``melanie`` is in 20 of 20 nodes (DF ratio
    1.0) so it discriminates nothing, while each session's topic term is in that
    session's four nodes only (0.20, under the 0.30 ceiling).

    Five sessions and not three because DF is counted per NODE: at three
    sessions a topic term would sit at 4/12 = 0.33 and be stripped as
    ubiquitous alongside the speaker, which is the fixture accidentally
    reproducing the bug rather than the corpus.
    """
    nodes: List[ResearchNode] = []
    topics = {"s1": "pottery", "s2": "hiking", "s3": "sourdough",
              "s4": "kayaking", "s5": "origami"}
    for session, topic in topics.items():
        for i in range(4):
            nodes.append(
                _node(
                    f"{session}-n{i}",
                    f"melanie mentioned {topic} in passing, detail {i}",
                    source_path=f"/corpus/{session}.md",
                )
            )
    return ResearchGraph(nodes=nodes)


def _rare_term_graph() -> ResearchGraph:
    """Six nodes, six terms, each term in exactly ONE node.

    With six documents the ubiquity ceiling is ``0.30 * 6 = 1.8``, so a term in
    one node survives and a query built from them is kept WHOLE — which is the
    ``""`` case the byte-identity test needs.
    """
    terms = ["pottery", "hiking", "sourdough", "kayaking", "origami", "welding"]
    return ResearchGraph(
        nodes=[
            _node(f"n{i}", f"a note about {term}", source_path=f"/corpus/{i}.md")
            for i, term in enumerate(terms)
        ]
    )


def _scored(node: ResearchNode, score: float) -> ScoredNode:
    return ScoredNode(node=node, score=score, per_lane={"bm25": score}, ranks={"bm25": 1})


def _ranking(nodes: List[ResearchNode]) -> List[ScoredNode]:
    return [_scored(node, 1.0 / (i + 1)) for i, node in enumerate(nodes)]


# ---------------------------------------------------------------------------
# The opt-in contract
# ---------------------------------------------------------------------------


def test_fanout_with_no_split_and_no_cap_is_byte_identical_to_hybrid_search():
    """THE load-bearing test. Nothing stripped, no cap: same result object.

    Compares the full observable surface a caller can see — node ids, fused
    scores, and BOTH diagnostic dicts — not just the ordering, because
    ``per_lane`` / ``ranks`` are what a profiled caller reads and adding a
    ``fanout`` key to them on the default path would be a silent shape change.
    """
    graph = _rare_term_graph()
    query = "pottery hiking"
    backend = HashEmbeddingBackend()

    # Precondition: this query survives the filter whole, so there IS no second
    # pass. Asserted rather than assumed — a fixture drifting into a split
    # would make this test pass for the wrong reason.
    assert discriminative_subquery(
        query,
        doc_freq={"pottery": 1, "hiking": 1},
        n_docs=len(graph.nodes),
    ) == ""

    base = hybrid_search(graph, query, top_k=4, backend=backend)

    # The no-op path RETURNS hybrid_search's own result object rather than
    # rebuilding an equal one, which is the strongest form this claim has:
    # there is no field a future edit could forget to copy.
    from tesserae.retrieval import fanout as fanout_mod

    original = fanout_mod.hybrid_search
    returned: List[object] = []

    def _spy(*args, **kwargs):
        result = original(*args, **kwargs)
        returned.append(result)
        return result

    fanout_mod.hybrid_search = _spy
    try:
        fanned = fanout_search(graph, query, top_k=4, backend=backend)
    finally:
        fanout_mod.hybrid_search = original

    assert len(returned) == 1, "the no-op path must run the lanes ONCE"
    assert fanned is returned[0]

    assert [
        (s.node.id, s.score, dict(s.per_lane), dict(s.ranks)) for s in fanned.scored
    ] == [
        (s.node.id, s.score, dict(s.per_lane), dict(s.ranks)) for s in base.scored
    ]
    assert fanned.total_matches == base.total_matches
    assert fanned.weights == base.weights
    assert fanned.mode == base.mode
    assert fanned.backend == base.backend
    assert fanned.profile is base.profile is None


def test_a_splitting_query_still_fans_out_under_the_default_cap():
    """The default is a no-op for a query with nothing to strip, NOT globally.

    Recorded as a test because "source_cap defaults to None" reads like "the
    defaults change nothing", and it does not: what the default buys is that
    the cap — the part whose correctness depends on the corpus's shape — is
    never chosen on a caller's behalf.
    """
    graph = _conversation_graph()
    backend = HashEmbeddingBackend()
    query = "what did melanie say about pottery"

    from tesserae.retrieval import fanout as fanout_mod

    original = fanout_mod.hybrid_search
    passes: List[str] = []

    def _spy(g, q, **kwargs):
        passes.append(q)
        return original(g, q, **kwargs)

    fanout_mod.hybrid_search = _spy
    try:
        fanned = fanout_search(graph, query, top_k=4, backend=backend)
    finally:
        fanout_mod.hybrid_search = original

    assert passes == [query, "say pottery"], "the second pass must have run"
    # And the merge re-stamped what it produced, so it is not hybrid_search's
    # object being handed back.
    assert all("fanout" in s.ranks for s in fanned.scored)


def test_fanout_empty_query_and_empty_nodes_delegate_to_hybrid_search():
    """Both short circuits stay hybrid_search's, bounds and all.

    The empty-query path slices ``nodes[:max(1, top_k)]`` with NO
    ``min(..., len(nodes))``; ``charter_route.py:399`` depends on that, so a
    second copy here would be a second contract.
    """
    graph = _rare_term_graph()
    backend = HashEmbeddingBackend()

    blank_base = hybrid_search(graph, "   ", top_k=3, backend=backend)
    blank_fan = fanout_search(graph, "   ", top_k=3, backend=backend,
                              source_cap=1, extra_facets=2)
    assert [s.node.id for s in blank_fan.scored] == [s.node.id for s in blank_base.scored]
    assert blank_fan.total_matches == blank_base.total_matches

    empty_base = hybrid_search(graph, "pottery", top_k=3, backend=backend,
                               candidate_filter=[])
    empty_fan = fanout_search(graph, "pottery", top_k=3, backend=backend,
                              candidate_filter=[], source_cap=1)
    assert empty_fan.scored == empty_base.scored == []
    assert empty_fan.total_matches == empty_base.total_matches == 0


def test_fanout_is_deterministic():
    graph = _conversation_graph()
    backend = HashEmbeddingBackend()
    kwargs = dict(top_k=6, backend=backend, source_cap=1, extra_facets=1)

    first = fanout_search(graph, "what did melanie say about pottery", **kwargs)
    second = fanout_search(graph, "what did melanie say about pottery", **kwargs)

    assert [
        (s.node.id, s.score, dict(s.per_lane), dict(s.ranks)) for s in first.scored
    ] == [
        (s.node.id, s.score, dict(s.per_lane), dict(s.ranks)) for s in second.scored
    ]


def test_fanout_head_is_document_disjoint_on_a_real_search():
    """End to end: the cap buys distinct sessions the budget otherwise repeats.

    ``melanie`` is in every node, so the uncapped top 3 is three nodes of ONE
    session — the budget spent three times on the same evidence, which is the
    failure this whole module is for.

    ``overfetch=8`` here so every ranking covers all 20 candidates and the
    union therefore holds more distinct documents than the budget. That is what
    makes the head *exactly* disjoint rather than merely more diverse: below it
    the no-shrink second pass legitimately re-admits a document to fill the
    budget, which ``test_merge_second_pass_fills_the_budget_when_groups_run_out``
    pins separately.
    """
    graph = _conversation_graph()
    backend = HashEmbeddingBackend()
    query = "what did melanie say about pottery"

    uncapped = fanout_search(graph, query, top_k=3, backend=backend,
                             overfetch=8, source_cap=None)
    capped = fanout_search(graph, query, top_k=3, backend=backend,
                           overfetch=8, source_cap=DEFAULT_SOURCE_CAP)

    assert len({s.node.source_path for s in uncapped.scored}) == 1
    paths = [s.node.source_path for s in capped.scored]
    assert len(paths) == 3
    assert len(set(paths)) == 3


def test_fanout_reports_the_original_querys_total_matches():
    """``total_matches`` is what the ORIGINAL query admitted, deliberately.

    It under-reports the union so the MCP "X of N matches" string keeps a
    stable meaning: the fan-out changes which candidates reach the budget, not
    how many exist.
    """
    graph = _conversation_graph()
    backend = HashEmbeddingBackend()
    query = "what did melanie say about pottery"

    base = hybrid_search(graph, query, top_k=3, backend=backend)
    fanned = fanout_search(graph, query, top_k=3, backend=backend, source_cap=1)
    assert fanned.total_matches == base.total_matches


def test_merge_admits_extra_rankings():
    """The seam graph expansion would come back through, pinned unused.

    A hand-built ranking reaches the result with no other machinery: a PPR
    result maps ids -> nodes carrying ``source_path``, so the same
    ``group_key`` applies and it slots in as one more list.
    """
    graph = _conversation_graph()
    backend = HashEmbeddingBackend()
    outsider = _node("outsider", "nothing in this text matches the query at all",
                     source_path="/corpus/s9.md")

    fanned = fanout_search(
        graph, "pottery", top_k=4, backend=backend,
        extra_rankings=[[_scored(outsider, 0.9)]],
    )
    assert "outsider" in [s.node.id for s in fanned.scored]


# ---------------------------------------------------------------------------
# The merge
# ---------------------------------------------------------------------------


def test_merge_head_is_document_disjoint():
    nodes = [
        _node(f"{doc}-{i}", "text", source_path=f"/corpus/{doc}.md")
        for doc in ("a", "b", "c")
        for i in range(4)
    ]
    merged = _merge_document_disjoint(
        [_ranking(nodes)], top_k=3, source_cap=1, group_key=_source_path_key
    )
    keys = [_source_path_key(s.node) for s in merged]
    assert len(keys) == 3
    assert len(set(keys)) == 3
    # Scores are re-stamped as RRF ranks, so the list is monotonically
    # descending and any downstream re-sort is a no-op.
    assert [s.score for s in merged] == sorted((s.score for s in merged), reverse=True)
    assert [s.ranks["fanout"] for s in merged] == [1, 2, 3]


def test_merge_second_pass_fills_the_budget_when_groups_run_out():
    """The cap must never SHRINK the result — pins the no-shrink clamp.

    12 nodes across 3 documents, budget 10: pass 1 admits 3 under the cap and
    pass 2 fills to 10 ignoring it. Without this, a K=40 LoCoMo query over a
    19-session corpus would return 19 items and fire
    ``LocomoMemory.shortfalls`` every single time.
    """
    nodes = [
        _node(f"{doc}-{i}", "text", source_path=f"/corpus/{doc}.md")
        for doc in ("a", "b", "c")
        for i in range(4)
    ]
    merged = _merge_document_disjoint(
        [_ranking(nodes)], top_k=10, source_cap=1, group_key=_source_path_key
    )
    assert len(merged) == min(10, len(nodes))
    head = [_source_path_key(s.node) for s in merged[:3]]
    assert len(set(head)) == 3
    # Nothing is duplicated by the second pass.
    ids = [s.node.id for s in merged]
    assert len(ids) == len(set(ids))

    # ...and a union smaller than the budget still returns exactly the union.
    short = _merge_document_disjoint(
        [_ranking(nodes[:2])], top_k=10, source_cap=1, group_key=_source_path_key
    )
    assert len(short) == 2


def test_merge_never_caps_keyless_nodes():
    """A node from no document cannot be a redundant re-read of one.

    This is a documented HOLE, not an oversight: on Tesserae's own compiled
    graph 10,824 Events carry no ``source_path`` and could flood a budget the
    cap cannot touch, which is why ``group_key`` is a parameter. Pinned so a
    future "fix" has to argue with a test.
    """
    nodes = [_node(f"keyless-{i}", "text") for i in range(5)]
    merged = _merge_document_disjoint(
        [_ranking(nodes)], top_k=5, source_cap=1, group_key=_source_path_key
    )
    assert [s.node.id for s in merged] == [n.id for n in nodes]


def test_merge_round_robins_across_lists_before_going_deeper():
    """Rank 1 of every list outranks rank 2 of the first — that IS the fan-out.

    Without it the second pass could only append, and the sub-query's best hit
    would land at rank 11 of a 10-slot budget.
    """
    a = [_node(f"a{i}", "t", source_path=f"/corpus/a{i}.md") for i in range(3)]
    b = [_node(f"b{i}", "t", source_path=f"/corpus/b{i}.md") for i in range(3)]
    merged = _merge_document_disjoint(
        [_ranking(a), _ranking(b)], top_k=4, source_cap=None,
        group_key=_source_path_key,
    )
    assert [s.node.id for s in merged] == ["a0", "b0", "a1", "b1"]
    # `fanout` records which list each item came from, contained to objects
    # this stage returns exactly as `rerank` is.
    assert [s.per_lane["fanout"] for s in merged] == [0.0, 1.0, 0.0, 1.0]


def test_merge_deduplicates_a_node_two_lists_both_ranked():
    a = [_node("shared", "t", source_path="/corpus/a.md"),
         _node("a1", "t", source_path="/corpus/b.md")]
    merged = _merge_document_disjoint(
        [_ranking(a), _ranking(list(reversed(a)))], top_k=4, source_cap=None,
        group_key=_source_path_key,
    )
    assert [s.node.id for s in merged] == ["shared", "a1"]


def test_merge_accepts_a_group_key_with_a_fallback():
    """The escape hatch a real-graph caller needs, exercised."""
    nodes = [_node(f"event-{i}", "t") for i in range(4)]

    def key(node: ResearchNode) -> str:
        return _source_path_key(node) or f"type:{node.type.value}"

    merged = _merge_document_disjoint(
        [_ranking(nodes)], top_k=4, source_cap=1, group_key=key
    )
    # All four share the fallback key, so the cap admits ONE in pass 1 and the
    # no-shrink clamp supplies the rest.
    assert len(merged) == 4
    assert merged[0].node.id == "event-0"


def test_overfetch_default_is_the_saturating_value():
    """A constant with a measured curve behind it, pinned so it cannot drift."""
    assert DEFAULT_OVERFETCH == 2
    assert DEFAULT_SOURCE_CAP == 1


def test_fanout_never_returns_a_node_outside_the_candidate_filter():
    """Every pass sees the identical candidate set.

    ``hybrid_search``'s one caller that filters (``mcp_server.py:4923``) does so
    to exclude node types the user asked against; a second pass over an
    unfiltered graph would resurrect them.
    """
    graph = _conversation_graph()
    backend = HashEmbeddingBackend()
    allowed = [n for n in graph.nodes if n.source_path == "/corpus/s1.md"]

    fanned = fanout_search(
        graph, "what did melanie say about pottery", top_k=8, backend=backend,
        candidate_filter=allowed, source_cap=None,
    )
    assert {s.node.id for s in fanned.scored} <= {n.id for n in allowed}


def test_extra_facets_add_single_token_subqueries_rarest_first():
    """Deterministic, tie-broken on the token string."""
    graph = _conversation_graph()
    backend = HashEmbeddingBackend()
    captured: List[str] = []

    from tesserae.retrieval import fanout as fanout_mod

    original = fanout_mod.hybrid_search

    def _spy(g, q, **kwargs):
        captured.append(q)
        return original(g, q, **kwargs)

    fanout_mod.hybrid_search = _spy
    try:
        fanout_search(graph, "what did melanie say about pottery", top_k=4,
                      backend=backend, source_cap=1, extra_facets=1)
    finally:
        fanout_mod.hybrid_search = original

    # Original, then the stripped sub-query, then one single-token facet drawn
    # from it — the ubiquitous speaker term appears in neither.
    assert captured[0] == "what did melanie say about pottery"
    assert "melanie" not in captured[1].split()
    assert "pottery" in captured[1].split()
    assert len(captured) == 3
    assert len(captured[2].split()) == 1
    assert captured[2] in captured[1].split()


def test_fanout_rejects_nothing_and_raises_nothing_on_a_one_node_graph():
    """Degenerate corpora are a real shape here — ``n_docs`` of 1 makes the
    ubiquity ceiling 0.3, so EVERY term is ubiquitous and the filter keeps
    nothing. That must be the one-pass signal, not an exception."""
    graph = ResearchGraph(nodes=[_node("only", "pottery kiln", source_path="/c/a.md")])
    backend = HashEmbeddingBackend()
    result = fanout_search(graph, "pottery kiln", top_k=5, backend=backend,
                           source_cap=1)
    assert [s.node.id for s in result.scored] == ["only"]


def test_group_key_none_uses_source_path():
    """The documented default, asserted rather than implied."""
    graph = _conversation_graph()
    backend = HashEmbeddingBackend()
    explicit = fanout_search(graph, "what did melanie say about pottery", top_k=3,
                             backend=backend, source_cap=1,
                             group_key=_source_path_key)
    implicit = fanout_search(graph, "what did melanie say about pottery", top_k=3,
                             backend=backend, source_cap=1)
    assert [s.node.id for s in explicit.scored] == [s.node.id for s in implicit.scored]


def test_fanout_search_accepts_and_forwards_document_first(tmp_path):
    """The LoCoMo arm passes document_first=True to whichever search it runs;
    with --fanout that was fanout_search, which did not know the kwarg and
    crashed the retrieval canary (2026-08-29)."""
    from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
    from tesserae.retrieval.fanout import fanout_search

    docs = tmp_path / "docs"; docs.mkdir()
    a = docs / "a.md"; a.write_text("Caroline went to the LGBTQ support group on Friday.")
    nodes = [
        ResearchNode(id="SD:a", name="Session A", type=ResearchNodeType.SOURCE_DOCUMENT, source_path=str(a)),
        ResearchNode(id="C:1", name="support group", type=ResearchNodeType.CONCEPT, description="a support group"),
        ResearchNode(id="C:2", name="LGBTQ community", type=ResearchNodeType.CONCEPT, description="the LGBTQ community"),
    ]
    g = ResearchGraph(nodes=nodes, edges=[])
    out = fanout_search(g, "LGBTQ support group", top_k=3, source_root=docs, document_first=True)
    assert out.scored and out.scored[0].node.id == "SD:a"
    plain = fanout_search(g, "LGBTQ support group", top_k=3, source_root=docs)
    assert [s.node.id for s in plain.scored] == [s.node.id for s in fanout_search(g, "LGBTQ support group", top_k=3, source_root=docs, document_first=False).scored]

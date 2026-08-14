"""The BM25 inverted index: it may change what a query COSTS, never what it returns.

Every test here is a way of asking the same question. The lane used to rebuild
its whole world per query — tokenise the corpus, count document frequency over
the entire vocabulary, score every document — and the index removes that. What
it must not remove is the answer, so the assertions are exact float equality
between an indexed run and the in-memory one, on a corpus wide enough that IDF
actually separates terms rather than collapsing to one value.
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
from typing import List, Optional, Sequence

import pytest

from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.retrieval.bm25_index import Bm25Index, doc_key
from tesserae.retrieval.hybrid import hybrid_search

# A vocabulary with a deliberate frequency gradient: the first few words land in
# most documents (low IDF), the tail lands in one or two (high IDF). A corpus
# where every term has the same document frequency would make IDF a constant and
# would pass these tests even if the index computed it wrongly.
_COMMON = ("system", "graph", "node", "query", "index")
_MID = (
    "retrieval", "compile", "sidecar", "temporal", "session", "vector",
    "lexical", "fusion", "provenance", "artifact", "ledger", "audit",
)
_RARE = tuple(f"corpuscle{i:03d}" for i in range(120))


def _wide_graph(count: int = 240, seed: int = 7) -> ResearchGraph:
    """A deterministic corpus with real vocabulary spread and varied lengths."""
    rng = random.Random(seed)
    nodes: List[ResearchNode] = []
    for index in range(count):
        words: List[str] = []
        words.extend(rng.sample(_COMMON, rng.randint(1, len(_COMMON))))
        words.extend(rng.sample(_MID, rng.randint(1, 5)))
        words.extend(rng.sample(_RARE, rng.randint(1, 4)))
        # Repeat some words so term frequency (not just presence) varies.
        words.extend(rng.choice(words) for _ in range(rng.randint(0, 6)))
        rng.shuffle(words)
        nodes.append(
            ResearchNode(
                id=f"Paper:doc-{index:04d}",
                name=f"Document {index:04d} {words[0]}",
                type=ResearchNodeType.PAPER,
                description=" ".join(words),
            )
        )
    # A document with no description at all — the shortest ``_node_text`` the
    # corpus can produce, which is what drags ``avgdl`` around.
    nodes.append(
        ResearchNode(id="Paper:empty", name="", type=ResearchNodeType.PAPER, description="")
    )
    # Two nodes whose descriptions are byte-identical. Their ``_node_text``
    # still differs (it carries the node id), which is the point: the index is
    # keyed on the text that was tokenised, so these are two rows — the
    # one-row-two-documents case is exercised directly against ``prepare``.
    for suffix in ("a", "b"):
        nodes.append(
            ResearchNode(
                id=f"Paper:twin-{suffix}",
                name="Twin",
                type=ResearchNodeType.PAPER,
                description="fusion provenance corpuscle007 corpuscle007",
            )
        )
    return ResearchGraph(nodes=nodes, edges=[])


_QUERIES = (
    "graph retrieval sidecar",
    "corpuscle007 fusion",
    "system",
    # A repeated term: the in-memory lane walks ``query_tokens`` with its
    # duplicates and counts the contribution twice, and the index must too.
    "temporal temporal ledger",
    # A term nobody has, beside one everybody has: the absent term must reach
    # neither an IDF nor a score.
    "unobtainium graph",
)


def _project(tmp_path, name: str):
    root = tmp_path / name
    (root / ".tesserae").mkdir(parents=True)
    return root


def _bm25_lane(
    graph: ResearchGraph,
    query: str,
    *,
    index: Optional[Bm25Index] = None,
    candidates: Optional[Sequence[ResearchNode]] = None,
):
    """Every node the BM25 lane admitted, with its exact per-lane score."""
    result = hybrid_search(
        graph,
        query,
        top_k=10_000,
        mode="bm25",
        bm25_index=index,
        candidate_filter=list(candidates) if candidates is not None else None,
    )
    return [(item.node.id, item.per_lane["bm25"], item.score) for item in result.scored]


# --------------------------------------------------------------------------- #
# The invariant                                                                #
# --------------------------------------------------------------------------- #


def test_bm25_scores_are_identical_cold_warm_and_unindexed(tmp_path):
    """Cold index, warm index and no index must produce the SAME scores.

    Exact float equality on purpose, per lane and fused. The index rearranges
    which documents are visited; if it ever rearranged the arithmetic — a
    different IDF, a summation in another order, a document frequency taken
    over the wrong set — it would show up here and nowhere else, because BM25
    ordering is stable enough to hide small numeric drift.
    """
    graph = _wide_graph()
    index = Bm25Index.for_project(_project(tmp_path, "proj"))
    assert index is not None

    for query in _QUERIES:
        unindexed = _bm25_lane(graph, query)
        cold = _bm25_lane(graph, query, index=index)
        warm = _bm25_lane(graph, query, index=index)
        assert cold == unindexed, f"cold index changed scores for {query!r}"
        assert warm == unindexed, f"warm index changed scores for {query!r}"
        # A test that compared two empty result sets would pass vacuously.
        assert unindexed, f"query {query!r} matched nothing — fixture is wrong"

    # And the index actually served rather than quietly falling back.
    assert index.stats.hits > 0
    assert index.count() > 0


def test_the_index_never_scores_against_the_unfiltered_corpus(tmp_path):
    """Filter-first is the property that killed the ANN idea; it holds here too.

    ``candidate_filter`` hands the lane an arbitrary pre-filtered iterable, and
    BM25's ``n_docs``, ``avgdl`` and document frequencies are all functions of
    THAT set. An index that answered from corpus-wide statistics would return a
    plausible, differently-ranked, wrong answer — so this asserts both halves:
    the filtered scores match the unindexed filtered scores exactly, and they
    genuinely differ from the full-corpus ones.
    """
    graph = _wide_graph()
    index = Bm25Index.for_project(_project(tmp_path, "proj"))
    assert index is not None

    # Warm the index over the WHOLE corpus first, so the filtered query is
    # answered from rows describing documents it must not count.
    for query in _QUERIES:
        _bm25_lane(graph, query, index=index)

    subset = [node for node in graph.nodes if node.id.endswith(("0", "4", "8"))]
    assert 10 < len(subset) < len(graph.nodes)

    changed = False
    for query in _QUERIES:
        filtered_plain = _bm25_lane(graph, query, candidates=subset)
        filtered_index = _bm25_lane(graph, query, index=index, candidates=subset)
        assert filtered_index == filtered_plain, query

        full = dict((node_id, score) for node_id, score, _ in _bm25_lane(graph, query))
        for node_id, score, _ in filtered_plain:
            if node_id in full and full[node_id] != score:
                changed = True
    assert changed, (
        "the filtered corpus scored identically to the full one, so this test "
        "could not have caught an index answering from corpus-wide statistics"
    )


def test_two_documents_with_the_same_text_stay_two_documents(tmp_path):
    """One sidecar row, two documents — document frequency counts documents.

    The twins share a ``text_key`` and therefore a ``doc_id``. Resolving the
    prepared corpus by key rather than by position would collapse them, which
    lowers every shared term's document frequency and silently raises its IDF
    for the whole corpus.
    """
    graph = _wide_graph()
    index = Bm25Index.for_project(_project(tmp_path, "proj"))
    assert index is not None

    query = "corpuscle007 fusion"
    unindexed = _bm25_lane(graph, query)
    indexed = _bm25_lane(graph, query, index=index)
    assert indexed == unindexed

    twins = [entry for entry in indexed if entry[0].startswith("Paper:twin-")]
    assert len(twins) == 2, "both twins must be admitted"

    prepared = index.prepare(
        ["fusion provenance corpuscle007 corpuscle007"] * 2, lambda text: text.split()
    )
    assert prepared is not None
    assert prepared.doc_ids[0] == prepared.doc_ids[1]
    assert len(prepared.doc_ids) == 2, "one row, still two documents"


# --------------------------------------------------------------------------- #
# Invalidation — the place a cache silently lies                               #
# --------------------------------------------------------------------------- #


def test_changed_text_is_reindexed_and_a_relocated_project_is_not(tmp_path):
    """The key is the document TEXT, not the node id and not the path.

    Keying on node id would invert both halves: an edited node would keep
    serving its old postings (wrong scores from a stale index), and an
    unchanged node in a moved or recompiled project would be re-tokenised for
    nothing.
    """
    import shutil

    from tesserae.retrieval.hybrid import _node_text

    graph = _wide_graph(count=40)
    origin = _project(tmp_path, "origin")
    index = Bm25Index.for_project(origin)
    assert index is not None
    _bm25_lane(graph, "graph retrieval sidecar", index=index)
    indexed_after_first = index.stats.indexed
    assert indexed_after_first == len(graph.nodes)

    # Relocating the project: same texts, same keys, nothing re-indexed.
    moved = tmp_path / "moved"
    shutil.copytree(origin, moved)
    moved_index = Bm25Index.for_project(moved)
    assert moved_index is not None
    _bm25_lane(graph, "graph retrieval sidecar", index=moved_index)
    assert moved_index.stats.indexed == 0
    assert moved_index.stats.misses == 0

    # Editing ONE node's description re-indexes exactly that node, and the new
    # scores match a run with no index at all.
    edited_nodes = list(graph.nodes)
    target = edited_nodes[0]
    edited_nodes[0] = ResearchNode(
        id=target.id,
        name=target.name,
        type=target.type,
        description=target.description + " corpuscle999 corpuscle999",
    )
    edited = ResearchGraph(nodes=edited_nodes, edges=[])
    edit_index = Bm25Index.for_project(moved)
    assert edit_index is not None
    got = _bm25_lane(edited, "corpuscle999 graph", index=edit_index)
    assert edit_index.stats.indexed == 1, "only the edited document is re-indexed"
    assert got == _bm25_lane(edited, "corpuscle999 graph")

    # The old row is still there and is simply never asked for again.
    with sqlite3.connect(moved / ".tesserae" / "sqlite.db") as con:
        keys = {row[0] for row in con.execute("select text_key from bm25_docs")}
    assert doc_key(_node_text(target)) in keys
    assert doc_key(_node_text(edited_nodes[0])) in keys


def test_a_shrinking_corpus_leaves_stale_rows_that_cannot_change_a_score(tmp_path):
    """Rows for deleted documents make the sidecar bigger, never wrong.

    Nothing prunes, deliberately — the sidecar is classified a cache and is
    droppable. What makes the accumulation harmless is that a score only ever
    looks a CANDIDATE up by its own key, so a document that left the corpus is
    read out of the docs table and then never matched against anything.
    """
    big = _wide_graph(count=200)
    index = Bm25Index.for_project(_project(tmp_path, "proj"))
    assert index is not None
    for query in _QUERIES:
        _bm25_lane(big, query, index=index)
    indexed_rows = index.count()

    small = ResearchGraph(nodes=list(big.nodes)[:25], edges=[])
    for query in _QUERIES:
        assert _bm25_lane(small, query, index=index) == _bm25_lane(small, query), query
    assert index.count() == indexed_rows, "a smaller corpus indexes nothing new"


def test_a_document_row_never_commits_without_its_postings(tmp_path):
    """Docs and postings commit together, or neither does.

    This is the specific way this cache could lie. A ``bm25_docs`` row visible
    without its postings reads as a document containing no terms: it would
    score 0.0 forever while still inflating ``n_docs`` and ``avgdl`` for
    everybody else. Making the two writes one transaction is what rules it out,
    and this test is what would notice if they were ever split.
    """
    from tesserae.graph_stores.sqlite import SqliteGraphStore

    root = _project(tmp_path, "proj")
    store = SqliteGraphStore(root / ".tesserae" / "sqlite.db")
    calls = {"n": 0}

    class _FailsOnThePostingsWrite:
        """The real connection, except the second ``executemany`` never lands."""

        def __init__(self, con: sqlite3.Connection) -> None:
            self._con = con

        def __enter__(self):
            self._con.__enter__()
            return self

        def __exit__(self, *exc_info):
            return self._con.__exit__(*exc_info)

        def execute(self, *args, **kwargs):
            return self._con.execute(*args, **kwargs)

        def commit(self):
            return self._con.commit()

        def executemany(self, sql, params):
            if "bm25_postings" in sql:
                calls["n"] += 1
                raise sqlite3.OperationalError("disk I/O error")
            return self._con.executemany(sql, params)

    real_connect = store._connect
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store, "_connect", lambda: _FailsOnThePostingsWrite(real_connect()))
        with pytest.raises(sqlite3.OperationalError):
            store.write_bm25_docs_many([("key-a", 3, {"alpha": 1, "beta": 2})])

    assert calls["n"] == 1
    assert store.read_bm25_docs() == {}, (
        "a doc row survived a failed postings write — it would now read as a "
        "document with no terms and score 0.0 forever"
    )


def test_a_termless_document_is_a_row_with_no_postings_and_still_scores_zero(tmp_path):
    """An empty document legitimately has a row and no postings.

    Which is why the presence of the ``bm25_docs`` row, not of postings, is
    what "indexed" means — and why the atomicity above is load-bearing rather
    than paranoid. Both lanes are asked directly here so the equivalence is
    pinned at the arithmetic rather than through a search that might never
    admit the document at all.
    """
    from tesserae.retrieval.hybrid import _bm25_scores, _bm25_scores_indexed

    index = Bm25Index.for_project(_project(tmp_path, "proj"))
    assert index is not None

    corpus = ["", "alpha beta", "alpha alpha gamma"]
    prepared = index.prepare(corpus, lambda text: text.split())
    assert prepared is not None
    assert prepared.doc_lens == (0, 2, 3)

    postings = index.postings(["alpha"])
    assert postings is not None
    assert prepared.doc_ids[0] not in postings["alpha"], "no terms, no postings"

    assert _bm25_scores_indexed(["alpha"], prepared, postings) == _bm25_scores(
        ["alpha"], [text.split() for text in corpus]
    )


# --------------------------------------------------------------------------- #
# Degradation — a broken index costs time, never correctness                   #
# --------------------------------------------------------------------------- #


def test_an_unusable_sidecar_degrades_to_the_in_memory_lane(tmp_path):
    """An unreadable sidecar costs a slow query, never a wrong or failed one."""
    broken = tmp_path / ".tesserae"
    broken.mkdir()
    (broken / "sqlite.db").write_text("this is not a database", encoding="utf-8")

    graph = _wide_graph(count=60)
    index = Bm25Index(broken / "sqlite.db")
    assert _bm25_lane(graph, "graph retrieval sidecar", index=index) == _bm25_lane(
        graph, "graph retrieval sidecar"
    )
    assert index.stats.errors > 0  # fail loud in the counters, not in the caller


def test_the_index_stands_down_where_rank_bm25_would_run(tmp_path, monkeypatch):
    """``rank_bm25``, when importable, replaces the formula the index reproduces.

    So on a machine that has it the index must not serve: its Okapi and
    ``BM25Okapi``'s are different functions, and swapping between them per query
    would make ranking depend on whether an undeclared optional package happens
    to be installed.
    """
    from tesserae.retrieval import hybrid

    graph = _wide_graph(count=60)
    index = Bm25Index.for_project(_project(tmp_path, "proj"))
    assert index is not None

    monkeypatch.setattr(hybrid, "_rank_bm25_available", lambda: True)
    result = hybrid_search(
        graph, "graph retrieval sidecar", top_k=50, mode="bm25",
        bm25_index=index, profile=True,
    )
    assert result.profile is not None
    assert result.profile.bm25_index is False
    assert index.stats.hits == 0 and index.stats.misses == 0
    assert index.count() == 0, "standing down must not write an index either"


def test_no_index_without_a_tesserae_sidecar(tmp_path):
    """No ``.tesserae/`` means no index — a read must not create one as a side effect."""
    assert Bm25Index.for_project(None) is None
    assert Bm25Index.for_project(tmp_path) is None
    assert Bm25Index.for_graph_path(tmp_path / "graph.json") is None
    assert not (tmp_path / ".tesserae").exists()

    root = _project(tmp_path, "proj")
    assert Bm25Index.for_graph_path(root / ".tesserae" / "graph.json") is not None


# --------------------------------------------------------------------------- #
# Observability and artifact bytes                                             #
# --------------------------------------------------------------------------- #


def test_the_profile_tells_a_cold_index_from_a_warm_one(tmp_path):
    """A silently-cold index must not read as a fast path (roadmap step 9).

    ``bm25_index`` says whether the lane was served at all; the hit and miss
    counters say how much of the corpus it had to build. Without both, the most
    expensive possible query — one that indexes the whole corpus — is
    indistinguishable in the profile from the cheapest.
    """
    graph = _wide_graph(count=80)
    index = Bm25Index.for_project(_project(tmp_path, "proj"))
    assert index is not None
    total = len(graph.nodes)

    cold = hybrid_search(
        graph, "graph retrieval sidecar", top_k=20, mode="bm25",
        bm25_index=index, profile=True,
    ).profile
    warm = hybrid_search(
        graph, "graph retrieval sidecar", top_k=20, mode="bm25",
        bm25_index=index, profile=True,
    ).profile
    none_at_all = hybrid_search(
        graph, "graph retrieval sidecar", top_k=20, mode="bm25", profile=True
    ).profile

    assert cold is not None and warm is not None and none_at_all is not None
    assert cold.bm25_index is True and warm.bm25_index is True
    assert none_at_all.bm25_index is False

    # A cold index is the WORST case, not the best: every document is a miss
    # and has to be tokenised and written before the query can be answered.
    assert cold.lanes["bm25"].cache_misses == total
    assert cold.lanes["bm25"].cache_hits == 0
    assert warm.lanes["bm25"].cache_misses == 0
    assert warm.lanes["bm25"].cache_hits == total
    assert none_at_all.lanes["bm25"].cache_hits == 0
    assert none_at_all.lanes["bm25"].cache_misses == 0
    # BM25 makes no model calls, whatever the index did.
    assert cold.lanes["bm25"].embed_calls == 0
    assert cold.to_dict()["bm25_index"] is True


def test_searching_writes_the_index_and_leaves_graph_json_byte_identical(tmp_path):
    """The index is SQLite-only. ``graph.json`` must not move a byte.

    Named explicitly rather than left to the idempotence suites: they compare
    two compiles, and an index written by a READ between them is exactly the
    kind of state those suites were structurally unable to see the last four
    times a wall clock leaked into an artifact.
    """
    root = _project(tmp_path, "proj")
    graph = _wide_graph(count=60)
    graph_path = root / ".tesserae" / "graph.json"
    graph_path.write_text(graph.to_json(indent=2), encoding="utf-8")
    before = hashlib.sha256(graph_path.read_bytes()).hexdigest()

    index = Bm25Index.for_graph_path(graph_path)
    assert index is not None
    for query in _QUERIES:
        _bm25_lane(graph, query, index=index)

    assert index.count() > 0, "the index must actually have been written"
    assert hashlib.sha256(graph_path.read_bytes()).hexdigest() == before

    # And nothing about the index reached node metadata on the way past.
    import json

    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    for node in payload["nodes"]:
        metadata = node.get("metadata") or {}
        for banned in ("doc_id", "doc_len", "text_key", "bm25_docs", "bm25_postings"):
            assert banned not in metadata

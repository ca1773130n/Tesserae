"""Tests for :class:`SqliteGraphStore`.

Verifies the SQLite ``GraphStore`` adapter that wraps the existing local
SQLite schema (shared with :class:`SQLiteResearchGraphStore`) behind the
``GraphStore`` protocol shape used by the hexagonal pipeline.

The store ignores ``owner_user_id`` because the standalone SQLite mode has
no notion of users — that scoping only matters for the multi-tenant
Postgres adapter introduced in Phase 1b.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tesserae.graph_stores import SqliteGraphStore
from tesserae.persistence import SQLiteResearchGraphStore
from tesserae.ports import GraphStore
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def _make_node(
    node_id: str,
    name: str,
    node_type: ResearchNodeType = ResearchNodeType.CONCEPT,
    description: str = "",
) -> ResearchNode:
    return ResearchNode(id=node_id, name=name, type=node_type, description=description)


def test_upsert_node_inserts_then_updates(tmp_path: Path) -> None:
    """Calling ``upsert_node`` twice with the same id should replace the row."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    node = _make_node("c:diffusion", "Diffusion", description="first version")
    returned_id = store.upsert_node(node)
    assert returned_id == "c:diffusion"

    updated = _make_node("c:diffusion", "Diffusion", description="second version")
    store.upsert_node(updated)

    fetched = store.get_node("c:diffusion")
    assert fetched is not None
    assert fetched.description == "second version"


def test_get_node_returns_inserted_node(tmp_path: Path) -> None:
    """``get_node`` round-trips the values written via ``upsert_node``."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    node = ResearchNode(
        id="p:0001",
        name="Sample Paper",
        type=ResearchNodeType.PAPER,
        aliases=["alt name"],
        description="abstract",
        source_path="papers/sample.md",
        metadata={"arxiv_id": "0001.0001"},
    )
    store.upsert_node(node)

    fetched = store.get_node("p:0001")
    assert fetched is not None
    assert fetched.name == "Sample Paper"
    assert fetched.type == ResearchNodeType.PAPER
    assert fetched.aliases == ["alt name"]
    assert fetched.metadata == {"arxiv_id": "0001.0001"}
    assert fetched.source_path == "papers/sample.md"


def test_get_node_missing_returns_none(tmp_path: Path) -> None:
    """``get_node`` on an unknown id returns ``None`` rather than raising."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    assert store.get_node("does-not-exist") is None


def test_iterate_nodes_filters_by_type(tmp_path: Path) -> None:
    """``iterate_nodes(node_type=...)`` should yield only matching rows."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    store.upsert_node(_make_node("p:1", "Paper One", node_type=ResearchNodeType.PAPER))
    store.upsert_node(_make_node("c:1", "Concept One", node_type=ResearchNodeType.CONCEPT))
    store.upsert_node(_make_node("c:2", "Concept Two", node_type=ResearchNodeType.CONCEPT))

    papers = list(store.iterate_nodes(node_type=ResearchNodeType.PAPER.value))
    assert len(papers) == 1
    assert papers[0].id == "p:1"

    concepts = list(store.iterate_nodes(node_type=ResearchNodeType.CONCEPT.value))
    assert len(concepts) == 2

    everything = list(store.iterate_nodes())
    assert len(everything) == 3


def test_query_subgraph_returns_seeds_at_depth_zero(tmp_path: Path) -> None:
    """At depth 0 the result is just the seed nodes with no edges."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    store.upsert_node(_make_node("a", "A"))
    store.upsert_node(_make_node("b", "B"))
    store.upsert_edge(ResearchEdge(source="a", target="b", type="uses"))

    sub = store.query_subgraph(["a"], depth=0)
    ids = {n.id for n in sub.nodes}
    assert ids == {"a"}
    assert sub.edges == []


def test_query_subgraph_expands_to_depth_one(tmp_path: Path) -> None:
    """Depth 1 follows edges in either direction from the seed set."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    store.upsert_node(_make_node("a", "A"))
    store.upsert_node(_make_node("b", "B"))
    store.upsert_node(_make_node("c", "C"))
    store.upsert_edge(ResearchEdge(source="a", target="b", type="uses"))
    store.upsert_edge(ResearchEdge(source="c", target="a", type="extends"))

    sub = store.query_subgraph(["a"], depth=1)
    ids = {n.id for n in sub.nodes}
    assert ids == {"a", "b", "c"}
    edge_keys = {(e.source, e.target, e.type) for e in sub.edges}
    assert ("a", "b", "uses") in edge_keys
    assert ("c", "a", "extends") in edge_keys


def test_find_canonical_case_insensitive(tmp_path: Path) -> None:
    """``find_canonical`` matches names regardless of case."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    store.upsert_node(
        _make_node("c:ld", "Latent Diffusion", node_type=ResearchNodeType.CONCEPT)
    )

    found = store.find_canonical("latent diffusion", ResearchNodeType.CONCEPT.value)
    assert found is not None
    assert found.id == "c:ld"

    # Wrong type returns None even with matching name.
    other_type = store.find_canonical("latent diffusion", ResearchNodeType.PAPER.value)
    assert other_type is None


def test_sqlite_graph_store_is_runtime_checkable_graph_store(tmp_path: Path) -> None:
    """The adapter must satisfy the ``GraphStore`` runtime-checkable protocol."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    assert isinstance(store, GraphStore)


def test_upsert_edge_dedupes_on_source_target_type(tmp_path: Path) -> None:
    """Two edges with same ``(source, target, type)`` must collapse to one row.

    The deterministic edge id derived from the triple makes ``insert or
    replace`` on ``id`` equivalent to deduping on ``(source, target, type)``.
    Calling ``upsert_edge`` twice with different metadata must leave a single
    edge in the resulting subgraph.
    """
    store = SqliteGraphStore(tmp_path / "g.db")
    store.upsert_node(ResearchNode(id="A", name="A", type=ResearchNodeType.PAPER))
    store.upsert_node(ResearchNode(id="B", name="B", type=ResearchNodeType.PAPER))
    e1 = ResearchEdge(source="A", target="B", type="extends", metadata={"v": 1})
    e2 = ResearchEdge(source="A", target="B", type="extends", metadata={"v": 2})
    store.upsert_edge(e1)
    store.upsert_edge(e2)

    sub = store.query_subgraph(["A"], depth=1)
    matches = [
        e for e in sub.edges if (e.source, e.target, e.type) == ("A", "B", "extends")
    ]
    assert len(matches) == 1
    # Latest write wins — metadata reflects the second upsert.
    assert matches[0].metadata == {"v": 2}


def test_sqlite_graph_store_shares_schema_with_legacy_store(tmp_path: Path) -> None:
    """A graph written by :class:`SQLiteResearchGraphStore` is readable via the new adapter.

    Pins the headline schema-compatibility claim: both classes operate on
    the same on-disk file, so a node persisted by the legacy batch writer
    must be retrievable through the new row-at-a-time adapter.
    """
    db = tmp_path / "shared.db"
    legacy = SQLiteResearchGraphStore(db)
    legacy.write_graph(
        ResearchGraph(
            nodes=[
                ResearchNode(
                    id="A",
                    name="LegacyNode",
                    type=ResearchNodeType.PAPER,
                )
            ],
            edges=[],
        )
    )

    store = SqliteGraphStore(db)
    node = store.get_node("A")
    assert node is not None
    assert node.name == "LegacyNode"
    assert node.type == ResearchNodeType.PAPER


# --------------------------------------------------------------------------- #
# node_provenance sidecar + delete semantics (CMP-02)                          #
# --------------------------------------------------------------------------- #


def test_delete_nodes_by_source_keeps_cross_file_node(tmp_path: Path) -> None:
    """A node owned by two source files survives deletion of just one source.

    This is the 2400->1700 anti-collapse guarantee at the unit level: a
    cross-file concept node referenced by an unchanged file must NOT be
    tombstoned when one of its sources is re-extracted.
    """
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    store.upsert_node(_make_node("c:attention", "Attention"))
    store.record_provenance("c:attention", "a.md", timestamp="2026-01-01T00:00:00Z")
    store.record_provenance("c:attention", "b.md", timestamp="2026-01-01T00:00:00Z")

    deleted = store.delete_nodes_by_source({"a.md"})

    assert deleted == set()
    assert store.get_node("c:attention") is not None


def test_delete_nodes_by_source_removes_orphaned_node(tmp_path: Path) -> None:
    """A node whose only source is deleted is tombstoned and returned."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    store.upsert_node(_make_node("c:solo", "Solo"))
    store.record_provenance("c:solo", "a.md", timestamp="2026-01-01T00:00:00Z")

    deleted = store.delete_nodes_by_source({"a.md"})

    assert deleted == {"c:solo"}
    assert store.get_node("c:solo") is None


def test_record_provenance_is_deterministic_first_seen(tmp_path: Path) -> None:
    """Re-recording preserves ``first_seen_at`` and advances ``last_updated_at``."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    store.record_provenance("n", "f", timestamp="T1")
    store.record_provenance("n", "f", timestamp="T2")

    with sqlite3.connect(store.path) as con:
        first_seen, last_updated = con.execute(
            "select first_seen_at, last_updated_at from node_provenance"
            " where node_id = ? and source_path = ?",
            ("n", "f"),
        ).fetchone()
    assert first_seen == "T1"
    assert last_updated == "T2"


def test_delete_nodes_by_source_empty_set_is_noop(tmp_path: Path) -> None:
    """An empty changed-source set returns ``set()`` and raises nothing."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    assert store.delete_nodes_by_source(set()) == set()


def test_store_still_satisfies_protocol(tmp_path: Path) -> None:
    """The extended adapter still satisfies the runtime-checkable protocol."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    assert isinstance(store, GraphStore)


# ---------------------------------------------------------------------------
# node_vectors sidecar (embedding cache)
# ---------------------------------------------------------------------------


def test_node_vectors_round_trip_is_exact(tmp_path: Path) -> None:
    """A cached vector must decode to the EXACT floats that were stored.

    The cache is only allowed to change what retrieval costs, never what it
    returns — a lossy encoding (float32, rounded JSON) would make a warm cache
    score differently from a cold one, which is a wrong answer, not a slow one.
    """
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    vector = [0.1, -1.0 / 3.0, 2.718281828459045, 0.0, 1e-17]
    store.write_node_vectors_many("model:x", len(vector), [("abc123", vector)])

    got = store.read_node_vectors("model:x", len(vector), ["abc123"])
    assert got["abc123"] == vector

    # The blob view the vectorised embedding lane reads is the SAME row, so a
    # matrix built from it can never disagree with the decoded floats.
    blobs = store.read_node_vector_blobs("model:x", len(vector), ["abc123"])
    assert len(blobs["abc123"]) == len(vector) * 8
    store.write_node_vector_blobs_many("model:y", len(vector), blobs.items())
    assert store.read_node_vectors("model:y", len(vector), ["abc123"])["abc123"] == vector


def test_node_vectors_never_cross_backend_keys(tmp_path: Path) -> None:
    """Two backends' vectors live in different spaces and must not be shared."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    store.write_node_vectors_many("model:a", 3, [("hash1", [1.0, 0.0, 0.0])])

    assert store.read_node_vectors("model:b", 3, ["hash1"]) == {}
    assert store.read_node_vectors("model:a", 4, ["hash1"]) == {}
    assert store.read_node_vectors("model:a", 3, ["hash1"]) == {"hash1": [1.0, 0.0, 0.0]}
    assert store.count_node_vectors("model:a", 3) == 1
    assert store.count_node_vectors("model:b", 3) == 0


def test_node_vectors_rewrite_keeps_first_value(tmp_path: Path) -> None:
    """The row is a pure function of its key, so a re-insert is a no-op.

    ``insert or ignore`` is what makes two concurrent writers safe: they wrote
    the same bytes, so there is nothing to reconcile.
    """
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    store.write_node_vectors_many("model:a", 2, [("k", [1.0, 2.0])])
    store.write_node_vectors_many("model:a", 2, [("k", [1.0, 2.0])])

    assert store.count_node_vectors("model:a", 2) == 1


def test_node_vectors_read_handles_more_than_one_chunk(tmp_path: Path) -> None:
    """A corpus-sized hash list must not blow SQLite's bound-parameter limit."""
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    rows = [(f"hash-{i:04d}", [float(i), 0.5]) for i in range(1200)]
    store.write_node_vectors_many("model:a", 2, rows)

    got = store.read_node_vectors("model:a", 2, [key for key, _ in rows])
    assert len(got) == 1200
    assert got["hash-1199"] == [1199.0, 0.5]



def test_node_vectors_read_does_not_depend_on_requested_order(tmp_path: Path) -> None:
    """The read sorts its hashes, and that must be invisible in the answer.

    Sorted lookups walk the ``(backend_name, backend_dim, text_sha256)``
    primary key forwards instead of seeking around it: 241 ms unsorted against
    179 ms sorted on this project's own 65,190-row sidecar, warm. Sorting is
    only allowed to change what that read COSTS, so the mapping has to be
    identical whichever order it was asked in — including for a batch that
    spans several bound-parameter chunks, where sorting moves keys between
    chunks rather than only within one.
    """
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    rows = [(f"hash-{i:04d}", [float(i), 0.5]) for i in range(1200)]
    store.write_node_vectors_many("model:a", 2, rows)

    keys = [key for key, _ in rows]
    shuffled = keys[7::13] + keys[:7] + keys[8::13]
    forwards = store.read_node_vector_blobs("model:a", 2, keys)
    backwards = store.read_node_vector_blobs("model:a", 2, list(reversed(keys)))
    scattered = store.read_node_vector_blobs("model:a", 2, shuffled)

    assert len(forwards) == 1200
    assert forwards == backwards
    assert {k: forwards[k] for k in scattered} == scattered
    assert store.read_node_vectors("model:a", 2, reversed(keys))["hash-1199"] == [
        1199.0,
        0.5,
    ]


def test_node_vectors_read_ignores_duplicate_and_empty_hashes(tmp_path: Path) -> None:
    """Deduplication survived the switch from ``dict.fromkeys`` to a set.

    ``dict.fromkeys`` was doing two jobs — dedupe and preserve order — and only
    one of them was load-bearing. Dropping the wrong one would send duplicate
    bound parameters into a chunk and silently shrink the batch.
    """
    store = SqliteGraphStore(tmp_path / "graph.sqlite")
    store.write_node_vectors_many("model:a", 2, [("k1", [1.0, 2.0]), ("k2", [3.0, 4.0])])

    got = store.read_node_vector_blobs("model:a", 2, ["k2", "k1", "k2", "", "k1"])
    assert set(got) == {"k1", "k2"}


# --------------------------------------------------------------------------- #
# Keyed-read surface: graph-ordered subgraphs, the suppression probe, and the  #
# name index. These exist so a read can be served BY KEY instead of by loading #
# the whole graph, which means each one has to reproduce what the in-memory    #
# read path would have computed — not merely "something similar".              #
# --------------------------------------------------------------------------- #


def _seed_ordered_store(path: Path) -> tuple[SqliteGraphStore, ResearchGraph]:
    """A store seeded in an order that is DELIBERATELY not the canonical one.

    ``graph_order`` promises ``graph.json`` order, and ``graph.json`` is
    written from a canonicalized graph. If the promise were implemented as
    "insertion order" it would still pass on a fixture inserted canonically —
    so the fixture inserts scrambled, and the tests assert the canonical
    answer. Written through ``write_graph(replace=True)``, the same
    truncate-and-reinsert a compile performs.
    """
    graph = ResearchGraph(
        nodes=[_make_node(f"n{i}", f"Node {i}") for i in (3, 0, 5, 1, 4, 2)],
        edges=[
            ResearchEdge(source="n4", target="n0", type="extends"),
            ResearchEdge(source="n0", target="n3", type="uses"),
            ResearchEdge(source="n1", target="n5", type="uses"),
            ResearchEdge(source="n0", target="n1", type="uses"),
            ResearchEdge(source="n2", target="n0", type="extends"),
        ],
    )
    SQLiteResearchGraphStore(path).write_graph(graph, replace=True)
    return SqliteGraphStore(path), graph


def test_query_subgraph_graph_order_matches_graph_json_order(tmp_path: Path) -> None:
    """``graph_order=True`` returns what ``graph.json`` holds, in file order.

    A read surface that scans ``graph.edges`` and slices the first N —
    ``node_context`` does exactly that — gets a DIFFERENT N if the store hands
    back edges in whatever order the query planner produced them. The expected
    order is taken from ``canonicalized()`` rather than restated, because that
    is the single call ``ProjectWiki._publish`` makes before writing every
    artifact: if its sort key ever changes, this test changes with it instead
    of quietly disagreeing.
    """
    store, graph = _seed_ordered_store(tmp_path / "graph.sqlite")
    published = graph.canonicalized()

    sub = store.query_subgraph(["n0"], depth=1, graph_order=True)
    expected = [
        (e.source, e.type, e.target)
        for e in published.edges
        if "n0" in (e.source, e.target)
    ]
    assert [(e.source, e.type, e.target) for e in sub.edges] == expected
    assert [n.id for n in sub.nodes] == ["n0", "n1", "n2", "n3", "n4"]
    # Not vacuous: the store was seeded in a different order than it answers in.
    assert [(e.source, e.type, e.target) for e in graph.edges] != [
        (e.source, e.type, e.target) for e in published.edges
    ]


def test_query_subgraph_graph_order_ignores_seed_list_position(tmp_path: Path) -> None:
    """The same seed SET returns the same graph however the list is arranged.

    Seed-position dependence is the specific defect an earlier walk/seed fusion
    shipped and was reverted for, so it gets an explicit test rather than being
    left to follow from the sort.
    """
    store, _graph = _seed_ordered_store(tmp_path / "graph.sqlite")
    seeds = ["n0", "n1", "n4"]

    forward = store.query_subgraph(seeds, depth=1, graph_order=True)
    reversed_ = store.query_subgraph(list(reversed(seeds)), depth=1, graph_order=True)
    duplicated = store.query_subgraph(seeds + seeds, depth=1, graph_order=True)

    def shape(g: ResearchGraph):
        return (
            [n.id for n in g.nodes],
            [(e.source, e.type, e.target) for e in g.edges],
        )

    assert shape(forward) == shape(reversed_) == shape(duplicated)


def test_query_subgraph_default_still_returns_the_same_content(tmp_path: Path) -> None:
    """The default (no ``graph_order``) keeps its historical contract.

    ``_materialize_graph`` publishes ``subgraph.edges`` AS the graph's edge
    list, so the flag must be strictly additive: same nodes, same edges, only
    the ordering guarantee is new.
    """
    store, _graph = _seed_ordered_store(tmp_path / "graph.sqlite")

    default = store.query_subgraph(["n0"], depth=1)
    ordered = store.query_subgraph(["n0"], depth=1, graph_order=True)

    assert {n.id for n in default.nodes} == {n.id for n in ordered.nodes}
    assert {(e.source, e.type, e.target) for e in default.edges} == {
        (e.source, e.type, e.target) for e in ordered.edges
    }


def test_read_suppression_edges_finds_the_edge_a_second_hop_would_cost(
    tmp_path: Path,
) -> None:
    """The probe returns suppression edges that hang off the NEIGHBOUR.

    This is the whole reason the probe exists. ``n1`` is superseded by ``n9``,
    and that edge is incident to ``n1``, not to the focal ``n0`` — so a depth-1
    neighbourhood cannot see it and would serve a superseded node as current
    knowledge. Reaching it by BFS means a second hop, which on the real graph's
    largest hub is a tenth of the corpus.
    """
    graph = ResearchGraph(
        nodes=[_make_node(f"n{i}", f"Node {i}") for i in (0, 1, 2, 9)],
        edges=[
            ResearchEdge(source="n0", target="n1", type="uses"),
            ResearchEdge(source="n0", target="n2", type="uses"),
            ResearchEdge(source="n9", target="n1", type="supersedes"),
        ],
    )
    SQLiteResearchGraphStore(tmp_path / "graph.sqlite").write_graph(graph, replace=True)
    store = SqliteGraphStore(tmp_path / "graph.sqlite")

    depth_one = store.query_subgraph(["n0"], depth=1, graph_order=True)
    assert ("n9", "supersedes", "n1") not in {
        (e.source, e.type, e.target) for e in depth_one.edges
    }

    probed = store.read_suppression_edges({n.id for n in depth_one.nodes})
    assert [(e.source, e.type, e.target) for e in probed] == [("n9", "supersedes", "n1")]


def test_read_suppression_edges_covers_every_suppression_type(tmp_path: Path) -> None:
    """Every type in ``SUPPRESSION_EDGE_TYPES``, and nothing else.

    The probe decides which edges get FETCHED for ``suppressed_ids`` to read.
    If the two sets ever disagree the filter silently stops filtering, so the
    test asserts against the shared constant rather than a hand-written list.
    """
    from tesserae.graph_filters import SUPPRESSION_EDGE_TYPES

    nodes = [_make_node(f"n{i}", f"Node {i}") for i in range(2 * len(SUPPRESSION_EDGE_TYPES) + 2)]
    edges = [ResearchEdge(source="n0", target="n1", type="uses")]
    for i, edge_type in enumerate(sorted(SUPPRESSION_EDGE_TYPES)):
        edges.append(
            ResearchEdge(source=f"n{2 * i}", target=f"n{2 * i + 1}", type=edge_type)
        )
    graph = ResearchGraph(nodes=nodes, edges=edges)
    SQLiteResearchGraphStore(tmp_path / "graph.sqlite").write_graph(graph, replace=True)
    store = SqliteGraphStore(tmp_path / "graph.sqlite")

    found = store.read_suppression_edges([n.id for n in nodes])
    assert {e.type for e in found} == set(SUPPRESSION_EDGE_TYPES)
    assert "uses" not in {e.type for e in found}


def test_read_suppression_edges_is_order_free_and_ignores_empty_ids(
    tmp_path: Path,
) -> None:
    """Same id SET, same answer — across chunk boundaries and duplicates.

    The id list is chunked into bound-parameter batches, so an id's POSITION
    decides which batch it lands in. If the result depended on that, a caller
    passing its ids in a different order would get a different suppression set.
    """
    count = 1200  # spans several ``_SUPPRESSION_QUERY_CHUNK`` batches
    nodes = [_make_node(f"n{i:04d}", f"Node {i}") for i in range(count)]
    edges = [
        ResearchEdge(source=f"n{i:04d}", target=f"n{i + 1:04d}", type="supersedes")
        for i in range(0, count - 1, 200)
    ]
    graph = ResearchGraph(nodes=nodes, edges=edges)
    SQLiteResearchGraphStore(tmp_path / "graph.sqlite").write_graph(graph, replace=True)
    store = SqliteGraphStore(tmp_path / "graph.sqlite")

    ids = [n.id for n in nodes]
    forward = store.read_suppression_edges(ids)
    backward = store.read_suppression_edges(list(reversed(ids)))
    noisy = store.read_suppression_edges(ids + ids + ["", None])  # type: ignore[list-item]

    def shape(found):
        return [(e.source, e.type, e.target) for e in found]

    assert shape(forward) == shape(backward) == shape(noisy)
    assert len(forward) == len(edges)


def test_read_name_index_is_graph_ordered_and_keeps_non_ascii_case(
    tmp_path: Path,
) -> None:
    """Graph order, and the fold left to Python.

    SQLite's ``lower()`` is ASCII-only without ICU, so folding in SQL would
    disagree with the in-memory ``name.casefold()`` index on exactly the names
    a research corpus is full of. The store returns raw names and lets the
    caller fold; ordering is rowid so a last-wins index picks the same node the
    in-memory dict comprehension would.
    """
    graph = ResearchGraph(
        nodes=[
            _make_node("n3", "duplicate"),
            _make_node("n1", "STRASSE"),
            _make_node("n0", "Édouard"),
            _make_node("n2", "Duplicate"),
        ],
        edges=[],
    ).canonicalized()
    SQLiteResearchGraphStore(tmp_path / "graph.sqlite").write_graph(graph, replace=True)
    store = SqliteGraphStore(tmp_path / "graph.sqlite")

    pairs = store.read_name_index()
    assert pairs == [
        ("n0", "Édouard"),
        ("n1", "STRASSE"),
        ("n2", "Duplicate"),
        ("n3", "duplicate"),
    ]

    keyed = {name.casefold(): node_id for node_id, name in pairs}
    in_memory = {n.name.casefold(): n.id for n in graph.nodes}
    assert keyed == in_memory
    assert keyed["édouard"] == "n0"  # a SQL-side lower() would have missed this
    assert keyed["duplicate"] == "n3"  # last wins, exactly like the comprehension

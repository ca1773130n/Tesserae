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

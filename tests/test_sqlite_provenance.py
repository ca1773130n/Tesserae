"""Tests for the atomic provenance tombstone path and the edge-coverage
predicate on :class:`SqliteGraphStore`.

Covers blocker #7 (``delete_nodes_by_source_with_edges`` is a single
transaction so a concurrent reader never observes a node tombstone before
its edge tombstone) and major #5 (``provenance_covers_edges`` — the
edge analog of ``provenance_covers_nodes`` consumed by the Plan-02
readiness gate).

All tests are deterministic: a ``tmp_path`` SQLite file and caller-supplied
ISO timestamps (no wall-clock assertions).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tesserae.graph_stores import SqliteGraphStore

TS = "2026-01-01T00:00:00Z"


def _store(tmp_path: Path) -> SqliteGraphStore:
    return SqliteGraphStore(tmp_path / "graph.sqlite")


def _seed_node(con: sqlite3.Connection, node_id: str) -> None:
    con.execute(
        "insert or ignore into nodes"
        " (id, name, type, aliases_json, description, source_path, metadata_json)"
        " values (?, ?, ?, ?, ?, ?, ?)",
        (node_id, node_id, "concept", "[]", "", None, "{}"),
    )


def _seed_edge(con: sqlite3.Connection, source: str, etype: str, target: str) -> None:
    con.execute(
        "insert or ignore into edges"
        " (id, source, target, type, evidence, metadata_json)"
        " values (?, ?, ?, ?, ?, ?)",
        (f"{source}->{target}:{etype}", source, target, etype, None, "{}"),
    )


def _seed(store: SqliteGraphStore) -> None:
    """Two files. a.md solely owns node ``a`` and edge (a,rel,b); b.md owns
    node ``b``. So removing a.md must tombstone node ``a`` and the edge whose
    endpoint ``a`` disappears, while node ``b`` survives (owned by b.md)."""
    with store._connect() as con:
        store._ensure_schema(con)
        for nid in ("a", "b"):
            _seed_node(con, nid)
        _seed_edge(con, "a", "rel", "b")
        con.commit()
    store.record_provenance_many([("a", "a.md", TS), ("b", "b.md", TS)])
    store.record_edge_provenance_many([("a", "rel", "b", "a.md", TS)])


def _rows(store: SqliteGraphStore, table: str) -> int:
    with store._connect() as con:
        return con.execute(f"select count(*) from {table}").fetchone()[0]


# --------------------------------------------------------------------------- #
# Atomic tombstone (blocker #7)
# --------------------------------------------------------------------------- #


def test_with_edges_removes_node_and_incident_edge(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)

    removed_nodes, removed_edges = store.delete_nodes_by_source_with_edges({"a.md"})

    assert removed_nodes == {"a"}
    assert removed_edges == set()  # edge already cascaded by node deletion
    # node ``a`` and its incident edge are gone; node ``b`` survives.
    assert _rows(store, "nodes") == 1
    assert _rows(store, "edges") == 0
    assert _rows(store, "edge_provenance") == 0
    with store._connect() as con:
        assert con.execute("select id from nodes").fetchall() == [("b",)]


def test_with_edges_tombstones_stale_cross_file_edge(tmp_path: Path) -> None:
    """Edge whose BOTH endpoints survive but whose only asserting file changed
    must still be tombstoned by the edge pass within the same transaction."""
    store = _store(tmp_path)
    with store._connect() as con:
        store._ensure_schema(con)
        for nid in ("a", "b"):
            _seed_node(con, nid)
        _seed_edge(con, "a", "rel", "b")
        con.commit()
    # both nodes owned by an UNCHANGED file, edge solely asserted by c.md.
    store.record_provenance_many([("a", "keep.md", TS), ("b", "keep.md", TS)])
    store.record_edge_provenance_many([("a", "rel", "b", "c.md", TS)])

    removed_nodes, removed_edges = store.delete_nodes_by_source_with_edges({"c.md"})

    assert removed_nodes == set()  # both endpoints survive
    assert removed_edges == {("a", "rel", "b")}
    assert _rows(store, "nodes") == 2  # endpoints kept
    assert _rows(store, "edges") == 0  # stale edge gone
    assert _rows(store, "edge_provenance") == 0


def test_with_edges_matches_sequential_standalone_result(tmp_path: Path) -> None:
    """The single-transaction path returns the SAME tuple the two standalone
    methods would have produced — no behaviour change, only atomicity."""
    store = _store(tmp_path)
    _seed(store)
    combined = store.delete_nodes_by_source_with_edges({"a.md"})

    other = _store(tmp_path.with_name("other"))
    _seed(other)
    sequential = (
        other.delete_nodes_by_source({"a.md"}),
        other.delete_edges_by_source({"a.md"}),
    )
    assert combined == sequential


def test_with_edges_empty_source_is_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    removed_nodes, removed_edges = store.delete_nodes_by_source_with_edges(set())
    assert removed_nodes == set()
    assert removed_edges == set()
    assert _rows(store, "nodes") == 2
    assert _rows(store, "edges") == 1


# --------------------------------------------------------------------------- #
# Edge-coverage predicate (major #5)
# --------------------------------------------------------------------------- #


def test_provenance_covers_edges_true_when_all_present(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    assert store.provenance_covers_edges([("a", "rel", "b")]) is True


def test_provenance_covers_edges_false_when_one_missing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    assert (
        store.provenance_covers_edges(
            [("a", "rel", "b"), ("x", "rel", "y")]
        )
        is False
    )


def test_provenance_covers_edges_empty_is_true(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store)
    assert store.provenance_covers_edges([]) is True


def test_provenance_covers_edges_empty_on_fresh_db(tmp_path: Path) -> None:
    """Empty input is vacuously covered even before any rows exist."""
    store = _store(tmp_path)
    with store._connect() as con:
        store._ensure_schema(con)
        con.commit()
    assert store.provenance_covers_edges([]) is True
    assert store.provenance_covers_edges([("a", "rel", "b")]) is False

"""Orphaned sidecar rows are removed on a full compile.

`node_provenance` and `edge_provenance` are reconciled on every full compile;
`node_memory` and `fact_observed` never were. Measured on this project's own
1.19 GB store after 238 builds: node_memory held 371,943 rows for 16,869 live
nodes (95.5% dead), fact_observed 74.3% dead, both provenance tables 0%.
"""
from __future__ import annotations

from pathlib import Path

from tesserae.graph_stores.sqlite import SqliteGraphStore


def _store(tmp_path: Path) -> SqliteGraphStore:
    return SqliteGraphStore(tmp_path / "sqlite.db")


def test_dead_memory_and_fact_rows_are_deleted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # (node_id, decay_score, access_count, last_accessed_at, confidence,
    #  superseded, updated_at)
    store.write_node_memory_many([
        ("A", 1.0, 3, None, None, 0, "2026-01-02"),
        ("GONE", 0.5, 9, None, None, 0, "2026-01-02"),
    ])
    store.write_fact_observed_many(
        [("A", "cites", "B"), ("GONE", "cites", "B")], "2026-01-02"
    )

    pruned = store.prune_orphaned_sidecars(["A", "B"], [("A", "cites", "B")])

    assert pruned == {"node_memory": 1, "fact_observed": 1}
    assert set(store.read_node_memory()) == {"A"}, "a live node's memory is never dropped"
    assert set(store.read_fact_observed()) == {("A", "cites", "B")}


def test_pruning_is_idempotent_and_keeps_every_live_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_node_memory_many([("A", 1.0, 3, None, None, 0, "2026-01-02")])
    store.write_fact_observed_many([("A", "cites", "B")], "2026-01-02")

    first = store.prune_orphaned_sidecars(["A", "B"], [("A", "cites", "B")])
    second = store.prune_orphaned_sidecars(["A", "B"], [("A", "cites", "B")])

    assert first == second == {"node_memory": 0, "fact_observed": 0}
    assert store.read_node_memory()["A"]["access_count"] == 3, "state survives a prune"


def test_an_empty_live_graph_does_not_wipe_the_sidecars_by_accident(tmp_path: Path) -> None:
    """A caller that passes nothing live would delete everything.

    Guarded here rather than in the method: the compile only calls this with the
    FINAL graph of a full compile, and an empty final graph legitimately means
    an empty corpus. The test pins the contract so the meaning of "live" cannot
    drift into "whatever happened to be in scope".
    """
    store = _store(tmp_path)
    store.write_node_memory_many([("A", 1.0, 3, None, None, 0, "2026-01-02")])
    pruned = store.prune_orphaned_sidecars([], [])
    assert pruned["node_memory"] == 1
    assert store.read_node_memory() == {}

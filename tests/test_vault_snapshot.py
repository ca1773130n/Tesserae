"""Round-trip + edge-case tests for the vault snapshot store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_wiki.research_graph import ResearchNode, ResearchNodeType
from llm_wiki.vault_snapshot import (
    SNAPSHOT_VERSION,
    NodeSnapshot,
    read_snapshot,
    write_snapshot,
)


def _make_node(node_id: str, **overrides) -> ResearchNode:
    defaults = dict(
        id=node_id,
        name="Test",
        type=ResearchNodeType.CONCEPT,
        aliases=[],
        description="",
        source_path=None,
        metadata={},
    )
    defaults.update(overrides)
    return ResearchNode(**defaults)


def test_round_trip_preserves_name_aliases_description_and_scalar_metadata(tmp_path: Path) -> None:
    node = _make_node(
        "Concept:foo",
        name="Foo",
        aliases=["F"],
        description="A foo is a foo.",
        metadata={"author": "neo", "year": 2026, "scalar_bool": True, "drop_me_list": [1, 2]},
    )
    snap_path = tmp_path / "vault_snapshot.json"
    write_snapshot([node], snap_path)

    loaded = read_snapshot(snap_path)
    assert loaded is not None
    assert set(loaded.keys()) == {"Concept:foo"}
    entry = loaded["Concept:foo"]
    assert entry.name == "Foo"
    assert entry.aliases == ("F",)
    assert entry.description == "A foo is a foo."
    # Scalars survive, non-scalars are dropped on the way out.
    assert entry.metadata == {"author": "neo", "year": 2026, "scalar_bool": True}


def test_atomic_write_doesnt_leave_tmp_file(tmp_path: Path) -> None:
    snap_path = tmp_path / "vault_snapshot.json"
    write_snapshot([_make_node("Concept:x")], snap_path)
    assert snap_path.exists()
    assert not snap_path.with_suffix(snap_path.suffix + ".tmp").exists()


def test_read_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert read_snapshot(tmp_path / "does-not-exist.json") is None


def test_read_returns_none_for_corrupt_json(tmp_path: Path) -> None:
    snap_path = tmp_path / "vault_snapshot.json"
    snap_path.write_text("not valid json {{{", encoding="utf-8")
    assert read_snapshot(snap_path) is None


def test_read_returns_none_for_wrong_top_level_shape(tmp_path: Path) -> None:
    snap_path = tmp_path / "vault_snapshot.json"
    snap_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert read_snapshot(snap_path) is None


def test_written_file_records_schema_version(tmp_path: Path) -> None:
    snap_path = tmp_path / "vault_snapshot.json"
    write_snapshot([_make_node("Concept:y")], snap_path)
    payload = json.loads(snap_path.read_text(encoding="utf-8"))
    assert payload["version"] == SNAPSHOT_VERSION


def test_empty_iterable_writes_empty_nodes_dict(tmp_path: Path) -> None:
    snap_path = tmp_path / "vault_snapshot.json"
    write_snapshot([], snap_path)
    payload = json.loads(snap_path.read_text(encoding="utf-8"))
    assert payload == {"version": SNAPSHOT_VERSION, "nodes": {}}


def test_node_snapshot_from_node_dedups_zero_values_correctly() -> None:
    """Empty defaults shouldn't be confused with missing data on round-trip."""
    snap = NodeSnapshot.from_node(_make_node("Concept:bare"))
    assert snap.name == "Test"
    assert snap.aliases == ()
    assert snap.description == ""
    assert snap.metadata == {}

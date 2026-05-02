"""Tests for :class:`SqliteGraphStore`.

Verifies the SQLite ``GraphStore`` adapter that wraps the existing local
SQLite schema (shared with :class:`SQLiteResearchGraphStore`) behind the
``GraphStore`` protocol shape used by the hexagonal pipeline.

The store ignores ``owner_user_id`` because the standalone SQLite mode has
no notion of users — that scoping only matters for the multi-tenant
Postgres adapter introduced in Phase 1b.
"""

from __future__ import annotations

from pathlib import Path

from llm_wiki.graph_stores import SqliteGraphStore
from llm_wiki.ports import GraphStore
from llm_wiki.research_graph import ResearchEdge, ResearchNode, ResearchNodeType


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

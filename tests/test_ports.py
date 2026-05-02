"""Tests for the hexagonal ports defining input/output adapters."""

from __future__ import annotations

from typing import Iterator, List, Optional, Union
from uuid import UUID

import pytest

from llm_wiki.ports import GraphStore, Source, SourceLoader
from llm_wiki.research_graph import ResearchEdge, ResearchGraph, ResearchNode


def test_source_constructs_with_required_fields() -> None:
    src = Source(id="src-1", path="file:///tmp/x.md", content="hello world")
    assert src.id == "src-1"
    assert src.path == "file:///tmp/x.md"
    assert src.content == "hello world"


def test_source_metadata_defaults_to_empty_dict() -> None:
    # Explicitly pass path=None to verify the optional field accepts None.
    src = Source(id="src-2", path=None, content="body")
    assert src.metadata == {}


def test_source_path_defaults_to_none() -> None:
    # Verify path is now optional (default None) per the docstring.
    src = Source(id="src-3", content="body")
    assert src.path is None


# ---------------------------------------------------------------------------
# SourceLoader runtime_checkable protocol
# ---------------------------------------------------------------------------

_SOURCE_LOADER_METHODS = ("discover", "fetch")


def _build_source_loader_class(skip: Optional[str] = None) -> type:
    """Build a SourceLoader-shaped class, optionally omitting one method."""

    namespace: dict = {}

    def discover(self) -> Iterator[Source]:
        yield Source(id="a", path=None, content="x")

    def fetch(self, source_id: str) -> Source:
        return Source(id=source_id, path=None, content="x")

    methods = {"discover": discover, "fetch": fetch}
    for name, fn in methods.items():
        if name == skip:
            continue
        namespace[name] = fn
    return type("DynamicLoader", (), namespace)


def test_source_loader_protocol_runtime_checkable() -> None:
    # Note: @runtime_checkable validates method presence only, not signatures.
    GoodLoader = _build_source_loader_class()
    assert isinstance(GoodLoader(), SourceLoader)


@pytest.mark.parametrize("missing_method", _SOURCE_LOADER_METHODS)
def test_source_loader_rejects_class_missing_method(missing_method: str) -> None:
    """Removing any required method must cause isinstance to return False."""
    BadLoader = _build_source_loader_class(skip=missing_method)
    assert not isinstance(BadLoader(), SourceLoader)


def test_source_loader_works_for_abc_subclass() -> None:
    """An ABC-style subclass of SourceLoader should also pass isinstance."""

    class GoodLoaderABC(SourceLoader):
        def discover(self) -> Iterator[Source]:
            return iter([])

        def fetch(self, source_id: str) -> Source:
            raise NotImplementedError

    assert isinstance(GoodLoaderABC(), SourceLoader)


# ---------------------------------------------------------------------------
# GraphStore runtime_checkable protocol
# ---------------------------------------------------------------------------

_GRAPH_STORE_METHODS = (
    "upsert_node",
    "upsert_edge",
    "get_node",
    "iterate_nodes",
    "query_subgraph",
    "find_canonical",
)


def _build_graph_store_class(skip: Optional[str] = None) -> type:
    """Build a GraphStore-shaped class, optionally omitting one method."""

    def upsert_node(self, node: ResearchNode) -> str:
        return "id"

    def upsert_edge(self, edge: ResearchEdge) -> None:
        return None

    def get_node(self, node_id: str) -> Optional[ResearchNode]:
        return None

    def iterate_nodes(
        self,
        node_type: Optional[str] = None,
        owner_user_id: Optional[Union[str, UUID]] = None,
    ) -> Iterator[ResearchNode]:
        return iter(())

    def query_subgraph(self, seeds: List[str], depth: int = 1) -> ResearchGraph:
        return ResearchGraph()

    def find_canonical(self, name: str, node_type: str) -> Optional[ResearchNode]:
        return None

    methods = {
        "upsert_node": upsert_node,
        "upsert_edge": upsert_edge,
        "get_node": get_node,
        "iterate_nodes": iterate_nodes,
        "query_subgraph": query_subgraph,
        "find_canonical": find_canonical,
    }
    namespace: dict = {}
    for name, fn in methods.items():
        if name == skip:
            continue
        namespace[name] = fn
    return type("DynamicStore", (), namespace)


def test_graph_store_protocol_runtime_checkable() -> None:
    # Note: @runtime_checkable validates method presence only, not signatures.
    GoodStore = _build_graph_store_class()
    assert isinstance(GoodStore(), GraphStore)


@pytest.mark.parametrize("missing_method", _GRAPH_STORE_METHODS)
def test_graph_store_rejects_class_missing_method(missing_method: str) -> None:
    """Removing any required method must cause isinstance to return False."""
    BadStore = _build_graph_store_class(skip=missing_method)
    assert not isinstance(BadStore(), GraphStore)


def test_graph_store_works_for_abc_subclass() -> None:
    """An ABC-style subclass of GraphStore should also pass isinstance."""

    class GoodStoreABC(GraphStore):
        def upsert_node(self, node: ResearchNode) -> str:
            return "id"

        def upsert_edge(self, edge: ResearchEdge) -> None:
            return None

        def get_node(self, node_id: str) -> Optional[ResearchNode]:
            return None

        def iterate_nodes(
            self,
            node_type: Optional[str] = None,
            owner_user_id: Optional[Union[str, UUID]] = None,
        ) -> Iterator[ResearchNode]:
            return iter(())

        def query_subgraph(self, seeds: List[str], depth: int = 1) -> ResearchGraph:
            return ResearchGraph()

        def find_canonical(self, name: str, node_type: str) -> Optional[ResearchNode]:
            return None

    assert isinstance(GoodStoreABC(), GraphStore)

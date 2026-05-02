"""Graph store port: output adapter interface for LLM-Wiki persistence.

A `GraphStore` accepts upserts of nodes/edges produced by the extractor and
serves graph queries back to consumers. Implementations include
`SqliteGraphStore` (LLM-Wiki standalone) and `PostgresGraphStore`
(HypePaper-driven, multi-tenant with owner_user_id scoping).
"""

from __future__ import annotations

from typing import Iterator, List, Optional, Protocol, Union, runtime_checkable
from uuid import UUID

from ..research_graph import ResearchEdge, ResearchGraph, ResearchNode


@runtime_checkable
class GraphStore(Protocol):
    """Port for persisting and querying the research knowledge graph."""

    def upsert_node(self, node: ResearchNode) -> str:
        """Persist a node, merging on canonical identity. Returns node id."""
        ...

    def upsert_edge(self, edge: ResearchEdge) -> None:
        """Persist an edge, idempotent on (src, dst, type)."""
        ...

    def get_node(self, node_id: str) -> Optional[ResearchNode]:
        """Fetch a single node by id, or ``None`` if absent."""
        ...

    def iterate_nodes(
        self,
        node_type: Optional[str] = None,
        owner_user_id: Optional[Union[str, UUID]] = None,
    ) -> Iterator[ResearchNode]:
        """Iterate nodes, optionally filtered by type and/or owner."""
        ...

    def query_subgraph(self, seeds: List[str], depth: int = 1) -> ResearchGraph:
        """Return the subgraph reachable from ``seeds`` within ``depth`` hops."""
        ...

    def find_canonical(self, name: str, node_type: str) -> Optional[ResearchNode]:
        """Look up a canonical node by display name and type, for canonicalization."""
        ...

"""Graph store port: output adapter interface for Tesserae persistence.

A `GraphStore` accepts upserts of nodes/edges produced by the extractor and
serves graph queries back to consumers. Implementations include
`SqliteGraphStore` (Tesserae standalone) and `PostgresGraphStore`
(HypePaper-driven, multi-tenant with owner_user_id scoping).
"""

from __future__ import annotations

from typing import (
    Collection,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Protocol,
    Set,
    Union,
    runtime_checkable,
)
from uuid import UUID

from ..research_graph import (
    UNTRUSTED_EDGE_STATUSES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
)


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

    def delete_node(self, node_id: str) -> bool:
        """Delete a single node by id. Returns True if a row was removed."""
        ...

    def delete_nodes_by_source(self, source_paths: Set[str]) -> Set[str]:
        """Delete nodes whose provenance set becomes EMPTY after removing ``source_paths``.

        Nodes still referenced by an unchanged source_path are kept
        (cross-file concepts survive). Returns the SET of deleted node ids
        (so the caller can drop exactly those from the in-memory graph —
        NOT a count).
        """
        ...


# --------------------------------------------------------------------------- #
# Optional capped-neighbour capability (0.42, T8)                              #
# --------------------------------------------------------------------------- #
#
# Deliberately NOT part of ``GraphStore``: adding a method to a
# ``runtime_checkable`` Protocol makes every existing store (HypePaper's
# ``PostgresGraphStore`` among them) fail ``isinstance(store, GraphStore)`` the
# day this release is installed. A store opts in by implementing the methods
# below; callers go through :func:`store_neighbors` / :func:`store_iterate_edges`,
# which use them when present (``hasattr``) and fall back otherwise.

_DIRECTIONS = ("out", "in", "both")


@runtime_checkable
class NeighborQueryStore(Protocol):
    """Stores that can answer capped, typed neighbour queries natively."""

    def neighbors(
        self,
        node_id: str,
        types: Optional[Collection[str]] = None,
        direction: str = "both",
        limit_per_type: Optional[int] = None,
        min_confidence: Optional[float] = None,
        include_statuses: Optional[Collection[str]] = None,
    ) -> List[ResearchEdge]:
        """Edges incident to ``node_id``, at most ``limit_per_type`` per type."""
        ...

    def iterate_edges(self, types: Optional[Collection[str]] = None) -> Iterator[ResearchEdge]:
        """Every edge, optionally restricted to ``types``."""
        ...


def _confidence(edge: ResearchEdge) -> float:
    # An edge without a confidence (every 0.41 edge) is an untracked assertion:
    # ranked and filtered as 1.0, the same way PPR walks it at full weight.
    return 1.0 if edge.confidence is None else float(edge.confidence)


def filter_neighbor_edges(
    edges: Iterable[ResearchEdge],
    node_id: str,
    types: Optional[Collection[str]] = None,
    direction: str = "both",
    limit_per_type: Optional[int] = None,
    min_confidence: Optional[float] = None,
    include_statuses: Optional[Collection[str]] = None,
) -> List[ResearchEdge]:
    """The reference semantics of a neighbour query, over any edge iterable.

    Keeps edges incident to ``node_id`` in ``direction``; drops types outside
    ``types``, edges below ``min_confidence``, and edges whose status is in
    ``UNTRUSTED_EDGE_STATUSES`` unless listed in ``include_statuses``. Within
    each type, ranks by confidence (desc) then the other endpoint's id, and
    keeps the first ``limit_per_type``. Deterministic.
    """
    if direction not in _DIRECTIONS:
        raise ValueError(f"direction must be one of {_DIRECTIONS}, got {direction!r}")
    if limit_per_type is not None and limit_per_type <= 0:
        raise ValueError(f"limit_per_type must be positive, got {limit_per_type}")
    wanted = frozenset(types) if types is not None else None
    skipped = UNTRUSTED_EDGE_STATUSES - frozenset(include_statuses or ())
    by_type: Dict[str, List[ResearchEdge]] = {}
    seen: Set[tuple] = set()
    for edge in edges:
        outgoing = edge.source == node_id
        incoming = edge.target == node_id
        if not (
            (direction in ("out", "both") and outgoing)
            or (direction in ("in", "both") and incoming)
        ):
            continue
        if wanted is not None and edge.type not in wanted:
            continue
        if edge.status is not None and edge.status in skipped:
            continue
        if min_confidence is not None and _confidence(edge) < float(min_confidence):
            continue
        key = (edge.source, edge.type, edge.target)
        if key in seen:
            continue
        seen.add(key)
        by_type.setdefault(edge.type, []).append(edge)
    out: List[ResearchEdge] = []
    for edge_type in sorted(by_type):
        ranked = sorted(
            by_type[edge_type],
            key=lambda e: (
                -_confidence(e),
                e.target if e.source == node_id else e.source,
                e.source,
                e.target,
            ),
        )
        out.extend(ranked[:limit_per_type] if limit_per_type else ranked)
    return out


def store_neighbors(
    store: GraphStore,
    node_id: str,
    types: Optional[Collection[str]] = None,
    direction: str = "both",
    limit_per_type: Optional[int] = None,
    min_confidence: Optional[float] = None,
    include_statuses: Optional[Collection[str]] = None,
) -> List[ResearchEdge]:
    """Capped neighbours of ``node_id`` through ``store``.

    Uses the store's native ``neighbors`` when it has one; otherwise a 1-hop
    ``query_subgraph`` filtered by :func:`filter_neighbor_edges` — correct for
    every ``GraphStore``, just not capped at the source.
    """
    native = getattr(store, "neighbors", None)
    if callable(native):
        return list(
            native(
                node_id,
                types=types,
                direction=direction,
                limit_per_type=limit_per_type,
                min_confidence=min_confidence,
                include_statuses=include_statuses,
            )
        )
    sub = store.query_subgraph([node_id], depth=1)
    return filter_neighbor_edges(
        sub.edges,
        node_id,
        types=types,
        direction=direction,
        limit_per_type=limit_per_type,
        min_confidence=min_confidence,
        include_statuses=include_statuses,
    )


def store_iterate_edges(
    store: GraphStore,
    types: Optional[Collection[str]] = None,
    *,
    allow_full_scan: bool = False,
) -> Iterator[ResearchEdge]:
    """Every edge in ``store`` (optionally of ``types``).

    Native ``iterate_edges`` when the store has one. Otherwise the only
    portable route is a depth-1 subgraph over EVERY node — O(graph) memory on a
    store that may hold millions of rows — so it runs only when the caller
    passes ``allow_full_scan=True`` and raises ``NotImplementedError`` instead
    of silently doing it.
    """
    native = getattr(store, "iterate_edges", None)
    if callable(native):
        yield from native(types=types)
        return
    if not allow_full_scan:
        raise NotImplementedError(
            f"{type(store).__name__} has no iterate_edges(); pass "
            "allow_full_scan=True to enumerate edges through query_subgraph "
            "over every node"
        )
    wanted = frozenset(types) if types is not None else None
    node_ids = sorted(n.id for n in store.iterate_nodes())
    # depth=1 from every node returns every edge incident to any node — all of
    # them (depth=0 fetches no edges at all).
    sub = store.query_subgraph(node_ids, depth=1) if node_ids else ResearchGraph()
    for edge in sorted(sub.edges, key=lambda e: (e.source, e.type, e.target)):
        if wanted is None or edge.type in wanted:
            yield edge

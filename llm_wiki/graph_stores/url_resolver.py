"""URL → GraphStore dispatcher.

Resolves a URL like ``sqlite:///path/to.db`` or
``hypepaper-postgres://user:pass@host/db`` to the appropriate GraphStore
implementation. Used by the MCP server to let the operator point it at
any backing store.

For Postgres URLs, this resolver lazy-imports HypePaper's
``PostgresGraphStore`` and HypePaper's ``AsyncSessionLocal`` and returns
a ``_PostgresGraphStoreSession`` wrapper that satisfies the synchronous
:class:`GraphStore` Protocol by opening a fresh ``AsyncSession`` per
method call. This avoids binding a single SQLAlchemy session to the
lifetime of the MCP process (sessions don't survive across the multiple
``asyncio.run`` event loops the sync facade creates) while keeping the
MCP server itself fully sync (stdio JSON-RPC).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterator, List, Optional, Union
from urllib.parse import urlparse
from uuid import UUID

from .sqlite import SqliteGraphStore
from ..ports import GraphStore
from ..research_graph import ResearchEdge, ResearchGraph, ResearchNode


def resolve_graph_store(
    url: str, *, owner_user_id: Optional[Union[str, UUID]] = None
) -> GraphStore:
    """Resolve a graph-store URL to a concrete :class:`GraphStore` adapter.

    Supported schemes:
      - ``sqlite:///abs/path/to.db`` — opens (or creates) a local
        :class:`SqliteGraphStore`.
      - ``sqlite:///relative/path`` — same, resolved as a relative path
        (rare; document for completeness).
      - ``hypepaper-postgres://...`` / ``postgresql://...`` /
        ``postgres://...`` / ``postgresql+asyncpg://...`` — lazy-imports
        HypePaper's :class:`PostgresGraphStore` and returns a session-
        scoping wrapper. Requires the HypePaper backend package to be
        importable in the running Python environment.

    The ``owner_user_id`` keyword is honoured for Postgres URLs only; it
    scopes the resulting store to a single HypePaper user's private
    graph layer (None = global/canonical layer). SQLite stores ignore
    this argument.
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme == "sqlite":
        # urlparse splits ``sqlite:///abs/path`` into netloc='' and
        # path='/abs/path'. A leading slash means absolute.
        path_str = parsed.path
        if path_str.startswith("/"):
            return SqliteGraphStore(Path(path_str))
        return SqliteGraphStore(Path(path_str.lstrip("/")))
    if scheme in ("postgresql", "postgres", "postgresql+asyncpg", "hypepaper-postgres"):
        try:
            from src.features.wiki.graph_store import PostgresGraphStore  # noqa: F401
            from src.core.database import AsyncSessionLocal  # noqa: F401
        except ImportError as exc:  # pragma: no cover — import error path
            raise ImportError(
                "PostgresGraphStore requires the HypePaper backend to be importable. "
                "Run the LLM-Wiki MCP server inside the HypePaper backend's Python "
                "env (PYTHONPATH must include hypepaper/backend), or install the "
                "HypePaper backend as a package."
            ) from exc
        return _PostgresGraphStoreSession(owner_user_id=owner_user_id)
    raise ValueError(f"Unsupported graph-store URL scheme: {scheme!r}")


class _PostgresGraphStoreSession:
    """Synchronous :class:`GraphStore` wrapper around HypePaper's async
    ``PostgresGraphStore``.

    The MCP server is sync (stdio + JSON-RPC), but HypePaper's adapter
    is async-first and uses a per-request ``AsyncSession``. We can't
    reuse a single SQLAlchemy session across calls because the sync
    facade on ``PostgresGraphStore`` spins up a fresh ``asyncio.run``
    event loop per operation, and SQLAlchemy async sessions are bound
    to the loop they were created on. So this wrapper:

    1. Opens a fresh ``AsyncSession`` from ``AsyncSessionLocal``.
    2. Constructs ``PostgresGraphStore(db, owner_user_id=...)`` against it.
    3. Awaits the requested async method.
    4. Commits + closes the session.

    All inside a single ``asyncio.run`` per public method call. This is
    fine for the MCP server's read-mostly tool workload; the cost of a
    new asyncpg connection per tool call is acceptable for an
    interactive Claude Code MCP session and avoids the worse problem of
    leaking connections across iterations of the JSON-RPC loop.
    """

    def __init__(
        self, owner_user_id: Optional[Union[str, UUID]] = None
    ) -> None:
        self.owner_user_id = owner_user_id

    async def _run(self, fn):
        """Open a session, build a store, run ``fn(store)``, commit, close."""
        # Imported lazily so non-Postgres MCP setups don't pay the cost.
        from src.core.database import AsyncSessionLocal
        from src.features.wiki.graph_store import PostgresGraphStore

        async with AsyncSessionLocal() as session:
            store = PostgresGraphStore(session, owner_user_id=self.owner_user_id)
            try:
                result = await fn(store)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise

    # GraphStore Protocol implementations ---------------------------------

    def upsert_node(self, node: ResearchNode) -> str:
        return asyncio.run(self._run(lambda store: store.aupsert_node(node)))

    def upsert_edge(self, edge: ResearchEdge) -> None:
        asyncio.run(self._run(lambda store: store.aupsert_edge(edge)))

    def get_node(self, node_id: str) -> Optional[ResearchNode]:
        return asyncio.run(self._run(lambda store: store.aget_node(node_id)))

    def iterate_nodes(
        self,
        node_type: Optional[str] = None,
        owner_user_id: Optional[Union[str, UUID]] = None,
    ) -> Iterator[ResearchNode]:
        async def _collect(store):
            return [
                n
                async for n in store.aiterate_nodes(node_type, owner_user_id)
            ]

        return iter(asyncio.run(self._run(_collect)))

    def query_subgraph(self, seeds: List[str], depth: int = 1) -> ResearchGraph:
        return asyncio.run(
            self._run(lambda store: store.aquery_subgraph(seeds, depth))
        )

    def find_canonical(self, name: str, node_type: str) -> Optional[ResearchNode]:
        return asyncio.run(
            self._run(lambda store: store.afind_canonical(name, node_type))
        )


__all__ = ["resolve_graph_store"]

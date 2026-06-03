"""URL → GraphStore dispatcher.

Resolves a URL like ``sqlite:///path/to.db`` or
``hypepaper-postgres://user:pass@host/db`` to the appropriate GraphStore
implementation. Used by the MCP server to let the operator point it at
any backing store.

For Postgres URLs, this resolver lazy-imports HypePaper's
``PostgresGraphStore`` and HypePaper's ``AsyncSessionLocal`` and returns
a ``_PostgresGraphStoreSession`` wrapper that satisfies the synchronous
:class:`GraphStore` Protocol by opening a fresh ``AsyncSession`` per
method call, while keeping the MCP server itself fully sync (stdio
JSON-RPC).

Async work is dispatched onto a single, persistent background event loop
(see :class:`_AsyncRuntime` / :func:`_runtime`) running on a daemon
thread for the lifetime of the process. Coroutines are submitted with
``asyncio.run_coroutine_threadsafe(...).result()``. This replaces the
former ``asyncio.run``-per-call pattern (CMP-04): a fresh loop is no
longer spun up and torn down on every method, so the asyncpg connection
pool created by ``AsyncSessionLocal`` stays warm across streaming
incremental upserts. Each ``AsyncSession`` is still opened and closed
per call, so transaction semantics are unchanged — only the loop driving
the coroutines is now long-lived and shared.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Iterator, List, Optional, Union
from urllib.parse import urlparse
from uuid import UUID

from .sqlite import SqliteGraphStore
from ..ports import GraphStore
from ..research_graph import ResearchEdge, ResearchGraph, ResearchNode


class _AsyncRuntime:
    """A persistent asyncio event loop running on a daemon thread.

    Created lazily (never at import time, per 04-RESEARCH.md Pitfall 5) so
    we never clobber an already-running loop in the importing process. The
    loop is driven by ``run_forever`` on a dedicated daemon thread; sync
    callers submit coroutines via :meth:`run` and block on the result.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name="url-resolver-loop",
            daemon=True,
        )
        self._thread.start()

    def run(self, coro):
        """Submit *coro* to the background loop and block for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self) -> None:
        """Stop the loop, join the daemon thread, and close the loop."""
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()


_runtime_singleton: Optional[_AsyncRuntime] = None
_runtime_lock = threading.Lock()


def _runtime() -> _AsyncRuntime:
    """Return the process-wide :class:`_AsyncRuntime`, creating it once.

    Construction is guarded by a lock so concurrent first-callers share a
    single persistent loop (and thus a single warm connection pool).
    """
    global _runtime_singleton
    if _runtime_singleton is None:
        with _runtime_lock:
            if _runtime_singleton is None:
                _runtime_singleton = _AsyncRuntime()
    return _runtime_singleton


def shutdown_runtime() -> None:
    """Tear down the persistent runtime and clear the singleton.

    Primarily for tests / clean process teardown. After calling this, the
    next :func:`_runtime` call constructs a fresh runtime.
    """
    global _runtime_singleton
    with _runtime_lock:
        runtime = _runtime_singleton
        _runtime_singleton = None
    if runtime is not None:
        runtime.close()


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
                "Run the Tesserae MCP server inside the HypePaper backend's Python "
                "env (PYTHONPATH must include hypepaper/backend), or install the "
                "HypePaper backend as a package."
            ) from exc
        return _PostgresGraphStoreSession(owner_user_id=owner_user_id)
    raise ValueError(f"Unsupported graph-store URL scheme: {scheme!r}")


class _PostgresGraphStoreSession:
    """Synchronous :class:`GraphStore` wrapper around HypePaper's async
    ``PostgresGraphStore``.

    The MCP server is sync (stdio + JSON-RPC), but HypePaper's adapter
    is async-first and uses a per-request ``AsyncSession``. Every public
    method dispatches its coroutine onto the shared persistent background
    loop via ``_runtime().run(...)`` (CMP-04) — no ``asyncio.run`` is
    created per call. Because the loop is long-lived, the asyncpg
    connection pool behind ``AsyncSessionLocal`` stays warm across calls,
    which matters for streaming incremental upserts. Each call still:

    1. Opens a fresh ``AsyncSession`` from ``AsyncSessionLocal``.
    2. Constructs ``PostgresGraphStore(db, owner_user_id=...)`` against it.
    3. Awaits the requested async method.
    4. Commits + closes the session.

    So transaction/session semantics are identical to before; only the
    event loop driving the coroutines is now persistent and shared rather
    than spun up and torn down per operation.
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
        return _runtime().run(self._run(lambda store: store.aupsert_node(node)))

    def upsert_edge(self, edge: ResearchEdge) -> None:
        _runtime().run(self._run(lambda store: store.aupsert_edge(edge)))

    def get_node(self, node_id: str) -> Optional[ResearchNode]:
        return _runtime().run(self._run(lambda store: store.aget_node(node_id)))

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

        return iter(_runtime().run(self._run(_collect)))

    def query_subgraph(self, seeds: List[str], depth: int = 1) -> ResearchGraph:
        return _runtime().run(
            self._run(lambda store: store.aquery_subgraph(seeds, depth))
        )

    def find_canonical(self, name: str, node_type: str) -> Optional[ResearchNode]:
        return _runtime().run(
            self._run(lambda store: store.afind_canonical(name, node_type))
        )


__all__ = ["resolve_graph_store", "shutdown_runtime"]

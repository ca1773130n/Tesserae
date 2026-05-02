"""URL → GraphStore dispatcher.

Resolves a URL like ``sqlite:///path/to.db`` or ``postgresql://...`` to
the appropriate GraphStore implementation. Used by the MCP server to
let the operator point it at any backing store.

Future: registers a Postgres adapter when the HypePaper-side
``PostgresGraphStore`` package is importable. For now only SQLite
URLs resolve.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from .sqlite import SqliteGraphStore
from ..ports import GraphStore


def resolve_graph_store(url: str) -> GraphStore:
    """Resolve a graph-store URL to a concrete :class:`GraphStore` adapter.

    Supported schemes:
      - ``sqlite:///abs/path/to.db`` — opens (or creates) a local
        :class:`SqliteGraphStore`.
      - ``sqlite:///relative/path`` — same, resolved as a relative path
        (rare; document for completeness).

    Schemes reserved for the HypePaper integration (``postgresql://``,
    ``postgres://``, ``postgresql+asyncpg://``, ``hypepaper-postgres://``)
    raise :class:`NotImplementedError` until that adapter is registered.
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
        raise NotImplementedError(
            f"PostgresGraphStore is provided by the HypePaper integration package. "
            f"To use {scheme}:// URLs, install the HypePaper integration that "
            f"registers this scheme via llm_wiki.graph_stores.url_resolver. "
            f"Until then, pass a GraphStore instance directly to the MCP server."
        )
    raise ValueError(f"Unsupported graph-store URL scheme: {scheme!r}")

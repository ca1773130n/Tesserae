"""Concrete :class:`GraphStore` implementations.

Adapters in this package satisfy :class:`llm_wiki.ports.GraphStore` by
persisting :class:`ResearchNode` / :class:`ResearchEdge` records to a
storage substrate (SQLite for standalone, Postgres for HypePaper-driven
multi-tenant). The pipeline depends only on the port, so any of these
can be swapped without changes to extraction or canonicalization.
"""

from __future__ import annotations

from .sqlite import SqliteGraphStore

__all__ = ["SqliteGraphStore"]

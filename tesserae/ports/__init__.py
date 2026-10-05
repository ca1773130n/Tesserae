"""Hexagonal ports: pluggable input/output adapter interfaces for Tesserae.

Adapters in this package decouple the extraction/canonicalization core from
storage and source-loading concerns, so the same pipeline can run against
filesystem + SQLite (standalone) or Postgres (HypePaper-driven) without
changes to the middle layer.
"""

from __future__ import annotations

from .content_resolver import ContentResolver, ContentView
from .graph_store import GraphStore, NeighborQueryStore, store_iterate_edges, store_neighbors
from .source_loader import Source, SourceLoader

__all__ = [
    "ContentResolver",
    "ContentView",
    "GraphStore",
    "NeighborQueryStore",
    "Source",
    "SourceLoader",
    "store_iterate_edges",
    "store_neighbors",
]

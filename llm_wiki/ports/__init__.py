"""Hexagonal ports: pluggable input/output adapter interfaces for LLM-Wiki.

Adapters in this package decouple the extraction/canonicalization core from
storage and source-loading concerns, so the same pipeline can run against
filesystem + SQLite (standalone) or Postgres (HypePaper-driven) without
changes to the middle layer.
"""

from __future__ import annotations

from .graph_store import GraphStore
from .source_loader import Source, SourceLoader

__all__ = ["GraphStore", "Source", "SourceLoader"]

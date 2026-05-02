"""Concrete :class:`SourceLoader` implementations.

Adapters in this package satisfy :class:`llm_wiki.ports.SourceLoader` by
yielding :class:`llm_wiki.ports.Source` records from various substrates
(filesystem, Postgres, etc.). The pipeline depends only on the port, so any
of these can be swapped without changes to extraction or canonicalization.
"""

from __future__ import annotations

from .filesystem import FilesystemSourceLoader

__all__ = ["FilesystemSourceLoader"]

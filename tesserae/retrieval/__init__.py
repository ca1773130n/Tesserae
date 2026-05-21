"""Tesserae retrieval helpers.

This subpackage hosts retrieval-time utilities that sit *between* the typed
``ResearchGraph`` and surface tools such as the MCP ``search_nodes`` endpoint
or the ``ask`` backends.

Modules:

- :mod:`tesserae.retrieval.hybrid` — reciprocal-rank-fusion blend of BM25,
  lexical/FTS-style substring matching, and a pluggable embedding lane.
- :mod:`tesserae.retrieval.ppr` — HippoRAG-style Personalized PageRank for
  multi-hop seed expansion.
"""

from .hybrid import (
    EmbeddingBackend,
    HashEmbeddingBackend,
    HybridSearchResult,
    ScoredNode,
    SentenceTransformersBackend,
    active_embedding_backend,
    hybrid_search,
)
from .ppr import (
    DEFAULT_EDGE_TYPE_WEIGHTS,
    personalized_pagerank,
)

__all__ = [
    "DEFAULT_EDGE_TYPE_WEIGHTS",
    "EmbeddingBackend",
    "HashEmbeddingBackend",
    "HybridSearchResult",
    "ScoredNode",
    "SentenceTransformersBackend",
    "active_embedding_backend",
    "hybrid_search",
    "personalized_pagerank",
]

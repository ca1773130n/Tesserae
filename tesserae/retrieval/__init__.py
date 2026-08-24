"""Tesserae retrieval helpers.

This subpackage hosts retrieval-time utilities that sit *between* the typed
``ResearchGraph`` and surface tools such as the MCP ``search_nodes`` endpoint
or the ``ask`` backends.

Modules:

- :mod:`tesserae.retrieval.hybrid` — reciprocal-rank-fusion blend of BM25,
  lexical/FTS-style substring matching, and a pluggable embedding lane.
- :mod:`tesserae.retrieval.ppr` — HippoRAG-style Personalized PageRank for
  multi-hop seed expansion.
- :mod:`tesserae.retrieval.fanout` — opt-in query fan-out with a
  document-disjoint merge, layered ABOVE ``hybrid_search``. Unlike
  ``rerank`` it pulls no heavy dependency, so it is exported from here.
"""

from .fanout import (
    DEFAULT_OVERFETCH,
    DEFAULT_SOURCE_CAP,
    fanout_search,
)
from .hybrid import (
    EmbeddingBackend,
    HashEmbeddingBackend,
    HybridSearchResult,
    LaneProfile,
    RetrievalProfile,
    ScoredNode,
    SentenceTransformersBackend,
    WinnerAttribution,
    active_embedding_backend,
    hybrid_search,
)
from .ppr import (
    DEFAULT_EDGE_TYPE_WEIGHTS,
    personalized_pagerank,
)
from .query_decompose import DEFAULT_UBIQUITY_DF_RATIO

__all__ = [
    "DEFAULT_EDGE_TYPE_WEIGHTS",
    "DEFAULT_OVERFETCH",
    "DEFAULT_SOURCE_CAP",
    "DEFAULT_UBIQUITY_DF_RATIO",
    "EmbeddingBackend",
    "HashEmbeddingBackend",
    "HybridSearchResult",
    "LaneProfile",
    "RetrievalProfile",
    "ScoredNode",
    "SentenceTransformersBackend",
    "WinnerAttribution",
    "active_embedding_backend",
    "fanout_search",
    "hybrid_search",
    "personalized_pagerank",
]

"""Retrieval utilities over the typed ResearchGraph.

Currently provides Personalized PageRank (PPR) for HippoRAG-style
multi-hop seed expansion. See ``tesserae.retrieval.ppr``.
"""

from tesserae.retrieval.ppr import (
    DEFAULT_EDGE_TYPE_WEIGHTS,
    personalized_pagerank,
)

__all__ = ["personalized_pagerank", "DEFAULT_EDGE_TYPE_WEIGHTS"]

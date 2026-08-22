"""Tests for the cross-encoder reranking stage.

Every test here runs a STUB reranker. The point of the module is that the
reordering logic, the score bookkeeping and the never-adds-a-document
guarantee are all testable without torch, transformers, or a 1.1 GB download —
so a normal install runs this file rather than skipping it.
"""

from __future__ import annotations

from typing import List, Sequence

import pytest

from tesserae.research_graph import ResearchNode, ResearchNodeType
from tesserae.retrieval.hybrid import ScoredNode
from tesserae.retrieval.rerank import Qwen3Reranker, rerank_nodes


class StubReranker:
    """Scores a document by how many times ``query`` occurs in it."""

    name = "stub"

    def __init__(self) -> None:
        self.calls: List[Sequence[str]] = []

    def score(self, query: str, documents: Sequence[str]) -> List[float]:
        self.calls.append(list(documents))
        return [float(doc.lower().count(query.lower())) for doc in documents]


def _scored(name: str, fused: float, bm25_rank: int) -> ScoredNode:
    return ScoredNode(
        node=ResearchNode(
            id=f"MethodologicalConcept:{name}",
            name=name,
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            description=f"{name} " * (bm25_rank + 1),
        ),
        score=fused,
        per_lane={"bm25": fused, "lexical": 0.0, "embedding": 0.0},
        ranks={"bm25": bm25_rank, "lexical": 9, "embedding": 9},
    )


def test_rerank_reorders_against_the_fused_order() -> None:
    """The fused winner loses to the document the cross-encoder prefers."""
    candidates = [_scored("alpha", 0.9, 1), _scored("beta", 0.1, 3)]
    out = rerank_nodes("beta", candidates, reranker=StubReranker())
    assert [item.node.name for item in out] == ["beta", "alpha"]
    assert out[0].score > out[1].score


def test_rerank_keeps_the_lane_scores_it_arrived_with() -> None:
    """A reranked node stays readable as the fused hit it used to be."""
    candidates = [_scored("alpha", 0.9, 1), _scored("beta", 0.1, 3)]
    out = rerank_nodes("beta", candidates, reranker=StubReranker())
    winner = out[0]
    assert winner.per_lane["bm25"] == 0.1, "original lane score must survive"
    assert winner.ranks["bm25"] == 3, "original lane rank must survive"
    assert winner.per_lane["rerank"] == winner.score
    assert [item.ranks["rerank"] for item in out] == [1, 2]


def test_rerank_truncates_to_top_n() -> None:
    candidates = [_scored(n, 0.5, 1) for n in ("alpha", "beta", "gamma")]
    out = rerank_nodes("beta", candidates, reranker=StubReranker(), top_n=2)
    assert len(out) == 2


def test_rerank_never_adds_a_document() -> None:
    """No candidates in, no candidates out — a reranker cannot fix recall."""
    stub = StubReranker()
    assert rerank_nodes("anything", [], reranker=stub) == []
    assert stub.calls == [], "an empty candidate set must not reach the model"


def test_rerank_refuses_a_score_count_that_does_not_match() -> None:
    """A silently short score list would mis-assign every score after it."""

    class ShortReranker:
        name = "short"

        def score(self, query: str, documents: Sequence[str]) -> List[float]:
            return [1.0]

    candidates = [_scored("alpha", 0.9, 1), _scored("beta", 0.1, 3)]
    with pytest.raises(ValueError, match="1 scores for 2 documents"):
        rerank_nodes("q", candidates, reranker=ShortReranker())


def test_rerank_reads_the_same_text_the_lexical_lane_scored() -> None:
    """The reranker must not be handed text the ranking it replaces never saw."""
    candidates = [_scored("alpha", 0.9, 1)]
    stub = StubReranker()
    rerank_nodes("alpha", candidates, reranker=stub)
    seen = stub.calls[0][0]
    assert "MethodologicalConcept:alpha" in seen, "node id is part of _node_text"
    assert "alpha alpha" in seen, "description is part of _node_text"


def test_constructing_the_real_reranker_downloads_nothing() -> None:
    """Making one is free; the 1.1 GB of weights arrives on the first score()."""
    reranker = Qwen3Reranker()
    assert reranker.name == "Qwen/Qwen3-Reranker-0.6B"
    assert reranker._model is None
    assert reranker.score("q", []) == [], "no documents means no model load"
    assert reranker._model is None

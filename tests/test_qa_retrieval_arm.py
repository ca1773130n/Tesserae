"""The retrieval arm exists because every prior BM25 comparison scored RANKING.

BM25 cannot answer a question — it returns files. Comparing a memory against it
on "did the top-k contain the gold documents" measures the thing BM25 is built
for and says nothing about supplying an agent with usable context. This arm
makes the baseline answer, under the same prompt and the same answer shape.
"""

from __future__ import annotations

import asyncio

import pytest

from evals.qa.benchmark_retrieval import (
    DEFAULT_TOP_K,
    LANES,
    QABenchmarkRetrieval,
    RetrievalConfig,
)
from evals.qa.null_model import NULL_SYSTEM_PROMPT


class _Client:
    def __init__(self): self.seen = []
    def complete_text(self, *, system, user):
        self.seen.append((system, user)); return "  a short answer  "


def _arm(lane="bm25", docs=("alpha beta gamma", "delta epsilon", "alpha zeta")):
    b = QABenchmarkRetrieval(list(docs), [], RetrievalConfig(lane=lane, top_k=2))
    b.client_factory = _Client
    for i, d in enumerate(docs):
        asyncio.get_event_loop().run_until_complete(b.insert_document(d, i))
    return b


def test_the_shape_matches_the_null_model_or_the_comparison_is_unpublishable() -> None:
    """`scorer.FAIRNESS_KEYS` blocks a cross-system number when the systems were
    asked for different answer shapes, because EM and token F1 run over the
    whole answer string."""
    cfg = RetrievalConfig(lane="bm25")
    assert cfg.answer_shape == "short-span"
    assert cfg.system_prompt.startswith(NULL_SYSTEM_PROMPT), (
        "the retrieval arm must inherit the null model's instruction verbatim; "
        "a reworded prompt is a different ask and silently changes the metric"
    )


def test_it_keeps_the_documents_the_null_model_discards() -> None:
    """The two arms differ by exactly one thing — whether the corpus reaches the
    prompt. That is what makes their difference interpretable."""
    b = _arm()
    assert len(b.documents) == 3


def test_retrieved_sources_reach_the_prompt_and_are_capped() -> None:
    b = _arm(docs=("alpha " * 5000, "beta gamma", "alpha delta"))
    b.config.doc_chars = 100
    b.rag_client = _Client()
    out = asyncio.get_event_loop().run_until_complete(b.query_rag("alpha"))
    assert out == "a short answer", "the answer must be stripped, not raw"
    system, user = b.prompts_sent[0]["system"], b.prompts_sent[0]["user"]
    assert "<source id=" in user and "Question: alpha" in user
    # No single long document may crowd out the rest — that would measure
    # document length rather than ranking.
    for block in user.split("<source id=")[1:]:
        body = block.split(">", 1)[1].rsplit("</source>", 1)[0]
        assert len(body) <= 100 + 2, "doc_chars cap not applied"


def test_an_exhausted_account_raises_instead_of_reading_as_a_refusal() -> None:
    """An empty answer scores as a refusal downstream, which would credit a rate
    limit as caution and quietly improve the arm's hallucination rate."""
    class _Dead:
        def complete_text(self, *, system, user): return None

    b = _arm()
    b.rag_client = _Dead()
    with pytest.raises(RuntimeError, match="no answer"):
        asyncio.get_event_loop().run_until_complete(b.query_rag("alpha"))


def test_unknown_lane_fails_at_construction() -> None:
    from evals.qa.vendor_base import MissingPrerequisite

    with pytest.raises(MissingPrerequisite, match="unknown retrieval lane"):
        QABenchmarkRetrieval([], [], RetrievalConfig(lane="nonsense"))


def test_defaults_match_the_rest_of_the_repository() -> None:
    assert DEFAULT_TOP_K == 10, "the evidence budget every other measurement used"
    assert set(LANES) == {"bm25", "hybrid"}

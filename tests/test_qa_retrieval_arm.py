"""The retrieval arm exists because every prior BM25 comparison scored RANKING.

BM25 cannot answer a question — it returns files. Comparing a memory against it
on "did the top-k contain the gold documents" measures the thing BM25 is built
for and says nothing about supplying an agent with usable context. This arm
makes the baseline answer, under the same prompt and the same answer shape.
"""

from __future__ import annotations

import asyncio

# asyncio.run, never get_event_loop(): another module in the same session
# (tests/test_qa_scorer.py) leaves no current loop in the main thread, and
# get_event_loop() then raises. These tests passed alone and failed in the
# suite — an ordering dependency, not a real defect, but one that would have
# read as a broken arm.

import pytest

from evals.qa.vendor_base import MissingPrerequisite

# The arm subclasses the VENDORED cognee benchmark base, which lives in the
# gitignored `evals/cognee` clone and needs `dotenv`. Neither exists on a CI
# runner, so importing at module scope turns a missing optional prerequisite
# into a collection ERROR that fails the whole suite — which is exactly what it
# did. `tests/test_lme_mab_dataset.py` already skips on absent `pyarrow`; this
# is the same idiom for a prerequisite that raises instead of ImportError.
try:
    from evals.qa.benchmark_retrieval import (
        DEFAULT_TOP_K,
        LANES,
        QABenchmarkRetrieval,
        RetrievalConfig,
    )
    from evals.qa.null_model import NULL_SYSTEM_PROMPT
except MissingPrerequisite as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"vendored QA benchmark base unavailable: {exc}",
                allow_module_level=True)


class _Client:
    def __init__(self): self.seen = []
    def complete_text(self, *, system, user):
        self.seen.append((system, user)); return "  a short answer  "


def _arm(lane="bm25", docs=("alpha beta gamma", "delta epsilon", "alpha zeta")):
    b = QABenchmarkRetrieval(list(docs), [], RetrievalConfig(lane=lane, top_k=2))
    b.client_factory = _Client
    for i, d in enumerate(docs):
        asyncio.run(b.insert_document(d, i))
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
    out = asyncio.run(b.query_rag("alpha"))
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
        asyncio.run(b.query_rag("alpha"))


def test_unknown_lane_fails_at_construction() -> None:
    from evals.qa.vendor_base import MissingPrerequisite

    with pytest.raises(MissingPrerequisite, match="unknown retrieval lane"):
        QABenchmarkRetrieval([], [], RetrievalConfig(lane="nonsense"))


def test_defaults_match_the_rest_of_the_repository() -> None:
    assert DEFAULT_TOP_K == 10, "the evidence budget every other measurement used"
    assert set(LANES) == {"bm25", "hybrid"}


def test_the_corpus_arrives_without_the_harness_staging_it() -> None:
    """The bug that produced a whole table of fake numbers.

    `insert_document` is only called during a staging phase, and `--answer`
    alone skips it. The first real run indexed nothing, retrieved nothing, and
    reported token F1 of 0.009-0.057 for 332 questions — a null model wearing a
    retrieval label. The corpus must reach the arm by construction, not by
    hoping the harness feeds it.
    """
    b = QABenchmarkRetrieval(["alpha beta", "gamma delta"], [], RetrievalConfig(lane="bm25"))
    assert len(b.documents) == 2
    assert b.declared_meta()["documents_indexed"] == 2


def test_staging_twice_does_not_double_index_the_corpus() -> None:
    """A staged run and an --answer-only run must index the same corpus, or the
    two are not comparable — and duplicated documents would also distort BM25's
    document-frequency statistics."""
    docs = ["alpha beta", "gamma delta"]
    b = QABenchmarkRetrieval(list(docs), [], RetrievalConfig(lane="bm25"))
    for i, d in enumerate(docs):
        asyncio.run(b.insert_document(d, i))
    assert len(b.documents) == 2, "constructor-seeded documents were re-added"
    asyncio.run(b.insert_document("epsilon", 9))
    assert len(b.documents) == 3, "a genuinely new document must still be accepted"


def test_an_empty_index_refuses_instead_of_answering() -> None:
    """Answering from no documents yields numbers that look like retrieval and
    are not. One raised question is cheap; a full plausible table is not."""
    b = QABenchmarkRetrieval([], [], RetrievalConfig(lane="bm25"))
    b.rag_client = _Client()
    with pytest.raises(RuntimeError, match="no documents to retrieve from"):
        asyncio.run(b.query_rag("alpha"))


# ---------------------------------------------------- opt-in abstention gate


def _gated_arm(answer: str, quantile=None, docs=None):
    """A 20-document corpus, so idf can tell a rare term from a common one."""
    # "zephyrine" sits in the FIRST document so it survives top_k: BM25 ties
    # across this corpus and the arm breaks ties by index. A rare term in a
    # document the model was never shown is correctly refused, which is what
    # test_invented_vocabulary_does_not_satisfy_the_gate pins.
    docs = docs or (
        ["alpha beta gamma delta zephyrine corpus filler text"]
        + [f"alpha beta gamma delta rare{i} corpus filler text" for i in range(19)]
    )

    class _Fixed:
        def complete_text(self, *, system, user): return answer

    b = QABenchmarkRetrieval(list(docs), [], RetrievalConfig(
        lane="bm25", top_k=5, grounding_quantile=quantile))
    b.rag_client = _Fixed()
    for i, d in enumerate(docs):
        asyncio.run(b.insert_document(d, i))
    return b


def test_the_grounding_gate_is_off_unless_a_run_asks_for_it() -> None:
    """Default None is today's behaviour byte for byte.

    This repository has reverted a change that made an eval-only behaviour the
    product default. The flag exists so a run can opt in; nothing else in the
    arm may notice it is there.
    """
    assert RetrievalConfig().grounding_quantile is None
    b = _gated_arm("alpha beta gamma")          # a pure question restatement
    assert asyncio.run(b.query_rag("alpha beta gamma")) == "alpha beta gamma"


def test_an_answer_that_only_restates_the_question_is_refused_when_gated() -> None:
    """The failure mode the gate exists for: fluent, on-topic, adds nothing.

    Retrieval selected these documents BY the question's terms, so echoing the
    question looks perfectly *extractively supported*. Subtracting the
    question's own vocabulary is what makes the score mean anything — measured
    detector AUC 0.587 before that subtraction and 0.746 after.
    """
    b = _gated_arm("alpha beta gamma", quantile=0.25)
    assert asyncio.run(b.query_rag("alpha beta gamma")) == "", \
        "an empty answer is what scorer.is_refusal reads as a refusal"


def test_a_rare_source_attested_term_passes_the_gate() -> None:
    """The gate must not simply refuse everything: novel evidence gets through."""
    b = _gated_arm("zephyrine", quantile=0.25)
    assert asyncio.run(b.query_rag("alpha beta gamma")) == "zephyrine"


def test_invented_vocabulary_does_not_satisfy_the_gate() -> None:
    """Rare is not enough — the term has to be in the documents that were shown."""
    b = _gated_arm("flombulator", quantile=0.25)
    assert asyncio.run(b.query_rag("alpha beta gamma")) == ""

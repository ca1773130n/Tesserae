"""Pins for the Novel Grounded Evidence (NGE) abstention signal.

NGE exists because the obvious version of it does not work. *Extractive
support* — how much of an answer's vocabulary appears in the retrieved
sources — was measured on 352 persisted answers and scored AUC 0.587 at
separating fabrications from correct answers, barely above chance. The reason
is structural rather than incidental: retrieval selected those documents **by
the question's terms**, so an answer that merely restates the question is
guaranteed to look fully supported. Measured question-echo fraction is 0.49
for fabrications against 0.35 for correct answers, so the confound does not
average out — it points the wrong way.

Subtracting the question's own vocabulary is the whole mechanism, and it is
what these tests pin. Everything here is arithmetic over three strings that
already exist at answer time; no LLM, no graph, no network.
"""

from __future__ import annotations

import math

import pytest

from tesserae.retrieval.grounding import (
    corpus_idf,
    grounding_tau,
    novel_grounded_evidence,
)

# The benchmark arms import the VENDORED cognee QA base, which is gitignored and
# absent on a CI runner. Their imports here are function-local, so a missing
# prerequisite surfaced as four test FAILURES rather than a collection error —
# which is what it did on PR #213. `tests/test_qa_retrieval_arm.py` already
# carries this idiom for the same prerequisite; the difference is that the pure
# `tesserae.retrieval.grounding` tests in this file have no such dependency and
# must keep running, so the skip is per-test rather than module-level.
try:
    from evals.qa.vendor_base import load_qa_benchmark_base

    load_qa_benchmark_base()
    _VENDORED_BASE = None
except Exception as exc:  # pragma: no cover - environment-dependent
    _VENDORED_BASE = str(exc)

needs_vendored_base = pytest.mark.skipif(
    _VENDORED_BASE is not None,
    reason=f"vendored QA benchmark base unavailable: {_VENDORED_BASE}",
)


#: A bundle with a real frequency spread, because a corpus without one cannot
#: exercise idf: in five documents where every term is a hapax, four common
#: words outweigh one rare name and the test would pin the opposite of the
#: property it means to check. ``scene``, ``camera`` and ``optimisation``
#: appear nearly everywhere; ``nerfies``, ``barf`` and ``regulariser`` once.
SOURCES = [
    "Nerfies stabilises the scene with an elastic energy regulariser, "
    "optimising camera pose against the optimisation objective.",
    "BARF anneals positional encodings coarse to fine, optimising the scene "
    "and the camera together under the same optimisation.",
    "The scene representation is optimised jointly with camera pose, an "
    "optimisation over the scene and the camera.",
    "Photometric loss drives the optimisation of the scene and the camera "
    "pose in every one of these systems.",
    "A radiance field renders novel views of the scene from posed camera "
    "images, after the optimisation of pose.",
]


@pytest.fixture()
def bundle():
    idf, n_docs = corpus_idf(SOURCES)
    return idf, n_docs


def test_question_echoed_tokens_contribute_zero(bundle):
    """A token the question already carries adds nothing, however rare it is.

    This is the correction that turns a chance-level feature into a working
    one. ``regulariser`` is a hapax in the bundle and would otherwise dominate
    the score; because the question names it, it must not count.
    """
    idf, n = bundle
    question = "What does the elastic energy regulariser do?"
    echo_only = "The elastic energy regulariser."
    assert novel_grounded_evidence(echo_only, question, SOURCES, idf, n) == 0.0

    # The same answer scored against a question that does NOT name the term
    # keeps the term's full weight — proving the zero above came from the
    # subtraction, not from the token being unscoreable.
    other_q = "What stabilises the deformation field?"
    assert novel_grounded_evidence(echo_only, other_q, SOURCES, idf, n) > 0.0


def test_pure_question_restatement_scores_zero(bundle):
    """The fabrication signature: fluent, on-topic, and adds no vocabulary."""
    idf, n = bundle
    question = "How does the photometric loss drives the optimisation?"
    restatement = "The photometric loss drives the optimisation."
    assert novel_grounded_evidence(restatement, question, SOURCES, idf, n) == 0.0


def test_morphological_variants_are_not_matched(bundle):
    """KNOWN LIMITATION, pinned rather than hidden: there is no stemmer.

    "drive" in the question does not cancel "drives" in the answer, so a
    restatement that inflects a verb scores as if it had added evidence. The
    surface-form comparison is deliberate — a stemmer is a dependency, a
    language assumption, and a second thing to keep in step with the
    retriever's tokenizer — but the leak is real and bounds how tight the
    zero-restatement guarantee above actually is.
    """
    idf, n = bundle
    question = "How does the photometric loss drive the optimisation?"
    restatement = "The photometric loss drives the optimisation."
    assert novel_grounded_evidence(restatement, question, SOURCES, idf, n) > 0.0


def test_hapax_entity_from_the_sources_dominates(bundle):
    """A rare, source-attested name outweighs a pile of common vocabulary."""
    idf, n = bundle
    question = "Which system anneals its encodings?"
    rare = novel_grounded_evidence("BARF", question, SOURCES, idf, n)
    common = novel_grounded_evidence(
        "the scene camera pose optimisation", question, SOURCES, idf, n
    )
    assert rare > 0.0
    assert rare > common


def test_vocabulary_absent_from_the_sources_contributes_zero(bundle):
    """Invented rare words score nothing — that is what "grounded" means."""
    idf, n = bundle
    question = "Which system anneals its encodings?"
    grounded = novel_grounded_evidence("BARF", question, SOURCES, idf, n)
    invented = novel_grounded_evidence("Flombulator", question, SOURCES, idf, n)
    assert invented == 0.0
    assert grounded > invented


def test_repeats_and_stopwords_do_not_inflate_the_score(bundle):
    """Each novel type counts once; stopwords and short tokens never count."""
    idf, n = bundle
    question = "Which system anneals its encodings?"
    once = novel_grounded_evidence("BARF", question, SOURCES, idf, n)
    thrice = novel_grounded_evidence("BARF BARF BARF", question, SOURCES, idf, n)
    assert thrice == pytest.approx(once)

    padded = novel_grounded_evidence(
        "It is the one that was in the same one of these", question, SOURCES, idf, n
    )  # every token a stopword or shorter than three characters
    assert padded == 0.0


def test_no_sources_means_nothing_is_grounded(bundle):
    idf, n = bundle
    assert novel_grounded_evidence("BARF", "Which system?", [], idf, n) == 0.0
    assert novel_grounded_evidence("", "Which system?", SOURCES, idf, n) == 0.0


def test_idf_is_the_bm25_idf_of_the_bundle():
    """Rarity is measured over the bundle actually shown to the model."""
    idf, n = corpus_idf(SOURCES)
    assert n == len(SOURCES)
    # "scene" appears in all five documents; "barf" in one.
    assert idf["barf"] > idf["scene"]
    assert idf["barf"] == pytest.approx(math.log(1 + (5 - 1 + 0.5) / (1 + 0.5)))


def test_tau_as_an_idf_quantile_is_scale_invariant():
    """tau must be a quantile of THIS bundle's idf, never a constant.

    An absolute threshold tuned on a 135-document corpus means something else
    entirely on a 62k-node graph, because BM25 idf grows with ``log(N)``: a
    hapax is worth ~4.5 in the small corpus and ~10.6 in the large one. The
    same absolute tau therefore slides from two thirds of one rare word to
    under a third of one — the gate quietly loosens on exactly the corpus that
    matters. A quantile of the bundle's own idf distribution tracks the scale
    instead.
    """
    def zipfish(n_docs: int):
        """A corpus with a common core, a mid band, and a hapax tail."""
        docs = []
        for i in range(n_docs):
            words = ["common", f"uniqA{i}", f"uniqB{i}"]
            words += [f"band{j}" for j in range(2, 12) if i % j == 0]
            docs.append(" ".join(words))
        return docs

    idf_small, n_small = corpus_idf(zipfish(135))
    idf_large, n_large = corpus_idf(zipfish(62_000))

    tau_small = grounding_tau(idf_small)
    tau_large = grounding_tau(idf_large)

    # The absolute value moves with the corpus...
    assert tau_large > tau_small
    # ...but its meaning does not: tau stays the same fraction of what a
    # single hapax is worth, within a few percent.
    hapax_small = math.log(1 + (n_small - 0.5) / 1.5)
    hapax_large = math.log(1 + (n_large - 0.5) / 1.5)
    assert tau_small / hapax_small == pytest.approx(
        tau_large / hapax_large, abs=0.05
    )

    # And the constant that was tuned offline does NOT transfer: 3.0 is a
    # substantial share of a hapax in the small corpus and noise in the large.
    assert 3.0 / hapax_small > 2 * (3.0 / hapax_large)


def test_grounding_tau_is_monotone_in_its_quantile():
    idf, _ = corpus_idf(SOURCES)
    taus = [grounding_tau(idf, quantile=q) for q in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert taus == sorted(taus)
    assert grounding_tau({}, quantile=0.5) == 0.0


@needs_vendored_base
def test_gate_is_opt_in_for_both_benchmark_arms():
    """An eval-only behaviour must never become the product default.

    This repo has reverted a change that made one. Both benchmark configs
    therefore default ``grounding_quantile`` to ``None`` = today's behaviour, and
    ``tesserae.query.QueryResult`` reports the number without acting on it.
    """
    from dataclasses import fields

    from evals.qa.benchmark_retrieval import RetrievalConfig
    from evals.qa.benchmark_tesserae import TesseraeConfig
    from tesserae.query import QueryResult

    for config in (TesseraeConfig, RetrievalConfig):
        declared = {f.name: f for f in fields(config)}
        # A QUANTILE, not an absolute tau: the constant that gates correctly
        # over 135 documents is noise over 62k, so the knob that ships has to
        # be the scale-free one.
        assert "grounding_quantile" in declared, config.__name__
        assert declared["grounding_quantile"].default is None, config.__name__

    result = QueryResult(
        question="q", hits=[], answer="a", model=None, used_llm=False,
        fallback_reason=None,
    )
    assert result.grounding is None
    assert result.to_dict()["grounding"] is None


# ------------------------------------------------- the Tesserae arm's gate


def _tesserae_arm(quantile=None):
    from evals.qa.benchmark_tesserae import QABenchmarkTesserae, TesseraeConfig

    docs = ["alpha beta gamma delta zephyrine corpus filler text"] + [
        f"alpha beta gamma delta rare{i} corpus filler text" for i in range(19)
    ]
    return QABenchmarkTesserae(
        docs, [], TesseraeConfig(project_root="/nonexistent",
                                 grounding_quantile=quantile)
    )


@needs_vendored_base
def test_the_tesserae_arm_prefers_the_score_the_query_layer_already_computed():
    """Recomputing from excerpts measures a different bundle than the model read.

    ``tesserae.query`` scores against the page bodies it actually pasted into
    the prompt and hands the number back on the envelope. The arm must use
    that one; the excerpt-based recomputation is a fallback for backends that
    do not carry it.
    """
    arm = _tesserae_arm(quantile=0.25)
    idf, n_docs = arm._corpus_idf()
    tau = grounding_tau(idf, 0.25)

    # An envelope whose own score is above tau passes, even though the excerpt
    # it carries is a pure restatement that would fail on recomputation.
    rich = {"grounding": tau + 1.0, "hits": [{"excerpt": "alpha beta gamma"}]}
    assert arm._below_grounding_gate("alpha beta gamma", "alpha beta", rich) is False

    poor = {"grounding": 0.0, "hits": [{"excerpt": "alpha zephyrine beta"}]}
    assert arm._below_grounding_gate("alpha beta", "zephyrine", poor) is True


@needs_vendored_base
def test_a_missing_score_does_not_gate_on_a_substitute():
    """No score means DO NOT GATE — never "gate on whatever is to hand".

    There was a fallback here that recomputed the score from ``hit["excerpt"]``,
    200-character clips, while the planner had pasted 4,000-character source
    documents into the prompt. Every one of the 352 benchmark questions routes
    through the planner, whose envelope did not then carry a score, so the
    fallback ran on ALL of them and refused 71.1% of ANSWERABLE questions:
    Youden J +0.289 against +0.505 for not gating at all. The gate made the
    product worse while its unit tests passed, because they exercised the branch
    that never executed.

    A restatement with no score must pass, however obviously ungrounded it looks
    on the excerpts — the arm has nothing to judge it with.
    """
    arm = _tesserae_arm(quantile=0.25)
    hits = [{"excerpt": "alpha beta gamma delta zephyrine corpus filler text"}]
    assert arm._below_grounding_gate(
        "alpha beta gamma", "alpha beta gamma", {"hits": hits}
    ) is False
    assert arm._below_grounding_gate("q", "a", {}) is False
    assert arm._below_grounding_gate("q", "a", None) is False


def test_the_planner_envelope_carries_a_grounding_score():
    """The planner scores its OWN message, so the gate has something real to
    read on the path every benchmark question actually takes."""
    from tesserae.ask_planner import source_blocks_of

    message = (
        'preamble\n<source kind="wiki" node_id="n1">alpha zephyrine beta</source>\n'
        '<source kind="kg:x" node_id="n2">gamma delta</source>\n'
    )
    blocks = source_blocks_of(message)
    assert blocks == ["alpha zephyrine beta", "gamma delta"]
    assert source_blocks_of("") == []
    assert source_blocks_of("no sources here") == []


@needs_vendored_base
def test_the_tesserae_arm_measures_rarity_over_the_corpus_not_the_bundle():
    """A single shown document caps BM25 idf near 0.9 — every term looks alike.

    Rarity has to come from the whole corpus or the score stops
    discriminating, which is why ``_corpus_idf`` exists separately from the
    per-question source list.
    """
    arm = _tesserae_arm(quantile=0.25)
    idf, n_docs = arm._corpus_idf()
    assert n_docs == 20
    assert idf["zephyrine"] > idf["alpha"]
    bundle_only, _ = corpus_idf(["alpha beta gamma delta zephyrine"])
    assert bundle_only["zephyrine"] == bundle_only["alpha"], (
        "a one-document bundle cannot rank rarity — this is the degenerate "
        "case _corpus_idf exists to avoid"
    )

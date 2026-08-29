"""The evidence check: did an answer say things its evidence does not contain?

Validated against a negative control rather than intuition — the same answer
scored against its own evidence and against a different question's separated at
AUC 0.908, and the threshold is the operating point on that curve.
"""

from __future__ import annotations

from tesserae.verify_answer import (DEFAULT_COVERAGE, NO_CONTENT, SUPPORTED,
                                    UNSUPPORTED, check_against_evidence,
                                    content_words, separation, split_sentences)

EVIDENCE = ("ResNet-50 was evaluated on the ImageNet benchmark and reached "
            "76.1 top-1 accuracy under a single-crop protocol.")


def test_a_sentence_built_from_its_evidence_is_supported():
    r = check_against_evidence("ResNet-50 reached 76.1 top-1 accuracy on ImageNet.", EVIDENCE)
    assert r.sentences[0].verdict == SUPPORTED


def test_a_sentence_the_evidence_never_mentions_is_flagged():
    """The flag. Every content word here is absent from the evidence."""
    r = check_against_evidence("It outperformed DenseNet on CIFAR-100 segmentation.", EVIDENCE)
    s = r.sentences[0]
    assert s.verdict == UNSUPPORTED
    assert "densenet" in s.missing and "cifar-100" in s.missing


def test_the_flag_names_what_is_missing():
    """A flag that cannot say WHY is an accusation, not a check."""
    r = check_against_evidence(
        "DenseNet applied dropout regularisation throughout.", EVIDENCE)
    assert r.sentences[0].missing, "an UNSUPPORTED verdict must name the absent words"


def test_a_sentence_too_short_to_judge_is_not_flagged():
    """`MIN_CONTENT_WORDS` has a real cost: "DenseNet used dropout" carries only
    two content words ("used" is a stopword) and escapes checking, so a short
    fabrication can pass. The guard exists because without it "It is important."
    scores 0.0 and reads as invented — and a false accusation costs more than
    this miss. The threshold was measured with this guard in place; changing it
    invalidates that curve."""
    r = check_against_evidence("DenseNet used dropout.", EVIDENCE)
    assert r.sentences[0].verdict == NO_CONTENT


def test_framing_is_not_a_claim():
    """'This is important.' would score 0.0 and be flagged as fabricated."""
    r = check_against_evidence("This is important.", EVIDENCE)
    assert r.sentences[0].verdict == NO_CONTENT
    assert r.checkable == 0


def test_the_rate_is_over_checkable_sentences_only():
    """A rate over ALL sentences would let an answer look grounded by padding
    itself with framing."""
    r = check_against_evidence(
        "ResNet-50 reached 76.1 top-1 accuracy on ImageNet. It is notable. Truly.",
        EVIDENCE)
    assert r.checkable == 1 and r.supported_rate == 1.0


def test_it_is_deterministic():
    a = check_against_evidence("ResNet-50 reached 76.1 accuracy.", EVIDENCE)
    b = check_against_evidence("ResNet-50 reached 76.1 accuracy.", EVIDENCE)
    assert [(s.verdict, s.coverage) for s in a.sentences] == \
           [(s.verdict, s.coverage) for s in b.sentences]


def test_empty_evidence_flags_every_claim():
    """The degenerate case must not read as 'all supported'."""
    r = check_against_evidence("ResNet-50 reached 76.1 top-1 accuracy on ImageNet.", "")
    assert r.sentences[0].verdict == UNSUPPORTED


def test_separation_reports_a_coin_as_a_coin():
    same = [0.5] * 10
    assert separation(same, same)["auc"] == 0.5
    assert separation([1.0] * 5, [0.0] * 5)["auc"] == 1.0


def test_the_default_threshold_is_the_measured_operating_point():
    """0.70 was the taste-based first guess and would flag HALF of correct
    output (50.7% on own evidence). 0.50 flags 11.3% and catches 78.5%."""
    assert DEFAULT_COVERAGE == 0.50


def test_stopwords_do_not_carry_a_claim():
    assert "the" not in content_words("the model")
    assert "model" in content_words("the model")


def test_sentence_splitting_survives_an_empty_answer():
    assert split_sentences("") == []
    assert check_against_evidence("", EVIDENCE).sentences == ()


# --------------------------------------------------------- the cascade ---
# The band exists because the check and a model fail on DIFFERENT sentences.
# These pin the contract that makes that safe to exploit: a judge may improve a
# verdict, never erase one, and may never be reached for a sentence the check
# was already confident about.

def _judge_saying(verdict):
    calls = []

    def judge(sentence, evidence):
        calls.append(sentence)
        return verdict

    return judge, calls


def test_adjudicate_replaces_a_verdict_inside_the_band():
    from tesserae.verify_answer import adjudicate_uncertain

    r = check_against_evidence("ResNet-50 reached 76.1 accuracy on ImageNet.",
                               "ResNet-50 was evaluated on ImageNet.")
    assert r.sentences[0].verdict == UNSUPPORTED
    assert 0.30 <= r.sentences[0].coverage <= 0.70, "fixture must sit in the band"
    judge, calls = _judge_saying(SUPPORTED)
    out = adjudicate_uncertain(r, "evidence", judge)
    assert out.sentences[0].verdict == SUPPORTED
    assert out.sentences[0].adjudicated is True
    assert len(calls) == 1


def test_adjudicate_never_pays_for_a_confident_sentence():
    from tesserae.verify_answer import adjudicate_uncertain

    r = check_against_evidence("ResNet-50 reached 76.1 top-1 accuracy on ImageNet.",
                               EVIDENCE)
    assert r.sentences[0].coverage > 0.70
    judge, calls = _judge_saying(UNSUPPORTED)
    out = adjudicate_uncertain(r, EVIDENCE, judge)
    assert calls == [], "a confident sentence must not cost a call"
    assert out.sentences[0].verdict == r.sentences[0].verdict
    assert out.sentences[0].adjudicated is False


def test_a_judge_that_cannot_answer_leaves_the_verdict_standing():
    """A failed call must never be able to turn a flagged sentence clean."""
    from tesserae.verify_answer import adjudicate_uncertain

    r = check_against_evidence("ResNet-50 reached 76.1 accuracy on ImageNet.",
                               "ResNet-50 was evaluated on ImageNet.")
    for judge in (lambda s, e: None,
                  lambda s, e: "MAYBE",
                  lambda s, e: (_ for _ in ()).throw(RuntimeError("no network"))):
        out = adjudicate_uncertain(r, "evidence", judge)
        assert out.sentences[0].verdict == UNSUPPORTED
        assert out.sentences[0].adjudicated is False


def test_adjudicate_leaves_sentences_with_no_claim_alone():
    from tesserae.verify_answer import adjudicate_uncertain

    r = check_against_evidence("This is important.", EVIDENCE)
    assert r.sentences[0].verdict == NO_CONTENT
    judge, calls = _judge_saying(UNSUPPORTED)
    assert adjudicate_uncertain(r, EVIDENCE, judge).sentences[0].verdict == NO_CONTENT
    assert calls == []


def test_the_band_is_the_measured_one():
    from tesserae.verify_answer import UNCERTAIN_HIGH, UNCERTAIN_LOW

    assert (UNCERTAIN_LOW, UNCERTAIN_HIGH) == (0.30, 0.70)
    assert UNCERTAIN_LOW < DEFAULT_COVERAGE < UNCERTAIN_HIGH, \
        "the band must straddle the threshold it exists to second-guess"

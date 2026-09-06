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


# --- attribution: the figure is real, the sentence is grounded, the OWNER is wrong -----
#
# Coverage checking passes a misattributed figure — every token of "SystemX scored
# 91.4 on BenchY" is in the evidence when 91.4 sits in SystemZ's row. Built and
# audited 2026-09-01 (handoff §4.6): 14/15 hallucinations caught across both
# arms, 4 false alarms in 33 true answers, verdicts byte-identical with the
# graph absent. Ownership lives in the RECORD (the \n\n-delimited table row or
# paragraph) and the benchmark in the packed document ("[Title]\n..." block).

ATTR_EVIDENCE = (
    "[Paper Alpha]\nWe evaluate every method on BenchY under the standard split.\n\n"
    "| Method | BenchY |\n| SystemZ | 91.4 |\n\n"
    "| SystemX | 88.0 |\n\n"
    "[Paper Beta]\nSystemX reaches 77.2 on BenchQ in our runs.\n\n"
    "[Paper Gamma]\nSystemX is our model. On BenchY, our method achieves 93.1.\n"
)


def _attr(answer, subject="SystemX", obj="BenchY", evidence=ATTR_EVIDENCE):
    from tesserae.verify_answer import check_attribution
    return check_attribution(answer, evidence, subject=subject, obj=obj)


def test_a_figure_in_the_subjects_own_record_is_attributed():
    r = _attr("SystemX scores 88.0 on BenchY.")
    assert r["flagged"] is False and r["reason"] == "attributed"
    assert r["detail"]["checked_figures"] == ["88.0"]


def test_a_real_figure_lifted_from_another_systems_row_is_flagged():
    """The coverage check cannot see this: every token is in the evidence."""
    r = _attr("SystemX scores 91.4 on BenchY.")
    assert r["flagged"] is True and r["reason"] == "figure_attributed_to_other_subject"
    fig = r["detail"]["figures"][0]
    assert fig["subject_local"] is False and "SystemZ" in fig["competing"]


def test_right_system_wrong_benchmark_is_flagged():
    """The subject-only shape misses this one: SystemX's real BenchQ number,
    re-labelled BenchY. The paper that holds the record never says BenchY."""
    r = _attr("SystemX scores 77.2 on BenchY.")
    assert r["flagged"] is True and r["reason"] == "benchmark_absent_from_source_paper"


def test_a_figure_absent_from_the_evidence_is_the_worst_verdict():
    r = _attr("SystemX scores 99.9 on BenchY.")
    assert r["flagged"] is True and r["reason"] == "figure_absent_from_evidence"


def test_an_answer_without_a_figure_is_not_checkable_and_not_flagged():
    r = _attr("SystemX is strong on BenchY.")
    assert r["flagged"] is False and r["reason"] == "no_checkable_figure"


def test_self_reference_binds_our_to_the_papers_own_system():
    """A results row labelled "our method" belongs to the system the paper is
    about; naive record matching would call it somebody else's."""
    r = _attr("SystemX scores 93.1 on BenchY.")
    assert r["flagged"] is False and r["reason"] == "attributed"
    assert r["detail"]["figures"][0]["self_referential"] is True


def test_the_worst_figure_decides_and_is_named():
    r = _attr("SystemX scores 88.0 on BenchY, up from 91.4 last year.")
    assert r["flagged"] is True and r["reason"] == "figure_attributed_to_other_subject"
    assert r["detail"]["deciding_figure"] == "91.4"


def test_a_name_with_no_identity_tokens_is_uncheckable_not_flagged():
    """Audit probe P6: a name that vanishes under generic-token stripping
    matches nothing, so the un-audited script auto-flagged it. Refuse instead."""
    r = _attr("The dataset scores 88.0 on BenchY.", subject="The Dataset")
    assert r["flagged"] is False and r["reason"] == "subject_name_uncheckable"
    r = _attr("SystemX scores 88.0 on the benchmark.", obj="The Benchmark")
    assert r["flagged"] is False and r["reason"] == "benchmark_name_uncheckable"


def test_verify_attribution_is_an_mcp_tool_and_a_cli_verb(capsys):
    """Agents reach it over MCP; Agented reaches it over the CLI — same bytes."""
    import json

    import tesserae.cli as cli
    from tesserae.cli import _NEW_DISPATCH
    from tesserae.cli_tree import KNOWN_COMMANDS
    from tesserae.mcp_server import LLMWikiMCPServer

    server = LLMWikiMCPServer()
    assert "verify_attribution" in {t["name"] for t in server.list_tools()}
    from_mcp = server.call_tool("verify_attribution", {
        "answer": "SystemX scores 91.4 on BenchY.", "evidence": ATTR_EVIDENCE,
        "subject": "SystemX", "object": "BenchY"})
    assert from_mcp["flagged"] is True

    assert "verify-attribution" in _NEW_DISPATCH and "verify-attribution" in KNOWN_COMMANDS
    rc = cli.main(["verify-attribution", "-s", "SystemX", "-o", "BenchY",
                   "--answer", "SystemX scores 91.4 on BenchY.", "--evidence", ATTR_EVIDENCE])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == from_mcp

    rc = cli.main(["verify-attribution", "-s", "The Dataset", "-o", "BenchY",
                   "--answer", "It scores 88.0.", "--evidence", ATTR_EVIDENCE])
    assert rc == 2, "could-not-check is the only non-zero exit, as for verify-claim"
    assert json.loads(capsys.readouterr().out)["reason"] == "subject_name_uncheckable"

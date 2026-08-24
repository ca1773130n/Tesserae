"""LoCoMo retrieval — gold alignment, the random floor, and the K set.

Offline, synthetic and deterministic: no dataset file, no compile, no model, no
network. This is the half of the benchmark that reproduces exactly, so it is
also the half that can be asserted to four decimals — and is.

What is pinned:

* evidence resolves by ``dia_id`` lookup, and everything that does NOT resolve
  is counted rather than repaired or dropped;
* a question with no gold is excluded from the metrics, never scored zero;
* the random-ranker floor is exact arithmetic, so a reader can see how much of a
  recall@10 on a 19-session conversation is the corpus being small;
* the reported K set is frozen in code, not chosen after the fact.
"""

from __future__ import annotations

import pytest

from evals.locomo.dataset import Conversation, LocomoQuestion, LocomoSession, Turn
from evals.locomo.retrieval import (
    PROTOCOL_KS,
    RefusedToAlignGold,
    align_gold,
    alignment_summary,
    floors_for_rows,
    random_recall_floor,
    random_rr_floor,
    require_ks,
    retrieval_rows,
    score_at_ks,
    verify_dia_ids,
)


def _conversation(questions, *, sessions: int = 3, sample_id: str = "conv-test"):
    return Conversation(
        sample_id=sample_id, speaker_a="Ada", speaker_b="Bo",
        sessions=[
            LocomoSession(number=n, date="noon", turns=[
                Turn(dia_id=f"D{n}:{t}", session=n, speaker="Ada",
                     text=f"session {n} turn {t}")
                for t in range(1, 4)
            ])
            for n in range(1, sessions + 1)
        ],
        questions=questions,
    )


def _question(evidence, category: int = 4, answer="x"):
    return LocomoQuestion(question="What?", category=category, evidence=evidence,
                          conversation="conv-test", answer=answer)


# ------------------------------------------------------------------ alignment


def test_evidence_resolves_to_the_session_its_dia_id_names():
    alignment = align_gold(_conversation([_question(["D2:1"])]))
    assert alignment.gold == [[2]]


def test_multi_session_gold_keeps_first_seen_order_without_repeats():
    alignment = align_gold(_conversation([_question(["D3:1", "D1:2", "D3:2"])]))
    assert alignment.gold == [[3, 1]]


def test_a_malformed_string_is_counted_and_still_resolved_where_it_can_be():
    alignment = align_gold(_conversation([_question(["D1:1; D2:1"])]))
    assert alignment.gold == [[1, 2]]
    assert alignment.n_malformed == 1


def test_an_unreadable_annotation_resolves_to_nothing_and_is_counted():
    alignment = align_gold(_conversation([_question(["D"])]))
    assert alignment.gold == [[]]
    assert alignment.n_unparseable == 1
    assert alignment.n_no_gold == 1


def test_an_id_naming_a_turn_that_does_not_exist_is_counted_as_dangling():
    alignment = align_gold(_conversation([_question(["D2:99"])]))
    assert alignment.gold == [[]]
    assert alignment.n_dangling == 1


def test_an_id_naming_a_session_that_does_not_exist_is_counted_as_dangling():
    alignment = align_gold(_conversation([_question(["D9:1"])], sessions=3))
    assert alignment.n_dangling == 1


def test_empty_evidence_is_counted_and_leaves_the_question_without_gold():
    alignment = align_gold(_conversation([_question([])]))
    assert alignment.n_empty_evidence == 1
    assert alignment.n_no_gold == 1


def test_alignment_refuses_when_a_dia_id_disagrees_with_its_session():
    """The whole lookup strategy rests on ``D<n>`` meaning ``session_n``.

    Measured over all 5,882 turns of the shipped file it holds without
    exception, so this fires on a change in the data and never on a threshold.
    """
    broken = Conversation(
        sample_id="conv-broken", speaker_a="", speaker_b="",
        sessions=[LocomoSession(number=1, date="", turns=[
            Turn(dia_id="D7:1", session=1, speaker="Ada", text="mislabelled")])],
        questions=[_question(["D7:1"])],
    )
    with pytest.raises(RefusedToAlignGold):
        verify_dia_ids(broken)
    with pytest.raises(RefusedToAlignGold):
        align_gold(broken)


def test_the_summary_sums_every_dirt_count():
    alignments = [align_gold(_conversation([_question(["D"]), _question([])])),
                  align_gold(_conversation([_question(["D1:1; D2:1"])]))]
    # "D" is counted twice over, in two different columns: it is malformed
    # (not one clean id) and it is unparseable (no id could be read from it).
    # The columns answer different questions and neither subsumes the other.
    assert alignment_summary(alignments) == {
        "n_questions": 3, "n_no_gold": 2, "n_empty_evidence": 1,
        "n_malformed": 2, "n_unparseable": 1, "n_dangling": 0,
    }


# ------------------------------------------------------------------- scoring


def _rows(gold, retrieved, *, candidates: int = 20, category: int = 4):
    return [{"question": "q", "stratum": "single-hop", "category": category,
             "conversation": "conv-test", "n_candidates": candidates,
             "gold": gold, "retrieved": retrieved}]


def test_a_question_with_no_gold_is_excluded_and_not_scored_zero():
    """Zero is a claim the arm failed to retrieve something that was never there."""
    reports = score_at_ks(_rows([], [1, 2]), system="X", ks=(5,))
    assert reports[0]["overall"]["n"] == 1
    assert reports[0]["overall"]["n_scored"] == 0
    assert reports[0]["overall"]["n_no_gold"] == 1


def test_recall_and_rr_are_exact():
    reports = score_at_ks(_rows([7], [3, 7, 9]), system="X", ks=(3,))
    overall = reports[0]["overall"]
    assert overall["recall_at_k"] == 1.0
    assert overall["mrr"] == pytest.approx(0.5)


def test_a_hit_outside_the_budget_does_not_count():
    reports = score_at_ks(_rows([9], [1, 2, 9]), system="X", ks=(2, 3))
    assert reports[0]["k"] == 2 and reports[0]["overall"]["recall_at_k"] == 0.0
    assert reports[1]["k"] == 3 and reports[1]["overall"]["recall_at_k"] == 1.0


def test_multi_gold_recall_is_capped_by_the_budget_not_penalised_by_it():
    """Three golds at K=2 cannot all be retrieved; scoring 0.67 for perfect
    retrieval would penalise the arm for the budget rather than the ranking."""
    reports = score_at_ks(_rows([1, 2, 3], [1, 2]), system="X", ks=(2,))
    assert reports[0]["overall"]["recall_at_k"] == 1.0


def test_every_report_carries_its_random_floor():
    reports = score_at_ks(_rows([1], [1]), system="X", ks=PROTOCOL_KS)
    assert [r["k"] for r in reports] == list(PROTOCOL_KS)
    assert all("random_floor" in r for r in reports)


# ---------------------------------------------------------------- the floors


def test_the_recall_floor_is_the_share_of_the_corpus_the_budget_covers():
    """On a 19-session conversation, a coin already scores 0.526 at K=10.

    This is the column that stops a recall@10 near 0.6 reading as a result.
    """
    assert random_recall_floor(19, 1, 10) == pytest.approx(10 / 19)
    assert random_recall_floor(19, 1, 1) == pytest.approx(1 / 19)


def test_the_recall_floor_never_exceeds_one():
    assert random_recall_floor(5, 1, 10) == 1.0


def test_the_recall_floor_is_zero_without_gold_or_budget():
    assert random_recall_floor(19, 0, 10) == 0.0
    assert random_recall_floor(19, 1, 0) == 0.0
    assert random_recall_floor(0, 1, 10) == 0.0


def test_the_rr_floor_is_the_exact_expectation_for_a_single_gold():
    """``E[RR] = (1/N) * sum(1/r)`` over the ranks inside the budget."""
    expected = sum(1.0 / r for r in range(1, 11)) / 19
    assert random_rr_floor(19, 1, 10) == pytest.approx(expected)


def test_the_rr_floor_rises_with_more_gold():
    assert random_rr_floor(19, 3, 10) > random_rr_floor(19, 1, 10)


def test_the_floor_is_averaged_over_the_same_rows_the_metric_is():
    """A floor from the mean conversation size is a different quantity.

    Two conversations of different lengths have different floors, and the report
    compares a macro mean against a macro mean.
    """
    rows = _rows([1], [1], candidates=19) + _rows([1], [1], candidates=32)
    floor = floors_for_rows(rows, k=10)
    assert floor["n"] == 2
    assert floor["recall_at_k"] == pytest.approx((10 / 19 + 10 / 32) / 2)


def test_rows_without_gold_are_excluded_from_the_floor_too():
    rows = _rows([1], [1], candidates=19) + _rows([], [], candidates=19)
    assert floors_for_rows(rows, k=10)["n"] == 1


# ------------------------------------------------------------------- the set


def test_the_reported_k_set_is_frozen():
    """Fixed in code before any result existed. Choosing K after seeing results
    is the thing this benchmark must not do."""
    assert PROTOCOL_KS == (1, 2, 3, 5, 10)
    assert require_ks(None) == PROTOCOL_KS


def test_a_narrowed_set_is_allowed_sorted_and_de_duplicated():
    assert require_ks([10, 1, 10]) == (1, 10)


@pytest.mark.parametrize("bad", [[0], [-1], ["x"], []])
def test_an_unusable_k_refuses_by_name(bad):
    from evals.qa.run_qa_eval import Skip

    with pytest.raises(Skip):
        require_ks(bad)


# --------------------------------------------------------------------- rows


def test_rows_carry_the_candidate_count_and_the_category_name():
    conversation = _conversation([_question(["D2:1"], category=2)], sessions=4)
    alignment = align_gold(conversation)
    rows = retrieval_rows(conversation, alignment, [[2, 1]])
    assert rows[0]["n_candidates"] == 4
    assert rows[0]["stratum"] == "temporal"
    assert rows[0]["gold"] == [2]
    assert rows[0]["retrieved"] == [2, 1]


def test_a_question_the_arm_never_reached_gets_an_empty_ranking():
    conversation = _conversation([_question(["D2:1"]), _question(["D1:1"])])
    rows = retrieval_rows(conversation, align_gold(conversation), [[2]])
    assert rows[1]["retrieved"] == []


def test_retrieval_scoring_is_reproducible_to_four_decimals():
    """The deterministic half of this benchmark, asserted as such.

    LongMemEval's retrieval arm reproduced BM25 to four decimals across four
    independent runs while its generative arm swung 0.043 token F1 between two
    identical configurations. The point of separating them is that this half can
    be pinned; pinning it here is what keeps that true.
    """
    rows = _rows([3], [1, 3], candidates=19) + _rows([5], [5], candidates=19)
    first = score_at_ks(rows, system="X", ks=(10,))[0]
    second = score_at_ks(rows, system="X", ks=(10,))[0]
    assert round(first["overall"]["mrr"], 4) == round(second["overall"]["mrr"], 4)
    assert round(first["overall"]["mrr"], 4) == 0.75
    assert round(first["overall"]["recall_at_k"], 4) == 1.0


# --------------------------------------------------------------------------
# The ratchet against the real answer key. Skips when it is not on this machine.
# --------------------------------------------------------------------------


def _real_data():
    from evals.locomo.run import DEFAULT_DATA

    return DEFAULT_DATA


@pytest.mark.skipif(not _real_data().is_file(), reason="locomo10.json not on disk")
def test_the_real_answer_keys_dirt_is_still_what_was_measured():
    """Every figure `align_gold`'s docstring cites, reproduced from the file.

    A benchmark that repairs an annotation quietly has changed its answer key,
    so the counts of what could NOT be resolved are part of the result and are
    pinned like one.
    """
    from evals.locomo.dataset import load_conversations

    conversations = load_conversations(_real_data())
    alignments = [align_gold(c) for c in conversations]
    assert alignment_summary(alignments) == {
        "n_questions": 1986,
        "n_no_gold": 4,
        "n_empty_evidence": 4,
        "n_malformed": 6,
        "n_unparseable": 2,
        "n_dangling": 2,
    }
    elements = sum(len(q.evidence) for c in conversations for q in c.questions)
    assert elements == 2815
    spanning = sum(1 for a in alignments for gold in a.gold if len(gold) > 1)
    assert spanning == 332      # the questions the two denominators differ on

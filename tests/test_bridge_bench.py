"""The bridge benchmark's statistic and metrics, which the R4 ship/no-ship rule reads."""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")

from evals.bridge.bridge_bench import metrics, paired_permutation_p  # noqa: E402


def test_exact_permutation_p_matches_the_two_sided_sign_test():
    # 6 wins, 0 losses: only the all-plus and all-minus flips are as extreme.
    assert paired_permutation_p([1] * 6 + [0] * 4, [0] * 6 + [0] * 4) == pytest.approx(2 / 64)
    # Ties carry no information and must not shrink p.
    assert paired_permutation_p([1, 0, 0], [0, 0, 0]) == 1.0
    assert paired_permutation_p([0.5, 0.5], [0.5, 0.5]) == 1.0


def test_monte_carlo_permutation_p_is_small_for_a_large_consistent_gain():
    a, b = [1.0] * 40, [0.0] * 40
    p = paired_permutation_p(a, b, n_perm=2000)
    assert 0 < p < 0.01


def test_monte_carlo_permutation_p_is_large_for_balanced_differences():
    a = [1.0, 0.0] * 30
    b = [0.0, 1.0] * 30
    assert paired_permutation_p(a, b, n_perm=2000) > 0.5


def test_metrics_split_bridge_doc_from_answer_recall():
    gold = ["owner.md", "second.md"]
    ranked = ["x.md", "y.md", "z.md", "w.md", "v.md", "second.md"]
    m = metrics(ranked, gold)
    assert m["bridge_doc@5"] == 0.0  # the most-mentioning gold doc is absent
    assert m["answer_R@10"] == 0.5
    assert m["hit@5"] == 0.0 and m["hit@10"] == 1.0

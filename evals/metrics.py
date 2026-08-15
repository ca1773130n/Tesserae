"""The one TP/FP/FN → precision/recall/F1 implementation the evals share.

``evals.federation.run_eval`` counts predicted vs gold cross-project *links*;
``evals.qa.scorer`` counts predicted vs gold answer *tokens*. Both are the same
arithmetic over a different set, and two evals that each define their own F1
stop being comparable the first time one of them picks a different convention
for an empty denominator. So it is written once, here, with no imports.

Conventions, fixed by what federation already shipped and measured:

* an empty predicted set (``tp + fp == 0``) scores precision **0.0**, not 1.0 —
  predicting nothing is not perfect precision;
* an empty gold set (``tp + fn == 0``) scores recall **0.0** for the same reason;
* ``f1`` is 0.0 when precision and recall are both 0, rather than NaN.

The degenerate "both sides empty" case is a *question about the task*, not about
the arithmetic — an empty answer to an empty gold answer is a match, an empty
answer to a real one is not — so callers decide it above this function. See
:func:`evals.qa.scorer.token_f1`.
"""

from __future__ import annotations

from typing import Dict


def prf1(tp: int, fp: int, fn: int) -> Dict[str, float]:
    """Precision, recall and F1 from raw counts.

    Returns the counts back alongside the rates so a caller can build a report
    row (and re-derive the aggregate) from a single dict.
    """
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


__all__ = ["prf1"]

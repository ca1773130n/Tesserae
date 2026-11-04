"""Test-retest agreement: is a stage's score a measurement or noise?

The hallucination metric this harness replaces agreed with itself 35% of the
time on re-run (below chance), so nothing it reported could be acted on. Every
stage here is therefore re-run N times and its per-ITEM labels compared:

* **Cohen's kappa** over every pair of runs, on the categorical labels
  (covered / omitted, correct / reversed / omitted, judge yes / no). The gate
  reads the MINIMUM pairwise kappa, not the mean: one run that disagrees with
  the others is exactly the instability being tested for.
* **Spearman's rho** over every pair of runs, on per-UNIT rates (one rate per
  session, per user, per question group) — whether the ranking of units is
  stable even where individual labels flip.

Acceptance rule (roadmap R1): ``min kappa >= 0.8`` → the stage may serve as a
gate; ``min kappa < 0.6`` → rejected as a gate; in between → diagnostic only.
Fewer than two runs cannot be judged and says so.

Pure Python, no numpy: these are a few thousand labels at most.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Dict, Hashable, List, Mapping, Optional, Sequence

GATE_KAPPA = 0.8
REJECT_KAPPA = 0.6


def cohen_kappa(a: Sequence[Hashable], b: Sequence[Hashable]) -> float:
    """Cohen's kappa for two equal-length label sequences.

    When both raters use one identical label throughout, expected agreement is
    1 and kappa is 0/0. That case is perfect agreement on a degenerate item
    set, returned as 1.0; :func:`test_retest` flags it as ``degenerate`` so a
    perfect score on a constant stage is never mistaken for evidence.
    """
    if len(a) != len(b):
        raise ValueError(f"label sequences differ in length: {len(a)} vs {len(b)}")
    n = len(a)
    if n == 0:
        raise ValueError("cohen_kappa needs at least one item")
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    count_a: Dict[Hashable, int] = {}
    count_b: Dict[Hashable, int] = {}
    for x in a:
        count_a[x] = count_a.get(x, 0) + 1
    for y in b:
        count_b[y] = count_b.get(y, 0) + 1
    expected = sum(count_a[k] * count_b.get(k, 0) for k in count_a) / (n * n)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def _average_ranks(values: Sequence[float]) -> List[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        mean_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = mean_rank
        i = j + 1
    return ranks


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Spearman's rho with average ranks for ties; ``None`` when either side
    is constant (rank correlation is undefined, not zero)."""
    if len(x) != len(y):
        raise ValueError(f"sequences differ in length: {len(x)} vs {len(y)}")
    if len(x) < 2:
        return None
    rx, ry = _average_ranks(x), _average_ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((p - mx) * (q - my) for p, q in zip(rx, ry))
    vx = sum((p - mx) ** 2 for p in rx)
    vy = sum((q - my) ** 2 for q in ry)
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy) ** 0.5


@dataclass(frozen=True)
class Agreement:
    runs: int
    items: int
    kappa_min: Optional[float]
    kappa_mean: Optional[float]
    spearman_min: Optional[float]
    spearman_mean: Optional[float]
    degenerate: bool
    verdict: str  # gate | diagnostic | rejected | insufficient-runs

    def to_dict(self) -> dict:
        def r(v: Optional[float]) -> Optional[float]:
            return None if v is None else round(v, 4)

        return {
            "runs": self.runs,
            "items": self.items,
            "kappa_min": r(self.kappa_min),
            "kappa_mean": r(self.kappa_mean),
            "spearman_min": r(self.spearman_min),
            "spearman_mean": r(self.spearman_mean),
            "degenerate": self.degenerate,
            "verdict": self.verdict,
            "rule": f"min pairwise kappa >= {GATE_KAPPA} gate; < {REJECT_KAPPA} rejected",
        }


def verdict_for(kappa_min: Optional[float], runs: int) -> str:
    if runs < 2 or kappa_min is None:
        return "insufficient-runs"
    if kappa_min >= GATE_KAPPA:
        return "gate"
    if kappa_min < REJECT_KAPPA:
        return "rejected"
    return "diagnostic"


def test_retest(
    labels_by_run: Sequence[Mapping[str, Hashable]],
    rates_by_run: Optional[Sequence[Mapping[str, float]]] = None,
) -> Agreement:
    """Agreement across runs of one stage.

    ``labels_by_run[i]`` maps item id → label for run ``i``; only items present
    in EVERY run are compared (a run that crashed on an item cannot agree or
    disagree about it). ``rates_by_run`` maps unit id → rate, likewise
    intersected, for the Spearman half.
    """
    runs = len(labels_by_run)
    if runs < 2:
        return Agreement(runs, 0, None, None, None, None, False, "insufficient-runs")
    common = sorted(set.intersection(*(set(r) for r in labels_by_run)))
    if not common:
        raise ValueError("the runs share no labelled items")
    kappas = []
    degenerate = False
    for left, right in combinations(labels_by_run, 2):
        a = [left[k] for k in common]
        b = [right[k] for k in common]
        if len(set(a)) == 1 and len(set(b)) == 1:
            degenerate = True
        kappas.append(cohen_kappa(a, b))
    rhos: List[float] = []
    if rates_by_run is not None and len(rates_by_run) == runs:
        units = sorted(set.intersection(*(set(r) for r in rates_by_run)))
        for left, right in combinations(rates_by_run, 2):
            rho = spearman([left[u] for u in units], [right[u] for u in units])
            if rho is not None:
                rhos.append(rho)
    kappa_min = min(kappas)
    return Agreement(
        runs=runs,
        items=len(common),
        kappa_min=kappa_min,
        kappa_mean=sum(kappas) / len(kappas),
        spearman_min=min(rhos) if rhos else None,
        spearman_mean=sum(rhos) / len(rhos) if rhos else None,
        degenerate=degenerate,
        verdict=verdict_for(kappa_min, runs),
    )


# pytest collects any callable named test_* that a test module imports; this
# one is API, not a test.
test_retest.__test__ = False  # type: ignore[attr-defined]

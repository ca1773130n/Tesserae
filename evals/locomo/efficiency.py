"""Accuracy at a fixed token budget, and the two scalars derived from it.

The primary quantity is a CURVE, not a scalar: accuracy as a function of the
token budget the arm was given. Three things are computed from it, in the order
a reader should trust them.

1. :func:`curve` — accuracy per (arm, rung), each point carrying its own
   denominator, its refusal count, its truncation count and the tokens actually
   spent. This is the result.
2. :func:`aulbc` — normalised area under the accuracy/log-budget curve. A single
   number for ranking arms, and a lossy one: it hides WHERE on the ladder an
   advantage sits, and it depends on the declared ladder, so two runs with
   different rungs produce two incomparable scalars. Both facts are printed
   beside it.
3. :func:`tokens_to_tau` — the cheapest rung reaching accuracy ``tau``, or
   CENSORED when no rung does. Censoring is returned, never imputed: an arm that
   never reaches tau has no T@tau, and substituting the largest rung would make
   a failure look like an expensive success.

**The pathology this module refuses to have.** "Correctness per 1,000 tokens" is
the obvious efficiency metric and it is unusable as a headline: an arm that
supplies no evidence spends the fewest tokens, so a ratio with tokens in the
denominator rewards silence without bound, and a ratio has no scale on which
"better" can be read. :func:`correctness_per_1k` exists — a reader will compute
it anyway — and it is guarded: it returns ``None`` rather than a number whenever
the denominator is zero or the arm answered nothing, and
:func:`dominates_by_ratio_only` names the case where the ratio ranks an arm
first that the accuracy curve ranks last. The report prints the flag next to the
ratio, so the ratio cannot be read alone.

Nothing here calls a model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class Point:
    """One arm at one rung. Every rate carries the denominator it is over."""

    arm: str
    budget: int
    n: int
    n_correct: int
    n_refused: int
    n_errored: int
    n_truncated: int
    score_sum: float
    token_sum: int
    #: The largest single request at this rung. A mean alone hides an arm whose
    #: fitting overshot on one question.
    max_tokens: int

    @property
    def accuracy(self) -> float:
        return self.n_correct / self.n if self.n else 0.0

    @property
    def graded_score(self) -> float:
        return self.score_sum / self.n if self.n else 0.0

    @property
    def mean_tokens(self) -> float:
        return self.token_sum / self.n if self.n else 0.0

    @property
    def refusal_rate(self) -> float:
        return self.n_refused / self.n if self.n else 0.0

    @property
    def over_budget(self) -> bool:
        """True when a request at this rung exceeded the rung it was fitted to.

        Never a warning to be read later: an arm that overshoots its budget is
        not measured at that budget, and the report prints this on the row.

        False for an unbudgeted control by construction: a floor and a ceiling
        were never given a budget, so they cannot have exceeded one, and
        flagging them would put a YES on the two rows where it means nothing.
        """
        return self.budget > UNBUDGETED and self.max_tokens > self.budget

    def as_dict(self) -> Dict[str, Any]:
        return {
            "arm": self.arm, "budget": self.budget, "n": self.n,
            "n_correct": self.n_correct, "n_refused": self.n_refused,
            "n_errored": self.n_errored, "n_truncated": self.n_truncated,
            "accuracy": self.accuracy, "graded_score": self.graded_score,
            "mean_tokens": self.mean_tokens, "max_tokens": self.max_tokens,
            "token_sum": self.token_sum, "refusal_rate": self.refusal_rate,
            "over_budget": self.over_budget,
        }


@dataclass
class Curve:
    """Every point, plus what could not be computed and why."""

    points: List[Point] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)

    @property
    def arms(self) -> List[str]:
        return sorted({p.arm for p in self.points})

    @property
    def budgets(self) -> List[int]:
        return sorted({p.budget for p in self.points})

    def for_arm(self, arm: str) -> List[Point]:
        return sorted((p for p in self.points if p.arm == arm),
                      key=lambda p: p.budget)

    def as_dict(self) -> Dict[str, Any]:
        return {"points": [p.as_dict() for p in self.points],
                "missing": list(self.missing)}


#: Rows whose budget is this are unbudgeted controls. They are kept out of every
#: scalar — a floor and a ceiling are not rungs of the ladder — and reported on
#: their own row.
UNBUDGETED = 0


def curve(rows: Sequence[Mapping[str, Any]]) -> Curve:
    """Group graded, token-counted rows into (arm, rung) points.

    ``rows`` are dicts carrying at least ``arm``, ``budget``, ``prompt_tokens``,
    ``correct``, ``score``, ``refused``, ``errored`` and ``truncated``. A row
    missing ``prompt_tokens`` is a row whose prompt was never counted, and that
    is refused rather than defaulted to zero: a missing count would enter every
    mean as free context.
    """
    result = Curve()
    if not rows:
        result.missing.append("no rows — there is no curve")
        return result
    uncounted = [r for r in rows if r.get("prompt_tokens") is None]
    if uncounted:
        result.missing.append(
            f"{len(uncounted)} of {len(rows)} rows carry no prompt_tokens; a "
            f"row whose request was never counted cannot enter a token mean")
        return result

    buckets: Dict[Tuple[str, int], List[Mapping[str, Any]]] = {}
    for row in rows:
        buckets.setdefault((str(row["arm"]), int(row["budget"])), []).append(row)
    for (arm, budget), group in sorted(buckets.items()):
        tokens = [int(r["prompt_tokens"]) for r in group]
        result.points.append(Point(
            arm=arm,
            budget=budget,
            n=len(group),
            n_correct=sum(1 for r in group if r.get("correct")),
            n_refused=sum(1 for r in group if r.get("refused")),
            n_errored=sum(1 for r in group if r.get("errored")),
            n_truncated=sum(1 for r in group if r.get("truncated")),
            score_sum=float(sum(float(r.get("score") or 0.0) for r in group)),
            token_sum=sum(tokens),
            max_tokens=max(tokens),
        ))
    return result


def aulbc(points: Sequence[Point]) -> Optional[float]:
    """Trapezoid area under accuracy vs log2(budget), normalised to [0, 1].

    ``None`` on fewer than two BUDGETED rungs — a single point has no area, and
    returning 0.0 there would rank a one-rung arm below every other arm for a
    reason that is not about the arm.
    """
    ladder = sorted((p for p in points if p.budget > UNBUDGETED),
                    key=lambda p: p.budget)
    if len(ladder) < 2:
        return None
    span = math.log2(ladder[-1].budget) - math.log2(ladder[0].budget)
    if span <= 0:
        return None
    area = 0.0
    for left, right in zip(ladder, ladder[1:]):
        width = math.log2(right.budget) - math.log2(left.budget)
        area += 0.5 * (left.accuracy + right.accuracy) * width
    return area / span


@dataclass(frozen=True)
class Tau:
    """The cheapest rung reaching ``tau``, or the censoring that says none did."""

    tau: float
    budget: Optional[int]
    censored: bool
    best_accuracy: float

    def as_dict(self) -> Dict[str, Any]:
        return {"tau": self.tau, "budget": self.budget,
                "censored": self.censored, "best_accuracy": self.best_accuracy}


def tokens_to_tau(points: Sequence[Point], tau: float) -> Tau:
    """The smallest budgeted rung whose accuracy is at least ``tau``."""
    ladder = sorted((p for p in points if p.budget > UNBUDGETED),
                    key=lambda p: p.budget)
    best = max((p.accuracy for p in ladder), default=0.0)
    for point in ladder:
        if point.accuracy >= tau:
            return Tau(tau=tau, budget=point.budget, censored=False,
                       best_accuracy=best)
    return Tau(tau=tau, budget=None, censored=True, best_accuracy=best)


def correctness_per_1k(point: Point) -> Optional[float]:
    """Correct answers per 1,000 prompt tokens, or ``None`` when meaningless.

    GUARDED, and not a headline. ``None`` when no tokens were spent (silence
    would otherwise score infinity) and when nothing was correct at zero cost.
    Read only beside :func:`curve`, and only with
    :func:`dominates_by_ratio_only` printed next to it.
    """
    if point.n == 0 or point.token_sum <= 0:
        return None
    return 1_000.0 * point.n_correct / point.token_sum


def dominates_by_ratio_only(points: Sequence[Point]) -> List[str]:
    """Arms the ratio ranks first that the accuracy curve does not.

    The list this returns is the reason the ratio is not the headline. Computed
    per rung, because an arm can win the ratio at one budget and lose it at
    another, and a single verdict would hide that.
    """
    flagged: List[str] = []
    by_budget: Dict[int, List[Point]] = {}
    for point in points:
        by_budget.setdefault(point.budget, []).append(point)
    for budget, group in sorted(by_budget.items()):
        ratios = [(correctness_per_1k(p), p) for p in group]
        usable = [(r, p) for r, p in ratios if r is not None]
        if len(usable) < 2:
            continue
        ratio_best = max(usable, key=lambda pair: pair[0])[1]
        accuracy_best = max(group, key=lambda p: (p.accuracy, -p.mean_tokens))
        if ratio_best.arm != accuracy_best.arm:
            flagged.append(
                f"at budget {budget}: correctness-per-1k ranks {ratio_best.arm} "
                f"first (accuracy {ratio_best.accuracy:.3f}) while accuracy "
                f"ranks {accuracy_best.arm} first "
                f"({accuracy_best.accuracy:.3f})")
    return flagged


def free_lunch(points: Sequence[Point], floor_arm: str = "closed_book"
               ) -> List[str]:
    """Arms that do not beat the no-evidence floor at the same question set.

    On LoCoMo the adversarial category's gold answer is a refusal, so an arm
    handed nothing scores that whole category. Any arm at or below the floor has
    demonstrated nothing about context, however good its absolute number looks.
    """
    floors = [p for p in points if p.arm == floor_arm]
    if not floors:
        return []
    floor = max(p.accuracy for p in floors)
    return [
        f"{p.arm} at budget {p.budget} scores {p.accuracy:.3f}, at or below the "
        f"{floor_arm} floor of {floor:.3f} — no evidence bought anything here"
        for p in points
        if p.arm != floor_arm and p.accuracy <= floor
    ]


__all__ = [
    "UNBUDGETED",
    "Curve",
    "Point",
    "Tau",
    "aulbc",
    "correctness_per_1k",
    "curve",
    "dominates_by_ratio_only",
    "free_lunch",
    "tokens_to_tau",
]

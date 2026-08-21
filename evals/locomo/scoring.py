"""The three-number contract, the adversarial control, and replicate spread.

Every rule in this module exists because a headline produced without it was
wrong in this repository, in a way nobody could see from the headline.

**Three numbers or none.** A gap of +0.077 on one corpus turned out to be 72%
the baseline REFUSING and scoring zero; the like-for-like subset was +0.021. A
gap of +0.0906 on another decomposed 99.3% answer-rate and 0.7% quality. Neither
was visible in the number that got quoted. So a report from this package prints
all three of

1. every scorable question, with a refusal scoring zero — the number the field
   quotes;
2. the subset every arm actually answered — the like-for-like comparison;
3. each arm's refusal and error counts — the thing that separates the two;

or it prints none of them and says which one it could not compute.
:func:`decompose` returns that decision as data rather than as prose.

**The adversarial category is never in the refusal table.** On LoCoMo's 446
category-5 questions a refusal IS the gold answer, so mixing them into a refusal
rate averages a virtue and a defect. They are scored in their own block, and
that block carries the warning that makes it readable: a wholly dead backbone
returning "" scores 446 of 446 there, so the adversarial number means nothing
without the answerable number beside it.

**Generative numbers get replicates.** Protocol B's grader defaults to three
runs and reports the mean and the spread across whole-run accuracies, and that
is copied here. A single generative number is not a measurement in this harness:
two identical configurations have differed by 0.043 token F1.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .dataset import ADVERSARIAL_CATEGORY, JUDGED_CATEGORIES, LocomoQuestion
from .judge import Judge, Verdict


def question_key(question: LocomoQuestion, index: int) -> str:
    """A stable identity for one question, across arms and replicates.

    ``<conversation>#<index>`` and not the question text. Measured this phase on
    the shipped file: twelve questions repeat a question already asked in the
    same conversation, and one of those repeats carries two contradictory keys —
    conv-30 asks "What did Gina receive from a dance contest?" once as category
    4 with gold "a trophy" and once as category 5, where declining is correct
    and "a trophy" is the distractor. Keying on text would merge those, changing
    both the denominator and the pairing the like-for-like subset depends on.
    """
    return f"{question.conversation}#{index}"


@dataclass(frozen=True)
class GradedRow:
    """One question, one arm, one replicate, graded."""

    key: str
    arm: str
    replicate: int
    conversation: str
    question: str
    category: int
    stratum: str
    answer: str
    verdict: Verdict

    @property
    def is_adversarial(self) -> bool:
        return self.category == ADVERSARIAL_CATEGORY

    def as_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "arm": self.arm,
            "replicate": self.replicate,
            "conversation": self.conversation,
            "question": self.question,
            "category": self.category,
            "stratum": self.stratum,
            "answer": self.answer,
            "correct": self.verdict.correct,
            "score": self.verdict.score,
            "label": self.verdict.label,
            "judge": self.verdict.judge,
            "refused": self.verdict.refused,
            "errored": self.verdict.errored,
            "reference_correct": self.verdict.reference_correct,
        }


def grade(
    judge: Judge,
    question: LocomoQuestion,
    answer: Optional[str],
    *,
    key: str,
    arm: str,
    replicate: int,
) -> GradedRow:
    """Grade one answer. The only place a :class:`Verdict` becomes a row."""
    return GradedRow(
        key=key,
        arm=arm,
        replicate=replicate,
        conversation=question.conversation,
        question=question.question,
        category=question.category,
        stratum=question.category_name,
        answer=str(answer or ""),
        verdict=judge.grade(question, answer),
    )


@dataclass(frozen=True)
class ArmScores:
    """One arm over one set of questions. Every rate carries its denominator."""

    arm: str
    n: int
    n_correct: int
    n_refused: int
    n_errored: int
    score_sum: float

    @property
    def accuracy(self) -> float:
        return self.n_correct / self.n if self.n else 0.0

    @property
    def graded_score(self) -> float:
        """Mean of the judge's graded score. Token F1 under the deterministic
        judge; the same 0/1 as accuracy under a binary LLM judge."""
        return self.score_sum / self.n if self.n else 0.0

    @property
    def refusal_rate(self) -> float:
        return self.n_refused / self.n if self.n else 0.0

    @property
    def error_rate(self) -> float:
        return self.n_errored / self.n if self.n else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "arm": self.arm, "n": self.n, "n_correct": self.n_correct,
            "n_refused": self.n_refused, "n_errored": self.n_errored,
            "accuracy": self.accuracy, "graded_score": self.graded_score,
            "refusal_rate": self.refusal_rate, "error_rate": self.error_rate,
        }


def _score(arm: str, rows: Sequence[GradedRow]) -> ArmScores:
    return ArmScores(
        arm=arm,
        n=len(rows),
        n_correct=sum(1 for r in rows if r.verdict.correct),
        n_refused=sum(1 for r in rows if r.verdict.refused),
        n_errored=sum(1 for r in rows if r.verdict.errored),
        score_sum=sum(float(r.verdict.score) for r in rows),
    )


@dataclass
class Decomposition:
    """The three numbers, or the reason there are not three.

    :attr:`complete` is what the report reads. It is False when any of the three
    cannot be computed — no arms, no scorable questions, or an empty
    like-for-like subset — and the report then prints :attr:`missing` INSTEAD of
    a headline, on the same rule the protocol gate follows: an invalid number
    must not appear at all rather than appear with a retraction underneath it.
    """

    #: (1) every scorable question, refusals scoring zero. Keyed by arm.
    all_questions: Dict[str, ArmScores] = field(default_factory=dict)
    #: (2) the subset EVERY arm answered — no refusal, no error, anywhere.
    like_for_like: Dict[str, ArmScores] = field(default_factory=dict)
    #: (3) per-arm refusal counts over (1). Redundant with ``all_questions`` and
    #: printed separately anyway: the contract is that a reader sees the count
    #: without having to derive it.
    refusals: Dict[str, int] = field(default_factory=dict)
    errors: Dict[str, int] = field(default_factory=dict)
    #: The adversarial category, scored on its own. Never merged into (1) or (2).
    adversarial: Dict[str, ArmScores] = field(default_factory=dict)
    #: The published narrower abstention rule over the same adversarial rows —
    #: the sensitivity of 446 questions to one rule choice.
    adversarial_reference: Dict[str, ArmScores] = field(default_factory=dict)
    n_all: int = 0
    n_like_for_like: int = 0
    n_adversarial: int = 0
    missing: List[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing

    def as_dict(self) -> Dict[str, Any]:
        return {
            "complete": self.complete,
            "missing": list(self.missing),
            "n_all": self.n_all,
            "n_like_for_like": self.n_like_for_like,
            "n_adversarial": self.n_adversarial,
            "all_questions": {a: s.as_dict() for a, s in self.all_questions.items()},
            "like_for_like": {a: s.as_dict() for a, s in self.like_for_like.items()},
            "refusals": dict(self.refusals),
            "errors": dict(self.errors),
            "adversarial": {a: s.as_dict() for a, s in self.adversarial.items()},
            "adversarial_reference": {a: s.as_dict()
                                      for a, s in self.adversarial_reference.items()},
        }


def decompose(rows: Sequence[GradedRow], *, replicate: Optional[int] = None) -> Decomposition:
    """The three-number contract over graded rows.

    ``replicate`` restricts to one pass; ``None`` pools every replicate, which is
    what the headline is computed over. The like-for-like subset is derived
    within whatever slice is being scored, because a question one replicate
    refused and another answered is not a question every arm answered.

    The subset rule is deliberately strict: a key survives only if NO arm
    refused it and NO arm errored on it, in any row of the slice. A looser rule
    — drop the refusals per arm — compares each arm on a different set of
    questions and is not a like-for-like comparison at all, which is the error
    it exists to prevent.
    """
    slice_rows = [r for r in rows if replicate is None or r.replicate == replicate]
    decomposition = Decomposition()
    if not slice_rows:
        decomposition.missing.append(
            "no graded answers — nothing was answered in this slice, so none of "
            "the three numbers exists")
        return decomposition

    arms = sorted({r.arm for r in slice_rows})
    scorable = [r for r in slice_rows if r.category in JUDGED_CATEGORIES]
    adversarial = [r for r in slice_rows if r.is_adversarial]

    if not scorable:
        decomposition.missing.append(
            f"no scorable questions — every row was category "
            f"{ADVERSARIAL_CATEGORY}, where a refusal is the gold answer and a "
            f"dead backbone scores perfectly; that number must never stand alone")

    for arm in arms:
        arm_rows = [r for r in scorable if r.arm == arm]
        decomposition.all_questions[arm] = _score(arm, arm_rows)
        decomposition.refusals[arm] = sum(1 for r in arm_rows if r.verdict.refused)
        decomposition.errors[arm] = sum(1 for r in arm_rows if r.verdict.errored)
        adversarial_rows = [r for r in adversarial if r.arm == arm]
        if adversarial_rows:
            decomposition.adversarial[arm] = _score(arm, adversarial_rows)
            decomposition.adversarial_reference[arm] = ArmScores(
                arm=arm,
                n=len(adversarial_rows),
                n_correct=sum(1 for r in adversarial_rows
                              if r.verdict.reference_correct),
                n_refused=sum(1 for r in adversarial_rows if r.verdict.refused),
                n_errored=sum(1 for r in adversarial_rows if r.verdict.errored),
                score_sum=float(sum(1 for r in adversarial_rows
                                    if r.verdict.reference_correct)),
            )

    keys = {r.key for r in scorable}
    withheld = {r.key for r in scorable if r.verdict.refused or r.verdict.errored}
    shared = sorted(keys - withheld)
    for arm in arms:
        decomposition.like_for_like[arm] = _score(
            arm, [r for r in scorable if r.arm == arm and r.key in shared])

    decomposition.n_all = len(keys)
    decomposition.n_like_for_like = len(shared)
    decomposition.n_adversarial = len({r.key for r in adversarial})
    if keys and not shared:
        decomposition.missing.append(
            f"the like-for-like subset is empty — all {len(keys)} scorable "
            f"questions were refused or errored by at least one arm, so there "
            f"is no set every arm answered and no comparison to make")
    return decomposition


@dataclass(frozen=True)
class GapDecomposition:
    """Where a headline gap between two arms actually comes from.

    ``answer_rate_share`` is the fraction of the headline gap that disappears
    when the comparison is restricted to the questions both arms answered. On
    one measured run in this repo that share was 99.3%, meaning the headline was
    almost entirely one arm declining to answer and scoring zero — and the
    headline alone said nothing about it.
    """

    a: str
    b: str
    gap_all: float
    gap_like_for_like: float
    n_all: int
    n_like_for_like: int

    @property
    def answer_rate_share(self) -> Optional[float]:
        """``None`` when the headline gap is zero and the share is undefined.

        Dividing by a zero gap would print 0% or infinity for two arms that are
        indistinguishable, and both readings are claims the data does not make.
        """
        if not self.gap_all:
            return None
        return 1.0 - (self.gap_like_for_like / self.gap_all)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "a": self.a, "b": self.b,
            "gap_all": self.gap_all,
            "gap_like_for_like": self.gap_like_for_like,
            "answer_rate_share": self.answer_rate_share,
            "n_all": self.n_all,
            "n_like_for_like": self.n_like_for_like,
        }


def gap_decomposition(
    decomposition: Decomposition, a: str, b: str, *, metric: str = "accuracy"
) -> Optional[GapDecomposition]:
    """The a-minus-b gap, headline and like-for-like. ``None`` if an arm is absent."""
    if a not in decomposition.all_questions or b not in decomposition.all_questions:
        return None
    def read(scores: ArmScores) -> float:
        return float(getattr(scores, metric))
    return GapDecomposition(
        a=a, b=b,
        gap_all=read(decomposition.all_questions[a]) - read(decomposition.all_questions[b]),
        gap_like_for_like=(read(decomposition.like_for_like[a])
                           - read(decomposition.like_for_like[b])),
        n_all=decomposition.n_all,
        n_like_for_like=decomposition.n_like_for_like,
    )


# --------------------------------------------------------------------------
# Replicates
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplicateSpread:
    """Whole-run accuracies across replicates, with their spread.

    ``sd`` is the POPULATION standard deviation, which is what the reference
    grader's ``numpy.std`` computes over its three runs. Matching it is
    deliberate — a spread reported under a different estimator is not the
    published spread — and it is named here so nobody has to guess which one a
    three-run sd is.
    """

    arm: str
    values: Tuple[float, ...]

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def mean(self) -> float:
        return statistics.fmean(self.values) if self.values else 0.0

    @property
    def sd(self) -> Optional[float]:
        """``None`` for a single replicate: the spread of one number is not 0.0.

        Printing 0.0 there would claim a reproducibility this run did not
        measure, which is the specific failure that made replicates mandatory.
        """
        return statistics.pstdev(self.values) if len(self.values) > 1 else None

    @property
    def spread(self) -> Optional[float]:
        return (max(self.values) - min(self.values)) if len(self.values) > 1 else None

    def as_dict(self) -> Dict[str, Any]:
        return {"arm": self.arm, "n": self.n, "values": list(self.values),
                "mean": self.mean, "sd": self.sd, "spread": self.spread}


def replicate_spread(
    rows: Sequence[GradedRow], *, metric: str = "accuracy"
) -> Dict[str, ReplicateSpread]:
    """Per-arm whole-run values, one per replicate, in replicate order.

    Whole-run accuracies and not per-question means of per-replicate outcomes:
    the published protocol computes a run's accuracy, three times, and reports
    the spread of those three numbers. Averaging the questions first would
    produce a tighter interval that describes a different experiment.
    """
    replicates = sorted({r.replicate for r in rows})
    out: Dict[str, ReplicateSpread] = {}
    for arm in sorted({r.arm for r in rows}):
        values = []
        for replicate in replicates:
            slice_rows = [r for r in rows
                          if r.arm == arm and r.replicate == replicate
                          and r.category in JUDGED_CATEGORIES]
            if not slice_rows:
                continue
            values.append(float(getattr(_score(arm, slice_rows), metric)))
        out[arm] = ReplicateSpread(arm=arm, values=tuple(values))
    return out


__all__ = [
    "ArmScores",
    "Decomposition",
    "GapDecomposition",
    "GradedRow",
    "ReplicateSpread",
    "decompose",
    "gap_decomposition",
    "grade",
    "question_key",
    "replicate_spread",
]

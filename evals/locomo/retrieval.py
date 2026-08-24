"""Retrieval-only scoring for LoCoMo: resolve the gold turns, then measure.

The headline this module produces is recall@K and MRR **of the gold session**,
and it spends no LLM quota to produce them. That matters more here than it did
for LongMemEval, for a reason this repository measured rather than assumed:
deterministic measurements in this harness reproduce to four decimals across
independent runs, while the generative arm has swung 0.043 token F1 between two
identical configurations. Separating the two is not tidiness — it is the
difference between a number that can be asserted in a test and a number that
cannot.

Alignment is a DICTIONARY LOOKUP, not a content signature
---------------------------------------------------------

LoCoMo names its evidence explicitly: a question carries ``evidence: ["D1:3"]``
and ``D1:3`` is the ``dia_id`` of a turn in ``session_1``. Measured over all
5,882 turns of ``locomo10.json``, the ``D<n>`` prefix always equals the
``session_n`` key the turn lives under — zero violations — which is what makes
the lookup safe, and :func:`verify_dia_ids` is what keeps it safe if the data
changes. :func:`evals.lme_mab.retrieval.align_gold`'s content-signature
machinery exists because LongMemEval gives only a ``has_answer`` boolean across
two views that disagree on order; none of that applies here and none of it is
imported.

K is a REPORTED SET, and it is fixed before any result is seen
--------------------------------------------------------------

The evidence unit is the turn; the retrieval unit is the session; and a LoCoMo
conversation has only 19 to 32 sessions (measured). At that scale a single K is
not a measurement. Ten documents out of 19 is more than half the corpus, so a
uniformly random ranker scores recall@10 of about 0.53 on the smallest
conversation — a number that cannot separate a memory system from a coin.

So :data:`PROTOCOL_KS` is a frozen set of five, every one of them reported, each
printed beside :func:`random_recall_floor` for the same conversation. MRR is the
headline, because it is the metric a large K cannot inflate. The set is fixed
here, in code, before any result exists, because choosing K after seeing results
is the thing that must not happen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..lme_mab.retrieval import score_retrieval
from ..qa.run_qa_eval import Skip
from .dataset import (
    Conversation,
    LocomoQuestion,
    is_malformed_evidence,
    parse_dia_ids,
)

#: The evidence budgets this benchmark reports. All of them, always, on every
#: run — see the module docstring. Not a tuning knob and not a default that a
#: flag narrows: a report that prints one K is a report whose K was chosen.
PROTOCOL_KS: Tuple[int, ...] = (1, 2, 3, 5, 10)

#: The caveat that travels with every retrieval table this package prints. One
#: string, one owner, rendered ABOVE the table it qualifies — a caveat under the
#: crop is not a caveat.
NOT_COMPARABLE = (
    "**These numbers are this machine's own protocol and may not be quoted "
    "beside published LoCoMo results.** The published harness scores retrieval "
    "per EVIDENCE ID, dividing by the number of ids a question lists and "
    "counting repeats; this scores per gold SESSION, dividing by "
    "`min(|gold sessions|, K)`. Those denominators differ on every question "
    "whose gold spans more than one session — measured, 332 of the 1,986. The "
    "retrieval unit here is a session document, which is this harness's choice "
    "and not the paper's, and a conversation holds only 19 to 32 of them, so "
    "read every figure beside the random-ranker floor printed next to it. No "
    "published baseline's own number is reproduced anywhere in this repository."
)


class RefusedToAlignGold(Skip):
    """Gold alignment could not be established, so nothing is scored.

    A :class:`Skip`: the runner prints ``SKIP: <what>`` plus the command that
    fixes it and exits 0. Never a fallback to a best-effort mapping — the
    alternative to refusing is a recall number computed against the wrong
    document, which looks exactly like a real one.
    """


def verify_dia_ids(conversation: Conversation) -> None:
    """Refuse unless every turn's ``dia_id`` names the session it lives in.

    The whole alignment strategy rests on ``D<n>:<t>`` implying ``session_n``.
    Measured across all 5,882 turns of ``locomo10.json`` that holds without
    exception, so this check is inert on the shipped data and fires only if the
    data changes. It is cheap, and the failure it prevents is a gold answer
    attributed to the wrong document while every printed number stays plausible.
    """
    for session in conversation.sessions:
        for turn in session.turns:
            prefix, _, _ = str(turn.dia_id).partition(":")
            if prefix != f"D{session.number}":
                raise RefusedToAlignGold(
                    f"{conversation.sample_id}: turn {turn.dia_id!r} lives under "
                    f"session_{session.number}, so the dia_id prefix and the "
                    f"session key disagree and evidence can no longer be "
                    f"resolved by lookup",
                    "score this conversation by hand, or drop it with "
                    "--conversations — measured on the shipped locomo10.json "
                    "all 5,882 turns agree, so this is a change in the data and "
                    "not a threshold to relax",
                )


@dataclass
class GoldAlignment:
    """Which sessions hold each question's gold evidence, plus what did not resolve.

    Every count here is printed. An annotation this harness could not resolve is
    a fact about the answer key, and a benchmark that drops one quietly has
    changed the key without saying so.
    """

    conversation: str
    #: ``gold[i]`` are the session NUMBERS carrying gold evidence for question
    #: ``i``, in first-seen order. Empty when nothing resolved — never a guess.
    gold: List[List[int]] = field(default_factory=list)
    #: Evidence elements that were not exactly one ``D<n>:<t>``. Measured this
    #: phase over the whole file: 6 of 2,815.
    n_malformed: int = 0
    #: Elements from which no ``D<n>:<t>`` could be recovered at all. Measured:
    #: 2 — the bare string ``"D"`` and ``"D:11:26"``, which is ambiguous between
    #: two readings and is therefore not read at all.
    n_unparseable: int = 0
    #: Ids naming a session or a turn that does not exist. Measured: 2.
    n_dangling: int = 0
    #: Questions whose ``evidence`` list was empty in the file. Measured: 4, and
    #: they are the only 4 questions in the file with no gold session resolved.
    n_empty_evidence: int = 0

    @property
    def n_no_gold(self) -> int:
        return sum(1 for found in self.gold if not found)


def align_gold(conversation: Conversation) -> GoldAlignment:
    """Resolve every question's ``evidence`` to session numbers.

    Does not refuse on dirt, and counts it instead — the opposite call from
    :func:`evals.lme_mab.retrieval.align_gold`, for a reason specific to this
    data. There, an unresolved gold session was a signature that matched nothing
    and could not be distinguished from a corpus mismatch, so refusing was the
    only safe reading. Here the failures are enumerable, measured, and
    individually inspectable: 6 malformed strings, 2 of them unrecoverable, 2
    dangling ids, 4 empty evidence lists. Refusing on those would refuse the
    whole benchmark over 8 of its 2,815 evidence elements.

    What protects the score instead is the exclusion rule: a question with no
    resolved gold is EXCLUDED from every retrieval metric, not scored zero.
    Scoring it zero would claim an arm failed to retrieve something that was
    never there, and would punish an arm for the answer key's dirt.
    """
    verify_dia_ids(conversation)
    turns = conversation.turn_ids()
    known_sessions = set(conversation.session_numbers)
    alignment = GoldAlignment(conversation=conversation.sample_id)
    for question in conversation.questions:
        if not question.evidence:
            alignment.n_empty_evidence += 1
        found: List[int] = []
        for element in question.evidence:
            if is_malformed_evidence(element):
                alignment.n_malformed += 1
            pairs = parse_dia_ids(element)
            if not pairs:
                alignment.n_unparseable += 1
                continue
            for session_number, turn_number in pairs:
                dia_id = f"D{session_number}:{turn_number}"
                if session_number not in known_sessions or dia_id not in turns:
                    alignment.n_dangling += 1
                    continue
                if session_number not in found:
                    found.append(session_number)
        alignment.gold.append(found)
    return alignment


# --------------------------------------------------------------------------
# Random floors
# --------------------------------------------------------------------------


def random_recall_floor(n_candidates: int, n_gold: int, k: int) -> float:
    """Expected recall@k of a UNIFORMLY RANDOM ranking over ``n_candidates``.

    ``k * n_gold / n_candidates`` gold documents land in the top k on average,
    over the same ``min(n_gold, k)`` denominator :func:`score_retrieval` uses.
    Printed beside every measured recall, because on a 19-session conversation
    recall@10 from a coin is already above one half and a table without this
    column reads as if it were not.
    """
    if n_candidates <= 0 or n_gold <= 0 or k <= 0:
        return 0.0
    budget = min(k, n_candidates)
    expected_hits = budget * min(n_gold, n_candidates) / n_candidates
    return min(1.0, expected_hits / min(n_gold, budget))


def random_rr_floor(n_candidates: int, n_gold: int, k: int) -> float:
    """Expected reciprocal rank of a uniformly random ranking, truncated at ``k``.

    Exact rather than simulated: the first gold falls at rank ``r`` with
    probability ``C(n_candidates - r, n_gold - 1) / C(n_candidates, n_gold)``,
    and reciprocal rank is 0 when no gold reaches the top ``k`` — which is what
    :func:`evals.lme_mab.retrieval.score_retrieval` scores.
    """
    if n_candidates <= 0 or n_gold <= 0 or k <= 0:
        return 0.0
    gold = min(n_gold, n_candidates)
    total = math.comb(n_candidates, gold)
    if not total:
        return 0.0
    return sum(
        math.comb(n_candidates - rank, gold - 1) / total / rank
        for rank in range(1, min(k, n_candidates) + 1)
        if n_candidates - rank >= gold - 1
    )


def floors_for_rows(rows: Sequence[Mapping[str, Any]], *, k: int) -> Dict[str, float]:
    """The random-ranker floor for a set of scored rows, averaged as the metric is.

    Each row carries its own ``n_candidates`` (its conversation's session count)
    and its own gold multiplicity, so the floor is a macro mean over exactly the
    rows the metric was a macro mean over. A single floor computed from the mean
    conversation size would be a different quantity wearing the same label.
    """
    measured = [r for r in rows if r.get("gold")]
    if not measured:
        return {"recall_at_k": 0.0, "mrr": 0.0, "n": 0}
    recalls = []
    rrs = []
    for row in measured:
        candidates = int(row.get("n_candidates") or 0)
        gold = len({int(g) for g in row["gold"]})
        recalls.append(random_recall_floor(candidates, gold, k))
        rrs.append(random_rr_floor(candidates, gold, k))
    return {
        "recall_at_k": sum(recalls) / len(recalls),
        "mrr": sum(rrs) / len(rrs),
        "n": len(measured),
    }


# --------------------------------------------------------------------------
# Rows and scoring
# --------------------------------------------------------------------------


def retrieval_rows(
    conversation: Conversation,
    alignment: GoldAlignment,
    retrieved: Sequence[Sequence[int]],
) -> List[Dict[str, Any]]:
    """One scoreable retrieval row per question.

    ``n_candidates`` rides on every row because the random floor is a property
    of the conversation, and a run that mixes a 19-session conversation with a
    32-session one has no single floor. ``stratum`` is the category NAME, so a
    reader never has to remember which integer means adversarial.
    """
    candidates = len(conversation.sessions)
    rows: List[Dict[str, Any]] = []
    for index, question in enumerate(conversation.questions):
        rows.append({
            "question": question.question,
            "stratum": question.category_name,
            "category": question.category,
            "conversation": conversation.sample_id,
            "n_candidates": candidates,
            "gold": list(alignment.gold[index]) if index < len(alignment.gold) else [],
            "retrieved": list(retrieved[index]) if index < len(retrieved) else [],
        })
    return rows


def require_ks(values: Optional[Iterable[Any]]) -> Tuple[int, ...]:
    """``--k`` as a sorted tuple of budgets, or :data:`PROTOCOL_KS`.

    Every value must be at least 1: ``k=0`` divides recall by ``min(|G|, 0)`` and
    ``k=-1`` slices the last document off every ranking and prints a negative
    rate as if it were one. A run that narrows the set is allowed — it is how a
    single-K sensitivity check is done — but the report records which set ran, so
    a table showing one K is visibly a table that asked for one.
    """
    if values is None:
        return PROTOCOL_KS
    budgets = []
    for value in values:
        try:
            budget = int(value)
        except (TypeError, ValueError) as exc:
            raise Skip(
                f"--k {value!r} is not a number, and K is the evidence budget "
                f"every arm in the comparison shares",
                f"pass integers — this benchmark reports "
                f"{', '.join(str(k) for k in PROTOCOL_KS)} by default",
            ) from exc
        if budget < 1:
            raise Skip(
                f"--k {budget}: K must be at least 1 — 0 divides recall by "
                f"zero and a negative K drops documents off the end of every "
                f"ranking and prints a negative rate",
                f"pass --k {' '.join(str(k) for k in PROTOCOL_KS)}",
            )
        budgets.append(budget)
    if not budgets:
        raise Skip("--k is empty, so there is no budget to score at",
                   f"drop the flag to report {PROTOCOL_KS}")
    return tuple(sorted(dict.fromkeys(budgets)))


def score_at_ks(
    rows: Sequence[Mapping[str, Any]],
    *,
    system: str,
    ks: Sequence[int] = PROTOCOL_KS,
    meta: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """One :func:`evals.lme_mab.retrieval.score_retrieval` report per K, plus floors.

    The per-K scorer is imported rather than rewritten. Its arithmetic is
    exactly what this benchmark needs — recall over ``min(|G|, k)`` so a
    multi-gold question is not penalised for the budget, reciprocal rank of the
    FIRST hit, questions without gold excluded rather than scored zero — and a
    second implementation of it would be a second answer to the same question.
    """
    reports = []
    for k in ks:
        report = score_retrieval(rows, system=system, k=k, meta=meta)
        report["random_floor"] = floors_for_rows(rows, k=k)
        reports.append(report)
    return reports


def alignment_summary(alignments: Sequence[GoldAlignment]) -> Dict[str, int]:
    """The dirt counts, summed across conversations. Every field is printed."""
    return {
        "n_questions": sum(len(a.gold) for a in alignments),
        "n_no_gold": sum(a.n_no_gold for a in alignments),
        "n_empty_evidence": sum(a.n_empty_evidence for a in alignments),
        "n_malformed": sum(a.n_malformed for a in alignments),
        "n_unparseable": sum(a.n_unparseable for a in alignments),
        "n_dangling": sum(a.n_dangling for a in alignments),
    }


def gold_of(alignment: GoldAlignment, question_index: int) -> List[int]:
    """``alignment.gold[question_index]``, or ``[]`` past the end."""
    if 0 <= question_index < len(alignment.gold):
        return list(alignment.gold[question_index])
    return []


def questions_with_gold(
    conversation: Conversation, alignment: GoldAlignment
) -> List[LocomoQuestion]:
    """The questions this alignment could resolve. The retrieval denominator."""
    return [q for i, q in enumerate(conversation.questions) if gold_of(alignment, i)]


__all__ = [
    "NOT_COMPARABLE",
    "PROTOCOL_KS",
    "GoldAlignment",
    "RefusedToAlignGold",
    "align_gold",
    "alignment_summary",
    "floors_for_rows",
    "gold_of",
    "questions_with_gold",
    "random_recall_floor",
    "random_rr_floor",
    "require_ks",
    "retrieval_rows",
    "score_at_ks",
    "verify_dia_ids",
]

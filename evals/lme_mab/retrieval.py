"""Retrieval-only scoring for LongMemEval-MAB: find the gold session, then measure it.

The headline number this module produces is recall@K and MRR **of the gold
session**, not answer accuracy. That removes the LLM judge — the one control
this machine cannot meet — from the comparison entirely, which is what makes a
Tesserae / BM25 / Dense table measurable here at all. What it does not do is
make the table quotable next to somebody else's: see :data:`NOT_COMPARABLE`,
which every consumer imports and nobody restates.


Alignment is by CONTENT SIGNATURE. Positional alignment is measurably wrong
------------------------------------------------------------------------------

``metadata.haystack_sessions`` is stored **per question** — 1-6 sessions each,
overlapping and repeating — while ``split_sessions`` yields the group's whole
dialogue in ``context`` order. The obvious bridge between the two is a running
offset into the flattened haystack, and it is wrong. Measured on the real
parquet:

* flattening gives 111 / 107 / 116 / 112 / 113 sessions for groups 0-4, against
  111 / 107 / 116 / **111** / **110** from ``split_sessions`` — the counts do
  not even agree for groups 3 and 4, because a question's slices repeat;
* and where they do agree the ORDER still does not. ``flatten(...)[0]`` and
  ``split_sessions(...)[0]`` are different conversations in group 0 (a Delta
  SkyMiles redemption against a resume rewrite), so an algorithm keyed on
  position mis-attributes gold in every group, silently, while printing a
  plausible number.

So the bridge is the turn text itself: :func:`session_signature` over the
normalised turn contents. Measured, that resolves **514 of 514** gold sessions
across all five groups with **zero** duplicate signatures, and leaves exactly
one non-gold haystack session in group 4 unmatched — which is counted as
``n_unmatched`` rather than guessed at. An unmatched session that IS gold is
refused instead of counted, because that one moves the answer key; see
:func:`align_gold`. The two counts are NOT required to be equal: content
matching does not need them to be.

Nothing here reads a clock, a network, or a model.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from ..qa.run_qa_eval import Skip
from ..qa.scorer import _mean
from .adapter import PROTOCOL_EMBEDDING_MODEL, PROTOCOL_K, Session
from .dataset import MabGroup

#: The caveat that has to travel with every retrieval table this package
#: prints. **One string, one owner**: the report renders it ABOVE the table it
#: qualifies, because that is the part of a report that gets screenshotted and
#: a caveat underneath the crop is not a caveat. Do not restate it elsewhere —
#: two prose versions of a limitation drift, and the weaker one gets quoted.
NOT_COMPARABLE = (
    "**These numbers are this machine's own protocol and may not be quoted "
    "beside published LongMemEval results.** Every arm below was measured over "
    f"one corpus, with one local embedder, at the same K={PROTOCOL_K}, so the "
    "rows are comparable WITH EACH OTHER and with nothing else. The published "
    f"protocol fixes {PROTOCOL_EMBEDDING_MODEL} for retrieval and the embedder "
    "named in the table is not it, which makes every gap here unattributable "
    "to anyone else's architecture; the retrieval unit is a session of one MAB "
    "group, which is this harness's choice and not the paper's. The published "
    "baselines' own figures are reproduced nowhere in this repo — quoting "
    "numbers it never measured is what #178 retracted."
)


class RefusedToScore(Skip):
    """The evidence budget is out of range, so nothing is scored.

    A :class:`Skip` for the same reason every other refusal in this package is
    one: the runner prints ``SKIP: <what>`` plus the command that fixes it and
    exits 0, and a benchmark that tracebacks on a bad flag has told the operator
    less than one that names the flag.
    """


class RefusedToAlignGold(Skip):
    """Gold alignment could not be established, so nothing is scored.

    A :class:`Skip` — the runner already knows how to print ``SKIP: <what>``
    plus the command that fixes it and exit 0 — and never a fallback to a
    best-effort mapping. Every failure this raises on has the same shape: the
    gold session for some question is ambiguous or absent, and the alternative
    to refusing is a recall number computed against the wrong document, which
    looks exactly like a real one.
    """


# --------------------------------------------------------------------------
# The caveat's enforcement
# --------------------------------------------------------------------------


def embedder_refusal(
    reports: Sequence[Mapping[str, Any]], *, local: str
) -> Optional[str]:
    """``None`` when :data:`NOT_COMPARABLE`'s "one local embedder" is TRUE here.

    Otherwise the prose §6 prints INSTEAD of its table, naming the arms that
    disagreed and the embedder each one resolved.

    The sentence above that table says every arm was measured "with one local
    embedder", and gives as its reason for not being quotable that the embedder
    named in the table is not ``text-embedding-3-small``. Both halves were
    falsifiable by one flag: ``--embedding-prefer openai`` resolved
    ``OpenAIEmbeddingBackend`` for the Tesserae arm while the dense arm resolved
    model2vec, so the table printed two embedders under a claim of one — and
    printed ``text-embedding-3-small`` directly under a sentence saying it was
    not that. Rewording the caveat would have made it true of a comparison that
    still was not one; this makes the table refuse to exist unless the claim
    holds, which is the only version a reader can rely on.

    Arms with no embedder at all are skipped rather than counted as
    disagreement: BM25 declares ``none`` because it has no lane to hold still,
    and a lexical baseline is not a second embedder.
    """
    declared = [
        (str(report.get("system") or "?"), name)
        for report in reports
        for name in [str((report.get("meta") or {}).get("embedder") or "").strip()]
        if name and name.lower() != "none"
    ]
    if not declared:
        return None
    first_system, first = declared[0]
    for system, name in declared[1:]:
        if name != first:
            return (
                f"**§6 is withheld: the arms did not share an embedder.** "
                f"{first_system} retrieved with `{first}` and {system} with "
                f"`{name}`, so the gap between their rows is a gap between two "
                f"embedders as much as between two memories, and the caveat "
                f"this table prints — one corpus, one local embedder, one K — "
                f"would be false above it. Re-run with `--embedding-prefer "
                f"{local}`, which is the default and what every other section "
                f"of this report assumes."
            )
    if not first.startswith(f"{local}:") and first != local:
        return (
            f"**§6 is withheld: {first_system} retrieved with `{first}`, which "
            f"is not the local embedder.** This section compares arms under one "
            f"self-consistent LOCAL protocol and says so above its table; a run "
            f"on a hosted embedder is a different experiment, and printing it "
            f"here would put a sentence about `{local}` over a row that is not. "
            f"Re-run with `--embedding-prefer {local}` for the comparison, or "
            f"read §2-§4, which are where a published-protocol run is scored."
        )
    return None


# --------------------------------------------------------------------------
# Signatures
# --------------------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")


def _normalise(text: Any) -> str:
    """Collapse whitespace and strip. ``None`` becomes ``""``.

    The two views of the haystack agree on the words and disagree on the
    wrapping — ``context`` is a ``repr`` round-trip — so a signature that keeps
    raw whitespace matches nothing.
    """
    return _WHITESPACE.sub(" ", str(text or "")).strip()


def session_signature(turns: Iterable[Mapping[str, Any]]) -> str:
    """sha1 over the session's normalised turn contents, joined by ``|``.

    Content only. ``role`` is excluded because the two views spell it the same
    way today and a signature should not depend on that continuing;
    ``has_answer`` is excluded because it is the gold marker, and a signature
    that changed when a turn was gold would make the map from haystack session
    to context session depend on the answer key.

    The ``|`` is a separator and not decoration: joining bare would make one
    session of ``["ab"]`` collide with one of ``["a", "b"]``.
    """
    joined = "|".join(_normalise(turn.get("content")) for turn in turns)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Alignment
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GoldAlignment:
    """Which context sessions hold each question's gold evidence."""

    #: ``gold[i]`` are the ``Session.index`` values whose haystack copy carries a
    #: ``has_answer`` turn for question ``i``, in first-seen order. Empty for a
    #: question whose gold is absent — never a guess.
    gold: List[List[int]]
    #: NON-GOLD haystack sessions (counted per question occurrence, so repeats
    #: count repeatedly) whose signature is in no context session. Measured: 1
    #: across all five groups. Counted rather than resolved — an unmatched
    #: non-gold session changes no metric, so counting it is the whole of what
    #: honesty requires. An unmatched GOLD session is a different animal and
    #: never reaches this counter: :func:`align_gold` refuses.
    n_unmatched: int
    #: Questions with an empty gold list. They are excluded from every metric —
    #: retrieval of a gold that does not exist is not a thing to score.
    n_no_gold: int


def align_gold(group: MabGroup, sessions: Sequence[Session]) -> GoldAlignment:
    """Map each question's gold haystack sessions onto ``sessions`` by content.

    ``sessions`` is :func:`evals.lme_mab.adapter.split_sessions`'s output — the
    same units the arms retrieve and the same ``index`` their document names are
    derived from, so an entry here can be compared with a retrieved document
    without either side formatting a filename.

    An unmatched session is counted when it is NOT gold and REFUSED when it is,
    and the asymmetry is the point. A non-gold session that resolves to no
    document changes no metric — it is not in anyone's answer key — so counting
    it says everything there is to say. An unmatched gold session changes the
    answer key itself, in the direction of a better-looking number and without
    leaving a mark: a question with two golds, one unmatched, prints recall
    1.000 where the truth is 0.500, and a question whose ONLY gold went missing
    falls into ``n_no_gold`` and leaves the mean altogether — removing exactly
    the question the arms would most likely have missed. Refusing is the same
    call this function already makes on a duplicate signature, and it is inert
    on the measured data (514 of 514 gold sessions match, and the one unmatched
    session in group 4 is non-gold), so it fires on a change in the data rather
    than on a threshold.
    """
    if not group.haystack_sessions:
        raise RefusedToAlignGold(
            f"group {group.index} ({group.source}) carries no "
            f"metadata.haystack_sessions, so no question's gold session is "
            f"identifiable and retrieval cannot be scored",
            "load the groups with evals.lme_mab.dataset.load_groups from the "
            "Accurate_Retrieval parquet — that view is where the gold marker "
            "lives; the context view carries dates and no answer key",
        )
    if len(group.haystack_sessions) != len(group.questions):
        raise RefusedToAlignGold(
            f"group {group.index}: {len(group.haystack_sessions)} haystack "
            f"entries against {len(group.questions)} questions — "
            f"metadata.haystack_sessions is per question, so unequal counts "
            f"mean the gold would be attributed to the wrong question",
            "re-read the group with evals.lme_mab.dataset.load_groups; both "
            "lists come from one parquet row and measured they are 60 and 60",
        )

    by_signature: Dict[str, int] = {}
    for session in sessions:
        signature = session_signature(session.turns)
        first = by_signature.get(signature)
        if first is not None:
            raise RefusedToAlignGold(
                f"group {group.index}: context sessions {first} and "
                f"{session.index} hold identical turn contents (signature "
                f"{signature[:12]}), so a gold session matching them belongs to "
                f"both and to neither",
                "drop this group with --groups, or score it by hand — measured "
                "on the real parquet no group has a duplicate signature, so "
                "this is a change in the data and not a threshold to relax",
            )
        by_signature[signature] = session.index

    gold: List[List[int]] = []
    n_unmatched = 0
    for question_index, per_question in enumerate(group.haystack_sessions):
        found: List[int] = []
        for turns in per_question:
            signature = session_signature(turns)
            index = by_signature.get(signature)
            is_gold = any(turn.get("has_answer") for turn in turns)
            if index is None:
                if is_gold:
                    raise RefusedToAlignGold(
                        f"group {group.index}: question {question_index} "
                        f"({str(group.questions[question_index])[:60]!r}) lists a "
                        f"GOLD session of {len(turns)} turn(s) — signature "
                        f"{signature[:12]}, opening "
                        f"{_normalise(turns[0].get('content') if turns else '')[:40]!r} "
                        f"— whose contents match no session in the dated context "
                        f"view, so this question's answer key is incomplete and "
                        f"any recall computed over the rest of it reads better "
                        f"than the truth",
                        "drop this group with --groups, or score it by hand — "
                        "measured on the real parquet all 514 gold sessions "
                        "across the five groups match, and the single unmatched "
                        "session (group 4) is NOT gold, so this is a change in "
                        "the data and not a threshold to relax",
                    )
                # Measured once, in group 4, on a NON-gold session. Counting it
                # keeps the arithmetic honest; picking the nearest session would
                # invent evidence.
                n_unmatched += 1
                continue
            if is_gold and index not in found:
                found.append(index)
        gold.append(found)

    return GoldAlignment(
        gold=gold,
        n_unmatched=n_unmatched,
        n_no_gold=sum(1 for found in gold if not found),
    )


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def require_k(k: Any) -> int:
    """``k`` as an evidence budget of at least 1, or a refusal naming the flag.

    K is the one control every arm in this table shares, and each bad value
    fails in its own quiet way: ``k=0`` divides recall by ``min(|G|, 0)`` and
    raises ``ZeroDivisionError`` — a traceback, arriving after the 20MB parquet
    has been read — while ``k=-1`` makes ``[:k]`` drop the last document off
    every ranking and prints a NEGATIVE recall as if it were a rate.

    One owner, on the handoff's rule: ``run.py`` calls this before it reads any
    input so the refusal is cheap, and :func:`score_retrieval` calls it again so
    a library caller cannot route around the runner. Two spellings of "is K
    sane" would drift, and the weaker one would be the one that ran.
    """
    try:
        budget = int(k)
    except (TypeError, ValueError) as exc:
        raise RefusedToScore(
            f"--k {k!r} is not a number, and K is the evidence budget every arm "
            f"in the comparison shares",
            f"pass an integer — the protocol fixes K={PROTOCOL_K}, and any other "
            f"value blocks the comparison rather than tuning it",
        ) from exc
    if budget < 1:
        raise RefusedToScore(
            f"--k {budget}: K must be at least 1 — it is the evidence budget "
            f"every arm shares, and 0 divides recall by min(|G|, 0) while a "
            f"negative K silently drops documents off the end of every ranking "
            f"and prints a negative rate",
            f"pass --k {PROTOCOL_K} — the protocol fixes it, and any other value "
            f"blocks the comparison rather than tuning it",
        )
    return budget


def _score_row(row: Mapping[str, Any], *, k: int) -> Dict[str, Any]:
    """One question's retrieval, scored. ``None`` metrics mean "not measured"."""
    gold = list(dict.fromkeys(int(i) for i in (row.get("gold") or [])))
    # Truncate rather than trust: K is the control, and an arm that returned
    # more than it was asked for must not be scored on the surplus.
    retrieved = list(dict.fromkeys(int(i) for i in (row.get("retrieved") or [])))[:k]
    gold_set = set(gold)
    hits = [i for i in retrieved if i in gold_set]
    rank = next((pos for pos, doc in enumerate(retrieved, start=1) if doc in gold_set), 0)
    measured = bool(gold)
    return {
        "question": str(row.get("question") or ""),
        "stratum": str(row.get("stratum") or "unspecified"),
        "group": row.get("group"),
        "n_gold": len(gold),
        "n_retrieved": len(retrieved),
        "n_hits": len(hits),
        # No candidate count. A row is a RANKED LIST — how many documents the
        # lane scored above zero before slicing to K is something only the arm
        # saw, and it keeps its own record (``_Arm.shortfalls``,
        # ``MabMemory.shortfalls``). Defaulting it to 0 here put a hardcoded
        # zero into a field shaped exactly like a measurement, so the first
        # render of a baseline record would read "0 candidates" for a lane that
        # found two.
        # min(|G|, k) is the multi-gold cap: three golds at K=2 cannot all be
        # retrieved, and scoring 0.67 for perfect retrieval would penalise the
        # arm for the budget rather than for the ranking.
        "recall_at_k": (len(hits) / min(len(gold), k)) if measured else None,
        # First hit only. A question is answered once the evidence is in front
        # of the backbone; the second copy of the gold is worth nothing.
        "rr": ((1.0 / rank) if rank else 0.0) if measured else None,
    }


def _summarize(scored_rows: Sequence[Mapping[str, Any]], *, k: int) -> Dict[str, Any]:
    """Macro means over the rows that HAVE gold, plus the denominators.

    A question with no gold is excluded and not scored zero: zero is a claim the
    arm failed to retrieve something, and there was nothing to retrieve. As in
    ``evals/qa/scorer.summarize``, every rate here is a mean over a possibly
    empty set and therefore ``0.0`` when empty, so ``n_scored`` travels beside
    it and a caller MUST print the denominator (``run_qa_eval._rate``) rather
    than the bare rate.

    ``n_under_k`` is deliberately NOT called a shortfall. It counts questions
    that came back with fewer than ``k`` DISTINCT DOCUMENTS, and for the
    Tesserae arm that is usually the budget working rather than a failure:
    ``MabMemory.search_documents`` de-duplicates hits onto sessions, so a full
    ``k`` hits drawn from four sessions is four documents — "the budget doing
    its job rather than a shortfall to fix", in the adapter's own words, and on
    a real run it fires on nearly every question. A lane that matched nothing is
    counted here too, and this number alone cannot tell the two apart. The count
    that IS a search shortfall lives on the arm (``MabMemory.shortfalls``,
    ``_Arm.shortfalls``) and is §5's.
    """
    measured = [r for r in scored_rows if r["recall_at_k"] is not None]
    return {
        "n": len(scored_rows),
        "n_scored": len(measured),
        "n_no_gold": len(scored_rows) - len(measured),
        # Total gold sessions over the scored rows, so a reader who wants the
        # uncapped recall can recompute it from n_hits without this file.
        "n_gold": sum(int(r["n_gold"]) for r in measured),
        "n_hits": sum(int(r["n_hits"]) for r in measured),
        "n_under_k": sum(1 for r in scored_rows if int(r["n_retrieved"]) < k),
        "recall_at_k": _mean([float(r["recall_at_k"]) for r in measured]),
        "mrr": _mean([float(r["rr"]) for r in measured]),
    }


def score_retrieval(
    rows: Iterable[Mapping[str, Any]],
    *,
    system: str,
    k: int = PROTOCOL_K,
    meta: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Score one arm's retrieval, overall and per stratum.

    Each row is one question: ``gold`` and ``retrieved`` are context-session
    indices (:attr:`evals.lme_mab.adapter.Session.index`), ``retrieved`` in rank
    order; ``stratum`` is the benchmark's own ``question_types`` entry, because
    an aggregate that hides WHICH kind of question failed says very little —
    ``temporal-reasoning`` and ``single-session-user`` miss for different
    reasons and only one of them is about retrieval.

    A row returning fewer than ``k`` documents is scored over the ``m`` it
    actually returned and never padded: ranks that do not exist cannot hold
    gold. The denominator stays ``min(|G|, k)`` — shrinking it to ``min(|G|, m)``
    would let an arm score 1.0 by returning one document, which is the opposite
    of what an under-filled budget means.

    Those rows come back as ``under_k`` and NOT as ``shortfalls``: the word is
    ``MabMemory.shortfalls``' for a search that matched fewer than K nodes,
    while this counts documents after de-duplication, which for the Tesserae arm
    is usually the budget working. See :func:`_summarize`. The records carry no
    candidate count, because the scorer never saw one — the arms keep theirs.
    """
    budget = require_k(k)
    scored = [_score_row(row, k=budget) for row in rows]
    strata: Dict[str, List[Dict[str, Any]]] = {}
    for row in scored:
        strata.setdefault(str(row["stratum"]), []).append(row)
    return {
        "system": system,
        "meta": dict(meta or {}),
        "k": budget,
        "overall": _summarize(scored, k=budget),
        "strata": {name: _summarize(rows_, k=budget)
                   for name, rows_ in sorted(strata.items())},
        "rows": scored,
        "under_k": [
            {
                "question": row["question"],
                "requested": budget,
                "returned": row["n_retrieved"],
            }
            for row in scored
            if int(row["n_retrieved"]) < budget
        ],
    }


__all__ = [
    "NOT_COMPARABLE",
    "GoldAlignment",
    "RefusedToAlignGold",
    "RefusedToScore",
    "align_gold",
    "embedder_refusal",
    "require_k",
    "score_retrieval",
    "session_signature",
]

"""Peer-review status as a FILTER over evidence, never as a score.

A researcher does not remember every fact; they remember where the fact lives
and they quote it with its reference. Whether that reference was peer reviewed
is part of the citation, not a number folded into it — so this module answers
"which of these claims survive if I only accept reviewed evidence?" and refuses
to answer "how much should I trust this claim overall?".

**Why a filter and not a score.** A score blends provenance, verdict and venue
into one number and hides the disagreement that made the question worth asking.
A filter keeps them separate: an agent asks for the peer-reviewed subset, sees
what survives, and sees what it lost. When a claim is supported only by
preprints, that is the answer — and a score would have reported it as "0.6" and
buried it.

**Absence is not rejection, and this is the failure the vocabulary exists to
prevent.** A paper with no OpenReview record has not been rejected; it has not
been *found*. Most arXiv preprints were never submitted anywhere this can see.
:data:`UNKNOWN` and :data:`PREPRINT` are therefore distinct from
:data:`REJECTED`, and no filter here may treat them as the same thing — the
whole point of adding OpenReview beside arXiv is that rejection is real
information that arXiv alone cannot express.

Pure. No I/O, no network, no LLM, no fuzzy matching. Review status is written
into node metadata at INGEST time and read here as bytes, which is what lets
:func:`tesserae.verify.verify_claim` stay a pure function of the graph while
gaining a review filter.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

#: Accepted at a venue that ran peer review.
PEER_REVIEWED = "peer_reviewed"
#: Submitted and rejected. Only OpenReview-style sources can tell us this, and
#: it is the single fact a preprint server structurally cannot express.
REJECTED = "rejected"
#: Submitted, decision not yet made.
UNDER_REVIEW = "under_review"
#: On a preprint server with no submission this graph knows about. NEUTRAL:
#: most preprints are simply not submitted where we can see, so this says
#: nothing about quality.
PREPRINT = "preprint"
#: No review metadata at all. Distinct from PREPRINT: we do not even know the
#: document is a paper.
UNKNOWN = "unknown"

#: Every state. Ordered from most to least evidence of review having happened —
#: an ORDER, deliberately not a scale. Nothing here multiplies or averages.
REVIEW_STATES: Tuple[str, ...] = (
    PEER_REVIEWED, UNDER_REVIEW, PREPRINT, UNKNOWN, REJECTED,
)

#: Metadata keys an ingest writes. `arxiv_id` already exists in this codebase
#: (`ingest/fetch.py` extracts it from the URL); the rest are new and all
#: optional, so a graph compiled before this module reads as UNKNOWN rather
#: than breaking.
REVIEW_STATUS_KEY = "review_status"
VENUE_KEY = "venue"
OPENREVIEW_KEY = "openreview_id"
ARXIV_KEY = "arxiv_id"

_DIRECT = {
    "accept": PEER_REVIEWED, "accepted": PEER_REVIEWED, "poster": PEER_REVIEWED,
    "oral": PEER_REVIEWED, "spotlight": PEER_REVIEWED, "published": PEER_REVIEWED,
    "reject": REJECTED, "rejected": REJECTED, "desk_reject": REJECTED,
    "withdrawn": REJECTED,
    "under_review": UNDER_REVIEW, "submitted": UNDER_REVIEW, "pending": UNDER_REVIEW,
    "preprint": PREPRINT, "none": PREPRINT,
}


def review_state(node: Any) -> str:
    """The review state of one node, from its metadata bytes alone.

    Reads :data:`REVIEW_STATUS_KEY` first because an ingest that consulted
    OpenReview knows more than we can infer. Falls back to
    "has an arxiv id and nothing else" -> :data:`PREPRINT`, which is the
    honest reading of a document we fetched from a preprint server and never
    looked up anywhere.

    An unrecognised status is :data:`UNKNOWN`, never a guess: a venue string
    this module has not seen must not be silently promoted to peer reviewed.
    """
    meta = getattr(node, "metadata", None) or {}
    raw = str(meta.get(REVIEW_STATUS_KEY, "") or "").strip().casefold()
    if raw:
        return _DIRECT.get(raw.replace("-", "_").replace(" ", "_"), UNKNOWN)
    if meta.get(OPENREVIEW_KEY):
        # Known to OpenReview but carrying no decision: submitted, undecided.
        return UNDER_REVIEW
    if meta.get(ARXIV_KEY):
        return PREPRINT
    return UNKNOWN


def passes(node: Any, *, require: Optional[Iterable[str]] = None) -> bool:
    """Does ``node`` survive a review filter?

    ``require=None`` admits everything — the default, because a filter that is
    on by default would silently drop evidence a caller never asked to drop.
    Otherwise ``require`` is the set of states admitted, named explicitly. There
    is deliberately no "minimum level": :data:`REVIEW_STATES` is an order, not a
    scale, and a caller asking for peer-reviewed evidence should say whether
    they also accept preprints rather than inheriting an inequality.
    """
    if require is None:
        return True
    wanted = {str(s).strip().casefold() for s in require}
    unknown = wanted - set(REVIEW_STATES)
    if unknown:
        raise ValueError(
            f"unknown review state(s) {sorted(unknown)}; "
            f"expected some of {list(REVIEW_STATES)}"
        )
    return review_state(node) in wanted


def partition(nodes: Sequence[Any], *,
              require: Iterable[str]) -> Tuple[List[Any], List[Any]]:
    """``(kept, dropped)`` under a filter — BOTH halves, on purpose.

    What a filter removed is as much a result as what it left. A claim whose
    only support was three preprints, asked for under peer review, should report
    "nothing survives, and here is what was lost" rather than an empty list that
    reads like the claim was never supported at all.
    """
    kept, dropped = [], []
    for node in nodes:
        (kept if passes(node, require=require) else dropped).append(node)
    return kept, dropped


def census(nodes: Iterable[Any]) -> Dict[str, int]:
    """How many nodes sit in each state. Every state present, zeros included.

    Zeros are kept so a report cannot silently omit a state — a census showing
    no ``rejected`` key reads as "we did not check" where ``rejected: 0`` reads
    as "we checked and found none", and those are different claims.
    """
    counts = {state: 0 for state in REVIEW_STATES}
    for node in nodes:
        counts[review_state(node)] += 1
    return counts


__all__ = [
    "ARXIV_KEY", "OPENREVIEW_KEY", "PEER_REVIEWED", "PREPRINT", "REJECTED",
    "REVIEW_STATES", "REVIEW_STATUS_KEY", "UNDER_REVIEW", "UNKNOWN", "VENUE_KEY",
    "census", "partition", "passes", "review_state",
]

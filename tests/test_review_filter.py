"""Peer-review status as a filter over evidence.

Pure: no network, no LLM, no compiled graph. Review status is written into node
metadata at ingest and read here as bytes, which is what lets `verify_claim`
gain a review filter without losing its documented property of being a pure
function of the graph.
"""

from __future__ import annotations

import pytest

from tesserae.review import (
    PEER_REVIEWED,
    PREPRINT,
    REJECTED,
    REVIEW_STATES,
    UNDER_REVIEW,
    UNKNOWN,
    census,
    partition,
    passes,
    review_state,
)


class _Node:
    def __init__(self, **metadata):
        self.metadata = metadata


def test_an_arxiv_preprint_is_neutral_and_not_a_rejection():
    """The distinction the whole vocabulary exists to protect.

    Most arXiv preprints were never submitted anywhere OpenReview can see, so a
    missing record is a paper we did not FIND, not a paper that was refused.
    Collapsing the two would turn "no evidence of review" into "evidence of
    failed review" — and that inference is wrong for the large majority of
    preprints.
    """
    assert review_state(_Node(arxiv_id="2510.27246")) == PREPRINT
    assert review_state(_Node()) == UNKNOWN
    assert review_state(_Node(review_status="reject")) == REJECTED
    assert PREPRINT != REJECTED and UNKNOWN != REJECTED


def test_rejection_is_information_only_openreview_can_carry():
    """arXiv cannot express it; that is the reason for the second source."""
    for verdict in ("reject", "rejected", "desk_reject", "withdrawn"):
        assert review_state(_Node(review_status=verdict)) == REJECTED


def test_acceptance_forms_all_read_as_peer_reviewed():
    for verdict in ("Accept", "accepted", "poster", "oral", "spotlight"):
        assert review_state(_Node(review_status=verdict)) == PEER_REVIEWED


def test_an_unrecognised_status_is_unknown_and_never_promoted():
    """A venue string nobody has seen must not become peer review by default."""
    assert review_state(_Node(review_status="some-new-track")) == UNKNOWN
    assert review_state(_Node(venue="Journal of Things")) == UNKNOWN, (
        "a venue name alone is not a decision — plenty of papers name a venue "
        "they were never accepted to"
    )


def test_an_openreview_record_with_no_decision_is_under_review():
    assert review_state(_Node(openreview_id="AbC123")) == UNDER_REVIEW


def test_no_filter_admits_everything():
    """A filter that were on by default would drop evidence nobody asked to drop."""
    nodes = [_Node(arxiv_id="a"), _Node(review_status="reject"), _Node()]
    assert all(passes(n) for n in nodes)


def test_the_filter_reports_what_it_dropped_as_well_as_what_it_kept():
    """A claim supported only by preprints must not read as unsupported.

    `partition` returns both halves so "nothing survives peer review, and here
    is the preprint evidence that was lost" can be said. An empty list alone
    reads as "no evidence exists", which is a different and wrong claim.
    """
    nodes = [_Node(review_status="Accept"), _Node(arxiv_id="x"), _Node(arxiv_id="y")]
    kept, dropped = partition(nodes, require=[PEER_REVIEWED])
    assert len(kept) == 1 and len(dropped) == 2

    preprints_only = [_Node(arxiv_id="x"), _Node(arxiv_id="y")]
    kept, dropped = partition(preprints_only, require=[PEER_REVIEWED])
    assert kept == [] and len(dropped) == 2, (
        "the caller must be able to tell 'filtered away' from 'never existed'"
    )


def test_the_filter_names_states_rather_than_taking_a_threshold():
    """REVIEW_STATES is an ORDER, not a scale, so there is no minimum level.

    A caller asking for reviewed evidence has to say whether preprints also
    count, instead of inheriting an inequality nobody wrote down.
    """
    nodes = [_Node(review_status="Accept"), _Node(arxiv_id="x")]
    assert len(partition(nodes, require=[PEER_REVIEWED])[0]) == 1
    assert len(partition(nodes, require=[PEER_REVIEWED, PREPRINT])[0]) == 2

    with pytest.raises(ValueError, match="unknown review state"):
        passes(nodes[0], require=["probably_fine"])


def test_the_census_keeps_zeros_so_a_state_cannot_go_unmentioned():
    """`rejected: 0` is 'we checked'; a missing key is 'we did not'."""
    counts = census([_Node(arxiv_id="x")])
    assert set(counts) == set(REVIEW_STATES)
    assert counts[PREPRINT] == 1 and counts[REJECTED] == 0

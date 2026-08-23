"""Vetting state as a filter over evidence, and how it travels down a chain.

Pure: no network, no LLM, no compiled graph. State is written into node metadata
at ingest and read here as bytes, which is what lets `verify_claim` gain a
vetting filter without losing its documented property of being a pure function
of the graph.
"""

from __future__ import annotations

import pytest

from tesserae.vetting import (
    PENDING,
    REJECTED,
    UNKNOWN,
    UNVETTED,
    VETTED,
    VETTING_STATES,
    best_state,
    census,
    is_source,
    partition,
    passes,
    support,
    vetting_state,
)


class _Node:
    def __init__(self, id="n", **metadata):
        self.id = id
        self.metadata = metadata


class _Edge:
    def __init__(self, source, target, type):
        self.source, self.target, self.type = source, target, type


class _Graph:
    def __init__(self, nodes, edges):
        self.nodes, self.edges = nodes, edges


# ----------------------------------------------------------------- the states


def test_unvetted_is_neutral_and_is_not_a_rejection():
    """The distinction the whole vocabulary exists to protect.

    A preprint nobody submitted, and a blog post nobody fact-checked, have not
    been REFUSED — they have not been looked at. Collapsing the two turns "no
    evidence of review" into "evidence of failed review", which is wrong for the
    large majority of unvetted sources.
    """
    assert vetting_state(_Node(vetting_state=UNVETTED)) == UNVETTED
    assert vetting_state(_Node()) == UNKNOWN
    assert UNVETTED != REJECTED and UNKNOWN != REJECTED


def test_an_unrecognised_state_is_unknown_and_never_promoted():
    """Venue jargon belongs to a provider, not here.

    "spotlight" is a fact about machine-learning conferences. A news or legal
    provider would bring its own words, and core must not privilege either.
    """
    for jargon in ("poster", "spotlight", "desk_reject", "fact_checked_true"):
        assert vetting_state(_Node(vetting_state=jargon)) == UNKNOWN


def test_the_legacy_academic_names_still_read():
    """Graphs written before the generalisation must not silently go UNKNOWN."""
    assert vetting_state(_Node(vetting_state="peer_reviewed")) == VETTED
    assert vetting_state(_Node(vetting_state="preprint")) == UNVETTED
    assert vetting_state(_Node(vetting_state="under_review")) == PENDING


# ----------------------------------------------------------------- the filter


def test_no_filter_admits_everything():
    """A filter on by default would drop evidence nobody asked to drop."""
    nodes = [_Node(vetting_state=UNVETTED), _Node(vetting_state=REJECTED), _Node()]
    assert all(passes(n) for n in nodes)


def test_the_filter_reports_what_it_dropped_as_well_as_what_it_kept():
    """A claim supported only by unvetted sources must not read as unsupported."""
    nodes = [_Node(vetting_state=VETTED), _Node(vetting_state=UNVETTED),
             _Node(vetting_state=UNVETTED)]
    kept, dropped = partition(nodes, require=[VETTED])
    assert len(kept) == 1 and len(dropped) == 2

    kept, dropped = partition(nodes[1:], require=[VETTED])
    assert kept == [] and len(dropped) == 2, (
        "the caller must be able to tell 'filtered away' from 'never existed'"
    )


def test_the_filter_names_states_rather_than_taking_a_threshold():
    """VETTING_STATES is an ORDER, not a scale, so there is no minimum level."""
    nodes = [_Node(vetting_state=VETTED), _Node(vetting_state=UNVETTED)]
    assert len(partition(nodes, require=[VETTED])[0]) == 1
    assert len(partition(nodes, require=[VETTED, UNVETTED])[0]) == 2

    with pytest.raises(ValueError, match="unknown vetting state"):
        passes(nodes[0], require=["probably_fine"])


def test_the_census_keeps_zeros_so_a_state_cannot_go_unmentioned():
    """`rejected: 0` is 'we checked'; a missing key is 'we did not'."""
    counts = census([_Node(vetting_state=UNVETTED)])
    assert set(counts) == set(VETTING_STATES)
    assert counts[UNVETTED] == 1 and counts[REJECTED] == 0


# --------------------------------------------------------- provenance chains


def _n(i, **m):
    return _Node(i, **m)


def test_a_claim_is_a_conduit_not_a_weak_link():
    """A claim is not a publishable unit and has no vetting state of its own.

    Counting its missing metadata as UNKNOWN was the first version of this
    module, and it made the filter useless: almost no claim node carries vetting
    metadata, so every chain resolved to UNKNOWN and nothing was ever admitted.
    """
    claim, paper, venue = _n("C"), _n("P", vetting_state=VETTED), _n("V", vetting_state=VETTED)
    graph = _Graph([claim, paper, venue],
                   [_Edge("C", "P", "evidenced_by"), _Edge("P", "V", "cites")])
    assert is_source(claim) is False and is_source(paper) is True
    assert best_state(claim, graph) == VETTED


def test_independent_sources_corroborate_rather_than_drag_each_other_down():
    """Two sources is STRONGER than one; a weakest-link rule said weaker.

    Strongest ACROSS paths, weakest WITHIN one. And the census keeps both, so
    disagreement is surfaced rather than averaged — one retracted source beside
    one reviewed source is exactly what a reader needs to see.
    """
    claim = _n("C")
    good, pre = _n("P1", vetting_state=VETTED), _n("P2", vetting_state=UNVETTED)
    graph = _Graph([claim, good, pre],
                   [_Edge("C", "P1", "evidenced_by"), _Edge("C", "P2", "evidenced_by")])
    assert best_state(claim, graph) == VETTED
    assert support(claim, graph)[VETTED] == 1
    assert support(claim, graph)[UNVETTED] == 1

    bad = _n("P3", vetting_state=REJECTED)
    graph = _Graph([claim, good, bad],
                   [_Edge("C", "P1", "evidenced_by"), _Edge("C", "P3", "evidenced_by")])
    counts = support(claim, graph)
    assert counts[VETTED] == 1 and counts[REJECTED] == 1, (
        "a retraction beside a reviewed source must be surfaced, not averaged"
    )


def test_the_weakest_link_still_governs_within_one_path():
    """A paragraph is no better vetted than the study it quotes."""
    claim, para = _n("C"), _n("Para")
    bad, venue = _n("P", vetting_state=REJECTED), _n("V", vetting_state=VETTED)
    graph = _Graph([claim, para, bad, venue],
                   [_Edge("C", "Para", "quotes"), _Edge("Para", "P", "quotes"),
                    _Edge("P", "V", "cites")])
    assert best_state(claim, graph) == REJECTED


def test_a_distilled_upper_layer_resolves_to_the_sources_beneath_it():
    """What keeps an abstraction layer falsifiable.

    Louvain gives this graph a real dendrogram and every level's summary is an
    assertion about the level beneath it. `part_of` is minted member -> whole,
    so a summary reaches its members only by walking it backwards — and a
    summary that cannot resolve to its sources is an unfalsifiable claim wearing
    a node type, at exactly the layer an agent is most likely to read.
    """
    summary = _n("CommunitySummary:1")
    c1, c2 = _n("Claim:1"), _n("Claim:2")
    good, pre = _n("P1", vetting_state=VETTED), _n("P2", vetting_state=UNVETTED)
    graph = _Graph([summary, c1, c2, good, pre], [
        _Edge("Claim:1", "CommunitySummary:1", "part_of"),
        _Edge("Claim:2", "CommunitySummary:1", "part_of"),
        _Edge("Claim:1", "P1", "evidenced_by"),
        _Edge("Claim:2", "P2", "evidenced_by"),
    ])
    assert best_state(summary, graph) == VETTED
    counts = support(summary, graph)
    assert counts[VETTED] == 1 and counts[UNVETTED] == 1, (
        "the summary must report what each branch beneath it rests on"
    )


def test_filtering_on_the_graph_drops_what_filtering_on_the_node_would_keep():
    claim = _n("C", vetting_state=VETTED)
    bad = _n("P", vetting_state=REJECTED)
    graph = _Graph([claim, bad], [_Edge("C", "P", "evidenced_by")])
    assert partition([claim], require=[VETTED])[0] == [claim]
    assert partition([claim], require=[VETTED], graph=graph)[0] == []


def test_a_cycle_is_visited_once():
    a, b = _n("A", vetting_state=VETTED), _n("B", vetting_state=UNVETTED)
    graph = _Graph([a, b], [_Edge("A", "B", "cites"), _Edge("B", "A", "cites")])
    assert sum(support(a, graph).values()) == 1

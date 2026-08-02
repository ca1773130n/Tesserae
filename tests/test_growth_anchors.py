"""Regression guard on the growth eval's anchor matcher.

`evals/growth/probe_anchors.py` holds the guards that need a compiled graph (~75
min); these are the ones that do not, so a change to `resolve_anchor` that
loosens matching beyond word order fails here in seconds instead.

The shape being pinned: matching is loosened from "literal phrase occurs" to
"all content words present, any order" — but only in the LABEL, and only on
node types that assert something rather than name an entity. Both restrictions
are what keep the loosening from manufacturing paths, so both get a test that
fails if they are dropped.
"""

from __future__ import annotations

from evals.growth.run import ASSERTION_TYPES, HUB_TYPES, resolve_anchor

SRC = "papers/instant-ngp/abstract.md"
GROUNDED = {SRC}


def node(nid, type_, name, description="", aliases=None, source_path=SRC):
    return {"id": nid, "type": type_, "name": name, "description": description,
            "aliases": aliases or [], "source_path": source_path}


GRAPH = {"nodes": [
    # reordered + punctuated label: the case the substring matcher misses
    node("claim", "EvidenceSpan", "Evidence: training/rendering speed numbers"),
    # same words, but a type that names an entity rather than asserting
    node("entity", "Metric", "Evidence: training/rendering speed numbers"),
    # words present only in prose, on an assertion type
    node("prose", "ContributionClaim", "Orders-of-magnitude speedup",
         description="reduces training cost; speed is reported per scene"),
    # literal phrase in a description: layer 1, must keep resolving
    node("literal", "Model", "Instant-NGP",
         description="A hash encoding that improves training speed."),
    # only one of the anchor's two words
    node("partial", "ContributionClaim", "Training converges in seconds"),
    # right label, wrong slice
    node("ungrounded", "EvidenceSpan", "Evidence: training/rendering speed numbers",
         source_path="papers/not-staged/abstract.md"),
]}


def resolve(anchor):
    return resolve_anchor(GRAPH, anchor, GROUNDED)


def test_word_order_and_punctuation_no_longer_hide_a_claim():
    """The defect this matcher was changed to fix: `hash-encoding`'s answer sat
    in the graph under a label the literal phrase does not appear in."""
    assert "claim" in resolve("training speed")


def test_substring_layer_is_unchanged():
    """Layer 1 must stay byte-identical — nothing that resolved before may stop."""
    assert "literal" in resolve("training speed")
    assert resolve("hash encoding") == {"literal"}


def test_loosened_matching_is_gated_to_assertion_types():
    """An entity node with the identical label must NOT match. Widening entity
    anchors is how a shared metric becomes a fake relationship — the failure the
    second control exists to catch."""
    assert "entity" not in resolve("training speed")


def test_loosened_matching_reads_the_label_not_the_description():
    """Descriptions are prose, where unrelated terms co-occur freely."""
    assert "prose" not in resolve("training speed")


def test_every_anchor_word_is_required():
    assert "partial" not in resolve("training speed")


def test_grounding_still_filters_first():
    assert "ungrounded" not in resolve("training speed")


def test_assertion_types_never_route():
    """The safety argument is that the loosened family is disjoint from the
    family the path search refuses to traverse. run.py asserts this at import;
    this states why, so a merge that unions the sets fails with a reason."""
    assert ASSERTION_TYPES.isdisjoint(HUB_TYPES)


def test_resolution_is_repeatable_and_leaves_the_graph_alone():
    import copy

    before = copy.deepcopy(GRAPH)
    assert resolve("training speed") == resolve("training speed")
    assert GRAPH == before

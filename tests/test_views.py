"""Tests for tesserae.retrieval.views — the named edge-partition registry.

The partition is the deliverable of roadmap step 7: these tests pin it so a
vocabulary change cannot silently drop an edge type from every view, and so
the mechanics ``weights_for``/``traversable_edge_types`` rely on stay true.
"""

from __future__ import annotations

import pytest

from tesserae.research_graph import ALLOWED_EDGE_TYPES, CAUSAL_EDGE_TYPES
from tesserae.retrieval.ppr import DEFAULT_EDGE_TYPE_WEIGHTS
from tesserae.retrieval.views import (
    VIEW_EXCLUDED_EDGE_TYPES,
    VIEWS,
    traversable_edge_types,
    weights_for,
)


def test_partition_covers_the_vocabulary_exactly_once() -> None:
    """The four views plus the excluded bucket partition ALLOWED_EDGE_TYPES.

    This is the loud gate the registry's module docstring promises: adding an
    edge type to the vocabulary without deciding its view fails HERE, instead
    of the new type silently landing in no view (union check) or in two
    (disjointness check).
    """
    buckets = list(VIEWS.values()) + [VIEW_EXCLUDED_EDGE_TYPES]
    union = frozenset().union(*buckets)
    assert union == ALLOWED_EDGE_TYPES, (
        f"unassigned edge types: {sorted(ALLOWED_EDGE_TYPES - union)}; "
        f"stale (no longer in the vocabulary): {sorted(union - ALLOWED_EDGE_TYPES)}"
    )
    assert sum(len(b) for b in buckets) == len(union), (
        "an edge type appears in more than one bucket"
    )


def test_the_causal_view_keeps_its_observed_anchor() -> None:
    """``recovers`` (the step-6 observed-outcome edge) anchors the causal view,
    and its 2.0 default weight outranks the text-asserted causal types."""
    assert CAUSAL_EDGE_TYPES <= VIEWS["causal"]
    for edge_type in CAUSAL_EDGE_TYPES:
        asserted = VIEWS["causal"] - CAUSAL_EDGE_TYPES
        assert all(
            DEFAULT_EDGE_TYPE_WEIGHTS[edge_type]
            > DEFAULT_EDGE_TYPE_WEIGHTS.get(other, 1.0)
            for other in asserted
        )


def test_weights_for_zeroes_exactly_the_non_members() -> None:
    for view_name, members in VIEWS.items():
        weights = weights_for(view_name)
        assert set(weights) == set(ALLOWED_EDGE_TYPES - members)
        assert all(w == 0.0 for w in weights.values())
        # Members are deliberately ABSENT so they keep their defaults
        # (session upweights, recovers 2.0) through the ppr merge.
        assert not (set(weights) & members)


def test_weights_for_unknown_view_fails_loud() -> None:
    with pytest.raises(ValueError) as exc:
        weights_for("provenance")
    message = str(exc.value)
    assert "provenance" in message
    for view_name in VIEWS:
        assert view_name in message


def test_default_weights_are_all_positive() -> None:
    """``traversable_edge_types`` treats an absent type as traversable because
    every default weight is positive. If a zero default ever lands, that
    shortcut breaks — this is the tripwire."""
    assert all(w > 0.0 for w in DEFAULT_EDGE_TYPE_WEIGHTS.values())


def test_traversable_edge_types_mirrors_the_ppr_merge() -> None:
    view_weights = weights_for("semantic")
    traversable = traversable_edge_types(view_weights)
    assert traversable == VIEWS["semantic"]
    # A caller override can resurrect an out-of-view type...
    resurrected = dict(view_weights)
    resurrected["summarizes"] = 0.5
    assert "summarizes" in traversable_edge_types(resurrected)
    # ...and silence a member.
    silenced = dict(view_weights)
    silenced["uses"] = 0.0
    assert "uses" not in traversable_edge_types(silenced)


def test_every_view_is_a_real_subgraph() -> None:
    """No view may approximate the whole graph — the spec's central warning.

    The abstraction/provenance types that dominate the edge count stay out of
    every view; being excluded from views does NOT down-weight them in
    view-less traversal (DEFAULT_EDGE_TYPE_WEIGHTS is untouched)."""
    for heavy in ("summarizes", "evidenced_by"):
        assert heavy in VIEW_EXCLUDED_EDGE_TYPES
        assert all(heavy not in members for members in VIEWS.values())


def test_the_view_names_have_one_source_of_truth() -> None:
    """The registry's names surface in two more places — the MCP schema enum
    and the CLI --view choices. Both must read from VIEWS, so a view added
    to the registry is advertised and accepted everywhere for free."""
    from tesserae.cli import _build_context_parser
    from tesserae.mcp_server import LLMWikiMCPServer

    by_name = {t["name"]: t for t in LLMWikiMCPServer().list_tools()}
    schema_prop = by_name["compile_context"]["inputSchema"]["properties"]["view"]
    single, many = schema_prop["anyOf"]
    assert single["enum"] == list(VIEWS)
    assert many["items"]["enum"] == list(VIEWS)

    parser = _build_context_parser()
    view_action = next(a for a in parser._actions if a.dest == "view")
    assert list(view_action.choices) == list(VIEWS)

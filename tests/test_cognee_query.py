"""Cognee search-type resolution (Cognee 1.0 dropped the V1 INSIGHTS type)."""

from __future__ import annotations

import pytest


def test_insights_aliases_to_graph_completion_and_passthrough():
    cognee = pytest.importorskip("cognee")
    from tesserae.cognee_query import _search_type

    # Cognee 1.0 removed INSIGHTS -> Tesserae's historical default must still resolve.
    assert _search_type("INSIGHTS") == cognee.SearchType.GRAPH_COMPLETION
    assert _search_type("insights") == cognee.SearchType.GRAPH_COMPLETION   # case-insensitive
    assert _search_type(None) == cognee.SearchType.GRAPH_COMPLETION         # default
    assert _search_type("CHUNKS") == cognee.SearchType.CHUNKS               # valid type passes through


def test_unknown_search_type_still_raises_clearly():
    pytest.importorskip("cognee")
    from tesserae.cognee_query import _search_type

    with pytest.raises(ValueError, match="Unknown Cognee search type"):
        _search_type("DEFINITELY_NOT_A_TYPE")

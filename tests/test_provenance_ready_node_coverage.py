"""``_provenance_ready`` must REQUIRE the node-coverage API, not merely use it.

The predicate probed ``provenance_covers_nodes`` behind ``hasattr``, so an
injected store that simply did not implement it was declared ready — absence of
the coverage API read as evidence of coverage.

That leniency stopped being academic when the predicate gained a second
consumer. It used to gate only the experimental incremental differ
(``incremental_compile``, off by default); it now also gates the
``fallback_only`` scoped retry, reachable in the DEFAULT config on
``compile --changed-only --retry-fallbacks``, whose first act is a
``delete_nodes_by_source_with_edges`` tombstone against the prior graph. An
uncovered store there deletes co-owned nodes outright instead of falling back to
the always-correct full recompile.

Lives in its own module rather than ``test_provenance_readiness.py`` only to
keep concurrent edits to that file conflict-free; it is the same surface.
"""

from __future__ import annotations

from tesserae.project import ProjectWiki


class _NoNodeCoverageStore:
    """The full edge-aware surface MINUS ``provenance_covers_nodes``.

    Every other method answers optimistically, so the only thing that can
    reject this store is the missing coverage API itself.
    """

    def delete_nodes_by_source(self, *a, **k):  # noqa: ANN001
        return set()

    def record_provenance_many(self, *a, **k):  # noqa: ANN001
        return None

    def delete_nodes_by_source_with_edges(self, *a, **k):  # noqa: ANN001
        return set(), set()

    def record_edge_provenance_many(self, *a, **k):  # noqa: ANN001
        return None

    def provenance_covers_edges(self, *a, **k):  # noqa: ANN001
        return True

    def has_node_provenance_rows(self):
        return True


class _CoveringStore(_NoNodeCoverageStore):
    """Same surface plus a truthful ``provenance_covers_nodes`` — the control."""

    def __init__(self, covered: set[str]):
        self._covered = covered

    def provenance_covers_nodes(self, node_ids):  # noqa: ANN001
        return set(node_ids) <= self._covered


def test_store_without_node_coverage_api_is_not_ready() -> None:
    """No coverage API => not ready, even for a node it never recorded."""
    assert (
        ProjectWiki._provenance_ready(
            _NoNodeCoverageStore(), ["n1"], prior_edge_triples=[("n1", "rel", "n2")]
        )
        is False
    )


def test_store_with_truthful_node_coverage_is_still_accepted() -> None:
    """The requirement must not become a blanket rejection of injected stores."""
    assert (
        ProjectWiki._provenance_ready(
            _CoveringStore({"n1"}), ["n1"], prior_edge_triples=[("n1", "rel", "n2")]
        )
        is True
    )
    assert (
        ProjectWiki._provenance_ready(
            _CoveringStore({"n1"}), ["n1", "n2"], prior_edge_triples=[("n1", "rel", "n2")]
        )
        is False
    )

"""Named traversal views over the edge vocabulary (roadmap step 7).

A *view* is a named subset of :data:`~tesserae.research_graph.ALLOWED_EDGE_TYPES`
— the same memory traversed as a semantic, temporal, causal, or entity graph
(the MAGMA borrowing: one memory, four orthogonal projections). A view is NOT a
new ranking algorithm: :func:`weights_for` resolves a view name to explicit
zero-weights for every edge type OUTSIDE the view, and
:func:`tesserae.retrieval.ppr.personalized_pagerank` already deletes
zero-weight edge classes from the walk. Member types are deliberately NOT
listed, so they keep their ``DEFAULT_EDGE_TYPE_WEIGHTS`` (session edges stay
pre-upweighted, ``recovers`` keeps its observed-causation 2.0 over the
text-asserted causal pair).

The partition is judgement, not mechanics — the spec's own warning. The
decisions that shape it:

* ``summarizes`` + ``evidenced_by`` (~50% of all edges) are abstraction and
  provenance, not domain semantics: they belong to NO view, or the semantic
  view becomes the whole graph again. Summary/evidence nodes stay reachable
  through direct search, ``drill_down``/``verify_claim``, and view-less
  traversal — ``DEFAULT_EDGE_TYPE_WEIGHTS`` is untouched by this module.
* The causal view is wider than ``CAUSAL_EDGE_TYPES``: ``{recovers}`` alone
  would be a view with (today) zero live edges. ``resolved_by`` and
  ``attributes_improvement_to`` serve the same "why did this break / what
  fixed it" intent; ``recovers`` still outranks them through its 2.0 default
  weight, so observed causation beats text-asserted causation.
  ``CAUSAL_EDGE_TYPES`` remains the write-path gate — that distinction is
  epistemic (observed vs asserted), this one is traversal intent.
* ``contradicts_claim`` sits in temporal, split from ``supports_claim``
  (semantic): ``temporal.INVALIDATING_PREDICATES`` already assigns it a
  validity-ending job, and at 31 edges its loss from semantic is nil while
  its presence in the sparse temporal view is material.
* Structural/code composition (``part_of``, ``contains``, ``calls``,
  ``imports``, ...) is entity: relations among named concrete things — which
  also keeps charter's synthetic ``part_of`` quotient edges out of the
  conceptual walk.
* ``user_link`` stays traversable (semantic): its source comment says it is
  "used for graph reachability" — zero weight in every view is the one
  assignment that would break its documented function.

Tests pin the partition (``tests/test_views.py``): the five buckets must
cover ``ALLOWED_EDGE_TYPES`` exactly, so adding an edge type to the
vocabulary without deciding its view fails CI loudly rather than silently
dropping it from every view.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Mapping

from ..research_graph import ALLOWED_EDGE_TYPES

#: "What is X / how do these ideas relate" — the conceptual research layer.
SEMANTIC_VIEW: FrozenSet[str] = frozenset({
    "achieves_score",
    "addresses",
    "belongs_to_approach_family",
    "compares_against",
    "criticizes",
    "defines",
    "derived_from",
    "documents",
    "evaluated_on",
    "extends",
    "has_limitation",
    "improves_on",
    "introduces",
    "is_a",
    "optimizes_for",
    "references",
    "reports_result",
    "shares_concept_with",
    "subfield_of",
    "supports_claim",
    "user_link",
    "uses",
    "uses_dataset",
    "uses_metric",
})

#: "When did this happen / what was true at T" — sessions are the graph's
#: time-carriers, so their edges live here rather than in excluded despite
#: their provenance flavor: excluding them would disconnect the timeline.
TEMPORAL_VIEW: FrozenSet[str] = frozenset({
    "contradicts_claim",
    "declining_in",
    "derived_from_session",
    "discussed_in",
    "emerged_after",
    "precedes",
    "rising_in",
    "supersedes",
})

#: "Why did this break / what fixed it" — anchored by the step-6 ``recovers``
#: edge (observed outcome), widened by the two text-asserted causal types.
CAUSAL_VIEW: FrozenSet[str] = frozenset({
    "attributes_improvement_to",
    "recovers",
    "resolved_by",
})

#: "Which named concrete things are involved" — actors, orgs, repos, code
#: symbols, and the structural composition between them.
ENTITY_VIEW: FrozenSet[str] = frozenset({
    "authored_by",
    "calls",
    "contains",
    "declared_in",
    "decorates",
    "discusses",
    "exports",
    "implemented_in",
    "implements",
    "imports",
    "inherits_from",
    "instantiates",
    "overrides",
    "part_of",
    "performed_by",
    "released_by",
    "reports_to",
    "returns",
    "type_of",
})

#: Abstraction/provenance edges in NO view — ~50% of all edges. Any view that
#: admits these approximates the full graph, which is exactly what a view
#: exists to avoid. They stay at full default weight in view-less traversal.
VIEW_EXCLUDED_EDGE_TYPES: FrozenSet[str] = frozenset({
    "evidenced_by",
    "mentioned_in",
    "summarizes",
    "synthesizes",
})

#: The registry: view name -> member edge types. The four members plus
#: :data:`VIEW_EXCLUDED_EDGE_TYPES` partition :data:`ALLOWED_EDGE_TYPES`
#: (pinned by tests/test_views.py).
VIEWS: Dict[str, FrozenSet[str]] = {
    "semantic": SEMANTIC_VIEW,
    "temporal": TEMPORAL_VIEW,
    "causal": CAUSAL_VIEW,
    "entity": ENTITY_VIEW,
}


def weights_for(view: str) -> Dict[str, float]:
    """Resolve ``view`` to explicit ``0.0`` weights for every non-member type.

    The result is merged ONTO ``DEFAULT_EDGE_TYPE_WEIGHTS`` by
    :func:`~tesserae.retrieval.ppr.personalized_pagerank`, so member types —
    deliberately absent from the result — keep their default weights. The
    zeros must be explicit, not omitted: the PPR merge treats an absent type
    as default-weighted, never as deleted. Keys are sorted so the dict is
    deterministic by construction, whatever ``ALLOWED_EDGE_TYPES``'s set
    iteration order does under hash randomization.

    Raises ``ValueError`` for an unknown view, naming the valid ones.
    """
    members = VIEWS.get(view)
    if members is None:
        raise ValueError(
            f"unknown view {view!r} — valid views: {', '.join(sorted(VIEWS))}."
        )
    return {t: 0.0 for t in sorted(ALLOWED_EDGE_TYPES - members)}


def traversable_edge_types(weights: Mapping[str, float]) -> FrozenSet[str]:
    """The edge types whose post-merge PPR weight stays positive.

    Mirrors the ``personalized_pagerank`` merge semantics: an ``ALLOWED``
    type absent from ``weights`` keeps its default weight, and every
    ``DEFAULT_EDGE_TYPE_WEIGHTS`` value is positive (pinned by
    tests/test_views.py), so absence means traversable. This is the set the
    depth-neighbourhood BFS must be restricted to under a view — otherwise a
    node the view cannot reach within ``depth`` hops is still admitted into
    the neighbourhood through a deleted edge class and leaks into the bundle.
    """
    return frozenset(
        t for t in ALLOWED_EDGE_TYPES if float(weights.get(t, 1.0)) > 0.0
    )

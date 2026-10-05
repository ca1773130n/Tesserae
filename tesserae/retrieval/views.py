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
  ``imports``, ...) is entity: relations among named concrete things.
* ``user_link`` stays traversable (semantic): its source comment says it is
  "used for graph reachability" — zero weight in every view is the one
  assignment that would break its documented function.

Tests pin the partition (``tests/test_views.py``): every KNOWN edge type —
the core vocabulary plus the active types of the host's type registry
(0.42, T3) — has exactly one view or is excluded, so adding an edge type
without deciding its view fails CI loudly rather than silently dropping it
from every view.

Registry types (0.42, T3). A host registers edge types at runtime
(:mod:`tesserae.type_registry`). Each one resolves to exactly one bucket via
:func:`view_of`: the view its spec declares, else ``crossdomain`` when it is
one of :data:`CROSSDOMAIN_VIEW`'s names, else its core parent's view. The core
types never move. ``crossdomain`` has no core member at all — it exists for the
host's bridge relations (``transfers_to``, ``inspired_by``, ...), so on a graph
with no registry it is an empty walk, never a wrong one.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Mapping, Optional

from ..research_graph import ALLOWED_EDGE_TYPES
from ..type_registry import get_registry

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
    # ``retracts`` (step 10) sits here for the same reason
    # ``contradicts_claim`` does: it ends its target's validity — it is in
    # ``temporal.INVALIDATING_PREDICATES`` — and "what was true when" is the
    # question a retraction answers.
    "retracts",
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

#: "What moved between fields" (0.42, T3) — attested and proposed transfers of
#: an idea from one research domain into another. NONE of these is a core type:
#: they are the host's registry relations, and only reach a walk once
#: registered active. ``candidate_transfer`` is a generated hypothesis; PPR
#: already skips ``status="candidate"`` edges unless the caller opts in (T4).
CROSSDOMAIN_VIEW: FrozenSet[str] = frozenset({
    "analogous_to",
    "applies",
    "blends",
    "candidate_transfer",
    "inspired_by",
    "transfers_to",
})

#: The registry: view name -> STATIC member edge types. The four core views
#: plus :data:`VIEW_EXCLUDED_EDGE_TYPES` partition :data:`ALLOWED_EDGE_TYPES`;
#: ``crossdomain`` lists registry names only (pinned by tests/test_views.py).
#: Registry types join a view at runtime — :func:`view_members` is the live set.
VIEWS: Dict[str, FrozenSet[str]] = {
    "semantic": SEMANTIC_VIEW,
    "temporal": TEMPORAL_VIEW,
    "causal": CAUSAL_VIEW,
    "entity": ENTITY_VIEW,
    "crossdomain": CROSSDOMAIN_VIEW,
}

#: The bucket name :func:`view_of` returns for an excluded type.
EXCLUDED = "excluded"


def known_edge_types() -> FrozenSet[str]:
    """The core vocabulary plus the ACTIVE registry edge types."""
    return frozenset(ALLOWED_EDGE_TYPES) | get_registry().active_types("edge")


def _core_view_of(edge_type: str) -> Optional[str]:
    if edge_type in VIEW_EXCLUDED_EDGE_TYPES:
        return EXCLUDED
    for name, members in VIEWS.items():
        if edge_type in members and edge_type in ALLOWED_EDGE_TYPES:
            return name
    return None


def view_of(edge_type: str) -> Optional[str]:
    """The ONE bucket ``edge_type`` belongs to: a view name, or ``"excluded"``.

    ``None`` for a type that is neither core nor registered. A registry type
    takes its declared ``view``, else ``crossdomain`` for the names in
    :data:`CROSSDOMAIN_VIEW`, else its core parent's view (T3: "a registry type
    inherits its parent's view unless it declares one"). A declared view that
    is not a real bucket raises — a typo would otherwise drop the type from
    every walk.
    """
    if edge_type in ALLOWED_EDGE_TYPES:
        return _core_view_of(edge_type)
    registry = get_registry()
    spec = registry.get(edge_type)
    if spec is None or spec.kind != "edge":
        return None
    if spec.view:
        if spec.view != EXCLUDED and spec.view not in VIEWS:
            raise ValueError(
                f"registry edge type {edge_type!r} declares unknown view {spec.view!r} — "
                f"valid: {', '.join(sorted(VIEWS))}, {EXCLUDED}."
            )
        return spec.view
    if edge_type in CROSSDOMAIN_VIEW:
        return "crossdomain"
    parent = registry.core_parent(edge_type, "edge")
    return _core_view_of(parent) if parent else None


def view_members(view: str) -> FrozenSet[str]:
    """Every known edge type whose bucket is ``view`` (static core members plus
    the registry types :func:`view_of` places there)."""
    if view not in VIEWS:
        raise ValueError(
            f"unknown view {view!r} — valid views: {', '.join(sorted(VIEWS))}."
        )
    core = frozenset(t for t in VIEWS[view] if t in ALLOWED_EDGE_TYPES)
    extra = frozenset(
        t for t in get_registry().active_types("edge") if view_of(t) == view
    )
    return core | extra


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
    members = view_members(view)  # raises on an unknown view
    return {t: 0.0 for t in sorted(known_edge_types() - members)}


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
        t for t in known_edge_types() if float(weights.get(t, 1.0)) > 0.0
    )

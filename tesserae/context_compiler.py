"""On-demand context compilation — Pillar 3 (CTX-01).

``compile_context`` is a *pure* function that turns a free-form query (or a set
of explicit seeds) into a tailored, fully-cited, agent-ready markdown bundle by
COMPOSING the existing retrieval stack. No new ranking algorithm lives here.

Pipeline
--------
1. **Seed resolution** — explicit seeds (kept iff they exist in the graph)
   first, then :func:`tesserae.retrieval.hybrid.hybrid_search` results, deduped
   with stable order.
2. **PPR expansion** — :func:`tesserae.retrieval.ppr.personalized_pagerank`
   ranks the k-hop neighbourhood. If PPR returns nothing (disconnected seeds),
   we fall back to the seed order so the bundle is never empty.
3. **Budget-bound selection** — walk the PPR order, including each node's cited
   body until the next body would overflow ``budget`` (``budget <= 0`` = uncapped).
4. **Cited markdown assembly** — one section per selected node + a trailing
   ``## Citations`` block. The no-LLM body embeds NO wall-clock timestamp, so it
   is byte-identical for the same ``(graph, query, seeds, depth, budget)``.
5. **Optional LLM synthesis** — only when ``synthesize=True`` AND an
   ``ANTHROPIC_API_KEY`` is present; otherwise the deterministic assembly stands.

The returned :class:`ContextBundle` is in-memory only — nothing is written under
``.tesserae/`` (the bundle is an on-demand projection, not part of compile, and
must not perturb byte-idempotence).
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (Any, Callable, Dict, FrozenSet, List, Mapping, Optional,
                    Sequence, Set, Tuple)

from .graph_filters import superseded_ids
from .research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from .retrieval.hybrid import hybrid_search
from .retrieval.ppr import personalized_pagerank
from .wiki_projector import kind_for_node
from .wiki_store import WikiPageStore

# Modest default recency blend for INTERACTIVE ask (relevance still dominates at
# 0.75). 0 disables. Compiled/export paths leave it off entirely for determinism.
DEFAULT_RECENCY_WEIGHT = 0.25

_RECENCY_HALF_LIFE_DAYS = 30.0
_LEADING_DATE = re.compile(r"\s*(\d{4}-\d{2}-\d{2})")


def _parse_iso(value: object) -> Optional[datetime]:
    """Parse an ISO-8601 date/datetime string into an aware (UTC) datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _recency_score(node: ResearchNode, now: datetime) -> float:
    """Recency in ``[0, 1]`` for ranking. Anchor = a metadata timestamp, else a
    leading ``YYYY-MM-DD`` in the node name (session/synthesis titles carry one),
    else **neutral 0.5** — an UNDATED node is never treated as max-fresh. (Raw
    ``compute_decay_score`` returns 1.0 for undated nodes; synthesis nodes omit
    timestamps for byte-idempotence, so that would hand the offending 'Review ALL
    improvements' nodes top freshness — the exact bug this guards against.)"""
    meta = getattr(node, "metadata", None) or {}
    # Session nodes carry their date in ended_at/started_at, NOT first_seen_at —
    # omit those and a real session node anchors on nothing, falls through to
    # neutral, and the blend can't tell old from recent: the exact no-op a
    # reviewer caught against the reported nodes. Order = most-recent activity first.
    anchor = (
        _parse_iso(meta.get("last_accessed_at"))
        or _parse_iso(meta.get("first_seen_at"))
        or _parse_iso(meta.get("ended_at"))
        or _parse_iso(meta.get("started_at"))
    )
    if anchor is None:
        match = _LEADING_DATE.match(getattr(node, "name", "") or "")
        if match:
            anchor = _parse_iso(match.group(1))
    if anchor is None:
        return 0.5  # genuinely undated -> neutral, NOT fresh
    age_days = max((now - anchor).total_seconds() / 86400.0, 0.0)
    return math.exp(-math.log(2) * age_days / _RECENCY_HALF_LIFE_DAYS)

__all__ = [
    "compile_context",
    "fit_to_budget",
    "BudgetFit",
    "ContextBundle",
    "ContextCitation",
    "DEFAULT_HEADER_RESERVE",
    "DEFAULT_RECENCY_WEIGHT",
]


@dataclass(frozen=True)
class ContextCitation:
    """A single resolvable citation in a :class:`ContextBundle`."""

    node_id: str
    node_name: str
    source_path: Optional[str]
    wiki_kind: Optional[str]


@dataclass(frozen=True)
class ContextBundle:
    """The compiled, cited context document plus its provenance metadata."""

    query: str
    seeds_used: List[str] = field(default_factory=list)
    ranked_nodes: List[str] = field(default_factory=list)
    selected_nodes: List[str] = field(default_factory=list)
    citations: List[ContextCitation] = field(default_factory=list)
    body: str = ""
    synthesized: bool = False
    char_budget_used: int = 0
    char_budget_total: int = 0
    #: What each procedural pool reserved, when reservation ran. Pool type value
    #: -> ``None`` when no producer-made node of that type was in the
    #: neighbourhood, else ``{"node_id": str, "delivered": bool}``.
    #:
    #: ``delivered`` is not decoration. Reservation moves its pick to the front
    #: of the budget walk, but the walk still stops at the budget, so a reserved
    #: node can be dropped after being reserved. Reporting that pool as served
    #: would be the very defect this field exists to expose, relocated into the
    #: reporting: the caller reads procedural memory as delivered when nothing
    #: from that pool reached the bundle.
    #:
    #: ``None`` for the WHOLE field means one thing only: reservation never ran
    #: because ``multi_pool`` was off. Whenever ``multi_pool=True`` this is a
    #: dict keyed by every pool — including on the empty-seed early return,
    #: which used to return ``None`` and give that value a second meaning.
    pool_reservations: Optional[Dict[str, Optional[Dict[str, object]]]] = None


def _pool_order() -> Tuple[ResearchNodeType, ...]:
    """The procedural pools, in reservation order, from their one declaration.

    Read through a function rather than imported at module scope so the single
    source of truth stays ``research_graph`` at call time — importing the tuple
    once here would freeze a copy and re-create the duplicate this removes.
    """
    from .research_graph import PROCEDURAL_POOL_ORDER

    return PROCEDURAL_POOL_ORDER


def _empty_pool_reservations() -> Dict[str, Optional[Dict[str, object]]]:
    """Every pool, reserving nothing — the shape ``multi_pool=True`` always
    returns, so ``pool_reservations is None`` means only "reservation never
    ran"."""
    return {pool.value: None for pool in _pool_order()}


def _fetch_body(node: ResearchNode, store: Optional[WikiPageStore]) -> str:
    """Return the best available body text for ``node``, degrading gracefully.

    Prefer the projected wiki page body (when a ``store`` and a public wiki kind
    exist); fall back to the node description, then a minimal stub. Filesystem
    errors are swallowed (PITFALL 2 — degrade, never raise).
    """
    if store is not None:
        try:
            kind = kind_for_node(node)
            if kind:
                slug = store.slug_for(node.name)
                path = store.path_for(kind, slug)
                if path.exists():
                    return store.read_page(path).body
        except (FileNotFoundError, OSError):
            pass
    return node.description or f"_{node.type.value} node: {node.name}_"


_TRUNCATION_MARKER = "\n…[truncated]"


def _truncate_to_budget(body: str, budget: int) -> str:
    """Truncate ``body`` to ``<= budget`` chars at a word/newline boundary.

    Appends :data:`_TRUNCATION_MARKER` so the cut is visible to the agent, and
    guarantees the returned string (marker included) never exceeds ``budget``.
    Deterministic — no wall-clock — so byte-idempotence holds. When ``budget`` is
    too small to fit even the marker, fall back to a hard char slice (the marker
    would itself overflow), preserving the ``<= budget`` invariant.
    """
    if len(body) <= budget:
        return body
    keep = budget - len(_TRUNCATION_MARKER)
    if keep <= 0:
        # No room for the marker — hard slice to honour the budget.
        return body[:budget]
    head = body[:keep]
    # Prefer the last newline, then the last space, to land on a clean boundary.
    cut = max(head.rfind("\n"), head.rfind(" "))
    if cut > 0:
        head = head[:cut]
    return head + _TRUNCATION_MARKER


# --------------------------------------------------------------------------- CTX-01

#: Chars carved out of ``budget_chars`` before entry admission: the tool's own
#: header fields and the single ``+N more, cursor=K`` continuation line must
#: always fit inside it, whatever the entries do.
DEFAULT_HEADER_RESERVE = 600


@dataclass(frozen=True)
class BudgetFit:
    """Outcome of :func:`fit_to_budget` — the admitted entries plus drop math.

    ``entries`` preserves the input order (a strict prefix of the input;
    strings may be truncated in the default char mode). ``cursor`` is the index
    of the first dropped entry — i.e. the resume offset a paging caller feeds
    back. ``continuation`` is the single O(1) ``+N more, cursor=K`` line when
    anything was dropped, else ``None``. ``payload`` is set only in render mode
    (the final rendering the size check ran against).
    """

    entries: List[Any]
    dropped: int
    cursor: int
    continuation: Optional[str]
    payload: Optional[str] = None


def fit_to_budget(
    entries: Sequence[Any],
    budget_chars: int,
    header_reserve: int = DEFAULT_HEADER_RESERVE,
    *,
    render: Optional[Callable[[List[Any], int], str]] = None,
    drop_step: int = 32,
) -> BudgetFit:
    """Fit ``entries`` into ``budget_chars`` per invariant CTX-01 (§5.3).

    Default (char) mode — ``entries`` are strings:

    * each entry is individually truncated to ``min(len(entry),
      budget_chars // 8)`` (via :func:`_truncate_to_budget`, so the cut lands
      on a word/newline boundary and is marked);
    * deterministic input order is preserved — admission is greedy and stops
      BEFORE the first entry that would overflow
      ``budget_chars - header_reserve`` (kept entries are always a prefix);
    * exactly one continuation line ``+N more, cursor=K`` reports the drop;
    * ``budget_chars <= 0`` is the uncapped passthrough — entries returned
      byte-identical, nothing dropped (compile_context's existing ``budget=0``
      invariant, preserved verbatim).

    Render mode — ``render`` given, ``entries`` arbitrary: reproduces the
    agent-distill ``ARTIFACT_CHAR_BUDGET`` assemble-then-truncate math
    byte-for-byte (the distill determinism tests are the oracle). ``render``
    maps ``(kept_entries, dropped_count)`` to the full payload; while the
    payload overflows ``budget_chars``, ``max(1, min(len(kept), drop_step))``
    entries are dropped from the tail and the payload is re-rendered.
    ``header_reserve`` is unused here — the rendering carries its own header —
    and the final rendering is returned as ``payload``.

    Deterministic — no wall-clock, no randomness — in both modes.
    """
    if render is not None:
        kept_any: List[Any] = list(entries)
        dropped = 0
        while True:
            payload = render(kept_any, dropped)
            if budget_chars <= 0 or len(payload) <= budget_chars or not kept_any:
                break
            drop = max(1, min(len(kept_any), drop_step))
            kept_any = kept_any[:-drop]
            dropped += drop
        continuation = (
            f"+{dropped} more, cursor={len(kept_any)}" if dropped else None
        )
        return BudgetFit(
            entries=kept_any,
            dropped=dropped,
            cursor=len(kept_any),
            continuation=continuation,
            payload=payload,
        )

    if budget_chars <= 0:
        return BudgetFit(
            entries=list(entries), dropped=0, cursor=len(entries), continuation=None
        )

    per_entry_cap = budget_chars // 8
    available = max(0, budget_chars - header_reserve)
    kept: List[Any] = []
    used = 0
    for entry in entries:
        text = (
            entry
            if len(entry) <= per_entry_cap
            else _truncate_to_budget(entry, per_entry_cap)
        )
        if used + len(text) > available:
            break
        kept.append(text)
        used += len(text)
    dropped = len(entries) - len(kept)
    continuation = f"+{dropped} more, cursor={len(kept)}" if dropped else None
    return BudgetFit(
        entries=kept, dropped=dropped, cursor=len(kept), continuation=continuation
    )


def _neighborhood_within_depth(
    graph: ResearchGraph,
    seed_ids: Sequence[str],
    depth: int,
    edge_types: Optional[FrozenSet[str]] = None,
) -> Set[str]:
    """Return the set of node ids reachable from any seed in ``<= depth`` hops.

    BFS over the UNDIRECTED edge set (each edge traversable both ways, matching
    ``personalized_pagerank``'s default ``directed=False``). ``depth <= 0``
    collapses to just the seeds themselves. The returned set always contains the
    valid seeds so PPR seeded on them never runs over an empty subgraph.

    ``edge_types`` (default ``None`` = every edge, byte-identical to the
    pre-view behaviour) restricts the adjacency to those edge types — the
    mandatory companion of a view-restricted PPR walk. Without it, a node
    within ``depth`` hops ONLY through a zero-weighted edge class is still
    admitted here, and if the view's walk reaches it by a longer path it
    surfaces in the bundle although the view cannot reach it within depth.
    """
    adjacency: Dict[str, Set[str]] = {}
    for edge in graph.edges:
        if edge_types is not None and edge.type not in edge_types:
            continue
        adjacency.setdefault(edge.source, set()).add(edge.target)
        adjacency.setdefault(edge.target, set()).add(edge.source)

    reachable: Set[str] = set(seed_ids)
    frontier: deque[tuple] = deque((sid, 0) for sid in seed_ids)
    while frontier:
        node_id, dist = frontier.popleft()
        if dist >= depth:
            continue
        for neighbor in adjacency.get(node_id, ()):  # noqa: SIM118
            if neighbor not in reachable:
                reachable.add(neighbor)
                frontier.append((neighbor, dist + 1))
    return reachable


def _induced_subgraph(graph: ResearchGraph, keep: Set[str]) -> ResearchGraph:
    """The subgraph induced by ``keep``: those nodes + edges internal to them.

    In-memory only, input order preserved — the community-scoped PPR run
    (Descent §5.4) never sees a hub outside the scope, so 2-hop hub explosion
    is impossible by construction rather than by downweighting.
    """
    return ResearchGraph(
        nodes=[n for n in graph.nodes if n.id in keep],
        edges=[e for e in graph.edges if e.source in keep and e.target in keep],
    )


def _summary_layer_nodes(
    graph: ResearchGraph, hierarchy, project_root: Path
) -> List[ResearchNode]:
    """The hierarchical-seeding corpus (Descent §5.4): one node per summary.

    In-graph coarse COMMUNITY_SUMMARY nodes (live in the hierarchy sidecar)
    plus WARM cached fine summaries read from the cache index under
    ``.tesserae/community_summaries/`` — the cached ones become synthetic
    in-memory nodes for hybrid search only, never minted into the graph
    (§3's reserved-``refines`` decision stays deferred). Deterministic:
    sorted cache scan (``rglob`` so the PR6 level subdirs are covered), the
    in-graph node wins over a cache file for the same cid, cold/invalid/
    orphaned cache files are skipped silently.
    """
    from .community_summaries import _read_cache, _validate_summary

    layer: List[ResearchNode] = []
    seen: Set[str] = set()
    for node in graph.nodes:
        if node.type is not ResearchNodeType.COMMUNITY_SUMMARY:
            continue
        if hierarchy.find_scope(node.id) is None:
            continue
        layer.append(node)
        seen.add(node.id)
    cache_dir = project_root / ".tesserae" / "community_summaries"
    if cache_dir.is_dir():
        for path in sorted(cache_dir.rglob("CommunitySummary_*.json")):
            payload = _read_cache(path)
            if not isinstance(payload, dict):
                continue
            cid = str(payload.get("community_id") or "")
            if not cid or cid in seen or hierarchy.find_scope(cid) is None:
                continue
            validated = _validate_summary(payload.get("summary"))
            if validated is None:
                continue
            title, description, tags = validated
            layer.append(
                ResearchNode(
                    id=cid,
                    name=title,
                    type=ResearchNodeType.COMMUNITY_SUMMARY,
                    description=description,
                    metadata={"tags": tags},
                )
            )
            seen.add(cid)
    return layer


def compile_context(
    graph: ResearchGraph,
    project_root: Optional[str] = None,
    query: str = "",
    seeds: Optional[Sequence[str]] = None,
    depth: int = 2,
    budget: int = 32_000,
    synthesize: bool = False,
    backend=None,
    multi_pool: bool = False,
    edge_type_weights: Optional[Mapping[str, float]] = None,
    recency_now: Optional[datetime] = None,
    recency_weight: float = 0.0,
    include_superseded: bool = False,
    scope: Optional[str] = None,
    strategy: str = "default",
    tame_hubs: bool = False,
    view: Optional[str] = None,
) -> ContextBundle:
    """Compile a tailored, cited context bundle for ``query`` / ``seeds``.

    See the module docstring for the full pipeline. Pure function: returns a
    :class:`ContextBundle` and writes nothing to disk.

    ``include_superseded=False`` (default, mirroring the MCP read tools)
    excludes superseded / arbitration-losing nodes from the ranked candidates,
    so a claim that lost to a winner is never cited as current knowledge. The
    losers still count as seeds, so a query landing on a stale claim surfaces
    its winner via the ``supersedes`` / ``resolved_by`` edge.

    Descent §5.4 (PR8) additions — all default-off, the default path is
    byte-identical with them unset:

    * ``scope=<cid>`` restricts the whole pipeline (seed search, PPR,
      selection) to the community-induced subgraph, member set resolved from
      the ``hierarchy.json`` sidecar — 2-hop hub explosion is killed
      structurally. Unknown cids fail loud with the valid grammar
      (``graph_map`` card ``scope_id``s).
    * ``strategy="hierarchical"`` seeds hybrid search against the summary
      layer first (in-graph coarse summaries + warm cached fine summaries,
      see :func:`_summary_layer_nodes`), descends the top ``max(1, depth)``
      matched branches, then runs the normal pipeline within the selected
      communities' member union (intersected with ``scope`` when both are
      given). Needs a non-empty ``query``; without one — or when no summary
      matches — it degrades to the default/scope path rather than guessing.
    * ``tame_hubs=True`` forwards the flag-gated PR1 hub mitigation to PPR,
      wiring the sidecar's precomputed ``hubs`` list into the degree cap
      when the hierarchy is available (best-effort: a missing sidecar just
      falls back to the fanout scan).
    * ``view=<name>`` (roadmap step 7) restricts the walk to one named edge
      partition from :mod:`tesserae.retrieval.views` — ``semantic`` /
      ``temporal`` / ``causal`` / ``entity``. Resolved to explicit
      zero-weights for every out-of-view edge type (merged under any caller
      ``edge_type_weights``, so an explicit caller weight can still
      resurrect or silence a type) plus the matching restriction on the
      depth-neighbourhood BFS. Unknown views fail loud with the valid names.

    Both ``scope`` and ``strategy="hierarchical"`` require ``project_root``
    (the sidecar lives under it); the ``budget=0`` uncapped invariant is
    honoured on every path.
    """
    if strategy not in ("default", "hierarchical"):
        raise ValueError(
            f"compile_context: unknown strategy {strategy!r} — expected "
            f"'default' or 'hierarchical'."
        )

    # View resolution (roadmap step 7). Deliberately BEFORE Step 0 for the
    # fail-fast ValueError, but it only takes effect strictly downstream of the
    # Step 0 scope induction — a view alters PPR edge weights and the BFS
    # adjacency inside whatever subgraph scope/strategy selected, never which
    # subgraph is selected (the charter composition rule: a view must not pick
    # a different domain).
    nb_edge_types: Optional[FrozenSet[str]] = None
    if view is not None:
        from .retrieval.views import traversable_edge_types, weights_for

        merged_weights = weights_for(view)  # ValueError on an unknown view
        if edge_type_weights:
            # Caller overrides win — exactly as they do against the defaults.
            merged_weights.update(edge_type_weights)
        edge_type_weights = merged_weights
        nb_edge_types = traversable_edge_types(merged_weights)

    # --- Step 0 (Descent §5.4): resolve hierarchy-backed restriction --------
    hierarchy = None
    if scope is not None or strategy == "hierarchical":
        if project_root is None:
            raise ValueError(
                "compile_context: scope= and strategy='hierarchical' resolve "
                "community members from the .tesserae/hierarchy.json sidecar "
                "— pass project_root (then `tesserae compile` writes it)."
            )
        from .hierarchy import load_hierarchy  # local: hierarchy imports us

        hierarchy = load_hierarchy(Path(project_root))
    elif tame_hubs and project_root is not None:
        from .hierarchy import load_hierarchy

        try:  # best-effort hub list — the cap works without it (fanout scan)
            hierarchy = load_hierarchy(Path(project_root))
        except ValueError:
            hierarchy = None
    hub_ids = hierarchy.hubs if (tame_hubs and hierarchy is not None) else None

    restrict: Optional[Set[str]] = None
    if scope is not None:
        found_scope = hierarchy.find_scope(scope)
        if found_scope is None:
            raise ValueError(
                f"compile_context: unknown scope {scope!r} — valid scopes are "
                f"community ids from the hierarchy sidecar (a graph_map "
                f"card's scope_id, e.g. 'CommunitySummary:<hash>'); start "
                f"from graph_map() and descend."
            )
        restrict = set(found_scope[1])

    if strategy == "hierarchical" and query and query.strip():
        layer = _summary_layer_nodes(graph, hierarchy, Path(project_root))
        if layer:
            matches = hybrid_search(
                ResearchGraph(nodes=layer, edges=[]),
                query,
                top_k=max(1, depth) * 5,
                backend=backend,
            )
            union: Set[str] = set()
            for scored in matches.scored[: max(1, depth)]:
                found_branch = hierarchy.find_scope(scored.node.id)
                if found_branch is not None:
                    union.update(found_branch[1])
            if union:
                narrowed = union if restrict is None else restrict & union
                # An empty intersection means the matched branches all live
                # outside the caller's scope — keep the explicit scope, it
                # is the harder contract of the two.
                restrict = narrowed or restrict

    if restrict is not None:
        graph = _induced_subgraph(graph, restrict)

    node_index = {n.id: n for n in graph.nodes}
    suppressed: Set[str] = set() if include_superseded else superseded_ids(graph)

    # --- Step 1: seed resolution (explicit first, then hybrid, deduped) ------
    seed_ids: List[str] = []
    seen = set()
    if seeds:
        for sid in seeds:
            if sid in node_index and sid not in seen:
                seed_ids.append(sid)
                seen.add(sid)
    if query and query.strip():
        # Multi-pool retrieval (AgentRunbook): decompose the question into
        # sub-queries and union hybrid-search seeds across them. The default
        # (single-pool) path is byte-identical — ``decompose`` returns
        # ``[query]`` when ``multi_pool`` is off.
        if multi_pool:
            from .retrieval.query_decompose import decompose_query

            subqueries = decompose_query(query, max_subqueries=5) or [query]
        else:
            subqueries = [query]
        for subq in subqueries:
            result = hybrid_search(
                graph, subq, top_k=max(1, depth) * 5, backend=backend
            )
            for scored in result.scored:
                nid = scored.node.id
                if nid not in seen:
                    seed_ids.append(nid)
                    seen.add(nid)

    # Empty query + no valid seeds -> empty-but-valid bundle.
    if not seed_ids:
        return ContextBundle(
            query=query,
            seeds_used=[],
            ranked_nodes=[],
            selected_nodes=[],
            citations=[],
            body=f"# Context: {query}\n\n---\n## Citations\n",
            synthesized=False,
            char_budget_used=0,
            char_budget_total=budget,
            # The caller asked about the pools, so answer about the pools: every
            # one empty. Returning ``None`` here would give that value a second,
            # undocumented meaning ("multi_pool was on but the query resolved to
            # nothing") on top of its documented one ("multi_pool was off").
            pool_reservations=_empty_pool_reservations() if multi_pool else None,
        )

    # --- Step 2: PPR expansion, bounded to the depth-hop neighbourhood -------
    # ``depth`` must bound hop-distance, not just scale ``top_k``: PPR runs over
    # the FULL connected component, so without this filter a depth=1 request can
    # surface nodes only reachable in 2+ hops. We precompute the seed
    # neighbourhood up to ``depth`` hops (BFS over the undirected edge set) and
    # keep only PPR results that fall inside it.
    #
    # The depth filter must run BEFORE the ``top_k`` cap, not after — otherwise
    # out-of-depth high-PPR nodes consume the window and valid in-depth nodes get
    # dropped. We request the FULL PPR ranking (``top_k = node count``), filter to
    # the in-depth set, THEN cap, so the cap is filled from in-depth nodes only.
    in_neighborhood = _neighborhood_within_depth(
        graph, seed_ids, max(0, depth), edge_types=nb_edge_types
    )
    cap = max(1, depth) * 10
    full_ranked = personalized_pagerank(
        graph, seed_ids, alpha=0.15, top_k=max(1, len(graph.nodes)),
        edge_type_weights=edge_type_weights,
        tame_hubs=tame_hubs, hub_ids=hub_ids,
    )
    in_nb = [
        (nid, score)
        for nid, score in full_ranked
        if nid in in_neighborhood and nid not in suppressed
    ]

    # Recency-aware re-rank (OPT-IN). Pure relevance magnets onto old "review of
    # ALL recent work" synthesis nodes for "what's recent" queries — strongest
    # semantic match, oldest content. Blend each node's normalized PPR relevance
    # with a recency score so newer nodes surface. Two things matter:
    #  - apply it to the FULL in-neighbourhood set BEFORE the cap, so a newer node
    #    below the top PPR window can still be pulled in (codex review);
    #  - use ``_recency_score`` (NOT raw decay), which treats an UNDATED node as
    #    neutral, never max-fresh — synthesis nodes deliberately omit timestamps
    #    for byte-idempotence, so raw decay (undated -> 1.0) would hand the very
    #    "Review ALL improvements" nodes top freshness (codex review).
    # Disabled by default (recency_now None / weight <= 0) so the slice below is
    # byte-identical to the old ``[:cap]`` and compiled/export artifacts stay stable.
    if recency_now is not None and recency_weight > 0 and in_nb:
        _max = max((s for _, s in in_nb), default=0.0) or 1.0
        _w = min(max(recency_weight, 0.0), 1.0)

        def _blended(item):
            nid, ppr = item
            node = node_index.get(nid)
            rec = _recency_score(node, recency_now) if node is not None else 0.5
            # ppr secondary key keeps ties in PPR order (stable).
            return ((1.0 - _w) * (ppr / _max) + _w * rec, ppr)

        in_nb = sorted(in_nb, key=_blended, reverse=True)

    ranked = in_nb[:cap]
    if not ranked:  # PITFALL 1: disconnected seeds -> fall back to seed order.
        ranked = [(sid, 0.0) for sid in seed_ids if sid not in suppressed]
    ranked_nodes = [nid for nid, _ in ranked]

    # Multi-pool reservation: guarantee the most relevant distilled-memory node
    # of each pool (Runbook / Gotcha / Event) in the neighbourhood gets a budget
    # slot, even when raw findings would otherwise fill the window. We pull the
    # top in-neighbourhood node of each pool from the FULL PPR ranking (so a
    # relevant distilled node below the raw cap is still surfaced) and move it to
    # the front of ``ranked``. Off by default -> default path untouched.
    pool_reservations: Optional[Dict[str, Optional[Dict[str, object]]]] = None
    if multi_pool:
        from .research_graph import has_producer_provenance

        # ONE declaration of what the pools are, in ``research_graph``. A second
        # literal list here could disagree with it silently: a type added to the
        # vocabulary's pool set would get no slot, one removed would keep one.
        # Agent-layer pools (spec §9) — distilled knowledge and the per-agent
        # capability card — are in that list for the same reason as the rest.
        _pools = _pool_order()
        _in_nb_ranked = [
            (nid, sc)
            for nid, sc in full_ranked
            if nid in in_neighborhood and nid not in suppressed
        ]
        _reserved: List[tuple] = []
        _reserved_ids: set = set()
        pool_reservations = _empty_pool_reservations()
        for _pool in _pools:
            # Within a pool, a fallback-quality distillate (structural stand-in
            # for a failed LLM call) loses its slot to any llm-quality sibling
            # further down the ranking (spec §9) — first non-fallback wins,
            # fallback kept only when it is all the pool has.
            _fallback_pick: Optional[tuple] = None
            for _nid, _sc in _in_nb_ranked:
                _n = node_index.get(_nid)
                if _n is None or _n.type != _pool or _nid in _reserved_ids:
                    continue
                # A reserved procedural slot is earned by PROVENANCE, not by
                # type. These five type names are also mintable by document
                # extraction, so without this the top-ranked 'Event' in the
                # neighbourhood may be a conference deadline — and reservation
                # is additive, so it would be promoted to the FRONT of the
                # budget walk from anywhere in the neighbourhood, evicting the
                # finding that actually earned the slot.
                if not has_producer_provenance(_n.type, _n.metadata):
                    continue
                if str(_n.metadata.get("distill_quality") or "") == "fallback":
                    if _fallback_pick is None:
                        _fallback_pick = (_nid, _sc)
                    continue
                _fallback_pick = None
                _reserved.append((_nid, _sc))
                _reserved_ids.add(_nid)
                # ``delivered`` is settled after the budget walk below, not here
                # — reservation is a claim on a slot, not proof of one.
                pool_reservations[_pool.value] = {
                    "node_id": _nid,
                    "delivered": False,
                }
                break  # one per pool
            if _fallback_pick is not None:
                _reserved.append(_fallback_pick)
                _reserved_ids.add(_fallback_pick[0])
                pool_reservations[_pool.value] = {
                    "node_id": _fallback_pick[0],
                    "delivered": False,
                }
        if _reserved:
            ranked = _reserved + [r for r in ranked if r[0] not in _reserved_ids]
            ranked_nodes = [nid for nid, _ in ranked]

    # --- Step 3: budget-bound selection (deterministic, PPR order) ----------
    store: Optional[WikiPageStore] = None
    if project_root is not None:
        store = WikiPageStore(Path(project_root) / ".tesserae" / "wiki")

    selected: List[tuple] = []  # (node, body)
    chars_used = 0
    for node_id, _score in ranked:
        node = node_index.get(node_id)
        if node is None:
            continue
        body = _fetch_body(node, store)
        if budget > 0 and chars_used + len(body) > budget:
            # A valid query must never yield an empty bundle just because the
            # first ranked body overflows the budget: always include the FIRST
            # selectable node, truncating its body to fit. Subsequent overflows
            # stop the walk as before.
            if not selected:
                truncated = _truncate_to_budget(body, budget) if budget > 0 else body
                selected.append((node, truncated))
                chars_used += len(truncated)
            break
        selected.append((node, body))
        chars_used += len(body)

    # Reservation buys a place at the FRONT of the walk above, not a place in
    # the bundle: the walk still stops at the budget, so with several pools
    # reserving, the later ones can be dropped after being reserved. Settle the
    # report against what was actually selected — a pool reported as served
    # when nothing from it was delivered is the same silent story the field
    # exists to end.
    if pool_reservations is not None:
        _delivered_ids = {node.id for node, _b in selected}
        for _entry in pool_reservations.values():
            if _entry is not None:
                _entry["delivered"] = _entry["node_id"] in _delivered_ids

    # --- Step 4: assemble cited markdown ------------------------------------
    sections: List[str] = [f"# Context: {query}\n"]
    # Staleness header (spec §9): when distilled knowledge is in the bundle,
    # say how fresh it is — delegation on weeks-old expertise is at least
    # labeled. Max over the selected distillates' corpus-clock watermarks.
    _watermarks = [
        str(n.metadata.get("distilled_through") or "")
        for n, _b in selected
        if n.metadata.get("distilled_through")
    ]
    if _watermarks:
        sections.append(f"\n_distilled through: {max(_watermarks)}_\n")
    citations: List[ContextCitation] = []
    for i, (node, body) in enumerate(selected, 1):
        anchor = f"node-{i}"
        _flag = (
            " _(fallback distillate — structural stand-in, LLM summary pending)_"
            if str(node.metadata.get("distill_quality") or "") == "fallback"
            else ""
        )
        sections.append(f"\n## [{node.name}][{anchor}]{_flag}\n\n{body}\n")
        citations.append(
            ContextCitation(
                node_id=node.id,
                node_name=node.name,
                source_path=node.source_path,
                wiki_kind=kind_for_node(node),
            )
        )
    sections.append("\n---\n## Citations\n")
    for i, c in enumerate(citations, 1):
        target = c.source_path or c.node_id
        sections.append(
            f"[node-{i}]: {target}  <!-- node_id={c.node_id} -->\n"
        )
    body_text = "".join(sections)

    # --- Step 5: optional, gated LLM synthesis ------------------------------
    # PITFALL 4 — degrade, NEVER raise. ANY missing SDK / missing key / API
    # failure falls back to the deterministic ``body_text`` assembled above. The
    # module docstring promises graceful fallback, so synthesis is purely
    # additive: when it works we prepend the synthesized body; otherwise the
    # deterministic bundle stands unchanged.
    synthesized = False
    if synthesize:
        try:
            from .llm_synthesis import LlmSynthesisRequest, LlmSynthesizer

            req = LlmSynthesisRequest(
                # ``topic`` is the VALID synthesis kind for a narrative context
                # summary over a set of related nodes (see _VALID_KINDS).
                kind="topic",
                title=query or "Context Summary",
                inputs=[
                    {
                        "id": c.node_id,
                        "name": c.node_name,
                        "description": node.description,
                    }
                    for (node, _b), c in zip(selected, citations)
                ],
            )
            resp = LlmSynthesizer(max_tokens=1200).synthesize(req)
            if resp:
                body_text = resp.body + "\n\n---\n" + body_text
                synthesized = True
        except Exception:
            # Missing anthropic SDK, missing API key, network/API error — keep
            # the deterministic assembly. Synthesis is best-effort only.
            synthesized = False

    return ContextBundle(
        query=query,
        seeds_used=seed_ids,
        ranked_nodes=ranked_nodes,
        selected_nodes=[n.id for n, _ in selected],
        citations=citations,
        body=body_text,
        synthesized=synthesized,
        char_budget_used=chars_used,
        char_budget_total=budget,
        pool_reservations=pool_reservations,
    )

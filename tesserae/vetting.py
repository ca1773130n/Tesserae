"""Was this source vetted by someone independent, and can a claim inherit that?

The general problem, of which peer review is one instance. A news article quotes
a paragraph; the paragraph's source is a study; the study appeared somewhere
that either did or did not check it. **Trust in the leaf is bounded by the
weakest link in that chain**, and this module is how a caller asks about it.

Nothing here knows what a "spotlight" or a "desk reject" is. Those are facts
about machine-learning conferences, and they live in a provider
(:mod:`tesserae.ingest.vetting_lookup`). A news-verification or legal-precedent
provider sits beside it and reuses everything below unchanged.

## A filter, never a score

A score blends vetting, provenance and verdict into one number and hides the
disagreement that made the question worth asking. A filter keeps them separate:
ask for the vetted subset, see what survives, see what was lost. When a claim is
supported only by unvetted sources, that IS the answer — a score would have
called it 0.6 and buried it.

## Absence is not rejection

A source with no record has not been rejected, it has not been FOUND. Most
preprints were never submitted anywhere visible; most blog posts were never
fact-checked. :data:`UNVETTED` and :data:`UNKNOWN` are therefore distinct from
:data:`REJECTED`, and nothing here may conflate them. Knowing a submission was
REFUSED is real information that a publishing platform structurally cannot
express, and it is the only reason to consult a review body at all.

Pure. No I/O, no network, no LLM, no fuzzy matching. Vetting state is written
into node metadata at INGEST time and read here as bytes, which is what lets
:func:`tesserae.verify.verify_claim` gain a vetting filter while keeping the
property its docstring promises: a pure function of the graph bytes.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

#: An independent reviewer looked at this and accepted it. Peer review, an
#: editorial fact-check, a court upholding a precedent.
VETTED = "vetted"
#: An independent reviewer looked at this and refused it. A conference reject, a
#: retraction, a fact-check rating of false. The one state a publishing
#: platform cannot report about its own contents.
REJECTED = "rejected"
#: Submitted for review, no decision yet.
PENDING = "pending"
#: Published without external review — a preprint, a blog, a press release.
#: NEUTRAL. It says nothing about quality, only that nobody independent checked.
UNVETTED = "unvetted"
#: Nothing known. Distinct from UNVETTED: we do not even know it was published.
UNKNOWN = "unknown"

#: Every state. An ORDER of how much review is known to have happened —
#: deliberately not a scale. Nothing in this module multiplies or averages.
VETTING_STATES: Tuple[str, ...] = (VETTED, PENDING, UNVETTED, UNKNOWN, REJECTED)

#: Metadata keys a provider writes.
VETTING_KEY = "vetting_state"
AUTHORITY_KEY = "vetting_authority"
#: Edges a node inherits vetting THROUGH, followed source -> target. A claim is
#: no better vetted than the source it quotes, and a distilled node no better
#: than what it distilled: `derived_from` and `contains` are how an upper
#: abstraction layer reaches its constituents.
SOURCE_EDGE_TYPES: Tuple[str, ...] = (
    "evidenced_by", "derived_from", "cites", "quotes", "sourced_from",
    "derived_from_session", "documents", "contains",
)

#: Edges pointing the OTHER way up the abstraction hierarchy, followed target ->
#: source. `part_of` is minted member -> whole, so a COMMUNITY_SUMMARY reaches
#: its members only by walking it backwards.
#:
#: **This is what keeps a distilled layer falsifiable.** Louvain gives this graph
#: a real dendrogram (`community_summaries.detect_community_levels`, finest to
#: coarsest) and each level's summary is an assertion about the level beneath
#: it. A summary that cannot resolve to the vetted sources under it is an
#: unfalsifiable claim wearing a node type — the exact failure a provenance
#: graph exists to prevent, and it would appear precisely at the layer an agent
#: is most likely to read.
CONSTITUENT_EDGE_TYPES: Tuple[str, ...] = ("part_of", "belongs_to_approach_family")

_LEGACY = {"peer_reviewed": VETTED, "preprint": UNVETTED, "under_review": PENDING}


def vetting_state(node: Any) -> str:
    """The vetting state of one node, from its metadata bytes alone.

    An unrecognised value is :data:`UNKNOWN`, never a guess: a state string this
    module has not seen must not be silently promoted to vetted.
    """
    meta = getattr(node, "metadata", None) or {}
    raw = str(meta.get(VETTING_KEY, "") or "").strip().casefold().replace("-", "_")
    if not raw:
        return UNKNOWN
    if raw in VETTING_STATES:
        return raw
    return _LEGACY.get(raw, UNKNOWN)


def passes(node: Any, *, require: Optional[Iterable[str]] = None) -> bool:
    """Does ``node`` survive a vetting filter?

    ``require=None`` admits everything, because a filter on by default would
    drop evidence nobody asked to drop. Otherwise ``require`` NAMES the states
    admitted: there is no threshold, because :data:`VETTING_STATES` is an order
    and not a scale, and a caller wanting vetted evidence should say whether
    unvetted also counts rather than inheriting an inequality.
    """
    if require is None:
        return True
    wanted = {str(s).strip().casefold() for s in require}
    unknown = wanted - set(VETTING_STATES)
    if unknown:
        raise ValueError(
            f"unknown vetting state(s) {sorted(unknown)}; "
            f"expected some of {list(VETTING_STATES)}"
        )
    return vetting_state(node) in wanted


def is_source(node: Any) -> bool:
    """Does this node carry a vetting state of its OWN?

    A claim is not a publishable unit and has no vetting state; a paper does. So
    a node with no vetting metadata is a CONDUIT, not a weak link — the walk
    passes through it and asks what is upstream. Treating it as UNKNOWN was the
    first version of this module and it made the filter useless on a real graph:
    almost no claim node carries vetting metadata, so every chain resolved to
    UNKNOWN and nothing was ever admitted.
    """
    meta = getattr(node, "metadata", None) or {}
    return bool(str(meta.get(VETTING_KEY, "") or "").strip())


def _weakest(states: Iterable[str]) -> str:
    """Weakest state in one dependency path. REJECTED wins outright."""
    seen = set(states)
    if not seen:
        return UNKNOWN
    if REJECTED in seen:
        return REJECTED
    for state in (UNKNOWN, UNVETTED, PENDING, VETTED):
        if state in seen:
            return state
    return UNKNOWN


def support(node: Any, graph: Any, *, max_depth: int = 4) -> Dict[str, int]:
    """How many INDEPENDENT support paths back ``node``, by their state.

    Two things a single state cannot express, and both are what a reader
    actually wants to know:

    * **Corroboration.** A claim backed by a reviewed paper AND a preprint is
      stronger than one backed by the preprint alone. A weakest-link rule
      reports it as weaker, which is backwards. Each direct support edge is a
      separate path and gets its own entry.
    * **Disagreement.** A claim with one retracted source and one reviewed
      source is exactly the case an expert wants surfaced, not averaged. Both
      appear in the census; nothing collapses them.

    WITHIN one path the weakest link still governs, because a path is a
    dependency: a paragraph is no better vetted than the study it quotes, and
    that study no better than where it appeared.
    """
    by_id = {n.id: n for n in getattr(graph, "nodes", [])}
    out_edges: Dict[str, List[str]] = {}
    for edge in getattr(graph, "edges", []):
        if edge.type in SOURCE_EDGE_TYPES:
            out_edges.setdefault(edge.source, []).append(edge.target)
        elif edge.type in CONSTITUENT_EDGE_TYPES:
            # Minted member -> whole, so the whole reaches its members backwards.
            out_edges.setdefault(edge.target, []).append(edge.source)

    counts = {state: 0 for state in VETTING_STATES}
    roots = out_edges.get(getattr(node, "id", ""), [])
    for root in roots:
        # Walk this branch, gathering the state of every SOURCE on it.
        states: List[str] = []
        seen: Set[str] = {getattr(node, "id", "")}
        frontier = [root]
        for _ in range(max_depth):
            nxt: List[str] = []
            for nid in frontier:
                if nid in seen or nid not in by_id:
                    continue
                seen.add(nid)
                candidate = by_id[nid]
                if is_source(candidate):
                    states.append(vetting_state(candidate))
                nxt.extend(out_edges.get(nid, []))
            if not nxt:
                break
            frontier = nxt
        counts[_weakest(states)] += 1
    return counts


def best_state(node: Any, graph: Any, *, max_depth: int = 4) -> str:
    """The STRONGEST state among ``node``'s independent support paths.

    Strongest across paths, weakest within one. A claim is as good as its best
    independent source, and no better than the weakest link of that source's own
    provenance.

    A node with no support paths at all is UNKNOWN — nothing backs it, which is
    different from being backed by something unvetted.
    """
    counts = support(node, graph, max_depth=max_depth)
    for state in (VETTED, PENDING, UNVETTED, REJECTED, UNKNOWN):
        if counts.get(state):
            return state
    return UNKNOWN


def partition(nodes: Sequence[Any], *, require: Iterable[str],
              graph: Any = None) -> Tuple[List[Any], List[Any]]:
    """``(kept, dropped)`` under a filter — BOTH halves, on purpose.

    What a filter removed is as much a result as what it left. A claim whose
    only support was three unvetted sources, asked for under vetting, should
    report "nothing survives, and here is what was lost" rather than an empty
    list that reads as though the claim was never supported at all.

    Pass ``graph`` to filter on the whole source CHAIN instead of the node
    alone.
    """
    kept, dropped = [], []
    for node in nodes:
        if graph is None:
            ok = passes(node, require=require)
        else:
            wanted = {str(s).strip().casefold() for s in require}
            unknown = wanted - set(VETTING_STATES)
            if unknown:
                raise ValueError(
                    f"unknown vetting state(s) {sorted(unknown)}; "
                    f"expected some of {list(VETTING_STATES)}"
                )
            ok = best_state(node, graph) in wanted
        (kept if ok else dropped).append(node)
    return kept, dropped


def census(nodes: Iterable[Any], *, graph: Any = None) -> Dict[str, int]:
    """How many nodes sit in each state, zeros included.

    Zeros are kept so a report cannot silently omit a state: ``rejected: 0``
    says we checked and found none, a missing key says we did not check, and
    those are different claims.
    """
    counts = {state: 0 for state in VETTING_STATES}
    for node in nodes:
        state = vetting_state(node) if graph is None else best_state(node, graph)
        counts[state] += 1
    return counts


__all__ = [
    "AUTHORITY_KEY", "CONSTITUENT_EDGE_TYPES", "PENDING", "REJECTED", "SOURCE_EDGE_TYPES", "UNKNOWN",
    "UNVETTED", "VETTED", "VETTING_KEY", "VETTING_STATES", "best_state", "census",
    "is_source", "partition", "passes", "support", "vetting_state",
]

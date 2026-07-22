"""Connection discovery for the engine sleep cycle — the "associate" pass.

The daemon's idle-consolidation tick already runs the distill (compress/forget)
pass; this module is the third brain-consolidation operation: *discover new
connections while sleeping*. It finds embedding-similar idea-bearing nodes that
are NOT already linked — both intra-project (across different nodes of one
graph) and cross-agent (across different agents' distilled notes) — and records
them as ``shares_concept_with`` links.

Hard constraint — byte-idempotence of ``graph.json``. Embeddings are
machine-dependent (they vary with the installed model), so discovered links can
NEVER be written into ``graph.json`` or any compiled artifact. They live in a
``.tesserae/`` sidecar overlay (:data:`OVERLAY_FILENAME`) that ACCUMULATES across
sleep cycles, and are merged back only as extra IN-MEMORY edges when a graph is
loaded for queries / PPR (:func:`apply_overlay`) — exactly the "read-time
overlay, never serialized" idiom :func:`tesserae.agent_view._worker_view` uses
for its absorption edges. The embedding machinery is reused verbatim from
:func:`tesserae.federation.add_semantic_links` (``scope="intra"``), so the pass
is deterministic given a fixed backend and a HONEST no-op on the hash-bucket
stub backend (no real model → similarities are noise → skip and say so).

Surfaces:

* :func:`discover_links` — ``(graph, backend) -> [(source, target, cosine), ...]``.
* :func:`persist_links` — merge/dedup/accumulate into the overlay (atomic write).
* :func:`load_overlay_edges` / :func:`apply_overlay` — read-time in-memory merge.
* :func:`consolidate_associations` — the daemon entrypoint: discover → persist,
  never raises.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

from ..federation import (
    DEFAULT_SEMANTIC_MIN_COSINE,
    add_semantic_links,
    federate_graphs,
)
from ..research_graph import ResearchEdge, ResearchGraph

PathLike = Union[str, Path]

# The accumulating sidecar overlay. Same JSON shape as federation's link cache
# (``[[source, target, cosine], ...]``) but a STABLE name — never keyed on the
# candidate signature — so it accumulates discovered links across sleep cycles
# instead of self-invalidating whenever a node changes.
OVERLAY_FILENAME = "discovered_links.json"

SHARES_CONCEPT_EDGE = "shares_concept_with"

# Federation alias for the base graph when discovering cross-agent links (so the
# project's own idea nodes count as one "member" and can bridge to agent notes,
# while agent keys are the other members). Never leaks into graph.json.
_BASE_ALIAS = "project"

# Discovery defaults — mirror federation.add_semantic_links so the associate pass
# and cross-project bridges use the same tuned thresholds.
DEFAULT_ASSOCIATE_TOP_K = 5
DEFAULT_ASSOCIATE_MAX_CANDIDATES = 1500


# --------------------------------------------------------------------------- #
# Overlay paths + validation                                                  #
# --------------------------------------------------------------------------- #

def _overlay_path(project_root: PathLike) -> Path:
    return Path(project_root) / ".tesserae" / OVERLAY_FILENAME


def _validate_triples(raw: object) -> Optional[List[list]]:
    """Return validated ``[[str, str, number], ...]`` or ``None``.

    Anything off-shape is treated as a miss (mirrors
    :func:`tesserae.federation._load_cached_links`): a corrupt overlay must
    never poison reads or raise — the pass simply recomputes next cycle.
    """
    if not isinstance(raw, list):
        return None
    for triple in raw:
        if (not isinstance(triple, list) or len(triple) != 3
                or not isinstance(triple[0], str) or not isinstance(triple[1], str)
                or not isinstance(triple[2], (int, float)) or isinstance(triple[2], bool)):
            return None
    return raw


def _load_overlay_raw(project_root: PathLike) -> List[list]:
    """Read + validate the persisted overlay triples (``[]`` if absent/corrupt)."""
    path = _overlay_path(project_root)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    triples = _validate_triples(raw)
    return triples if triples is not None else []


# --------------------------------------------------------------------------- #
# (1) Discovery                                                               #
# --------------------------------------------------------------------------- #

def discover_links(
    graph: ResearchGraph,
    *,
    backend,
    agents: Optional[Sequence[Tuple[str, ResearchGraph]]] = None,
    top_k: int = DEFAULT_ASSOCIATE_TOP_K,
    min_cosine: float = DEFAULT_SEMANTIC_MIN_COSINE,
    max_candidates: int = DEFAULT_ASSOCIATE_MAX_CANDIDATES,
) -> List[Tuple[str, str, float]]:
    """Discover NEW ``shares_concept_with`` links over ``graph``.

    With ``agents=None`` this is INTRA-project discovery: any two idea-bearing
    nodes of ``graph`` whose embeddings are similar and that are not already
    linked. With ``agents=[(agent_key, agent_graph), ...]`` the base graph and
    the agent graphs are federated (agent keys become provenance aliases) and
    discovery links only CROSS-agent pairs — different agents' related notes,
    never same-agent ones — plus base↔agent bridges.

    Reuses :func:`tesserae.federation.add_semantic_links` (``scope="intra"``), so
    it is deterministic given a fixed ``backend`` (id-sorted, canonical
    ``source < target`` direction, existing edges suppressed) and an honest no-op
    on the hash-bucket stub backend (returns ``[]``). The result is sorted, so
    equal inputs yield byte-identical output.
    """
    work = graph
    if agents:
        named: List[Tuple[str, ResearchGraph]] = [(_BASE_ALIAS, graph)]
        named.extend((str(key), member) for key, member in agents)
        work, _ = federate_graphs(named)  # identity-merge only; namespaces ids

    enriched, stats = add_semantic_links(
        work,
        scope="intra",
        backend=backend,
        top_k=top_k,
        min_cosine=min_cosine,
        max_candidates=max_candidates,
    )
    if stats.get("semantic_skipped"):
        return []  # stub backend / numpy absent → honest no-op

    # The enriched graph keeps any pre-existing shares_concept_with edges and
    # appends the newly discovered ones; keep only the new links.
    before = {
        (edge.source, edge.target)
        for edge in work.edges
        if edge.type == SHARES_CONCEPT_EDGE
    }
    links = {
        (edge.source, edge.target, round(float(edge.metadata.get("cosine") or 0.0), 4))
        for edge in enriched.edges
        if edge.type == SHARES_CONCEPT_EDGE
        and edge.metadata.get("federation_semantic")
        and (edge.source, edge.target) not in before
    }
    return sorted(links)


# --------------------------------------------------------------------------- #
# (2) Persistence — accumulate + dedup, byte-stable                          #
# --------------------------------------------------------------------------- #

def persist_links(
    project_root: PathLike,
    links: Sequence[Tuple[str, str, float]],
) -> int:
    """Merge ``links`` into the accumulating overlay and return its total size.

    Accumulates ACROSS sleep cycles: prior links are preserved, new ones added,
    and duplicates collapse by ``(source, target)`` (keeping the higher cosine
    for a deterministic tiebreak). The written bytes are byte-stable given the
    same accumulated link SET — the list is sorted and dumped compactly — so
    re-persisting an unchanged set is a no-op at the byte level. The write is
    atomic (tmp + ``replace``) so a concurrent MCP reader never sees a torn file.
    """
    by_pair: Dict[Tuple[str, str], float] = {}
    for source, target, cosine in _load_overlay_raw(project_root):
        by_pair[(source, target)] = float(cosine)
    for source, target, cosine in links:
        source, target = str(source), str(target)
        if source == target:
            continue  # never a self-link
        value = round(float(cosine), 4)
        prev = by_pair.get((source, target))
        by_pair[(source, target)] = value if prev is None else max(prev, value)

    payload = sorted([source, target, cosine] for (source, target), cosine in by_pair.items())

    path = _overlay_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic: no torn-file read by a concurrent reader
    return len(payload)


# --------------------------------------------------------------------------- #
# (3) Read-time overlay merge — in-memory only, NEVER graph.json             #
# --------------------------------------------------------------------------- #

def load_overlay_edges(project_root: PathLike) -> List[ResearchEdge]:
    """Return the persisted overlay as ``shares_concept_with`` edges.

    Edges carry ``federation_semantic`` (so PPR down-weighting for the type
    applies just like a federation bridge) and ``associate_overlay`` (so a
    surface can tell a discovered link from a federation one). Empty when the
    overlay is absent or corrupt — never raises.
    """
    return [
        ResearchEdge(
            source=source,
            target=target,
            type=SHARES_CONCEPT_EDGE,
            metadata={"federation_semantic": True, "associate_overlay": True, "cosine": cosine},
        )
        for source, target, cosine in _load_overlay_raw(project_root)
    ]


def apply_overlay(project_root: PathLike, graph: ResearchGraph) -> ResearchGraph:
    """Return ``graph`` with the accumulated overlay merged as extra edges.

    In-memory ONLY — mirrors :func:`tesserae.agent_view._worker_view`: build a
    NEW :class:`ResearchGraph` (never mutate the input, which may be a shared
    cached instance) that adds each overlay edge whose BOTH endpoints exist in
    ``graph`` and that is not already present. Overlay links whose endpoints are
    absent (e.g. namespaced cross-agent ids against a raw project graph) are
    skipped, so the same overlay is safe to apply to any view. Node count is
    unchanged and ``graph.json`` is never touched. Returns the input unchanged
    (same instance) when there is nothing to add.
    """
    overlay = load_overlay_edges(project_root)
    if not overlay:
        return graph
    node_ids = {node.id for node in graph.nodes}
    edge_keys = {(edge.source, edge.type, edge.target) for edge in graph.edges}
    extra: List[ResearchEdge] = []
    for edge in overlay:
        if edge.source not in node_ids or edge.target not in node_ids:
            continue  # endpoint absent in this view — skip (never fabricate nodes)
        key = (edge.source, edge.type, edge.target)
        if key in edge_keys:
            continue
        edge_keys.add(key)
        extra.append(edge)
    if not extra:
        return graph
    return ResearchGraph(nodes=list(graph.nodes), edges=list(graph.edges) + extra)


# --------------------------------------------------------------------------- #
# (4) Daemon entrypoint — discover → persist, never raises                   #
# --------------------------------------------------------------------------- #

def _is_real_backend(backend) -> bool:
    """True when ``backend`` (or the active default) is a real embedding model."""
    from ..retrieval.hybrid import HashEmbeddingBackend, active_embedding_backend

    resolved = backend if backend is not None else active_embedding_backend()
    return not isinstance(resolved, HashEmbeddingBackend)


def consolidate_associations(
    project_root: PathLike,
    graph: ResearchGraph,
    *,
    backend=None,
    agents: Optional[Sequence[Tuple[str, ResearchGraph]]] = None,
    top_k: int = DEFAULT_ASSOCIATE_TOP_K,
    min_cosine: float = DEFAULT_SEMANTIC_MIN_COSINE,
    max_candidates: int = DEFAULT_ASSOCIATE_MAX_CANDIDATES,
) -> Dict[str, object]:
    """Run one associate pass for the sleep cycle: discover links, persist them.

    The single entrypoint the daemon's ``_consolidate_once`` calls after distill.
    NEVER raises — every failure (including a stub backend) degrades to a stats
    dict, so it is safe to call inside the idle-consolidation tick. Gated on a
    real embedding backend (no-op with the hash stub). Idempotent on unchanged
    input: discovery is deterministic and persistence dedups, so a second call
    leaves the overlay byte-identical.
    """
    try:
        if not _is_real_backend(backend):
            return {
                "associate_added": 0,
                "associate_skipped": "no real embedding backend (install tesserae[semantic])",
            }
        links = discover_links(
            graph,
            backend=backend,
            agents=agents,
            top_k=top_k,
            min_cosine=min_cosine,
            max_candidates=max_candidates,
        )
        if not links:
            return {"associate_added": 0, "associate_overlay_size": len(_load_overlay_raw(project_root))}
        total = persist_links(project_root, links)
        return {"associate_added": len(links), "associate_overlay_size": total}
    except Exception as exc:  # never raise into the daemon loop
        return {"associate_added": 0, "associate_error": repr(exc)}

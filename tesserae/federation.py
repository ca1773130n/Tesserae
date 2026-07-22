"""Cross-project federated graph (MVP): identity-merge + federated recall.

Assemble ONE :class:`ResearchGraph` from several registered projects by
namespacing every node id per project (``<alias>::<id>``) and merging ONLY
high-precision identity matches — same arxiv id / repo / content hash / code
symbol. Then run the normal context compiler over the union so an answer can
cross-reference projects instead of the per-project fan-out (``all-registered``).

Design invariants (see docs/superpowers/specs/2026-06-26-cross-project-federation-design.md):
- per-project ``graph.json`` is READ-ONLY — federation writes nothing there;
- DETERMINISTIC — identity keys only (no fuzzy name/embedding dedup, NOT
  ``merge_graphs`` which is order-dependent), smallest-id cluster representatives,
  sorted folds — so the federated graph is byte-stable regardless of project order.
"""

from __future__ import annotations

import dataclasses
import os
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    prefer_research_node,
)

_NS = "::"  # namespace separator; never produced by stable_id

# Semantic-link defaults — single source of truth (the eval harness in
# evals/federation/ and its regression test import these, so the data-backed
# values and the shipped values can never silently diverge). See
# evals/federation/report.md for the precision/recall + swamping data.
DEFAULT_SEMANTIC_MIN_COSINE = 0.55   # F1 frontier with perfect precision
SEMANTIC_BRIDGE_PPR_WEIGHT = 0.5     # nudge: below references (1.5); >=1.0 swamps


# --------------------------------------------------------------------------- #
# Identity keys — high precision only (a None key is NEVER auto-merged)        #
# --------------------------------------------------------------------------- #

def _norm_repo(metadata: dict) -> str:
    """Canonical ``host/owner/repo`` repo identity from repo_url/github_repo.

    Handles https/http/www and scp-style SSH (``git@host:owner/repo`` — the colon
    becomes a slash so SSH and HTTPS remotes of the same repo merge). Returns ""
    for a bare host or anything without an owner/repo path, so a malformed value
    never becomes a (false) identity key.
    """
    url = str(metadata.get("repo_url") or "").strip().lower().rstrip("/")
    is_ssh = url.startswith("git@")
    for pre in ("https://", "http://", "git@", "www."):
        if url.startswith(pre):
            url = url[len(pre):]
    if is_ssh and ":" in url:
        url = url.replace(":", "/", 1)  # git@host:owner/repo -> host/owner/repo
    if url.endswith(".git"):
        url = url[:-4]
    if url and url.count("/") >= 2:  # host/owner/repo — reject bare host / owner-only
        return url
    gh = str(metadata.get("github_repo") or "").strip().lower().strip("/")
    if gh:
        canonical = gh if gh.startswith("github.com/") else f"github.com/{gh}"
        return canonical if canonical.count("/") >= 2 else ""
    return ""


def identity_key(node: ResearchNode) -> Optional[tuple]:
    """A cross-project identity for ``node``, or ``None`` to never auto-merge it.

    Only *verified* metadata keys participate; fuzzy name/embedding matches are
    deliberately excluded (a false merge cross-contaminates answers).
    """
    md = node.metadata or {}
    type_value = node.type.value if hasattr(node.type, "value") else str(node.type)

    if node.type == ResearchNodeType.PAPER:
        arxiv = str(md.get("arxiv_id") or "").strip().lower()
        return ("Paper", arxiv) if arxiv else None
    if node.type == ResearchNodeType.REPOSITORY:
        repo = _norm_repo(md)
        return ("Repository", repo) if repo else None
    if node.type == ResearchNodeType.SOURCE_DOCUMENT:
        content_hash = str(md.get("content_hash") or "").strip()
        return ("SourceDocument", content_hash) if content_hash else None
    # Agent-layer identities (2026-07-19 layered-agent-kg spec §4). The
    # ``agent_key`` is role-grade (``harness:account:role``); ``lineage_key``
    # hashes the sorted transitive raw L0 member ids underlying a distillate
    # (Runbook/Gotcha included once the distill pass stamps it) — LLM wording
    # is change detection only, never identity.
    if node.type == ResearchNodeType.AGENT:
        agent_key = str(md.get("agent_key") or "").strip()
        return ("Agent", agent_key) if agent_key else None
    if node.type in (
        ResearchNodeType.DISTILLED_NOTE,
        ResearchNodeType.RUNBOOK,
        ResearchNodeType.GOTCHA,
    ):
        lineage = str(md.get("lineage_key") or "").strip()
        return (type_value, lineage) if lineage else None
    if node.type == ResearchNodeType.EXPERTISE_PROFILE:
        agent = str(md.get("agent") or "").strip()
        return ("profile", agent) if agent else None
    if type_value.startswith("Code"):
        source_path = str(node.source_path or md.get("source_path") or "").strip()
        qualified = str(md.get("qualified_name") or "").strip()
        return (type_value, source_path, qualified) if (source_path and qualified) else None
    return None


# --------------------------------------------------------------------------- #
# Namespacing + merge                                                         #
# --------------------------------------------------------------------------- #

def namespace_graph(graph: ResearchGraph, alias: str) -> ResearchGraph:
    """Return a copy of ``graph`` with ids prefixed ``<alias>::`` and edges
    rewritten. Stamps ``federation_alias`` / ``federation_origin_id``. The input
    graph (frozen nodes) is not mutated."""
    def nid(raw: str) -> str:
        return f"{alias}{_NS}{raw}"

    nodes = [
        dataclasses.replace(
            n,
            id=nid(n.id),
            metadata={**(n.metadata or {}), "federation_alias": alias, "federation_origin_id": n.id},
        )
        for n in graph.nodes
    ]
    edges = [dataclasses.replace(e, source=nid(e.source), target=nid(e.target)) for e in graph.edges]
    return ResearchGraph(nodes=nodes, edges=edges)


def _members(node: ResearchNode) -> List[str]:
    md = node.metadata or {}
    existing = md.get("federation_members")
    if isinstance(existing, list):
        return [str(x) for x in existing]
    alias = md.get("federation_alias")
    return [str(alias)] if alias else []


def _merge_two(a: ResearchNode, b: ResearchNode) -> ResearchNode:
    """Fold two cluster members into one, preserving cross-project provenance.

    Reuses ``prefer_research_node`` (title-quality/alias/source_path logic) but
    unions a sorted ``federation_members`` list so the merged node records every
    project it came from — ``prefer_research_node`` alone would drop one side's.
    """
    merged = prefer_research_node(a, b)
    members = sorted(set(_members(a)) | set(_members(b)))
    return dataclasses.replace(merged, metadata={**merged.metadata, "federation_members": members})


def federate_graphs(
    named_graphs: List[Tuple[str, ResearchGraph]],
    *,
    semantic: bool = False,
    semantic_top_k: int = 5,
    semantic_min_cosine: float = DEFAULT_SEMANTIC_MIN_COSINE,
    semantic_backend=None,
    semantic_cache_dir: Optional[Path] = None,
) -> Tuple[ResearchGraph, dict]:
    """Namespace + identity-merge ``[(alias, graph), ...]`` into one ResearchGraph.

    Deterministic regardless of input order: aliases are sorted, nodes processed
    in id order, clusters keep the smallest member id as representative.

    ``semantic=True`` (opt-in, v2) additionally adds embedding-backed
    ``shares_concept_with`` edges across projects (see :func:`add_semantic_links`)
    so federated PPR can bridge RELATED, not just identical, concepts. This makes
    the result embedding-backend-dependent (no longer byte-identical across
    machines); the default identity-only mode stays byte-stable.
    """
    all_nodes: List[ResearchNode] = []
    all_edges: List[ResearchEdge] = []
    for alias, graph in sorted(named_graphs, key=lambda kv: kv[0]):
        ns = namespace_graph(graph, alias)
        all_nodes.extend(ns.nodes)
        all_edges.extend(ns.edges)
    all_nodes.sort(key=lambda n: n.id)

    parent: Dict[str, str] = {n.id: n.id for n in all_nodes}

    def find(x: str) -> str:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        lo, hi = (ra, rb) if ra < rb else (rb, ra)
        parent[hi] = lo  # smaller id wins → order-independent representative

    key_to_first: Dict[tuple, str] = {}
    for node in all_nodes:  # id-sorted
        key = identity_key(node)
        if key is None:
            continue
        if key in key_to_first:
            union(key_to_first[key], node.id)
        else:
            key_to_first[key] = node.id

    clusters: Dict[str, List[ResearchNode]] = defaultdict(list)
    for node in all_nodes:
        clusters[find(node.id)].append(node)

    merged_nodes: List[ResearchNode] = []
    merged_groups = 0
    for root, members in clusters.items():
        members.sort(key=lambda n: n.id)
        if len(members) == 1:
            merged_nodes.append(members[0])
            continue
        merged_groups += 1
        acc = members[0]
        for member in members[1:]:
            acc = _merge_two(acc, member)
        # representative = smallest id; record the absorbed ids so `explain` can
        # resolve a merged-away id back to its representative.
        acc = dataclasses.replace(
            acc, id=root,
            metadata={**acc.metadata, "federation_merged_ids": sorted(m.id for m in members)},
        )
        merged_nodes.append(acc)

    live = {n.id for n in merged_nodes}
    seen: set = set()
    fed_edges: List[ResearchEdge] = []
    dropped_edges = 0
    for edge in all_edges:
        source, target = find(edge.source), find(edge.target)
        if source == target or source not in live or target not in live:
            dropped_edges += 1  # self-loop after merge, dangling, or duplicate target
            continue
        key = (source, edge.type, target)
        if key in seen:
            dropped_edges += 1
            continue
        seen.add(key)
        fed_edges.append(dataclasses.replace(edge, source=source, target=target))

    federated = ResearchGraph(nodes=merged_nodes, edges=fed_edges).canonicalized()
    stats = {
        "projects": sorted(alias for alias, _ in named_graphs),
        "nodes": len(federated.nodes),
        "edges": len(federated.edges),
        "merged_groups": merged_groups,
        "dropped_edges": dropped_edges,
    }
    if semantic:
        federated, sem_stats = add_semantic_links(
            federated, top_k=semantic_top_k, min_cosine=semantic_min_cosine,
            backend=semantic_backend, cache_dir=semantic_cache_dir,
        )
        stats.update(sem_stats)
        stats["edges"] = len(federated.edges)
    return federated, stats


# --------------------------------------------------------------------------- #
# Semantic cross-project links (v2, opt-in, embedding-backed)                  #
# --------------------------------------------------------------------------- #

# Idea-bearing node types worth a semantic bridge across projects (concepts,
# methods, claims, session findings...). Excludes documents/code/people/papers
# (those bridge by IDENTITY) and high-volume structural nodes.
_SEMANTIC_TYPE_VALUES = frozenset({
    "Concept", "TechnicalTerm", "MathematicalConcept", "MethodologicalConcept",
    "Algorithm", "ArchitecturePattern", "TrainingParadigm", "InferenceStrategy",
    "Task", "Capability", "ResearchTopic", "ProblemArea", "ApproachFamily",
    "SessionInsight", "SessionDecision", "SessionHypothesis", "SessionTakeaway",
    "SessionQuestion", "SessionTODO", "Runbook", "Gotcha", "OpenQuestion",
    "Claim", "ContributionClaim", "PerformanceClaim", "ComparisonClaim",
    "LimitationClaim", "CausalClaim",
})


def _federation_cache_dir() -> Path:
    return Path.home() / ".tesserae" / "federation"


# In-process memo for the assembled federated graph. Assembling the union
# (read + parse + namespace + identity-merge, ~25% of federated-ask latency)
# repeats for every query in a conversation against the SAME project set. We key
# on each member's graph-file change signature (mtime+size), so the entry
# self-invalidates the instant ANY member project recompiles — no disk, no stale
# graph. This is a query-time projection only (never a compiled artifact), so it
# raises no byte-idempotence concern. Bounded; a short-lived CLI process simply
# never gets a second hit (the win is the long-lived MCP server / a burst of
# follow-ups). Disable with TESSERAE_NO_FEDERATION_CACHE=1.
_FED_GRAPH_CACHE: "OrderedDict[tuple, Tuple[ResearchGraph, dict]]" = OrderedDict()
_FED_GRAPH_CACHE_MAX = 8


def _graph_signature(path: str) -> "Optional[tuple]":
    """A cheap (mtime_ns, size) change-signature for a graph file; None if absent."""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _federation_cache_enabled() -> bool:
    return os.environ.get("TESSERAE_NO_FEDERATION_CACHE", "") not in ("1", "true", "yes")


def _semantic_cache_key(backend_name, top_k, min_cosine, max_candidates, candidates, existing) -> str:
    """Key on EVERYTHING add_semantic_links reads that changes the output, so a
    stale cache can never survive a change it should invalidate."""
    import hashlib
    import json

    payload = json.dumps(
        [backend_name, top_k, repr(float(min_cosine)), max_candidates,
         [(n.id, f"{n.name}. {(n.description or '').strip()}".strip()) for n in candidates],
         sorted(existing)],  # existing edges suppress duplicate links -> part of the result
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()  # full digest, no truncation


def _load_cached_links(cache_file):
    """Return validated [[str, str, number], ...] or None (treat anything off as
    a miss — a corrupt cache must never poison results or raise)."""
    import json

    try:
        raw = json.loads(cache_file.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(raw, list):
        return None
    for triple in raw:
        if (not isinstance(triple, list) or len(triple) != 3
                or not isinstance(triple[0], str) or not isinstance(triple[1], str)
                or not isinstance(triple[2], (int, float)) or isinstance(triple[2], bool)):
            return None
    return raw


def _apply_cached_links(graph, cached, existing):
    """Rebuild shares_concept_with edges from validated [source, target, cosine]."""
    edges = []
    for source, target, cosine in cached:
        if (source, target) in existing:
            continue
        existing.add((source, target))
        existing.add((target, source))
        edges.append(ResearchEdge(source=source, target=target, type="shares_concept_with",
                                  metadata={"federation_semantic": True, "cosine": cosine}))
    return edges


def add_semantic_links(
    graph: ResearchGraph,
    *,
    top_k: int = 5,
    min_cosine: float = DEFAULT_SEMANTIC_MIN_COSINE,
    backend=None,
    max_candidates: int = 1500,
    cache_dir: Optional[Path] = None,
    scope: str = "cross",
) -> Tuple[ResearchGraph, dict]:
    """Add ``shares_concept_with`` edges between idea-bearing nodes whose
    embeddings are similar — so PPR (already run by ``compile_context``) can
    traverse RELATED, not just identical, concepts.

    Embedding-backed and opt-in. Honest degradation: with only the hash-bucket
    stub (no real model) the similarities are noise, so we skip and say so.
    Deterministic given a fixed embedding model (id-sorted candidates, canonical
    edge direction), and never duplicates an existing edge.

    ``scope="cross"`` (default) links only nodes from DIFFERENT projects — nodes
    sharing a project, or carrying no provenance at all, are never linked. This
    is the federation path and stays byte-identical. ``scope="intra"`` relaxes
    that for the consolidation "associate" pass
    (:mod:`tesserae.memory.associate`): any two distinct candidates with DISJOINT
    provenance are eligible, so idea nodes within ONE project (empty provenance)
    and notes from DIFFERENT agents both link, while same-project / same-agent
    pairs never do. The persisted link cache (``cache_dir``) is used for the
    ``cross`` scope only — the associate pass keeps its own accumulating overlay.
    """
    # NB: numpy is imported only AFTER the stub-skip below, so `--semantic` on a
    # base install (no embedding extra, no numpy) degrades cleanly instead of
    # crashing on the import.
    from .retrieval.hybrid import HashEmbeddingBackend, active_embedding_backend

    backend = backend or active_embedding_backend()
    backend_name = getattr(backend, "name", type(backend).__name__)
    candidates = sorted(
        (n for n in graph.nodes if (n.type.value if hasattr(n.type, "value") else str(n.type)) in _SEMANTIC_TYPE_VALUES),
        key=lambda n: n.id,
    )
    capped = len(candidates) > max_candidates
    if capped:
        candidates = candidates[:max_candidates]
    if isinstance(backend, HashEmbeddingBackend):
        return graph, {"semantic_added": 0, "semantic_backend": backend_name,
                       "semantic_skipped": "no real embedding backend (install tesserae[semantic])"}
    if len(candidates) < 2:
        return graph, {"semantic_added": 0, "semantic_backend": backend_name}

    existing: set = set()
    for edge in graph.edges:
        existing.add((edge.source, edge.target))
        existing.add((edge.target, edge.source))

    # Persisted link cache (best-effort). Keyed on the candidate (id, text) set +
    # backend + params, so it auto-invalidates when any project recompiles.
    # ponytail: caches the whole link set per project-combo, not incrementally —
    # fine until you federate many large overlapping project combos.
    cache_file = None
    if cache_dir is not None and scope == "cross":
        key = _semantic_cache_key(backend_name, top_k, min_cosine, max_candidates, candidates, existing)
        cache_file = Path(cache_dir) / f"links-{key}.json"
        if cache_file.is_file():
            cached = _load_cached_links(cache_file)
            if cached is not None:
                edges = _apply_cached_links(graph, cached, existing)
                enriched = ResearchGraph(nodes=list(graph.nodes), edges=list(graph.edges) + edges).canonicalized()
                return enriched, {"semantic_added": len(edges), "semantic_backend": backend_name, "semantic_cached": True}

    try:
        import numpy as np
    except ImportError:
        return graph, {"semantic_added": 0, "semantic_backend": backend_name,
                       "semantic_skipped": "numpy not available (install tesserae[semantic])"}

    vectors = np.asarray(
        backend.embed([f"{n.name}. {(n.description or '').strip()}".strip() for n in candidates]),
        dtype="float64",
    )
    # L2-normalize defensively: the EmbeddingBackend protocol does not guarantee
    # unit vectors, so raw dot products could exceed 1 and create false links.
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors = vectors / norms
    sims = vectors @ vectors.T  # now genuine cosine in [-1, 1]
    project_sets = [set(_members(n)) for n in candidates]

    new_edges: List[ResearchEdge] = []
    for i, node in enumerate(candidates):
        row = sims[i]
        # Eligibility. ``scope="cross"`` (default): both nodes must carry
        # provenance AND share no project (empty provenance is NOT treated as
        # cross-project — guards direct calls on a non-federated graph).
        # ``scope="intra"`` (associate pass): any distinct pair with DISJOINT
        # provenance — empty-provenance intra-project nodes and different-agent
        # notes both qualify; same-project / same-agent pairs never do.
        scored = [
            (float(row[j]), candidates[j].id)
            for j in range(len(candidates))
            if j != i and row[j] >= min_cosine
            and not (project_sets[i] & project_sets[j])
            and (scope == "intra" or (project_sets[i] and project_sets[j]))
        ]
        scored.sort(key=lambda t: (-t[0], t[1]))
        for cosine, other_id in scored[:top_k]:
            source, target = (node.id, other_id) if node.id < other_id else (other_id, node.id)
            if (source, target) in existing:
                continue
            existing.add((source, target))
            existing.add((target, source))
            new_edges.append(ResearchEdge(
                source=source, target=target, type="shares_concept_with",
                metadata={"federation_semantic": True, "cosine": round(cosine, 4)},
            ))

    if cache_file is not None:
        import json

        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps([[e.source, e.target, e.metadata["cosine"]] for e in new_edges]),
                encoding="utf-8",
            )
            tmp.replace(cache_file)  # atomic: no torn-file read by a concurrent reader
        except OSError:
            pass  # ponytail: cache write is best-effort, never break recall over it

    enriched = ResearchGraph(nodes=list(graph.nodes), edges=list(graph.edges) + new_edges).canonicalized()
    stats = {"semantic_added": len(new_edges), "semantic_backend": backend_name}
    if capped:
        stats["semantic_capped_at"] = max_candidates
    return enriched, stats


# --------------------------------------------------------------------------- #
# Loading + recall                                                            #
# --------------------------------------------------------------------------- #

def load_federated_graph(
    aliases, registry, *, semantic: bool = False,
    semantic_min_cosine: float = DEFAULT_SEMANTIC_MIN_COSINE,
    semantic_cache_dir: Optional[Path] = None,
) -> Tuple[ResearchGraph, dict]:
    """Load the named registered projects' graphs (read-only) and federate them.

    ``registry`` must expose ``list_projects() -> {"projects": [{name, root,
    graph_path}]}`` (both the CLI registry and the MCP server's registry do).
    Raises ``ValueError`` on an empty selection, unknown alias, or missing graph.
    """
    from .project import load_graph_file

    data = registry.list_projects()
    by_name = {str(p.get("name")): p for p in (data.get("projects") or [])}
    wanted = sorted({str(a).strip() for a in (aliases or []) if str(a).strip()})
    if not wanted:
        raise ValueError(
            "federated scope needs at least one project — pass --scope-aliases A B "
            "(CLI) or scope_aliases (MCP)."
        )
    missing = [a for a in wanted if a not in by_name]
    if missing:
        raise ValueError(
            f"unknown project alias(es): {missing}. Run 'tesserae projects list' "
            "to see registered projects."
        )

    # Resolve + validate each member's graph path first (cheap; needed for the
    # cache signature) BEFORE the expensive read+parse+merge.
    paths: List[Tuple[str, str]] = []
    for alias in wanted:
        entry = by_name[alias]
        graph_path = entry.get("graph_path")
        if not graph_path:
            root = entry.get("root")
            graph_path = str(Path(str(root)) / ".tesserae" / "graph.json") if root else None
        if not graph_path or not Path(graph_path).is_file():
            raise ValueError(
                f"project '{alias}' has no compiled graph at {graph_path!r}; "
                "run 'tesserae compile' for it first."
            )
        paths.append((alias, graph_path))

    use_cache = _federation_cache_enabled()
    key = (
        tuple(wanted),
        bool(semantic),
        round(float(semantic_min_cosine), 6),
        str(semantic_cache_dir or ""),
        tuple((a, _graph_signature(p)) for a, p in paths),
    )
    if use_cache:
        cached = _FED_GRAPH_CACHE.get(key)
        if cached is not None:
            _FED_GRAPH_CACHE.move_to_end(key)  # LRU touch
            return cached

    named: List[Tuple[str, ResearchGraph]] = [(alias, load_graph_file(p)) for alias, p in paths]
    result = federate_graphs(
        named, semantic=semantic, semantic_min_cosine=semantic_min_cosine,
        semantic_cache_dir=semantic_cache_dir or _federation_cache_dir(),
    )
    if use_cache:
        _FED_GRAPH_CACHE[key] = result
        _FED_GRAPH_CACHE.move_to_end(key)
        while len(_FED_GRAPH_CACHE) > _FED_GRAPH_CACHE_MAX:
            _FED_GRAPH_CACHE.popitem(last=False)  # evict oldest
    return result


def federated_recall(
    aliases,
    query: str,
    *,
    depth: int = 2,
    budget: int = 64_000,
    synthesize: bool = False,
    semantic: bool = True,
    semantic_min_cosine: float = DEFAULT_SEMANTIC_MIN_COSINE,
    recency_weight: Optional[float] = None,
    registry,
) -> dict:
    """Federate the selected projects and compile ONE cited context bundle.

    ``synthesize=False`` (default) needs no LLM. ``semantic=True`` adds
    embedding-backed cross-project ``shares_concept_with`` edges so the answer can
    bridge related (not just identical) concepts across projects — the v2 payoff.
    """
    graph, stats = load_federated_graph(
        aliases, registry, semantic=semantic, semantic_min_cosine=semantic_min_cosine
    )
    from datetime import datetime, timezone

    from .context_compiler import DEFAULT_RECENCY_WEIGHT, compile_context

    # Cross-project semantic edges should NUDGE, not dominate: down-weight
    # shares_concept_with in PPR so an identity merge (node collapse) and
    # references (1.5) still outrank a fuzzy bridge. Only when semantic links
    # were actually added.
    edge_type_weights = {"shares_concept_with": SEMANTIC_BRIDGE_PPR_WEIGHT} if stats.get("semantic_added") else None
    # Interactive recall is recency-aware by default so a "what's recent" query
    # doesn't magnet onto old "review of all recent work" syntheses (pass
    # recency_weight=0 to rank by pure relevance).
    _rw = DEFAULT_RECENCY_WEIGHT if recency_weight is None else recency_weight
    bundle = compile_context(
        graph, project_root=None, query=query, depth=depth, budget=budget,
        synthesize=synthesize, edge_type_weights=edge_type_weights,
        recency_now=datetime.now(timezone.utc) if _rw > 0 else None,
        recency_weight=_rw,
    )
    return {
        "scope": "federated",
        "question": query,
        "projects": stats["projects"],
        "stats": stats,
        "body": bundle.body,
        "citations": [dataclasses.asdict(c) for c in bundle.citations],
        "selected_node_ids": bundle.selected_nodes,
        "char_budget_used": bundle.char_budget_used,
        "synthesized": bundle.synthesized,
    }


# --------------------------------------------------------------------------- #
# Inspectability (v3): status + explain                                       #
# --------------------------------------------------------------------------- #

def federation_status(aliases, registry, *, semantic: bool = False) -> dict:
    """Counts for a federation: per-project node contribution + merge/link stats."""
    graph, stats = load_federated_graph(aliases, registry, semantic=semantic)
    per_project: Dict[str, int] = {}
    for node in graph.nodes:
        for alias in _members(node):  # a merged node counts toward each source project
            per_project[alias] = per_project.get(alias, 0) + 1
    return {
        "projects": stats["projects"],
        "per_project_nodes": dict(sorted(per_project.items())),
        "nodes": stats["nodes"],
        "edges": stats["edges"],
        "identity_merges": stats["merged_groups"],
        "dropped_edges": stats.get("dropped_edges", 0),
        "semantic": {k: v for k, v in stats.items() if k.startswith("semantic")},
    }


def federation_explain(node_ref, aliases, registry, *, semantic: bool = True) -> dict:
    """Explain ONE node's cross-project connections — why it bridges projects.

    Accepts the namespaced id (``alias::id``) or a unique suffix / origin id.
    """
    graph, _ = load_federated_graph(aliases, registry, semantic=semantic)
    by_id = {n.id: n for n in graph.nodes}
    node = by_id.get(node_ref)
    if node is None:
        # a merged-away id (absorbed by a representative) resolves to that rep
        node = next((n for n in graph.nodes
                     if node_ref in ((n.metadata or {}).get("federation_merged_ids") or [])), None)
    if node is None:
        matches = [n for n in graph.nodes
                   if n.id.endswith(node_ref) or (n.metadata or {}).get("federation_origin_id") == node_ref]
        if len(matches) == 1:
            node = matches[0]
        elif not matches:
            raise ValueError(f"node {node_ref!r} not found among {len(graph.nodes)} federated nodes")
        else:
            raise ValueError(f"ambiguous node {node_ref!r}: {sorted(m.id for m in matches)[:8]}")

    links = []
    for edge in graph.edges:
        if node.id not in (edge.source, edge.target):
            continue
        other_id = edge.target if edge.source == node.id else edge.source
        other = by_id.get(other_id)
        links.append({
            "other": other_id,
            "other_name": other.name if other else None,
            "other_projects": sorted(set(_members(other))) if other else [],
            "type": edge.type,
            "semantic": bool((edge.metadata or {}).get("federation_semantic")),
            "cosine": (edge.metadata or {}).get("cosine"),
        })
    links.sort(key=lambda link: (not link["semantic"], link["other"]))
    return {
        "node": node.id,
        "name": node.name,
        "type": node.type.value if hasattr(node.type, "value") else str(node.type),
        "merged_from_projects": sorted(set(_members(node))),  # >1 => identity merge spanned projects
        "links": links,
    }

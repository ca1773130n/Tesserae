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
from collections import defaultdict
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
        merged_nodes.append(dataclasses.replace(acc, id=root))  # representative = smallest id

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
            backend=semantic_backend,
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


def add_semantic_links(
    graph: ResearchGraph,
    *,
    top_k: int = 5,
    min_cosine: float = DEFAULT_SEMANTIC_MIN_COSINE,
    backend=None,
    max_candidates: int = 1500,
) -> Tuple[ResearchGraph, dict]:
    """Add ``shares_concept_with`` edges between idea-bearing nodes from DIFFERENT
    projects whose embeddings are similar — so federated PPR (already run by
    ``compile_context``) can traverse RELATED, not just identical, concepts.

    Embedding-backed and opt-in. Honest degradation: with only the hash-bucket
    stub (no real model) the similarities are noise, so we skip and say so.
    Deterministic given a fixed embedding model (id-sorted candidates, canonical
    edge direction). Linking is cross-project only (nodes sharing a project are
    never linked) and never duplicates an existing edge.
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

    existing: set = set()
    for edge in graph.edges:
        existing.add((edge.source, edge.target))
        existing.add((edge.target, edge.source))

    new_edges: List[ResearchEdge] = []
    for i, node in enumerate(candidates):
        row = sims[i]
        # cross-project ONLY: both nodes must carry provenance AND share no project
        # (empty provenance is NOT treated as cross-project — guards direct calls
        # on a non-federated graph).
        scored = [
            (float(row[j]), candidates[j].id)
            for j in range(len(candidates))
            if j != i and project_sets[i] and project_sets[j]
            and not (project_sets[i] & project_sets[j]) and row[j] >= min_cosine
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

    enriched = ResearchGraph(nodes=list(graph.nodes), edges=list(graph.edges) + new_edges).canonicalized()
    stats = {"semantic_added": len(new_edges), "semantic_backend": backend_name}
    if capped:
        stats["semantic_capped_at"] = max_candidates
    return enriched, stats


# --------------------------------------------------------------------------- #
# Loading + recall                                                            #
# --------------------------------------------------------------------------- #

def load_federated_graph(
    aliases, registry, *, semantic: bool = False, semantic_min_cosine: float = DEFAULT_SEMANTIC_MIN_COSINE
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

    named: List[Tuple[str, ResearchGraph]] = []
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
        named.append((alias, load_graph_file(graph_path)))
    return federate_graphs(named, semantic=semantic, semantic_min_cosine=semantic_min_cosine)


def federated_recall(
    aliases,
    query: str,
    *,
    depth: int = 2,
    budget: int = 64_000,
    synthesize: bool = False,
    semantic: bool = False,
    semantic_min_cosine: float = DEFAULT_SEMANTIC_MIN_COSINE,
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
    from .context_compiler import compile_context

    # Cross-project semantic edges should NUDGE, not dominate: down-weight
    # shares_concept_with in PPR so an identity merge (node collapse) and
    # references (1.5) still outrank a fuzzy bridge. Only when semantic links
    # were actually added.
    edge_type_weights = {"shares_concept_with": SEMANTIC_BRIDGE_PPR_WEIGHT} if stats.get("semantic_added") else None
    bundle = compile_context(
        graph, project_root=None, query=query, depth=depth, budget=budget,
        synthesize=synthesize, edge_type_weights=edge_type_weights,
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

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


def federate_graphs(named_graphs: List[Tuple[str, ResearchGraph]]) -> Tuple[ResearchGraph, dict]:
    """Namespace + identity-merge ``[(alias, graph), ...]`` into one ResearchGraph.

    Deterministic regardless of input order: aliases are sorted, nodes processed
    in id order, clusters keep the smallest member id as representative.
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
    return federated, stats


# --------------------------------------------------------------------------- #
# Loading + recall                                                            #
# --------------------------------------------------------------------------- #

def load_federated_graph(aliases, registry) -> Tuple[ResearchGraph, dict]:
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
    return federate_graphs(named)


def federated_recall(
    aliases,
    query: str,
    *,
    depth: int = 2,
    budget: int = 64_000,
    synthesize: bool = False,
    registry,
) -> dict:
    """Federate the selected projects and compile ONE cited context bundle.

    ``synthesize=False`` (default) is fully deterministic and needs no LLM — the
    body is assembled from the selected nodes' descriptions, cited per project.
    """
    graph, stats = load_federated_graph(aliases, registry)
    from .context_compiler import compile_context

    bundle = compile_context(
        graph, project_root=None, query=query, depth=depth, budget=budget, synthesize=synthesize
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

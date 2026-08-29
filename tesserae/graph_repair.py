"""Apply the post-extraction passes to a graph that already exists.

Two fixes shipped in one day change what a compiled graph looks like without
changing what the model extracted: one anchor per document instead of one per
chunk, and one node per entity name instead of one per spelling and type. Both
run inside ``compile``, which means a user with a graph on disk sees neither
until they recompile — and a recompile on a large corpus is hours the first
time, or half an hour of cache hits on a good day. This module does the same
work on the graph bytes alone: no model, no network, seconds.

It is deliberately the SAME rules the compile applies, reached through the same
functions, so a repaired graph and a recompiled graph agree. Measured on the
148-paper corpus (2026-08-29): repairing the old graph in place reproduced the
recompile's anchor count, refusal count and retrieval recall within noise.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Set, Tuple

from .research_graph import ResearchEdge, ResearchGraph, ResearchNode, is_source_anchor


def collapse_document_anchors(graph: ResearchGraph) -> Tuple[ResearchGraph, int]:
    """One anchor per source file: every other anchor of that file that carries
    ``contains`` edges is a chunk's anchor and is redirected to the document's.

    A cited work the model typed as a document — ``S2orc`` as a SourceDocument
    inside a paper about embeddings — carries no ``contains`` edges of its own
    and is left alone: it IS a different document. The winner is chosen by the
    rule extraction uses, :func:`~tesserae.llm_extractor._document_anchor`.
    Returns the same graph object when nothing changes.
    """
    from .llm_extractor import _document_anchor

    contains: Dict[str, int] = defaultdict(int)
    for edge in graph.edges:
        if edge.type == "contains":
            contains[edge.source] += 1
    by_file: Dict[str, List[ResearchNode]] = defaultdict(list)
    for node in graph.nodes:
        if is_source_anchor(node) and node.source_path and contains.get(node.id):
            by_file[node.source_path].append(node)
    redirect: Dict[str, str] = {}
    for path, anchors in by_file.items():
        if len(anchors) < 2:
            continue
        # every piece anchor is the same type as the document's (a chunk names
        # the document, never a repository), so group per type before picking
        by_type: Dict[str, List[ResearchNode]] = defaultdict(list)
        for node in anchors:
            by_type[node.type.value].append(node)
        for group in by_type.values():
            if len(group) < 2:
                continue
            winner = _document_anchor(group, graph.edges, group[0].type)
            for node in group:
                if node.id != winner.id:
                    redirect[node.id] = winner.id
    if not redirect:
        return graph, 0
    nodes = [n for n in graph.nodes if n.id not in redirect]
    seen: Set[Tuple[str, str, str]] = set()
    edges: List[ResearchEdge] = []
    for edge in graph.edges:
        src = redirect.get(edge.source, edge.source)
        dst = redirect.get(edge.target, edge.target)
        if src == dst:
            continue
        key = (src, edge.type, dst)
        if key in seen:
            continue
        seen.add(key)
        edges.append(edge if (src, dst) == (edge.source, edge.target) else
                     ResearchEdge(source=src, target=dst, type=edge.type,
                                  evidence=edge.evidence, metadata=edge.metadata))
    return ResearchGraph(nodes=nodes, edges=edges), len(redirect)


def repair_graph(graph: ResearchGraph) -> Tuple[ResearchGraph, Dict[str, int]]:
    """Anchor collapse, then entity resolution — the compile's order.

    The report says what changed, in numbers a caller can print or compare:
    ``anchors_collapsed``, ``entities_merged``, and node/edge counts before
    and after. A second run on the result reports zeros.
    """
    from .entity_resolution import resolve_entities

    before_nodes, before_edges = len(graph.nodes), len(graph.edges)
    graph, anchors = collapse_document_anchors(graph)
    graph, merged = resolve_entities(graph)
    return graph, {
        "anchors_collapsed": anchors,
        "entities_merged": merged,
        "nodes_before": before_nodes,
        "nodes_after": len(graph.nodes),
        "edges_before": before_edges,
        "edges_after": len(graph.edges),
    }

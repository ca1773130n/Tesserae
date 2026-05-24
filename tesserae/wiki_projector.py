"""Project graph nodes into the markdown wiki layer.

The synthesis projector handles higher-order pages (pulse, daily, weekly,
topic, comparison, field). This projector handles the leaf kinds that mirror
graph nodes one-to-one: sources, concepts, entities, papers, repos, topics,
questions. Together they populate ``.tesserae/wiki/`` so the static site
renders detail pages for every wiki-layer node.

Idempotent: a page is only rewritten when its body hash would change.

This module is also the single funnel through which every other public-facing
consumer (``site/search.py``, ``site/exports.py``, ``site/pages.py``,
``project.py`` artifact split) asks the question "what nodes/edges are public
research-layer entities?". The two helpers ``public_nodes()`` and
``public_edges()`` plus ``kind_for_node()`` are the canonical answer — see
F-9 and F-10 in the codex extraction review.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from .research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType, is_public_research_node
from .wiki_store import WikiPage, WikiPageStore


# --- Node-type taxonomies ---------------------------------------------------
#
# Three disjoint partitions of ``ResearchNodeType``:
#
# * ``CODE_GRAPH_TYPES`` — code-graph layer; lives in ``code-graph.json`` and
#   never appears in ``graph.json``. Includes ``CodeProject`` (F-9: this is
#   the *internal* code-graph artifact, not the user-facing research repo —
#   the public repo type is ``Repository``).
# * ``ASSERTION_LAYER_TYPES`` — claim / evidence layer; lives in the research
#   graph but is never given a dedicated URL (rendered inline on detail pages
#   as bullets / badges).
# * ``PUBLIC_RESEARCH_TYPES`` — every type that gets a public wiki page
#   (subject to per-node validity, e.g. paper title quality).

CODE_GRAPH_TYPES: FrozenSet[ResearchNodeType] = frozenset({
    ResearchNodeType.CODE_PROJECT,
    ResearchNodeType.SOURCE_FILE,
    # Feature A / impl-code-graph (`tesserae project ingest-code`): new
    # CodeFile/CodeMethod variants live alongside the older SourceFile/
    # CodeFunction nodes. Both ontology slices are private code-graph.
    ResearchNodeType.CODE_FILE,
    ResearchNodeType.CODE_MODULE,
    ResearchNodeType.CODE_CLASS,
    ResearchNodeType.CODE_FUNCTION,
    ResearchNodeType.CODE_METHOD,
    ResearchNodeType.DEPENDENCY,
    # Option-C / CodeGraph-adapter additions (`tesserae project sync-code`).
    # Same private-layer treatment — these never appear in the public
    # research graph or wiki site; they live in ``code-graph.json``.
    ResearchNodeType.CODE_INTERFACE,
    ResearchNodeType.CODE_TRAIT,
    ResearchNodeType.CODE_STRUCT,
    ResearchNodeType.CODE_ENUM,
    ResearchNodeType.CODE_ENUM_MEMBER,
    ResearchNodeType.CODE_TYPE_ALIAS,
    ResearchNodeType.CODE_VARIABLE,
    ResearchNodeType.CODE_CONSTANT,
    ResearchNodeType.CODE_ROUTE,
    ResearchNodeType.CODE_COMPONENT,
    ResearchNodeType.CODE_FIELD,
    ResearchNodeType.CODE_PARAMETER,
    ResearchNodeType.CODE_NAMESPACE,
    ResearchNodeType.CODE_SYMBOL,
})


ASSERTION_LAYER_TYPES: FrozenSet[ResearchNodeType] = frozenset({
    ResearchNodeType.CLAIM,
    ResearchNodeType.CONTRIBUTION_CLAIM,
    ResearchNodeType.PERFORMANCE_CLAIM,
    ResearchNodeType.COMPARISON_CLAIM,
    ResearchNodeType.LIMITATION_CLAIM,
    ResearchNodeType.CAUSAL_CLAIM,
    ResearchNodeType.EVIDENCE_SPAN,
})


# --- Node-type → wiki kind --------------------------------------------------
#
# F-9 note: ``CodeProject`` and ``Repository`` look semantically similar
# (both wrap a repo-shaped artifact), but they live in different layers.
# ``CodeProject`` is an *internal* code-graph node minted by
# :class:`CodeGraphExtractor` for the local workspace; it is filtered out by
# :func:`is_public_research_node` (via ``CODE_GRAPH_TYPES``) and never gets a
# public route. ``Repository`` is the user-facing research entity (GitHub
# notes, paper/repo pairs) and gets the ``/repos/`` route. Likewise
# ``Project`` is treated as a public research-layer alias for ``Repository``
# so legacy graphs that minted it under the old ontology still surface.

_KIND_FOR_TYPE: Mapping[ResearchNodeType, str] = {
    ResearchNodeType.SOURCE_DOCUMENT: "sources",
    ResearchNodeType.PAPER: "papers",
    ResearchNodeType.REPOSITORY: "repos",
    ResearchNodeType.PROJECT: "repos",
    # NB: CODE_PROJECT is intentionally *not* mapped to a public kind. It is
    # a code-graph node; it lives in ``code-graph.json``, not in the public
    # site. See ``CODE_GRAPH_TYPES`` above and F-9 in the extraction review.
    ResearchNodeType.CONCEPT: "concepts",
    ResearchNodeType.TECHNICAL_TERM: "concepts",
    ResearchNodeType.MATHEMATICAL_CONCEPT: "concepts",
    ResearchNodeType.METHODOLOGICAL_CONCEPT: "concepts",
    ResearchNodeType.ALGORITHM: "concepts",
    ResearchNodeType.OBJECTIVE_FUNCTION: "concepts",
    ResearchNodeType.ARCHITECTURE_PATTERN: "concepts",
    ResearchNodeType.TRAINING_PARADIGM: "concepts",
    ResearchNodeType.INFERENCE_STRATEGY: "concepts",
    ResearchNodeType.EVALUATION_PROTOCOL: "concepts",
    ResearchNodeType.TASK: "concepts",
    ResearchNodeType.CAPABILITY: "concepts",
    ResearchNodeType.MODEL: "entities",
    ResearchNodeType.DATASET: "entities",
    ResearchNodeType.BENCHMARK: "entities",
    ResearchNodeType.METRIC: "entities",
    ResearchNodeType.RESULT: "entities",
    ResearchNodeType.ORGANIZATION: "entities",
    ResearchNodeType.PERSON: "entities",
    ResearchNodeType.RESEARCH_FIELD: "topics",
    ResearchNodeType.RESEARCH_TOPIC: "topics",
    ResearchNodeType.PROBLEM_AREA: "topics",
    ResearchNodeType.APPROACH_FAMILY: "topics",
    ResearchNodeType.TREND: "topics",
    ResearchNodeType.OPEN_QUESTION: "questions",
    ResearchNodeType.SYNTHESIS: "syntheses",
    # Community summaries — opt-in post-compile pass surfaces a vault
    # page per detected cluster (Louvain/label-propagation).
    ResearchNodeType.COMMUNITY_SUMMARY: "communities",
}


@dataclass(frozen=True)
class _Adjacency:
    out: Dict[str, List[ResearchEdge]] = field(default_factory=lambda: defaultdict(list))
    inn: Dict[str, List[ResearchEdge]] = field(default_factory=lambda: defaultdict(list))


def _build_adjacency(graph: ResearchGraph) -> _Adjacency:
    adj = _Adjacency()
    for edge in graph.edges:
        adj.out[edge.source].append(edge)
        adj.inn[edge.target].append(edge)
    return adj


def _format_relation_block(title: str, items: Sequence[Tuple[str, str, str]]) -> str:
    if not items:
        return ""
    lines = [f"## {title}", ""]
    for label, name, kind in items[:25]:
        lines.append(f"- **{label}** → {name} _({kind})_")
    lines.append("")
    return "\n".join(lines)


class WikiLayerProjector:
    """Materialize wiki/<kind>/<slug>.md files for every wiki-layer graph node."""

    def __init__(self, wiki_store: WikiPageStore) -> None:
        self.wiki_store = wiki_store

    def project(self, graph: ResearchGraph) -> List[WikiPage]:
        adj = _build_adjacency(graph)
        nodes_by_id = {node.id: node for node in graph.nodes}
        written: List[WikiPage] = []
        for node in graph.nodes:
            kind = kind_for_node(node)
            if kind is None:
                continue
            page = self._page_for_node(node, kind, adj, nodes_by_id)
            if self.wiki_store.write_page(page):
                written.append(page)
        return written

    def _page_for_node(
        self,
        node: ResearchNode,
        kind: str,
        adj: _Adjacency,
        nodes_by_id: Mapping[str, ResearchNode],
    ) -> WikiPage:
        slug = self.wiki_store.slug_for(node.name) if hasattr(self.wiki_store, "slug_for") else _local_slug(node.name)
        try:
            slug = self.wiki_store.slug_for(node.name)
        except NotImplementedError:
            slug = _local_slug(node.name)
        title = node.name
        outgoing = [
            (edge.type, nodes_by_id[edge.target].name, nodes_by_id[edge.target].type.value)
            for edge in adj.out.get(node.id, [])
            if edge.target in nodes_by_id
            and kind_for_node(nodes_by_id[edge.target]) is not None
        ]
        incoming = [
            (edge.type, nodes_by_id[edge.source].name, nodes_by_id[edge.source].type.value)
            for edge in adj.inn.get(node.id, [])
            if edge.source in nodes_by_id
            and kind_for_node(nodes_by_id[edge.source]) is not None
        ]
        outgoing.sort()
        incoming.sort()
        type_mix = Counter(item[2] for item in outgoing + incoming)
        # The page header in ``site/pages.py`` already renders type / aliases /
        # source path. Emitting them here too made every detail page show
        # "Source:" twice (once in the eyebrow metadata, once in the markdown
        # body). We keep the description + relations only; the page chrome
        # surfaces the structured fields via frontmatter.
        body_lines = [
            f"# {title}",
            "",
        ]
        if node.description:
            body_lines.extend([node.description, ""])
        if outgoing:
            body_lines.append(_format_relation_block("Outgoing relations", outgoing))
        if incoming:
            body_lines.append(_format_relation_block("Incoming relations", incoming))
        if type_mix:
            body_lines.append("## Connected node types")
            body_lines.append("")
            for kind_name, count in sorted(type_mix.items(), key=lambda item: (-item[1], item[0])):
                body_lines.append(f"- {kind_name}: {count}")
            body_lines.append("")
        body = "\n".join(body_lines).rstrip() + "\n"
        frontmatter: Dict[str, object] = {
            "title": title,
            "kind": kind,
            "node_id": node.id,
            "node_type": node.type.value,
            "source_path": node.source_path or "",
        }
        if node.aliases:
            frontmatter["aliases"] = sorted(node.aliases)
        path = self.wiki_store.path_for(kind, slug)
        return WikiPage(kind=kind, slug=slug, title=title, body=body, path=path, frontmatter=frontmatter)


def _local_slug(value: str) -> str:
    import hashlib
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    if not safe:
        safe = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
    return safe[:80]


# ---------------------------------------------------------------------------
# Public predicate / partition helpers (F-9, F-10, F-11)
# ---------------------------------------------------------------------------
#
# Every consumer that asks "is this node public?" or "which artifact does this
# node belong in?" goes through one of these helpers. Three layers in play:
#
#   1. ``is_code_graph_node(n)``  — code-graph layer (F-11). Always private,
#      lives in ``code-graph.json``, never appears in ``graph.json``.
#   2. ``is_assertion_node(n)``   — claim/evidence layer. Lives in the
#      research graph (so MCP/Cognee see it) but never gets a public URL.
#   3. ``is_public_research_node`` (re-exported from research_graph) — public
#      research entity (Paper/Repository/Concept/Synthesis/...) subject to
#      per-node validity gates such as paper title quality.
#
# ``kind_for_node()`` is the SSOT for "given a node, which wiki kind is it?".
# Returns ``None`` for code-graph / assertion / private-research nodes.


def is_code_graph_node(node: ResearchNode) -> bool:
    """True iff ``node`` belongs to the code-graph layer (F-11)."""
    return node.type in CODE_GRAPH_TYPES


def is_assertion_node(node: ResearchNode) -> bool:
    """True iff ``node`` is a claim/evidence node (private, but research-layer)."""
    return node.type in ASSERTION_LAYER_TYPES


def kind_for_node(node: ResearchNode) -> Optional[str]:
    """Return the public wiki kind (``papers`` / ``concepts`` / ...) or ``None``.

    A node is considered "public" iff:
      * it is not a code-graph node (F-9 / F-11), AND
      * it is not an assertion-layer node, AND
      * :func:`is_public_research_node` accepts it (paper title quality,
        social-feed source filter, etc.), AND
      * its type maps to a public wiki kind via ``_KIND_FOR_TYPE``.
    """
    if is_code_graph_node(node):
        return None
    if is_assertion_node(node):
        return None
    if not is_public_research_node(node):
        return None
    return _KIND_FOR_TYPE.get(node.type)


def public_nodes(graph: ResearchGraph) -> List[ResearchNode]:
    """Return only nodes that should appear in public wiki/site projections.

    Single source of truth used by the static-site builder, search index,
    JSON-LD export, llms.txt / llms-full.txt, RSS, sitemap, and the
    ``/graph/payload.json`` route.
    """
    return [node for node in graph.nodes if kind_for_node(node) is not None]


def public_edges(graph: ResearchGraph) -> List[ResearchEdge]:
    """Return edges where both endpoints survive the :func:`public_nodes` filter."""
    public_ids = {node.id for node in public_nodes(graph)}
    return [edge for edge in graph.edges if edge.source in public_ids and edge.target in public_ids]


def partition_graph(graph: ResearchGraph) -> Tuple[ResearchGraph, ResearchGraph]:
    """Split ``graph`` into ``(research_graph, code_graph)``.

    * ``research_graph`` keeps every node that is *not* a code-graph type
      (so it includes public research nodes plus the private assertion layer
      that consumers like MCP/Cognee still want). Edges where either endpoint
      is a code-graph node are dropped.
    * ``code_graph`` keeps only code-graph nodes (``CodeProject`` /
      ``SourceFile`` / ``CodeModule`` / ``CodeClass`` / ``CodeFunction`` /
      ``Dependency``). Edges where either endpoint is a code-graph node are
      kept (this includes intra-code edges *and* any cross-layer
      ``mentioned_in`` edge that anchors a code symbol to a research node;
      callers can drop the cross-layer half if needed).
    """
    research_node_ids: set[str] = set()
    code_node_ids: set[str] = set()
    research_nodes: List[ResearchNode] = []
    code_nodes: List[ResearchNode] = []
    for node in graph.nodes:
        if is_code_graph_node(node):
            code_nodes.append(node)
            code_node_ids.add(node.id)
        else:
            research_nodes.append(node)
            research_node_ids.add(node.id)

    research_edges: List[ResearchEdge] = []
    code_edges: List[ResearchEdge] = []
    for edge in graph.edges:
        src_in_research = edge.source in research_node_ids
        tgt_in_research = edge.target in research_node_ids
        src_in_code = edge.source in code_node_ids
        tgt_in_code = edge.target in code_node_ids
        if src_in_research and tgt_in_research:
            research_edges.append(edge)
        if src_in_code or tgt_in_code:
            # Code-graph keeps anything touching the code layer (intra-code +
            # cross-layer anchors). The cross-layer edges let downstream tools
            # rebuild a combined view if they want one.
            code_edges.append(edge)

    return (
        ResearchGraph(nodes=research_nodes, edges=research_edges),
        ResearchGraph(nodes=code_nodes, edges=code_edges),
    )


__all__ = [
    "WikiLayerProjector",
    "CODE_GRAPH_TYPES",
    "ASSERTION_LAYER_TYPES",
    "is_code_graph_node",
    "is_assertion_node",
    "kind_for_node",
    "public_nodes",
    "public_edges",
    "partition_graph",
]

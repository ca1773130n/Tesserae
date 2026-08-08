"""A chartered institution over the research graph.

Community detection PROPOSES a domain vocabulary; this module's charter OWNS
it between explicit reorgs. That split exists because detection is
deterministic but not stable: identical input reproduces all 1,649 communities
exactly, yet a single 15-node document moves ~29% of members between
communities and drops large communities to Jaccard 0.39-0.60. Anything keyed
on community membership therefore takes a near-total cache miss per ingest,
and this corpus ingests daily.

See docs/superpowers/specs/2026-08-08-charter-expertise-org-design.md.
"""

from __future__ import annotations

from typing import Sequence

from .agent_distill import _render_member_block
from .community_summaries import detect_communities
from .research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType

#: Split threshold, in rendered member-block characters. A LITERAL, not
#: ``CHUNK_CHAR_BUDGET // 2``: deriving it would let an env override of
#: TESSERAE_LLM_CHUNK_CHARS reshape the tree, which is exactly the
#: declared-input leak agent_distill.py:150-155 warns about for
#: ARTIFACT_CHAR_BUDGET.
DOMAIN_MASS_CAP = 24_000

#: Minimum mass for a sub-community to become its own domain. Tuned by
#: measurement, not taste: at 3,000 the live graph yields 92 routers / 796
#: leaves / 9 unsplittable stalls, depth median 3 max 5. At 6,000 stalls jump
#: to 53; at 12,000 to 83.
DOMAIN_MASS_FLOOR = 3_000


def mass(nodes: Sequence[ResearchNode]) -> int:
    """Rendered size of ``nodes``, in the exact text a distill prompt packs.

    Uses ``_render_member_block`` rather than a proxy so the split threshold
    is measured in the same units as the budget it protects. LLM-free, and
    already the memory-pressure proxy at agent_distill.py:2813.
    """
    return sum(len(_render_member_block(node)) for node in nodes)


def sections(graph: ResearchGraph) -> tuple[list[list[str]], list[str]]:
    """Detect sections, and REPORT what detection threw away.

    ``detect_communities`` filters ``len(c) > 1`` (community_summaries.py:106),
    so Louvain singletons are dropped silently and the returned clusters do
    NOT partition the node set. Returning the dropped ids alongside is what
    keeps the partition invariant (CH-01) provable rather than aspirational —
    they become intake members in Task 4.
    """
    clusters = detect_communities(graph)
    covered = {nid for cluster in clusters for nid in cluster}
    dropped = sorted(node.id for node in graph.nodes if node.id not in covered)
    return clusters, dropped


#: Synthetic node id prefix for quotient scope-nodes. Never persisted into
#: graph.json — the quotient graph is an in-memory scratch structure.
_SCOPE_PREFIX = "CharterScope"


def quotient_graph(graph: ResearchGraph, clusters: Sequence[Sequence[str]]) -> ResearchGraph:
    """One node per section, one ``part_of`` edge per cross-section L0 edge.

    This is what turns an unusable top level into a readable one. The existing
    dendrogram's coarsest level has 1,820 communities of median size 2, so
    ``graph_map`` at root emits 1,820 size-ranked cards behind a cursor — an
    agent picks by rank and page number rather than by meaning. Feeding the
    quotient back through the SAME detector collapses that to a handful of
    balanced divisions.

    Both the synthetic nodes AND the edges are returned, because
    ``_undirected_projection`` (community_summaries.py:131-132) drops any edge
    whose endpoints are absent from ``graph.nodes`` — a quotient carrying only
    edges would be silently invisible to Louvain.

    ``part_of`` is used because ``ResearchEdge.__post_init__`` validates
    against ALLOWED_EDGE_TYPES and raises ValueError otherwise; "quotient_of"
    does not exist.
    """
    section_of: dict[str, int] = {}
    for index, cluster in enumerate(clusters):
        for node_id in cluster:
            section_of[node_id] = index

    nodes = [
        ResearchNode(
            id=f"{_SCOPE_PREFIX}:{index}",
            name=f"section-{index}",
            type=ResearchNodeType.CONCEPT,
            metadata={"member_count": len(cluster)},
        )
        for index, cluster in enumerate(clusters)
    ]

    pairs: set[tuple[int, int]] = set()
    for edge in graph.edges:
        left = section_of.get(edge.source)
        right = section_of.get(edge.target)
        if left is None or right is None or left == right:
            continue
        pairs.add((min(left, right), max(left, right)))

    edges = [
        ResearchEdge(
            source=f"{_SCOPE_PREFIX}:{left}",
            target=f"{_SCOPE_PREFIX}:{right}",
            type="part_of",
        )
        for left, right in sorted(pairs)
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def divisions(graph: ResearchGraph, clusters: Sequence[Sequence[str]]) -> list[list[int]]:
    """Group section indices into divisions by running detection on the quotient.

    Returns lists of INDICES into ``clusters``, sorted within each group and
    with groups ordered by ``(-size, first index)`` so the result is stable
    even though ``detect_communities`` deliberately does not sort its outer
    list (community_summaries.py:88-91).

    A section with no cross-section edge is a Louvain singleton in the
    quotient and is therefore dropped by the ``len(c) > 1`` filter. Those are
    NOT returned here; Task 4 routes them to intake.
    """
    groups: list[list[int]] = []
    for cluster in detect_communities(quotient_graph(graph, clusters)):
        indices = sorted(int(nid.split(":", 1)[1]) for nid in cluster)
        groups.append(indices)
    groups.sort(key=lambda g: (-sum(len(clusters[i]) for i in g), g[0]))
    return groups

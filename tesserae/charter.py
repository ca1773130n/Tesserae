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

import re
import unicodedata
from dataclasses import dataclass
from typing import Sequence, Set

from .agent_distill import _render_member_block
from .community_summaries import detect_communities
from .hierarchy import undirected_degrees
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


def intake_members(
    graph: ResearchGraph,
    clusters: Sequence[Sequence[str]],
    groups: Sequence[Sequence[int]],
) -> list[str]:
    """Every node no division holds: dropped singletons + edge-isolated sections.

    Measured on the live graph this is 5,643 nodes (12.0%) — 1,508 sections
    with no cross-section edge (3,806 members, max size 14) plus 1,837 Louvain
    singletons. It is genuinely unroutable BY STRUCTURE: lexical clustering as
    a fallback splitter was measured and produces 5,322 clusters of median
    size 1 with zero clusters above 3,000 chars, i.e. it is a near-duplicate
    clusterer, not a topical one.

    Intake is therefore the one domain whose brief is honestly a census. The
    fix is better linking at extraction time, which is a different project;
    until then this is a standing extraction-quality lint.
    """
    routed_sections = {index for group in groups for index in group}
    routed_members = {
        node_id
        for index, cluster in enumerate(clusters)
        if index in routed_sections
        for node_id in cluster
    }
    return sorted(node.id for node in graph.nodes if node.id not in routed_members)


@dataclass(frozen=True)
class SplitResult:
    """One level of division. ``children`` + ``direct`` always reconstruct the
    input member set exactly — that is lint CH-01, true by construction here
    so it can be asserted rather than hoped for."""

    children: tuple[tuple[str, ...], ...]
    direct: tuple[str, ...]
    stalled: bool


def induced_subgraph(graph: ResearchGraph, member_ids: Sequence[str]) -> ResearchGraph:
    """The subgraph over ``member_ids``, keeping only edges internal to it."""
    keep = set(member_ids)
    nodes = [node for node in graph.nodes if node.id in keep]
    edges = [
        edge for edge in graph.edges if edge.source in keep and edge.target in keep
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def split(graph: ResearchGraph, member_ids: Sequence[str]) -> SplitResult:
    """Divide an oversized domain by SUB-COMMUNITY, never by size.

    Numbered size shards were rejected: an agent cannot tell what is in shard 2
    versus shard 3 without reading both, which reintroduces exactly the
    multi-read cost the one-read bound exists to prevent. Splitting by meaning
    lets it choose a branch from named subjects.

    A domain that cannot be divided STALLS rather than raising: it stays an
    oversized leaf flagged ``stalled``, and degrades through the artifact
    layer's existing counted-remainder path. Measured on the live graph at
    DOMAIN_MASS_FLOOR=3,000 this happens 9 times out of 888 domains.
    """
    members = sorted(set(member_ids))
    by_id = {node.id: node for node in graph.nodes}
    scoped = [by_id[mid] for mid in members if mid in by_id]

    if mass(scoped) <= DOMAIN_MASS_CAP:
        return SplitResult(children=(), direct=tuple(members), stalled=False)

    sub = induced_subgraph(graph, members)
    candidates = [
        cluster
        for cluster in detect_communities(sub)
        if mass([by_id[mid] for mid in cluster if mid in by_id]) >= DOMAIN_MASS_FLOOR
    ]
    if not candidates:
        return SplitResult(children=(), direct=tuple(members), stalled=True)

    # Sort by (-mass, first id): detect_communities deliberately does not sort
    # its outer list, so an explicit key is what makes the charter stable.
    candidates.sort(
        key=lambda c: (-mass([by_id[mid] for mid in c if mid in by_id]), c[0])
    )
    children = tuple(tuple(sorted(cluster)) for cluster in candidates)
    claimed = {mid for child in children for mid in child}
    direct = tuple(mid for mid in members if mid not in claimed)
    return SplitResult(children=children, direct=direct, stalled=False)


def assign_anchors(
    graph: ResearchGraph, member_sets: Sequence[Sequence[str]]
) -> list[str]:
    """Pick each domain's top-degree member, greedily, no two the same.

    The anchor is the identity substrate: a hub does not move when 15 nodes
    arrive. Measured preservation under a one-document perturbation is 97.0%
    at fine level and 81.0% at coarse, against member-set Jaccard which fails
    for ~72% of large scopes. Assignment is greedy in ``(-degree, id)`` order
    ACROSS siblings so two domains can never claim the same anchor — a
    collision would make two domains indistinguishable to succession.
    """
    degrees = undirected_degrees(graph)
    ranked: list[tuple[int, int, str]] = []
    for index, members in enumerate(member_sets):
        for member_id in members:
            ranked.append((-degrees.get(member_id, 0), index, member_id))
    ranked.sort(key=lambda row: (row[0], row[2]))

    anchors: dict[int, str] = {}
    claimed: set[str] = set()
    for _degree, index, member_id in ranked:
        if index in anchors or member_id in claimed:
            continue
        anchors[index] = member_id
        claimed.add(member_id)
    return [anchors.get(i, sorted(m)[0] if m else "") for i, m in enumerate(member_sets)]


def slug_for(name: str, taken: Set[str]) -> str:
    """A stable, human-facing slug. Minted once from the anchor name and pinned.

    Human-facing because it goes in a config file an operator pins an agent to,
    and it must survive a reorg. A collision gets a numeric suffix rather than
    overwriting, because two domains silently sharing a path would corrupt both.
    """
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    if not base:
        # Non-Latin names (e.g. "한 줄 요약") strip to nothing under NFKD +
        # ASCII-encode. A content hash keeps the slug stable and unique
        # rather than falling back to a counter that would move when
        # siblings are reordered — and the live graph has such names as
        # real division anchors.
        import hashlib

        base = "domain-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"

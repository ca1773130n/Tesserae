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
from .research_graph import ResearchGraph, ResearchNode

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

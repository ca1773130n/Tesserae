"""Descent hierarchy sidecar loader + ``graph_map`` card builders (§5.1, PR5).

Reads the ``.tesserae/hierarchy.json`` sidecar written by
:meth:`tesserae.project.ProjectWiki._write_hierarchy_sidecar` (PR4) and turns
its Louvain dendrogram into the uniform *cards* the ``graph_map`` MCP tool
serves::

    {scope_id, kind, title, summary<=160ch, size, children_count,
     leaf_member_count, parent_scope, tags, quality: "llm"|"structural",
     stale: bool}

Parent/child links are derived at read time by membership containment between
adjacent dendrogram levels — the sidecar stores memberships only (§3). All of
this is pure structure: zero LLM calls. Coarsest-level communities that carry
an in-graph COMMUNITY_SUMMARY node reuse its title/description/tags with
``quality="llm"``; everything else gets a deterministic structural title
(type histogram + top-degree member names, the same fallback family
``agent_topics._structural_summary`` uses) with ``quality="structural"``.
Deterministic throughout — sorted iteration, no wall-clock, no randomness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .context_compiler import _truncate_to_budget
from .research_graph import ResearchGraph, ResearchNode, ResearchNodeType

#: Per-card prose cap (§3): summaries and node descriptions are clamped to
#: this many chars before budget packing ever sees them.
SUMMARY_CHAR_CAP = 160

JSONDict = Dict[str, object]


@dataclass(frozen=True)
class Hierarchy:
    """In-memory view of the ``hierarchy.json`` sidecar.

    ``levels`` holds one ``{cid: [sorted member node ids]}`` mapping per
    Louvain dendrogram level, **finest → coarsest** (sidecar order). Every
    level's members are leaf node ids — parent/child community links exist
    only by containment, computed by the methods below.
    """

    levels: List[Dict[str, List[str]]]
    hubs: List[str]

    @property
    def coarsest(self) -> Dict[str, List[str]]:
        """The root card set's communities (empty dict when no levels)."""
        return self.levels[-1] if self.levels else {}

    def find_scope(self, cid: str) -> Optional[Tuple[int, List[str]]]:
        """Return ``(level, members)`` for ``cid``, or ``None`` if unknown.

        A community unchanged between adjacent levels carries the same
        membership hash at both, so the COARSEST occurrence is returned —
        descent then skips the byte-identical repeats (see :meth:`children`).
        """
        for level in range(len(self.levels) - 1, -1, -1):
            members = self.levels[level].get(cid)
            if members is not None:
                return level, members
        return None

    def children(self, cid: str) -> Optional[Tuple[List[Tuple[str, List[str]]], List[str]]]:
        """Direct children of ``cid``: ``(community_children, loose_member_ids)``.

        Walks finer levels from the scope's coarsest occurrence and stops at
        the first level that actually SPLITS the scope — a level where the
        scope reappears byte-identically (single child with the same cid) is
        skipped. This is the auto-coarsen guarantee below the root (§5.1):
        descent always lands on the fewest, richest cards available and never
        over-descends past an intermediate community level straight to
        hundreds of node cards. ``community_children`` is sorted
        ``(-size, cid)``; ``loose_member_ids`` are members in no child
        community at that level (Louvain drops singletons per level) and are
        rendered as node cards. When no finer level splits the scope — the
        finest level, or an identical chain all the way down — community
        children are empty and every member is loose (member-node cards).
        Returns ``None`` for an unknown ``cid``.
        """
        found = self.find_scope(cid)
        if found is None:
            return None
        level, members = found
        member_set = set(members)
        for finer in range(level - 1, -1, -1):
            community_children: List[Tuple[str, List[str]]] = []
            covered: Set[str] = set()
            for child_cid, child_members in sorted(self.levels[finer].items()):
                # Dendrogram refinement: a finer community is either entirely
                # inside one coarser community or entirely outside it, so a
                # first-member probe short-circuits the full subset check.
                if not child_members or child_members[0] not in member_set:
                    continue
                if not member_set.issuperset(child_members):
                    continue
                community_children.append((child_cid, child_members))
                covered.update(child_members)
            if len(community_children) == 1 and community_children[0][0] == cid:
                continue  # byte-identical pass-through level — keep descending
            if not community_children:
                break  # members are singletons from here down — node cards
            community_children.sort(key=lambda item: (-len(item[1]), item[0]))
            return community_children, sorted(member_set - covered)
        return [], list(members)

    def parent_scope(self, cid: str) -> Optional[str]:
        """The containing community at the next coarser level, else ``None``.

        ``None`` means the scope is a root card — ``graph_map()`` (no scope)
        is its ascend. Starts above the coarsest occurrence, so a scope
        repeated identically across levels ascends to a genuinely coarser
        community, never to itself.
        """
        found = self.find_scope(cid)
        if found is None:
            return None
        level, members = found
        probe = members[0] if members else None
        for coarser in range(level + 1, len(self.levels)):
            for parent_cid, parent_members in sorted(self.levels[coarser].items()):
                if probe is not None and probe not in set(parent_members):
                    continue
                if set(parent_members).issuperset(members):
                    return parent_cid
        return None


def load_hierarchy(project_root: Path) -> Hierarchy:
    """Load ``<project_root>/.tesserae/hierarchy.json``. Fail-loud, actionable.

    The sidecar is a pure function of ``graph.json`` written on every compile
    (PR4); its absence means the project predates Descent or was never
    compiled — the remedy is always the same, so the error says it.
    """
    path = Path(project_root) / ".tesserae" / "hierarchy.json"
    if not path.is_file():
        raise ValueError(
            f"no hierarchy sidecar at {path} — run `tesserae compile` to write "
            f"it (the Descent dendrogram is produced by every compile), then retry."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"unreadable hierarchy sidecar at {path}: {exc} — recompile the "
            f"project (`tesserae compile`) to rewrite it."
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(
            f"unsupported hierarchy sidecar at {path} "
            f"(schema_version={payload.get('schema_version') if isinstance(payload, dict) else '?'!s}, "
            f"expected 1) — recompile the project (`tesserae compile`)."
        )
    raw_levels = payload.get("levels") or []
    levels: List[Dict[str, List[str]]] = [
        {str(cid): [str(m) for m in members or []] for cid, members in (level or {}).items()}
        for level in raw_levels
        if isinstance(level, dict)
    ]
    hubs = [str(h) for h in payload.get("hubs") or []]
    return Hierarchy(levels=levels, hubs=hubs)


def undirected_degrees(graph: ResearchGraph) -> Dict[str, int]:
    """Node degree over the deduped undirected projection of ``graph``.

    Same counting rules as ``community_summaries._undirected_projection``
    (parallel/reversed edges count once, self-loops never) so structural
    card titles rank members on the projection Louvain actually clustered.
    """
    pairs: Set[Tuple[str, str]] = set()
    for edge in graph.edges:
        if edge.source == edge.target:
            continue
        lo, hi = (
            (edge.source, edge.target)
            if edge.source < edge.target
            else (edge.target, edge.source)
        )
        pairs.add((lo, hi))
    degree: Dict[str, int] = {}
    for lo, hi in pairs:
        degree[lo] = degree.get(lo, 0) + 1
        degree[hi] = degree.get(hi, 0) + 1
    return degree


def _structural_summary(
    members: Sequence[str],
    by_id: Dict[str, ResearchNode],
    degrees: Dict[str, int],
) -> Tuple[str, str, List[str]]:
    """Deterministic ``(title, summary, tags)`` for an unsummarized community.

    Title = the top-degree member's name (id tiebreak); summary = member
    count + type histogram + the top-degree member names; tags = the
    dominant type names, lowercased. Pure function of graph content — the
    same fallback family as ``agent_topics._structural_summary``.
    """
    present = [by_id[m] for m in members if m in by_id]
    if not present:
        return "Untitled community", f"{len(members)} member(s), none in the graph", []
    ranked = sorted(present, key=lambda n: (-degrees.get(n.id, 0), n.id))
    top_names = [n.name for n in ranked[:3]]
    histogram: Dict[str, int] = {}
    for node in present:
        histogram[node.type.value] = histogram.get(node.type.value, 0) + 1
    top_types = sorted(histogram.items(), key=lambda item: (-item[1], item[0]))
    hist_text = ", ".join(f"{count} {type_name}" for type_name, count in top_types[:3])
    summary = f"{len(members)} members ({hist_text}) around {', '.join(top_names)}"
    tags = [type_name.lower() for type_name, _count in top_types[:5]]
    return top_names[0], _truncate_to_budget(summary, SUMMARY_CHAR_CAP), tags


def community_card(
    hierarchy: Hierarchy,
    cid: str,
    members: Sequence[str],
    by_id: Dict[str, ResearchNode],
    degrees: Dict[str, int],
) -> JSONDict:
    """Build the uniform card for one community scope (§3 card shape).

    ``quality="llm"`` iff the graph carries a COMMUNITY_SUMMARY node whose id
    is this cid (coarsest-level communities only, minted by the summary
    pass) — its title/description/tags are reused verbatim, so this PR adds
    zero LLM calls. ``children_count`` is the number of cards
    ``graph_map(cid)`` would return pre-budget (direct sub-communities plus
    loose member nodes); ``leaf_member_count`` is transitive by construction
    (sidecar memberships are leaf node ids at every level).
    """
    summary_node = by_id.get(cid)
    if summary_node is not None and summary_node.type is ResearchNodeType.COMMUNITY_SUMMARY:
        meta = summary_node.metadata or {}
        title = summary_node.name
        summary = _truncate_to_budget(summary_node.description or "", SUMMARY_CHAR_CAP)
        tags = [str(t) for t in meta.get("tags") or []]
        quality = "llm"
    else:
        title, summary, tags = _structural_summary(members, by_id, degrees)
        quality = "structural"
    children = hierarchy.children(cid)
    community_children, loose = children if children is not None else ([], list(members))
    return {
        "scope_id": cid,
        "kind": "community",
        "title": title,
        "summary": summary,
        "size": len(members),
        "children_count": len(community_children) + len(loose),
        "leaf_member_count": len(members),
        "parent_scope": hierarchy.parent_scope(cid),
        "tags": tags,
        "quality": quality,
        "stale": False,
    }


def node_card(node_id: str, parent_cid: str, by_id: Dict[str, ResearchNode]) -> JSONDict:
    """Build the uniform card for one leaf member node (§3 card shape)."""
    node = by_id.get(node_id)
    return {
        "scope_id": node_id,
        "kind": "node",
        "title": node.name if node is not None else node_id,
        "summary": _truncate_to_budget(
            (node.description or "") if node is not None else "", SUMMARY_CHAR_CAP
        ),
        "size": 1,
        "children_count": 0,
        "leaf_member_count": 1,
        "parent_scope": parent_cid,
        "tags": [],
        "quality": "structural",
        "stale": False,
    }

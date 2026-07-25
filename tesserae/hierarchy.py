"""Descent hierarchy sidecar loader + ``graph_map`` card builders (§5.1, PR5).

Reads the ``.tesserae/hierarchy.json`` sidecar written by
:meth:`tesserae.project.ProjectWiki._write_hierarchy_sidecar` (PR4) and turns
its Louvain dendrogram into the uniform *cards* the ``graph_map`` MCP tool
serves::

    {scope_id, kind, title, summary<=160ch, size, children_count,
     leaf_member_count, parent_scope, tags, quality: "llm"|"structural",
     stale: bool}

Graph-derived cards (:func:`community_card`, :func:`node_card`) add one more
key, ``live_member_count`` — how many of the scope's members the CURRENT graph
actually carries. The sidecar is written mid-compile, so a later rewrite of
``graph.json`` can leave memberships pointing at nodes that are gone; ``size``
keeps reporting the dendrogram's count and this reports the graph's. The
registry-derived builders below (:func:`agent_card`, :func:`distilled_note_card`)
have no such skew — their membership IS the registry — so they omit it. Read it
with a ``.get()`` default when iterating mixed card kinds.

Parent/child links are derived at read time by membership containment between
adjacent dendrogram levels — the sidecar stores memberships only (§3). All of
this is pure structure: zero LLM calls. Coarsest-level communities that carry
an in-graph COMMUNITY_SUMMARY node reuse its title/description/tags with
``quality="llm"``; everything else gets a deterministic structural title
(type histogram + top-degree member names, the same fallback family
``agent_topics._structural_summary`` uses) with ``quality="structural"``.
Also home to the agent-org card builders (§6.2 PR9): :func:`agent_card` and
:func:`distilled_note_card` render the registry tree and an agent's distilled
L1 Index in the same shape minus ``live_member_count`` (see above) — pure
functions of registry structure and distillate metadata, sealed off from raw
L0 content. Federated-scope helpers
(§6.3 PR10) — :func:`split_federated_scope`, :func:`federated_scope_id`,
:func:`namespace_card` — carry the ``alias::`` grammar for sibling-project
descent, reusing ``federation.federate_graphs`` namespacing semantics.
Deterministic throughout — sorted iteration, no wall-clock, no randomness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .community_summaries import read_warm_summary
from .context_compiler import _truncate_to_budget
from .federation import _NS as FEDERATION_NS
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
    *,
    summary_cache_dir: Optional[Path] = None,
) -> JSONDict:
    """Build the uniform card for one community scope (§3 card shape).

    ``quality="llm"`` when the graph carries a COMMUNITY_SUMMARY node whose
    id is this cid (coarsest-level communities only, minted by the summary
    pass) — its title/description/tags are reused verbatim — or, given a
    ``summary_cache_dir``, when the level-scoped lazy cache holds a warm
    digest-valid summary for the cid (§5.2; written by an earlier
    ``graph_map`` visit or the daemon pre-warm). Both lookups are reads —
    card building itself never calls an LLM. ``children_count`` is the
    number of cards ``graph_map(cid)`` would return pre-budget (direct
    sub-communities plus loose member nodes); ``leaf_member_count`` is
    transitive by construction (sidecar memberships are leaf node ids at
    every level).
    """
    warm: Optional[Tuple[str, str, List[str]]] = None
    summary_node = by_id.get(cid)
    if summary_node is not None and summary_node.type is ResearchNodeType.COMMUNITY_SUMMARY:
        meta = summary_node.metadata or {}
        title = summary_node.name
        summary = _truncate_to_budget(summary_node.description or "", SUMMARY_CHAR_CAP)
        tags = [str(t) for t in meta.get("tags") or []]
        quality = "llm"
    else:
        if summary_cache_dir is not None:
            found = hierarchy.find_scope(cid)
            if found is not None:
                warm = read_warm_summary(
                    summary_cache_dir,
                    found[0],
                    cid,
                    [by_id[m] for m in members if m in by_id],
                )
        if warm is not None:
            title, description, tags = warm
            summary = _truncate_to_budget(description, SUMMARY_CHAR_CAP)
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
        # Sidecar counts above are the dendrogram's truth; this one is the
        # GRAPH's. They diverge when graph.json is rewritten after the sidecar
        # was computed (a compile ⇄ code-sync ordering skew drops the code
        # layer, say), leaving cards that advertise hundreds of members and
        # resolve to nothing. Without this field a client cannot tell a healthy
        # card from a dead one without re-reading graph.json itself — so a
        # zero here is the signal to grey the card out, not to descend.
        "live_member_count": sum(1 for m in members if m in by_id),
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
        # 0 when the sidecar references a node graph.json no longer carries —
        # the leaf-level form of the same divergence community_card reports.
        "live_member_count": 1 if node is not None else 0,
        "parent_scope": parent_cid,
        "tags": [],
        "quality": "structural",
        "stale": False,
    }


#: Scope-grammar prefix for the agent org tree (§5.1): ``agent:<key>`` maps an
#: agent's distilled L1 Index; ``org:root`` (agent_identity.ORG_ROOT) maps the
#: registry tree itself.
AGENT_SCOPE_PREFIX = "agent:"

# ``FEDERATION_NS`` (imported above) is the federated-scope separator (§6.3
# PR10): the SAME ``::`` that :func:`tesserae.federation.namespace_graph`
# stamps on cross-project node ids, imported rather than redeclared so the
# ``graph_map`` scope grammar and ``federate_graphs`` id namespacing can never
# drift apart. Merger rule, codified: cross-graph read paths use
# ``federation.federate_graphs`` namespacing semantics ONLY — the
# order-dependent ``batch.merge_graphs`` is ingest machinery and must never be
# imported here (tests/test_graph_map_federated.py lints for it).


def split_federated_scope(scope: str) -> Optional[Tuple[str, str]]:
    """Parse ``<alias>::<sub>`` into ``(alias, sub)``, or ``None`` if local.

    ``sub`` is ``""`` for the alias root scope (``"<alias>::"``). Local
    community ids carry single colons (``CommunitySummary:<hash>``) but never
    the ``::`` separator, so the probe is unambiguous. An empty alias
    (``"::<cid>"``) is malformed and reported as non-federated, so the local
    scope lookup fails loud with the full scope-grammar error.
    """
    if FEDERATION_NS not in scope:
        return None
    alias, sub = scope.split(FEDERATION_NS, 1)
    if not alias:
        return None
    return alias, sub


def federated_scope_id(alias: str, scope_id: Optional[str]) -> str:
    """Namespace a scope id as ``alias::id`` (federate_graphs semantics).

    ``None`` — a parentless root card — maps to the alias root scope
    ``"<alias>::"``, so ``graph_map(card.parent_scope)`` ascends to the
    sibling project's root map instead of dead-ending.
    """
    return alias + FEDERATION_NS + (scope_id or "")


def namespace_card(card: JSONDict, alias: str, *, stale: bool = False) -> JSONDict:
    """Rewrite one card's navigation ids into the ``alias::`` namespace (§6.3).

    ``scope_id`` and ``parent_scope`` gain the alias prefix; everything else
    is served verbatim from the sibling's compiled bytes. ``stale=True`` marks
    the card per digest verification — the caller's held map predates the
    sibling's current bytes and should be rebuilt from ``"<alias>::"``.
    """
    out = dict(card)
    out["scope_id"] = federated_scope_id(alias, str(card["scope_id"]))
    parent = card.get("parent_scope")
    out["parent_scope"] = federated_scope_id(
        alias, str(parent) if parent is not None else None
    )
    if stale:
        out["stale"] = True
    return out


def agent_card(
    agent_key: str,
    *,
    label: str,
    parent_scope: str,
    direct_reports: int,
    subtree_agents: int,
    subtree_notes: int,
    distilled: bool,
) -> JSONDict:
    """Uniform card for one org-tree agent (§6.2).

    Pure registry/artifact structure — never resolved views, never L0 content
    (sealed L0). ``children_count`` is the agent's DIRECT reports (spec-pinned),
    ``size`` the agents in its subtree (itself included), ``leaf_member_count``
    the transitive DistilledNote count over the subtree's artifacts — the org
    tree's leaves are distillates, so that is the branch mass a caller gauges
    before descending. Descent is ``graph_map("agent:<key>")``; ``parent_scope``
    is ``org:root`` for root children, else the parent's agent scope.
    """
    artifact = "artifact present" if distilled else "artifact missing — run tesserae distill"
    summary = (
        f"{direct_reports} direct report(s), {subtree_agents} agent(s) in "
        f"subtree, {subtree_notes} distilled note(s); {artifact}"
    )
    return {
        "scope_id": AGENT_SCOPE_PREFIX + agent_key,
        "kind": "agent",
        "title": label or agent_key,
        "summary": _truncate_to_budget(summary, SUMMARY_CHAR_CAP),
        "size": subtree_agents,
        "children_count": direct_reports,
        "leaf_member_count": subtree_notes,
        "parent_scope": parent_scope,
        "tags": [],
        "quality": "structural",
        "stale": False,
    }


def distilled_note_card(note: ResearchNode) -> JSONDict:
    """Uniform card for one L1 DistilledNote (§6.2 — the agent's Index entry).

    Title/kind/size come from the distillate ONLY (sealed L0: member_refs are
    pointers, never dereferenced here). The ``drill`` block carries exactly the
    arguments the existing audited ``drill_down`` tool needs to escalate one
    member to raw L0: the owning agent key plus ``{node_id, content_hash}``
    refs. ``quality`` reflects ``distill_quality`` — llm-authored notes are
    ``"llm"``, deterministic fallback/structural notes stay visibly
    ``"structural"`` (§9 risk 8). ``parent_scope`` ascends to the OWNING
    agent's scope, so a federated manager-view note reorients to its author.
    """
    meta = note.metadata or {}
    owner = str(meta.get("agent") or "")
    member_refs = [
        {
            "node_id": str(ref.get("node_id") or ""),
            "content_hash": str(ref.get("content_hash") or ""),
        }
        for ref in meta.get("member_refs") or []
        if isinstance(ref, dict)
    ]
    kind = str(meta.get("kind") or "note")
    quality = "llm" if str(meta.get("distill_quality") or "") == "llm" else "structural"
    return {
        "scope_id": note.id,
        "kind": "note",
        "title": note.name,
        "summary": _truncate_to_budget(note.description or "", SUMMARY_CHAR_CAP),
        "size": len(member_refs),
        "children_count": 0,
        "leaf_member_count": len(member_refs),
        "parent_scope": AGENT_SCOPE_PREFIX + owner if owner else None,
        "tags": [kind],
        "quality": quality,
        "stale": False,
        "drill": {"tool": "drill_down", "agent": owner, "member_refs": member_refs},
    }

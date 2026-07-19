"""Per-agent topic-map rollup — the llm-wiki table of contents (§12 Phase 5).

Runs the existing :mod:`tesserae.community_summaries` machinery over a single
agent's distilled artifact (``.tesserae/agents/<key>/distilled.graph.json``) and
writes a deterministic ``topics.md`` projection next to it.

Design notes
------------
* **Input is the distillate set, not the project graph.** We slice the loaded
  artifact to its ``DistilledNote`` nodes plus the anchor nodes they cite
  (``derived_from`` edges), keeping only those edges. Two notes cluster iff they
  share a cited anchor — that shared-anchor bridge is what makes a "topic". The
  structural index/activity meta-notes (``kind`` in ``{index, activity}``) carry
  no ``derived_from`` edges, so they naturally drop out as singletons; we exclude
  them explicitly for clarity.
* **LLM titles via the injected-summarizer pattern** — a ``json_client`` with
  ``complete_json`` (the same protocol :mod:`community_summaries` and
  :mod:`agent_distill` accept). Tests stub it; without one we fall back to a
  deterministic structural title so the TOC is never empty when clusters exist.
* **Byte-idempotent projection.** ``topics.md`` is a content-stable regen
  (write-if-changed), a pure function of the artifact bytes + the content-keyed
  summary cache — no wall-clock, no counters, everything sorted. Mirrors the
  schema/index regen discipline in :mod:`tesserae.karpathy_layer`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .agent_distill import DistillError, agent_artifact_path
from .community_summaries import (
    community_id,
    compile_community_summaries,
    detect_communities,
)
from .project import load_graph_file
from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

# Meta-notes that describe the artifact rather than a topic — never their own
# cluster (they carry no ``derived_from`` anchors, so this is belt-and-braces).
_META_KINDS = frozenset({"index", "activity"})

# Sub-directory (next to the artifact) for the per-agent community-summary
# cache. Distinct from the project-level community cache so cluster ids can
# never collide across the two passes.
_CACHE_DIRNAME = "topics_cache"


def agent_topics_path(project_root: Path | str, agent_key: str) -> Path:
    """``.tesserae/agents/<sanitized_key>/topics.md`` — sibling of the artifact."""
    return agent_artifact_path(project_root, agent_key).parent / "topics.md"


def _topic_slice(graph: ResearchGraph) -> ResearchGraph:
    """DistilledNote (excluding meta-notes) + cited anchors + derived_from edges.

    The community detector clusters on edge structure, so we hand it only the
    note→anchor bridges: notes sharing an anchor become one connected component,
    notes with disjoint anchors stay singletons and drop out.
    """
    by_id = {n.id: n for n in graph.nodes}
    note_ids = {
        n.id
        for n in graph.nodes
        if n.type is ResearchNodeType.DISTILLED_NOTE
        and str((n.metadata or {}).get("kind") or "") not in _META_KINDS
    }
    keep_edges: List[ResearchEdge] = []
    anchor_ids: set[str] = set()
    for edge in graph.edges:
        if edge.type != "derived_from":
            continue
        if edge.source not in note_ids:
            continue
        if edge.target not in by_id:
            continue
        keep_edges.append(edge)
        anchor_ids.add(edge.target)
    keep_ids = note_ids | anchor_ids
    nodes = [by_id[i] for i in sorted(keep_ids)]
    edges = sorted(
        (e for e in keep_edges if e.source in keep_ids and e.target in keep_ids),
        key=lambda e: (e.source, e.type, e.target),
    )
    return ResearchGraph(nodes=nodes, edges=edges)


def _structural_summary(
    member_ids: List[str],
    by_id: Dict[str, ResearchNode],
) -> Tuple[str, str, List[str]]:
    """Deterministic title/description/tags when no summarizer is available.

    Title = the most-connected anchor's name (id tiebreak), else the
    lowest-id note's name. Pure function of the cluster's node content.
    """
    notes = [
        by_id[m]
        for m in member_ids
        if m in by_id and by_id[m].type is ResearchNodeType.DISTILLED_NOTE
    ]
    anchors = [
        by_id[m]
        for m in member_ids
        if m in by_id and by_id[m].type is not ResearchNodeType.DISTILLED_NOTE
    ]
    if anchors:
        title = sorted(anchors, key=lambda n: n.id)[0].name
    elif notes:
        title = sorted(notes, key=lambda n: n.id)[0].name
    else:
        title = "Untitled topic"
    description = (
        f"{len(notes)} related note(s) sharing {len(anchors)} anchor(s)."
    )
    return title, description, []


def _render(agent_key: str, entries: List[Dict[str, object]]) -> str:
    """Deterministic markdown TOC. ``entries`` is already sorted by caller."""
    lines: List[str] = []
    lines.append(f"# Topic map — {agent_key}")
    lines.append("")
    lines.append(
        "Auto-generated table of contents over this agent's distilled notes. "
        "Editing it by hand has no effect — it is regenerated on the next "
        "distill/topics pass."
    )
    lines.append("")
    if not entries:
        lines.append(
            "_No multi-note topics yet — this agent's distilled notes do not "
            "share cited anchors._"
        )
        lines.append("")
        return "\n".join(lines)
    lines.append(f"{len(entries)} topic(s).")
    lines.append("")
    for entry in entries:
        lines.append(f"## {entry['title']}")
        lines.append("")
        lines.append(str(entry["description"]))
        lines.append("")
        tags = entry["tags"]
        if isinstance(tags, list) and tags:
            lines.append("Tags: " + ", ".join(str(t) for t in tags))
            lines.append("")
        note_names = entry["notes"]
        if isinstance(note_names, list) and note_names:
            lines.append("Notes:")
            for name in note_names:
                lines.append(f"- {name}")
            lines.append("")
    return "\n".join(lines)


def _write_if_changed(path: Path, body: str) -> bool:
    """Write ``body`` only when it differs from what's on disk. Returns changed."""
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == body:
                return False
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return True


def compile_agent_topics(
    project_root: Path | str,
    agent_key: str,
    *,
    json_client: Optional[object] = None,
    min_size: int = 2,
    max_communities: int = 50,
) -> Path:
    """Build ``topics.md`` for ``agent_key`` from its distilled artifact.

    Fails loud (:class:`DistillError`, with the standard remedy message) when the
    artifact is missing. Writes a byte-idempotent topic-map projection and
    returns its path. Runs the LLM summarizer when ``json_client`` is supplied;
    otherwise every cluster gets a deterministic structural title.
    """
    root = Path(project_root)
    artifact = agent_artifact_path(root, agent_key)
    if not artifact.is_file():
        raise DistillError(
            f"agent {agent_key} has no distilled artifact at {artifact}; "
            f"run: tesserae distill --agent {agent_key}"
        )

    graph = load_graph_file(artifact)
    topic_graph = _topic_slice(graph)
    by_id = {n.id: n for n in topic_graph.nodes}

    # Cluster enumeration is the single source of truth for the TOC; the
    # summarizer only supplies richer titles. detect_communities returns sorted
    # member-id lists, and compile_community_summaries keys the same lists via
    # community_id() — so LLM summaries line up by id with no re-detection.
    clusters = [c for c in detect_communities(topic_graph) if len(c) >= max(2, int(min_size))]
    clusters.sort(key=lambda c: (-len(c), c[0] if c else ""))
    clusters = clusters[: max(1, int(max_communities))]

    summary_by_id: Dict[str, ResearchNode] = {}
    if json_client is not None:
        cache_dir = artifact.parent / _CACHE_DIRNAME
        slice_summaries = compile_community_summaries(
            topic_graph,
            cache_dir=cache_dir,
            json_client=json_client,
            min_size=min_size,
            max_communities=max_communities,
        )
        summary_by_id = {
            n.id: n
            for n in slice_summaries.nodes
            if n.type is ResearchNodeType.COMMUNITY_SUMMARY
        }

    entries: List[Dict[str, object]] = []
    for member_ids in clusters:
        cid = community_id(member_ids)
        summary = summary_by_id.get(cid)
        if summary is not None:
            title = summary.name
            description = summary.description
            tags = list((summary.metadata or {}).get("tags") or [])
        else:
            title, description, tags = _structural_summary(member_ids, by_id)
        note_names = sorted(
            by_id[m].name
            for m in member_ids
            if m in by_id and by_id[m].type is ResearchNodeType.DISTILLED_NOTE
        )
        entries.append(
            {
                "cid": cid,
                "title": title,
                "description": description,
                "tags": tags,
                "notes": note_names,
            }
        )

    # Stable render order: by cluster id (content-derived), independent of the
    # size-ranked detection order above.
    entries.sort(key=lambda e: str(e["cid"]))

    path = agent_topics_path(root, agent_key)
    _write_if_changed(path, _render(agent_key, entries))
    return path

"""Post-compile community detection + LLM summarization pass.

Microsoft GraphRAG playbook (see /tmp/tesserae-innovation/02-graphrag.md)
applied to the typed ``ResearchGraph``:

1. Project the typed graph to an undirected graph (community detection
   ignores edge direction and ontology).
2. Run ``networkx.community.louvain_communities`` when ``networkx`` is
   importable; otherwise fall back to deterministic label propagation.
3. Per cluster (>= ``min_size`` members), call an :class:`LLMJsonClient`
   for a ``{title, description, tags}`` triple. Cache at
   ``<cache_dir>/<community_id>.json`` keyed on the sorted-member content
   hash — membership-stable re-runs skip the LLM entirely.
4. Mint a :class:`ResearchNode` of type ``COMMUNITY_SUMMARY`` plus a
   ``summarizes`` edge per member.

Opt-in via ``TESSERAE_COMMUNITY_SUMMARIES=true`` (wired by
:meth:`tesserae.project.ProjectWiki._merge_community_summaries`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Community detection
# ---------------------------------------------------------------------------


def detect_communities(graph: ResearchGraph) -> List[List[str]]:
    """Return non-singleton node-id clusters from the undirected projection."""
    nodes = [n.id for n in graph.nodes]
    if not nodes:
        return []
    adj: Dict[str, Set[str]] = {nid: set() for nid in nodes}
    node_set = set(nodes)
    for edge in graph.edges:
        if edge.source == edge.target:
            continue
        if edge.source not in node_set or edge.target not in node_set:
            continue
        adj[edge.source].add(edge.target)
        adj[edge.target].add(edge.source)
    try:
        import networkx as nx  # type: ignore[import-not-found]

        g = nx.Graph()
        g.add_nodes_from(nodes)
        for src, neighbours in adj.items():
            for dst in neighbours:
                if src < dst:
                    g.add_edge(src, dst)
        # ``seed`` keeps Louvain deterministic so cache ids stay stable.
        clusters = nx.community.louvain_communities(g, seed=0)
        return [sorted(c) for c in clusters if len(c) > 1]
    except ImportError:  # pragma: no cover — exercised on minimal installs
        return _label_propagation(nodes, adj)


def _label_propagation(
    nodes: Sequence[str],
    adj: Mapping[str, Set[str]],
    *,
    max_iter: int = 30,
) -> List[List[str]]:
    """Deterministic synchronous label-propagation fallback."""
    labels: Dict[str, str] = {nid: nid for nid in nodes}
    ordered = sorted(nodes)
    for _ in range(max_iter):
        changed = False
        new_labels = dict(labels)
        for nid in ordered:
            neigh = adj.get(nid) or set()
            if not neigh:
                continue
            counts: Dict[str, int] = defaultdict(int)
            for n in neigh:
                counts[labels[n]] += 1
            best = max(counts.values())
            winners = sorted(l for l, c in counts.items() if c == best)
            chosen = winners[0]
            if chosen != labels[nid]:
                new_labels[nid] = chosen
                changed = True
        labels = new_labels
        if not changed:
            break
    groups: Dict[str, List[str]] = defaultdict(list)
    for nid, lab in labels.items():
        groups[lab].append(nid)
    return [sorted(g) for g in groups.values() if len(g) > 1]


# ---------------------------------------------------------------------------
# LLM summarization + cache
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = (
    "You are summarizing a community of related typed research-graph nodes. "
    "Return JSON with exactly three keys: \"title\" (<= 5 tokens, headline "
    "style), \"description\" (<= 60 tokens, single sentence describing the "
    "shared theme), \"tags\" (array of exactly 5 short lowercase keyword "
    "strings, no spaces inside a tag — use hyphens). Do not invent members "
    "outside the supplied list."
)


def _format_user_prompt(members: Sequence[ResearchNode]) -> str:
    lines = [f"Community has {len(members)} members. Members:"]
    for n in members:
        desc = (n.description or "").strip().splitlines()[0] if n.description else ""
        desc = desc[:160]
        lines.append(f"- {n.name} ({n.type.value}): {desc}")
    lines.append("")
    lines.append(
        'Respond with: {"title": "...", "description": "...", '
        '"tags": ["a","b","c","d","e"]}'
    )
    return "\n".join(lines)


def community_id(member_ids: Sequence[str]) -> str:
    """Stable id derived from the sorted-member content hash."""
    h = hashlib.sha256(("\n".join(sorted(member_ids))).encode("utf-8")).hexdigest()
    return f"CommunitySummary:{h[:16]}"


def _cache_path(cache_dir: Path, cid: str) -> Path:
    safe = cid.replace(":", "_")
    return cache_dir / f"{safe}.json"


def _read_cache(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(path: Path, payload: dict) -> None:
    # PID + random suffix so concurrent compiles don't race on a shared
    # `<x>.tmp` (same pattern as tesserae/batch.py::_write_manifest).
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _validate_summary(payload: object) -> Optional[Tuple[str, str, List[str]]]:
    """Return ``(title, description, tags)`` or ``None`` on invalid input."""
    if not isinstance(payload, dict):
        return None
    title = str(payload.get("title") or "").strip()
    description = str(payload.get("description") or "").strip()
    tags_raw = payload.get("tags") or []
    if not title or not description or not isinstance(tags_raw, list):
        return None
    tags = [str(t).strip().lower() for t in tags_raw if str(t).strip()]
    if not tags:
        return None
    return title, description, tags[:5]


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def compile_community_summaries(
    graph: ResearchGraph,
    *,
    cache_dir: Path,
    json_client: Optional[object] = None,
    min_size: int = 3,
    max_communities: int = 50,
    max_members_in_prompt: int = 25,
) -> ResearchGraph:
    """Mint COMMUNITY_SUMMARY nodes + ``summarizes`` edges for ``graph``.

    Returns a slice graph (summaries + edges only). Callers merge it back
    via :func:`tesserae.batch.merge_graphs`. Returns an empty graph when
    no cluster meets ``min_size`` or no LLM client is available and
    nothing is cached.
    """
    if not graph.nodes:
        return ResearchGraph()
    by_id = {n.id: n for n in graph.nodes}
    communities = detect_communities(graph)
    communities = [c for c in communities if len(c) >= max(2, int(min_size))]
    communities.sort(key=lambda c: (-len(c), c[0] if c else ""))
    communities = communities[: max(1, int(max_communities))]
    cache_dir.mkdir(parents=True, exist_ok=True)

    new_nodes: List[ResearchNode] = []
    new_edges: List[ResearchEdge] = []
    for member_ids in communities:
        cid = community_id(member_ids)
        cache_path = _cache_path(cache_dir, cid)
        cached = _read_cache(cache_path)
        members = [by_id[m] for m in member_ids if m in by_id]
        if not members:
            continue
        summary: Optional[Tuple[str, str, List[str]]] = None
        cache_hit = False
        if cached and isinstance(cached, dict):
            payload = cached.get("summary")
            summary = _validate_summary(payload) if payload else None
            cache_hit = summary is not None
        if summary is None:
            if json_client is None:
                logger.debug("community_summaries: no LLM; skipping %s", cid)
                continue
            prompt_members = members[: max(1, int(max_members_in_prompt))]
            try:
                resp = json_client.complete_json(  # type: ignore[attr-defined]
                    system=_SYSTEM_PROMPT,
                    user=_format_user_prompt(prompt_members),
                    schema_name="community_summary",
                    cache_key=f"community-summary-v1::{len(prompt_members)}",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("community_summaries: LLM failed for %s: %s", cid, exc)
                continue
            summary = _validate_summary(resp)
            if summary is None:
                logger.warning("community_summaries: invalid LLM response for %s", cid)
                continue
            _write_cache(
                cache_path,
                {
                    "schema_version": 1,
                    "community_id": cid,
                    "member_ids": list(member_ids),
                    "summary": {
                        "title": summary[0],
                        "description": summary[1],
                        "tags": summary[2],
                    },
                },
            )
        title, description, tags = summary
        new_nodes.append(
            ResearchNode(
                id=cid,
                name=title,
                type=ResearchNodeType.COMMUNITY_SUMMARY,
                description=description,
                aliases=[],
                metadata={
                    "member_ids": list(member_ids),
                    "member_count": len(member_ids),
                    "tags": tags,
                    "cache_hit": cache_hit,
                    "extractor": "community_summaries.compile_community_summaries",
                },
            )
        )
        for mid in member_ids:
            if mid not in by_id:
                continue
            new_edges.append(
                ResearchEdge(
                    source=cid,
                    target=mid,
                    type="summarizes",
                    metadata={"community_id": cid},
                )
            )
    return ResearchGraph(nodes=new_nodes, edges=new_edges)


def is_enabled_via_env(env: Optional[Mapping[str, str]] = None) -> bool:
    """Return True when ``TESSERAE_COMMUNITY_SUMMARIES`` is set to a truthy value."""
    env = env if env is not None else os.environ
    value = (env.get("TESSERAE_COMMUNITY_SUMMARIES") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}

"""Post-compile community detection + LLM summarization pass.

Microsoft GraphRAG playbook (see /tmp/tesserae-innovation/02-graphrag.md)
applied to the typed ``ResearchGraph``:

1. Project the typed graph to an undirected graph (community detection
   ignores edge direction and ontology).
2. Run ``networkx.community.louvain_communities`` when ``networkx`` is
   importable; otherwise fall back to deterministic label propagation.
3. Per cluster (>= ``min_size`` members), call an :class:`LLMJsonClient`
   for a ``{title, description, tags}`` triple. Cache at
   ``<cache_dir>/<community_id>.json`` keyed on the sorted member ids and
   invalidated via a content digest over the member prompt lines —
   membership- and content-stable re-runs skip the LLM entirely.
4. Mint a :class:`ResearchNode` of type ``COMMUNITY_SUMMARY`` plus a
   ``summarizes`` edge per member.

Default-on; opt-out via ``TESSERAE_COMMUNITY_SUMMARIES=false`` (wired
by :meth:`tesserae.project.ProjectWiki._merge_community_summaries`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
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
    """Return non-singleton node-id clusters from the undirected projection.

    Uses ``networkx.community.louvain_communities`` (pinned in
    ``pyproject.toml``) with a fixed ``seed`` so cluster cache ids stay
    deterministic across runs. The previous label-propagation fallback was
    removed in favour of a single tested code path: it collapsed the
    two-triangle-with-a-bridge fixture into one cluster on minimal installs,
    diverging from the production behaviour asserted by
    ``test_detect_communities_returns_two_clusters``.
    """
    # Canonical, order-independent input. Louvain with a fixed seed is still
    # sensitive to node/edge INSERTION ORDER, so an incremental compile (whose
    # graph is assembled in a different order than a full compile) would mint a
    # different partition for the SAME node set. Sorting the node ids and edges
    # before construction makes the partition depend only on the graph's content
    # — identical for full vs incremental (CMP-03 community parity).
    nodes = sorted(n.id for n in graph.nodes)
    if not nodes:
        return []
    node_set = set(nodes)
    edge_pairs: Set[Tuple[str, str]] = set()
    for edge in graph.edges:
        if edge.source == edge.target:
            continue
        if edge.source not in node_set or edge.target not in node_set:
            continue
        lo, hi = (edge.source, edge.target) if edge.source < edge.target else (edge.target, edge.source)
        edge_pairs.add((lo, hi))

    import networkx as nx

    g = nx.Graph()
    g.add_nodes_from(nodes)
    g.add_edges_from(sorted(edge_pairs))
    # ``seed`` + canonical insertion order keep Louvain deterministic so cache
    # ids stay stable across full and incremental compiles.
    clusters = nx.community.louvain_communities(g, seed=0)
    return [sorted(c) for c in clusters if len(c) > 1]


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


def _member_line(n: ResearchNode) -> str:
    desc = (n.description or "").strip().splitlines()[0] if n.description else ""
    desc = desc[:160]
    return f"- {n.name} ({n.type.value}): {desc}"


def _format_user_prompt(members: Sequence[ResearchNode]) -> str:
    lines = [f"Community has {len(members)} members. Members:"]
    for n in members:
        lines.append(_member_line(n))
    lines.append("")
    lines.append(
        'Respond with: {"title": "...", "description": "...", '
        '"tags": ["a","b","c","d","e"]}'
    )
    return "\n".join(lines)


def community_id(member_ids: Sequence[str]) -> str:
    """Stable id derived from the sorted member *ids* (membership only).

    Node identity must stay stable across member-content edits so
    ``graph.json`` stays byte-idempotent; content drift is handled by
    :func:`_members_digest` cache invalidation instead.
    """
    h = hashlib.sha256(("\n".join(sorted(member_ids))).encode("utf-8")).hexdigest()
    return f"CommunitySummary:{h[:16]}"


def _members_digest(members: Sequence[ResearchNode]) -> str:
    """Content hash over the exact per-member lines the LLM prompt uses.

    Reuses :func:`_member_line` so digest and prompt can never drift.
    Lines are sorted so the digest depends only on member content, not
    iteration order. Any change to a member's name/type/description
    invalidates the cached summary even when membership (and therefore
    :func:`community_id`) is unchanged.
    """
    lines = sorted(_member_line(n) for n in members)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


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
        prompt_members = members[: max(1, int(max_members_in_prompt))]
        digest = _members_digest(prompt_members)
        summary: Optional[Tuple[str, str, List[str]]] = None
        if cached and isinstance(cached, dict):
            payload = cached.get("summary")
            summary = _validate_summary(payload) if payload else None
            if summary is not None:
                stored_digest = cached.get("members_digest")
                if stored_digest is None:
                    # Legacy pre-digest cache: honour it once but backfill
                    # the digest so future runs can detect member-content
                    # drift (avoids a one-time LLM stampede on upgrade).
                    _write_cache(cache_path, {**cached, "members_digest": digest})
                elif stored_digest != digest:
                    if json_client is None:
                        logger.warning(
                            "community_summaries: cached summary for %s is "
                            "stale (member content changed) but no LLM is "
                            "available; serving the stale summary",
                            cid,
                        )
                    else:
                        summary = None  # content drifted → re-summarize
        if summary is None:
            if json_client is None:
                logger.debug("community_summaries: no LLM; skipping %s", cid)
                continue
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
                    "members_digest": digest,
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
                    # NOTE: only membership-stable, content-derived fields may
                    # live here — COMMUNITY_SUMMARY nodes flow into
                    # ``.tesserae/site/graph.json``, which §13 requires to be
                    # byte-identical across re-compiles of an unchanged corpus.
                    # Build-provenance that differs between runs (e.g. whether
                    # this run was an LLM call or a cache hit) must NOT be
                    # persisted here or it breaks ``test_compile_is_byte_idempotent``.
                    "member_ids": list(member_ids),
                    "member_count": len(member_ids),
                    "tags": tags,
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
    """Decide whether to run the community-summary pass.

    Default-on (post-v0.3.0). Unlike the pure-Python passes
    (e.g. insight-symbol-link) this calls the LLM once per cluster,
    so we soften the default-on cost at the wiring layer by bumping
    ``min_size`` from 3 to 5 — only meaningfully-sized clusters get
    summarized unless the project config opts back into a lower
    threshold.

    To opt out explicitly, set ``TESSERAE_COMMUNITY_SUMMARIES`` to
    one of: ``false``, ``0``, ``no``, ``off`` (case-insensitive,
    whitespace-trimmed). Unset / empty / whitespace / any other
    value means enabled.
    """
    env = env if env is not None else os.environ
    value = (env.get("TESSERAE_COMMUNITY_SUMMARIES") or "").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return False
    return True

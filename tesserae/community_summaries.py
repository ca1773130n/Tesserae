"""Post-compile community detection + LLM summarization pass.

Microsoft GraphRAG playbook (see /tmp/tesserae-innovation/02-graphrag.md)
applied to the typed ``ResearchGraph``:

1. Project the typed graph to an undirected graph (community detection
   ignores edge direction and ontology).
2. Run ``networkx.community.louvain_partitions`` (seed=0) and keep the
   coarsest dendrogram level (byte-identical to the previous direct
   ``louvain_communities`` call; finer levels feed the Descent hierarchy
   sidecar via :func:`detect_community_levels`).
3. Per cluster (>= ``min_size`` members), call an :class:`LLMJsonClient`
   for a ``{title, description, tags}`` triple. Cache at
   ``<cache_dir>/<community_id>.json`` keyed on the sorted member ids and
   invalidated via a content digest over the member prompt lines —
   membership- and content-stable re-runs skip the LLM entirely.
4. Mint a :class:`ResearchNode` of type ``COMMUNITY_SUMMARY`` plus a
   ``summarizes`` edge per member.

Default-on; opt-out via ``TESSERAE_COMMUNITY_SUMMARIES=false`` (wired
by :meth:`tesserae.project.ProjectWiki._merge_community_summaries`).

Descent PR6 (§5.2) reuses the same single-community path lazily at query
time: :func:`materialize_community_summary` pays exactly one LLM call the
first time ``graph_map`` visits a cold community, caching the result under
the level-scoped layout ``<cache_dir>/<level>/CommunitySummary_<cid>.json``
(same envelope, same digest invalidation, same atomic write). Scopes whose
children are themselves communities carry a citation discipline: the prompt
lists the child community ids and :func:`_cites_child_communities` rejects
prose that cites none of them — rejected output falls back to the
structural card and is never cached as llm-quality.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

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

    Implemented as the coarsest level of :func:`detect_community_levels`
    (Descent PR3). ``networkx.community.louvain_communities`` is defined as
    the last yield of ``louvain_partitions`` with the same ``seed``, so the
    output here is byte-identical to the previous direct
    ``louvain_communities(seed=0)`` call — same clusters, same cluster order,
    same member order (asserted by
    ``test_detect_communities_matches_legacy_louvain_communities`` and the
    CMP-03 full-vs-incremental parity tests). The previous label-propagation
    fallback was removed in favour of a single tested code path: it collapsed
    the two-triangle-with-a-bridge fixture into one cluster on minimal
    installs, diverging from the production behaviour asserted by
    ``test_detect_communities_returns_two_clusters``.
    """
    levels = detect_community_levels(graph)
    return levels[-1] if levels else []


def detect_community_levels(graph: ResearchGraph) -> List[List[List[str]]]:
    """Return every Louvain dendrogram level, finest to coarsest.

    Uses ``networkx.community.louvain_partitions`` (pinned in
    ``pyproject.toml``) with a fixed ``seed`` so cluster cache ids stay
    deterministic across runs. Each level applies the SAME filtering
    :func:`detect_communities` has always applied to the coarsest level:
    singleton clusters are dropped (``len > 1``) and each surviving cluster's
    member ids are sorted. Cluster order within a level is Louvain's
    deterministic emission order — NOT re-sorted, because re-ordering the
    coarsest level would change :func:`detect_communities` output and
    therefore ``graph.json`` bytes (§13 idempotence). A level that is all
    singletons filters to an empty list but is kept, so level indices stay
    aligned with the dendrogram.
    """
    nodes, edge_pairs = _undirected_projection(graph)
    if not nodes:
        return []

    import networkx as nx

    g = nx.Graph()
    g.add_nodes_from(nodes)
    g.add_edges_from(edge_pairs)
    # ``seed`` + canonical insertion order keep Louvain deterministic so cache
    # ids stay stable across full and incremental compiles.
    return [
        [sorted(c) for c in partition if len(c) > 1]
        for partition in nx.community.louvain_partitions(g, seed=0)
    ]


def _undirected_projection(graph: ResearchGraph) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Canonical sorted undirected projection of ``graph``.

    Sorted node ids plus deduped ``(lo, hi)`` edge pairs — self-loops and
    edges with a missing endpoint dropped. Canonical, order-independent
    input: Louvain with a fixed seed is still sensitive to node/edge
    INSERTION ORDER, so an incremental compile (whose graph is assembled in
    a different order than a full compile) would mint a different partition
    for the SAME node set. Sorting the node ids and edges before
    construction makes the partition depend only on the graph's content —
    identical for full vs incremental (CMP-03 community parity). Shared by
    :func:`detect_community_levels` and :func:`hub_node_ids` so hub degrees
    and partitions can never drift onto different projections.
    """
    nodes = sorted(n.id for n in graph.nodes)
    node_set = set(nodes)
    edge_pairs: Set[Tuple[str, str]] = set()
    for edge in graph.edges:
        if edge.source == edge.target:
            continue
        if edge.source not in node_set or edge.target not in node_set:
            continue
        lo, hi = (edge.source, edge.target) if edge.source < edge.target else (edge.target, edge.source)
        edge_pairs.add((lo, hi))
    return nodes, sorted(edge_pairs)


# Degree threshold above which a node is listed in the hierarchy sidecar's
# ``hubs`` array (Descent §3): consumers (PPR degree-capping, provenance
# downweighting) treat these as hub nodes whose neighborhoods explode
# naive expansion — e.g. the deg-1,257 leaked-prompt Session node.
HUB_DEGREE_THRESHOLD = 200


def hub_node_ids(graph: ResearchGraph, *, degree_threshold: int = HUB_DEGREE_THRESHOLD) -> List[str]:
    """Sorted node ids with degree > ``degree_threshold`` in the undirected projection.

    Degrees are computed over the SAME deduped projection Louvain partitions
    (parallel/reversed edges count once, self-loops never), so the hub list
    is a pure, deterministic function of graph content.
    """
    _, edge_pairs = _undirected_projection(graph)
    degree: Dict[str, int] = {}
    for lo, hi in edge_pairs:
        degree[lo] = degree.get(lo, 0) + 1
        degree[hi] = degree.get(hi, 0) + 1
    return sorted(nid for nid, d in degree.items() if d > degree_threshold)


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

# Appended to the system prompt when the summarized scope has community
# children (§5.2 citation discipline): the description must cite at least one
# child community id verbatim, and :func:`_cites_child_communities` enforces
# it deterministically after the call.
_CITATION_SYSTEM_SUFFIX = (
    " This community is composed of the child sub-communities listed in the "
    "prompt; the description MUST cite at least one child community id "
    "verbatim (copy the full id string)."
)


def _member_line(n: ResearchNode) -> str:
    desc = (n.description or "").strip().splitlines()[0] if n.description else ""
    desc = desc[:160]
    return f"- {n.name} ({n.type.value}): {desc}"


def _format_user_prompt(
    members: Sequence[ResearchNode], child_cids: Sequence[str] = ()
) -> str:
    lines = [f"Community has {len(members)} members. Members:"]
    for n in members:
        lines.append(_member_line(n))
    if child_cids:
        lines.append("")
        lines.append(
            "Child sub-communities (cite at least one of these ids verbatim "
            "in the description):"
        )
        for child_cid in child_cids:
            lines.append(f"- {child_cid}")
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


#: Longest filename this module will produce, in BYTES. APFS, ext4 and NTFS all
#: cap a single path COMPONENT at 255 bytes; 200 leaves room for the ``.json``
#: suffix and for a caller's own prefix without tracking each filesystem's exact
#: rule. Chosen once here so the truncation point cannot drift between writers.
_MAX_STEM_BYTES = 200


def _cache_path(cache_dir: Path, cid: str) -> Path:
    """``<cache_dir>/<cid with ':' → '_'>.json``, bounded to a writable length.

    A community id carries its anchor's slug, and a charter anchor can be a
    whole sentence — the graph is built from prose, so an anchor like "the label
    cap is count-based and unchanged so increasing label sprite scale…" becomes
    a 250-character slug. Unbounded, that produced a filename over the 255-byte
    limit every mainstream filesystem enforces, and the write failed with
    ``[Errno 63] File name too long``. Measured on this project's 1,411-domain
    charter: 5 domains could never cache a summary, on any budget and any retry,
    because the failure was in the path rather than in the answer.

    Over-long stems are truncated and given a 16-hex-character digest of the
    FULL id, so two ids sharing a 180-character prefix still address different
    files. Anything that already fits is left byte-identical — the 1,405 caches
    written before this existed keep resolving, and a rename pass is not needed.
    """
    safe = cid.replace(":", "_")
    if len(safe.encode("utf-8")) <= _MAX_STEM_BYTES:
        return cache_dir / f"{safe}.json"
    digest = hashlib.sha1(cid.encode("utf-8")).hexdigest()[:16]
    # Truncate on a byte boundary, not a character count: a multi-byte slug
    # sliced by characters can still exceed the limit.
    keep = _MAX_STEM_BYTES - len(digest) - 1
    stem = safe.encode("utf-8")[:keep].decode("utf-8", "ignore").rstrip("-_")
    return cache_dir / f"{stem}-{digest}.json"


def level_cache_path(cache_dir: Path, level: int, cid: str) -> Path:
    """Level-scoped cache location for lazily-materialized summaries (§3).

    ``<cache_dir>/<level>/CommunitySummary_<cid>.json`` — ``level`` is the
    dendrogram index (0 = finest) at which ``graph_map`` resolved the scope.
    The flat top-level files stay reserved for the compile pass's coarsest
    communities, so the two writers can never collide on a path.
    """
    return _cache_path(cache_dir / str(int(level)), cid)


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


def prune_stale_summary_caches(cache_dir: Path, live_cids: Iterable[str]) -> List[str]:
    """Delete summary-cache files whose cid is live at NO hierarchy level.

    ``live_cids`` is the manifest of community ids across ALL dendrogram
    levels of the freshly-written hierarchy sidecar (Descent §9.5): a cache
    file is stale only when its community no longer exists at any level —
    keying on the coarsest level alone would delete valid fine-level caches.
    Live-but-unvisited files are deliberately KEPT (they cost nothing and
    save a future LLM call). Only files matching the
    ``CommunitySummary_*.json`` cache naming scheme are considered — both in
    the flat compile-pass layout and in the numeric ``<level>/`` subdirs the
    lazy path writes (:func:`level_cache_path`) — so tmp files and foreign
    artifacts are never touched. Returns the deleted paths relative to
    ``cache_dir`` in sorted order.

    That prefix restriction is load-bearing rather than tidy. Charter domain
    briefs share these ``<level>/`` directories under a ``CharterDomain_``
    prefix (``tesserae.charter._BRIEF_CID_PREFIX``) precisely because they are
    keyed on a slug, and a slug is never a community id — so widening this
    glob to ``*.json`` would delete every brief on every compile, which is the
    per-ingest cache wipe a stable key exists to end.
    """
    if not cache_dir.is_dir():
        return []
    live_names = {_cache_path(cache_dir, cid).name for cid in live_cids}
    candidates = list(cache_dir.glob("CommunitySummary_*.json")) + [
        path
        for path in cache_dir.glob("*/CommunitySummary_*.json")
        if path.parent.name.isdigit()
    ]
    deleted: List[str] = []
    for path in sorted(candidates):
        if path.name in live_names:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        deleted.append(str(path.relative_to(cache_dir)))
    return sorted(deleted)


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


def _cites_child_communities(
    summary: Tuple[str, str, List[str]], child_cids: Sequence[str]
) -> bool:
    """Deterministic citation lint for summaries-of-summaries (§5.2).

    Mirrors the ``agent_distill`` faithfulness-lint pattern: pure string
    checks, no LLM. Prose (title + description) must cite at least one child
    community id verbatim, so an upper-level summary is always anchored to a
    child a reader can descend into — the summary-of-summary drift hole
    never opens. Vacuously true when the scope has no community children
    (finest level: members are leaf nodes, not summaries).
    """
    if not child_cids:
        return True
    title, description, _tags = summary
    prose = f"{title}\n{description}"
    return any(child_cid in prose for child_cid in child_cids)


def _forget_rejected(json_client: object, *, system: str, user: str) -> None:
    """Drop the cached answer this module just refused, so a retry re-asks.

    Without this the rejection is permanent rather than transient. The cache is
    addressed by the assembled prompt, and the prompt for a given community is
    a pure function of its members — so a rejected answer is served back to
    every later attempt at the same community, at no LLM cost and with the same
    verdict, forever. The citation lint makes that population predictable
    rather than rare: it can only reject a community that HAS children, which
    is exactly the routers a charter's upper tiers are made of.

    Mirrors ``ResearchGraphExtractor``'s drop (``llm_extractor.py``): duck-typed
    because a client with no cache (Anthropic SDK, test fakes) has no such
    method, and swallowing because a failed drop must not replace the caller's
    own ``None`` return with an unrelated exception. Worst case the rejected
    answer survives and the community re-fails identically — the behaviour this
    function exists to end, not a new one.
    """
    forget = getattr(json_client, "forget_cached_answer", None)
    if not callable(forget):
        return
    try:
        forget(
            "community-summary-v1",
            schema_name="community_summary",
            system=system,
            user=user,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("community_summaries: cache drop failed: %s", exc)


def summarize_community(
    prompt_members: Sequence[ResearchNode],
    *,
    cid: str,
    member_ids: Sequence[str],
    cache_path: Path,
    json_client: Optional[object],
    child_cids: Sequence[str] = (),
) -> Optional[Tuple[str, str, List[str]]]:
    """Summarize-and-cache ONE community; the shared single-community path.

    Cache read → digest invalidation → LLM call → validation → atomic cache
    write, exactly as :func:`compile_community_summaries` has always done per
    cluster (the compile pass now delegates here; the ``graph_map`` lazy path
    reuses it via :func:`materialize_community_summary`). Returns the
    validated ``(title, description, tags)`` or ``None`` when the community
    cannot be summarized (no client and no cache, LLM failure, invalid or
    citation-rejected response). ``child_cids``, when non-empty, engages the
    §5.2 citation discipline: the prompt lists the child community ids and
    :func:`_cites_child_communities` rejects prose citing none of them —
    rejected output is NOT cached, so a later attempt may still produce an
    llm-quality summary.
    """
    cached = _read_cache(cache_path)
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
            return None
        # Hoisted so the rejection paths below can hand `forget_cached_answer`
        # the SAME pair this call sent. The cache entry is addressed by the
        # assembled prompt, so a drop that rebuilt either string differently
        # would unlink nothing and report success.
        system_prompt = (
            _SYSTEM_PROMPT + _CITATION_SYSTEM_SUFFIX if child_cids else _SYSTEM_PROMPT
        )
        user_prompt = _format_user_prompt(prompt_members, child_cids)
        try:
            resp = json_client.complete_json(  # type: ignore[attr-defined]
                system=system_prompt,
                user=user_prompt,
                schema_name="community_summary",
                # Namespace label only. It used to append the prompt-member
                # COUNT, which read like content-keying and was not: every
                # community with the same number of members addressed ONE cache
                # entry and was served whichever community got there first. The
                # client now hashes the assembled prompt itself, so the count
                # bought nothing but the appearance of safety — dropped.
                cache_key="community-summary-v1",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("community_summaries: LLM failed for %s: %s", cid, exc)
            return None
        summary = _validate_summary(resp)
        if summary is None:
            logger.warning("community_summaries: invalid LLM response for %s", cid)
            _forget_rejected(json_client, system=system_prompt, user=user_prompt)
            return None
        if not _cites_child_communities(summary, child_cids):
            logger.warning(
                "community_summaries: summary for %s cites none of its child "
                "community ids; falling back to structural (not cached)",
                cid,
            )
            _forget_rejected(json_client, system=system_prompt, user=user_prompt)
            return None
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
    return summary


# ---------------------------------------------------------------------------
# Lazy materialization (§5.2, PR6)
# ---------------------------------------------------------------------------


def read_warm_summary(
    cache_dir: Path,
    level: int,
    cid: str,
    members: Sequence[ResearchNode],
    *,
    max_members_in_prompt: int = 25,
) -> Optional[Tuple[str, str, List[str]]]:
    """Warm-cache read for card building: validated summary or ``None``.

    Strict — a digest mismatch is a miss, never a stale serve: cards must
    not present drifted prose as ``quality="llm"``; the next scope visit
    re-summarizes (or, without a client, the card stays structural).
    """
    cached = _read_cache(level_cache_path(cache_dir, level, cid))
    if not isinstance(cached, dict):
        return None
    payload = cached.get("summary")
    summary = _validate_summary(payload) if payload else None
    if summary is None:
        return None
    prompt_members = list(members)[: max(1, int(max_members_in_prompt))]
    if cached.get("members_digest") != _members_digest(prompt_members):
        return None
    return summary


def materialize_community_summary(
    members: Sequence[ResearchNode],
    *,
    cid: str,
    member_ids: Sequence[str],
    level: int,
    cache_dir: Path,
    json_client: Optional[object],
    child_cids: Sequence[str] = (),
    max_members_in_prompt: int = 25,
) -> Optional[Tuple[str, str, List[str]]]:
    """Lazily summarize one ``graph_map`` scope (§5.2). NEVER raises.

    Exactly one ``complete_json`` call on a cold cache (via
    :func:`summarize_community` — same prompt family, envelope, digest
    invalidation and atomic write as the compile pass), cached under the
    level-scoped layout (:func:`level_cache_path`). Any failure — no
    client, LLM error, invalid or citation-rejected response, cache IO —
    returns ``None`` so the caller keeps its deterministic structural card;
    the map is never blocked by summarization.
    """
    try:
        prompt_members = list(members)[: max(1, int(max_members_in_prompt))]
        if not prompt_members:
            return None
        return summarize_community(
            prompt_members,
            cid=cid,
            member_ids=member_ids,
            cache_path=level_cache_path(cache_dir, level, cid),
            json_client=json_client,
            child_cids=child_cids,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "community_summaries: lazy materialization failed for %s: %s", cid, exc
        )
        return None


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
        members = [by_id[m] for m in member_ids if m in by_id]
        if not members:
            continue
        prompt_members = members[: max(1, int(max_members_in_prompt))]
        # Shared single-community path (PR6 refactor): cache read, digest
        # invalidation, LLM call, validation and atomic write all live in
        # summarize_community — behaviourally identical to the inline code
        # this replaced (coarsest-level flat cache layout, no child_cids, so
        # no citation discipline and byte-identical prompts/envelopes).
        summary = summarize_community(
            prompt_members,
            cid=cid,
            member_ids=member_ids,
            cache_path=_cache_path(cache_dir, cid),
            json_client=json_client,
        )
        if summary is None:
            continue
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

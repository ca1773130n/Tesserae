"""Agent-scoped graph views — the manager/worker resolution layer (spec §6.1/§8.1).

Resolves an ``agent=`` request onto one of three read-time views:

- **worker key** → L0 ∪ that agent's own L1 (merged by node id, distillate
  wins) with the *absorption overlay*: in-memory ``supersedes`` edges from each
  distillate to the raw members it absorbed, derived at load time from the live
  artifact's ``absorbed_refs``. Existing readers suppress absorbed raw via the
  unchanged :func:`tesserae.graph_filters.superseded_ids` path;
  ``include_superseded`` / ``drill_down`` still reach it. Nothing here is ever
  written to any graph file — regenerating the artifact regenerates the
  overlay, so ghost suppressions are structurally impossible.
- **manager key** → :func:`tesserae.federation.federate_graphs` over the
  children's L1 artifacts ∪ the manager's own L1 (aliases = agent keys). Raw
  L0 findings are NOT in this view — the manager sees distilled knowledge only.
- **``agent='org'``** (builtin pseudo-key, zero registry config) → federation
  of every L1 artifact present on disk — the team overview.

Resolution is fail-loud (spec §3.2): a child without a distilled artifact is
an explicit error naming the remedy command, never a silently thinner view.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .agent_distill import (
    DistillStateStore,
    _node_content_hash,
    _state_db_path,
    agent_artifact_path,
)
from .agent_identity import AgentRegistry
from .federation import federate_graphs
from .project import load_graph_file
from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNodeType,
)

AGENT_ORG_KEY = "org"

# Sidecar ledger scope for drill-down audit entries (§6.4). A named constant
# rather than a literal so the MCP tool and any CLI/library call site stay in
# lockstep (DistillStateStore has no SCOPE_* constant for this ledger).
DRILL_DOWN_AUDIT_SCOPE = "drill_down_audit"

__all__ = [
    "AGENT_ORG_KEY",
    "DRILL_DOWN_AUDIT_SCOPE",
    "AgentViewError",
    "drill_down",
    "resolve_agent_view",
]


class AgentViewError(ValueError):
    """A fail-loud agent-view resolution error (unknown key, missing L1)."""


def _known_agent_keys(graph: ResearchGraph, registry: AgentRegistry) -> List[str]:
    """All agent keys this project knows: L0 Agent nodes ∪ registry entries.

    L0 is the source of truth for *observed* agents (Phase 1 mints an Agent
    node per key even with zero registry config); the registry adds declared
    managers that may never have run a session themselves.
    """
    keys = {
        str(node.metadata.get("agent_key") or "")
        for node in graph.nodes
        if node.type == ResearchNodeType.AGENT
    }
    keys.discard("")
    declared = registry.load().get("agents")
    if isinstance(declared, dict):
        keys.update(declared.keys())
    return sorted(keys)


def _artifact_graph(project_root: Path, agent_key: str) -> Optional[Tuple[Path, ResearchGraph]]:
    path = agent_artifact_path(project_root, agent_key)
    if not path.is_file():
        return None
    return path, load_graph_file(path)


def _distilled_through(graph: ResearchGraph) -> str:
    """The artifact's corpus clock: max ``distilled_through`` over its nodes."""
    stamps = [
        str(node.metadata.get("distilled_through") or "")
        for node in graph.nodes
        if node.metadata.get("distilled_through")
    ]
    return max(stamps) if stamps else ""


def _member_info(agent_key: str, path: Path, graph: ResearchGraph) -> Dict[str, object]:
    return {
        "agent_key": agent_key,
        "artifact_path": str(path),
        "distilled_through": _distilled_through(graph),
        "nodes": len(graph.nodes),
    }


def _worker_view(l0: ResearchGraph, l1: ResearchGraph) -> ResearchGraph:
    """L0 ∪ own L1, distillate-preferred, with the absorption overlay."""
    nodes = {node.id: node for node in l0.nodes}
    # L1 wins id collisions (Agent/ExpertiseProfile share seeds with L0 by
    # design — the distilled copy carries the richer, fresher metadata).
    for node in l1.nodes:
        nodes[node.id] = node
    edge_keys = {(e.source, e.type, e.target) for e in l0.edges}
    edges = list(l0.edges)
    for edge in l1.edges:
        key = (edge.source, edge.type, edge.target)
        if key not in edge_keys:
            edge_keys.add(key)
            edges.append(edge)
    # Absorption overlay: distillate supersedes each absorbed raw member.
    # Load-time only — these edges exist in this in-memory view and nowhere
    # else. Sources are distillates present in the view by construction, so
    # every suppression source resolves to a live node (§6.1 invariant).
    for node in sorted(l1.nodes, key=lambda n: n.id):
        for ref in node.metadata.get("absorbed_refs") or []:
            target = str(ref.get("node_id") or "") if isinstance(ref, dict) else ""
            if not target or target not in nodes:
                continue
            key = (node.id, "supersedes", target)
            if key not in edge_keys:
                edge_keys.add(key)
                edges.append(ResearchEdge(source=node.id, target=target, type="supersedes"))
    return ResearchGraph(nodes=sorted(nodes.values(), key=lambda n: n.id), edges=edges)


def _missing_artifact_error(keys: List[str], *, owner: str) -> AgentViewError:
    remedy = " ".join(f"tesserae distill --agent {key};" for key in keys).rstrip(";")
    single = len(keys) == 1
    if owner:
        noun = "child" if single else "children"
        scope = f" of {owner}:"
    else:
        noun = "agent" if single else "agents"
        scope = ""
    verb = "has" if single else "have"
    return AgentViewError(
        f"{noun}{scope} {', '.join(keys)} {verb} no distilled artifact; run: {remedy}"
    )


# Small LRU keyed on (root, agent) with an input-signature guard: the view
# rebuilds iff L0 bytes, the registry file, or any involved artifact changed.
# Mirrors federation._FED_GRAPH_CACHE (write-if-changed keeps signatures calm).
_VIEW_CACHE: "OrderedDict[Tuple[str, str], Tuple[tuple, ResearchGraph, dict]]" = OrderedDict()
_VIEW_CACHE_MAX = 8


def _path_signature(path: Path) -> Optional[Tuple[int, int]]:
    try:
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None


def resolve_agent_view(
    project_root: Path | str,
    agent: str,
    l0: ResearchGraph,
    *,
    l0_path: Optional[Path] = None,
    bridges: bool = False,
    bridge_backend=None,
    bridge_cache_dir: Optional[Path] = None,
) -> Tuple[ResearchGraph, Dict[str, object]]:
    """Resolve ``agent`` onto its read-time view over ``l0``.

    Returns ``(graph, info)`` where ``info`` carries ``mode`` (worker /
    manager / org), the resolved ``agent`` key, and per-member artifact
    provenance (path, ``distilled_through`` staleness watermark, node count)
    for ``federation_explain``-style surfacing. Raises :class:`AgentViewError`
    on unknown keys or missing artifacts — never a silently degraded view.

    ``bridges=True`` (opt-in, spec §8.3-step-5/§12 Phase 5) adds embedding-backed
    ``shares_concept_with`` edges across a manager/org federation so RELATED (not
    identical) distillates from different reports get linked — the existing
    :func:`tesserae.federation.add_semantic_links` machinery, with agent keys as
    the federation aliases (so two reports' notes count as cross-"project" and get
    bridged; two notes from the same report never do). Bridges are **edges only**
    and never fuse nodes, so the flag never changes node counts. It is a no-op on
    a worker view (no federation there). Because the resolved view is in-memory
    only and never serialized, the embedding-backend nondeterminism of bridges
    (:func:`add_semantic_links` is byte-stable only given a fixed model) never
    reaches a committed artifact — the default ``bridges=False`` path stays
    byte-identical. When bridges are requested but the active backend is the hash
    stub / numpy is absent, ``info['bridges']`` surfaces ``semantic_skipped`` so
    the caller learns bridges were requested-but-skipped rather than silently
    dropped. ``bridge_backend`` / ``bridge_cache_dir`` inject the embedding
    backend and persisted link cache (both optional; a caller passes them for
    testing or to reuse a warm bridge cache).
    """
    root = Path(project_root)
    registry = AgentRegistry.for_project(root)
    requested = str(agent or "").strip()
    if not requested:
        raise AgentViewError("agent must be a non-empty key or 'org'")

    known = _known_agent_keys(l0, registry)
    canonical = requested if requested == AGENT_ORG_KEY else registry.resolve_alias(requested)

    # The bridge signature MUST be part of the cache key: an ON view (with its
    # extra shares_concept_with edges) and the OFF view share (root, canonical),
    # so without this a bridged/unbridged result would bleed across calls. Backend
    # name + cache dir ride along too, so swapping the embedding model re-resolves
    # instead of serving links from another model.
    bridge_sig = (
        bool(bridges),
        getattr(bridge_backend, "name", type(bridge_backend).__name__)
        if (bridges and bridge_backend is not None) else "",
        str(bridge_cache_dir or "") if bridges else "",
    )
    cache_key = (str(root), canonical, bridge_sig)
    involved: List[Path] = [registry.path]
    if l0_path is not None:
        involved.append(l0_path)

    if canonical == AGENT_ORG_KEY:
        members = [k for k in known if agent_artifact_path(root, k).is_file()]
        if not members:
            raise _missing_artifact_error(known or ["<none observed>"], owner="")
        mode = "org"
        children = members
    else:
        if canonical not in known and canonical != "org:root":
            raise AgentViewError(
                f"Unknown agent: {requested}. Known agents: {', '.join(known) or '(none)'}. "
                "Use `tesserae agents list`."
            )
        children = [k for k in known if k != canonical and registry.effective_parent(k) == canonical]
        mode = "manager" if children or canonical == "org:root" else "worker"

    if mode == "worker":
        loaded = _artifact_graph(root, canonical)
        if loaded is None:
            raise _missing_artifact_error([canonical], owner="")
        path, l1 = loaded
        involved.append(path)
        signature = tuple(_path_signature(p) for p in involved)
        cached = _VIEW_CACHE.get(cache_key)
        if cached and cached[0] == signature:
            _VIEW_CACHE.move_to_end(cache_key)
            return cached[1], cached[2]
        view = _worker_view(l0, l1)
        info: Dict[str, object] = {
            "mode": "worker",
            "agent": canonical,
            "members": [_member_info(canonical, path, l1)],
        }
    else:
        missing = [k for k in children if not agent_artifact_path(root, k).is_file()]
        if missing:
            raise _missing_artifact_error(missing, owner=canonical if mode == "manager" else "")
        named: List[Tuple[str, ResearchGraph]] = []
        infos: List[Dict[str, object]] = []
        for key in children:
            loaded = _artifact_graph(root, key)
            if loaded is None:  # raced deletion — same fail-loud path
                raise _missing_artifact_error([key], owner=canonical)
            path, child = loaded
            involved.append(path)
            named.append((key, child))
            infos.append(_member_info(key, path, child))
        own = None if canonical == AGENT_ORG_KEY else _artifact_graph(root, canonical)
        if own is not None:
            path, own_l1 = own
            involved.append(path)
            named.append((canonical, own_l1))
            infos.append(_member_info(canonical, path, own_l1))
        signature = tuple(_path_signature(p) for p in involved)
        cached = _VIEW_CACHE.get(cache_key)
        if cached and cached[0] == signature:
            _VIEW_CACHE.move_to_end(cache_key)
            return cached[1], cached[2]
        if bridges:
            # Opt-in cross-agent bridges. Aliases are agent keys, so
            # add_semantic_links (cross-"project" only) links related notes from
            # DIFFERENT reports and never same-report ones. Edges only — node
            # count is identical to the unbridged federation.
            view, fed_info = federate_graphs(
                named, semantic=True, semantic_backend=bridge_backend,
                semantic_cache_dir=bridge_cache_dir,
            )
            bridge_info = {
                k: fed_info[k]
                for k in (
                    "semantic_added", "semantic_backend",
                    "semantic_skipped", "semantic_cached", "semantic_capped_at",
                )
                if k in fed_info
            }
            info = {"mode": mode, "agent": canonical, "members": infos, "bridges": bridge_info}
        else:
            view, _fed_info = federate_graphs(named)
            info = {"mode": mode, "agent": canonical, "members": infos}

    _VIEW_CACHE[cache_key] = (signature, view, info)
    _VIEW_CACHE.move_to_end(cache_key)
    while len(_VIEW_CACHE) > _VIEW_CACHE_MAX:
        _VIEW_CACHE.popitem(last=False)
    return view, info


def drill_down(
    project_root: Path | str,
    l0: ResearchGraph,
    node_id: str,
    *,
    content_hash: str = "",
    agent: str = "",
    l1_loader=None,
) -> Dict[str, object]:
    """Resolve a distillate ``member_ref`` against the raw L0 graph (§6.4).

    Drill-down is the explicit escalation past distilled visibility, so ``l0``
    is the UNSCOPED base graph — never an agent-filtered view. Statuses:

    - ``gone`` — ``node_id`` is absent from L0.
    - ``absorbed`` — the owning ``agent``'s live L1 artifact lists it in a
      distillate's ``absorbed_refs``.
    - ``changed`` — ``content_hash`` was supplied and no longer matches.
    - ``alive`` — present and unchanged.

    Every call is recorded in the ``drill_down_audit`` sidecar ledger
    (``.tesserae/sqlite.db``). The audit write is best-effort: a locked or
    unwritable sidecar must not break the read, so failures are logged loudly
    and surfaced via ``result['audited'] = False`` rather than swallowed.

    ``l1_loader`` (default :func:`load_graph_file`) loads the agent's L1
    artifact; the MCP server injects its mtime-cached loader so behavior stays
    identical to the in-process cache. The returned dict shape is stable:
    ``{node_id, status, agent, [absorbed_by], [node], audited}``.
    """
    root = Path(project_root)
    node_id = str(node_id or "")
    if not node_id:
        raise ValueError("drill_down requires 'node_id' (a member_refs[].node_id).")
    want_hash = str(content_hash or "")
    agent = str(agent or "")
    load_l1 = l1_loader or load_graph_file

    node = next((n for n in l0.nodes if n.id == node_id), None)
    absorbed_by = ""
    if node is not None and agent:
        artifact = agent_artifact_path(root, agent)
        if artifact.is_file():
            l1 = load_l1(artifact)
            for distillate in sorted(l1.nodes, key=lambda n: n.id):
                refs = distillate.metadata.get("absorbed_refs") or []
                if any(isinstance(r, dict) and r.get("node_id") == node_id for r in refs):
                    absorbed_by = distillate.id
                    break

    if node is None:
        status = "gone"
    elif absorbed_by:
        status = "absorbed"
    elif want_hash and want_hash != _node_content_hash(node):
        status = "changed"
    else:
        status = "alive"

    result: Dict[str, object] = {"node_id": node_id, "status": status, "agent": agent or None}
    if absorbed_by:
        result["absorbed_by"] = absorbed_by
    if node is not None:
        result["node"] = {
            "id": node.id,
            "name": node.name,
            "type": node.type.value,
            "description": node.description,
            "content_hash": _node_content_hash(node),
            "source_path": node.source_path,
        }
    # Audit log — every drill-down is recorded in the sidecar (§6.4). The only
    # wall-clock here writes to the sidecar sqlite, never to a graph artifact,
    # so it does not threaten artifact byte-idempotence. Best-effort like
    # bump_access: failures are logged loudly, not swallowed silently.
    from datetime import datetime, timezone

    try:
        import json as _json

        entry = _json.dumps(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "content_hash": want_hash,
                "node_id": node_id,
                "status": status,
            },
            sort_keys=True,
        )
        DistillStateStore(_state_db_path(root)).append(DRILL_DOWN_AUDIT_SCOPE, agent, entry)
        result["audited"] = True
    except Exception as exc:  # noqa: BLE001 — read must survive sidecar failure
        import logging

        logging.getLogger(__name__).warning("drill_down: audit log write failed (%s)", exc)
        result["audited"] = False
    return result

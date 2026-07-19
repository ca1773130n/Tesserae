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

from .agent_distill import agent_artifact_path
from .agent_identity import AgentRegistry
from .federation import federate_graphs
from .project import load_graph_file
from .research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNodeType,
)

AGENT_ORG_KEY = "org"

__all__ = ["AGENT_ORG_KEY", "AgentViewError", "resolve_agent_view"]


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
    noun = "children" if owner else "agents"
    scope = f" of {owner}" if owner else ""
    return AgentViewError(
        f"{noun}{scope} {', '.join(keys)} have no distilled artifact; run: {remedy}"
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
) -> Tuple[ResearchGraph, Dict[str, object]]:
    """Resolve ``agent`` onto its read-time view over ``l0``.

    Returns ``(graph, info)`` where ``info`` carries ``mode`` (worker /
    manager / org), the resolved ``agent`` key, and per-member artifact
    provenance (path, ``distilled_through`` staleness watermark, node count)
    for ``federation_explain``-style surfacing. Raises :class:`AgentViewError`
    on unknown keys or missing artifacts — never a silently degraded view.
    """
    root = Path(project_root)
    registry = AgentRegistry.for_project(root)
    requested = str(agent or "").strip()
    if not requested:
        raise AgentViewError("agent must be a non-empty key or 'org'")

    known = _known_agent_keys(l0, registry)
    canonical = requested if requested == AGENT_ORG_KEY else registry.resolve_alias(requested)

    cache_key = (str(root), canonical)
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
        view, _fed_info = federate_graphs(named)
        info = {"mode": mode, "agent": canonical, "members": infos}

    _VIEW_CACHE[cache_key] = (signature, view, info)
    _VIEW_CACHE.move_to_end(cache_key)
    while len(_VIEW_CACHE) > _VIEW_CACHE_MAX:
        _VIEW_CACHE.popitem(last=False)
    return view, info

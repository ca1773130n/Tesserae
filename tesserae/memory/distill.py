"""Cross-session ``Runbook`` / ``Gotcha`` distillation pass (AgentRunbook layer 2).

Ports the *core idea* of LongMemEval-V2's AgentRunbook procedure/hint notes
to Tesserae's typed graph: above the raw session-finding nodes, distill
**multi-granularity memory** — clusters of related findings collapse into a
single higher-order node:

- *procedure-ish* clusters (how / steps / decisions) -> a :class:`Runbook`.
- *pitfall-ish* clusters (fail / error / gotcha / avoid / wrong / broke /
  pitfall / regression) -> a :class:`Gotcha`.

Pass shape (opt-in, mirrors ``community_summaries`` + ``supersede``):

1. Cluster the session-finding nodes (:data:`SESSION_FINDING_TYPES`) with the
   deterministic ``reinforce``-style :class:`_UnionFind` keyed on node ids:
   union on ``supersede.jaccard`` name-similarity AND on existing
   ``supersedes`` edges (same two signals ``reinforce`` uses).
2. For each cluster with ``>= min_cluster_size`` members, classify it as a
   ``Gotcha`` (pitfall keywords present) or a ``Runbook`` (otherwise). Only
   layers listed in ``layers`` are minted.
3. Mint ONE node of that kind per cluster. The id is a content hash of the
   SORTED member node-ids (stable, no wall-clock / RNG). Name + description
   are deterministically composed from members on the fallback path; an
   :class:`LLMJsonClient` only *enriches* the title/body (content-keyed disk
   cache under ``cache_dir``). ``first_seen_at`` = the earliest member's
   timestamp — never ``datetime.now()``.
4. Emit a ``derived_from`` edge from the new node to each member finding.

Strictly additive + degrade-never-raise: all existing nodes/edges are
preserved, new ones are appended in-place, and any LLM failure falls back to
the deterministic body. Byte-idempotent — a rerun over the same corpus mints
byte-identical node ids / bodies / edges.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from ..llm_json import LLMJsonClient
from ..research_graph import (
    SESSION_FINDING_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from .supersede import jaccard

logger = logging.getLogger(__name__)

DERIVED_FROM_EDGE = "derived_from"
"""Edge minted from a distilled node to each member finding it summarizes."""

# Names whose Jaccard similarity exceeds this cluster together — matches the
# ``reinforce`` near-dup threshold so the two passes agree on cluster shape.
_NEAR_DUP_THRESHOLD = 0.55

# A cluster is a Gotcha when any member name/description contains one of these.
_PITFALL_KEYWORDS: Tuple[str, ...] = (
    "fail",
    "error",
    "gotcha",
    "avoid",
    "wrong",
    "broke",
    "pitfall",
    "regression",
)

_FALSY = {"0", "false", "no", "off"}


# ---------------------------------------------------------------------------
# Enable / disable resolution + test-client injection
# ---------------------------------------------------------------------------


_TEST_CLIENT: Optional[LLMJsonClient] = None


def set_distillation_test_client(client: Optional[LLMJsonClient]) -> None:
    """Inject a fake JSON client for tests (mirrors community_summaries)."""
    global _TEST_CLIENT
    _TEST_CLIENT = client


def _get_distillation_test_client() -> Optional[LLMJsonClient]:
    """Return the injected test client, if any."""
    return _TEST_CLIENT


def distillation_enabled(
    cfg: Optional[dict] = None,
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """Decide whether to run the Runbook/Gotcha distillation pass.

    Opt-in: enabled when ``cfg["distillation"]["enabled"]`` is truthy OR the
    env var ``TESSERAE_RUNBOOK_DISTILLATION`` is truthy. An explicit config
    ``false`` (when no truthy env override is present) disables; an env value
    in ``{"0", "false", "no", "off"}`` (case-insensitive, trimmed) disables.

    Resolution mirrors ``community_summaries.is_enabled_via_env``: env takes
    precedence when it is an explicit truthy/falsy spelling, then the config
    flag, else disabled (the default — this is an additive, opt-in pass).
    """
    env = env if env is not None else os.environ
    raw = (env.get("TESSERAE_RUNBOOK_DISTILLATION") or "").strip().lower()
    if raw:
        if raw in _FALSY:
            return False
        return True

    section = (cfg or {}).get("distillation")
    if isinstance(section, Mapping):
        flag = section.get("enabled")
        if isinstance(flag, str):
            return flag.strip().lower() not in _FALSY and bool(flag.strip())
        return bool(flag)
    return False


# ---------------------------------------------------------------------------
# Clustering (reinforce-style deterministic union-find, id-ordered)
# ---------------------------------------------------------------------------


class _UnionFind:
    """Tiny deterministic union-find keyed on string ids (reinforce pattern)."""

    def __init__(self) -> None:
        self._parent: Dict[str, str] = {}

    def add(self, node_id: str) -> None:
        self._parent.setdefault(node_id, node_id)

    def find(self, node_id: str) -> str:
        root = node_id
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[node_id] != root:
            self._parent[node_id], node_id = root, self._parent[node_id]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        lo, hi = (ra, rb) if ra < rb else (rb, ra)
        self._parent[hi] = lo


def _kind(node: ResearchNode) -> str:
    return node.type.value if hasattr(node.type, "value") else str(node.type)


def _cluster_findings(graph: ResearchGraph) -> List[List[ResearchNode]]:
    """Cluster session-finding nodes; returns id-sorted clusters, id-sorted.

    Two findings join the same cluster when EITHER a ``supersedes`` edge links
    them OR their names exceed the Jaccard near-dup threshold — the same two
    signals ``reinforce`` uses. The pairwise scan is ordered by node id so the
    union-find is fully deterministic.
    """
    finding_values = {t.value for t in SESSION_FINDING_TYPES}
    findings: List[ResearchNode] = sorted(
        (n for n in graph.nodes if _kind(n) in finding_values),
        key=lambda n: n.id,
    )
    if not findings:
        return []

    uf = _UnionFind()
    for node in findings:
        uf.add(node.id)
    finding_ids: Set[str] = {n.id for n in findings}

    # 1. supersedes chains -> same cluster.
    for edge in graph.edges:
        if edge.type != "supersedes":
            continue
        if edge.source in finding_ids and edge.target in finding_ids:
            uf.union(edge.source, edge.target)

    # 2. Jaccard near-dup on names (deterministic id-ordered scan).
    for i, a in enumerate(findings):
        for b in findings[i + 1 :]:
            if uf.find(a.id) == uf.find(b.id):
                continue
            if jaccard(a.name, b.name) > _NEAR_DUP_THRESHOLD:
                uf.union(a.id, b.id)

    clusters: Dict[str, List[ResearchNode]] = {}
    for node in findings:
        clusters.setdefault(uf.find(node.id), []).append(node)
    # Each cluster id-sorted; cluster list ordered by canonical root id.
    return [
        sorted(members, key=lambda n: n.id)
        for _root, members in sorted(clusters.items(), key=lambda kv: kv[0])
    ]


# ---------------------------------------------------------------------------
# Classification + deterministic body composition
# ---------------------------------------------------------------------------


def _is_pitfall(members: Sequence[ResearchNode]) -> bool:
    """True when any member name/description hints at a pitfall/gotcha."""
    for node in members:
        blob = f"{node.name} {node.description or ''}".lower()
        if any(kw in blob for kw in _PITFALL_KEYWORDS):
            return True
    return False


def _cluster_node_type(members: Sequence[ResearchNode]) -> ResearchNodeType:
    return (
        ResearchNodeType.GOTCHA
        if _is_pitfall(members)
        else ResearchNodeType.RUNBOOK
    )


def _distilled_id(node_type: ResearchNodeType, member_ids: Sequence[str]) -> str:
    """Stable id = ``<Kind>:<sha of sorted member ids>``. No wall-clock / RNG."""
    h = hashlib.sha256("\n".join(sorted(member_ids)).encode("utf-8")).hexdigest()
    return f"{node_type.value}:{h[:16]}"


def _dominant_member(members: Sequence[ResearchNode]) -> ResearchNode:
    """The cluster's representative finding: longest name, id as tiebreak.

    Deterministic — keys ONLY on immutable content (name length, then id), so
    the same cluster always picks the same representative across reruns.
    """
    return max(members, key=lambda n: (len(n.name or ""), n.id))


def _deterministic_title_body(
    node_type: ResearchNodeType, members: Sequence[ResearchNode]
) -> Tuple[str, str]:
    """Fallback title + bulleted body composed purely from member content."""
    dominant = _dominant_member(members)
    label = "Gotcha" if node_type is ResearchNodeType.GOTCHA else "Runbook"
    title = f"{label}: {dominant.name}".strip()
    bullets = "\n".join(f"- {m.name}" for m in members)
    body = f"Distilled from {len(members)} session findings:\n{bullets}"
    return title, body


def _earliest_first_seen(members: Sequence[ResearchNode]) -> Optional[str]:
    """Earliest member ``first_seen_at`` timestamp; ``None`` when none carry one.

    Lexicographic min over the ISO-8601 timestamp strings already stamped onto
    findings by ``session_graph`` — content-derived, never ``datetime.now()``.
    """
    stamps = [
        str((m.metadata or {}).get("first_seen_at"))
        for m in members
        if (m.metadata or {}).get("first_seen_at")
    ]
    return min(stamps) if stamps else None


# ---------------------------------------------------------------------------
# LLM enrichment (content-keyed disk cache; degrade-never-raise)
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = (
    "You distill a cluster of related coding/agent session findings into a "
    "single higher-order memory note. For a Runbook, write a reusable "
    "procedure; for a Gotcha, write the pitfall and how to avoid it. Return "
    'JSON with exactly two keys: "title" (<= 8 tokens, headline style) and '
    '"body" (1-4 short sentences). Do not invent facts outside the findings.'
)


def _format_user_prompt(
    node_type: ResearchNodeType, members: Sequence[ResearchNode]
) -> str:
    label = node_type.value
    lines = [f"Distil a {label} from these {len(members)} session findings:"]
    for m in members:
        desc = (m.description or "").strip().splitlines()
        first = desc[0][:160] if desc else ""
        lines.append(f"- {m.name}{(': ' + first) if first else ''}")
    lines.append("")
    lines.append('Respond with: {"title": "...", "body": "..."}')
    return "\n".join(lines)


def _cache_path(cache_dir: Path, distilled_id: str) -> Path:
    return cache_dir / f"{distilled_id.replace(':', '_')}.json"


def _read_cache(path: Path) -> Optional[Tuple[str, str]]:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _validate(payload.get("distilled") if isinstance(payload, dict) else None)


def _write_cache(path: Path, distilled_id: str, title: str, body: str) -> None:
    """Atomic write with PID+random suffix (matches community_summaries)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    payload = {
        "schema_version": 1,
        "distilled_id": distilled_id,
        "distilled": {"title": title, "body": body},
    }
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


def _validate(payload: object) -> Optional[Tuple[str, str]]:
    if not isinstance(payload, dict):
        return None
    title = str(payload.get("title") or "").strip()
    body = str(payload.get("body") or "").strip()
    if not title or not body:
        return None
    return title, body


def _ask_llm(
    client: LLMJsonClient,
    node_type: ResearchNodeType,
    members: Sequence[ResearchNode],
) -> Optional[Tuple[str, str]]:
    """Call the JSON client for a cleaner title/body; ``None`` on any failure."""
    try:
        resp = client.complete_json(
            system=_SYSTEM_PROMPT,
            user=_format_user_prompt(node_type, members),
            schema_name="distilled_memory",
            cache_key=f"distillation-v1::{node_type.value}",
            max_retries=1,
        )
    except Exception:  # noqa: BLE001 — degrade-never-raise
        logger.exception("distill: LLM call raised")
        return None
    return _validate(resp)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_distillation_pass(
    graph: ResearchGraph,
    *,
    json_client: Optional[LLMJsonClient] = None,
    cache_dir: Path,
    layers: Sequence[str] = ("runbook", "gotcha"),
    min_cluster_size: int = 2,
) -> ResearchGraph:
    """Mint ``Runbook`` / ``Gotcha`` nodes + ``derived_from`` edges in-place.

    Clusters the session-finding nodes (``supersede.jaccard`` near-dup +
    existing ``supersedes`` edges, via the ``reinforce`` union-find), classifies
    each cluster of ``>= min_cluster_size`` members as procedure-ish
    (``Runbook``) or pitfall-ish (``Gotcha``), and mints ONE node of that kind
    per qualifying cluster (only the layers named in ``layers``).

    Determinism: the minted node id is a content hash of the SORTED member
    ids; the fallback title/body is composed purely from member content;
    ``first_seen_at`` is the earliest member timestamp. There is no
    ``datetime.now()`` / RNG, so a rerun over an unchanged corpus produces
    byte-identical nodes and edges. A ``json_client`` only ENRICHES the
    title/body (content-keyed disk cache under ``cache_dir``); on ANY LLM
    failure the deterministic fallback is used. All existing nodes/edges are
    preserved; new ones are appended.
    """
    wanted: Set[ResearchNodeType] = set()
    layer_map = {
        "runbook": ResearchNodeType.RUNBOOK,
        "gotcha": ResearchNodeType.GOTCHA,
    }
    for name in layers:
        kind = layer_map.get(str(name).strip().lower())
        if kind is not None:
            wanted.add(kind)
    if not wanted:
        return graph

    clusters = _cluster_findings(graph)
    if not clusters:
        return graph

    existing_node_ids: Set[str] = {n.id for n in graph.nodes}
    existing_edges: Set[Tuple[str, str, str]] = {
        (e.source, e.type, e.target) for e in graph.edges
    }
    minted = 0

    for members in clusters:
        if len(members) < max(2, int(min_cluster_size)):
            continue
        node_type = _cluster_node_type(members)
        if node_type not in wanted:
            continue

        member_ids = [m.id for m in members]
        distilled_id = _distilled_id(node_type, member_ids)
        if distilled_id in existing_node_ids:
            continue

        title, body = _deterministic_title_body(node_type, members)
        if json_client is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = _cache_path(cache_dir, distilled_id)
            enriched = _read_cache(cache_path)
            if enriched is None:
                enriched = _ask_llm(json_client, node_type, members)
                if enriched is not None:
                    _write_cache(cache_path, distilled_id, enriched[0], enriched[1])
            if enriched is not None:
                title, body = enriched

        metadata: Dict[str, object] = {
            "member_ids": sorted(member_ids),
            "member_count": len(member_ids),
            "extractor": "memory.distill.run_distillation_pass",
        }
        first_seen = _earliest_first_seen(members)
        if first_seen:
            metadata["first_seen_at"] = first_seen

        graph.nodes.append(
            ResearchNode(
                id=distilled_id,
                name=title,
                type=node_type,
                description=body,
                aliases=[],
                metadata=metadata,
            )
        )
        existing_node_ids.add(distilled_id)

        for mid in member_ids:
            edge_key = (distilled_id, DERIVED_FROM_EDGE, mid)
            if edge_key in existing_edges:
                continue
            graph.edges.append(
                ResearchEdge(
                    source=distilled_id,
                    target=mid,
                    type=DERIVED_FROM_EDGE,
                    metadata={"distilled_id": distilled_id},
                )
            )
            existing_edges.add(edge_key)
        minted += 1

    if minted:
        logger.info("memory.distill: minted %d distilled-memory nodes", minted)
    return graph

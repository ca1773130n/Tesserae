"""Gated, region-scoped dream (consolidation) pass — ``tesserae dream``.

The engine daemon's sleep cycle (:meth:`tesserae.engine.daemon.Daemon._consolidate_once`)
runs its memory edits on every idle or ceiling tick with no gate, no preview
and no way back. This module is the same consolidation, made operable:

* **Planned before written.** :func:`run_dream` computes every op first. With
  ``dry_run=True`` that plan is the whole answer and nothing is written; the
  apply path recomputes the same plan and writes exactly the ops it promotes,
  so "dry-run ops == applied ops" holds by construction.
* **Region-scoped.** Ops are planned per Louvain community (the same
  :func:`~tesserae.community_summaries.detect_communities` the compile uses),
  over the graph with the edge types this pass writes removed, so a pass cannot
  re-partition the regions the next pass is scoped by. Nodes in no community
  form one ``region:unassigned``. Near-duplicates that live in two regions are
  never paired here — the compile's global supersede pass still sees them.
* **Gated.** Each region's ops become a candidate overlay that is promoted only
  if a held-out probe set shows no regression against the baseline (paired
  bootstrap over per-probe reciprocal rank in
  :func:`~tesserae.context_compiler.compile_context`, with the project's vector
  cache and BM25 index, on a LOCAL backend — see :func:`resolve_probe_backend`).
  The probe retriever sees ``supersedes`` edges (suppression) and association
  edges (PPR expansion); it does NOT see ``decay_score``, so a decay-only
  region always passes. The gate measures collateral damage to retrieval, not
  whether a supersede verdict is correct: a probe whose gold node was
  superseded accepts the superseder.
* **Cursor-scoped.** A per-session cursor in ``session_chunks.db`` records the
  last turn reviewed and a digest of that session's findings in ``graph.json``
  at the time; only regions holding findings from sessions with newer turns OR
  changed findings are in scope (the tailer stores turns before a compile turns
  them into findings, so turns alone would mark a session reviewed too early).
  With no chunk store (the daemon's tailer never ran) every region is in scope.
  Applying runs under ``compile.lock``; a dry run takes no lock.
* **Journaled and undoable.** Each applied pass journals its ops, its gate
  verdicts, the sessions it reviewed and every before-value it will overwrite
  under ``.tesserae/dream/journal/<YYYY-MM-DD>/`` BEFORE its first write. A pass
  that raises mid-apply is rolled back from that journal on the spot; one that
  crashed (journal left ``applying``) blocks further passes until
  :func:`undo_dream` replays it backwards. :func:`undo_dream` replays the live
  pass with the highest ``pass_index``; ``graph.json`` and ``node_memory`` come
  back byte-identical, or the undo refuses loudly.

Op kinds (all deterministic for a fixed graph, sidecars and backend):

* ``supersede`` — :func:`tesserae.memory.supersede.run_supersede_pass` on the
  region subgraph, with a client that only READS the supersede cache, so a
  recorded LLM verdict (including "distinct") is honoured and no model is
  called. Adds the ``supersedes`` edge to ``graph.json`` and sets
  ``node_memory.superseded`` on the target. **For supersede the gate and undo
  are advisory until the next compile**: the compile's own supersede pass is
  on by default, global and ungated, and re-derives the same deterministic
  verdict, so an edge the gate rejected, or one an undo removed, is minted by
  the next ``tesserae compile`` anyway (unless an LLM-backed compile has
  recorded "distinct" for the pair). What the gate decides for supersede is
  only whether the edge lands before that compile.
* ``associate`` — :func:`tesserae.memory.associate.discover_links` on the region
  subgraph with the existing overlay applied; persists into
  ``discovered_links.json``. Skipped honestly without a semantic backend.
* ``decay`` — re-scores ``node_memory.decay_score`` at the compile's own
  content-derived reference instant, folding in reads recorded since the last
  compile (the same computation as ``ProjectWiki._run_memory_passes``). Only
  rows that already exist are touched.
* ``distill`` — per worker agent past the memory-pressure floor, behind the
  existing ``TESSERAE_AGENT_DISTILL`` gate. Not region-scoped and not
  probe-visible (it writes agent artifacts, not the project graph). Undo
  restores the artifact bytes and the watermark row; the append-only forget
  ledger and the content-keyed distill cache are left as they are.

Not done here: no git commit of the journal (the roadmap's optional
``(dream)`` commit), no journal rotation, no LLM calls of its own.

Opt-in: the daemon only runs this pass in place of its ungated distill +
associate step when ``TESSERAE_DREAM=1`` or ``config.json``
``{"dream": {"enabled": true}}``. The CLI verb is an explicit request; the MCP
tool previews freely but applies only under the same opt-in.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import secrets
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .research_graph import ResearchEdge, ResearchGraph, ResearchNode

logger = logging.getLogger(__name__)

DREAM_ENV = "TESSERAE_DREAM"
DREAM_DIRNAME = "dream"
OP_KINDS: Tuple[str, ...] = ("supersede", "associate", "decay", "distill")
UNASSIGNED_REGION = "region:unassigned"
JOURNAL_VERSION = 1

_FALSY = frozenset({"0", "false", "no", "off", ""})
_SUPERSEDE = "supersedes"
_SHARES = "shares_concept_with"
#: Edge types this pass writes. Removed before regions are computed so a pass's
#: own writes cannot move the region boundaries the next pass is scoped by.
_DREAM_EDGE_TYPES = frozenset({_SUPERSEDE, _SHARES})
_DECAY_EPS = 1e-12


class DreamError(RuntimeError):
    """A dream pass or undo that cannot proceed safely. Never swallowed."""


def dream_enabled(cfg: Optional[Mapping[str, Any]] = None, env: Optional[Mapping[str, str]] = None) -> bool:
    """Opt-in gate (``TESSERAE_DREAM`` env, else ``cfg["dream"]["enabled"]``).

    Same resolution order as :func:`tesserae.agent_distill.agent_distill_enabled`:
    an explicit env spelling wins, then the config section, else off.
    """
    env = env if env is not None else os.environ
    raw = (env.get(DREAM_ENV) or "").strip().lower()
    if raw:
        return raw not in _FALSY
    section = (cfg or {}).get("dream")
    if isinstance(section, Mapping):
        flag = section.get("enabled")
        if isinstance(flag, str):
            return flag.strip().lower() not in _FALSY
        return bool(flag)
    return False


@dataclass
class DreamSettings:
    """Knobs for one pass. ``config.json`` ``"dream"`` keys override defaults."""

    ops: Tuple[str, ...] = OP_KINDS
    gate: bool = True
    #: Probes sampled from the graph (all of them when fewer exist).
    probes: int = 50
    #: Run the probe gate on every Nth applied pass; passes in between write
    #: nothing (they are deferred, not promoted ungated).
    probe_every: int = 1
    #: Reject a region when its mean reciprocal-rank change is below -max_drop,
    #: or when the bootstrap CI's upper bound is below zero.
    max_drop: float = 0.02
    bootstrap: int = 1000
    alpha: float = 0.05
    top_k: int = 20
    #: Regions evaluated per pass; the rest wait for the next pass and their
    #: sessions' cursors are not advanced.
    max_regions: int = 16
    all_regions: bool = False
    regions: Tuple[str, ...] = ()
    seed: int = 0

    @classmethod
    def from_config(cls, cfg: Optional[Mapping[str, Any]], **overrides: Any) -> "DreamSettings":
        section = (cfg or {}).get("dream")
        values: Dict[str, Any] = {}
        names = {f.name for f in fields(cls)}
        if isinstance(section, Mapping):
            for key, value in section.items():
                if key in names:
                    values[key] = value
        values.update({k: v for k, v in overrides.items() if v is not None})
        settings = cls(**values)
        settings.ops = tuple(settings.ops)
        settings.regions = tuple(settings.regions)
        unknown = sorted(set(settings.ops) - set(OP_KINDS))
        if unknown:
            raise DreamError(f"unknown dream op(s) {unknown}; valid: {list(OP_KINDS)}")
        if int(settings.probe_every) < 1:
            raise DreamError("probe_every must be >= 1")
        return settings


# --------------------------------------------------------------------------- paths / io


def dream_dir(project_root: Path | str) -> Path:
    return Path(project_root) / ".tesserae" / DREAM_DIRNAME


def _state_path(project_root: Path) -> Path:
    return dream_dir(project_root) / "state.json"


def _probes_path(project_root: Path) -> Path:
    return dream_dir(project_root) / "probes.jsonl"


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DreamError(f"{path} is unreadable ({exc}); fix or remove it by hand") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _load_state(project_root: Path) -> Dict[str, Any]:
    state = _read_json(_state_path(project_root), {})
    return {"passes": int(state.get("passes", 0)), "rejected": sorted(set(state.get("rejected", [])))}


def load_project_config(project_root: Path) -> Optional[dict]:
    path = project_root / ".tesserae" / "config.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("dream: %s unreadable (%s); using defaults", path, exc)
        return None
    return payload if isinstance(payload, dict) else None


def _sha256(path: Path) -> Optional[str]:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _fingerprint(op: Mapping[str, Any]) -> str:
    body = {k: v for k, v in op.items() if k != "region"}
    return hashlib.sha1(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- regions


def _session_key(value: str) -> str:
    """The transcript stem both stores agree on.

    The graph names a Claude session ``claude-code:<sid>:<stem>``; the chunk
    store names it ``<account>:<stem>``. The last ``:`` segment is the shared
    part. Codex ids do not always share it — those sessions are reviewed but
    scope no region (reported as ``unmatched_sessions``).
    """
    return str(value).rsplit(":", 1)[-1]


def compute_regions(graph: ResearchGraph) -> List[Tuple[str, List[str]]]:
    """``[(region_id, sorted member ids)]``, plus ``region:unassigned`` for the rest."""
    from .community_summaries import detect_communities

    base = ResearchGraph(
        nodes=list(graph.nodes),
        edges=[e for e in graph.edges if e.type not in _DREAM_EDGE_TYPES],
    )
    regions: List[Tuple[str, List[str]]] = []
    assigned: Set[str] = set()
    for members in detect_communities(base):
        digest = hashlib.sha256("\n".join(sorted(members)).encode("utf-8")).hexdigest()[:12]
        regions.append((f"region:{digest}", sorted(members)))
        assigned.update(members)
    rest = sorted(n.id for n in graph.nodes if n.id not in assigned)
    if rest:
        regions.append((UNASSIGNED_REGION, rest))
    return sorted(regions)


def _subgraph(graph: ResearchGraph, members: Set[str], edge_types: Optional[Set[str]] = None) -> ResearchGraph:
    return ResearchGraph(
        nodes=[n for n in graph.nodes if n.id in members],
        edges=[
            e
            for e in graph.edges
            if e.source in members and e.target in members and (edge_types is None or e.type in edge_types)
        ],
    )


# --------------------------------------------------------------------------- op planners


class _CacheOnlyClient:
    """An ``LLMJsonClient`` that never answers.

    Handing it to ``run_supersede_pass`` makes the pass read its on-disk cache
    (so a recorded LLM verdict, "distinct" included, still wins) while every
    uncached pair falls to the deterministic verdict — no model call, no cache
    write. Without it the pass would skip the cache and could mint an edge an
    LLM-backed compile had explicitly rejected.
    """

    def complete_json(self, **_kwargs: Any) -> None:
        return None


def _edge_dict(edge: ResearchEdge) -> Dict[str, Any]:
    return {
        "source": edge.source,
        "target": edge.target,
        "type": edge.type,
        "evidence": edge.evidence,
        "metadata": dict(edge.metadata or {}),
    }


def _plan_supersede(project_root: Path, graph: ResearchGraph, region: str, members: Set[str]) -> List[dict]:
    from .memory.supersede import run_supersede_pass, supersede_pass_enabled

    if not supersede_pass_enabled():
        return []
    sub = _subgraph(graph, members, {_SUPERSEDE})
    before = len(sub.edges)
    run_supersede_pass(
        sub,
        json_client=_CacheOnlyClient(),  # type: ignore[arg-type]
        cache_dir=project_root / ".tesserae" / "supersede_cache",
    )
    return [
        {"op": "supersede", "region": region, "edge": _edge_dict(edge)}
        for edge in sub.edges[before:]
    ]


def _plan_associate(view: ResearchGraph, region: str, members: Set[str], backend) -> List[dict]:
    from .memory.associate import discover_links

    links = discover_links(_subgraph(view, members), backend=backend)
    return [
        {"op": "associate", "region": region, "source": s, "target": t, "cosine": c}
        for s, t, c in links
    ]


def _decay_reference(project_root: Path, graph: ResearchGraph) -> datetime:
    from .project import ProjectWiki

    return ProjectWiki(project_root)._compile_reference_timestamp(graph)


def _plan_decay(graph_nodes: Mapping[str, ResearchNode], region: str, members: Sequence[str], memory, reference: datetime) -> List[dict]:
    """Mirror of the compile's decay staging (``_run_memory_passes`` step 7)."""
    from .memory.decay import compute_decay_score

    ops: List[dict] = []
    for node_id in members:
        prev = memory.get(node_id)
        node = graph_nodes.get(node_id)
        if prev is None or node is None:
            continue  # compile owns row creation
        meta = dict(node.metadata) if isinstance(node.metadata, dict) else {}
        if prev.access_count:
            meta["access_count"] = prev.access_count
        if prev.last_accessed_at:
            meta["last_accessed_at"] = prev.last_accessed_at
        after = compute_decay_score(SimpleNamespace(metadata=meta), reference)
        before = float(prev.decay_score) if prev.decay_score is not None else None
        if before is None or abs(after - before) > _DECAY_EPS:
            ops.append({"op": "decay", "region": region, "node_id": node_id, "before": before, "after": after})
    return ops


def _plan_distill(project_root: Path, graph: ResearchGraph, cfg: Optional[dict], env: Mapping[str, str]) -> List[dict]:
    """Worker agents past the pressure floor — the gate ``maybe_distill_on_refresh`` uses."""
    from .agent_distill import (
        agent_distill_enabled,
        known_agent_keys,
        manager_children,
        undistilled_slice_chars,
    )
    from .agent_identity import AgentRegistry
    from .llm_chunking import chunk_char_budget

    if not agent_distill_enabled(cfg, env):
        return []
    registry = AgentRegistry.for_project(project_root)
    floor = chunk_char_budget() // 2
    ops = []
    for key in known_agent_keys(graph, registry):
        if manager_children(graph, registry, key):
            continue  # managers roll up children; not a dream op (yet)
        if undistilled_slice_chars(graph, key, project_root) > floor:
            ops.append({"op": "distill", "region": "", "agent": key})
    return ops


# --------------------------------------------------------------------------- probe gate


def _build_probes(project_root: Path, graph: ResearchGraph, n: int, seed: int) -> List[Tuple[str, List[str]]]:
    """``[(query, gold ids)]`` from ``.tesserae/dream/probes.jsonl``, else sampled.

    Sampled probes are self-retrieval probes: a node's name as the query, the
    node as gold, chosen by a seeded hash so the set is stable across passes.
    """
    path = _probes_path(project_root)
    if path.is_file():
        probes: List[Tuple[str, List[str]]] = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                query, gold = str(row["query"]), [str(g) for g in row["gold"]]
            except (ValueError, KeyError, TypeError) as exc:
                raise DreamError(f"{path}:{lineno}: expected {{\"query\": str, \"gold\": [ids]}} ({exc})") from exc
            probes.append((query, gold))
        return probes
    from .graph_filters import suppressed_ids

    suppressed = suppressed_ids(graph)
    pool = [
        node
        for node in graph.nodes
        if node.id not in suppressed and len((node.name or "").split()) >= 2
    ]
    pool.sort(key=lambda node: hashlib.sha256(f"{seed}:{node.id}".encode("utf-8")).hexdigest())
    return [(node.name, [node.id]) for node in pool[: max(0, int(n))]]


def _probe_scores(
    graph: ResearchGraph,
    probes: Sequence[Tuple[str, List[str]]],
    backend,
    top_k: int,
    *,
    project_root: Optional[Path] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Optional[List[float]]:
    """Per-probe reciprocal rank, or ``None`` when ``should_stop`` fired.

    ``project_root`` hands ``compile_context`` the project's vector cache and
    BM25 index: the candidate graphs differ from the baseline by edges only,
    so every probe after the first embeds nothing new. Without it each call
    re-embeds and re-tokenises the whole corpus.
    """
    from .context_compiler import compile_context

    superseders: Dict[str, Set[str]] = {}
    for edge in graph.edges:
        if edge.type == _SUPERSEDE:
            superseders.setdefault(edge.target, set()).add(edge.source)
    scores: List[float] = []
    for query, gold in probes:
        if should_stop is not None and should_stop():
            return None
        accept: Set[str] = set()
        frontier = list(gold)
        while frontier:  # gold plus everything that (transitively) supersedes it
            nid = frontier.pop()
            if nid in accept:
                continue
            accept.add(nid)
            frontier.extend(superseders.get(nid, ()))
        bundle = compile_context(
            graph, project_root=str(project_root) if project_root else None, query=query, backend=backend, budget=4000
        )
        rr = 0.0
        for rank, nid in enumerate(bundle.ranked_nodes[:top_k], 1):
            if nid in accept:
                rr = 1.0 / rank
                break
        scores.append(rr)
    return scores


def paired_bootstrap(base: Sequence[float], cand: Sequence[float], *, samples: int, alpha: float, seed: int) -> Dict[str, Any]:
    """Mean paired delta (cand - base) and its percentile bootstrap CI."""
    if len(base) != len(cand):
        raise ValueError("paired bootstrap needs equal-length score lists")
    deltas = [c - b for b, c in zip(base, cand)]
    n = len(deltas)
    if n == 0:
        return {"n": 0, "mean_delta": 0.0, "ci": [0.0, 0.0]}
    mean = sum(deltas) / n
    if all(d == 0 for d in deltas):
        return {"n": n, "mean_delta": 0.0, "ci": [0.0, 0.0]}
    rng = random.Random(seed)
    means = sorted(sum(deltas[rng.randrange(n)] for _ in range(n)) / n for _ in range(max(1, samples)))
    lo = means[int((alpha / 2) * (len(means) - 1))]
    hi = means[int((1 - alpha / 2) * (len(means) - 1))]
    return {"n": n, "mean_delta": mean, "ci": [lo, hi]}


def _candidate_graph(view: ResearchGraph, ops: Sequence[dict]) -> ResearchGraph:
    extra: List[ResearchEdge] = []
    for op in ops:
        if op["op"] == "supersede":
            extra.append(ResearchEdge(**op["edge"]))
        elif op["op"] == "associate":
            extra.append(
                ResearchEdge(
                    source=op["source"],
                    target=op["target"],
                    type=_SHARES,
                    metadata={"federation_semantic": True, "associate_overlay": True, "cosine": op["cosine"]},
                )
            )
    if not extra:
        return view
    return ResearchGraph(nodes=list(view.nodes), edges=list(view.edges) + extra)


# --------------------------------------------------------------------------- plan


@dataclass
class RegionPlan:
    region: str
    size: int
    ops: List[dict] = field(default_factory=list)
    gate: Dict[str, Any] = field(default_factory=dict)
    promoted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"region": self.region, "size": self.size, "ops": self.ops, "gate": self.gate, "promoted": self.promoted}


@dataclass
class DreamPlan:
    pass_index: int
    scope: str
    regions: List[RegionPlan] = field(default_factory=list)
    distill_ops: List[dict] = field(default_factory=list)
    deferred_regions: List[str] = field(default_factory=list)
    reviewed_sessions: Dict[str, List[str]] = field(default_factory=dict)
    unmatched_sessions: List[str] = field(default_factory=list)
    skipped_rejected: int = 0
    gate_ran: bool = False
    deferred_reason: str = ""
    notes: List[str] = field(default_factory=list)

    def promoted_ops(self) -> List[dict]:
        if self.deferred_reason:
            return []
        ops = [op for region in self.regions if region.promoted for op in region.ops]
        return ops + list(self.distill_ops)

    def rejected_ops(self) -> List[dict]:
        if self.deferred_reason:
            return []
        return [op for region in self.regions if not region.promoted for op in region.ops]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pass_index": self.pass_index,
            "scope": self.scope,
            "regions": [r.to_dict() for r in self.regions],
            "distill_ops": self.distill_ops,
            "deferred_regions": self.deferred_regions,
            "reviewed_sessions": self.reviewed_sessions,
            "unmatched_sessions": self.unmatched_sessions,
            "skipped_rejected": self.skipped_rejected,
            "gate_ran": self.gate_ran,
            "deferred_reason": self.deferred_reason,
            "notes": self.notes,
            "promoted_ops": self.promoted_ops(),
            "op_counts": _op_counts(self.promoted_ops()),
        }


def _op_counts(ops: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    counts = {kind: 0 for kind in OP_KINDS}
    for op in ops:
        counts[str(op["op"])] += 1
    return counts


def _findings_sha(nodes: Iterable[ResearchNode]) -> str:
    """Digest of a session's findings as the graph has them (ids + names)."""
    lines = sorted(f"{node.id}\t{node.name or ''}" for node in nodes)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _dirty_scope(project_root: Path, graph: ResearchGraph, regions: Sequence[Tuple[str, List[str]]]):
    """``(scope, dirty region ids or None for all, {sid: [head, findings_sha]}, unmatched)``.

    A session is dirty when the chunk store has turns past its cursor OR its
    findings in ``graph.json`` changed since the cursor was set. The second
    half is what keeps cursor scoping honest: the tailer stores turns live,
    before a compile turns them into findings, so a pass can review a session
    whose findings do not exist yet (``unmatched_sessions``) or exist only in
    part. Its cursor still advances to the turn head, but with the findings
    digest it saw; when the compile adds the findings the digest changes and
    the session's regions are dirty again.
    """
    from .session_chunks import SessionChunksDB, chunks_db_path

    db_path = chunks_db_path(project_root)
    if not db_path.is_file():
        return "all", None, {}, []
    db = SessionChunksDB(db_path)
    heads = db.session_heads()
    if not heads:
        return "all", None, {}, []
    cursors = db.dream_cursors()
    node_region = {nid: rid for rid, members in regions for nid in members}
    by_key: Dict[str, List[ResearchNode]] = {}
    for node in graph.nodes:
        sid = (node.metadata or {}).get("session_id") if isinstance(node.metadata, dict) else None
        if sid:
            by_key.setdefault(_session_key(str(sid)), []).append(node)
    touched: Set[str] = set()
    dirty: Dict[str, List[str]] = {}
    unmatched: List[str] = []
    for sid in sorted(heads):
        key = _session_key(sid)
        hit = list(by_key.get(key, ()))
        if not hit:  # codex stems carry the session id as a suffix
            for graph_key, nodes in by_key.items():
                # length floor: a short key would suffix-match unrelated stems
                if len(graph_key) >= 8 and key.endswith(graph_key):
                    hit.extend(nodes)
        sha = _findings_sha(hit)
        last_ts, last_sha = cursors.get(sid, ("", ""))
        if heads[sid] <= last_ts and sha == last_sha:
            continue
        dirty[sid] = [heads[sid], sha]
        if hit:
            touched |= {node_region.get(node.id, UNASSIGNED_REGION) for node in hit}
        else:
            unmatched.append(sid)
    return "cursor", touched, dirty, unmatched


def plan_dream(
    project_root: Path | str,
    *,
    settings: Optional[DreamSettings] = None,
    backend=None,
    probe_backend=None,
    cfg: Optional[dict] = None,
    env: Optional[Mapping[str, str]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> DreamPlan:
    """Compute one pass's plan. Writes no graph or memory state (opening the
    chunk store may add its empty ``dream_cursor`` table, and the probe gate
    may fill the project's vector cache, as any reader would)."""
    from .memory.associate import apply_overlay
    from .memory.store import read_memory
    from .project import load_graph_file

    root = Path(project_root)
    env = env if env is not None else os.environ
    settings = settings or DreamSettings()
    graph_path = root / ".tesserae" / "graph.json"
    if not graph_path.is_file():
        raise DreamError(f"no compiled graph at {graph_path}; run `tesserae compile` first")
    graph = load_graph_file(graph_path)
    view = apply_overlay(root, graph)
    state = _load_state(root)
    rejected = set(state["rejected"])
    plan = DreamPlan(pass_index=state["passes"], scope="all")

    regions = compute_regions(graph)
    if settings.regions:
        known = {rid for rid, _ in regions}
        missing = sorted(set(settings.regions) - known)
        if missing:
            raise DreamError(f"unknown region(s) {missing}; `tesserae dream --dry-run --json` lists them")
        regions = [(rid, m) for rid, m in regions if rid in set(settings.regions)]
        plan.scope = "explicit"
    scope, dirty, plan.reviewed_sessions, plan.unmatched_sessions = _dirty_scope(root, graph, regions)
    if settings.regions:
        # An explicit region list reviews those regions, not whole sessions:
        # leave every cursor where it is.
        plan.reviewed_sessions = {}
    elif settings.all_regions:
        plan.scope = "all"
    else:
        plan.scope = scope
        if dirty is not None:
            regions = [(rid, m) for rid, m in regions if rid in dirty]

    memory = {}
    sqlite_path = root / ".tesserae" / "sqlite.db"
    if "decay" in settings.ops or "supersede" in settings.ops:
        if sqlite_path.is_file():
            memory = read_memory(sqlite_path)
        elif "decay" in settings.ops:
            plan.notes.append("decay: no node_memory sidecar yet (compile writes it); skipped")
    if "associate" in settings.ops and backend is None:
        plan.notes.append("associate: no semantic embedding backend; skipped")
    reference = _decay_reference(root, graph) if "decay" in settings.ops and memory else None
    nodes_by_id = {n.id: n for n in graph.nodes}

    for rid, member_list in regions:
        members = set(member_list)
        ops: List[dict] = []
        if "supersede" in settings.ops:
            ops += _plan_supersede(root, graph, rid, members)
        if "associate" in settings.ops and backend is not None:
            ops += _plan_associate(view, rid, members, backend)
        if reference is not None:
            ops += _plan_decay(nodes_by_id, rid, member_list, memory, reference)
        kept = [op for op in ops if _fingerprint(op) not in rejected]
        plan.skipped_rejected += len(ops) - len(kept)
        if kept:
            plan.regions.append(RegionPlan(region=rid, size=len(members), ops=kept))

    # The cap bounds the probe gate, so it counts only regions WITH ops: next
    # pass, the ones handled here plan 0 ops (applied, or filtered as
    # rejected) and the deferred ones move up instead of starving.
    if len(plan.regions) > settings.max_regions:
        plan.deferred_regions = [r.region for r in plan.regions[settings.max_regions:]]
        plan.regions = plan.regions[: settings.max_regions]
        # A session whose findings sit in a deferred region has not been
        # reviewed; holding its cursor keeps that region dirty for next pass.
        plan.reviewed_sessions = {}
        plan.notes.append(
            f"{len(plan.deferred_regions)} region(s) with ops over max_regions={settings.max_regions} "
            "wait for the next pass; cursors not advanced"
        )

    if "distill" in settings.ops:
        plan.distill_ops = [op for op in _plan_distill(root, graph, cfg, env) if _fingerprint(op) not in rejected]

    _gate(root, plan, view, settings, probe_backend, should_stop)
    return plan


def _defer_ungated(plan: DreamPlan, regions: Sequence[RegionPlan]) -> None:
    """``should_stop`` fired: hand the not-yet-gated regions to the next pass."""
    for region in regions:
        plan.regions.remove(region)
        plan.deferred_regions.append(region.region)
    plan.reviewed_sessions = {}
    plan.notes.append(
        f"asked to stop mid-gate (a pipeline run is waiting): {len(regions)} region(s) deferred to the "
        "next pass; cursors not advanced"
    )


def resolve_probe_backend():
    """A LOCAL backend for the probe gate: model2vec, else the hash stub.

    Never the active backend: ``TESSERAE_EMBEDDING_PREFER=gemini`` (or
    openai) would turn every probe into a metered call. The gate compares a
    baseline against candidates on the same backend, so a weaker local one
    still measures the difference it is asked about.
    """
    from .retrieval.hybrid import HashEmbeddingBackend, active_embedding_backend

    try:
        return active_embedding_backend("model2vec")
    except Exception as exc:  # noqa: BLE001 - optional dep; degrade loudly
        logger.warning("dream: model2vec unavailable for the probe gate (%s); probing with the hash stub", exc)
        return HashEmbeddingBackend()


def _gate(
    root: Path,
    plan: DreamPlan,
    view: ResearchGraph,
    settings: DreamSettings,
    probe_backend,
    should_stop: Optional[Callable[[], bool]] = None,
) -> None:
    visible = [r for r in plan.regions if any(op["op"] in ("supersede", "associate") for op in r.ops)]
    if not settings.gate:
        for region in plan.regions:
            region.promoted = True
            region.gate = {"skipped": "gate disabled"}
        return
    if visible and plan.pass_index % int(settings.probe_every) != 0:
        plan.deferred_reason = (
            f"probe gate runs every {settings.probe_every} pass(es); pass {plan.pass_index} writes nothing"
        )
        plan.reviewed_sessions = {}
        return
    visible_ids = {id(r) for r in visible}
    for region in plan.regions:
        if id(region) not in visible_ids:
            region.promoted = True
            region.gate = {"skipped": "no probe-visible ops (decay is not seen by the probe retriever)"}
    probes = _build_probes(root, view, settings.probes, settings.seed) if visible else []
    if visible and len(probes) < settings.probes:
        plan.notes.append(f"probe set has n={len(probes)} (< {settings.probes} requested: that is all the graph offers)")
    if probes and probe_backend is None:
        probe_backend = resolve_probe_backend()
    score = dict(project_root=root, should_stop=should_stop)
    base = _probe_scores(view, probes, probe_backend, settings.top_k, **score) if probes else []
    if base is None:
        _defer_ungated(plan, visible)
        return
    plan.gate_ran = bool(visible)
    for region in visible:
        if not probes:
            region.promoted = False
            region.gate = {"n": 0, "reason": "no probes available; refusing to promote ungated"}
            continue
        cand = _probe_scores(_candidate_graph(view, region.ops), probes, probe_backend, settings.top_k, **score)
        if cand is None:
            _defer_ungated(plan, [r for r in visible if not r.gate])
            return
        stats = paired_bootstrap(base, cand, samples=settings.bootstrap, alpha=settings.alpha, seed=settings.seed)
        regressed = stats["ci"][1] < 0 or stats["mean_delta"] < -float(settings.max_drop)
        region.promoted = not regressed
        region.gate = {
            **stats,
            "base_mrr": sum(base) / len(base),
            "cand_mrr": sum(cand) / len(cand),
            "max_drop": settings.max_drop,
            "reason": "regression" if regressed else "no regression",
        }


# --------------------------------------------------------------------------- apply / undo


def _journal_dir(root: Path) -> Path:
    return dream_dir(root) / "journal"


def _is_interrupted(status: str) -> bool:
    """A pass that started writing and neither finished nor rolled back."""
    return status == "applying" or status.startswith("failed")


def _live_journals(root: Path) -> List[Tuple[Path, Dict[str, Any]]]:
    """Journals whose writes are still on disk, oldest first.

    Ordered by ``pass_index``, which is read and bumped under ``compile.lock``,
    never by the wall-clock stamp in the filename: on N hosts sharing a disk a
    skewed clock would otherwise make an older pass look newest to undo.
    """
    live = []
    for path in _journal_dir(root).glob("*/*.json"):
        payload = _read_json(path, {})
        status = str(payload.get("status", ""))
        if status == "applied" or _is_interrupted(status):
            live.append((path, payload))
    live.sort(key=lambda item: int(item[1]["plan"]["pass_index"]))
    indexes = [int(payload["plan"]["pass_index"]) for _, payload in live]
    if len(indexes) != len(set(indexes)):
        raise DreamError(
            f"two live dream journals share a pass index ({sorted(indexes)}) under "
            f"{_journal_dir(root)}; the undo order is ambiguous, so resolve it by hand"
        )
    return live


def _interrupted_journal(root: Path) -> Optional[Dict[str, Any]]:
    for _path, payload in _live_journals(root):
        if _is_interrupted(str(payload["status"])):
            return payload
    return None


def _canonical_graph_text(graph: ResearchGraph) -> str:
    return graph.to_json(indent=2) + "\n"


def run_dream(
    project_root: Path | str,
    *,
    dry_run: bool = True,
    settings: Optional[DreamSettings] = None,
    backend=None,
    probe_backend=None,
    cfg: Optional[dict] = None,
    env: Optional[Mapping[str, str]] = None,
    lock_wait: Optional[float] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Plan (and unless ``dry_run``, apply + journal) one pass.

    Applying runs under ``compile.lock`` and raises
    :class:`~tesserae.locking.CompileLockHeldError` when a compile holds it. A
    dry run only reads, so it takes no lock: a preview never blocks a compile
    or fails because one is running (its probe-gate verdicts are then computed
    on whatever graph was last published). :class:`DreamError` on anything
    unsafe, including an earlier pass left half-applied. ``should_stop`` is
    polled during the probe gate; when it turns true the regions not yet gated
    are deferred to the next pass (the daemon passes its pipeline-pending flag).
    """
    from .locking import compile_lock

    root = Path(project_root).resolve()
    cfg = cfg if cfg is not None else load_project_config(root)
    settings = settings or DreamSettings.from_config(cfg)
    kwargs = dict(
        settings=settings, backend=backend, probe_backend=probe_backend, cfg=cfg, env=env, should_stop=should_stop
    )
    if dry_run:
        plan = plan_dream(root, **kwargs)
        report = plan.to_dict()
        report["dry_run"] = True
        stuck = _interrupted_journal(root)
        if stuck:
            report["notes"].append(
                f"pass {stuck['pass_id']} is {stuck['status']!r} (in progress, or interrupted: then "
                f"`tesserae dream --undo {stuck['pass_id']}` before applying again)"
            )
        return report
    with compile_lock(root / ".tesserae", wait_seconds=lock_wait):
        stuck = _interrupted_journal(root)
        if stuck:
            raise DreamError(
                f"dream pass {stuck['pass_id']} was interrupted mid-apply ({stuck['status']}); its "
                f"partial writes are still on disk. Run `tesserae dream --undo {stuck['pass_id']}` first"
            )
        plan = plan_dream(root, **kwargs)
        report = plan.to_dict()
        report["dry_run"] = False
        if plan.deferred_reason:
            _write_json(_state_path(root), {**_load_state(root), "passes": plan.pass_index + 1})
            report["applied_ops"] = []
            return report
        if not plan.promoted_ops() and not plan.rejected_ops() and not plan.reviewed_sessions:
            # Nothing to write, reject or review: no journal entry and no pass
            # counted, so an idle daemon does not grow the journal every tick.
            report["applied_ops"] = []
            return report
        report.update(_apply(root, plan))
        return report


def _apply(root: Path, plan: DreamPlan) -> Dict[str, Any]:
    """Write a plan's promoted ops; on ANY failure roll the written ones back.

    Every before-value (and the graph's expected after-hash) is journaled
    before the first write, so the same reverse replay serves three cases: an
    exception here (rolled back in place), a crash that left the journal at
    ``applying`` (``undo_dream`` accepts it), and an ordinary undo.
    """
    from .memory.associate import _overlay_path, persist_links
    from .memory.store import read_memory, set_memory_fields
    from .project import _publish_atomically, load_graph_file
    from .session_chunks import SessionChunksDB, chunks_db_path

    ops = plan.promoted_ops()
    graph_path = root / ".tesserae" / "graph.json"
    sqlite_path = root / ".tesserae" / "sqlite.db"
    state_before = _load_state(root)
    now = datetime.now(timezone.utc)
    pass_id = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{plan.pass_index:06d}"
    journal_path = _journal_dir(root) / now.strftime("%Y-%m-%d") / f"{pass_id}.json"

    supersede = [op for op in ops if op["op"] == "supersede"]
    associate = [op for op in ops if op["op"] == "associate"]
    decay = [op for op in ops if op["op"] == "decay"]
    distill = [op for op in ops if op["op"] == "distill"]

    graph = load_graph_file(graph_path)
    graph_bytes = graph_path.read_bytes()
    graph_sha_before = hashlib.sha256(graph_bytes).hexdigest()
    graph_text_after: Optional[str] = None
    if supersede:
        if _canonical_graph_text(graph).encode("utf-8") != graph_bytes:
            raise DreamError(
                f"{graph_path} does not round-trip through the graph serializer, so an undo could not "
                "restore it byte-for-byte; recompile before dreaming"
            )
        for op in supersede:
            graph.edges.append(ResearchEdge(**op["edge"]))
        graph_text_after = _canonical_graph_text(graph)
    graph_sha_after = (
        hashlib.sha256(graph_text_after.encode("utf-8")).hexdigest() if graph_text_after is not None else graph_sha_before
    )

    # node_memory before-values for every row an op touches.
    memory = read_memory(sqlite_path) if sqlite_path.is_file() else {}
    touched = {op["node_id"] for op in decay} | {op["edge"]["target"] for op in supersede}
    memory_before = {
        nid: [float(memory[nid].decay_score), bool(memory[nid].superseded)] for nid in sorted(touched) if nid in memory
    }

    chunks_path = chunks_db_path(root)
    cursor_before: Dict[str, Optional[List[str]]] = {}
    if plan.reviewed_sessions and chunks_path.is_file():
        existing = SessionChunksDB(chunks_path).dream_cursors()
        cursor_before = {sid: (list(existing[sid]) if sid in existing else None) for sid in plan.reviewed_sessions}

    journal: Dict[str, Any] = {
        "version": JOURNAL_VERSION,
        "pass_id": pass_id,
        "status": "applying",
        "started_at": now.isoformat(),
        "plan": plan.to_dict(),
        "ops": ops,
        "rejected_ops": plan.rejected_ops(),
        "reviewed_sessions": plan.reviewed_sessions,
        "before": {
            "graph_sha256": graph_sha_before,
            "node_memory": memory_before,
            "overlay_existed": _overlay_path(root).is_file(),
            "cursors": cursor_before,
            "state": state_before,
            "distill": {op["agent"]: _distill_before(root, op["agent"]) for op in distill},
        },
        "after": {"graph_sha256": graph_sha_after},
    }
    _write_json(journal_path, journal)
    try:
        if graph_text_after is not None:
            _publish_atomically(graph_path, graph_text_after)
        memory_rows: Dict[str, List[Any]] = {nid: list(v) for nid, v in memory_before.items()}
        for op in decay:
            if op["node_id"] in memory_rows:
                memory_rows[op["node_id"]][0] = float(op["after"])
        for op in supersede:
            if op["edge"]["target"] in memory_rows:
                memory_rows[op["edge"]["target"]][1] = True
        if memory_rows:
            set_memory_fields(sqlite_path, [(nid, v[0], v[1]) for nid, v in memory_rows.items()])
        if associate:
            persist_links(root, [(op["source"], op["target"], op["cosine"]) for op in associate])
        for op in distill:
            journal["before"]["distill"][op["agent"]]["status"] = _run_distill(root, graph, op["agent"])
        if plan.reviewed_sessions and chunks_path.is_file():
            SessionChunksDB(chunks_path).set_dream_cursors(dict(plan.reviewed_sessions))
        rejected = set(state_before["rejected"]) | {_fingerprint(op) for op in plan.rejected_ops()}
        _write_json(_state_path(root), {"passes": plan.pass_index + 1, "rejected": sorted(rejected)})
    except BaseException as exc:  # incl. KeyboardInterrupt: roll back, then re-raise
        _roll_back(root, journal, journal_path, exc)
        raise
    journal["status"] = "applied"
    _write_json(journal_path, journal)
    return {"pass_id": pass_id, "journal": str(journal_path), "applied_ops": ops}


def _roll_back(root: Path, journal: Dict[str, Any], journal_path: Path, exc: BaseException) -> None:
    """Undo a pass that raised mid-apply. Never raises: the caller re-raises ``exc``."""
    journal["status"] = f"failed: {exc!r}"
    _write_json(journal_path, journal)
    try:
        _reverse(root, journal, interrupted=True)
    except Exception as undo_exc:  # noqa: BLE001 - reported loudly; the original error wins
        journal["status"] = f"failed: {exc!r}; rollback failed: {undo_exc!r}"
        _write_json(journal_path, journal)
        logger.error(
            "dream pass %s failed mid-apply (%r) AND its rollback failed (%r): partial writes remain; "
            "fix the cause, then `tesserae dream --undo %s` (journal %s)",
            journal["pass_id"], exc, undo_exc, journal["pass_id"], journal_path,
        )
        return
    journal["status"] = f"rolled back: {exc!r}"
    journal["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(journal_path, journal)
    logger.error("dream pass %s failed mid-apply (%r); rolled back (journal %s)", journal["pass_id"], exc, journal_path)


def _distill_before(root: Path, agent: str) -> Dict[str, Any]:
    from .agent_distill import DistillStateStore, _state_db_path, agent_artifact_path

    artifact = agent_artifact_path(root, agent)
    return {
        "artifact": str(artifact),
        "artifact_b64": base64.b64encode(artifact.read_bytes()).decode("ascii") if artifact.is_file() else None,
        "watermark": DistillStateStore(_state_db_path(root)).get(DistillStateStore.SCOPE_WATERMARK, agent, ""),
    }


def _run_distill(root: Path, graph: ResearchGraph, agent: str) -> str:
    from .agent_distill import DistillError, DistillStateStore, _state_db_path, distill_agent

    try:
        return distill_agent(graph, agent, project_root=root, state=DistillStateStore(_state_db_path(root))).status
    except DistillError as exc:
        logger.warning("dream: distill for %s failed: %s", agent, exc)
        return f"failed: {exc}"


def _reverse(root: Path, journal: Mapping[str, Any], *, interrupted: bool) -> None:
    """Replay one journal backwards. Every check runs before the first write.

    ``interrupted`` (a pass that raised or crashed mid-apply) also accepts a
    ``graph.json`` still at its pre-pass bytes — the publish never happened —
    and every other step is an idempotent write of a before-value.
    """
    from .memory.associate import remove_links
    from .memory.store import set_memory_fields
    from .project import _publish_atomically, load_graph_file
    from .session_chunks import SessionChunksDB, chunks_db_path

    graph_path = root / ".tesserae" / "graph.json"
    before = journal["before"]
    ops = journal["ops"]
    current = _sha256(graph_path)
    after_sha = journal["after"]["graph_sha256"]
    allowed = {after_sha, before["graph_sha256"]} if interrupted else {after_sha}
    if current not in allowed:
        raise DreamError(
            f"graph.json changed since dream pass {journal['pass_id']} (a compile or another writer "
            "ran); its edges are already re-derived or gone, so it cannot be replayed backwards"
        )
    restored_graph: Optional[str] = None
    if current != before["graph_sha256"]:
        graph = load_graph_file(graph_path)
        for op in (op for op in ops if op["op"] == "supersede"):
            key = (op["edge"]["source"], op["edge"]["type"], op["edge"]["target"])
            for i in range(len(graph.edges) - 1, -1, -1):
                edge = graph.edges[i]
                if (edge.source, edge.type, edge.target) == key:
                    del graph.edges[i]
                    break
            else:
                raise DreamError(f"undo: edge {key} is not in graph.json; refusing a partial undo")
        restored_graph = _canonical_graph_text(graph)
        if hashlib.sha256(restored_graph.encode("utf-8")).hexdigest() != before["graph_sha256"]:
            raise DreamError("undo: the replayed graph.json would not match its pre-pass bytes; left untouched")

    for agent, info in sorted(before.get("distill", {}).items()):
        _undo_distill(root, agent, info)
    if before.get("cursors"):
        chunks_path = chunks_db_path(root)
        if chunks_path.is_file():
            SessionChunksDB(chunks_path).set_dream_cursors(before["cursors"])
    associate = [(op["source"], op["target"], op["cosine"]) for op in ops if op["op"] == "associate"]
    if associate:
        remove_links(root, associate, delete_if_empty=not before["overlay_existed"])
    if before["node_memory"]:
        set_memory_fields(
            root / ".tesserae" / "sqlite.db",
            [(nid, v[0], v[1]) for nid, v in before["node_memory"].items()],
        )
    if restored_graph is not None:
        _publish_atomically(graph_path, restored_graph)
    _write_json(_state_path(root), before["state"])


def undo_dream(project_root: Path | str, pass_id: str = "last", *, lock_wait: Optional[float] = None) -> Dict[str, Any]:
    """Reverse-replay the newest live pass (``pass_id`` must name it, or ``last``).

    "Newest" is the highest ``pass_index``. A pass left ``applying`` or
    ``failed`` by a crash is live too, and undoing it is how its partial
    writes come back out. Refuses when ``graph.json`` changed since that pass
    (a compile ran) or when the restored bytes would not match.
    """
    from .locking import compile_lock

    root = Path(project_root).resolve()
    with compile_lock(root / ".tesserae", wait_seconds=lock_wait):
        live = _live_journals(root)
        if not live:
            raise DreamError("no applied dream pass to undo")
        path, journal = live[-1]
        if pass_id not in ("last", journal["pass_id"]):
            raise DreamError(
                f"pass {pass_id} is not the newest applied pass ({journal['pass_id']}); undo is "
                "reverse replay, so newer passes must be undone first"
            )
        _reverse(root, journal, interrupted=_is_interrupted(str(journal["status"])))
        journal["status"] = "undone"
        journal["undone_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(path, journal)
        return {"undone": journal["pass_id"], "journal": str(path), "op_counts": _op_counts(journal["ops"])}


def _undo_distill(root: Path, agent: str, info: Mapping[str, Any]) -> None:
    from .agent_distill import DistillStateStore, _state_db_path

    artifact = Path(info["artifact"])
    if info.get("artifact_b64") is None:
        if artifact.is_file():
            artifact.unlink()
    else:
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(base64.b64decode(info["artifact_b64"]))
    DistillStateStore(_state_db_path(root)).put(
        DistillStateStore.SCOPE_WATERMARK, agent, "", info.get("watermark") or ""
    )


def resolve_backend():
    """The semantic backend associate needs, or ``None`` for the hash stub.

    Same rule as ``Daemon._resolve_embedding_backend``: a stub would discover
    noise links, so it counts as no backend.
    """
    from .retrieval.hybrid import HashEmbeddingBackend, active_embedding_backend

    backend = active_embedding_backend()
    return None if isinstance(backend, HashEmbeddingBackend) else backend


def format_report(report: Mapping[str, Any]) -> str:
    """A short human summary; ``--json`` prints the full report instead."""
    if "undone" in report:
        return f"undid dream pass {report['undone']}: {report['op_counts']}"
    lines = [
        f"dream pass {report['pass_index']} ({'dry-run' if report['dry_run'] else 'applied'}), scope={report['scope']}",
        f"  promoted ops: {report['op_counts']}",
    ]
    for region in report["regions"]:
        gate = region["gate"]
        verdict = "promoted" if region["promoted"] else "REJECTED"
        detail = gate.get("reason") or gate.get("skipped", "")
        if "mean_delta" in gate:
            detail += f" (n={gate['n']}, mean Δ={gate['mean_delta']:+.4f}, CI=[{gate['ci'][0]:+.4f}, {gate['ci'][1]:+.4f}])"
        lines.append(f"  {region['region']} ({region['size']} nodes, {len(region['ops'])} ops): {verdict} — {detail}")
    if report.get("deferred_reason"):
        lines.append(f"  deferred: {report['deferred_reason']}")
    for note in report.get("notes", []):
        lines.append(f"  note: {note}")
    if report.get("journal"):
        lines.append(f"  journal: {report['journal']}")
    return "\n".join(lines)

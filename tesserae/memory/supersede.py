"""Post-compile ``superseded_by`` edge pass for session-finding nodes.

Inspired by Graphiti's superseded-edge pattern and A-MEM's Zettelkasten
note-evolution loop (see ``/tmp/tesserae-innovation/03-memory.md``).

Pass shape (default-on, opt-out via ``TESSERAE_SUPERSEDE_PASS=false``):

1. Group session-finding nodes by ``ResearchNodeType`` (insights with
   insights, decisions with decisions, ...).
2. Inside each group, compute a cheap Jaccard token-set similarity on
   the node ``name`` strings. Pairs with ``similarity > 0.55`` become
   judgement candidates — quadratic in group size, but a real project
   tops out at a few hundred session findings per kind so this is fine.
3. For each candidate pair, ask the LLM whether either side obsoletes
   the other. The answer is cached on disk so reruns are free.
4. When the LLM says "A obsoletes B" (or vice-versa) mint a
   ``superseded_by`` edge in that direction. The older node is the one
   that is now obsolete; the newer one is the target.

Keeps the pass strictly additive: no nodes are deleted, only edges are
added. Downstream consumers (the MCP ``fresh_insights`` tool, the wiki
projection, ...) filter on the new edge.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..llm_json import LLMJsonClient
from ..research_graph import (
    SESSION_FINDING_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
)

logger = logging.getLogger(__name__)

SUPERSEDE_EDGE = "supersedes"
"""Edge kind minted by this pass. Already in :data:`ALLOWED_EDGE_TYPES`.

Direction: ``source supersedes target`` — i.e. ``target`` is the older
finding now obsoleted by ``source``. The MCP ``fresh_insights`` filter
hides any node that has an *incoming* ``supersedes`` edge OR any node
that has an outgoing edge pointing AT it (matching the spec wording
"excluding ones with outgoing ``superseded_by`` edge" where the older
node is the one pointing forward to its replacement). We canonicalise
to "newer supersedes older" so the graph reads cleanly.
"""

# Word characters only — avoids matching different punctuation as a token.
_TOKEN_SPLIT_CHARS = " \t\n\r\f\v.,;:!?()[]{}\"'`/\\|<>@#$%^&*+=~"


def supersede_pass_enabled() -> bool:
    """Read the opt-OUT env flag — the pass is DEFAULT-ON (KB-03).

    ``TESSERAE_SUPERSEDE_PASS`` disables the pass only when set to one of
    the falsy spellings ``{"0", "false", "no", "off"}``. An unset or any
    other value leaves the pass enabled, so a plain ``project compile``
    runs supersede arbitration by default. Disk-cached, content-keyed
    LLM verdicts keep reruns byte-idempotent (05-RESEARCH Pitfall 5).
    """
    raw = (os.environ.get("TESSERAE_SUPERSEDE_PASS") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


def _tokenise(text: str) -> Set[str]:
    """Cheap, dependency-free token set for Jaccard similarity."""
    if not text:
        return set()
    buf = []
    for ch in text.lower():
        if ch in _TOKEN_SPLIT_CHARS:
            buf.append(" ")
        else:
            buf.append(ch)
    return {tok for tok in "".join(buf).split() if len(tok) > 1}


def jaccard(a: str, b: str) -> float:
    """Jaccard similarity of token sets — ``0.0`` when either side is empty."""
    ta, tb = _tokenise(a), _tokenise(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


# ---------------------------------------------------------------------------
# LLM judgement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupersedeJudgement:
    """Outcome of asking the LLM to compare two findings.

    ``verdict`` is one of:
      - ``"a_obsoletes_b"`` — node A is newer/better; edge goes A->B.
      - ``"b_obsoletes_a"`` — node B is newer/better; edge goes B->A.
      - ``"distinct"`` — neither obsoletes the other; no edge.
    """

    verdict: str
    rationale: str = ""

    def is_supersede(self) -> bool:
        return self.verdict in {"a_obsoletes_b", "b_obsoletes_a"}


_VALID_VERDICTS = {"a_obsoletes_b", "b_obsoletes_a", "distinct"}


def _normalize(text: str) -> str:
    """Whitespace/case-normalised content for the cache key."""
    return " ".join((text or "").split()).strip().lower()


def _node_blob(node: ResearchNode) -> str:
    return _normalize(f"{node.name} {node.description or ''}")


def _pair_hash(a: ResearchNode, b: ResearchNode) -> str:
    """Order-independent, CONTENT-keyed hash for caching a pair's verdict
    (codex MAJOR 1).

    Keys on the sha256 of the SORTED pair of normalised
    ``name + description`` blobs, NOT node ids. So the same finding content
    reminted under different node ids hits the SAME warm cache file, and
    the cached ROLE verdict (``a_obsoletes_b`` is re-anchored to whichever
    side the caller passes as ``a``). We canonicalise the stored role to
    the SORTED-blob orientation so the file is independent of argument
    order; the caller maps it back to the live (a, b) roles.
    """
    blob_a, blob_b = _node_blob(a), _node_blob(b)
    lo, hi = sorted([blob_a, blob_b])
    raw = f"{lo}::{hi}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


# Cache verdict orientation is anchored to the SORTED-blob pair (lo, hi),
# independent of the live argument order. ``lo_obsoletes_hi`` /
# ``hi_obsoletes_lo`` / ``distinct``.
_LO_OBSOLETES_HI = "lo_obsoletes_hi"
_HI_OBSOLETES_LO = "hi_obsoletes_lo"
_CACHE_VERDICTS = {_LO_OBSOLETES_HI, _HI_OBSOLETES_LO, "distinct"}


def _verdict_to_cache_role(a: ResearchNode, b: ResearchNode, verdict: str) -> str:
    """Map a live ``a_obsoletes_b``/``b_obsoletes_a`` verdict to the
    sorted-blob orientation stored on disk."""
    if verdict == "distinct":
        return "distinct"
    blob_a, blob_b = _node_blob(a), _node_blob(b)
    a_is_lo = blob_a <= blob_b
    # winner = the obsoleter.
    a_wins = verdict == "a_obsoletes_b"
    winner_is_lo = a_wins == a_is_lo
    return _LO_OBSOLETES_HI if winner_is_lo else _HI_OBSOLETES_LO


def _cache_role_to_verdict(a: ResearchNode, b: ResearchNode, role: str) -> str:
    """Inverse of :func:`_verdict_to_cache_role` for the live (a, b)."""
    if role == "distinct":
        return "distinct"
    blob_a, blob_b = _node_blob(a), _node_blob(b)
    a_is_lo = blob_a <= blob_b
    lo_wins = role == _LO_OBSOLETES_HI
    a_wins = lo_wins == a_is_lo
    return "a_obsoletes_b" if a_wins else "b_obsoletes_a"


def _read_cached_judgement(
    path: Path, a: ResearchNode, b: ResearchNode
) -> Optional[SupersedeJudgement]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    role = str(payload.get("verdict") or "")
    if role not in _CACHE_VERDICTS:
        return None
    return SupersedeJudgement(
        verdict=_cache_role_to_verdict(a, b, role),
        rationale=str(payload.get("rationale") or ""),
    )


def _write_cached_judgement(
    path: Path,
    pair: Tuple[ResearchNode, ResearchNode],
    judgement: SupersedeJudgement,
) -> None:
    """Atomic write with PID+random suffix (matches session_graph._write_cache).

    Stores the ROLE verdict in the sorted-blob orientation (codex MAJOR
    1) — never concrete ids — so reminted ids hit the same warm cache.
    """
    a, b = pair
    payload = {
        "schema_version": 2,
        "verdict": _verdict_to_cache_role(a, b, judgement.verdict),
        "rationale": judgement.rationale,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(
        path.suffix + f".tmp.{os.getpid()}.{secrets.token_hex(4)}"
    )
    try:
        tmp.write_text(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.rename(path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


_SUPERSEDE_SYSTEM = (
    "You decide whether one research-session finding obsoletes another. "
    "Both findings come from the same project's compiled memory graph. "
    "Pick the single verdict that best fits."
)

_SUPERSEDE_USER_TEMPLATE = (
    "Finding A (id={a_id}):\n{a_name}\n\n"
    "Finding B (id={b_id}):\n{b_name}\n\n"
    "Return JSON shaped exactly like "
    '{{"verdict": "a_obsoletes_b" | "b_obsoletes_a" | "distinct", '
    '"rationale": "<one short sentence>"}}.'
)


def _ask_llm(
    client: LLMJsonClient,
    a: ResearchNode,
    b: ResearchNode,
) -> Optional[SupersedeJudgement]:
    """Call the JSON-constrained LLM client. ``None`` on any failure."""
    try:
        response = client.complete_json(
            system=_SUPERSEDE_SYSTEM,
            user=_SUPERSEDE_USER_TEMPLATE.format(
                a_id=a.id, a_name=a.name, b_id=b.id, b_name=b.name
            ),
            schema_name="supersede_judgement",
            cache_key="supersede-v1",
            max_retries=1,
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception("supersede: LLM call raised")
        return None
    if not isinstance(response, dict):
        return None
    verdict = str(response.get("verdict") or "").strip().lower()
    if verdict not in _VALID_VERDICTS:
        return None
    return SupersedeJudgement(
        verdict=verdict, rationale=str(response.get("rationale") or "")
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _session_id(node: ResearchNode) -> str:
    """Content-derived session id string for deterministic arbitration.

    Reads ``session_id`` from node metadata; ``""`` when absent. Kept
    self-contained (reinforce.py has its own copy — no cross-module import)
    so the supersede pass has no hidden dependencies.
    """
    sid = node.metadata.get("session_id") if node.metadata else None
    return str(sid) if sid else ""


def _deterministic_verdict(a: ResearchNode, b: ResearchNode) -> SupersedeJudgement:
    """Credential-free arbitration for a near-dup pair (KB-03).

    Callers pass the canonicalised ``a.id < b.id`` pair from
    :func:`_candidate_pairs`, so ``a`` is the smaller-id node. Pure function:
    reads ONLY immutable content fields (session-id metadata string, name
    length, id ordering) — no ``datetime.now()``, no RNG, no I/O — so two runs
    over the same graph yield byte-identical verdicts.

    Rule order:
      1. both have session_id and they DIFFER → the LATER session id wins,
         DECISIVELY in both directions (never fall through to name length —
         the newer finding must obsolete the older one regardless of name).
      2. ``len(b.name) > len(a.name) * 1.1`` → ``b_obsoletes_a``
         (more specific / longer name wins) — only when session ids tie/absent.
      3. else → ``a_obsoletes_b`` (stable smaller-id fallback).
    """
    sid_a, sid_b = _session_id(a), _session_id(b)
    if sid_a and sid_b and sid_a != sid_b:
        # Later session id wins, decisively (Codex blocker: the older case
        # must NOT fall through to name length and let an older finding win).
        if sid_b > sid_a:
            return SupersedeJudgement(verdict="b_obsoletes_a", rationale="newer session id")
        return SupersedeJudgement(verdict="a_obsoletes_b", rationale="newer session id")
    if len(b.name) > len(a.name) * 1.1:
        return SupersedeJudgement(
            verdict="b_obsoletes_a", rationale="more specific name"
        )
    return SupersedeJudgement(verdict="a_obsoletes_b", rationale="stable id ordering")


def _finding_groups(
    nodes: Sequence[ResearchNode],
) -> Dict[str, List[ResearchNode]]:
    groups: Dict[str, List[ResearchNode]] = {}
    finding_values = {t.value for t in SESSION_FINDING_TYPES}
    for node in nodes:
        kind = node.type.value if hasattr(node.type, "value") else str(node.type)
        if kind not in finding_values:
            continue
        groups.setdefault(kind, []).append(node)
    return groups


# NOTE (Phase 5.1): candidate generation stays Jaccard-only. Model2Vec
# embedding-based candidate enrichment (retrieval/hybrid.active_embedding_backend,
# backend_is_semantic) is DEFERRED to Phase 6+ — Jaccard is deterministic
# and byte-idempotent; embedding candidates add a model-version dependency.
def _candidate_pairs(
    nodes: Sequence[ResearchNode], threshold: float
) -> List[Tuple[ResearchNode, ResearchNode, float]]:
    """All ``(a, b, sim)`` with ``sim > threshold`` and ``a.id < b.id``."""
    pairs: List[Tuple[ResearchNode, ResearchNode, float]] = []
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            sim = jaccard(a.name, b.name)
            if sim > threshold:
                lo, hi = (a, b) if a.id < b.id else (b, a)
                pairs.append((lo, hi, sim))
    return pairs


def run_supersede_pass(
    graph: ResearchGraph,
    *,
    json_client: Optional[LLMJsonClient] = None,
    cache_dir: Path,
    similarity_threshold: float = 0.55,
) -> ResearchGraph:
    """Mint ``supersedes`` edges; returns the mutated graph.

    DEFAULT-ON (KB-03): with no ``json_client`` the pass mints
    deterministic, content-derived, byte-idempotent edges via
    :func:`_deterministic_verdict`. When a ``json_client`` IS present the
    LLM verdict (content-keyed disk cache → ``_ask_llm``) takes precedence
    and OVERRIDES the deterministic fallback; the deterministic verdict is
    used only when the LLM is unavailable or returns no valid verdict. All
    existing nodes and edges are preserved; new edges are appended in-place.
    """
    groups = _finding_groups(graph.nodes)
    if not groups:
        return graph

    existing_edges: Set[Tuple[str, str, str]] = {
        (e.source, e.type, e.target) for e in graph.edges
    }
    minted = 0
    for kind, nodes in groups.items():
        pairs = _candidate_pairs(nodes, similarity_threshold)
        for a, b, sim in pairs:
            judgement: Optional[SupersedeJudgement] = None
            if json_client is not None:
                # LLM path — content-keyed disk cache (unchanged semantics).
                # Only touch the filesystem when a client is actually present.
                cache_path = cache_dir / f"{_pair_hash(a, b)}.json"
                if cache_path.exists():
                    judgement = _read_cached_judgement(cache_path, a, b)
                if judgement is None:
                    judgement = _ask_llm(json_client, a, b)
                    if judgement is not None:
                        _write_cached_judgement(cache_path, (a, b), judgement)
            # LLM unavailable/failed → deterministic fallback (DEFAULT path).
            # Never cached to disk: recomputed cheaply and purely.
            if judgement is None:
                judgement = _deterministic_verdict(a, b)

            if not judgement.is_supersede():
                continue
            # Canonicalise: "source supersedes target" means source is
            # the newer/better finding and target is the obsolete one.
            if judgement.verdict == "a_obsoletes_b":
                source, target = a, b
            else:
                source, target = b, a
            edge_key = (source.id, SUPERSEDE_EDGE, target.id)
            if edge_key in existing_edges:
                continue
            graph.edges.append(
                ResearchEdge(
                    source=source.id,
                    target=target.id,
                    type=SUPERSEDE_EDGE,
                    evidence=judgement.rationale or None,
                    metadata={
                        "extractor": "memory.supersede",
                        "similarity": round(sim, 4),
                        "kind": kind,
                    },
                )
            )
            existing_edges.add(edge_key)
            minted += 1

    if minted:
        logger.info("memory.supersede: minted %d superseded_by edges", minted)
    return graph

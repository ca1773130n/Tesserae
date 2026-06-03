"""Post-compile contradiction RESOLUTION pass (KB-04).

lint's ``_check_contradicting_claims`` is an info-only *probe*: it flags
pairs of ``PerformanceClaim`` / ``ComparisonClaim`` nodes whose
descriptions carry opposite directional language (one ``outperforms`` the
other ``is outperformed by``) and share a topic. It never resolves them.

This pass upgrades that probe into an LLM-*arbitrated* resolution. For
each detected contradicting pair it asks the LLM which claim wins, then
mints a deterministic ``resolved_by`` edge from the losing claim to the
winning one. lint then demotes resolved pairs to ``info`` and raises
unresolved pairs to ``warning``.

Determinism (Pitfall 5): the LLM verdict is cached on disk keyed on the
sha256 of the *content* of the pair (``left_name + right_name +
left_description[:100]``), NOT on node ids. Identical claim content =>
identical cache hit => identical edges, with no ``datetime.now()`` / RNG
anywhere, so a wired compile keeps ``graph.json`` byte-identical across
runs.

REPLICATES (does not import-modify) the supersede.py arbitration shape
(``_pair_hash`` / ``_read_cached_judgement`` / ``_write_cached_judgement``
/ ``_ask_llm`` / ``run_supersede_pass``). Strictly additive: no nodes are
deleted, only ``resolved_by`` edges are added.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..llm_json import LLMJsonClient
from ..research_graph import ResearchEdge, ResearchGraph, ResearchNode

logger = logging.getLogger(__name__)

RESOLVED_BY_EDGE = "resolved_by"
"""Edge minted by this pass. Direction: ``source resolved_by target`` —
``source`` is the losing claim, ``target`` is the winning claim that
resolves the contradiction. Already in ``ALLOWED_EDGE_TYPES``."""

# Directional markers — kept NARROW (05-RESEARCH Open Question 1): only the
# explicit ``outperforms`` / ``is outperformed by`` antonym pair, mirroring
# lint's existing precision-first probe.
_LEFT_MARKER = "outperforms"
_RIGHT_MARKER = "is outperformed by"

# Claim node kinds this pass considers (matches lint's candidate filter).
_CLAIM_KINDS = {"PerformanceClaim", "ComparisonClaim"}

# Word characters only — avoids matching punctuation as a token.
_TOKEN_SPLIT_CHARS = " \t\n\r\f\v.,;:!?()[]{}\"'`/\\|<>@#$%^&*+=~"

_TOPIC_STOPWORDS = {
    "the", "a", "an", "of", "on", "in", "to", "is", "are", "was", "were",
    "and", "or", "by", "for", "with", "at", "as", "outperforms", "outperformed",
    "than", "vs", "versus", "it", "its", "that", "this", "be", "been",
}


# ---------------------------------------------------------------------------
# Detection (narrow, content-based — mirrors lint.py 444-490 over ResearchNode)
# ---------------------------------------------------------------------------


def _node_text(node: ResearchNode) -> str:
    """Name + description + evidence text used for marker / topic matching."""
    parts: List[str] = [node.name or "", node.description or ""]
    meta = node.metadata or {}
    evidence = meta.get("evidence") or meta.get("text")
    if isinstance(evidence, str):
        parts.append(evidence)
    return " ".join(p for p in parts if p)


def _topic_tokens(text: str) -> Set[str]:
    if not text:
        return set()
    buf = []
    for ch in text.lower():
        buf.append(" " if ch in _TOKEN_SPLIT_CHARS else ch)
    return {
        tok
        for tok in "".join(buf).split()
        if len(tok) > 1 and tok not in _TOPIC_STOPWORDS
    }


def _share_topic(left: str, right: str) -> bool:
    """At least two shared content tokens (matches lint's _share_topic)."""
    return len(_topic_tokens(left) & _topic_tokens(right)) >= 2


def _kind(node: ResearchNode) -> str:
    return node.type.value if hasattr(node.type, "value") else str(node.type)


def detect_contradicting_pairs(
    graph: ResearchGraph,
) -> List[Tuple[ResearchNode, ResearchNode]]:
    """Return ``(left, right)`` contradicting claim pairs, deterministically.

    ``left`` carries ``outperforms``; ``right`` carries ``is outperformed
    by``; they share a topic and come from different sources. Ordered by
    ``(left.id, right.id)`` so the pass is byte-stable.
    """
    candidates = sorted(
        (n for n in graph.nodes if _kind(n) in _CLAIM_KINDS),
        key=lambda n: n.id,
    )
    pairs: List[Tuple[ResearchNode, ResearchNode]] = []
    seen: Set[Tuple[str, str]] = set()
    for i, left in enumerate(candidates):
        left_text = _node_text(left)
        if _LEFT_MARKER not in left_text.lower():
            continue
        for right in candidates[i + 1 :]:
            if left.source_path and left.source_path == right.source_path:
                continue
            right_text = _node_text(right)
            if _RIGHT_MARKER not in right_text.lower():
                continue
            if not _share_topic(left_text, right_text):
                continue
            key = tuple(sorted([left.id, right.id]))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((left, right))
    return pairs


# ---------------------------------------------------------------------------
# LLM arbitration + content-keyed disk cache (replicates supersede.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContradictionVerdict:
    """LLM arbitration outcome for a contradicting pair.

    ``winner_id`` / ``loser_id`` are node ids drawn from the pair.
    """

    winner_id: str
    loser_id: str
    rationale: str = ""


def _content_hash(left: ResearchNode, right: ResearchNode) -> str:
    """Content-keyed cache hash (Pitfall 5): hashes the claims' CONTENT,
    not their ids, so identical claims reuse one cached verdict."""
    raw = f"{left.name}::{right.name}::{(left.description or '')[:100]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _read_cached_verdict(
    path: Path, valid_ids: Set[str]
) -> Optional[ContradictionVerdict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    winner = str(payload.get("winner_id") or "")
    loser = str(payload.get("loser_id") or "")
    if winner not in valid_ids or loser not in valid_ids or winner == loser:
        return None
    return ContradictionVerdict(
        winner_id=winner,
        loser_id=loser,
        rationale=str(payload.get("rationale") or ""),
    )


def _write_cached_verdict(path: Path, verdict: ContradictionVerdict) -> None:
    """Atomic write with PID+random suffix (matches supersede._write_cache)."""
    payload = {
        "schema_version": 1,
        "winner_id": verdict.winner_id,
        "loser_id": verdict.loser_id,
        "rationale": verdict.rationale,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
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


_SYSTEM = (
    "You arbitrate a contradiction between two research performance claims. "
    "One claim asserts model A outperforms model B; the other asserts the "
    "reverse. Decide which claim is better supported and should win."
)

_USER_TEMPLATE = (
    "Claim LEFT (id={left_id}):\n{left_name}\n{left_desc}\n\n"
    "Claim RIGHT (id={right_id}):\n{right_name}\n{right_desc}\n\n"
    "Return JSON shaped exactly like "
    '{{"winner_id": "<left_id or right_id>", '
    '"loser_id": "<the other id>", '
    '"rationale": "<one short sentence>"}}.'
)


def _ask_llm(
    client: LLMJsonClient, left: ResearchNode, right: ResearchNode
) -> Optional[ContradictionVerdict]:
    """Call the JSON-constrained client. ``None`` on any failure."""
    valid = {left.id, right.id}
    try:
        response = client.complete_json(
            system=_SYSTEM,
            user=_USER_TEMPLATE.format(
                left_id=left.id,
                left_name=left.name,
                left_desc=(left.description or "")[:400],
                right_id=right.id,
                right_name=right.name,
                right_desc=(right.description or "")[:400],
            ),
            schema_name="contradiction_verdict",
            cache_key="contradiction-v1",
            max_retries=1,
        )
    except Exception:  # pragma: no cover — defensive
        logger.exception("contradiction: LLM call raised")
        return None
    if not isinstance(response, dict):
        return None
    winner = str(response.get("winner_id") or "").strip()
    loser = str(response.get("loser_id") or "").strip()
    if winner not in valid or loser not in valid or winner == loser:
        return None
    return ContradictionVerdict(
        winner_id=winner,
        loser_id=loser,
        rationale=str(response.get("rationale") or ""),
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_contradiction_resolution(
    graph: ResearchGraph,
    *,
    llm: Optional[LLMJsonClient],
    cache_dir: Path | str,
) -> Tuple[ResearchGraph, Dict[str, str]]:
    """Arbitrate detected contradicting pairs into ``resolved_by`` edges.

    Returns the mutated graph plus a ``{node_id: confidence}`` map
    (winner => ``"high"``, loser => ``"low"``). No-op (graph unchanged,
    empty map) when ``llm`` is ``None`` — matching ``run_supersede_pass``'s
    no-client behaviour. A warm content-keyed cache mints the same edges
    with zero LLM calls.
    """
    confidence: Dict[str, str] = {}
    if llm is None:
        return graph, confidence

    pairs = detect_contradicting_pairs(graph)
    if not pairs:
        return graph, confidence

    cache_path_dir = Path(cache_dir)
    existing_edges: Set[Tuple[str, str, str]] = {
        (e.source, e.type, e.target) for e in graph.edges
    }
    minted = 0
    for left, right in pairs:
        valid = {left.id, right.id}
        cache_file = cache_path_dir / f"{_content_hash(left, right)}.json"
        verdict: Optional[ContradictionVerdict] = None
        if cache_file.exists():
            verdict = _read_cached_verdict(cache_file, valid)
        if verdict is None:
            verdict = _ask_llm(llm, left, right)
            if verdict is None:
                continue
            _write_cached_verdict(cache_file, verdict)

        edge_key = (verdict.loser_id, RESOLVED_BY_EDGE, verdict.winner_id)
        confidence[verdict.winner_id] = "high"
        confidence[verdict.loser_id] = "low"
        if edge_key in existing_edges:
            continue
        graph.edges.append(
            ResearchEdge(
                source=verdict.loser_id,
                target=verdict.winner_id,
                type=RESOLVED_BY_EDGE,
                evidence=verdict.rationale or None,
                metadata={"extractor": "memory.contradiction"},
            )
        )
        existing_edges.add(edge_key)
        minted += 1

    if minted:
        logger.info("memory.contradiction: minted %d resolved_by edges", minted)
    return graph, confidence

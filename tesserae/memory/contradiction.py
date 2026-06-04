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

    Roles are assigned by the CLAIM MARKERS, NOT by id sort order: ``left``
    is whichever node carries ``outperforms``; ``right`` is whichever
    carries ``is outperformed by``. We compare every UNORDERED pair, so a
    contradiction is detected regardless of how the two node ids sort
    (codex MAJOR 2). They must share a topic and come from different
    sources. The output list is sorted by ``(left.id, right.id)`` so the
    pass stays byte-stable.
    """
    candidates = sorted(
        (n for n in graph.nodes if _kind(n) in _CLAIM_KINDS),
        key=lambda n: n.id,
    )
    pairs: List[Tuple[ResearchNode, ResearchNode]] = []
    seen: Set[Tuple[str, str]] = set()
    for i, first in enumerate(candidates):
        first_text = first_lower = None  # lazy
        for second in candidates[i + 1 :]:
            if first.source_path and first.source_path == second.source_path:
                continue
            if first_text is None:
                first_text = _node_text(first)
                first_lower = first_text.lower()
            second_text = _node_text(second)
            second_lower = second_text.lower()

            # Assign left/right by marker, independent of id ordering. The
            # contradicting pair needs one ``outperforms`` claim and one
            # ``is outperformed by`` claim.
            if _LEFT_MARKER in first_lower and _RIGHT_MARKER in second_lower:
                left, right = first, second
                left_text, right_text = first_text, second_text
            elif _LEFT_MARKER in second_lower and _RIGHT_MARKER in first_lower:
                left, right = second, first
                left_text, right_text = second_text, first_text
            else:
                continue

            if not _share_topic(left_text, right_text):
                continue
            key = tuple(sorted([left.id, right.id]))
            if key in seen:
                continue
            seen.add(key)
            pairs.append((left, right))
    pairs.sort(key=lambda pr: (pr[0].id, pr[1].id))
    return pairs


# ---------------------------------------------------------------------------
# LLM arbitration + content-keyed disk cache (replicates supersede.py)
# ---------------------------------------------------------------------------


# Role verdicts: who wins the contradiction, expressed relative to the
# (left, right) ROLES — NOT to concrete node ids. ``left_wins`` => the
# ``outperforms`` claim wins; ``right_wins`` => the ``is outperformed by``
# claim wins; ``distinct`` => no resolution.
_LEFT_WINS = "left_wins"
_RIGHT_WINS = "right_wins"
_DISTINCT = "distinct"
_VALID_ROLE_VERDICTS = {_LEFT_WINS, _RIGHT_WINS, _DISTINCT}


@dataclass(frozen=True)
class ContradictionVerdict:
    """LLM arbitration outcome for a contradicting pair.

    ``role`` is one of ``left_wins`` / ``right_wins`` / ``distinct`` and is
    stored content-keyed (codex MAJOR 1) so identical claim content reuses
    the cached verdict under ANY node ids. ``winner_id`` / ``loser_id`` are
    the concrete ids resolved from ``role`` for the current pair (empty
    when ``role == "distinct"``).
    """

    role: str
    winner_id: str = ""
    loser_id: str = ""
    rationale: str = ""

    def is_resolved(self) -> bool:
        return self.role in {_LEFT_WINS, _RIGHT_WINS}

    @classmethod
    def from_role(
        cls,
        role: str,
        left: ResearchNode,
        right: ResearchNode,
        rationale: str = "",
    ) -> "ContradictionVerdict":
        if role == _LEFT_WINS:
            return cls(role, left.id, right.id, rationale)
        if role == _RIGHT_WINS:
            return cls(role, right.id, left.id, rationale)
        return cls(_DISTINCT, "", "", rationale)


def _normalize(text: str) -> str:
    """Whitespace/case-normalised claim content for the cache key."""
    return " ".join((text or "").split()).strip().lower()


def _content_hash(left: ResearchNode, right: ResearchNode) -> str:
    """Order-independent, content-keyed cache hash (codex MAJOR 1).

    Hashes the sha256 of the SORTED pair of normalised
    ``name + description`` blobs, so the SAME claim content reminted under
    DIFFERENT node ids lands on the SAME cache file. We sort the two sides
    so id/argument order never changes the key; the stored ``role`` is then
    re-anchored to the live (left, right) roles by the caller.
    """
    left_blob = _normalize(f"{left.name} {left.description or ''}")
    right_blob = _normalize(f"{right.name} {right.description or ''}")
    a, b = sorted([left_blob, right_blob])
    raw = f"{a}::{b}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _read_cached_verdict(
    path: Path, left: ResearchNode, right: ResearchNode
) -> Optional[ContradictionVerdict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    role = str(payload.get("role") or "")
    if role not in _VALID_ROLE_VERDICTS:
        return None
    return ContradictionVerdict.from_role(
        role, left, right, rationale=str(payload.get("rationale") or "")
    )


def _write_cached_verdict(path: Path, role: str, rationale: str) -> None:
    """Atomic write with PID+random suffix (matches supersede._write_cache).

    Persists the ROLE verdict, NOT concrete ids — so the cache file is
    valid for any node ids carrying the same claim content.
    """
    payload = {
        "schema_version": 2,
        "role": role,
        "rationale": rationale,
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
    """Call the JSON-constrained client. ``None`` on any failure.

    The wire protocol still returns concrete ``winner_id`` / ``loser_id``;
    we immediately collapse that to the content-keyed ROLE so the cache
    (and downstream determinism) never depends on the concrete ids.
    """
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
    role = _LEFT_WINS if winner == left.id else _RIGHT_WINS
    return ContradictionVerdict.from_role(
        role, left, right, rationale=str(response.get("rationale") or "")
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
        cache_file = cache_path_dir / f"{_content_hash(left, right)}.json"
        verdict: Optional[ContradictionVerdict] = None
        if cache_file.exists():
            verdict = _read_cached_verdict(cache_file, left, right)
        if verdict is None:
            verdict = _ask_llm(llm, left, right)
            if verdict is None:
                continue
            _write_cached_verdict(cache_file, verdict.role, verdict.rationale)

        if not verdict.is_resolved():
            continue
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

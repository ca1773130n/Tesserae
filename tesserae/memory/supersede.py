"""Post-compile ``superseded_by`` edge pass for session-finding nodes.

Inspired by Graphiti's superseded-edge pattern and A-MEM's Zettelkasten
note-evolution loop (see ``/tmp/tesserae-innovation/03-memory.md``).

Pass shape (opt-in via ``TESSERAE_SUPERSEDE_PASS=true``):

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
    """Read the opt-in env flag.

    ``TESSERAE_SUPERSEDE_PASS`` accepts the usual truthy spellings.
    """
    raw = (os.environ.get("TESSERAE_SUPERSEDE_PASS") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


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


def _pair_hash(a: ResearchNode, b: ResearchNode) -> str:
    """Order-independent stable hash for caching a pair's verdict."""
    left, right = sorted([a.id, b.id])
    raw = f"{left}::{right}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _read_cached_judgement(path: Path) -> Optional[SupersedeJudgement]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    verdict = str(payload.get("verdict") or "")
    if verdict not in _VALID_VERDICTS:
        return None
    return SupersedeJudgement(
        verdict=verdict, rationale=str(payload.get("rationale") or "")
    )


def _write_cached_judgement(
    path: Path,
    pair: Tuple[ResearchNode, ResearchNode],
    judgement: SupersedeJudgement,
) -> None:
    """Atomic write with PID+random suffix (matches session_graph._write_cache)."""
    a, b = pair
    payload = {
        "schema_version": 1,
        "a_id": a.id,
        "b_id": b.id,
        "verdict": judgement.verdict,
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
    json_client: Optional[LLMJsonClient],
    cache_dir: Path,
    similarity_threshold: float = 0.55,
) -> ResearchGraph:
    """Mint ``supersedes`` edges; returns the mutated graph.

    The pass is a no-op when ``json_client`` is ``None`` (we still need
    a backend to render the obsolete/distinct judgement). All existing
    nodes and edges are preserved; new edges are appended in-place.
    """
    if json_client is None:
        return graph

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
            cache_path = cache_dir / f"{_pair_hash(a, b)}.json"
            judgement: Optional[SupersedeJudgement] = None
            if cache_path.exists():
                judgement = _read_cached_judgement(cache_path)
            if judgement is None:
                judgement = _ask_llm(json_client, a, b)
                if judgement is None:
                    continue
                _write_cached_judgement(cache_path, (a, b), judgement)

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

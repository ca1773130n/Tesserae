"""Post-extraction contrast pass — mints REASONING edges the extractor cannot see.

Why a post-pass and not a prompt change
---------------------------------------
Measured on a real 5,197-node / 15,284-edge graph: 73% of edges are
structural/membership ("X appeared near Y") and only 8% carry reasoning.
Four edge types the ontology defines have ZERO instances:
``contradicts_claim``, ``derived_from``, ``attributes_improvement_to``,
``criticizes``. Two measurements decided the shape of this pass:

1. The extractor is strictly PER-DOCUMENT, but 601 of 803 candidate contrast
   pairs (75%) are CROSS-document. A prompt change is structurally blind to
   three quarters of the signal.
2. ``llm_extractor``'s ``cache_key`` hashes guidance + kind + path + text but
   NOT the prompt template. Editing the prompt would invalidate no cached
   extraction, so old documents would silently never get the new edges —
   a heterogeneous graph.

So the pass runs AFTER extraction, after ``graph.canonicalized()`` (node ids
final) and BEFORE graph.json is written.

Candidate generation is rare-token blocking, not O(n^2) LLM
-----------------------------------------------------------
Two blocks (claims, session findings). Per block: tokenise ``name +
description``, build a token -> ids inverted index, DROP tokens whose document
frequency exceeds 2% of the block, emit pairs sharing >= 3 surviving rare
tokens. Measured with THIS implementation on that graph: 1,414 claim pairs +
519 finding pairs, versus 392,941 + 340,725 all-pairs — a ~380x reduction
with no embeddings, no new dependency and no model-version coupling (the same
reasoning that made supersede.py defer Model2Vec). The LLM budget cap keeps
the call count at 200 regardless.

Blocking RECALL is unmeasured: rare-token overlap finds pairs that share
vocabulary and will miss a genuine contradiction phrased in disjoint words
("X degrades throughput" vs "X is a net win"). Those counts are precision-side
yield, not recall.

Default OFF
-----------
Unlike the supersede pass, this one is opt-IN (``TESSERAE_CONTRAST_PASS``).
There is no honest deterministic fallback for semantic contrast: a heuristic
here would mint confident nonsense at scale. No credentials, no flag, no
edges, no code executed.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from ..llm_json import LLMJsonClient
from ..research_graph import (
    SESSION_FINDING_TYPES,
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

# Read-only import of a PURE function. Deliberately not a third copy: unlike
# the cache/role helpers below (which contradiction.py established as
# "REPLICATES, does not import-modify"), the tokeniser has no per-pass
# semantics to diverge — and the whole point is that this pass blocks on the
# SAME token definition the supersede pass uses.
from .supersede import _tokenise

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

CLAIM_BLOCK = "claims"
FINDING_BLOCK = "findings"

_CLAIM_KINDS = frozenset(
    t.value
    for t in (
        ResearchNodeType.CLAIM,
        ResearchNodeType.CONTRIBUTION_CLAIM,
        ResearchNodeType.PERFORMANCE_CLAIM,
        ResearchNodeType.COMPARISON_CLAIM,
        ResearchNodeType.LIMITATION_CLAIM,
        ResearchNodeType.CAUSAL_CLAIM,
    )
)

# Findings MINUS TODO/Question: a TODO contradicting a TODO is a scheduling
# fact, not a reasoning fact, and questions assert nothing to contrast.
_FINDING_KINDS = frozenset(
    t.value
    for t in SESSION_FINDING_TYPES
    if t not in {ResearchNodeType.SESSION_TODO, ResearchNodeType.SESSION_QUESTION}
)

_BLOCK_KINDS: Dict[str, frozenset] = {
    CLAIM_BLOCK: _CLAIM_KINDS,
    FINDING_BLOCK: _FINDING_KINDS,
}


# ---------------------------------------------------------------------------
# Verdict vocabulary
# ---------------------------------------------------------------------------

# Deliberately tiny, and restricted to edge types the ontology DEFINES but has
# zero instances of. Anything not in this table is rejected — including two
# exclusions that are load-bearing:
#
# ponytail: ``compares_against`` is ABSENT on purpose. In this ontology it is a
#   Method<->Method edge; minting it between two claims would be a semantic
#   lie that reads correct in a graph query. Ceiling: claim-vs-claim comparison
#   is simply not expressible today. Upgrade path: add a separate Method-block
#   pass once claim->Method linkage densifies past the current 127
#   ``uses`` -> MethodologicalConcept edges — do NOT smuggle it in here.
#
# ``supersedes`` is ABSENT because memory/supersede.py already owns it (60
#   edges on the real graph). Re-litigating it here would double-mint.
_RELATION_TO_EDGE: Dict[str, Optional[str]] = {
    "contradicts": "contradicts_claim",
    "contradicts_claim": "contradicts_claim",
    "derived_from": "derived_from",
    "attributes_improvement_to": "attributes_improvement_to",
    "criticizes": "criticizes",
    "none": None,
}

_VALID_DIRECTIONS = {"a_to_b", "b_to_a"}

_DEFAULT_MAX_PAIRS = 200


def contrast_pass_enabled() -> bool:
    """Read the opt-IN env flag. Default OFF.

    Mirrors ``project._env_truthy``: only ``1/true/yes/on`` enable the pass.
    """
    return os.environ.get("TESSERAE_CONTRAST_PASS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def contrast_max_pairs() -> int:
    """LLM-call budget from ``TESSERAE_CONTRAST_MAX_PAIRS``; 0 disables."""
    raw = (os.environ.get("TESSERAE_CONTRAST_MAX_PAIRS") or "").strip()
    if not raw:
        return _DEFAULT_MAX_PAIRS
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_MAX_PAIRS


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidatePair:
    """One blocked pair, ranked. ``lo_id < hi_id`` always."""

    lo_id: str
    hi_id: str
    shared: int
    score: float
    block: str

    def rank_key(self) -> Tuple[float, str, str]:
        # Total order: -score first, then the (unique) id pair. No ties are
        # possible, so the top-N slice cannot depend on input list order.
        return (-self.score, self.lo_id, self.hi_id)


def _blob(node: ResearchNode) -> str:
    return f"{node.name} {node.description or ''}"


def candidate_pairs(
    graph: ResearchGraph,
    *,
    block: str,
    df_cap: float = 0.02,
    min_shared: int = 3,
) -> List[CandidatePair]:
    """Rare-token blocking over one node block, ranked best-first.

    Tokens whose document frequency exceeds ``df_cap`` of the block are
    DROPPED before pairing — that is what prevents the quadratic blowup, not
    ``min_shared`` alone: without it every claim mentioning "model" pairs with
    every other one.

    Ranking is ``sum(1/df)`` over the shared surviving tokens, accumulated in
    SORTED token order so the floating-point sum is reproducible, then rounded
    to 6 places before it enters the sort key.
    """
    kinds = _BLOCK_KINDS.get(block)
    if kinds is None:
        raise ValueError(f"Unknown contrast block: {block!r}")

    members = sorted(
        (n for n in graph.nodes if getattr(n.type, "value", str(n.type)) in kinds),
        key=lambda n: n.id,
    )
    if len(members) < 2:
        return []

    # Inverted index built over the SORTED member list, so every posting list
    # is itself sorted and pair enumeration is order-independent.
    postings: Dict[str, List[str]] = {}
    for node in members:
        for token in sorted(_tokenise(_blob(node))):
            postings.setdefault(token, []).append(node.id)

    # ``int()`` on a stable count — integer arithmetic, no float drift.
    # Floor of 2: a token shared by exactly two nodes is the rarest useful
    # signal there is, and a cap below 2 would drop every pair.
    cap = max(2, int(len(members) * df_cap))

    pair_tokens: Dict[Tuple[str, str], List[str]] = {}
    for token in sorted(postings):
        ids = postings[token]
        if len(ids) > cap:
            continue  # too common to carry signal
        for lo, hi in itertools.combinations(ids, 2):
            pair_tokens.setdefault((lo, hi), []).append(token)

    pairs: List[CandidatePair] = []
    for (lo, hi), tokens in pair_tokens.items():
        if len(tokens) < min_shared:
            continue
        # ``tokens`` is already in sorted order (outer loop iterates sorted
        # postings), so this accumulation order is fixed across runs.
        score = round(sum(1.0 / len(postings[t]) for t in tokens), 6)
        pairs.append(
            CandidatePair(lo_id=lo, hi_id=hi, shared=len(tokens), score=score, block=block)
        )
    pairs.sort(key=CandidatePair.rank_key)
    return pairs


# ---------------------------------------------------------------------------
# Cache (REPLICATES supersede.py's shape — does not import-modify it)
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _node_blob(node: ResearchNode) -> str:
    return _normalize(_blob(node))


def _pair_hash(a: ResearchNode, b: ResearchNode) -> str:
    """Order-independent, CONTENT-keyed cache hash.

    Keys on the sha256 of the SORTED pair of normalised ``name + description``
    blobs, NOT node ids — so identical content reminted under different ids
    still hits the warm file and an incremental compile is never re-billed.
    """
    lo, hi = sorted([_node_blob(a), _node_blob(b)])
    return hashlib.sha256(f"{lo}::{hi}".encode("utf-8")).hexdigest()[:16]


_LO_TO_HI = "lo_to_hi"
_HI_TO_LO = "hi_to_lo"
_CACHE_DIRECTIONS = {_LO_TO_HI, _HI_TO_LO}


@dataclass(frozen=True)
class ContrastVerdict:
    relation: str  # a key of _RELATION_TO_EDGE
    direction: str  # "a_to_b" | "b_to_a"
    rationale: str = ""
    # Empty for a judged verdict; otherwise names WHY the call produced no
    # edge. A rejection is a real, cacheable outcome — see _ask_llm.
    rejected: str = ""


def _direction_to_cache_role(a: ResearchNode, b: ResearchNode, direction: str) -> str:
    """Re-anchor a live ``a_to_b``/``b_to_a`` direction to the sorted-blob
    orientation stored on disk, so argument order can never flip a minted edge."""
    a_is_lo = _node_blob(a) <= _node_blob(b)
    a_is_source = direction == "a_to_b"
    return _LO_TO_HI if a_is_source == a_is_lo else _HI_TO_LO


def _cache_role_to_direction(a: ResearchNode, b: ResearchNode, role: str) -> str:
    """Inverse of :func:`_direction_to_cache_role` for the live ``(a, b)``."""
    a_is_lo = _node_blob(a) <= _node_blob(b)
    lo_is_source = role == _LO_TO_HI
    return "a_to_b" if lo_is_source == a_is_lo else "b_to_a"


def _read_cached_verdict(
    path: Path, a: ResearchNode, b: ResearchNode
) -> Optional[ContrastVerdict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    relation = str(payload.get("relation") or "")
    if relation not in _RELATION_TO_EDGE:
        return None
    if relation == "none":
        return ContrastVerdict(
            relation="none",
            direction="a_to_b",
            rationale="",
            rejected=str(payload.get("rejected") or ""),
        )
    role = str(payload.get("direction") or "")
    if role not in _CACHE_DIRECTIONS:
        return None
    return ContrastVerdict(
        relation=relation,
        direction=_cache_role_to_direction(a, b, role),
        rationale=str(payload.get("rationale") or ""),
    )


def _write_cached_verdict(
    path: Path, pair: Tuple[ResearchNode, ResearchNode], verdict: ContrastVerdict
) -> None:
    """Atomic tmp+rename write with PID+token suffix (matches supersede.py)."""
    a, b = pair
    # ``rejected`` is additive: readers ignore unknown keys and default it to
    # "", so warm caches written before this key existed stay valid. No
    # schema_version bump — nothing about the old payloads became wrong.
    payload = {
        "schema_version": 1,
        "relation": verdict.relation,
        "direction": (
            "none"
            if verdict.relation == "none"
            else _direction_to_cache_role(a, b, verdict.direction)
        ),
        "rationale": verdict.rationale,
        "rejected": verdict.rejected,
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


# ---------------------------------------------------------------------------
# LLM judgement
# ---------------------------------------------------------------------------

_CONTRAST_SYSTEM = (
    "You judge the REASONING relationship between two statements taken from a "
    "research knowledge graph. Quoted source sentences are provided; rely on "
    "them, not on the summarised statement text. Answer 'none' unless the "
    "relationship is explicit in the quoted evidence — a wrong edge is far "
    "more costly than a missing one."
)

_CONTRAST_USER_TEMPLATE = (
    "Statement A:\n{a_text}\n"
    "Quoted source sentences for A:\n{a_evidence}\n\n"
    "Statement B:\n{b_text}\n"
    "Quoted source sentences for B:\n{b_evidence}\n\n"
    "Return JSON shaped exactly like "
    '{{"relation": "contradicts" | "derived_from" | '
    '"attributes_improvement_to" | "criticizes" | "none", '
    '"direction": "a_to_b" | "b_to_a", '
    '"rationale": "<one short sentence>"}}. '
    "direction names the SOURCE of the relation: a_to_b means A relates to B."
)


def _evidence_index(graph: ResearchGraph) -> Dict[str, List[str]]:
    """``node id -> sorted verbatim EvidenceSpan texts`` via ``evidenced_by``.

    This is the "evidence originating outside the graph" requirement in
    practice: the judge reads the quoted source sentence, not a prior LLM's
    summary of it. One adjacency dict, built once.
    """
    nodes = {n.id: n for n in graph.nodes}
    index: Dict[str, List[str]] = {}
    for edge in graph.edges:
        if edge.type != "evidenced_by":
            continue
        span = nodes.get(edge.target)
        if span is None:
            continue
        text = (span.description or span.name or "").strip()
        if text:
            index.setdefault(edge.source, []).append(text)
    return {nid: sorted(set(texts)) for nid, texts in index.items()}


def _evidence_block(node_id: str, index: Dict[str, List[str]]) -> str:
    spans = index.get(node_id) or []
    if not spans:
        return "(no quoted source sentence available)"
    return "\n".join(f"- {text}" for text in spans)


def _rejection(why: str) -> ContrastVerdict:
    """A rejection expressed as the verdict it actually is: no edge."""
    return ContrastVerdict(relation="none", direction="a_to_b", rejected=why)


def _ask_llm(
    client: LLMJsonClient,
    a: ResearchNode,
    b: ResearchNode,
    index: Dict[str, List[str]],
) -> ContrastVerdict:
    """Judge one pair. ALWAYS returns a verdict, never ``None``.

    "No edge" is a result, not the absence of one. Returning ``None`` here is
    what let the caller skip the cache write, so every rejection was re-billed
    on every compile forever and — worse — a pair that failed transiently on
    compile N could mint an edge on compile N+1 from byte-identical inputs.
    Both holes close by making the rejection a cacheable value.

    ponytail: a transport failure is cached exactly like a judged ``none``, so
    one provider 429 permanently suppresses that pair's edge. Ceiling: the
    cache cannot distinguish "the model said no" from "we never asked". The
    reason is recorded in ``rejected`` so the upgrade path is a sweep that
    deletes cache files whose ``rejected == "llm_error"`` (a retry policy), not
    a schema change. Chosen deliberately: a bounded, deterministic
    under-claim beats an unbounded bill plus a non-idempotent graph.
    """
    try:
        response = client.complete_json(
            system=_CONTRAST_SYSTEM,
            user=_CONTRAST_USER_TEMPLATE.format(
                a_text=_blob(a).strip(),
                a_evidence=_evidence_block(a.id, index),
                b_text=_blob(b).strip(),
                b_evidence=_evidence_block(b.id, index),
            ),
            schema_name="contrast_verdict",
            cache_key="contrast-v1",
            max_retries=1,
        )
    except Exception:
        logger.exception("contrast: LLM call raised")
        return _rejection("llm_error")
    if not isinstance(response, dict):
        return _rejection("non_dict_response")
    relation = str(response.get("relation") or "").strip().lower()
    if relation not in _RELATION_TO_EDGE:
        # Out-of-vocabulary (notably ``compares_against`` / ``supersedes``) is
        # a rejection, never a silent remap. It is also the SYSTEMATIC class:
        # the model answers it again on every compile, so caching it is what
        # turns a permanent tax back into a one-off cost.
        return _rejection("out_of_vocab_relation")
    direction = str(response.get("direction") or "a_to_b").strip().lower()
    if direction not in _VALID_DIRECTIONS:
        return _rejection("invalid_direction")
    return ContrastVerdict(
        relation=relation,
        direction=direction,
        rationale=str(response.get("rationale") or ""),
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_contrast_pass(
    graph: ResearchGraph,
    *,
    llm: Optional[LLMJsonClient] = None,
    cache_dir: Path,
    max_pairs: Optional[int] = None,
) -> ResearchGraph:
    """Mint typed reasoning edges between blocked pairs; returns the graph.

    No-op (graph untouched, ``cache_dir`` not created) unless BOTH a client is
    supplied AND ``TESSERAE_CONTRAST_PASS`` is truthy.

    ``max_pairs=None`` reads ``TESSERAE_CONTRAST_MAX_PAIRS`` (default 200).
    The cap is applied to a DETERMINISTICALLY RANKED merged list, so raising
    it only ever adds pairs below the previous cut — every previously-asked
    pair is still a warm cache hit.
    """
    if llm is None or not contrast_pass_enabled():
        return graph
    budget = contrast_max_pairs() if max_pairs is None else max(0, int(max_pairs))
    if budget == 0:
        return graph

    ranked: List[CandidatePair] = []
    for block in (CLAIM_BLOCK, FINDING_BLOCK):
        ranked.extend(candidate_pairs(graph, block=block))
    ranked.sort(key=CandidatePair.rank_key)
    ranked = ranked[:budget]
    if not ranked:
        return graph

    nodes = {n.id: n for n in graph.nodes}
    index = _evidence_index(graph)
    existing: Set[Tuple[str, str, str]] = {
        (e.source, e.type, e.target) for e in graph.edges
    }
    minted = 0
    for pair in ranked:
        a, b = nodes.get(pair.lo_id), nodes.get(pair.hi_id)
        if a is None or b is None:  # pragma: no cover — defensive
            continue
        cache_path = cache_dir / f"{_pair_hash(a, b)}.json"
        verdict: Optional[ContrastVerdict] = None
        if cache_path.exists():
            verdict = _read_cached_verdict(cache_path, a, b)
        if verdict is None:
            # A missing/corrupt cache file is not a verdict, so we ask. The
            # answer — INCLUDING a rejection — is always written back, so this
            # pair is never billed twice.
            verdict = _ask_llm(llm, a, b, index)
            _write_cached_verdict(cache_path, (a, b), verdict)
        edge_type = _RELATION_TO_EDGE.get(verdict.relation)
        if edge_type is None:
            continue
        source, target = (a, b) if verdict.direction == "a_to_b" else (b, a)
        key = (source.id, edge_type, target.id)
        if key in existing:
            continue
        graph.edges.append(
            ResearchEdge(
                source=source.id,
                target=target.id,
                type=edge_type,
                evidence=verdict.rationale or None,
                metadata={
                    "extractor": "memory.contrast",
                    "shared_tokens": pair.shared,
                    "block": pair.block,
                },
            )
        )
        existing.add(key)
        minted += 1

    if minted:
        logger.info("memory.contrast: minted %d reasoning edges", minted)
    return graph


__all__ = [
    "CLAIM_BLOCK",
    "FINDING_BLOCK",
    "CandidatePair",
    "ContrastVerdict",
    "candidate_pairs",
    "contrast_max_pairs",
    "contrast_pass_enabled",
    "run_contrast_pass",
]

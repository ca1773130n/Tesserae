"""Hybrid retrieval over a ``ResearchGraph``.

This module implements a small, local-first hybrid retriever that fuses three
ranking lanes via reciprocal-rank fusion (RRF, k=60 — the value popularised by
the original Cormack/Clarke/Buettcher paper and adopted by LightRAG / KAG /
FalkorDB and friends):

    * **bm25**   — Okapi BM25 over the node corpus
    * **lexical** — case-folded substring / "FTS5-style" match used as the
      historical fallback in ``LLMWikiMCPServer.search_nodes``
    * **embedding** — cosine similarity against per-node vectors produced by a
      pluggable :class:`EmbeddingBackend`. The default backend is a
      deterministic hash-bucket pseudo-embedding that needs no extra deps;
      ``sentence-transformers`` (``all-MiniLM-L6-v2``) is preferred when the
      optional dependency is installed.

The public entry point is :func:`hybrid_search`. It takes a ``ResearchGraph``
plus a free-form query and returns a list of :class:`ScoredNode` tuples
ordered by fused RRF score.

Design notes:

* This module never imports anything heavy at import-time. The optional
  ``sentence-transformers`` dependency is loaded lazily inside the backend
  constructor so unit tests stay fast and an offline machine can still run
  the lexical + bm25 lanes.
* The BM25 implementation is a vanilla Okapi BM25 (k1=1.5, b=0.75). When
  ``rank_bm25`` is available we use it for parity with the rest of the
  ecosystem; otherwise the local implementation kicks in transparently.
* All randomness is removed: tokeniser, hash buckets, scoring and tie-breaks
  are deterministic so test runs are reproducible.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from ..research_graph import ResearchGraph, ResearchNode

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RRF_K = 60  # standard reciprocal-rank-fusion damping constant
DEFAULT_WEIGHTS: Dict[str, float] = {"bm25": 1.0, "lexical": 1.0, "embedding": 1.0}
EMBED_DIM = 128  # used by the hash-bucket backend
_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoredNode:
    """A node paired with its fused score and per-lane diagnostics."""

    node: ResearchNode
    score: float
    per_lane: Dict[str, float]
    ranks: Dict[str, int]


@dataclass(frozen=True)
class HybridSearchResult:
    """Wraps the ranked nodes plus retrieval metadata for callers / tests."""

    query: str
    mode: str
    backend: str
    weights: Dict[str, float]
    scored: List[ScoredNode]


# ---------------------------------------------------------------------------
# Embedding backends
# ---------------------------------------------------------------------------


class EmbeddingBackend(Protocol):
    """Minimal embedding protocol: project a list of strings to vectors."""

    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> List[List[float]]: ...


class HashEmbeddingBackend:
    """Deterministic hash-bucket pseudo-embedding (no model required).

    Each token is hashed to an integer; the bucket count is fixed to
    ``EMBED_DIM`` so vectors live in the same space across calls. Token
    weights use sub-linear TF (``1 + log(1 + tf)``) to avoid over-weighting
    repeats. The resulting vector is L2-normalised so cosine similarity is a
    simple dot product. This is **not** a semantic embedding — it is just a
    deterministic placeholder that lets the embedding lane contribute *some*
    signal when no model is installed.
    """

    name = "hash-bucket"
    dim = EMBED_DIM

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            counts: Dict[int, int] = {}
            for token in _tokenize(text):
                bucket = (
                    int.from_bytes(
                        hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(),
                        "little",
                    )
                    % self.dim
                )
                counts[bucket] = counts.get(bucket, 0) + 1
            for bucket, tf in counts.items():
                vec[bucket] = 1.0 + math.log1p(tf)
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            out.append(vec)
        return out


class SentenceTransformersBackend:
    """Thin wrapper around ``sentence-transformers`` if the dep is present.

    Loaded lazily — we never import the heavy module at file import time.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore

        self._model = SentenceTransformer(model_name)
        self.name = f"sentence-transformers:{model_name}"
        # Newer sentence-transformers renamed this method; keep both paths so
        # we work across versions without spamming a FutureWarning.
        dim_getter = getattr(
            self._model,
            "get_embedding_dimension",
            getattr(self._model, "get_sentence_embedding_dimension", None),
        )
        self.dim = int(dim_getter()) if dim_getter else 0

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [list(map(float, vec)) for vec in vectors]


def active_embedding_backend(prefer: str = "auto") -> EmbeddingBackend:
    """Resolve the best embedding backend that is actually importable.

    ``prefer`` may be ``auto`` (default), ``sentence-transformers`` or
    ``hash``. ``auto`` tries the semantic backend first and silently falls
    back to the hash bucket when the optional dep is missing so the function
    *always* returns a usable backend.
    """
    if prefer in ("auto", "sentence-transformers", "st"):
        try:
            return SentenceTransformersBackend()
        except Exception:  # pragma: no cover - depends on optional dep
            if prefer != "auto":
                raise
    return HashEmbeddingBackend()


# ---------------------------------------------------------------------------
# Tokenisation + text materialisation
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> List[str]:
    return [tok.casefold() for tok in _TOKEN_RE.findall(text or "")]


def _node_text(node: ResearchNode) -> str:
    parts = [
        node.id,
        node.name,
        node.type.value,
        node.description or "",
        " ".join(node.aliases),
    ]
    if node.metadata:
        # Surface key=value pairs as text so BM25 / embeddings can match on
        # arxiv_ids, slugs, tags, etc. without us depending on json.dumps.
        for key, value in node.metadata.items():
            parts.append(f"{key} {value}")
    return " ".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# Lane: Okapi BM25
# ---------------------------------------------------------------------------


def _bm25_scores(
    query_tokens: Sequence[str],
    corpus_tokens: Sequence[Sequence[str]],
    k1: float = 1.5,
    b: float = 0.75,
) -> List[float]:
    """Plain Okapi BM25. Returns one score per corpus document."""
    if not query_tokens or not corpus_tokens:
        return [0.0] * len(corpus_tokens)

    # Try rank_bm25 first — keeps us in lock-step with the wider ecosystem
    # without forcing the dep on users that do not need it.
    try:
        from rank_bm25 import BM25Okapi  # type: ignore

        bm25 = BM25Okapi(list(corpus_tokens), k1=k1, b=b)
        return [float(score) for score in bm25.get_scores(list(query_tokens))]
    except Exception:
        pass

    doc_lens = [len(doc) for doc in corpus_tokens]
    avgdl = sum(doc_lens) / max(1, len(doc_lens))
    df: Dict[str, int] = {}
    for doc in corpus_tokens:
        for term in set(doc):
            df[term] = df.get(term, 0) + 1
    n_docs = len(corpus_tokens)
    idf: Dict[str, float] = {}
    for term, freq in df.items():
        # Robertson/Spärck-Jones IDF with +1 floor to keep scores non-negative.
        idf[term] = math.log(1 + (n_docs - freq + 0.5) / (freq + 0.5))

    scores: List[float] = []
    for doc, doc_len in zip(corpus_tokens, doc_lens):
        if not doc:
            scores.append(0.0)
            continue
        tf: Dict[str, int] = {}
        for term in doc:
            tf[term] = tf.get(term, 0) + 1
        score = 0.0
        for term in query_tokens:
            if term not in tf:
                continue
            freq = tf[term]
            numerator = freq * (k1 + 1)
            denominator = freq + k1 * (1 - b + b * doc_len / max(1.0, avgdl))
            score += idf.get(term, 0.0) * numerator / denominator
        scores.append(score)
    return scores


# ---------------------------------------------------------------------------
# Lane: lexical / FTS-style substring scoring
# ---------------------------------------------------------------------------


def _lexical_scores(
    query: str,
    haystacks: Sequence[str],
) -> List[float]:
    """Case-folded term-presence count — matches the historical search_nodes
    behaviour and the FTS5 ``MATCH`` semantics closely enough that we can fold
    them into the same lane when the SQL index is unavailable."""
    terms = [term.casefold() for term in query.split() if term.strip()]
    if not terms:
        return [0.0] * len(haystacks)
    scores: List[float] = []
    for hay in haystacks:
        folded = hay.casefold()
        scores.append(float(sum(1 for term in terms if term in folded)))
    return scores


# ---------------------------------------------------------------------------
# Lane: embedding cosine
# ---------------------------------------------------------------------------


def _embedding_scores(
    query: str,
    corpus_texts: Sequence[str],
    backend: EmbeddingBackend,
) -> List[float]:
    if not corpus_texts:
        return []
    vectors = backend.embed([query, *corpus_texts])
    if not vectors:
        return [0.0] * len(corpus_texts)
    qvec = vectors[0]
    qnorm = math.sqrt(sum(v * v for v in qvec)) or 1.0
    scores: List[float] = []
    for doc_vec in vectors[1:]:
        dnorm = math.sqrt(sum(v * v for v in doc_vec)) or 1.0
        dot = sum(a * b for a, b in zip(qvec, doc_vec))
        scores.append(dot / (qnorm * dnorm))
    return scores


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


def _rrf_ranks(scores: Sequence[float]) -> List[int]:
    """Return 1-indexed dense ranks where ties get the same rank.

    Documents with score == 0 are considered non-relevant for that lane and
    receive ``len(scores) + 1`` (effectively excluding them from RRF unless
    no positive scores exist)."""
    indexed = sorted(
        ((score, idx) for idx, score in enumerate(scores)),
        key=lambda pair: (-pair[0], pair[1]),
    )
    ranks = [len(scores) + 1] * len(scores)
    next_rank = 1
    for score, idx in indexed:
        if score <= 0:
            continue
        ranks[idx] = next_rank
        next_rank += 1
    return ranks


def _fuse(
    lane_scores: Dict[str, List[float]],
    weights: Dict[str, float],
    n: int,
) -> Tuple[List[float], Dict[str, List[int]]]:
    """Run weighted RRF over the per-lane scores.

    Returns the fused per-document score plus the per-lane rank tables (for
    diagnostics / introspection in :class:`ScoredNode`)."""
    fused = [0.0] * n
    rank_tables: Dict[str, List[int]] = {}
    for lane, scores in lane_scores.items():
        weight = float(weights.get(lane, 0.0))
        ranks = _rrf_ranks(scores)
        rank_tables[lane] = ranks
        if weight <= 0:
            continue
        for idx, rank in enumerate(ranks):
            if rank <= n:  # only count lanes where the doc actually ranked
                fused[idx] += weight / (RRF_K + rank)
    return fused, rank_tables


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def hybrid_search(
    graph: ResearchGraph,
    query: str,
    *,
    top_k: int = 20,
    weights: Optional[Dict[str, float]] = None,
    mode: str = "hybrid",
    backend: Optional[EmbeddingBackend] = None,
    candidate_filter: Optional[Iterable[ResearchNode]] = None,
) -> HybridSearchResult:
    """Fuse BM25, lexical and embedding lanes over a ``ResearchGraph``.

    Parameters
    ----------
    graph
        The graph to search.
    query
        Free-form natural-language query. May be empty — when empty the
        lanes are short-circuited and the result preserves the original
        node order (matching legacy behaviour).
    top_k
        Maximum number of :class:`ScoredNode` items to return.
    weights
        Optional per-lane weight override. Missing lanes default to ``1.0``.
        Set a lane's weight to ``0`` to disable it without re-computing.
    mode
        One of ``hybrid`` (all three lanes), ``bm25``, ``lexical``,
        ``embedding`` or ``legacy``. ``legacy`` is identical to ``lexical``
        and preserved for callers migrating from the old substring search.
    backend
        Override the embedding backend (defaults to
        :func:`active_embedding_backend`). Pass a stub in tests to skip
        ``sentence-transformers`` loading.
    candidate_filter
        Optional iterable to restrict the candidate pool (e.g. after type /
        kind filtering done by the caller).
    """
    nodes = list(candidate_filter) if candidate_filter is not None else list(graph.nodes)
    if not nodes:
        return HybridSearchResult(
            query=query,
            mode=mode,
            backend=(backend.name if backend else "n/a"),
            weights=dict(weights or DEFAULT_WEIGHTS),
            scored=[],
        )

    # No query → preserve ordering, score 0 across the board.
    if not query.strip():
        scored = [
            ScoredNode(node=node, score=0.0, per_lane={}, ranks={})
            for node in nodes[: max(1, top_k)]
        ]
        return HybridSearchResult(
            query=query,
            mode=mode,
            backend=(backend.name if backend else "n/a"),
            weights=dict(weights or DEFAULT_WEIGHTS),
            scored=scored,
        )

    selected_weights: Dict[str, float] = dict(weights or DEFAULT_WEIGHTS)
    if mode == "bm25":
        selected_weights = {"bm25": 1.0, "lexical": 0.0, "embedding": 0.0}
    elif mode in ("lexical", "legacy"):
        selected_weights = {"bm25": 0.0, "lexical": 1.0, "embedding": 0.0}
    elif mode == "embedding":
        selected_weights = {"bm25": 0.0, "lexical": 0.0, "embedding": 1.0}
    elif mode != "hybrid":
        raise ValueError(f"Unknown mode: {mode!r}")

    texts = [_node_text(node) for node in nodes]
    corpus_tokens = [_tokenize(text) for text in texts]
    query_tokens = _tokenize(query)

    lane_scores: Dict[str, List[float]] = {}
    if selected_weights.get("bm25", 0.0) > 0:
        lane_scores["bm25"] = _bm25_scores(query_tokens, corpus_tokens)
    else:
        lane_scores["bm25"] = [0.0] * len(nodes)
    if selected_weights.get("lexical", 0.0) > 0:
        lane_scores["lexical"] = _lexical_scores(query, texts)
    else:
        lane_scores["lexical"] = [0.0] * len(nodes)
    if selected_weights.get("embedding", 0.0) > 0:
        embed_backend = backend or active_embedding_backend()
        lane_scores["embedding"] = _embedding_scores(query, texts, embed_backend)
        backend_name = embed_backend.name
    else:
        lane_scores["embedding"] = [0.0] * len(nodes)
        backend_name = backend.name if backend else "disabled"

    fused, ranks = _fuse(lane_scores, selected_weights, len(nodes))

    # Candidate-generation gate: a doc must have *some* lexical evidence (BM25
    # or lexical lane) before it can surface in hybrid mode. The embedding
    # lane acts as a re-ranker, never as a candidate generator on its own —
    # otherwise an opaque hash-bucket cosine can drag in unrelated nodes for
    # rare-token queries (e.g. CodeFunction names that no public node shares).
    # In single-lane modes (bm25 / lexical / embedding) the active lane *is*
    # the gate, which is the obvious user expectation.
    if mode == "hybrid":
        def _is_candidate(idx: int) -> bool:
            return lane_scores["bm25"][idx] > 0 or lane_scores["lexical"][idx] > 0
    else:
        active = [lane for lane, w in selected_weights.items() if w > 0]
        def _is_candidate(idx: int) -> bool:
            return any(lane_scores[lane][idx] > 0 for lane in active)

    indexed = sorted(
        ((fused[idx], idx) for idx in range(len(nodes)) if _is_candidate(idx)),
        key=lambda pair: (-pair[0], pair[1]),
    )
    bounded = max(1, min(int(top_k), len(nodes)))
    scored: List[ScoredNode] = []
    for fused_score, idx in indexed[:bounded]:
        scored.append(
            ScoredNode(
                node=nodes[idx],
                score=float(fused_score),
                per_lane={lane: float(lane_scores[lane][idx]) for lane in lane_scores},
                ranks={lane: int(ranks[lane][idx]) for lane in ranks},
            )
        )

    return HybridSearchResult(
        query=query,
        mode=mode,
        backend=backend_name,
        weights=selected_weights,
        scored=scored,
    )

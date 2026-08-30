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
  ecosystem; otherwise the local implementation kicks in transparently. The
  local one can be served from a persisted inverted index
  (:mod:`tesserae.retrieval.bm25_index`) when the caller passes one — same
  arithmetic, same floats, without re-tokenising the corpus and recounting
  document frequency on every query. ``rank_bm25``, being a different formula,
  stands the index down rather than swapping rankers mid-flight.
* All randomness is removed: tokeniser, hash buckets, scoring and tie-breaks
  are deterministic so test runs are reproducible.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from ..research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from .bm25_index import Bm25Index, PreparedCorpus
from .vector_cache import VectorCache, embed_texts

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RRF_K = 60  # standard reciprocal-rank-fusion damping constant
DEFAULT_WEIGHTS: Dict[str, float] = {"bm25": 1.0, "lexical": 1.0, "embedding": 1.0}
EMBED_DIM = 128  # used by the hash-bucket backend

# Minimum cosine for an embedding-ONLY candidate admission on the real-backend
# path (RETR-02). Real distilled vectors (model2vec / sentence-transformers) are
# virtually never orthogonal to a query, so raw cosine is strictly positive for
# nearly every node. A bare ``> 0`` gate would therefore admit ~the whole graph
# as candidates, ballooning ``total_matches`` ("X of N matches") to ≈ graph size
# on every real-backend query — a precision/reporting regression. We require a
# floor so only genuinely related nodes (paraphrases/synonyms, high cosine) are
# admitted on semantic evidence alone, while keeping the RRF top-k ranking
# untouched. 0.30 is a conservative default: well below the cosine of a true
# paraphrase/synonym hit (typically ≥ 0.5 for distilled sentence models) yet far
# above the low-but-nonzero background cosine of unrelated nodes. The hash stub
# still requires lexical evidence and is unaffected by this floor.
EMBED_CANDIDATE_MIN_COSINE = 0.30
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
class LaneProfile:
    """What ONE lane cost and contributed on one :func:`hybrid_search` call.

    ``candidates_in`` is 0 for a lane whose weight is 0: that lane never saw
    the corpus, which is exactly what distinguishes "ran and found nothing"
    from "did not run" — the distinction ``mode`` alone cannot make.
    ``scored`` counts the documents this lane found relevant, using
    :func:`_rrf_ranks`'s own criterion (``score > 0``), so a lane's count and
    the ranks the fusion consumed can never disagree.
    ``embed_calls`` can only ever be non-zero on the embedding lane, which is
    the only lane that calls a model. ``cache_hits`` / ``cache_misses`` are
    read by TWO lanes and mean the same thing in both: documents served from a
    sidecar against documents that had to be computed and written — vectors on
    the embedding lane, postings on the BM25 lane. They are carried on every
    lane so the shape is uniform for a consumer that iterates.
    ``vectorized`` is likewise only ever true on the embedding lane, and is
    load-bearing for the same reason ``bm25_index`` is: the pure-Python cosine
    fallback costs roughly 5x the vectorised one on a corpus-sized candidate
    set, so a reader who could not tell them apart would read a machine without
    numpy installed as one where the lane is simply slow.
    """

    lane: str
    weight: float
    candidates_in: int
    scored: int
    embed_calls: int
    cache_hits: int
    cache_misses: int
    ms: float
    vectorized: bool = False


@dataclass(frozen=True)
class WinnerAttribution:
    """One returned node and the lanes that actually put it there.

    ``lanes`` uses :func:`_fuse`'s own contribution criterion — positive
    weight AND a rank inside the corpus — so it reports what the fusion
    summed, not merely which lanes produced a non-zero score.
    """

    node_id: str
    score: float
    lanes: Tuple[str, ...]


@dataclass(frozen=True)
class RetrievalProfile:
    """Per-lane cost accounting for one :func:`hybrid_search` call.

    Opt-in (``hybrid_search(..., profile=True)``) because measuring costs:
    with the flag unset nothing here is computed, no clock is read, and the
    result is byte-identical. A profile is a report on a search that already
    happened — it is produced from the same score and rank tables the fusion
    used, so it can never move a ranking.

    ``vector_cache`` records whether a cache backed the embedding lane, and
    ``bm25_index`` whether the inverted index backed the BM25 lane. Both are
    load-bearing rather than decorative: with no sidecar the hit/miss counters
    are structurally 0, and a reader who could not tell that apart from a
    perfectly warm one would read the most expensive path as the cheapest.
    ``bm25_index`` is False whenever the lane fell back for ANY reason —
    no sidecar, an unreadable one, or ``rank_bm25`` being installed (see
    :func:`_rank_bm25_available`) — because from a cost reader's point of view
    those are the same event.
    """

    query: str
    mode: str
    backend: str
    #: Corpus size the lanes were handed (post ``candidate_filter``).
    candidates_in: int
    #: Candidates that survived the candidate-generation gate, pre-``top_k``.
    admitted: int
    #: Items actually returned after the ``top_k`` slice.
    returned: int
    ms: float
    vector_cache: bool
    bm25_index: bool = False
    lanes: Dict[str, LaneProfile] = field(default_factory=dict)
    winners: List[WinnerAttribution] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        """JSON-safe projection for the MCP layer."""
        return {
            "query": self.query,
            "mode": self.mode,
            "backend": self.backend,
            "candidates_in": self.candidates_in,
            "admitted": self.admitted,
            "returned": self.returned,
            "ms": round(self.ms, 3),
            "vector_cache": self.vector_cache,
            "bm25_index": self.bm25_index,
            "lanes": {
                name: {
                    "weight": lane.weight,
                    "candidates_in": lane.candidates_in,
                    "scored": lane.scored,
                    "embed_calls": lane.embed_calls,
                    "cache_hits": lane.cache_hits,
                    "cache_misses": lane.cache_misses,
                    "ms": round(lane.ms, 3),
                    "vectorized": lane.vectorized,
                }
                for name, lane in self.lanes.items()
            },
            "winners": [
                {"node_id": w.node_id, "score": w.score, "lanes": list(w.lanes)}
                for w in self.winners
            ],
        }


@dataclass(frozen=True)
class HybridSearchResult:
    """Wraps the ranked nodes plus retrieval metadata for callers / tests."""

    query: str
    mode: str
    backend: str
    weights: Dict[str, float]
    scored: List[ScoredNode]
    # Total number of candidates that survived the candidate-generation gate
    # *before* being sliced to ``top_k``. Callers (e.g. the MCP server) need
    # this to report an accurate ``total_matches`` rather than the page size.
    total_matches: int = 0
    #: Populated only when the caller passed ``profile=True``. ``None`` means
    #: profiling never ran, never "the search cost nothing".
    profile: Optional[RetrievalProfile] = None


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


class Model2VecBackend:
    """Static distilled embedding via model2vec — offline, ~8 MB, no torch.

    Loaded lazily (the heavy ``from model2vec import StaticModel`` stays inside
    ``__init__``, never at module import time). Encoding is a deterministic
    token-lookup + mean — no neural inference — so results are stable across
    runs, which matters for byte-idempotence of anything derived from them.
    """

    def __init__(self, model_name: str = "minishlab/potion-base-8M") -> None:
        from model2vec import StaticModel  # type: ignore

        self._model = StaticModel.from_pretrained(model_name)
        self.name = f"model2vec:{model_name}"
        self.dim = int(getattr(self._model, "dim", 0)) or len(
            self._encode(["x"])[0]
        )

    def _encode(self, texts: List[str]) -> List[List[float]]:
        # Some model2vec versions accept ``normalize=`` in ``encode``; older
        # ones don't. Prefer the native path (cosine == dot, matching the other
        # backends' L2-normalised contract); fall back to a manual L2-normalise.
        try:
            vectors = self._model.encode(texts, normalize=True)
        except TypeError:
            raw = self._model.encode(texts)
            vectors = []
            for vec in raw:
                floats = [float(v) for v in vec]
                norm = math.sqrt(sum(v * v for v in floats))
                if norm > 0:
                    floats = [v / norm for v in floats]
                vectors.append(floats)
            return vectors
        return [list(map(float, vec)) for vec in vectors]

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        return self._encode(list(texts))


class OpenAIEmbeddingBackend:
    """Hosted embeddings via the OpenAI embeddings API — EXPLICIT USE ONLY.

    Exists so a benchmark can hold the embedding substrate constant with a
    published protocol. LongMemEval-MAB results (and the baselines Tesserae
    would be compared against) fix ``text-embedding-3-small`` for every system,
    so retrieval differences are attributable to the memory architecture rather
    than to the embedder. Running Tesserae's default model2vec against those
    numbers would compare two things at once.

    **Never on the ``auto`` path, deliberately.** Every other backend in this
    module is free and local; this one bills per call and needs the network. A
    resolver that could reach it by default would turn an ordinary
    ``search_nodes`` into a metered request nobody asked for. It is reachable
    only as ``prefer="openai"``, and — following this module's stated contract
    that an explicit preference which fails to construct is re-raised — it
    raises rather than degrading when ``OPENAI_API_KEY`` is absent.

    Vectors are cached by :mod:`tesserae.retrieval.vector_cache` on
    ``(backend_name, backend_dim, sha256(text))``, and ``name`` carries the
    model, so switching models re-embeds instead of serving another model's
    vectors.
    """

    #: Published dimensionality, used only to avoid a probe call when the API
    #: has not been reached yet. The real value from the first response wins.
    _KNOWN_DIMS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
    }

    #: The API accepts many inputs per request; batch to keep any single
    #: request well inside its token ceiling on long corpus texts.
    _BATCH = 96

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        import os

        key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        if not key:
            raise RuntimeError(
                "OpenAIEmbeddingBackend requires OPENAI_API_KEY. It is never "
                "reached on the 'auto' path, so this means it was asked for "
                "explicitly (prefer='openai') without a key configured."
            )
        self._key = key
        self._model = model
        self.name = f"openai:{model}"
        self.dim = int(self._KNOWN_DIMS.get(model, 0))

    def _post(self, batch: List[str]) -> List[List[float]]:
        import json as _json
        import urllib.request

        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=_json.dumps({"model": self._model, "input": batch}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = _json.load(resp)
        # Order is not promised by the field order alone — the API returns an
        # explicit index per item, so sort on it rather than trusting arrival
        # order. A silently permuted batch would attach every vector to the
        # wrong text and be invisible downstream.
        items = sorted(payload["data"], key=lambda d: int(d["index"]))
        return [[float(x) for x in d["embedding"]] for d in items]

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        pending = list(texts)
        for i in range(0, len(pending), self._BATCH):
            out.extend(self._post(pending[i : i + self._BATCH]))
        if out and not self.dim:
            self.dim = len(out[0])
        return out


# Module-scope cache so repeated MCP `search_nodes` calls don't reload a
# multi-hundred-MB ``SentenceTransformer`` model every query. Keyed by the
# ``prefer`` argument so swapping resolver preferences mid-process still
# works as intended.
_BACKEND_CACHE: Dict[str, "EmbeddingBackend"] = {}


def reset_embedding_backend() -> None:
    """Drop the cached embedding backend(s).

    Intended for tests that want to assert backend construction behaviour
    or that monkey-patch the underlying SDK between cases.
    """
    _BACKEND_CACHE.clear()


def active_embedding_backend(prefer: str = "auto") -> EmbeddingBackend:
    """Resolve the best embedding backend that is actually importable.

    ``prefer`` may be ``auto`` (default), ``model2vec``/``m2v``,
    ``sentence-transformers``/``st``, ``openai`` or ``hash``.

    ``openai`` is reachable ONLY by name. It is the one backend that bills per
    call and needs the network, so the ``auto`` path must never be able to
    select it — an ordinary read would otherwise become a metered request. It
    exists so a benchmark can hold the embedding substrate constant with a
    published protocol that fixes ``text-embedding-3-small``.

    Resolution order on the ``auto`` path is **model2vec → sentence-transformers
    → hash stub**. model2vec is tried first: it is lighter (~8 MB static model),
    offline, and needs no torch. If neither real backend is importable, ``auto``
    does NOT silently degrade — it emits a loud :class:`UserWarning` naming
    ``tesserae[semantic]`` and only then returns the non-semantic
    :class:`HashEmbeddingBackend`. An EXPLICIT non-``auto`` preference that fails
    to construct is re-raised (fail-loud) rather than swallowed.

    The resolved backend is memoised at module scope — constructing a real
    model is expensive (and we never want to reload per query). The warning
    therefore fires only on a cache miss, i.e. effectively once per ``prefer``
    key per process. Use :func:`reset_embedding_backend` to clear the cache in
    tests.
    """
    cached = _BACKEND_CACHE.get(prefer)
    if cached is not None:
        return cached
    # model2vec first — lighter, offline, no torch.
    if prefer in ("auto", "model2vec", "m2v"):
        try:
            backend: EmbeddingBackend = Model2VecBackend()
            _BACKEND_CACHE[prefer] = backend
            return backend
        except Exception:  # optional dep / offline first-use download
            if prefer != "auto":
                raise
    # sentence-transformers second (heavier; stays opt-in).
    if prefer in ("auto", "sentence-transformers", "st"):
        try:
            backend = SentenceTransformersBackend()
            _BACKEND_CACHE[prefer] = backend
            return backend
        except Exception:  # pragma: no cover - depends on optional dep
            if prefer != "auto":
                raise
    # Hosted embeddings: EXPLICIT ONLY, never reachable from "auto". This one
    # bills per call and needs the network, so it must be asked for by name.
    if prefer == "openai":
        backend = OpenAIEmbeddingBackend()
        _BACKEND_CACHE[prefer] = backend
        return backend
    if prefer == "hash":
        backend = HashEmbeddingBackend()
        _BACKEND_CACHE[prefer] = backend
        return backend
    # No real backend on the auto path: FAIL LOUD, then degrade.
    import warnings

    warnings.warn(
        "No semantic embedding backend available (model2vec or "
        "sentence-transformers). Hybrid/embedding retrieval is running on "
        "the non-semantic hash-bucket stub. Install `tesserae[semantic]` "
        "for real semantic retrieval.",
        UserWarning,
        stacklevel=2,
    )
    backend = HashEmbeddingBackend()
    _BACKEND_CACHE[prefer] = backend
    return backend


def backend_is_semantic(backend: "EmbeddingBackend") -> bool:
    """True when ``backend`` is a real semantic backend (not the hash stub)."""
    return not isinstance(backend, HashEmbeddingBackend)


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
# Lane: raw source text for document-anchor nodes
# ---------------------------------------------------------------------------

#: How much of a document-anchor node's OWN source file joins its LEXICAL text.
#: Deliberately NOT the embedding text. A 256-dimension mean-pooled vector over
#: 8k characters is the per-file pooling failure that cost the dense lane
#: 0.7857 -> 0.6578 in lane ablation; measured on LongMemEval-MAB group 0, raw
#: text in ALL THREE lanes scores 0.803/0.612 against 0.820/0.707 for the
#: lexical lanes alone, so the gating below is load-bearing and not decoration.
SOURCE_LEXICAL_CHARS = 8_000

#: Node types whose ``source_path`` names the document the node stands for,
#: rather than a file that merely mentions it. Only these nodes get their own
#: file's text: a concept extracted from a paper must not become retrievable
#: through the paper's entire contents, which would make every concept in a
#: document score identically and destroy ranking within it.
_SOURCE_ANCHOR_TYPES = frozenset(
    {
        ResearchNodeType.SOURCE_DOCUMENT,
        ResearchNodeType.PAPER,
        ResearchNodeType.SOURCE_FILE,
        ResearchNodeType.REPOSITORY,
        ResearchNodeType.PROJECT,
    }
)


def _confined_source(path: str, root: Path, cache: Dict[str, str]) -> str:
    """``path``'s text, or ``""`` when it escapes ``root``.

    ``source_path`` is UNTRUSTED: it arrives from document frontmatter, and a
    document that declares ``source_path: /etc/ssh/id_rsa`` would otherwise
    paste that file into a retrieval corpus and, downstream, into an LLM
    prompt. Resolve-then-compare is the same contract
    ``ask_planner._read_source`` already enforces for the same reason.

    The cache is keyed on ``(root, path)`` rather than ``path`` alone so two
    projects sharing a process cannot serve each other's file contents.
    """
    key = f"{root}\x00{path}"
    if key in cache:
        return cache[key]
    text = ""
    try:
        resolved = Path(path).resolve()
        base = Path(root).resolve()
        if resolved.is_relative_to(base) and resolved.is_file():
            text = resolved.read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError, RuntimeError):
        text = ""
    cache[key] = text
    return text


def _lexical_texts(
    nodes: Sequence[ResearchNode], texts: Sequence[str], source_root: Optional[Path]
) -> Sequence[str]:
    """``texts`` with each anchor node's own source file appended, or ``texts``.

    Returns the SAME object when ``source_root`` is None, so the default path
    is byte-identical to not having this feature at all — no copy, no
    re-tokenisation, no ranking churn on any caller that does not opt in.
    """
    if source_root is None:
        return texts
    cache: Dict[str, str] = {}
    out: List[str] = []
    for node, text in zip(nodes, texts):
        raw = ""
        if node.type in _SOURCE_ANCHOR_TYPES and node.source_path:
            raw = _confined_source(node.source_path, source_root, cache)
        out.append(f"{text}\n{raw[:SOURCE_LEXICAL_CHARS]}" if raw else text)
    return out

# ---------------------------------------------------------------------------
# Lane: Okapi BM25
# ---------------------------------------------------------------------------


_RANK_BM25_PRESENT: Optional[bool] = None


def _rank_bm25_available() -> bool:
    """Whether :func:`_bm25_scores` will run ``rank_bm25`` instead of the local Okapi.

    A correctness gate, not an optimisation. :func:`_bm25_scores` prefers
    ``rank_bm25.BM25Okapi`` whenever it imports, and that library's IDF — plus
    its epsilon floor for terms whose IDF would go negative — is NOT the
    formula written below it, so a machine with the package installed already
    scores differently from one without. :func:`_bm25_scores_indexed`
    reproduces the LOCAL formula exactly; serving it where ``rank_bm25`` would
    otherwise have run would silently swap ranking functions, which is the one
    thing an index is not allowed to do. So where ``rank_bm25`` is present the
    index stands down and the query pays the old cost.

    Memoised because a FAILING import is not cached by the interpreter: without
    the memo every query re-walks ``sys.path`` looking for a package that is
    not there.
    """
    global _RANK_BM25_PRESENT
    if _RANK_BM25_PRESENT is None:
        try:
            import rank_bm25  # type: ignore  # noqa: F401

            _RANK_BM25_PRESENT = True
        except Exception:
            _RANK_BM25_PRESENT = False
    return _RANK_BM25_PRESENT


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


def _bm25_scores_indexed(
    query_tokens: Sequence[str],
    prepared: PreparedCorpus,
    postings: Dict[str, Dict[int, int]],
    k1: float = 1.5,
    b: float = 0.75,
) -> List[float]:
    """:func:`_bm25_scores` over an inverted index. Same numbers, less work.

    Read this beside the function above and keep the two together: every
    difference is about WHICH documents are visited, never about what is
    computed for one of them. The arithmetic, the operand order and the
    per-document iteration over ``query_tokens`` (duplicates included, so a
    query repeating a term still counts it twice) are copied verbatim, which
    is what makes a warm index equal to a cold one under exact float
    comparison rather than approximately.

    Three savings, and each is only legitimate because of a property of the
    formula:

    * The corpus is not tokenised. Tokenisation is a pure function of one
      document's text, so it belongs in the sidecar; ``prepare`` put it there.
    * Document frequency is counted for the QUERY's terms only. The function
      above builds ``df`` over the entire vocabulary — 94,929 terms on this
      project's graph — and then reads back exactly the two or three that
      ``idf.get(term, ...)`` asks for. The other 94,926 entries cannot reach a
      score, so not computing them cannot change one.
    * Only documents that contain a query term are scored. The function above
      scores all of them and gets 0.0 for the rest, because a document that
      shares no term with the query contributes nothing to the sum. The zero
      is written directly here instead.

    What is NOT taken from the sidecar is everything that depends on the
    candidate set — ``n_docs``, ``avgdl`` and every ``df`` — because
    ``hybrid_search`` is filter-first and the candidate set is whatever the
    caller filtered it down to. Precomputing corpus-wide statistics would make
    a type-filtered search score against the unfiltered graph's IDF, which is a
    different (and wrong) answer rather than a faster one.
    """
    n_docs = len(prepared.doc_ids)
    if not query_tokens or not n_docs:
        return [0.0] * n_docs

    doc_lens = prepared.doc_lens
    avgdl = sum(doc_lens) / max(1, len(doc_lens))

    # Distinct query terms for the df pass; the SCORING loop below still walks
    # ``query_tokens`` itself, duplicates and all, exactly as the in-memory
    # lane does.
    query_terms = list(dict.fromkeys(query_tokens))
    term_docs = [postings.get(term) or {} for term in query_terms]

    df: Dict[str, int] = {term: 0 for term in query_terms}
    matched: List[int] = []
    for idx, doc_id in enumerate(prepared.doc_ids):
        hit = False
        for term, docs in zip(query_terms, term_docs):
            if doc_id in docs:
                df[term] += 1
                hit = True
        if hit:
            matched.append(idx)

    idf: Dict[str, float] = {}
    for term, freq in df.items():
        # Robertson/Spärck-Jones IDF with +1 floor, character for character
        # the expression in _bm25_scores. A term nobody has is left at df 0
        # and its idf is simply never reached: no document lists it, so the
        # scoring loop skips it exactly as ``term not in tf`` does above.
        idf[term] = math.log(1 + (n_docs - freq + 0.5) / (freq + 0.5))

    scores = [0.0] * n_docs
    posting_by_term = dict(zip(query_terms, term_docs))
    for idx in matched:
        doc_len = doc_lens[idx]
        if not doc_len:
            continue
        doc_id = prepared.doc_ids[idx]
        score = 0.0
        for term in query_tokens:
            freq = posting_by_term.get(term, {}).get(doc_id)
            if freq is None:
                continue
            numerator = freq * (k1 + 1)
            denominator = freq + k1 * (1 - b + b * doc_len / max(1.0, avgdl))
            score += idf.get(term, 0.0) * numerator / denominator
        scores[idx] = score
    return scores


# ---------------------------------------------------------------------------
# Lane: lexical / FTS-style substring scoring
# ---------------------------------------------------------------------------


#: A query term occurring in more than this share of the haystacks carries no
#: ranking information — it separates nothing. Matched by eye to the BM25 lane's
#: own behaviour, where such a term's IDF collapses toward zero; this lane has no
#: IDF, so the cut has to be explicit.
LEXICAL_DF_CEILING = 0.30

#: A query token shorter than this never prefix-expands. "in" is a prefix of
#: "inference" and "information", so allowing short prefixes would reinstate
#: exactly the stopword blowout this lane was fixed to remove. Four characters
#: keeps "splat" -> "splatting" and "graph" -> "graphs" while refusing "in",
#: "the", "for", "and".
PREFIX_MIN_LEN = 4


def _lexical_scores(
    query: str,
    haystacks: Sequence[str],
) -> List[float]:
    """Fraction of the query's DISCRIMINATING tokens that a haystack contains.

    Token matching, not substring matching, because FTS5 ``MATCH`` — the
    semantics this lane exists to stand in for — is token-based.

    Three defects are fixed here, and together they cost 0.039 recall@10 on a
    120-question benchmark over a 25,410-node graph (0.476 -> 0.515 measured
    with this lane simply disabled, which scored no worse than this rebuild):

    * ``query.split()`` is not tokenisation. It left punctuation attached, so
      ``complexity?`` matched nothing at all, while bare ``in``/``the``/``to``
      matched almost every node in the graph.
    * ``term in folded`` is substring containment, so ``in`` scored a hit on
      "training", "domain" and "inference" alike.
    * The count was unnormalised, so a long node outscored a precise one purely
      by having more text, and the lane fused into RRF at weight 1.0 — equal to
      BM25 (see ``DEFAULT_WEIGHTS``).

    Scoring the FRACTION of discriminating query tokens present keeps the score
    in [0, 1] regardless of node length, so length no longer buys rank.
    """
    terms = list(dict.fromkeys(_tokenize(query)))
    if not terms:
        return [0.0] * len(haystacks)
    token_sets = [set(_tokenize(hay)) for hay in haystacks]
    if not token_sets:
        return []

    # PREFIX, not substring. Dropping substring matching outright broke the
    # thing it was right about: "splat" must still find "splatting". What it was
    # wrong about is matching INSIDE a word — "in" hitting "training",
    # "domain", "inference". A prefix keeps the first and refuses the second.
    #
    # Expansion happens once against the vocabulary rather than per haystack:
    # scanning every node's tokens for every term was ~20M string comparisons a
    # query on a 25k-node graph.
    vocab = set()
    for s in token_sets:
        vocab |= s
    expanded: List[set] = []
    for t in terms:
        matches = {t} if t in vocab else set()
        if len(t) >= PREFIX_MIN_LEN:
            matches |= {w for w in vocab if w.startswith(t)}
        expanded.append(matches)

    ceiling = max(1, int(len(token_sets) * LEXICAL_DF_CEILING))
    kept = [m for m in expanded
            if m and sum(1 for s in token_sets if s & m) <= ceiling]
    # Every term is ubiquitous (a short, wholly generic query): fall back to the
    # full term list rather than returning an all-zero lane, which would hand
    # the fusion a silently dead input.
    if not kept:
        kept = [m for m in expanded if m]
    if not kept:
        return [0.0] * len(haystacks)
    return [float(sum(1 for m in kept if s & m)) / len(kept) for s in token_sets]


# ---------------------------------------------------------------------------
# Lane: embedding cosine
# ---------------------------------------------------------------------------


_NUMPY_CHECKED = False
_NUMPY: Optional[Any] = None


def _numpy() -> Optional[Any]:
    """The ``numpy`` module, or ``None`` where it is not installed.

    numpy is an OPTIONAL dependency (the ``semantic`` extra), so the cosine
    lane cannot assume it the way it assumes the stdlib. Memoised because a
    FAILING import is not cached by the interpreter — without the memo every
    query would re-walk ``sys.path`` looking for a package that is not there,
    which is the cost this whole function exists to avoid.
    """
    global _NUMPY_CHECKED, _NUMPY
    if not _NUMPY_CHECKED:
        try:
            import numpy  # type: ignore

            _NUMPY = numpy
        except Exception:
            _NUMPY = None
        _NUMPY_CHECKED = True
    return _NUMPY


def _same_width(rows: Sequence[Sequence[object]]) -> int:
    """Common length of ``rows``, or 0 if they are ragged or empty.

    A ragged batch cannot become one matrix, and coercing it would either
    raise deep inside numpy or (worse, on older versions) build an object
    array that scores wrong. Checking here lets the lane fall back to the
    per-row Python path instead, which handles ragged input fine.
    """
    if not rows:
        return 0
    width = len(rows[0])
    if width == 0:
        return 0
    return width if all(len(row) == width for row in rows) else 0


def _embedding_scores_vectorized(
    query: str,
    corpus_texts: Sequence[str],
    backend: EmbeddingBackend,
    vector_cache: Optional[VectorCache] = None,
) -> Optional[List[float]]:
    """:func:`_embedding_scores` as one matrix-vector product.

    Returns ``None`` — having done no work and, crucially, no embedding — when
    it cannot run, so the caller falls back without paying twice.

    Why this exists: exhaustive cosine was never the expensive part, PYTHON
    was. Over this project's own graph (47,132 nodes x 256 dims) the arithmetic
    is ~4 ms vectorised against ~477 ms as a Python loop, and rebuilding the
    matrix from the cache's packed rows is ~8 ms against ~370 ms of
    ``struct.unpack`` plus list-to-array coercion; the whole lane goes 897 ms
    -> 173 ms. The lane stays EXHAUSTIVE and therefore stays filter-first: it
    scores exactly the candidate set it was handed, which is what an ANN index
    could not do (see :mod:`.vector_cache`'s module docstring). Only the cost
    changes.

    Scores are NOT bit-identical to the scalar path, and cannot be: BLAS
    reassociates the sums and pairs the operands differently. Measured over the
    same graph the two agree to 1.7e-15 absolute, while the tightest gap
    between adjacent distinct cosine scores there is 4.0e-11 — four orders of
    magnitude wider — and no position in any of the 47,132-long orderings
    moved.

    The one difference that CAN surface is a tie-break. Where two documents
    have the same true cosine, the scalar loop happens to land on the same
    float for both and this path does not, so which of them wins a ``top_k``
    slot may differ. That is an arbitrary choice either way, but it is a
    choice, so it is stated rather than left to be discovered: it shows up on
    the hash-bucket stub, whose vectors collide often, far more than on real
    embeddings. ``tests/test_hybrid_search.py`` pins the tolerance, pins that
    untied documents never move, and is where that claim is checked.
    """
    if not corpus_texts:
        return []
    np = _numpy()
    if np is None:
        return None

    if vector_cache is not None:
        # Packed float64 rows straight out of SQLite: joining them IS the
        # matrix, so the corpus never becomes 47k Python lists on the way in.
        blobs = vector_cache.embed_blobs(backend, [query, *corpus_texts])
        byte_width = _same_width(blobs)
        if byte_width % 8 or byte_width == 0:
            return None
        matrix = np.frombuffer(b"".join(blobs), dtype="<f8").reshape(
            len(blobs), byte_width // 8
        )
    else:
        vectors = embed_texts(backend, [query, *corpus_texts], None)
        if not vectors:
            return [0.0] * len(corpus_texts)
        if not _same_width(vectors):
            return None
        matrix = np.asarray(vectors, dtype=np.float64)

    qvec = matrix[0]
    docs = matrix[1:]
    # ``or 1.0`` on a zero norm, matching the scalar path: a zero vector scores
    # 0 rather than dividing by zero and poisoning the lane with NaN.
    qnorm = math.sqrt(float(qvec @ qvec)) or 1.0
    dnorms = np.sqrt(np.einsum("ij,ij->i", docs, docs))
    dnorms[dnorms == 0.0] = 1.0
    return ((docs @ qvec) / (qnorm * dnorms)).tolist()


def _embedding_scores(
    query: str,
    corpus_texts: Sequence[str],
    backend: EmbeddingBackend,
    vector_cache: Optional[VectorCache] = None,
) -> List[float]:
    """Exhaustive cosine, one document at a time.

    The fallback for machines without numpy, and the reference the vectorised
    path is tested against.
    """
    if not corpus_texts:
        return []
    # The query is embedded through the same cache as the corpus: it is just
    # another text, and a repeated question is common enough to be worth the
    # one row. Scores are unaffected either way — the cache returns the same
    # float64 vectors the backend produced (see vector_cache's module docstring).
    vectors = embed_texts(backend, [query, *corpus_texts], vector_cache)
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


def _short_circuit_profile(
    *,
    query: str,
    mode: str,
    backend: str,
    started: float,
    candidates_in: int,
    admitted: int,
    returned: int,
    vector_cache: bool,
    bm25_index: bool = False,
) -> RetrievalProfile:
    """Profile for a search that returned before any lane ran.

    ``lanes`` is empty rather than three zeroed entries, because zeroed lanes
    would read as "all three ran and scored nothing" — the opposite of what
    happened on an empty corpus or an empty query.
    """
    return RetrievalProfile(
        query=query,
        mode=mode,
        backend=backend,
        candidates_in=candidates_in,
        admitted=admitted,
        returned=returned,
        ms=(time.perf_counter() - started) * 1000.0,
        vector_cache=vector_cache,
        bm25_index=bm25_index,
        lanes={},
        winners=[],
    )


def hybrid_search(
    graph: ResearchGraph,
    query: str,
    *,
    top_k: int = 20,
    weights: Optional[Dict[str, float]] = None,
    mode: str = "hybrid",
    backend: Optional[EmbeddingBackend] = None,
    candidate_filter: Optional[Iterable[ResearchNode]] = None,
    vector_cache: Optional[VectorCache] = None,
    bm25_index: Optional[Bm25Index] = None,
    source_root: Optional[Path] = None,
    profile: bool = False,
    document_first: bool = False,
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
    vector_cache
        Optional :class:`~tesserae.retrieval.vector_cache.VectorCache` backing
        the embedding lane. Purely a cost optimisation: the same vectors are
        returned either way, so scores and ordering are identical with a cold
        cache, a warm cache, or none at all. ``None`` (default) embeds every
        call, which is the only option for a graph with no ``.tesserae``
        sidecar to write to.
    bm25_index
        Optional :class:`~tesserae.retrieval.bm25_index.Bm25Index` backing the
        BM25 lane. Purely a cost optimisation, on the same terms as
        ``vector_cache``: the lane returns the same floats with a cold index, a
        warm one, or none at all, so nothing about ranking depends on whether
        one was passed. The lane falls back to tokenising the corpus in memory
        whenever the index cannot serve the WHOLE candidate set, because a
        partly-indexed corpus would score differently rather than merely
        slower.
    source_root
        Opt-in. When given, a document-anchor node (see
        :data:`_SOURCE_ANCHOR_TYPES`) also becomes retrievable through its OWN
        source file's first :data:`SOURCE_LEXICAL_CHARS` characters, in the
        BM25 and lexical lanes ONLY. Structure still selects the text; it no
        longer replaces it, which is the change HippoRAG 2 makes with passage
        nodes and reaches here without a schema change.

        Measured on LongMemEval-MAB group 0, K=10, weights untouched: recall@10
        0.705 -> 0.820 and MRR 0.584 -> 0.707, against a BM25-over-whole-
        -documents reference of 0.911/0.803. The extraction pipeline's own text
        loss is what this recovers — a 14k-character chat session was otherwise
        retrievable only through 88-character concept summaries.

        Files are read confined to this root because ``source_path`` is
        untrusted frontmatter; ``None`` reads NOTHING rather than everything,
        and is byte-identical to this parameter not existing.

        Raises ranking churn for every caller that opts in, so callers opt in
        one at a time and deliberately.
    profile
        Opt-in per-lane cost accounting (roadmap step 9), attached to the
        result as :attr:`HybridSearchResult.profile`. Off by default because
        measuring costs: with it unset no clock is read and no counter is
        computed. It cannot change the answer — every number is derived from
        the score and rank tables the fusion already produced.
    """
    if document_first and query.strip():
        # Two stages. The unit of recall for conversational memory is the
        # session, and a session is retrievable through its anchor node, which
        # carries the whole session file as lexical text when ``source_root``
        # is given. Ranking those anchors on their text with BM25 alone — no
        # embedding lane, a whole-session vector against a short query is
        # noise (0.896 -> 0.914 recall@10 without it); no prefix-expanding
        # lexical lane either, it costs 0.004 recall and 0.032 MRR on documents
        # — and only then filling the remaining slots with node hits is what
        # closes the gap to a plain BM25 over the sessions. Measured on LoCoMo,
        # nine conversations, gold-session recall@10 / MRR: node ranking
        # 0.878 / 0.711, this 0.918 / 0.774, BM25 over the session documents
        # 0.923 / 0.766. The default path is untouched: on the
        # 148-paper corpus the same two-stage ranking scored WORSE (0.652 ->
        # 0.595 recall@10), because there the claim and span nodes are the
        # signal and whole-paper text drowns them — and dropping the embedding
        # lane there costs even more (0.652 -> 0.528). Opt in per caller.
        anchors = [
            n for n in (candidate_filter if candidate_filter is not None else graph.nodes)
            if n.type in _SOURCE_ANCHOR_TYPES and n.source_path
        ]
        # Overfetch, then one anchor per document. A document can own more
        # than one anchor node — a SourceDocument and a Paper for the same
        # file, or the leftovers of a chunked compile — and ten anchors for
        # four documents is a budget spent on repeats: on LongMemEval group 0
        # the top ten held four distinct sessions on a third of the questions.
        first = hybrid_search(
            graph, query, top_k=max(1, int(top_k)) * 4,
            weights={"bm25": 1.0, "lexical": 0.0, "embedding": 0.0},
            mode="hybrid", backend=backend, candidate_filter=anchors,
            vector_cache=vector_cache, bm25_index=None, source_root=source_root,
        ) if anchors else None
        if first is not None:
            one_per_doc: List[ScoredNode] = []
            docs_taken: set = set()
            for scored in first.scored:
                doc = str(scored.node.source_path or "")
                if doc in docs_taken:
                    continue
                docs_taken.add(doc)
                one_per_doc.append(scored)
                if len(one_per_doc) >= max(1, int(top_k)):
                    break
            first = dataclasses.replace(first, scored=one_per_doc)
        rest = hybrid_search(
            graph, query, top_k=top_k, weights=weights, mode=mode, backend=backend,
            candidate_filter=candidate_filter, vector_cache=vector_cache,
            bm25_index=bm25_index, source_root=source_root, profile=profile,
        )
        if first is None:
            return rest
        budget = max(1, int(top_k))
        merged: List[ScoredNode] = list(first.scored)
        seen = {s.node.id for s in merged}
        docs = {str(s.node.source_path or "") for s in merged}
        # Fill the document budget before spending slots on node hits. A
        # question whose words overlap few sessions matches few anchors, and
        # the node hits that follow repeat sessions already listed — on
        # LongMemEval 20 of 60 questions came back with fewer than k distinct
        # documents, a handicap no BM25 baseline has because it always returns
        # k documents. The unmatched anchors are appended by embedding
        # similarity, then in graph order, so the caller always gets k distinct
        # documents when the corpus has them. Deterministic either way.
        if len(docs) < budget:
            remaining = [a for a in anchors if str(a.source_path or "") not in docs]
            if remaining:
                order: List[ResearchNode] = []
                if backend is not None:
                    tail = hybrid_search(
                        graph, query, top_k=len(remaining),
                        weights={"bm25": 0.0, "lexical": 0.0, "embedding": 1.0},
                        mode="hybrid", backend=backend, candidate_filter=remaining,
                        vector_cache=vector_cache, bm25_index=None, source_root=None,
                    )
                    order = [s.node for s in tail.scored]
                ranked_ids = {n.id for n in order}
                order += [a for a in remaining if a.id not in ranked_ids]
                for node in order:
                    if len(docs) >= budget:
                        break
                    doc = str(node.source_path or "")
                    if doc in docs or node.id in seen:
                        continue
                    merged.append(ScoredNode(node=node, score=0.0, per_lane={}, ranks={}))
                    seen.add(node.id); docs.add(doc)
        merged += [s for s in rest.scored if s.node.id not in seen]
        return dataclasses.replace(
            rest, scored=merged[:budget],
            total_matches=max(rest.total_matches, first.total_matches),
        )

    _t_call = time.perf_counter() if profile else 0.0
    nodes = list(candidate_filter) if candidate_filter is not None else list(graph.nodes)
    # Build the reported weights dict by merging the override on top of the
    # defaults (see selected_weights below for the main-path rationale).
    reported_weights: Dict[str, float] = dict(DEFAULT_WEIGHTS)
    if weights:
        reported_weights.update(weights)
    if not nodes:
        return HybridSearchResult(
            query=query,
            mode=mode,
            backend=(backend.name if backend else "n/a"),
            weights=reported_weights,
            scored=[],
            total_matches=0,
            profile=(
                _short_circuit_profile(
                    query=query,
                    mode=mode,
                    backend=(backend.name if backend else "n/a"),
                    started=_t_call,
                    candidates_in=0,
                    admitted=0,
                    returned=0,
                    vector_cache=vector_cache is not None,
                    bm25_index=False,
                )
                if profile
                else None
            ),
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
            weights=reported_weights,
            scored=scored,
            total_matches=len(nodes),
            profile=(
                _short_circuit_profile(
                    query=query,
                    mode=mode,
                    backend=(backend.name if backend else "n/a"),
                    started=_t_call,
                    candidates_in=len(nodes),
                    admitted=len(nodes),
                    returned=len(scored),
                    vector_cache=vector_cache is not None,
                    bm25_index=False,
                )
                if profile
                else None
            ),
        )

    # Merge any caller override on top of DEFAULT_WEIGHTS so a partial dict
    # like ``{"embedding": 0}`` disables *only* the embedding lane instead of
    # silently zeroing BM25 and lexical too (which would empty the hybrid
    # candidate gate and return zero results).
    selected_weights: Dict[str, float] = dict(DEFAULT_WEIGHTS)
    if weights:
        selected_weights.update(weights)
    if mode == "bm25":
        selected_weights = {"bm25": 1.0, "lexical": 0.0, "embedding": 0.0}
    elif mode in ("lexical", "legacy"):
        selected_weights = {"bm25": 0.0, "lexical": 1.0, "embedding": 0.0}
    elif mode == "embedding":
        selected_weights = {"bm25": 0.0, "lexical": 0.0, "embedding": 1.0}
    elif mode != "hybrid":
        raise ValueError(f"Unknown mode: {mode!r}")

    texts = [_node_text(node) for node in nodes]
    # The embedding lane and the hybrid candidate gate keep reading ``texts``.
    # Only the two lexical lanes read ``lex_texts``, which is ``texts`` itself
    # unless a caller opted in with ``source_root``.
    lex_texts = _lexical_texts(nodes, texts, source_root)
    query_tokens = _tokenize(query)

    lane_scores: Dict[str, List[float]] = {}
    # Per-lane wall time, sampled between lanes so the timed region is the lane
    # call itself and nothing else. Only touched under ``profile``.
    lane_ms: Dict[str, float] = {}
    _t_lane = time.perf_counter() if profile else 0.0

    # Corpus tokenisation now happens INSIDE the BM25 lane rather than above as
    # shared setup. It was never shared — the lexical and embedding lanes read
    # ``texts``, not tokens — and profiling it as setup understated the lane by
    # the 197 ms it costs on this project's own graph, which is most of the
    # measurement that motivated the index. It is also skipped outright when
    # BM25 is disabled or the index serves the corpus.
    _bm25_used_index = False
    _bm25_hits = 0
    _bm25_misses = 0
    if selected_weights.get("bm25", 0.0) > 0:
        prepared: Optional[PreparedCorpus] = None
        postings: Optional[Dict[str, Dict[int, int]]] = None
        # ``_rank_bm25_available`` is a correctness gate, not a preference: the
        # index reproduces the LOCAL Okapi, and rank_bm25 replaces it.
        if bm25_index is not None and not _rank_bm25_available():
            _before = (bm25_index.stats.hits, bm25_index.stats.misses)
            prepared = bm25_index.prepare(lex_texts, _tokenize)
            _bm25_hits = bm25_index.stats.hits - _before[0]
            _bm25_misses = bm25_index.stats.misses - _before[1]
            if prepared is not None:
                postings = bm25_index.postings(query_tokens)
        if prepared is not None and postings is not None:
            lane_scores["bm25"] = _bm25_scores_indexed(
                query_tokens, prepared, postings
            )
            _bm25_used_index = True
        else:
            # Fall back to tokenising in memory. The counters are cleared with
            # it so ``bm25_index=False`` always means "the hit/miss numbers on
            # this lane are structurally zero" — a half-warm index reported as
            # warm on a query it did not serve is exactly the silent fast-path
            # lie the profile exists to prevent.
            _bm25_hits = _bm25_misses = 0
            lane_scores["bm25"] = _bm25_scores(
                query_tokens, [_tokenize(text) for text in lex_texts]
            )
    else:
        lane_scores["bm25"] = [0.0] * len(nodes)
    if profile:
        _now = time.perf_counter()
        lane_ms["bm25"] = (_now - _t_lane) * 1000.0
        _t_lane = _now

    if selected_weights.get("lexical", 0.0) > 0:
        lane_scores["lexical"] = _lexical_scores(query, lex_texts)
    else:
        lane_scores["lexical"] = [0.0] * len(nodes)
    if profile:
        _now = time.perf_counter()
        lane_ms["lexical"] = (_now - _t_lane) * 1000.0
        _t_lane = _now
    # Resolve the embedding backend once and reuse it for both the lane scores
    # and the candidate gate. Resolve only when the embedding lane is active OR
    # we're in hybrid mode (the only case where the gate consults backend
    # identity) — pure bm25/lexical single-lane queries never touch embeddings,
    # so we leave ``embed_backend = backend`` (possibly None) and do NOT call
    # ``active_embedding_backend()``, which would emit the fail-loud warning.
    embed_backend = backend
    if selected_weights.get("embedding", 0.0) > 0 or mode == "hybrid":
        embed_backend = backend or active_embedding_backend()
    _embedding_ran = selected_weights.get("embedding", 0.0) > 0
    # Snapshot the cache's OWN counters, not the process-wide ones: the process
    # totals move under any other search in flight, which would attribute a
    # neighbour's misses to this query.
    if profile and vector_cache is not None:
        _cache_before = (
            vector_cache.stats.embed_calls,
            vector_cache.stats.hits,
            vector_cache.stats.misses,
        )
    _embedding_vectorized = False
    if _embedding_ran:
        # Vectorised first, scalar as the fallback. ``_embedding_scores_vectorized``
        # returns None WITHOUT embedding anything when numpy is absent, so the
        # fallback re-embeds nothing and the cache counters below stay honest.
        embedding_scores = _embedding_scores_vectorized(
            query, texts, embed_backend, vector_cache
        )
        if embedding_scores is None:
            embedding_scores = _embedding_scores(
                query, texts, embed_backend, vector_cache
            )
        else:
            _embedding_vectorized = True
        lane_scores["embedding"] = embedding_scores
        backend_name = embed_backend.name
    else:
        lane_scores["embedding"] = [0.0] * len(nodes)
        backend_name = backend.name if backend else "disabled"
    if profile:
        _now = time.perf_counter()
        lane_ms["embedding"] = (_now - _t_lane) * 1000.0
        if vector_cache is not None:
            _embed_calls, _cache_hits, _cache_misses = (
                vector_cache.stats.embed_calls - _cache_before[0],
                vector_cache.stats.hits - _cache_before[1],
                vector_cache.stats.misses - _cache_before[2],
            )
        else:
            # Uncached, ``embed_texts`` is one ``backend.embed`` over the whole
            # batch and nothing counts it. Report that 1 rather than the 0 the
            # absent counters would suggest — a silent "no model call" on the
            # one path that always makes one is the failure this whole step
            # exists to prevent. ``vector_cache=False`` on the profile is what
            # keeps the 0 hits/misses from reading as a warm cache.
            _embed_calls = 1 if _embedding_ran else 0
            _cache_hits = _cache_misses = 0

    fused, ranks = _fuse(lane_scores, selected_weights, len(nodes))

    # Candidate-generation gate (RETR-02): backend-quality dependent.
    #
    # In hybrid mode the rule depends on the active embedding backend:
    #   - HASH stub (no semantics): require lexical evidence (BM25 or lexical
    #     lane) — the embedding lane is a re-ranker only. An opaque hash-bucket
    #     cosine has no real semantics and would drag in unrelated nodes for
    #     rare-token queries (e.g. CodeFunction names no public node shares).
    #   - REAL backend (model2vec / sentence-transformers): the embedding lane
    #     MAY surface a node on its own (RETR-02 candidate generation), since a
    #     semantic hit without lexical overlap is exactly the paraphrase case we
    #     want to admit.
    # In single-lane modes (bm25 / lexical / embedding) the active lane *is* the
    # gate, which is the obvious user expectation.
    if mode == "hybrid":
        _hash_backend = isinstance(embed_backend, HashEmbeddingBackend)

        def _is_candidate(idx: int) -> bool:
            lexical_hit = (
                lane_scores["bm25"][idx] > 0 or lane_scores["lexical"][idx] > 0
            )
            # Embedding-only admission on the real-backend path requires a
            # cosine FLOOR, not just ``> 0``: real vectors are ~never orthogonal
            # to a query, so ``> 0`` would admit nearly every node and inflate
            # ``total_matches``. A genuine paraphrase/synonym hit clears the
            # floor; unrelated low-cosine nodes do not. (RETR-02 intent — admit
            # semantic hits — is preserved; only the threshold tightens.)
            embed_hit = lane_scores["embedding"][idx] >= EMBED_CANDIDATE_MIN_COSINE
            return lexical_hit or (not _hash_backend and embed_hit)
    else:
        active = [lane for lane, w in selected_weights.items() if w > 0]
        def _is_candidate(idx: int) -> bool:
            return any(lane_scores[lane][idx] > 0 for lane in active)

    indexed = sorted(
        ((fused[idx], idx) for idx in range(len(nodes)) if _is_candidate(idx)),
        key=lambda pair: (-pair[0], pair[1]),
    )
    # ``total_matches`` reflects every candidate that survived the
    # candidate-generation gate *before* paging — callers depend on this to
    # display "X of N matches" without re-running the search.
    total_matches = len(indexed)
    bounded = max(1, min(int(top_k), len(nodes)))
    scored: List[ScoredNode] = []
    winners: List[WinnerAttribution] = []
    for fused_score, idx in indexed[:bounded]:
        scored.append(
            ScoredNode(
                node=nodes[idx],
                score=float(fused_score),
                per_lane={lane: float(lane_scores[lane][idx]) for lane in lane_scores},
                ranks={lane: int(ranks[lane][idx]) for lane in ranks},
            )
        )
        if profile:
            # _fuse's own contribution test, verbatim: a lane adds
            # ``weight / (RRF_K + rank)`` only when its weight is positive AND
            # the doc ranked inside the corpus. Re-deriving it from per_lane
            # scores would over-report every zero-weight lane.
            winners.append(
                WinnerAttribution(
                    node_id=nodes[idx].id,
                    score=float(fused_score),
                    lanes=tuple(
                        lane
                        for lane in lane_scores
                        if selected_weights.get(lane, 0.0) > 0
                        and ranks[lane][idx] <= len(nodes)
                    ),
                )
            )

    call_profile: Optional[RetrievalProfile] = None
    if profile:
        call_profile = RetrievalProfile(
            query=query,
            mode=mode,
            backend=backend_name,
            candidates_in=len(nodes),
            admitted=total_matches,
            returned=len(scored),
            ms=(time.perf_counter() - _t_call) * 1000.0,
            vector_cache=vector_cache is not None,
            bm25_index=_bm25_used_index,
            lanes={
                lane: LaneProfile(
                    lane=lane,
                    weight=float(selected_weights.get(lane, 0.0)),
                    # A zero-weight lane never saw the corpus — its score list
                    # is a zero-fill, not a result.
                    candidates_in=(
                        len(nodes) if selected_weights.get(lane, 0.0) > 0 else 0
                    ),
                    scored=sum(1 for score in lane_scores[lane] if score > 0),
                    embed_calls=_embed_calls if lane == "embedding" else 0,
                    # Two lanes report hits/misses against two different
                    # sidecars — vectors on ``embedding``, postings on
                    # ``bm25`` — and the third has none, so it reports zero.
                    cache_hits=(
                        _cache_hits
                        if lane == "embedding"
                        else (_bm25_hits if lane == "bm25" else 0)
                    ),
                    cache_misses=(
                        _cache_misses
                        if lane == "embedding"
                        else (_bm25_misses if lane == "bm25" else 0)
                    ),
                    ms=lane_ms.get(lane, 0.0),
                    # Only the embedding lane has a vectorised form; the other
                    # two report False because they never had one, not because
                    # they fell back.
                    vectorized=(_embedding_vectorized if lane == "embedding" else False),
                )
                for lane in lane_scores
            },
            winners=winners,
        )

    return HybridSearchResult(
        query=query,
        mode=mode,
        backend=backend_name,
        weights=selected_weights,
        scored=scored,
        total_matches=total_matches,
        profile=call_profile,
    )

"""Persisted embedding vectors — a CACHE, deliberately not an index.

Before this module every embedding call site re-embedded its whole corpus on
every invocation: ``hybrid._embedding_scores`` embedded ``[query, *corpus]``
per query, and the canonicalization and federation passes embedded their
candidate blocks per run. The model call is what dominates that cost, so this
module removes it and nothing else.

What it is NOT: an ANN / HNSW index. ``hybrid_search`` is filter-first by
design (``candidate_filter`` takes an arbitrary pre-filtered node iterable and
the MCP layer uses it for type / kind / code-node / ``include_superseded``
filtering), and an approximate index cannot honour an arbitrary pre-filter
without falling back to brute force anyway. Exact cosine over cached vectors
keeps every score identical to the uncached path — the cache is only allowed
to change what retrieval COSTS, never what it returns.

The key is ``(backend_name, backend_dim, sha256(embedded_text))`` and
deliberately NOT the node id:

* a renamed or re-described node produces different text, misses, re-embeds;
* an unchanged node hits even when the project moved on disk, when the graph
  was recompiled from scratch, or when canonicalization rewrote its id;
* vectors from two different models never meet, because two models' spaces
  are not comparable and a silent mix would corrupt cosine rather than fail.

State lives ONLY in the ``node_vectors`` SQLite table (see
:mod:`tesserae.graph_stores.sqlite`) — never in node metadata and never in
``graph.json``, which must stay byte-identical across compiles. This mirrors
:mod:`tesserae.memory.store`: one typed accessor so no call site embeds raw
SQL.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple, Union

# The two private codec helpers are imported deliberately: they ARE the wire
# format of the ``node_vectors`` blob, and this module is the one typed
# accessor over that table, so re-deriving the packing here is how the two
# would drift apart.
from ..graph_stores.sqlite import SqliteGraphStore, _decode_vector, _encode_vector

if TYPE_CHECKING:  # pragma: no cover - typing only (hybrid imports this module)
    from .hybrid import EmbeddingBackend

PathLike = Union[str, Path]

_LOG = logging.getLogger(__name__)


def node_embedding_text(node: object) -> str:
    """The ONE text a node is embedded as.

    ``canonicalization`` and ``federation`` each built this string inline and
    byte-identically, which meant one cache served both by coincidence. This
    function makes it true by construction instead: if the shape ever changes
    it changes in one place, and both sides keep sharing cached vectors rather
    than silently splitting into two half-warm caches.
    """
    name = getattr(node, "name", "") or ""
    description = getattr(node, "description", "") or ""
    return f"{name}. {description.strip()}".strip()


def text_key(text: str) -> str:
    """Content key for one embedded text (the cache's third key component)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass
class VectorCacheStats:
    """Counters for one cache, and for the process as a whole.

    ``errors`` is not decoration: a cache that cannot reach its sidecar
    degrades to plain embedding, which is correct but silently slow. Reporting
    the count is what keeps that from looking like a fast path.
    """

    hits: int = 0
    misses: int = 0
    embed_calls: int = 0
    errors: int = 0

    def add(self, other: "VectorCacheStats") -> None:
        self.hits += other.hits
        self.misses += other.misses
        self.embed_calls += other.embed_calls
        self.errors += other.errors


# Process-wide totals across every VectorCache instance. ``embedding_status``
# reports these so an operator can tell a warm cache from a cold one without
# instrumenting the call sites.
_PROCESS_STATS = VectorCacheStats()


def process_stats() -> VectorCacheStats:
    """Snapshot of the process-wide cache counters."""
    return VectorCacheStats(
        hits=_PROCESS_STATS.hits,
        misses=_PROCESS_STATS.misses,
        embed_calls=_PROCESS_STATS.embed_calls,
        errors=_PROCESS_STATS.errors,
    )


def reset_process_stats() -> None:
    """Zero the process-wide counters (tests assert on exact hit/miss counts)."""
    _PROCESS_STATS.hits = 0
    _PROCESS_STATS.misses = 0
    _PROCESS_STATS.embed_calls = 0
    _PROCESS_STATS.errors = 0


class VectorCache:
    """Read-through embedding cache over the ``node_vectors`` sidecar."""

    def __init__(self, db_path: PathLike) -> None:
        self.db_path = Path(db_path)
        self.stats = VectorCacheStats()

    # -- construction ------------------------------------------------- #

    @classmethod
    def for_project(cls, project_root: Optional[PathLike]) -> Optional["VectorCache"]:
        """Cache in ``<project_root>/.tesserae/sqlite.db``, or ``None``.

        Returns ``None`` when there is no project root or no ``.tesserae``
        directory — a store-backed or ad-hoc graph has nowhere to put a
        sidecar, and retrieval must keep working there uncached rather than
        creating a directory as a side effect of a read.
        """
        if project_root is None:
            return None
        root = Path(project_root)
        if not (root / ".tesserae").is_dir():
            return None
        return cls(root / ".tesserae" / "sqlite.db")

    @classmethod
    def for_graph_path(cls, graph_path: Optional[PathLike]) -> Optional["VectorCache"]:
        """Cache for a ``<root>/.tesserae/graph.json`` path, or ``None``.

        Ad-hoc output paths outside the canonical layout get no cache, the
        same graceful fallback ``_project_root_for_graph_path`` makes.
        """
        if not graph_path:
            return None
        path = Path(graph_path)
        if path.parent.name != ".tesserae":
            return None
        return cls(path.parent / "sqlite.db")

    # -- read-through ------------------------------------------------- #

    def embed(
        self,
        backend: "EmbeddingBackend",
        texts: Sequence[str],
    ) -> List[List[float]]:
        """Embed ``texts``, serving what is cached and persisting what is not.

        Returns one vector per input in input order, identical to
        ``backend.embed(texts)``. Duplicate texts inside one batch are embedded
        once. A sidecar failure is counted and logged, then degrades to a plain
        ``backend.embed`` — a cache must never be able to break retrieval.
        """
        keys, blobs = self._resolve_blobs(backend, texts)
        return [_decode_vector(blobs[key]) for key in keys]

    def embed_blobs(
        self,
        backend: "EmbeddingBackend",
        texts: Sequence[str],
    ) -> List[bytes]:
        """:meth:`embed`, returning the packed float64 rows instead of lists.

        Same cache semantics, same persistence, same order — the ONLY
        difference is that the caller receives the stored bytes. The vectorised
        embedding lane wants those directly: joining them and handing the
        result to ``numpy.frombuffer`` rebuilds the corpus matrix without ever
        materialising 47k Python lists, which is where most of the decode cost
        lived. ``_decode_vector`` of these bytes is the value :meth:`embed`
        returns, bit for bit, so the two views can never disagree on a score.
        """
        keys, blobs = self._resolve_blobs(backend, texts)
        return [blobs[key] for key in keys]

    def _resolve_blobs(
        self,
        backend: "EmbeddingBackend",
        texts: Sequence[str],
    ) -> Tuple[List[str], Dict[str, bytes]]:
        """Shared core of :meth:`embed` / :meth:`embed_blobs`.

        Returns the per-input key sequence plus the packed vector for every
        distinct key. Blobs rather than floats are the internal currency
        because that is what SQLite stores and what a write needs, so neither
        caller pays an encode/decode round trip the other's format forces.
        """
        wanted = list(texts)
        if not wanted:
            return [], {}
        backend_name = str(getattr(backend, "name", type(backend).__name__))
        backend_dim = int(getattr(backend, "dim", 0) or 0)

        keys = [text_key(text) for text in wanted]
        # Unique texts, first-occurrence order: a corpus with two identically
        # described nodes must cost one model call, not two.
        pending: Dict[str, str] = {}
        for key, text in zip(keys, wanted):
            pending.setdefault(key, text)

        cached: Dict[str, bytes] = {}
        store = self._store()
        if store is not None:
            try:
                cached = store.read_node_vector_blobs(
                    backend_name, backend_dim, pending.keys()
                )
            except Exception:  # sqlite locked / corrupt / unreadable
                self._count_error()
                _LOG.debug("vector cache read failed at %s", self.db_path, exc_info=True)
                cached = {}
        # A stored vector of the wrong width cannot be trusted as this
        # backend's output; treat it as a miss rather than scoring against it.
        # Width is checked in bytes here (8 per float64) rather than in decoded
        # elements — same test, one less decode.
        if backend_dim:
            cached = {k: v for k, v in cached.items() if len(v) == backend_dim * 8}

        missing = [key for key in pending if key not in cached]
        if missing:
            fresh = backend.embed([pending[key] for key in missing])
            if len(fresh) != len(missing):
                # Refuse rather than persist a misaligned text→vector pairing:
                # a wrong vector under the right key would poison every later
                # query and could never be told apart from a bad model.
                raise ValueError(
                    f"embedding backend {backend_name!r} returned {len(fresh)} "
                    f"vectors for {len(missing)} texts"
                )
            self.stats.embed_calls += 1
            _PROCESS_STATS.embed_calls += 1
            for key, vector in zip(missing, fresh):
                cached[key] = _encode_vector(vector)
            if store is not None:
                try:
                    store.write_node_vector_blobs_many(
                        backend_name,
                        backend_dim,
                        ((key, cached[key]) for key in missing),
                    )
                except Exception:
                    self._count_error()
                    _LOG.debug(
                        "vector cache write failed at %s", self.db_path, exc_info=True
                    )

        hits = len(pending) - len(missing)
        self.stats.hits += hits
        self.stats.misses += len(missing)
        _PROCESS_STATS.hits += hits
        _PROCESS_STATS.misses += len(missing)
        return keys, cached

    def count(self, backend: "EmbeddingBackend") -> int:
        """Rows cached for ``backend``'s ``(name, dim)`` key, 0 if unavailable."""
        store = self._store()
        if store is None:
            return 0
        try:
            return store.count_node_vectors(
                str(getattr(backend, "name", type(backend).__name__)),
                int(getattr(backend, "dim", 0) or 0),
            )
        except Exception:
            self._count_error()
            _LOG.debug("vector cache count failed at %s", self.db_path, exc_info=True)
            return 0

    # -- internals ----------------------------------------------------- #

    def _store(self) -> Optional[SqliteGraphStore]:
        """Open the sidecar, or ``None`` when it cannot be opened.

        Not memoised: :class:`SqliteGraphStore` uses short-lived connections so
        the sidecar stays safe under the multi-process access this repo already
        assumes (a compile and an MCP read can be in flight at once).
        """
        try:
            return SqliteGraphStore(self.db_path)
        except Exception:
            self._count_error()
            _LOG.debug("vector cache unavailable at %s", self.db_path, exc_info=True)
            return None

    def _count_error(self) -> None:
        self.stats.errors += 1
        _PROCESS_STATS.errors += 1


def embed_texts(
    backend: "EmbeddingBackend",
    texts: Sequence[str],
    cache: Optional[VectorCache] = None,
) -> List[List[float]]:
    """The single embedding entry point for every call site in the package.

    With ``cache=None`` this is exactly ``backend.embed(texts)`` — the uncached
    path stays available for callers with nowhere to persist (in-memory graphs,
    tests) and returns byte-identical vectors either way.
    """
    if cache is None:
        return backend.embed(list(texts))
    return cache.embed(backend, texts)

"""A persisted inverted index for the BM25 lane — a CACHE, not a new ranker.

Before this module the BM25 lane rebuilt its whole world on every query.
``hybrid_search`` tokenised all 46,926 candidate documents (196 ms measured on
this project's own graph), ``_bm25_scores`` then walked every one of them to
build a document-frequency table over the full 94,929-term vocabulary (146 ms)
even though only the query's two or three terms are ever read back out of it,
and finally scored all 46,926 documents (65 ms) to find the 172 that matched.
Query-independent rebuild was 85% of the lane's cost. Neo4j gets a Lucene
fulltext index free in Community Edition; per the roadmap's verdict Tesserae
builds one as a sidecar rather than adopting the database.

What it is NOT: a different ranking function, and not an approximate one.
``hybrid_search`` is filter-first by design — ``candidate_filter`` takes an
arbitrary pre-filtered node iterable and the MCP layer uses it for type / kind
/ code-node / ``include_superseded`` filtering — so the index may never assume
it is being asked about the whole corpus. Everything set-dependent (``n_docs``,
``avgdl``, and the document frequency of each query term) is therefore
recomputed from the candidates the caller actually passed; only the
per-document facts, which are pure functions of one document's text, come out
of the sidecar. That is what lets a warm index return scores that are equal to
a cold one by exact float comparison rather than approximately.

The key is ``sha256(document_text)`` and deliberately NOT the node id, for the
same reason :mod:`tesserae.retrieval.vector_cache` keys the way it does:

* a renamed or re-described node produces different text, misses, is re-indexed
  under its new key, and the old key's row is simply never asked for again;
* a node whose text did not change hits even when the project moved on disk,
  when the graph was recompiled from scratch, or when canonicalization rewrote
  its id;
* a node deleted from the corpus leaves its row behind, and that row cannot
  affect any score: document frequency is counted over the CANDIDATES, and a
  candidate is only ever looked up by its own key. The corpus shrinking makes
  the sidecar bigger than it needs to be, never wrong.

State lives ONLY in the ``bm25_docs`` / ``bm25_postings`` SQLite tables (see
:mod:`tesserae.graph_stores.sqlite`) — never in node metadata and never in
``graph.json``, which must stay byte-identical across compiles.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple, Union

from ..graph_stores.sqlite import SqliteGraphStore

PathLike = Union[str, Path]

_LOG = logging.getLogger(__name__)

#: A tokeniser, supplied by the caller. The index is only allowed to change
#: what the lane COSTS, so it must tokenise with the lane's own function rather
#: than a copy that can drift out of step with it.
Tokenizer = Callable[[str], Sequence[str]]


def doc_key(text: str) -> str:
    """Content key for one BM25 document (sha256 of its text)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PreparedCorpus:
    """Per-document facts for ONE candidate set, in the caller's order.

    Deliberately positional rather than keyed: two candidates may share a text
    (and therefore a ``doc_id``), and BM25 counts DOCUMENTS — a corpus holding
    the same text twice has a document frequency of two for its terms. Keying
    this by ``text_key`` would silently collapse that to one and change scores.
    """

    doc_ids: Tuple[int, ...]
    doc_lens: Tuple[int, ...]


@dataclass
class Bm25IndexStats:
    """Counters for one index, and for the process as a whole.

    ``errors`` is not decoration: an index that cannot reach its sidecar
    degrades to tokenising the corpus in memory, which is correct but silently
    slow — and slower than never having had an index, because the failed
    attempt is paid first. Reporting the count is what keeps that from looking
    like a fast path.
    """

    hits: int = 0
    misses: int = 0
    indexed: int = 0
    errors: int = 0

    def add(self, other: "Bm25IndexStats") -> None:
        self.hits += other.hits
        self.misses += other.misses
        self.indexed += other.indexed
        self.errors += other.errors


# Process-wide totals across every Bm25Index instance, reported the same way
# the vector cache's are, so a cold index cannot be mistaken for a warm one.
_PROCESS_STATS = Bm25IndexStats()


def process_stats() -> Bm25IndexStats:
    """Snapshot of the process-wide index counters."""
    return Bm25IndexStats(
        hits=_PROCESS_STATS.hits,
        misses=_PROCESS_STATS.misses,
        indexed=_PROCESS_STATS.indexed,
        errors=_PROCESS_STATS.errors,
    )


def reset_process_stats() -> None:
    """Zero the process-wide counters (tests assert on exact hit/miss counts)."""
    _PROCESS_STATS.hits = 0
    _PROCESS_STATS.misses = 0
    _PROCESS_STATS.indexed = 0
    _PROCESS_STATS.errors = 0


class Bm25Index:
    """Read-through inverted index over the ``bm25_*`` sidecar tables."""

    def __init__(self, db_path: PathLike) -> None:
        self.db_path = Path(db_path)
        self.stats = Bm25IndexStats()

    # -- construction ------------------------------------------------- #

    @classmethod
    def for_project(cls, project_root: Optional[PathLike]) -> Optional["Bm25Index"]:
        """Index in ``<project_root>/.tesserae/sqlite.db``, or ``None``.

        ``None`` when there is no project root or no ``.tesserae`` directory —
        an ad-hoc or store-backed graph has nowhere to put a sidecar, and
        retrieval must keep working there unindexed rather than creating a
        directory as a side effect of a read.
        """
        if project_root is None:
            return None
        root = Path(project_root)
        if not (root / ".tesserae").is_dir():
            return None
        return cls(root / ".tesserae" / "sqlite.db")

    @classmethod
    def for_graph_path(cls, graph_path: Optional[PathLike]) -> Optional["Bm25Index"]:
        """Index for a ``<root>/.tesserae/graph.json`` path, or ``None``."""
        if not graph_path:
            return None
        path = Path(graph_path)
        if path.parent.name != ".tesserae":
            return None
        return cls(path.parent / "sqlite.db")

    # -- read-through ------------------------------------------------- #

    def prepare(
        self,
        texts: Sequence[str],
        tokenize: Tokenizer,
    ) -> Optional[PreparedCorpus]:
        """Resolve every text to its ``(doc_id, doc_len)``, indexing misses.

        Returns ``None`` — meaning "score this query without me" — whenever the
        result would be PARTIAL. A corpus half in the index is the one outcome
        that must never be served: the documents it could not resolve would
        score 0.0 while still counting in ``n_docs`` and ``avgdl``, so an
        unavailable sidecar would not make retrieval slower, it would make it
        wrong. Falling back re-tokenises in memory, which costs the caller the
        time it was trying to save and nothing else.
        """
        wanted = list(texts)
        if not wanted:
            return PreparedCorpus(doc_ids=(), doc_lens=())
        keys = [doc_key(text) for text in wanted]

        store = self._store()
        if store is None:
            return None
        try:
            known = store.read_bm25_docs()
        except Exception:  # sqlite locked / corrupt / unreadable
            self._count_error()
            _LOG.debug("bm25 index read failed at %s", self.db_path, exc_info=True)
            return None

        # Unique missing texts, first-occurrence order: a corpus with two
        # identical documents is tokenised once, then counted twice.
        pending: Dict[str, str] = {}
        for key, text in zip(keys, wanted):
            if key not in known:
                pending.setdefault(key, text)

        if pending:
            rows: List[Tuple[str, int, Dict[str, int]]] = []
            for key, text in pending.items():
                tokens = list(tokenize(text))
                tf: Dict[str, int] = {}
                for term in tokens:
                    tf[term] = tf.get(term, 0) + 1
                rows.append((key, len(tokens), tf))
            try:
                store.write_bm25_docs_many(rows)
                known = store.read_bm25_docs()
            except Exception:
                self._count_error()
                _LOG.debug(
                    "bm25 index write failed at %s", self.db_path, exc_info=True
                )
                return None
            self.stats.indexed += len(rows)
            _PROCESS_STATS.indexed += len(rows)

        doc_ids: List[int] = []
        doc_lens: List[int] = []
        for key in keys:
            entry = known.get(key)
            if entry is None:
                # A write reported success and the row is still absent: refuse
                # rather than score a corpus with a hole in it.
                self._count_error()
                _LOG.debug("bm25 index missing key after write at %s", self.db_path)
                return None
            doc_ids.append(entry[0])
            doc_lens.append(entry[1])

        misses = len(pending)
        self.stats.hits += len(keys) - misses
        self.stats.misses += misses
        _PROCESS_STATS.hits += len(keys) - misses
        _PROCESS_STATS.misses += misses
        return PreparedCorpus(doc_ids=tuple(doc_ids), doc_lens=tuple(doc_lens))

    def postings(self, terms: Sequence[str]) -> Optional[Dict[str, Dict[int, int]]]:
        """``{term: {doc_id: tf}}``, or ``None`` when the sidecar is unreadable.

        A term absent from the index maps to an empty dict rather than being
        omitted, so a caller iterating query terms never has to distinguish
        "not in the corpus" from "the lookup did not happen".
        """
        store = self._store()
        if store is None:
            return None
        wanted = list(dict.fromkeys(terms))
        try:
            found = store.read_bm25_postings(wanted)
        except Exception:
            self._count_error()
            _LOG.debug("bm25 postings read failed at %s", self.db_path, exc_info=True)
            return None
        return {term: found.get(term, {}) for term in wanted}

    def count(self) -> int:
        """Indexed documents, 0 when the sidecar is unavailable."""
        store = self._store()
        if store is None:
            return 0
        try:
            return store.count_bm25_docs()
        except Exception:
            self._count_error()
            _LOG.debug("bm25 index count failed at %s", self.db_path, exc_info=True)
            return 0

    # -- internals ----------------------------------------------------- #

    def _store(self) -> Optional[SqliteGraphStore]:
        """Open the sidecar, or ``None`` when it cannot be opened.

        Not memoised, for the reason :class:`~tesserae.retrieval.vector_cache.
        VectorCache` gives: :class:`SqliteGraphStore` uses short-lived
        connections so the sidecar stays safe under the multi-process access
        this repo already assumes (a compile and an MCP read can be in flight
        at once).
        """
        try:
            return SqliteGraphStore(self.db_path)
        except Exception:
            self._count_error()
            _LOG.debug("bm25 index unavailable at %s", self.db_path, exc_info=True)
            return None

    def _count_error(self) -> None:
        self.stats.errors += 1
        _PROCESS_STATS.errors += 1

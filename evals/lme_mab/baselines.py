"""The two baselines that need no LLM to build a memory: BM25 and dense.

Both arms answer the same question :class:`evals.lme_mab.adapter.MabMemory`
answers — *which sessions hold the evidence* — and answer it over the same
bytes: :meth:`Session.render`, the exact document text a Tesserae run stages
into its corpus directory. That is the whole point of putting them here rather
than reaching for an off-the-shelf retriever. A baseline indexing the raw
``haystack_sessions`` turns, or a staged ``.md`` read back off disk, would be
scoring a different corpus, and a table whose rows do not share a corpus
compares the corpora.

**Zero LLM calls, zero money, and — with the model already cached — zero
network.** Neither arm touches the money layers ``run.py`` puts in front of the
Tesserae arm, and neither goes through ``MabMemory.query``, which resolves an
OpenAI embedding backend unconditionally. The dense arm's embedder is the
repo's own local ``model2vec`` — ~8 MB, no torch — and it is asked for **by
name**.

The network half of that used to be false, and measured with a spy on
``socket.getaddrinfo`` it was false on every run: ``StaticModel.from_pretrained``
contacts ``huggingface.co:443`` to revalidate the repo even when every file is
already in the local cache. So :func:`_offline_hub` switches the hub off around
the one line that loads a model — measured again with the same spy, zero calls —
and a cold cache is now a :class:`RefusedToEmbedLocally` naming the one-time
command that warms it rather than a silent 8 MB download in the middle of a
benchmark. The claim is true because the code holds it, not because it was
worded around.


Why ``prefer="model2vec"`` and never ``"auto"``
-----------------------------------------------

``active_embedding_backend("auto")`` degrades to the non-semantic hash-bucket
stub with nothing but a ``UserWarning`` when no real backend imports
(``hybrid.py:500-507``). A warning is invisible in a benchmark run, and the
stub produces perfectly plausible-looking numbers: dense would score somewhere
between BM25 and Tesserae and nobody reading the table could tell it had
measured a hash function. So the preference is explicit — which also makes a
failed construction re-raise instead of degrading — and the resolved backend's
``name`` is checked against that preference on every use. Anything else is a
:class:`RefusedToEmbedLocally`, never a number.


Why the two arms share everything except their scoring lane
------------------------------------------------------------

:class:`_Arm` owns the corpus, the ranking, the K slice and the shortfall
record; a subclass supplies one method, ``_scores``. The arms are meant to
differ in exactly one thing — how a document is scored against a question — and
any second difference is a confound the table would silently attribute to the
retriever. Two tie-break orders, or two answers to "is a zero-scoring document
retrieved", would move recall@K by more than the lanes do.

That shared rule: a document is returned only if its lane scored it **above
zero**. A BM25 score of zero is not a weak match, it is no shared term at all;
a non-positive cosine is not a weak similarity. Ranking those into the budget
would fill K with documents the lane rejected — and since ties break on index,
an arm that matched nothing would return sessions 0-9 and could hit gold by
luck. Returning fewer than K instead is recorded as a shortfall, in the same
shape :attr:`MabMemory.shortfalls` uses, and the metric never pads it.
"""

from __future__ import annotations

import math
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence

from tesserae.retrieval.hybrid import (
    _bm25_scores,
    _rank_bm25_available,
    _tokenize,
    active_embedding_backend,
)
from tesserae.retrieval.vector_cache import embed_texts

from ..qa.run_qa_eval import Skip
from .adapter import PROTOCOL_K, Session

#: What the dense arm asks :func:`active_embedding_backend` for, and the prefix
#: the backend it gets back must carry. One string for both, because a run that
#: asks for one embedder and checks for another checks nothing.
LOCAL_EMBEDDING_PREFER = "model2vec"


#: The environment variable ``huggingface_hub`` reads to stay off the network.
#: One name, used by :func:`_offline_hub` for both halves of the switch.
_HUB_OFFLINE = "HF_HUB_OFFLINE"


class RefusedToEmbedLocally(Skip):
    """The dense arm's embedder is not the local one, so it does not run.

    A :class:`Skip` — ``run.py`` prints ``SKIP: <what>`` plus the fix and exits
    0 — because the alternative is the failure this module exists to prevent:
    an arm that embedded with something else and printed a number anyway. It is
    also, since the load went offline, the shape a cold model cache arrives in:
    a refusal that names the warm-up command, rather than a download.
    """


@contextmanager
def _offline_hub() -> Iterator[None]:
    """Load from the local cache with the Hugging Face hub switched off.

    BOTH halves of the switch, because the two are read at different times: the
    environment variable is what a ``huggingface_hub`` that has not been
    imported yet will evaluate, and ``constants.HF_HUB_OFFLINE`` is what one
    that has already been imported is still holding. Setting only the variable
    leaves the network open in every process that touched the hub first, which
    is most of them.

    Restored on the way out. Switching the hub off is this load's business, not
    the process's: a benchmark that left it set would break the next thing that
    legitimately wanted to download something.
    """
    previous_env = os.environ.get(_HUB_OFFLINE)
    os.environ[_HUB_OFFLINE] = "1"
    try:
        from huggingface_hub import constants  # type: ignore
    except Exception:  # no hub installed: the variable is all there is to set
        constants = None  # type: ignore[assignment]
    previous_flag = getattr(constants, "HF_HUB_OFFLINE", None) if constants else None
    if constants is not None:
        constants.HF_HUB_OFFLINE = True
    try:
        yield
    finally:
        if constants is not None:
            constants.HF_HUB_OFFLINE = previous_flag
        if previous_env is None:
            os.environ.pop(_HUB_OFFLINE, None)
        else:
            os.environ[_HUB_OFFLINE] = previous_env


def _embedder_remedy() -> str:
    """What actually fixes a failed local load — which is not one thing.

    The package being absent and the model being absent from the cache are
    different faults with different commands, and printing the install one for
    both sends an operator who ran ``uv sync --all-extras`` back to run it
    again. So the cause is READ rather than assumed: if ``model2vec`` imports,
    the install is not what failed.
    """
    try:
        import model2vec  # type: ignore # noqa: F401
    except Exception:
        return ("install it with `uv sync --all-extras` (or `pip install "
                "'tesserae[semantic]'`) and re-run — the arm does not fall "
                "back to the hash stub, whose numbers would look like "
                "semantic retrieval and measure a hash function")
    return ("the package IS installed, so this is the model missing from the "
            "local Hugging Face cache — the arm loads with the hub switched "
            "off and will not download 8MB mid-benchmark. Warm the cache once, "
            "deliberately: `uv run python -c \"from tesserae.retrieval.hybrid "
            "import active_embedding_backend; active_embedding_backend("
            "'model2vec')\"` — that one command is allowed to reach "
            "huggingface.co and this benchmark is not")


# --------------------------------------------------------------------------
# The shared half
# --------------------------------------------------------------------------


class _Arm:
    """Corpus, ranking, budget and shortfall record. Subclasses add a lane.

    Module-private, and deliberately not a ``typing.Protocol``: the arm
    interface is one method — ``search_documents`` — with three implementers in
    one package (these two subclasses and ``adapter.MabMemory``, which shares
    no code with them) and one call site. Duck typing plus
    ``test_all_three_arms_expose_search_documents`` is the stricter check,
    because it calls all three.
    """

    #: How the report names this row in the ``method`` column.
    name = "arm"
    #: The report's ``retriever`` column. Overridden per arm.
    retriever = "none"

    def __init__(self, sessions: Sequence[Session]) -> None:
        self.sessions: List[Session] = list(sessions)
        #: The indexed text, document ``i`` for ``sessions[i]``. ``render()``
        #: and not a re-render: the arms and the Tesserae corpus are one corpus.
        self.documents: List[str] = [session.render() for session in self.sessions]
        #: One entry per query that returned fewer than K documents, in
        #: :attr:`evals.lme_mab.adapter.MabMemory.shortfalls`' shape so both
        #: render through one table.
        self.shortfalls: List[Dict[str, Any]] = []

    @property
    def embedder(self) -> str:
        """The report's ``embedder`` column: none, for a lane with no model.

        The dense arm overrides this to read the LIVE backend's name rather
        than declare one, on ``MabMemory.embedding_backend``'s reasoning — a
        hardcoded declaration passes the control check while the run used
        something else.
        """
        return "none"

    @property
    def meta(self) -> Dict[str, Any]:
        """What the report's §6 row declares about this arm."""
        return {
            "corpus": f"{len(self.documents)} session documents (Session.render)",
            "retriever": self.retriever,
            "embedder": self.embedder,
        }

    def _scores(self, question: str) -> Sequence[float]:
        """One score per document, in :attr:`documents` order. The only lane."""
        raise NotImplementedError

    def search_documents(self, question: str, *, k: int = PROTOCOL_K) -> List[int]:
        """The ``k`` best-scoring session indices, best first. Never padded.

        Ties break on the session index, so a run is reproducible; documents
        the lane did not score above zero are not returned at all (see the
        module docstring), which is why this can come back short.
        """
        scores = list(self._scores(question))
        candidates = [i for i, score in enumerate(scores) if score > 0.0]
        candidates.sort(key=lambda i: (-scores[i], i))
        # ``Session.index`` and not the position: the index is what the staged
        # document name and the gold alignment are both keyed on, and going
        # through it means no caller has to assume they coincide.
        documents = [self.sessions[i].index for i in candidates[:k]]
        if len(documents) < k:
            self.shortfalls.append({
                "question": question,
                "requested": k,
                "returned": len(documents),
                "total_matches": len(candidates),
            })
        return documents


# --------------------------------------------------------------------------
# Lexical: Okapi BM25
# --------------------------------------------------------------------------


class LexicalArm(_Arm):
    """BM25 over the session documents, through the repo's own implementation.

    :func:`tesserae.retrieval.hybrid._bm25_scores` and its tokeniser, the same
    reuse ``tesserae/temporal.py:851-855`` already makes. Using the repo's BM25
    rather than a fresh one keeps the baseline honest in the direction that
    matters: this is the lexical lane Tesserae itself retrieves with, so the
    row measures what the graph adds over it, not what two BM25s disagree on.

    The corpus is tokenised once here; ``_bm25_scores`` rebuilds its own index
    per query, which is ~110 documents of arithmetic and no model call.
    """

    name = "BM25"
    retriever = "Okapi BM25 (hybrid._bm25_scores, k1=1.5, b=0.75)"

    def __init__(self, sessions: Sequence[Session]) -> None:
        super().__init__(sessions)
        self._corpus_tokens = [_tokenize(document) for document in self.documents]
        #: WHICH BM25 ran. ``_bm25_scores`` prefers ``rank_bm25.BM25Okapi``
        #: whenever it imports and otherwise runs the local Okapi, and the two
        #: are different formulas — rank_bm25's IDF has an epsilon floor the
        #: local one does not — so a machine with the package installed already
        #: ranks differently from one without. A number is not reproducible
        #: without this, so it goes in the report rather than in a comment.
        self.bm25_impl = (
            "rank_bm25.BM25Okapi" if _rank_bm25_available() else "hybrid local Okapi"
        )

    @property
    def meta(self) -> Dict[str, Any]:
        return {**super().meta, "bm25_impl": self.bm25_impl}

    def _scores(self, question: str) -> Sequence[float]:
        return _bm25_scores(_tokenize(question), self._corpus_tokens)


# --------------------------------------------------------------------------
# Dense: local static embeddings, cosine
# --------------------------------------------------------------------------


def _unit(vector: Sequence[float]) -> List[float]:
    """L2-normalise, leaving a zero vector alone.

    ``or 1.0`` on the norm is ``hybrid``'s own convention: a zero vector scores
    0 against everything rather than dividing by zero and poisoning the lane
    with NaN, which sorts unpredictably and would look like a ranking.
    """
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class DenseArm(_Arm):
    """Exhaustive cosine against the corpus, embedded ONCE.

    ``hybrid._embedding_scores`` is deliberately not used: it embeds the query
    *and the whole corpus* on every call, so a 60-question group would embed
    ~110 sessions sixty times over. The corpus is embedded on first use here
    and normalised in place; a question costs one embedding and a dot product
    per document.

    ``backend`` is an injection point on the same terms as ``MabMemory``'s:
    every test passes a stub, because a lane whose wiring can only be checked
    by loading a model does not get checked. Injected or resolved, the backend
    has to identify as :data:`LOCAL_EMBEDDING_PREFER`.
    """

    name = "Dense"
    retriever = f"cosine, exhaustive over the corpus ({LOCAL_EMBEDDING_PREFER})"

    def __init__(self, sessions: Sequence[Session], *, backend: Any = None) -> None:
        super().__init__(sessions)
        self._backend = backend
        self._corpus_vectors: Optional[List[List[float]]] = None

    @property
    def embedder(self) -> str:
        """The live backend's own name, never a declared one."""
        return str(getattr(self.backend(), "name", "") or "")

    @property
    def meta(self) -> Dict[str, Any]:
        return {**super().meta, "embedding_dim": getattr(self.backend(), "dim", None)}

    def backend(self) -> Any:
        """The embedding backend, resolved on first use and then checked.

        Checked on every call and not only at construction: the object can be
        injected, and "the arm asked for model2vec" is not the same claim as
        "the arm embedded with model2vec".
        """
        if self._backend is None:
            try:
                # EXPLICIT. Never "auto" — see the module docstring. And with
                # the hub off: this is the only line in either arm that can
                # reach a network, and the claim above the §6 table says it
                # does not.
                with _offline_hub():
                    self._backend = active_embedding_backend(LOCAL_EMBEDDING_PREFER)
            except Exception as exc:  # optional dep missing / cold model cache
                raise RefusedToEmbedLocally(
                    f"the dense arm's embedder ({LOCAL_EMBEDDING_PREFER}) could "
                    f"not be constructed with the hub offline: {exc}",
                    _embedder_remedy(),
                ) from exc
        name = str(getattr(self._backend, "name", "") or "")
        if not name.startswith(f"{LOCAL_EMBEDDING_PREFER}:"):
            raise RefusedToEmbedLocally(
                f"the dense arm resolved the embedding backend {name or '(unnamed)'}, "
                f"which is not {LOCAL_EMBEDDING_PREFER} — every arm in this "
                f"comparison must share one embedder or the gaps between them "
                f"are not about the architectures",
                "pass prefer=\"model2vec\" (this module does) and check that "
                "model2vec imports; a run that reached the hash stub prints "
                "plausible numbers and measures nothing",
            )
        return self._backend

    def _vectors(self) -> List[List[float]]:
        if self._corpus_vectors is None:
            self._corpus_vectors = [
                _unit(vector) for vector in embed_texts(self.backend(), self.documents)
            ]
        return self._corpus_vectors

    def _scores(self, question: str) -> Sequence[float]:
        vectors = self._vectors()
        query = _unit(embed_texts(self.backend(), [question])[0])
        return [sum(q * d for q, d in zip(query, document)) for document in vectors]


__all__ = [
    "LOCAL_EMBEDDING_PREFER",
    "DenseArm",
    "LexicalArm",
    "RefusedToEmbedLocally",
]

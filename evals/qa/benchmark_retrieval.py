"""A retrieval baseline that has to ANSWER, not just rank.

Every comparison this repository has published against BM25 scored *ranking* —
did the top-k contain the gold documents. BM25 is built for that, so it wins it,
and winning it says nothing about a memory system: BM25 cannot answer a question
at all. It returns files.

This arm closes that gap. It retrieves with exactly the lanes
``evals/selfimprove/curve.py`` measures, pastes the top-k documents into a
prompt, and asks the same model, under the same instruction, for the same answer
shape as :mod:`evals.qa.null_model`. What then differs between this arm and
Tesserae's is *what context the memory chose to supply*, which is the actual
product claim — and what differs between this arm and the null model is whether
having any documents at all helps.

Three arms, three questions, and only together do they mean anything:

* **null** — the model alone. Bounds how much any memory can be contributing.
* **retrieval** — the model plus whole documents a keyword or fusion ranker
  picked. The thing a memory has to beat to justify existing.
* **tesserae** — the model plus a compiled, cited context bundle.

The prompt is :data:`evals.qa.null_model.NULL_SYSTEM_PROMPT` with a sources
block appended, and ``answer_shape`` is declared ``short-span`` to match. That
matching is not a formality: ``evals/qa/scorer.py::FAIRNESS_KEYS`` blocks
publication when two systems were asked for different shapes, because exact
match and token F1 are computed over the whole answer string.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .null_model import NULL_SYSTEM_PROMPT
from .vendor_base import MissingPrerequisite, load_qa_benchmark_base

QABenchmarkRAG, QABenchmarkConfig = load_qa_benchmark_base()

#: Retrieval lanes this arm can run. ``bm25`` is the single-lane keyword
#: baseline; ``hybrid`` is the same three-lane RRF fusion the graph arm's own
#: seeding stage uses, which is the fusion-matched control — comparing a
#: three-lane memory against a one-lane baseline measures lane count.
LANES = ("bm25", "hybrid")

#: Sources pasted into the prompt. Matches ``curve.K`` so the evidence budget is
#: the one every other measurement in this repository used.
DEFAULT_TOP_K = 10

#: Per-document character cap. Whole papers do not fit in a prompt, and letting
#: one long document crowd out nine others would measure document length rather
#: than ranking. Applied identically to every lane.
DEFAULT_DOC_CHARS = 4_000

_SOURCES_INSTRUCTION = (
    "\n\nYou are given source documents below. Answer from them. "
    "If they do not contain the answer, reply with exactly: I don't know"
)


@dataclass
class RetrievalConfig(QABenchmarkConfig):  # type: ignore[misc,valid-type]
    """Which lane retrieves, which model answers, and under what instruction."""

    lane: str = "bm25"
    top_k: int = DEFAULT_TOP_K
    doc_chars: int = DEFAULT_DOC_CHARS
    model: Optional[str] = None
    provider: Optional[str] = None
    system_prompt: str = NULL_SYSTEM_PROMPT + _SOURCES_INSTRUCTION
    #: Declared, never derived. Must equal the null model's for the comparison
    #: to be publishable at all.
    answer_shape: str = "short-span"
    results_file: str = "retrieval_qa_results.json"


class QABenchmarkRetrieval(QABenchmarkRAG):  # type: ignore[misc,valid-type]
    """Top-k documents from one retrieval lane, pasted into the same prompt."""

    def __init__(self, corpus, qa_pairs, config: RetrievalConfig):
        super().__init__(corpus, qa_pairs, config)
        self.config: RetrievalConfig = config
        if config.lane not in LANES:
            raise MissingPrerequisite(
                f"unknown retrieval lane {config.lane!r}",
                f"choose one of: {', '.join(LANES)}",
            )
        #: The corpus this arm actually indexes. Unlike the null model, which
        #: discards every document, this one keeps them — that difference is
        #: the whole point of running both.
        #:
        #: Seeded from the constructor rather than waiting for
        #: :meth:`insert_document`. The harness only calls that during a staging
        #: phase, and ``--answer`` alone skips it: the first real run of this arm
        #: retrieved from an empty index for all 332 questions and reported token
        #: F1 of 0.009-0.057, which is the null model wearing a retrieval label.
        #: `documents_indexed: 0` was printed in the fairness declarations and is
        #: the only reason it was caught.
        self.documents: List[str] = [d for d in (corpus or []) if isinstance(d, str)]
        self.prompts_sent: List[Dict[str, str]] = []
        self.client_factory = None
        self._index: Optional[Dict[str, Any]] = None

    @property
    def system_name(self) -> str:
        return f"Retrieval-{self.config.lane}"

    async def initialize_rag(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory()
        from tesserae.llm_json import build_rotating_client

        client = build_rotating_client(
            model_codex=self.config.model,
            model_claude=self.config.model,
            provider=self.config.provider,
        )
        if client is None:
            raise MissingPrerequisite(
                "no LLM client available for the retrieval arm "
                "(no Claude/Codex CLI account and no API key)",
                "authenticate one: `codex login` or `claude login`, "
                "or export ANTHROPIC_API_KEY",
            )
        return client

    async def cleanup_rag(self) -> None:
        return None

    async def insert_document(self, document: str, document_id: int) -> None:
        """Accept a document the harness feeds us, if it feeds us any.

        Idempotent against constructor seeding: a document already held is not
        stored twice, so a staged run and an ``--answer``-only run index the
        same corpus and produce comparable numbers. The index is invalidated
        rather than extended, so every lane is fitted once over the whole corpus
        — a lane fitted incrementally would score early questions against a
        smaller corpus than late ones.
        """
        del document_id
        if document not in self.documents:
            self.documents.append(document)
            self._index = None

    def _build_index(self) -> Dict[str, Any]:
        from tesserae.retrieval.hybrid import _tokenize

        idx: Dict[str, Any] = {"tokens": [_tokenize(d) for d in self.documents]}
        if self.config.lane == "hybrid":
            from tesserae.retrieval.hybrid import active_embedding_backend
            from tesserae.retrieval.vector_cache import embed_texts

            backend = active_embedding_backend("model2vec")
            if not backend.name.startswith("model2vec:"):
                raise MissingPrerequisite(
                    f"the hybrid lane resolved {backend.name}, not model2vec",
                    "install the semantic extra: uv sync --all-extras",
                )
            idx["backend"] = backend
            idx["vectors"] = embed_texts(backend, self.documents)
        return idx

    def _rank(self, question: str) -> List[int]:
        from tesserae.retrieval.hybrid import (
            DEFAULT_WEIGHTS, _bm25_scores, _fuse, _lexical_scores, _tokenize,
        )

        if self._index is None:
            self._index = self._build_index()
        idx = self._index
        bm25 = _bm25_scores(_tokenize(question), idx["tokens"])
        if self.config.lane == "bm25":
            scores = bm25
        else:
            from evals.selfimprove.curve import _cosine_scores

            scores, _ = _fuse(
                {
                    "bm25": bm25,
                    "lexical": _lexical_scores(question, self.documents),
                    "embedding": _cosine_scores(idx["backend"], idx["vectors"], question),
                },
                DEFAULT_WEIGHTS,
                len(self.documents),
            )
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
        return [i for i in order[: self.config.top_k] if scores[i] > 0.0]

    async def query_rag(self, question: str) -> str:
        if not self.documents:
            # A retrieval arm with no documents is a null model with a
            # misleading name. Refusing here costs one question; answering
            # produces a full table of numbers that look like retrieval and are
            # not, which is the failure this whole benchmark exists to avoid.
            raise RuntimeError(
                "retrieval arm has no documents to retrieve from — the corpus "
                "never reached it (check --corpus, and that the harness either "
                "stages or passes it to the constructor)"
            )
        picked = self._rank(question)
        blocks = [
            f"<source id=\"{i}\">\n{self.documents[i][: self.config.doc_chars]}\n</source>"
            for i in picked
        ]
        user = "\n\n".join(blocks + [f"Question: {question}"])
        prompt = {"system": self.config.system_prompt, "user": user}
        self.prompts_sent.append(prompt)
        answer = await asyncio.to_thread(
            self.rag_client.complete_text, system=prompt["system"], user=prompt["user"]
        )
        if answer is None:
            # Same reasoning as the null model: an empty string reads as a
            # refusal downstream, which would credit an exhausted account as
            # caution. Surface it as an error instead.
            raise RuntimeError("LLM returned no answer (every account exhausted?)")
        return answer.strip()

    def declared_meta(self) -> Dict[str, Any]:
        return {
            "answer_shape": self.config.answer_shape,
            "llm_model": self.config.model,
            "llm_provider": self.config.provider,
            "retrieval_lane": self.config.lane,
            "top_k": self.config.top_k,
            "doc_chars": self.config.doc_chars,
            "documents_indexed": len(self.documents),
        }


__all__ = ["QABenchmarkRetrieval", "RetrievalConfig", "LANES", "DEFAULT_TOP_K"]

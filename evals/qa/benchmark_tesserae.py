"""Drives Tesserae through the same QA harness that already drives four competitors.

Subclasses the vendored ``QABenchmarkRAG`` ABC (see :mod:`evals.qa.vendor_base`)
so a Tesserae number and a cognee / graphiti / mem0 / lightrag number come out of
one code path. Kept thin on purpose: the interesting code is the scorer, and a
clever adapter is a place for the benchmark to flatter the system it adapts.

**Ingestion is a two-phase, operator-driven thing here, and that is not an
accident.** For the competitors, ``insert_document`` *is* ingestion — one call,
one document, in-process. Tesserae's ingestion is a compile: an LLM extraction
pass over the whole corpus that takes hours, costs quota, and rewrites
``.tesserae/graph.json``. So:

* :meth:`QABenchmarkTesserae.insert_document` **stages** the document to a
  corpus directory and stops. It never compiles, never calls an LLM, never
  touches an existing graph.
* :meth:`QABenchmarkTesserae.initialize_rag` **refuses to run** against a project
  with no compiled search index, and names the command that fixes it. It does
  not fix it itself.

The compile between the two phases is a decision a human makes, and the harness
is not allowed to make it — which is also what keeps this file safe to import in
CI, where it would otherwise be one ``run()`` away from a multi-hour LLM bill.

The module raises :class:`~evals.qa.vendor_base.MissingPrerequisite` **at import
time** if the vendored clone is absent. That is deliberate: the class cannot be
declared without its base class, and a stub base would produce a "Tesserae
benchmark" that silently is not the shared harness. The runner catches it and
prints SKIP.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .vendor_base import MissingPrerequisite, load_qa_benchmark_base

QABenchmarkRAG, QABenchmarkConfig = load_qa_benchmark_base()


@dataclass
class TesseraeConfig(QABenchmarkConfig):  # type: ignore[misc,valid-type]
    """Where the project is and how ``ask`` should answer.

    ``staging_dir`` defaults to ``<project_root>/corpus`` — the layout
    ``evals/growth/run.py`` already builds and ``tesserae init --source
    ./corpus`` already expects. NEVER point ``project_root`` at the repo root:
    a compile there overwrites the project's real graph with the benchmark's,
    the same footgun ``evals/growth/run.py`` warns about.
    """
    #: The ANSWER SHAPE this arm asks for. Tesserae gained a short-span mode in
    #: #204 specifically so it could be compared with anything at all — every
    #: standard QA metric scores exact match and token F1 over the whole answer
    #: string, and 60-220 words of cited prose scores near zero against a phrase
    #: however correct it is. The arm then still called `ask_project` without it
    #: and answered in prose for a whole 332-question run, which
    #: `scorer.FAIRNESS_KEYS` would have blocked from publication.
    #:
    #: Defaults to the PRODUCT's shape, not the benchmark's. `tesserae ask`
    #: answers in cited prose, so that is what this arm answers in unless a
    #: caller asks otherwise — `run_qa_eval` does, because its job is numbers
    #: that can be compared. Defaulting to short-span here would make the arm
    #: describe a system nobody runs.
    answer_style: str = "prose-cited"


    #: OPT-IN abstention gate. ``None`` — the default — is today's behaviour
    #: exactly: no gate, nothing computed, no change to any number. Set it to a
    #: quantile in [0, 1] and an answer whose Novel Grounded Evidence falls
    #: below ``grounding_tau(corpus_idf, quantile)`` is replaced with ``""``,
    #: which :func:`evals.qa.scorer.is_refusal` already reads as a refusal.
    #:
    #: A QUANTILE, never an absolute threshold. BM25 idf scales with
    #: ``log(n_docs)``, so a constant tuned on a 135-document corpus means
    #: something else entirely on a 62k-page graph — see
    #: :func:`tesserae.retrieval.grounding.grounding_tau`.
    #:
    #: Measured at quantile 0.25 on 352 persisted answers, Tesserae arm:
    #: Youden J +0.5048 -> +0.6172, refuse|unanswerable 0.529 -> 0.691,
    #: refuse|answerable 0.025 -> 0.074. Honest leave-one-out J +0.5884
    #: (dJ +0.0837, paired bootstrap 95%% CI [-0.0004, +0.1719]). It costs
    #: token F1 0.3534 -> 0.3380 on the answerable stratum. Opt in per run;
    #: it is not the product's behaviour and must not become it.
    grounding_quantile: Optional[float] = None

    project_root: str = ""
    staging_dir: Optional[str] = None
    backend: str = "wiki"
    route: str = "auto"
    top_k: int = 5
    #: ``ask`` synthesizes an answer with an LLM by default. Set ``no_llm=True``
    #: for the retrieval-only reading — worth having, but note it changes what
    #: is being measured: a BM25 excerpt is not an answer, and it will score
    #: near zero on exact match by construction.
    no_llm: bool = False
    results_file: str = "hotpot_qa_tesserae_results.json"


class QABenchmarkTesserae(QABenchmarkRAG):  # type: ignore[misc,valid-type]
    """Tesserae implementation of the shared QA benchmark contract."""

    def __init__(self, corpus, qa_pairs, config: TesseraeConfig):
        super().__init__(corpus, qa_pairs, config)
        self.config: TesseraeConfig = config
        if not config.project_root:
            raise ValueError("TesseraeConfig.project_root is required")
        self.project_root = Path(config.project_root).resolve()
        self.staging_dir = (
            Path(config.staging_dir).resolve()
            if config.staging_dir
            else self.project_root / "corpus"
        )
        self.staged: List[Path] = []

    @property
    def system_name(self) -> str:
        return "Tesserae"

    # ------------------------------------------------------------------ phases

    async def initialize_rag(self) -> Any:
        """Open the compiled project. Raises if there is nothing compiled.

        Fail-loud rather than fall back to an empty index: a benchmark run
        against a project that was never compiled would score zero and read as
        "Tesserae cannot answer these questions".
        """
        search_index = self.project_root / ".tesserae" / "site" / "search-index.json"
        if not search_index.is_file():
            raise MissingPrerequisite(
                f"no compiled Tesserae project at {self.project_root} "
                f"(missing {search_index})",
                f"stage the corpus first (--stage-only), then compile it yourself: "
                f"cd {self.project_root} && tesserae init --yes --source ./corpus && "
                f"tesserae compile",
            )
        from tesserae.project import ProjectWiki

        # ProjectWiki, not WikiQuery: ask_project reads ``wiki.paths.graph`` to
        # decide whether the KG planner is available, and a bare WikiQuery has
        # no ``paths`` — so passing one silently pins every question to the
        # BM25 route and measures half the system.
        return ProjectWiki.load(self.project_root)

    async def cleanup_rag(self) -> None:
        """Nothing to release. Staged files are left on disk on purpose — they
        are the input to the operator's compile, and deleting them would make
        the run unreproducible."""
        return None

    async def insert_document(self, document: str, document_id: int) -> None:
        """Stage one document. Does NOT ingest, compile, or call an LLM.

        The filename is derived from ``document_id`` alone (zero-padded, no
        clock, no hash of mutable state) so re-staging the same corpus produces
        a byte-identical directory.
        """
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        destination = self.staging_dir / f"doc-{document_id:05d}.md"
        destination.write_text(document, encoding="utf-8")
        self.staged.append(destination)

    async def query_rag(self, question: str) -> str:
        """One question through ``tesserae.query.ask_project``."""
        from tesserae.query import ask_project

        envelope = await asyncio.to_thread(
            ask_project,
            self.rag_client,
            question,
            backend=self.config.backend,
            top_k=self.config.top_k,
            use_llm=not self.config.no_llm,
            no_llm=self.config.no_llm,
            route=self.config.route,
            answer_style=self.config.answer_style,
        )
        answer = answer_text(envelope)
        if self.config.grounding_quantile is None:
            return answer
        return "" if self._below_grounding_gate(question, answer, envelope) else answer

    # --------------------------------------------------------- abstention gate

    def _corpus_idf(self):
        """BM25 idf over the arm's whole corpus. Built once per run."""
        cached = getattr(self, "_idf", None)
        if cached is None:
            from tesserae.retrieval.grounding import corpus_idf

            cached = corpus_idf([d for d in (self.corpus or []) if isinstance(d, str)])
            self._idf = cached
        return cached

    def _below_grounding_gate(self, question: str, answer: str, envelope: Any) -> bool:
        """True when the answer adds too little novel, source-attested vocabulary.

        The score comes from the envelope, which measured it against the
        evidence the model actually read. There is deliberately NO fallback.

        There was one, and it recomputed the score from ``hit["excerpt"]`` —
        200-character clips of wiki pages — while the planner had pasted
        4,000-character raw source documents into the prompt. Every one of the
        352 benchmark questions routes through the planner, whose envelope did
        not carry the score, so the fallback ran on every question and refused
        71.1% of ANSWERABLE ones: Youden J +0.289, WORSE than the +0.505 of not
        gating at all. The measured +0.617 described an offline proxy no shipped
        path ran. Two independent reviews caught it; neither the unit tests nor
        the implementer's own numbers did, because both exercised the branch
        that never executes.

        So a missing score means DO NOT GATE, never "gate on a substitute".
        Scoring against different evidence is not a weaker version of the same
        measurement, it is a different measurement wearing its name.
        """
        from tesserae.retrieval.grounding import grounding_tau

        score = envelope.get("grounding") if isinstance(envelope, dict) else None
        if score is None:
            return False
        idf, _n_docs = self._corpus_idf()
        return float(score) < grounding_tau(idf, self.config.grounding_quantile)

    # ------------------------------------------------------------------- meta

    def answer_shape(self) -> str:
        """What shape this configuration's answers actually come out in.

        Tesserae has no short-answer mode. ``tesserae.query`` pins one house
        style for every caller — ``_SYSTEM_PREAMBLE_HEADER`` rule 4 asks for
        "60-220 words", rule 2 requires a bracket citation on every factual
        claim — and ``ask_project`` exposes no way to override it. So a run
        with synthesis on declares ``prose-cited``, which will not match the
        baseline's ``short-span`` and will correctly block publication of an
        exact-match comparison. That gap is a real property of the two systems,
        not a bookkeeping error, and declaring anything else here would hide it.

        With ``no_llm`` there is no synthesis at all and the answer is retrieved
        source text, which is a third shape again — worth measuring, never
        comparable with an answer.
        """
        if self.config.no_llm:
            return "excerpt"
        # Report what was actually ASKED FOR, never a constant. A hardcoded
        # declaration passes the fairness gate while the run used something
        # else, which is the precise failure the gate exists to catch.
        return "short-span" if self.config.answer_style == "short-span" else "prose-cited"

    def declared_meta(self) -> Dict[str, Any]:
        """The fairness declarations for this run — see
        :func:`evals.qa.scorer.fairness_blockers`.

        Every value is READ from the project's own config or resolved from the
        live backend, never hardcoded. A hardcoded declaration is worse than no
        declaration: it makes the fairness check pass while the run used
        something else, which is precisely the failure the check exists to
        catch.

        **Call this AFTER :meth:`initialize_rag`.** The model pins live in the
        project config, which is reachable only through ``rag_client``; called
        before the client exists it can only report ``llm_model: None``, and
        the run then declares — permanently, into the answers file — that
        nobody recorded what answered. ``evals/qa/run_qa_eval.py`` resolves the
        meta inside the answer phase for exactly this reason.
        """
        config: Dict[str, Any] = {}
        try:
            config = self.rag_client.config() if self.rag_client is not None else {}
        except Exception:  # a malformed config must not take the report down
            config = {}
        if not isinstance(config, dict):
            config = {}

        shape = self.answer_shape()

        if self.config.backend == "raganything":
            # The raganything store carries its OWN model pins, separately from
            # the ones Tesserae's own extraction uses — which is exactly how the
            # gpt-5.4 / gpt-5.6-luna gap got into the repo. Read them.
            raganything = ((config.get("memory_backends") or {}).get("raganything")) or {}
            llm = raganything.get("llm") or {}
            embedding = raganything.get("embedding") or {}
            return {
                "answer_shape": shape,
                "llm_model": llm.get("model"),
                "embedding_model": embedding.get("model"),
                "embedding_dim": embedding.get("dim"),
            }

        provider = config.get("llm_provider")
        extraction = config.get("extraction") or {}
        model = extraction.get("codex_model") if provider == "codex" else extraction.get("claude_model")
        if not model and provider == "codex":
            from tesserae.llm_json import CODEX_DEFAULT_MODEL

            model = CODEX_DEFAULT_MODEL
        meta: Dict[str, Any] = {
            "answer_shape": shape, "llm_model": model, "llm_provider": provider,
        }
        try:
            from tesserae.retrieval.hybrid import active_embedding_backend

            backend = active_embedding_backend("auto")
            meta["embedding_model"] = getattr(backend, "name", None)
            meta["embedding_dim"] = getattr(backend, "dim", None)
        except Exception:
            # Leave both undeclared. fairness_blockers() treats a missing
            # declaration as a blocker, which is the right outcome.
            pass
        return meta


def answer_text(envelope: Any) -> str:
    """The answer string out of an ``ask_project`` envelope.

    Both branches of ``ask_project`` return a dict, but only the raganything
    branch guarantees an ``answer`` key; the wiki branch merges a
    ``QueryResult.to_dict()`` whose answer may be absent when synthesis is off.
    Missing is normalized to ``""``, which the scorer reads as a refusal — the
    honest reading of "the system returned no answer".
    """
    if isinstance(envelope, dict):
        answer = envelope.get("answer")
        return "" if answer is None else str(answer)
    return "" if envelope is None else str(envelope)


__all__ = ["QABenchmarkTesserae", "TesseraeConfig", "answer_text"]

"""The null model: the same questions, answered by the LLM alone.

This is not a control that gets added later if there is time. It is the number
every other number in this benchmark is read against.

``evals/growth/probe_anchors.py`` records why, and it is worth restating because
it cost three candidate implementations to learn: three different anchor
matchers all scored 15/15 on the growth eval with both controls silent — and so
did *a deliberately crude null model*. A benchmark that cannot separate a system
from its own base model is not measuring the system. HotpotQA in particular is
built from Wikipedia, which every frontier model has memorised; "Tesserae
answered 18 of 24" means nothing until you know the bare model answers 14 of the
same 24 with no corpus at all.

By construction, this class **cannot** see the corpus:

* :meth:`QABenchmarkNullModel.insert_document` takes the document and drops it.
  It is not stored on the instance, not written to disk, not embedded, not put
  in a prompt. The only thing that survives is a counter.
* :meth:`QABenchmarkNullModel.query_rag` builds its prompt from the question
  alone.

That is stronger than "we did not pass the corpus" — there is no code path from
a document to a prompt, and ``tests/test_qa_scorer.py`` asserts it by feeding a
corpus with a unique marker token and checking no prompt ever contains it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .vendor_base import MissingPrerequisite, load_qa_benchmark_base

QABenchmarkRAG, QABenchmarkConfig = load_qa_benchmark_base()

#: The instruction the null model answers under. Two properties matter and both
#: are load-bearing for the metrics:
#:
#: 1. **Short answers.** Token F1 against a gold answer like "scotland" collapses
#:    if the system replies with a paragraph, so every system in the comparison
#:    must be asked for the same answer SHAPE. This prompt is the reference
#:    wording; any system compared against it should be asked the same way, and
#:    a run where they were not is a fairness blocker, not a result.
#: 2. **Refusal is permitted and given an exact form.** Without a licensed way to
#:    decline, the hallucination rate on unanswerable questions measures the
#:    prompt rather than the model.
NULL_SYSTEM_PROMPT = (
    "You are answering a factual question from memory. You have no documents "
    "and no search tool.\n"
    "Answer with the shortest exact answer — a name, a date, a number, or "
    "yes/no. No explanation, no full sentence.\n"
    "If you do not know the answer, reply with exactly: I don't know"
)


@dataclass
class NullModelConfig(QABenchmarkConfig):  # type: ignore[misc,valid-type]
    """Which model plays the null, and under what instruction."""

    model: Optional[str] = None
    provider: Optional[str] = None
    system_prompt: str = NULL_SYSTEM_PROMPT
    results_file: str = "hotpot_qa_null_results.json"


class QABenchmarkNullModel(QABenchmarkRAG):  # type: ignore[misc,valid-type]
    """The base model with no corpus access, driven by the shared harness."""

    def __init__(self, corpus, qa_pairs, config: NullModelConfig):
        super().__init__(corpus, qa_pairs, config)
        self.config: NullModelConfig = config
        #: Documents seen and discarded. Reported so the report can state that
        #: the null model was offered the same corpus and kept none of it.
        self.documents_discarded = 0
        #: Every prompt sent, for the leak test. Questions only, by construction.
        self.prompts_sent: List[Dict[str, str]] = []
        #: Injectable for tests. When None, ``initialize_rag`` builds the real
        #: rotating client.
        self.client_factory = None

    @property
    def system_name(self) -> str:
        return "NullModel"

    async def initialize_rag(self) -> Any:
        if self.client_factory is not None:
            client = self.client_factory()
        else:
            from tesserae.llm_json import build_rotating_client

            client = build_rotating_client(
                model_codex=self.config.model,
                model_claude=self.config.model,
                provider=self.config.provider,
            )
        if client is None:
            raise MissingPrerequisite(
                "no LLM client available for the null model "
                "(no Claude/Codex CLI account and no API key)",
                "authenticate one: `codex login` or `claude login`, "
                "or export ANTHROPIC_API_KEY",
            )
        return client

    async def cleanup_rag(self) -> None:
        return None

    async def insert_document(self, document: str, document_id: int) -> None:
        """Discard the document. This is the whole point of the null model.

        ``document`` is deliberately unused and deliberately not bound to
        anything that outlives this call.
        """
        del document, document_id
        self.documents_discarded += 1

    async def query_rag(self, question: str) -> str:
        """Ask the model the question. Nothing else is in the prompt."""
        prompt = {"system": self.config.system_prompt, "user": question}
        self.prompts_sent.append(prompt)
        answer = await asyncio.to_thread(
            self.rag_client.complete_text, system=prompt["system"], user=prompt["user"]
        )
        # complete_text returns None when every account is exhausted. Empty
        # string reads as a refusal downstream, which would quietly credit a
        # rate limit as caution — so it is surfaced as an error instead.
        if answer is None:
            return "Error: LLM client returned no completion (all accounts exhausted?)"
        return str(answer)

    def declared_meta(self) -> Dict[str, Any]:
        """Fairness declarations.

        ``role="baseline"`` exempts this system from the embedding-parity check
        — having no retrieval is what it is *for*. It stays subject to the
        ``llm_model`` check, and that one is the strict half: a null model run
        on a different model than the system it baselines measures nothing. The
        embedding declaration is the literal string ``"none"`` rather than blank,
        because blank means "not recorded" and those are different facts.
        """
        return {
            "role": "baseline",
            "llm_model": self.config.model or "<client default>",
            "embedding_model": "none",
            "embedding_dim": "none",
            "documents_discarded": self.documents_discarded,
        }


__all__ = ["NULL_SYSTEM_PROMPT", "NullModelConfig", "QABenchmarkNullModel"]

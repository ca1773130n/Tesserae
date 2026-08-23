"""Cross-encoder reranking over a candidate set another retriever produced.

The lanes in :mod:`tesserae.retrieval.hybrid` are *bi-encoders and term
statistics*: they score a query against a document without either seeing the
other. That is what makes them cheap enough to run over a whole graph, and it
is also their ceiling — measured on LoCoMo conv-26 the fused ranking is only
-0.051 MRR behind a strong lexical reference by rank 10 but **-0.107 at rank
1**, the signature of a retriever that finds the right document and orders it
badly.

A cross-encoder reads the query and one document *together* and scores the
pair. It cannot run over a corpus (one forward pass per document), so it runs
over the ~10-50 candidates the lanes already admitted, and only reorders them:
**a reranker can never improve recall, only precision.** If the answer is not
in the candidate set, nothing here helps, and the fix belongs in the lanes.

Opt-in twice over. The dependency (`torch`, `transformers`) lives in the
``rerank`` extra and is NOT part of a normal install, and no code path in this
package calls :func:`rerank_nodes` unless a caller asks for it.

    uv sync --extra rerank

Usage::

    from tesserae.retrieval.hybrid import hybrid_search
    from tesserae.retrieval.rerank import Qwen3Reranker, rerank_nodes

    result = hybrid_search(graph, query, top_k=50)
    reranker = Qwen3Reranker()                      # loads the model lazily
    top = rerank_nodes(query, result.scored, reranker=reranker, top_n=10)
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import List, Optional, Protocol, Sequence

# `_node_text` / `_lexical_texts` are private to `hybrid`, and imported anyway:
# the reranker has to read the SAME string the lanes scored, and a public
# copy of that logic here is a second definition that can drift from the
# first. Same package, one definition, no drift.
from tesserae.retrieval.hybrid import ScoredNode, _lexical_texts, _node_text

logger = logging.getLogger(__name__)

#: Task description handed to the cross-encoder. Qwen3-Reranker is instruction
#: -conditioned: the same pair scores differently under a different instruction,
#: so this string is part of the measurement and is recorded with any result.
DEFAULT_INSTRUCTION = (
    "Given a search query, retrieve relevant passages that answer the query"
)

DEFAULT_MODEL = "Qwen/Qwen3-Reranker-0.6B"

#: Tokens per (query, document) pair. The model accepts 8192; the default is
#: lower because cost is linear in it and the candidates are already short.
DEFAULT_MAX_LENGTH = 2048

DEFAULT_BATCH_SIZE = 8

_EXTRA_HINT = (
    "The cross-encoder reranker needs torch and transformers, which are NOT in "
    "a normal Tesserae install. Install them with `uv sync --extra rerank` (or "
    "`pip install 'tesserae[rerank]'`). Retrieval works without them; only "
    "reranking is unavailable."
)


class Reranker(Protocol):
    """Score a query against each document. Higher is more relevant."""

    name: str

    def score(self, query: str, documents: Sequence[str]) -> List[float]: ...


class Qwen3Reranker:
    """Qwen3-Reranker as a cross-encoder, following the model card exactly.

    The model is a causal LM, not a classification head: relevance is the
    probability it assigns to answering ``yes`` rather than ``no`` at the final
    position. Scores are therefore in ``[0, 1]`` and comparable across queries,
    which the RRF scores they replace are not.

    Loading is deferred to the first :meth:`score` call so that constructing
    one costs nothing and an unused reranker never downloads 1.1 GB of weights.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        instruction: str = DEFAULT_INSTRUCTION,
        max_length: int = DEFAULT_MAX_LENGTH,
        batch_size: int = DEFAULT_BATCH_SIZE,
        device: Optional[str] = None,
    ) -> None:
        self.name = model_name
        self.instruction = instruction
        self.max_length = max_length
        self.batch_size = batch_size
        self._device = device
        self._model = None
        self._tokenizer = None

    # -- loading ---------------------------------------------------------

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ImportError(f"{_EXTRA_HINT} (import failed: {exc})") from exc

        if self._device is None:
            if torch.backends.mps.is_available():
                self._device = "mps"
            elif torch.cuda.is_available():
                self._device = "cuda"
            else:
                self._device = "cpu"

        logger.info("loading %s on %s", self.name, self._device)
        tokenizer = AutoTokenizer.from_pretrained(self.name, padding_side="left")
        model = AutoModelForCausalLM.from_pretrained(
            self.name,
            torch_dtype=(torch.float32 if self._device == "cpu" else torch.float16),
        )
        model = model.to(self._device).eval()

        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model
        self._true_id = tokenizer.convert_tokens_to_ids("yes")
        self._false_id = tokenizer.convert_tokens_to_ids("no")
        # The chat scaffolding the model was trained with. Tokenised once
        # because it is prepended to every pair.
        prefix = (
            "<|im_start|>system\nJudge whether the Document meets the "
            'requirements based on the Query and the Instruct provided. Note '
            'that the answer can only be "yes" or "no".<|im_end|>\n'
            "<|im_start|>user\n"
        )
        suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        self._prefix_ids = tokenizer.encode(prefix, add_special_tokens=False)
        self._suffix_ids = tokenizer.encode(suffix, add_special_tokens=False)

    # -- scoring ---------------------------------------------------------

    def _pair(self, query: str, document: str) -> str:
        return (
            f"<Instruct>: {self.instruction}\n"
            f"<Query>: {query}\n"
            f"<Document>: {document}"
        )

    def score(self, query: str, documents: Sequence[str]) -> List[float]:
        if not documents:
            return []
        self._load()
        torch = self._torch
        out: List[float] = []
        for start in range(0, len(documents), self.batch_size):
            batch = [
                self._pair(query, doc)
                for doc in documents[start : start + self.batch_size]
            ]
            budget = self.max_length - len(self._prefix_ids) - len(self._suffix_ids)
            inputs = self._tokenizer(
                batch,
                padding=False,
                truncation="longest_first",
                return_attention_mask=False,
                max_length=budget,
            )
            inputs["input_ids"] = [
                self._prefix_ids + ids + self._suffix_ids
                for ids in inputs["input_ids"]
            ]
            # No `max_length=` here: `pad(padding=True)` pads to the longest
            # row in the batch and warns that it ignored the argument, once per
            # batch. Truncation to the budget already happened above.
            inputs = self._tokenizer.pad(inputs, padding=True, return_tensors="pt")
            inputs = {k: v.to(self._model.device) for k, v in inputs.items()}
            with torch.no_grad():
                # `logits_to_keep=1` and `use_cache=False` are MEMORY
                # CORRECTNESS, not tuning. A causal LM materialises logits for
                # every position by default: at batch 8 x 2048 tokens against
                # this model's 151,669-token vocabulary that is a single
                # 4.97 GB fp16 tensor, of which one row per row is read. The
                # KV cache is another 1.88 GB for a forward pass that generates
                # nothing. Together they took a 16 GB machine into swap —
                # 15.5 GB of 16 GB swap used and the process paged out to 4 MB
                # resident — while the model itself is 1.2 GB.
                logits = self._model(
                    **inputs, logits_to_keep=1, use_cache=False
                ).logits[:, -1, :]
                pair = torch.stack(
                    [logits[:, self._false_id], logits[:, self._true_id]], dim=1
                )
                probs = torch.nn.functional.log_softmax(pair.float(), dim=1)
                out.extend(probs[:, 1].exp().tolist())
        return out


def rerank_nodes(
    query: str,
    scored: Sequence[ScoredNode],
    *,
    reranker: Reranker,
    top_n: Optional[int] = None,
    source_root: Optional[Path] = None,
) -> List[ScoredNode]:
    """Reorder ``scored`` by cross-encoder relevance and return the top ``top_n``.

    The reranker reads the SAME text the lexical lanes scored — node summary,
    plus the node's own source file when ``source_root`` is given, on exactly
    the terms :func:`~tesserae.retrieval.hybrid.hybrid_search` uses. Handing it
    different text would make its ordering incomparable with the ordering it
    replaces.

    Each returned :class:`~tesserae.retrieval.hybrid.ScoredNode` carries the
    cross-encoder probability as its ``score``, and keeps every lane score it
    arrived with under ``per_lane`` plus a new ``rerank`` entry — so the fused
    rank a node came in at stays readable beside the rank it leaves with.

    An empty candidate set returns empty: **this function never adds a
    document**, so a miss upstream is still a miss.
    """
    if not scored:
        return []
    nodes = [item.node for item in scored]
    texts = _lexical_texts(nodes, [_node_text(node) for node in nodes], source_root)
    scores = reranker.score(query, list(texts))
    if len(scores) != len(scored):
        raise ValueError(
            f"reranker {getattr(reranker, 'name', reranker)!r} returned "
            f"{len(scores)} scores for {len(scored)} documents"
        )
    order = sorted(range(len(scored)), key=lambda i: (-scores[i], i))
    out: List[ScoredNode] = []
    for rank, idx in enumerate(order, start=1):
        item = scored[idx]
        out.append(
            replace(
                item,
                score=scores[idx],
                per_lane={**item.per_lane, "rerank": scores[idx]},
                ranks={**item.ranks, "rerank": rank},
            )
        )
    return out[:top_n] if top_n is not None else out

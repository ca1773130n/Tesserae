"""DeepScholar-Bench: render a Related Works section from a compiled graph.

Two arms, built to be compared against each other rather than against a
leaderboard row:

* :mod:`evals.deepscholar.evidence` — the Tesserae arm. Walks the compiled
  ``ResearchGraph`` for ``Paper --supports_claim--> Claim --evidenced_by-->
  EvidenceSpan`` and hands the writer verbatim, paper-anchored sentences.
* :mod:`evals.deepscholar.control` — the flat control. BM25 over the same
  abstracts, no graph, no claim nodes, no ``evidenced_by`` edge.

Both arms end in :mod:`evals.deepscholar.writer`, which is one function, one
prompt and one backbone call. Everything that is not the selection mechanism is
shared code rather than shared intent — see the module docstring there.
"""

from .dataset import CitedPaper, ParentPaper, Query, load_queries
from .evidence import EvidenceCard, apply_budget, graph_cards, split_sentences
from .control import bm25_cards
from .writer import RenderResult, cited_arxiv_ids, render, strip_links

__all__ = [
    "CitedPaper",
    "EvidenceCard",
    "ParentPaper",
    "Query",
    "RenderResult",
    "apply_budget",
    "bm25_cards",
    "cited_arxiv_ids",
    "graph_cards",
    "load_queries",
    "render",
    "split_sentences",
    "strip_links",
]

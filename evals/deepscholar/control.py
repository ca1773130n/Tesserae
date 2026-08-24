"""The flat control: BM25 over the same abstracts, and nothing else.

This is the floor that decides whether a win belongs to the architecture or to
the backbone. It is written to be as strong as a no-graph pipeline can be, and
the list of what it shares with the Tesserae arm is longer than the list of what
it does not.

HELD CONSTANT — same code, not merely the same intention:

* the corpus (:class:`~evals.deepscholar.dataset.Query` is passed to both arms
  unchanged, so both see every paper this parent cited that has an abstract);
* the evidence budget (:func:`~evals.deepscholar.evidence.apply_budget`, same
  paper count, same lines per paper, same character cap) — and not merely the
  cap: the Tesserae arm tops its cards up from the abstract when its claims
  fall short, so the two tables carry the same NUMBER of lines about the same
  papers, and any gap between the arms is selection rather than volume;
* the sentence units (``tesserae.research_graph.split_sentences``, the very
  splitter the deterministic extractor used to cut these abstracts into claim
  candidates);
* the table rendering (:func:`~evals.deepscholar.evidence.render_table`);
* the prompt, word cap, citation format and validation
  (:mod:`evals.deepscholar.writer`);
* the backbone, model and number of calls — exactly one generation per query
  for each arm.

DIFFERENT, and this is the whole measurement:

* **Selection.** Lines are the sentences of the abstract that score highest
  under Okapi BM25 against the parent paper's abstract as the query. No graph
  is loaded, no ``Claim`` node is read, no ``evidenced_by`` edge is traversed.
* **Paper ranking**, which only bites when a paper budget is set: BM25 of the
  whole abstract against the same query, rather than claim density.

Where the control is FAVOURED, said plainly because a reviewer will look: its
lines are query-ranked while the Tesserae arm's top-up lines are in reading
order, and on the two queries measured during development that left the control
with slightly MORE evidence text under the identical budget (1,366 vs 1,297 and
2,047 vs 1,772 characters). A floor that is a little too high is the right
direction for a floor to err.

Two choices worth defending, because both could be made to handicap the control
and neither is:

* The query is the parent's ABSTRACT, not its title. It is the longest and most
  specific description of the target paper available to any arm at generation
  time, it is exactly what the published task hands the system, and a
  title-only query would be a materially weaker retriever.
* BM25 is real Okapi BM25 with IDF, taken from
  ``tesserae.retrieval.hybrid._bm25_scores`` — the same ranker this project's
  own hybrid retriever runs. The BM25-lite variant in ``tesserae.site.search``
  drops IDF for browser-side stability and would have been the weaker choice.

Ties break on corpus order, so the control is deterministic and re-runnable to
the byte.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from .dataset import Query
from .evidence import (
    DEFAULT_BUDGET,
    EvidenceBudget,
    EvidenceCard,
    apply_budget,
    split_sentences,
)

__all__ = ["ORIGIN_BM25", "bm25_cards", "rank_papers"]

#: :attr:`EvidenceCard.origin` for every card this arm produces. Distinct from
#: the Tesserae arm's values so a saved run can be told apart after the fact.
ORIGIN_BM25 = "bm25"


def _bm25():
    """``(_bm25_scores, _tokenize)``, imported on use.

    Deferred the way ``tesserae.temporal`` defers the same pair: the module
    pulls in the retrieval package, and a ``--stage-only`` run has no business
    paying for it. No numeric dependency — the import brings in neither numpy
    nor torch, which is what lets the control run on a plain install.
    """
    from tesserae.retrieval.hybrid import _bm25_scores, _tokenize

    return _bm25_scores, _tokenize


def rank_papers(query: Query) -> List[Tuple[int, float]]:
    """``(corpus position, score)`` for every paper, best first.

    Whole-abstract BM25 against the parent abstract. Ties keep corpus order,
    which is the dataset's own citation order — an arbitrary but fixed
    tie-break, so two runs of the control agree exactly.
    """
    scores_fn, tokenize = _bm25()
    corpus = [tokenize(f"{p.title}\n{p.abstract}") for p in query.corpus]
    scores = scores_fn(tokenize(query.parent.abstract), corpus)
    order = sorted(range(len(query.corpus)), key=lambda i: (-scores[i], i))
    return [(i, float(scores[i])) for i in order]


def _rank_sentences(sentences: Sequence[str], query_tokens: Sequence[str]) -> List[str]:
    """Abstract sentences, most query-relevant first, ties keeping reading order."""
    scores_fn, tokenize = _bm25()
    if not sentences:
        return []
    scores = scores_fn(list(query_tokens), [tokenize(s) for s in sentences])
    order = sorted(range(len(sentences)), key=lambda i: (-scores[i], i))
    return [sentences[i] for i in order]


def bm25_cards(
    query: Query, *, budget: EvidenceBudget = DEFAULT_BUDGET
) -> List[EvidenceCard]:
    """The control's evidence table for one query.

    Takes no graph and no work directory, by construction rather than by
    convention: there is nowhere in this signature to pass one.
    """
    _, tokenize = _bm25()
    query_tokens = tokenize(query.parent.abstract)

    cards: List[EvidenceCard] = []
    for position, score in rank_papers(query):
        paper = query.corpus[position]
        sentences = [s for s in split_sentences(paper.abstract) if s.strip()]
        ranked = _rank_sentences(sentences, query_tokens)
        if not ranked:
            continue
        cards.append(
            EvidenceCard(
                paper=paper,
                lines=tuple(ranked),
                origin=ORIGIN_BM25,
                score=score,
            )
        )
    return apply_budget(cards, budget)

"""The evidence table both arms fill in, and the Tesserae arm that fills it.

An :class:`EvidenceCard` is one cited paper plus the sentences an arm chose to
show the writer about it. Both arms emit a list of these, both go through
:func:`apply_budget`, and both are then handed to the same prompt. That is
deliberate and structural: the two arms cannot differ in evidence budget,
citation format, sentence segmentation or prompt wording, because there is only
one implementation of each and both import it.

What the Tesserae arm does differently is the only thing it is allowed to do
differently — WHICH sentences. It walks the compiled graph:

    Paper --supports_claim--> Claim --evidenced_by--> EvidenceSpan --part_of--> Paper

and takes ``Claim.description``, which the deterministic extractor sets to a
verbatim sentence of the abstract. So every line on a card is a span that
resolves back to the paper it is printed under. The control has no such edge and
picks sentences lexically; see :mod:`evals.deepscholar.control`.

The fallback is not an afterthought. Measured on a real 29-paper DeepScholar
corpus, 10 of 29 abstracts (34.5%) produce no claim and no span at all — the
deterministic claim extractors are cue-phrase driven ("We present", "we
propose", "outperforms") and an abstract phrased otherwise yields nothing. A
claim-only renderer therefore cannot cite a third of the corpus, which caps
``reference_coverage`` and ``document_importance`` around 0.655 before the
writer runs. That is a handicap on the TREATMENT arm, and leaving it in would
make the comparison flatter, not more honest. So a paper the graph is thin
about falls back to its own abstract, read from ``Paper.source_path``.

For the same reason a card whose claims do not fill its line allowance is
topped up from the abstract in reading order, so both arms show the writer the
same number of lines about the same papers and the comparison is of SELECTION
rather than of volume. What each card actually contained is not lost:
:attr:`EvidenceCard.origin` names the primary source and
:attr:`EvidenceCard.claim_lines` counts how many lines came from a typed
``Claim``, both of which the runner reports per query.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# The extractor's own splitter, imported rather than reimplemented. It is the
# function that cut these abstracts into claim candidates in the first place, so
# reusing it makes the control's sentence units the SAME units as the Tesserae
# arm's — the arms cannot come to differ because one of them counts a sentence
# boundary the other does not.
from tesserae.research_graph import split_sentences

from .dataset import CitedPaper, Query
from .stage import abstract_of

__all__ = [
    "CLAIM_TYPES",
    "DEFAULT_BUDGET",
    "EvidenceBudget",
    "EvidenceCard",
    "apply_budget",
    "graph_cards",
    "render_table",
    "split_sentences",
]

#: Every node type in the claim family. Membership is checked by ``.value`` so
#: a graph rehydrated from JSON and one built in-process behave identically.
CLAIM_TYPES = frozenset(
    {
        "Claim",
        "ContributionClaim",
        "PerformanceClaim",
        "ComparisonClaim",
        "LimitationClaim",
        "CausalClaim",
    }
)

ORIGIN_CLAIM = "claim"
ORIGIN_SPAN = "span"
ORIGIN_ABSTRACT = "abstract"


@dataclass(frozen=True)
class EvidenceBudget:
    """What an arm is allowed to show the writer. Identical for both arms.

    ``papers=None`` means the whole of this query's corpus, which is the
    default and the condition under which the comparison is cleanest: both arms
    see every paper, the same number of lines each, and the ONLY difference
    between the tables is which sentences were chosen. Set ``papers`` to a
    number and the arms additionally differ in which papers survive — a
    different and larger claim, so it has to be asked for explicitly.

    ``chars`` is a backstop, not a selector. At the default it does not bind:
    the largest per-query corpus in this dataset is 40 papers, which at three
    lines of roughly 150 characters is ~18k. If a run reports it binding, the
    tables were not budget-matched and the comparison is void — the runner
    prints the figure for that reason.
    """

    papers: Optional[int] = None
    lines_per_paper: int = 3
    chars: int = 60_000

    def __post_init__(self) -> None:
        if self.papers is not None and self.papers < 1:
            raise ValueError("EvidenceBudget.papers must be >= 1 or None")
        if self.lines_per_paper < 1:
            raise ValueError("EvidenceBudget.lines_per_paper must be >= 1")
        if self.chars < 1:
            raise ValueError("EvidenceBudget.chars must be >= 1")


DEFAULT_BUDGET = EvidenceBudget()


@dataclass(frozen=True)
class EvidenceCard:
    """One cited paper as the writer will see it."""

    paper: CitedPaper
    lines: Tuple[str, ...]
    #: ``"claim"``, ``"span"``, ``"abstract"`` or ``"bm25"`` — the PRIMARY
    #: source of this card's lines. Reported, not decorative: it is how a reader
    #: learns what share of the Tesserae arm's table was actually anchored to a
    #: typed claim rather than to the fallback.
    origin: str
    #: The arm's own ranking score. Only used for ordering within one arm;
    #: never compared across arms, because the two scales mean different things.
    score: float = 0.0
    #: How many of :attr:`lines` came from a typed ``Claim`` node. The rest are
    #: the top-up described in :func:`graph_cards`. Zero for the control by
    #: construction — it never reads a claim node.
    claim_lines: int = 0

    @property
    def chars(self) -> int:
        return sum(len(line) for line in self.lines)


def apply_budget(
    cards: Sequence[EvidenceCard], budget: EvidenceBudget
) -> List[EvidenceCard]:
    """Trim ``cards`` to ``budget``. Both arms call this, and only this.

    Order is: drop cards with nothing to say, cut each card's lines, cut the
    number of cards, then stop at the character cap. Cards arrive already
    ranked by their arm; this function never reorders, so an arm's ranking is
    entirely its own and the budget is entirely shared.
    """
    trimmed: List[EvidenceCard] = []
    for card in cards:
        lines = tuple(line for line in card.lines if line.strip())
        if not lines:
            continue
        kept = lines[: budget.lines_per_paper]
        # Claim lines are emitted first, so trimming the tail can only remove
        # top-up lines until it reaches them. Recomputing rather than carrying
        # the field through keeps `claim_lines` a true count of what the writer
        # was actually shown.
        trimmed.append(
            replace(card, lines=kept, claim_lines=min(card.claim_lines, len(kept)))
        )
    if budget.papers is not None:
        trimmed = trimmed[: budget.papers]
    out: List[EvidenceCard] = []
    used = 0
    for card in trimmed:
        if used + card.chars > budget.chars:
            break
        out.append(card)
        used += card.chars
    return out


def render_table(cards: Sequence[EvidenceCard]) -> str:
    """The EVIDENCE block of the prompt. One renderer, so both arms share it.

    ``arxiv_id`` is printed on its own line, versioned, because it is the
    string the writer must copy into every citation URL and the string
    ``paper.csv`` is keyed on. Printing the bare id here is the single most
    expensive typo available in this harness: the link resolves to ``{}``, the
    entailment judge is handed an empty abstract, and the sentence scores 0
    without any error being raised.
    """
    blocks: List[str] = []
    for index, card in enumerate(cards, start=1):
        lines = [
            f"[{index}] arxiv_id: {card.paper.arxiv_versioned}",
            f"    title: {card.paper.title}",
            # The finished citation, ready to copy. Assembling one from the id
            # is where a model drops the version suffix, and a version-less id
            # resolves to an empty abstract rather than to an error.
            f"    cite as: [{card.paper.title}]({card.paper.url})",
        ]
        lines.extend(f"    - {line}" for line in card.lines)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ------------------------------------------------------------------ the arm


def _index_by_type(graph) -> Dict[str, List]:
    buckets: Dict[str, List] = {}
    for node in graph.nodes:
        buckets.setdefault(node.type.value, []).append(node)
    return buckets


def _papers_by_arxiv(graph) -> Dict[str, object]:
    """Paper nodes keyed on BARE arXiv id.

    Bare, not versioned, because that is the form
    ``extract_source_metadata`` normalises to — it matches ``(\\d{4}\\.\\d{4,6})``
    and drops the suffix. The versioned id never comes from the graph; it comes
    from the dataset row, through :class:`~evals.deepscholar.dataset.CitedPaper`.
    """
    found: Dict[str, object] = {}
    for node in graph.nodes:
        if node.type.value != "Paper":
            continue
        arxiv_id = str((node.metadata or {}).get("arxiv_id") or "").strip()
        if arxiv_id and arxiv_id not in found:
            found[arxiv_id] = node
    return found


def _adjacency(graph) -> Tuple[Dict[str, Dict[str, List[str]]], Dict[str, Dict[str, List[str]]]]:
    out: Dict[str, Dict[str, List[str]]] = {}
    incoming: Dict[str, Dict[str, List[str]]] = {}
    for edge in graph.edges:
        out.setdefault(edge.source, {}).setdefault(edge.type, []).append(edge.target)
        incoming.setdefault(edge.target, {}).setdefault(edge.type, []).append(edge.source)
    return out, incoming


def _dedup(lines: Sequence[str]) -> List[str]:
    """First occurrence wins, comparing on collapsed whitespace.

    A Claim and the EvidenceSpan it is evidenced by carry the SAME sentence in
    this extractor, so a card built from both routes would print every claim
    twice and spend a third of its budget saying nothing new.
    """
    seen = set()
    out: List[str] = []
    for line in lines:
        text = " ".join((line or "").split())
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(text)
    return out


def _abstract_lines(node, *, root: Optional[Path], cache: Dict[str, str]) -> List[str]:
    """The paper's own abstract, for a paper the extractor found no claim in.

    Reads through :func:`tesserae.context_compiler._source_text`, which is
    CONFINED to ``root`` and returns "" for anything outside it. ``root`` is
    passed always; omitting it makes every body come back empty with no error.
    """
    if root is None:
        return []
    from tesserae.context_compiler import _source_text

    document = _source_text(node, cache, root=str(root))
    if not document:
        return []
    return _dedup(split_sentences(abstract_of(document)))


def graph_cards(
    graph,
    query: Query,
    *,
    root: Path,
    budget: EvidenceBudget = DEFAULT_BUDGET,
    fill_from_abstract: bool = True,
) -> List[EvidenceCard]:
    """The Tesserae arm: one card per cited paper, claim sentences first.

    ``root`` is the staged work directory — the confinement boundary for the
    abstract fallback, not a decoration.

    ``fill_from_abstract`` is what makes the paired comparison a comparison of
    SELECTION rather than of volume, and it is on by default for that reason.
    Left off, a paper whose abstract yielded one claim contributes one line
    while the control contributes three, and a reader is entitled to ask
    whether any gap is the architecture or simply more text. Topped up, both
    arms show the writer the same number of lines about the same papers, and
    the only difference left is which sentences those are.

    The top-up is the paper's remaining abstract sentences in READING order,
    deliberately not in BM25 order: ranking them lexically would import the
    control's own mechanism into the arm being measured against it.

    Ranking, which only matters when ``budget.papers`` is set: claim-anchored
    papers first, more claims before fewer, corpus order breaking ties. It is
    deterministic and a property of the graph rather than of the query — a
    lexical ranking here would again be the control's mechanism wearing the
    treatment's name.
    """
    papers = _papers_by_arxiv(graph)
    out_edges, in_edges = _adjacency(graph)
    by_id = {node.id: node for node in graph.nodes}
    cache: Dict[str, str] = {}

    cards: List[EvidenceCard] = []
    for position, cited in enumerate(query.corpus):
        node = papers.get(cited.arxiv_bare)
        if node is None:
            continue
        claims: List[str] = []
        spans: List[str] = []
        for target in out_edges.get(node.id, {}).get("supports_claim", []):
            claim = by_id.get(target)
            if claim is None or claim.type.value not in CLAIM_TYPES:
                continue
            claims.append(claim.description or claim.name)
        for source in in_edges.get(node.id, {}).get("part_of", []):
            span = by_id.get(source)
            if span is None or span.type.value != "EvidenceSpan":
                continue
            spans.append(span.description or span.name)

        # One dedup over the concatenation, in priority order, so a sentence
        # that is both a Claim and the EvidenceSpan evidencing it — which is
        # every claim, in this extractor — is printed once and counted once.
        claim_lines = _dedup(claims)
        ordered = _dedup(claims + spans)
        origin = ORIGIN_CLAIM if claim_lines else (ORIGIN_SPAN if ordered else "")
        need = budget.lines_per_paper - len(ordered)
        if need > 0 and (fill_from_abstract or not ordered):
            fallback = _abstract_lines(node, root=Path(root), cache=cache)
            ordered = _dedup(ordered + fallback)
            if not origin:
                origin = ORIGIN_ABSTRACT
        if not ordered:
            continue
        # Claim count ranks; corpus position breaks ties without ever letting a
        # later paper overtake an equally-evidenced earlier one.
        rank = len(claim_lines) - position * 1e-6
        cards.append(
            EvidenceCard(
                paper=cited,
                lines=tuple(ordered),
                origin=origin,
                score=float(rank),
                claim_lines=len(claim_lines),
            )
        )
    cards.sort(key=lambda card: (card.origin != ORIGIN_CLAIM, -card.score))
    return apply_budget(cards, budget)

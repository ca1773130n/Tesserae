"""One prompt, one backbone call, one citation contract — for both arms.

Nothing in this module knows which arm it is serving. It is handed a query and
a list of :class:`~evals.deepscholar.evidence.EvidenceCard`, and every other
input to the model — system prompt, task wording, word cap, citation format,
retry policy, temperature — is a constant here. That is what makes the paired
control a control: an arm cannot be advantaged by a prompt it does not share,
because there is no second prompt to advantage it with.

The citation contract, and why it is spelled the way it is. DeepScholar's parser
(``eval/parsers/deepscholar_base.py``) finds citations with
``\\[([^\\]]+?)\\]\\((https?://[^\\)]+)\\)`` — INLINE markdown links over http
URLs — pulls the id out with ``arxiv\\.org/abs/([^)\\s]+)``, renumbers them
``[1]``, ``[2]``, and pairs each with a ``paper.csv`` row keyed on that exact
string. So:

* the link must be inline markdown, not reference-style. Tesserae's extractive
  bundle emits ``## [name][node-1]`` with a local file path in the definition
  block; run their regex over one and it returns zero citations. That output is
  unscoreable, not weak, which is why nothing here uses it;
* the id must carry its version suffix, character for character. It comes from
  :attr:`~evals.deepscholar.dataset.CitedPaper.arxiv_versioned`, the same field
  ``paper.csv`` is written from;
* an id that is not in ``paper.csv`` still gets a citation number and an EMPTY
  title and abstract, and the entailment judge then scores that sentence 0 with
  no error raised. :func:`render` refuses to let that reach disk — see
  :func:`strip_links`.

The word cap is the published task's own 600. It is stated to the model and
enforced only by statement, exactly as upstream: the shipped ``deepscholar_base``
artifacts run to 17,099 characters, so trimming ours would be a handicap no
competitor bears.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import List, Optional, Protocol, Sequence, Set, Tuple

from .dataset import WORD_CAP, Query
from .evidence import EvidenceCard, render_table

__all__ = [
    "Backbone",
    "CITATION_RE",
    "RenderResult",
    "SYSTEM_PROMPT",
    "build_user_prompt",
    "cited_arxiv_ids",
    "openai_backbone",
    "render",
    "repair_versions",
    "strip_links",
]

#: ``eval/parsers/deepscholar_base.py:19``, character for character. Restated
#: rather than imported because their repo is a sibling clone and not a
#: dependency; ``tests/test_deepscholar_writer.py`` pins the two together.
CITATION_RE = re.compile(r"\[([^\]]+?)\]\((https?://[^\)]+)\)")

#: ``eval/parsers/deepscholar_base.py:36``.
ARXIV_IN_URL_RE = re.compile(r"arxiv\.org/abs/([^)\s]+)")

#: The bare id inside a versioned one, for :func:`repair_versions`.
_BARE_RE = re.compile(r"(\d{4}\.\d{4,6})")

#: The default backbone. Named as a constant so a report can state it and a
#: test can assert both arms were given the same one.
PROTOCOL_BACKBONE = "gpt-4o-mini"

SYSTEM_PROMPT = """You write the Related Works section of an academic paper.

RULE 1 — Do not invent facts. Every statement you make about prior work must be \
supported by a line in the EVIDENCE table you are given. If the evidence does \
not say it, do not write it.
RULE 2 — EVERY sentence that says anything about prior work carries at least \
one citation, inside that sentence. Not one citation per paper, not one per \
paragraph — one per sentence. A sentence describing prior work with no \
citation in it is an error.
RULE 3 — A citation is written exactly like this:
    [Paper Title](http://arxiv.org/abs/ARXIV_ID)
Each entry in the EVIDENCE table ends with a `cite as:` line holding that \
paper's citation already written out. Copy that line character for character — \
including the version suffix such as v1 — and do not rebuild or shorten the URL.
RULE 4 — Never write a paper's title as plain text or in quotation marks. The \
`cite as:` link is how a paper is named; a bare title is not a citation and \
does not count as one.
RULE 5 — Cite only papers that appear in the EVIDENCE table. A link to any \
other paper, or an id you altered in any way, is a failure.
RULE 6 — Prose only: connected paragraphs. No headings, no bullet lists, no \
numbered reference list at the end, no preamble such as "Here is the section".
RULE 7 — Group related work thematically and say what each cited paper \
contributes. Two or three sentences per paper, each of them cited, is better \
than one sentence that mentions three papers.

EXAMPLE of the required density and form — note that every sentence carries its \
own link and no title appears as bare text:

    Removing protected attributes from learned representations has been \
approached by iterative projection onto a null space \
[Null It Out](http://arxiv.org/abs/2004.07667v1). That method is reported to \
outperform adversarial training across three tasks \
[Null It Out](http://arxiv.org/abs/2004.07667v1), which motivates the \
projection-based treatment used here."""


def build_user_prompt(
    query: Query, cards: Sequence[EvidenceCard], *, word_cap: int = WORD_CAP
) -> str:
    """The user turn. Identical in shape for both arms; only EVIDENCE differs."""
    return (
        "PAPER UNDER REVIEW\n"
        f"Title: {query.parent.title}\n"
        f"Abstract: {query.parent.abstract}\n\n"
        "EVIDENCE\n"
        f"{render_table(cards)}\n\n"
        "TASK\n"
        "Write the Related Works section for the paper under review, situating "
        "it against the papers in the EVIDENCE table. Do not exceed "
        f"{word_cap} words. Output the section text only."
    )


class Backbone(Protocol):
    """Anything that turns a system+user pair into text.

    :class:`tesserae.llm_json.OpenAIAPIJsonClient` satisfies this already, which
    is why there is no HTTP code in this package.
    """

    def complete_text(self, *, system: str, user: str) -> Optional[str]:
        ...


def openai_backbone(model: str = PROTOCOL_BACKBONE, **kwargs) -> Backbone:
    """The shipped backbone, on ``OPENAI_API_KEY``.

    Imported on use so the package stays importable — and testable — without
    ``tesserae.llm_json``'s provider machinery being constructed.
    """
    from tesserae.llm_json import OpenAIAPIJsonClient

    return OpenAIAPIJsonClient(model=model, **kwargs)


def cited_arxiv_ids(text: str) -> List[str]:
    """Every arXiv id the scorer will find in ``text``, in order, with repeats.

    Deliberately runs the SCORER's two regexes and not a friendlier pair. What
    this function cannot see, the benchmark cannot score.
    """
    out: List[str] = []
    for _, url in CITATION_RE.findall(text or ""):
        match = ARXIV_IN_URL_RE.search(url)
        if match:
            out.append(match.group(1))
    return out


def strip_links(text: str, bad_ids: Set[str]) -> Tuple[str, int]:
    """Replace links to ``bad_ids`` with their visible text. ``(text, removed)``.

    Removing the link rather than leaving it is the lesser of two zeros. An
    unresolvable id is still numbered by the parser and still appended to
    ``docs`` — with an empty title and an empty abstract — so it scores 0 on
    ``cite_p`` AND drags ``coverage_relevance_rate`` and the doc list that
    ``reference_coverage`` reads. Stripped, the sentence is merely uncited: the
    same 0 on ``cite_p``, and no phantom document. Neither outcome is hidden;
    the count rides on :attr:`RenderResult.stripped_citations` and the runner
    prints it.
    """
    removed = 0

    def repl(match: "re.Match[str]") -> str:
        nonlocal removed
        visible, url = match.groups()
        found = ARXIV_IN_URL_RE.search(url)
        if found and found.group(1) in bad_ids:
            removed += 1
            return visible
        return match.group(0)

    return CITATION_RE.sub(repl, text or ""), removed


def repair_versions(text: str, allowed: Set[str]) -> Tuple[str, int]:
    """Rewrite a version-less or mis-versioned id to the corpus's own spelling.

    Measured on the first live run of this harness: ``gpt-4o-mini`` wrote
    ``.../abs/2105.10312`` for a paper the evidence table printed as
    ``2105.10312v1``. That is not a fabricated source — the paper IS in the
    corpus and the model named it correctly — it is a formatting slip, and the
    slip costs the whole sentence, because an id absent from ``paper.csv``
    resolves to an empty title and an empty abstract and the entailment judge
    scores it 0 with no error.

    Repairing it is faithful precisely because it is unambiguous: the rewrite
    fires only when exactly one paper in this query's corpus shares the bare
    id. An id whose bare form matches nothing here is a citation to a paper the
    writer was never shown, and that is left alone for :func:`strip_links`.

    Applied identically to both arms, before the retry, and counted separately
    on :attr:`RenderResult.repaired_citations` so a run can report how much of
    its citation rate came from a repair rather than from the model.
    """
    by_bare: dict = {}
    for identifier in allowed:
        match = _BARE_RE.match(identifier)
        if match:
            by_bare.setdefault(match.group(1), set()).add(identifier)
    repaired = 0

    def repl(match: "re.Match[str]") -> str:
        nonlocal repaired
        visible, url = match.groups()
        found = ARXIV_IN_URL_RE.search(url)
        if not found or found.group(1) in allowed:
            return match.group(0)
        bare = _BARE_RE.match(found.group(1))
        if not bare:
            return match.group(0)
        candidates = by_bare.get(bare.group(1)) or set()
        if len(candidates) != 1:
            return match.group(0)
        repaired += 1
        fixed = next(iter(candidates))
        return f"[{visible}](http://arxiv.org/abs/{fixed})"

    return CITATION_RE.sub(repl, text or ""), repaired


@dataclass(frozen=True)
class RenderResult:
    """One query's generated section, and everything needed to audit it."""

    file_id: str
    text: str
    #: Distinct arXiv ids the scorer will resolve, after repair.
    cited: Tuple[str, ...]
    #: How many links were stripped because they named a paper outside this
    #: query's ``paper.csv``.
    stripped_citations: int
    #: Backbone calls actually spent on this query. 1 normally, 2 when the
    #: first reply cited outside the corpus, 0 when the backbone was silent.
    calls: int
    #: How many links named a corpus paper with the wrong version suffix and
    #: were rewritten to the spelling ``paper.csv`` carries.
    repaired_citations: int = 0
    cards: Tuple[EvidenceCard, ...] = ()
    #: Set when the backbone returned nothing at all. A failed generation must
    #: not be scored as an empty section that "hallucinates nothing".
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.text.strip())


def render(
    query: Query,
    cards: Sequence[EvidenceCard],
    *,
    backbone: Backbone,
    word_cap: int = WORD_CAP,
    retry_on_bad_citation: bool = True,
) -> RenderResult:
    """Generate one Related Works section. One backbone call, two on repair.

    The retry is not a quality loop and does not re-rank, re-retrieve or
    re-prompt for style — it names the ids the model invented and asks again
    with the identical evidence. Both arms get it, so neither can be advantaged
    by it; and the budget line in the report counts calls, not queries, so a
    run where one arm retried more often is visible rather than absorbed.
    """
    allowed = {card.paper.arxiv_versioned for card in cards}
    system = SYSTEM_PROMPT
    user = build_user_prompt(query, cards, word_cap=word_cap)

    calls = 0
    text = backbone.complete_text(system=system, user=user)
    calls += 1
    if text is None:
        return RenderResult(
            file_id=query.file_id, text="", cited=(), stripped_citations=0,
            calls=calls, cards=tuple(cards),
            error="backbone returned no text",
        )

    text, repaired = repair_versions(text, allowed)
    bad = {cid for cid in cited_arxiv_ids(text) if cid not in allowed}
    if bad and retry_on_bad_citation:
        retry_user = (
            f"{user}\n\n"
            "CORRECTION\n"
            "Your previous answer cited arXiv ids that are not in the EVIDENCE "
            f"table: {', '.join(sorted(bad))}. Rewrite the section using only "
            "the ids printed on the `arxiv_id:` lines above, copied character "
            "for character."
        )
        again = backbone.complete_text(system=system, user=retry_user)
        calls += 1
        if again is not None:
            again, again_repaired = repair_versions(again, allowed)
            retry_bad = {cid for cid in cited_arxiv_ids(again) if cid not in allowed}
            if len(retry_bad) <= len(bad):
                text, bad, repaired = again, retry_bad, again_repaired

    stripped = 0
    if bad:
        text, stripped = strip_links(text, bad)
        print(
            f"[deepscholar] query {query.file_id}: stripped {stripped} citation(s) "
            f"to ids outside paper.csv ({', '.join(sorted(bad))})",
            file=sys.stderr,
        )

    seen: List[str] = []
    for cid in cited_arxiv_ids(text):
        if cid not in seen:
            seen.append(cid)
    return RenderResult(
        file_id=query.file_id,
        text=text.strip(),
        cited=tuple(seen),
        stripped_citations=stripped,
        calls=calls,
        repaired_citations=repaired,
        cards=tuple(cards),
    )

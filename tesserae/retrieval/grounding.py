"""Novel Grounded Evidence — a read-side signal that an answer is grounded.

Retrieval on this benchmark is saturated: the gold document is in the top 10
for 282 of 284 answerable questions. The remaining error is therefore not a
retrieval problem, and no reranking fixes it. What is left is a *reading*
problem — the model is handed the right documents and writes an answer that
the documents do not support.

The obvious detector for that is **extractive support**: how much of the
answer's vocabulary appears in the sources. Measured on 352 persisted answers
it scores AUC 0.587, barely above chance, and the reason it fails is
structural. Retrieval selected those documents *by the question's terms*, so
an answer that merely restates the question is fully supported by
construction. Fabrications restate the question more than correct answers do —
question-echo fraction 0.49 against 0.35, AUC 0.681/0.702 across two
independent systems — so the confound does not wash out, it actively rewards
the failure mode.

Subtracting the question's own vocabulary is the whole mechanism:

    NGE(answer) = sum of idf(t) over content tokens t of the answer that the
                  QUESTION does not contain and that the SOURCES do.

Detector AUC, measured with this module on 352 persisted answers from two
independent systems: **0.746 [0.663, 0.826] on Tesserae and 0.763 [0.663,
0.851] on a retrieval-hybrid baseline** — scored on NON-REFUSED rows only,
which is the only set where a new gate can add anything (half the controls
are already refusals, and "I don't know" has no content tokens, so scoring
all 352 rows just re-reads the decision already made). AUC is the primary
number here and Youden J the derived one, because AUC is a deterministic
function of the answer string and so survives the run-to-run instability
that moves refusal rates on this benchmark by 13 points.

Three properties this module is built for:

* **Zero marginal cost.** The inputs are three strings that already exist at
  answer time: the question, the generated answer, and the source text that
  was pasted into the prompt. No second generation, no embedding, no graph
  read. Chain-of-Note, the competing reading-stage mechanism, costs 2-3x the
  answer budget; this costs 1.0x.
* **Reported, never enforced.** :class:`tesserae.query.QueryResult` carries
  the number so a caller can decide. Gating on it is opt-in and lives in the
  eval arms. An eval-only behaviour becoming the product default is a change
  this repo has already reverted once.
* **tau is a quantile, never a constant.** See :func:`grounding_tau`.

HONEST LIMIT, because it belongs next to the number: this is a filter, not a
solution. At the offline operating point it caught 11 of 32 fabrications and
newly refused 14 of 284 answerable questions, costing token F1 0.3534 ->
0.3380. It systematically penalises correct answers that are short and
generic — a one-word answer naming a common term scores near zero.

Part of the raw AUC is answer length: length alone scores 0.689 on the same
rows, and NGE falls to 0.695 once length is stratified into quartiles. The
honest claim is that NGE beats length, not that it is independent of it, and
the length-stratified figure must be quoted wherever the raw one is — the
benchmark's 48 legacy controls were voided for being separable by word count
alone at |AUC| 0.986, and this must not repeat that.

And the J gain does not generalise even though the detector does. It needs
headroom: Tesserae has it because it currently refuses almost nothing
(ref|answerable 0.025), while the retrieval-hybrid baseline already refuses
21% of answerable questions and gains dJ +0.032 with a CI crossing zero.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .hybrid import _tokenize

__all__ = [
    "STOPWORDS",
    "corpus_idf",
    "idf_from_document_frequency",
    "grounding_tau",
    "novel_grounded_evidence",
]


#: Function words carry no evidence, and an answer padded with them must not
#: score as grounded. Kept as an explicit, reviewable frozenset rather than a
#: dependency on an NLP package's list — like ``scorer.REFUSAL_MARKERS``, a
#: benchmark-adjacent list that nobody can read is a place for the measurement
#: to quietly grade itself. Tokens of two characters or fewer are dropped
#: separately, which covers most of what is missing here.
STOPWORDS = frozenset(
    """
    a an the of in on at to for from by with and or but is are was were be been
    being as that this these those it its their his her they he she we you i
    not no nor if then than so such which who whom what when where why how all
    any both each few more most other some only own same too very can will just
    should now do does did done have has had having about into over under
    between during before after above below up down out off again further once
    here there because while against among through per via use used using also
    may might must would could shall one two three new first second third
    within without across upon
    """.split()
)


#: Bracketed provenance citations, stripped from the answer before it is
#: scored. A citation is metadata about where an answer came from, not
#: evidence the model produced, and counting it is actively harmful here:
#: a citation quotes a source id or a node NAME, so it is rare and
#: source-attested *by construction* and hands free NGE to any answer that
#: cites — including a fabrication that cites the wrong document. Measured on
#: 277 of 284 Tesserae short-span answers, which carry a median 11 citation
#: tokens on a median 19-token answer; leaving them in costs 0.032 of J on
#: that arm. The pattern is ANY bracketed span rather than the id-shaped
#: :data:`tesserae.citation_names.NODE_CITATION_RE`, because 83% of real
#: citations cite a name and contain a space.
#:
#: Not to be confused with ask_planner's older "grounding gate", which checks
#: that a cited answer HAS a citation. That one asks whether provenance is
#: present; this module asks what the answer says once provenance is removed.
_CITATION_RE = re.compile(r"\[[^\]]{2,}\]")


def _content(text: Optional[str]) -> List[str]:
    """Evidence-bearing tokens: not a stopword, longer than two characters.

    Bracketed citations are removed first — see :data:`_CITATION_RE`.
    """
    stripped = _CITATION_RE.sub(" ", text or "")
    return [t for t in _tokenize(stripped) if t not in STOPWORDS and len(t) > 2]


def _hapax_idf(n_docs: int) -> float:
    """Weight for a term seen in exactly one document of an ``n_docs`` bundle.

    Also the fallback for a term missing from ``idf`` entirely: an unseen term
    is at least as rare as a hapax, and the caller has already established it
    appears in the sources.
    """
    return math.log(1 + (max(1, n_docs) - 1 + 0.5) / 1.5)


def idf_from_document_frequency(
    df: Mapping[str, int], n_docs: int
) -> Dict[str, float]:
    """BM25 idf from a document-frequency table.

    The formula is the one :func:`tesserae.retrieval.hybrid._bm25_scores`
    already uses, so "rare" means the same thing to the gate as it does to the
    retriever that assembled the bundle.

    Exposed separately from :func:`corpus_idf` because rarity must be measured
    over the WHOLE corpus while attestation is checked against the handful of
    documents actually shown to the model. Deriving idf from the shown bundle
    alone would put every term within a factor of two of every other — a
    ten-document bundle caps idf at 1.93 — and the score would stop
    discriminating. Callers that already hold a corpus-wide df (a search
    index, a compiled graph) pass it here instead of re-reading the corpus.
    """
    if n_docs <= 0:
        return {}
    return {
        term: math.log(1 + (n_docs - freq + 0.5) / (freq + 0.5))
        for term, freq in df.items()
    }


def corpus_idf(source_texts: Sequence[str]) -> Tuple[Dict[str, float], int]:
    """Document frequency over a corpus, as BM25 idf. Compute once, cache.

    Returns ``(idf, n_docs)``; pass both to :func:`novel_grounded_evidence`.
    """
    documents = list(source_texts or [])
    n_docs = len(documents)
    if not n_docs:
        return {}, 0
    df: Counter = Counter()
    for text in documents:
        df.update(set(_tokenize(text or "")))
    return idf_from_document_frequency(df, n_docs), n_docs


def novel_grounded_evidence(
    answer: Optional[str],
    question: Optional[str],
    source_texts: Sequence[str],
    idf: Dict[str, float],
    n_docs: int,
) -> float:
    """Rare, source-attested vocabulary the answer adds beyond the question.

    Higher is more grounded. Zero means the answer contributed no evidence
    token that the question did not already carry and the sources do — the
    signature of a fluent restatement, which is what fabrication looks like
    when retrieval has already succeeded.

    Every argument is a string or a cached table; nothing here calls a model.
    """
    if not answer or not source_texts:
        return 0.0
    attested: set = set()
    for text in source_texts:
        attested.update(_tokenize(text or ""))
    if not attested:
        return 0.0
    asked = set(_content(question))
    fallback = _hapax_idf(n_docs)
    seen: set = set()
    total = 0.0
    for token in _content(answer):
        if token in seen or token in asked or token not in attested:
            continue
        seen.add(token)
        total += idf.get(token, fallback)
    return total


def grounding_tau(idf: Dict[str, float], quantile: float = 0.25) -> float:
    """A refusal threshold expressed as a quantile of the bundle's own idf.

    **Never ship an absolute constant here.** BM25 idf scales with
    ``log(n_docs)``: a term seen in one document is worth about 4.5 in the
    135-document corpus this was tuned on and about 10.6 on the 62k-node
    graph. The absolute tau that refuses sensibly on the first is noise on the
    second, so the gate would silently stop gating on exactly the corpus that
    matters. Taking a quantile of the distribution the bundle actually has
    moves the threshold with the scale.

    ``quantile`` indexes the sorted idf *values* over the vocabulary, with
    linear interpolation between neighbours. Real vocabulary is Zipf-shaped,
    so most of that distribution sits in the rare tail and the knob reads as
    "how much of one rare word must the answer add" — which is the quantity
    that has to stay fixed across corpus sizes.

    The default 0.25 gives tau = 3.41 on the 135-document evaluation corpus
    and was picked from the middle of a flat region rather than at its
    argmax: measured Youden J is +0.617 at quantile 0.25 and stays within
    0.005 of that anywhere in [0.125, 0.5], so nothing here rests on the
    exact value. An empty vocabulary gives 0.0, which disables the gate
    rather than refusing everything — the safe direction, since the
    product's contract is to answer.
    """
    values = sorted(idf.values())
    if not values:
        return 0.0
    q = min(1.0, max(0.0, float(quantile)))
    position = q * (len(values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(values[low])
    return float(values[low] + (values[high] - values[low]) * (position - low))

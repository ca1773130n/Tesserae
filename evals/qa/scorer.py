"""Scores answers against gold answers. This module is the artifact.

Three attempts in this repo have driven a competitor QA system end to end and
none of them produced a number, because running a system and *measuring* it are
different jobs and only the first one was ever built. This is the second one.

What it computes, per system and per stratum:

* **exact match** — normalized string equality (SQuAD normalization: casefold,
  drop articles and punctuation, collapse whitespace);
* **token F1** — precision/recall/F1 over the answer's token *multiset*, via
  the shared :func:`evals.metrics.prf1`, so "F1" means the same thing here as
  it does in the federation eval;
* **refusal and hallucination rates on unanswerable questions** — a question
  with no answer in the corpus should draw a refusal; a fluent wrong answer to
  it is the failure mode neither of the two rates above can see.

Deliberate properties:

* **No dependency on anything outside the standard library and
  ``evals.metrics``.** Not on the vendored cognee clone, not on ``tesserae``,
  not on an LLM. The scorer must be testable — and correct — with no corpus, no
  network and no compile, or it will not get tested at all.
* **Over-refusal is reported next to hallucination.** A system that answers "I
  don't know" to everything scores a perfect 0.0 hallucination rate. That number
  is worthless without ``refusal_rate`` on the *answerable* stratum beside it,
  so the two are always emitted together and neither is ever reported alone.
* **Ties are ties.** :func:`rank_systems` gives equal scores equal rank instead
  of letting dict order crown a winner.

Nothing here reads a wall clock or writes to a graph.
"""

from __future__ import annotations

import re
import string
import unicodedata
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from ..metrics import prf1

#: A row's ``gold`` is ``None`` when the question is **unanswerable**: the
#: corpus does not contain the answer and the correct behaviour is to say so.
#: Distinct from ``""`` (gold answer is the empty string), which is a real,
#: matchable answer — the distinction is the whole unanswerable metric.
UNANSWERABLE = None

#: Substrings that mark an answer as a refusal, matched against the *normalized*
#: answer so punctuation and casing do not matter. Kept as an explicit,
#: reviewable list rather than a cleverer classifier: a refusal detector that
#: nobody can read is a place for the benchmark to quietly grade itself.
#:
#: Every entry is normalized on import by the same function that normalizes the
#: answer, so "I don't know." and "i do not know" both have to be written the
#: way the normalizer produces them — hence the tests pinning both spellings.
#:
#: KNOWN LIMITATION, stated rather than hidden: matching is by substring, so a
#: hedged answer that still answers ("I do not know for certain, but Scotland")
#: is counted as a refusal. Exact match and token F1 are computed independently
#: and are unaffected — only ``refusal_rate`` over-counts, and only for systems
#: that hedge. The short-answer instruction every system is asked under
#: (:data:`evals.qa.null_model.NULL_SYSTEM_PROMPT`) is what keeps that rare;
#: if a system under test hedges routinely, read its refusal rate as an upper
#: bound and say so in the report.
REFUSAL_MARKERS: Tuple[str, ...] = (
    "i dont know",
    "i do not know",
    "i am not sure",
    "im not sure",
    "cannot answer",
    "can not answer",
    "cant answer",
    "unable to answer",
    "no answer",
    "not enough information",
    "insufficient information",
    "no information",
    "not mentioned",
    "not stated",
    "not specified",
    "not found in",
    "not present in",
    "does not contain",
    "no relevant",
    "unanswerable",
)

#: The vendored ``QABenchmarkRAG.answer_questions`` catches a query exception and
#: records the answer as ``f"Error: {exc}"``. That is a harness failure, not the
#: system's answer and not a refusal — counting it as either would let a broken
#: run read as a cautious one. Detected here so a saved results file scores
#: honestly.
_ERROR_PREFIX = re.compile(r"^\s*error\s*:", re.IGNORECASE)

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_WS = re.compile(r"\s+")
#: Apostrophes are DELETED; every other punctuation mark becomes a space. An
#: apostrophe inside a word is a contraction or a possessive ("don't",
#: "Minnesota's"), so splitting on it would produce the stray token "t" and put
#: "i don t know" in the refusal list. Everything else is a separator.
_APOSTROPHES = str.maketrans({"'": "", "‘": "", "’": "", "ʼ": ""})
_PUNCT_TABLE = {ord(c): " " for c in string.punctuation if c != "'"}


def normalize_answer(text: Optional[str]) -> str:
    """SQuAD/HotpotQA answer normalization.

    Casefold, strip accents, drop apostrophes, replace the remaining punctuation
    with spaces, drop the English articles, collapse whitespace. ``None``
    normalizes to ``""``.

    Punctuation becomes a *space* rather than being deleted, so "gpt-5.4"
    tokenizes to ``["gpt", "5", "4"]`` instead of the single token "gpt54".
    Deleting it would make "co-slam" and "coslam" identical and "1,000" and
    "1000" different, which is the wrong trade in both directions. Apostrophes
    are the documented exception — see :data:`_APOSTROPHES`.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", str(text)).casefold()
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.translate(_APOSTROPHES)
    folded = folded.translate(_PUNCT_TABLE)
    folded = _ARTICLES.sub(" ", folded)
    return _WS.sub(" ", folded).strip()


def tokenize(text: Optional[str]) -> List[str]:
    """Whitespace tokens of the normalized answer."""
    normalized = normalize_answer(text)
    return normalized.split() if normalized else []


_NORMALIZED_REFUSALS: Tuple[str, ...] = tuple(
    dict.fromkeys(normalize_answer(m) for m in REFUSAL_MARKERS)
)


def is_error(text: Optional[str]) -> bool:
    """True for the ``Error: ...`` string the vendored ABC records on a crash."""
    return bool(text) and bool(_ERROR_PREFIX.match(str(text)))


def is_refusal(text: Optional[str]) -> bool:
    """True when the answer declines to answer.

    An empty or whitespace-only answer counts: a system that returns nothing has
    declined, whatever it meant to do. A harness ``Error:`` does **not** count —
    see :data:`_ERROR_PREFIX`.
    """
    if is_error(text):
        return False
    normalized = normalize_answer(text)
    if not normalized:
        return True
    return any(marker in normalized for marker in _NORMALIZED_REFUSALS if marker)


def exact_match(predicted: Optional[str], gold: Optional[str]) -> bool:
    """Normalized string equality. Two empty answers match."""
    return normalize_answer(predicted) == normalize_answer(gold)


def token_f1(predicted: Optional[str], gold: Optional[str]) -> Dict[str, float]:
    """Precision/recall/F1 over the answer token multisets, via :func:`prf1`.

    Multiset, not set: an answer that repeats a gold token twice gets credit
    once and pays a false positive for the repeat, which is what stops "the the
    the the" from scoring well against "the".

    Both sides empty is the one case :func:`prf1` cannot decide (0/0/0 would
    score 0.0) and it is decided here: two empty answers agree, so F1 is 1.0.
    An empty prediction against a real gold answer scores 0.0 — no credit for
    silence.
    """
    predicted_tokens = tokenize(predicted)
    gold_tokens = tokenize(gold)
    if not predicted_tokens and not gold_tokens:
        return {"tp": 0, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0}
    gold_remaining: Dict[str, int] = {}
    for token in gold_tokens:
        gold_remaining[token] = gold_remaining.get(token, 0) + 1
    tp = 0
    for token in predicted_tokens:
        if gold_remaining.get(token, 0) > 0:
            gold_remaining[token] -= 1
            tp += 1
    return prf1(tp, len(predicted_tokens) - tp, len(gold_tokens) - tp)


def _gold_alternatives(gold: Union[None, str, Sequence[str]]) -> List[str]:
    """Gold answers as an ordered list. ``None`` (unanswerable) yields ``[]``."""
    if gold is UNANSWERABLE:
        return []
    if isinstance(gold, str):
        return [gold]
    return list(gold)


def score_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Score one ``{question, answer, gold, stratum}`` record.

    ``gold`` may be a string, a list of accepted aliases, or ``None`` for an
    unanswerable question. With aliases, the best-scoring one wins on
    ``(f1, exact_match)`` and **ties break toward the earliest alias in the
    list** — deterministic, so the same answers file always scores the same.

    ``golden_answer`` is accepted as a synonym for ``gold`` because that is the
    key the vendored ``QABenchmarkRAG`` writes into its results file.
    """
    predicted = row.get("answer")
    gold = row["gold"] if "gold" in row else row.get("golden_answer", UNANSWERABLE)
    alternatives = _gold_alternatives(gold)
    answerable = bool(alternatives)
    refused = is_refusal(predicted)
    errored = is_error(predicted)

    scored: Dict[str, Any] = {
        "question": row.get("question"),
        "answer": predicted,
        "gold": gold,
        "stratum": row.get("stratum") or row.get("level") or "unspecified",
        "answerable": answerable,
        "refused": refused,
        "errored": errored,
    }

    if not answerable:
        # Nothing to match against. The only question is whether the system knew
        # to say so; a substantive answer to a question with no answer is a
        # hallucination, and a harness error is neither.
        scored.update({
            "exact_match": False, "f1": 0.0, "precision": 0.0, "recall": 0.0,
            "tp": 0, "fp": 0, "fn": 0,
            "hallucinated": not refused and not errored,
            "matched_gold": None,
        })
        return scored

    best: Optional[Tuple[float, bool, str, Dict[str, float]]] = None
    for alternative in alternatives:
        counts = token_f1(predicted, alternative)
        em = exact_match(predicted, alternative)
        candidate = (counts["f1"], em, alternative, counts)
        # Strict > keeps the FIRST alias on a tie.
        if best is None or (candidate[0], candidate[1]) > (best[0], best[1]):
            best = candidate
    assert best is not None  # alternatives is non-empty on this branch
    f1, em, matched, counts = best
    scored.update({
        "exact_match": em, "f1": f1,
        "precision": counts["precision"], "recall": counts["recall"],
        "tp": counts["tp"], "fp": counts["fp"], "fn": counts["fn"],
        "hallucinated": False, "matched_gold": matched,
    })
    return scored


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize(scored_rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Aggregate already-scored rows into one summary block.

    Two F1s are reported because they answer different questions and neither is
    a substitute for the other:

    * ``f1_macro`` — the mean of the per-question F1s. Every question counts the
      same. This is the HotpotQA-convention number.
    * ``f1_micro`` — :func:`prf1` over the summed token counts. Long answers
      count more. Reported because it is the number the federation eval's F1 is
      arithmetically the same as, and a wide macro/micro gap says the system is
      failing selectively on short answers (or long ones).

    ``refusal_rate`` here is over **answerable** questions — over-refusal, the
    counterweight to ``hallucination_rate``. Both are always present in the
    output even when the corresponding stratum is empty, so a report template
    cannot silently omit the inconvenient one.
    """
    answerable = [r for r in scored_rows if r["answerable"]]
    unanswerable = [r for r in scored_rows if not r["answerable"]]
    micro = prf1(
        sum(int(r["tp"]) for r in answerable),
        sum(int(r["fp"]) for r in answerable),
        sum(int(r["fn"]) for r in answerable),
    )
    return {
        "n": len(scored_rows),
        "n_answerable": len(answerable),
        "n_unanswerable": len(unanswerable),
        "exact_match": _mean([1.0 if r["exact_match"] else 0.0 for r in answerable]),
        "f1_macro": _mean([float(r["f1"]) for r in answerable]),
        "f1_micro": micro["f1"],
        "precision_micro": micro["precision"],
        "recall_micro": micro["recall"],
        "refusal_rate": _mean([1.0 if r["refused"] else 0.0 for r in answerable]),
        "hallucination_rate": _mean([1.0 if r["hallucinated"] else 0.0 for r in unanswerable]),
        "unanswerable_refusal_rate": _mean([1.0 if r["refused"] else 0.0 for r in unanswerable]),
        "error_rate": _mean([1.0 if r["errored"] else 0.0 for r in scored_rows]),
    }


def score_system(
    rows: Iterable[Mapping[str, Any]],
    *,
    system: str,
    meta: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Score every row of one system's answers, overall and per stratum.

    ``meta`` is carried through untouched and is where the fairness declarations
    live (``llm_model``, ``embedding_model``, ``embedding_dim``, ``corpus``).
    See :func:`fairness_blockers` — a number without that block is not
    publishable, so the shape that produces the number carries it.
    """
    scored = [score_row(row) for row in rows]
    strata: Dict[str, List[Dict[str, Any]]] = {}
    for row in scored:
        strata.setdefault(str(row["stratum"]), []).append(row)
    return {
        "system": system,
        "meta": dict(meta or {}),
        "overall": summarize(scored),
        "strata": {name: summarize(rows_) for name, rows_ in sorted(strata.items())},
        "rows": scored,
    }


def rank_systems(
    reports: Sequence[Mapping[str, Any]],
    *,
    key: Union[str, Callable[[Mapping[str, Any]], float]] = "f1_macro",
    ndigits: int = 4,
) -> List[Dict[str, Any]]:
    """Rank systems by a summary metric, with ties held as ties.

    Standard competition ranking: two systems tied at the top are both rank 1
    and the next is rank 3. Scores are compared rounded to ``ndigits`` first,
    because two systems that differ in the twelfth decimal place of a mean are
    not distinguishable by a 24-question benchmark and reporting them as ordered
    is a false claim. Within a tie, systems are ordered by name so the output is
    stable.

    Returns one entry per system: ``{rank, system, score, tied}``.
    """
    getter = key if callable(key) else (lambda report: float(report["overall"][key]))
    scored = sorted(
        ((round(float(getter(r)), ndigits), str(r["system"])) for r in reports),
        key=lambda pair: (-pair[0], pair[1]),
    )
    out: List[Dict[str, Any]] = []
    for index, (score, system) in enumerate(scored):
        rank = index + 1
        if index and score == scored[index - 1][0]:
            rank = out[-1]["rank"]
        tied = sum(1 for s, _ in scored if s == score) > 1
        out.append({"rank": rank, "system": system, "score": score, "tied": tied})
    return out


#: Declarations that must MATCH across systems before a cross-system number can
#: be published. Each maps to the human sentence explaining why a mismatch
#: invalidates the comparison rather than merely complicating it.
FAIRNESS_KEYS: Dict[str, str] = {
    "llm_model": (
        "the answering model differs, so the comparison measures the models, "
        "not the retrieval"
    ),
    "embedding_model": (
        "the retrieval embeddings differ, so recall differences are not "
        "attributable to the graph"
    ),
    "embedding_dim": "the embedding dimensionality differs alongside the model",
    "corpus": "the systems did not read the same documents",
    "question_set": "the systems did not answer the same questions",
}

#: Checks a ``meta["role"] == "baseline"`` system is exempt from. A null model
#: has no retrieval — that is what it is for — so requiring it to declare a
#: matching embedding backend would make every baseline comparison unpublishable
#: and the gate would get switched off. It stays subject to ``llm_model``, which
#: is the half that matters: a baseline run on a different model than the system
#: it baselines measures nothing.
BASELINE_EXEMPT_KEYS = frozenset({"embedding_model", "embedding_dim"})


def fairness_blockers(reports: Sequence[Mapping[str, Any]]) -> List[str]:
    """Reasons the systems in ``reports`` may NOT be compared in public.

    An empty list means every declaration agrees. A **missing** declaration is
    itself a blocker: "we did not record which model answered" is not the same
    as "the models matched", and a benchmark that treats the two alike will
    publish the first invalid comparison it is handed.
    """
    blockers: List[str] = []
    if len(reports) < 2:
        return blockers
    for key, why in FAIRNESS_KEYS.items():
        declared = {
            str(r["system"]): (r.get("meta") or {}).get(key)
            for r in reports
            if not (
                key in BASELINE_EXEMPT_KEYS
                and (r.get("meta") or {}).get("role") == "baseline"
            )
        }
        if len(declared) < 2:
            continue
        missing = sorted(name for name, value in declared.items() if value in (None, ""))
        if missing:
            blockers.append(f"{key}: not declared by {', '.join(missing)} — {why}")
            continue
        values = {str(v) for v in declared.values()}
        if len(values) > 1:
            detail = ", ".join(f"{name}={declared[name]}" for name in sorted(declared))
            blockers.append(f"{key}: differs ({detail}) — {why}")
    return blockers


__all__ = [
    "BASELINE_EXEMPT_KEYS",
    "FAIRNESS_KEYS",
    "REFUSAL_MARKERS",
    "UNANSWERABLE",
    "exact_match",
    "fairness_blockers",
    "is_error",
    "is_refusal",
    "normalize_answer",
    "rank_systems",
    "score_row",
    "score_system",
    "summarize",
    "token_f1",
    "tokenize",
]

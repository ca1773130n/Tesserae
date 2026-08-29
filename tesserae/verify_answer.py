"""Did this answer say things its evidence does not contain?

THE QUESTION THIS ASKS, AND THE ONE IT DOES NOT. An earlier version of this
module asked whether the GRAPH relates the entities a sentence mentions. That
question has no useful answer: a sentence can name three entities and assert
nothing about any of them — "in long document summarization and time-series
forecasting, several strategies exist" — so co-occurrence is not assertion and
the check flagged 92% of real agent output. Four rounds of entity filtering did
not fix it, because the filter was never the defect.

This asks the question a hallucination guard actually needs: of the content
words in this sentence, how many appear in the evidence the model was handed?
A sentence built from its evidence scores high. A sentence the model supplied
from its own weights scores low. That is checkable without a judge, without
tokens, and identically on every run.

WHAT A VERDICT MEANS.

* ``SUPPORTED`` — the sentence's content words are largely present in the
  evidence. It does NOT mean the sentence is true, or that the evidence entails
  it; it means the model was working from what it was given.
* ``UNSUPPORTED`` — the content is largely absent from the evidence. This is
  the flag. It is not proof of fabrication: a correct paraphrase using
  different vocabulary lands here too, which is exactly why the threshold is
  measured against a negative control rather than chosen.
* ``NO_CONTENT`` — nothing to check. Framing, transitions, refusals.

THE THRESHOLD IS VALIDATED, NOT PICKED. Scoring an answer against its OWN
evidence and against a DIFFERENT question's evidence gives two distributions
that must separate; where they separate is where the threshold belongs, and how
far they separate is whether the check works at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

SUPPORTED = "SUPPORTED"
UNSUPPORTED = "UNSUPPORTED"
NO_CONTENT = "NO_CONTENT"
VERDICTS = (SUPPORTED, UNSUPPORTED, NO_CONTENT)

#: Coverage at or above which a sentence counts as drawn from its evidence.
#:
#: MEASURED AGAINST A NEGATIVE CONTROL, not chosen. 442 sentences of real agent
#: output were scored twice — against the evidence the model was given, and
#: against a different question's evidence:
#:
#:     mean coverage, own evidence         0.682
#:     mean coverage, foreign evidence     0.365
#:     AUC (separation)                    0.908
#:
#: The operating point follows from the curve:
#:
#:     threshold   flagged on own   flagged on foreign
#:                 (false alarms)   (catches)
#:          0.50            11.3%               78.5%
#:          0.60            26.7%               90.0%
#:          0.70            50.7%               96.6%
#:          0.80            74.2%               99.3%
#:
#: 0.50 catches roughly four in five ungrounded sentences while flagging about
#: one in nine grounded ones. This first shipped at 0.70 on nothing but taste,
#: which would have flagged HALF of correct output — and a flag nobody trusts is
#: worth less than no flag, so the number had to come from the control.
#:
#: Raise it when a missed fabrication costs more than a false alarm; lower it
#: when the reverse is true. Both directions are on the curve above.
DEFAULT_COVERAGE = 0.50

#: The coverage band where this check is least reliable, and the only band
#: worth paying a model to re-decide.
#:
#: MEASURED, not chosen. On 755 held-out sentence-evidence pairs — LoCoMo
#: conversation, with the threshold above fitted on academic papers, so held
#: out by domain as well as by data — the deterministic check scored 0.870 and
#: a gpt-4o-mini judge asked about every sentence scored 0.926. Deferring only
#: this band to that judge scored 0.932 on 42% of the calls: indistinguishable
#: from asking it about everything (McNemar p=0.52) at 42% of the cost, and
#: clearly better than the check alone (p=4.3e-07).
#:
#: Widening it buys nothing. 0.25-0.80 defers 63% and scores 0.923; 0.40-0.60
#: defers 22% and scores 0.914. The arms fail on different sentences — 98 wrong
#: for the check, 56 for the judge, only 14 for both — which is why splitting
#: the work beats either one.
UNCERTAIN_LOW = 0.30
UNCERTAIN_HIGH = 0.70

#: A sentence with fewer content words than this carries no checkable claim.
#: "It is important." would otherwise score 0.0 and be flagged as fabricated.
MIN_CONTENT_WORDS = 3

_TOKEN = re.compile(r"[0-9A-Za-z]+(?:[-._][0-9A-Za-z]+)*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")

#: Words that carry no claim. Deliberately small: an aggressive list would make
#: every sentence look supported by leaving only words the evidence must share.
STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for with
by from as is are was were be been being it its they them their there here we
our us you your he she his her which who whom whose what when where why how
not no nor can could may might must shall should will would do does did done
have has had having also very more most much many some any all both each other
such only own same so too just now new used using use uses because while during
between into through above below over under again further once about against
""".split())


@dataclass(frozen=True)
class SentenceVerdict:
    sentence: str
    verdict: str
    coverage: float = 0.0
    #: Content words the evidence does NOT contain — what the flag is about.
    missing: Tuple[str, ...] = ()
    content_words: int = 0
    #: True when a model re-decided this sentence because its coverage fell in
    #: the uncertain band. Kept on the record so an audit can tell a verdict
    #: that cost nothing from one that cost a call.
    adjudicated: bool = False


@dataclass(frozen=True)
class AnswerReport:
    sentences: Tuple[SentenceVerdict, ...] = ()

    @property
    def counts(self) -> Dict[str, int]:
        out = {v: 0 for v in VERDICTS}
        for s in self.sentences:
            out[s.verdict] = out.get(s.verdict, 0) + 1
        return out

    @property
    def checkable(self) -> int:
        """Sentences carrying a claim. A rate over ALL sentences would let an
        answer look grounded by containing more framing."""
        return self.counts[SUPPORTED] + self.counts[UNSUPPORTED]

    @property
    def supported_rate(self) -> Optional[float]:
        n = self.checkable
        return None if not n else self.counts[SUPPORTED] / n

    @property
    def mean_coverage(self) -> Optional[float]:
        vals = [s.coverage for s in self.sentences if s.verdict != NO_CONTENT]
        return sum(vals) / len(vals) if vals else None

    def flagged(self) -> List[SentenceVerdict]:
        return [s for s in self.sentences if s.verdict == UNSUPPORTED]


def split_sentences(text: str) -> List[str]:
    parts = [p.strip() for p in _SENTENCE.split(text or "") if p.strip()]
    return parts or ([text.strip()] if (text or "").strip() else [])


def content_words(text: str) -> List[str]:
    return [w for w in (t.casefold() for t in _TOKEN.findall(text or ""))
            if len(w) >= 3 and w not in STOPWORDS]


def check_against_evidence(
    answer: str,
    evidence: str,
    *,
    coverage: Optional[float] = None,
) -> AnswerReport:
    """Verdict every sentence of ``answer`` against the ``evidence`` given to it.

    Pure: no LLM, no embedding, no network. Same inputs, same report — which is
    what lets it be an audit rather than an opinion.
    """
    thr = DEFAULT_COVERAGE if coverage is None else coverage
    have: Set[str] = set(content_words(evidence))
    out: List[SentenceVerdict] = []
    for sentence in split_sentences(answer):
        words = content_words(sentence)
        uniq = list(dict.fromkeys(words))
        if len(uniq) < MIN_CONTENT_WORDS:
            out.append(SentenceVerdict(sentence, NO_CONTENT, 1.0, (), len(uniq)))
            continue
        missing = tuple(w for w in uniq if w not in have)
        cov = 1.0 - (len(missing) / len(uniq))
        verdict = SUPPORTED if cov >= thr else UNSUPPORTED
        out.append(SentenceVerdict(sentence, verdict, cov, missing, len(uniq)))
    return AnswerReport(tuple(out))


def adjudicate_uncertain(
    report: AnswerReport,
    evidence: str,
    judge: Callable[[str, str], Optional[str]],
    *,
    low: float = UNCERTAIN_LOW,
    high: float = UNCERTAIN_HIGH,
) -> AnswerReport:
    """Re-decide only the sentences whose coverage lands in the uncertain band.

    ``judge(sentence, evidence)`` returns ``SUPPORTED``, ``UNSUPPORTED``, or
    ``None`` when it cannot say. It is supplied by the caller and is the only
    thing in this path that can touch a network — this module holds no model
    client, so :func:`check_against_evidence` keeps its promise of costing
    nothing and this stays testable without one.

    A judge that returns ``None``, answers with something unrecognised, or
    raises leaves the deterministic verdict standing. The cascade may improve a
    verdict; it may never erase one, because a failed call must not be able to
    turn a flagged sentence clean.

    ``missing`` is left untouched even when the judge overrides to
    ``SUPPORTED``: those words really are absent from the evidence, and that is
    a measurement rather than a verdict.
    """
    out: List[SentenceVerdict] = []
    for sv in report.sentences:
        if sv.verdict == NO_CONTENT or not (low <= sv.coverage <= high):
            out.append(sv)
            continue
        try:
            got = judge(sv.sentence, evidence)
        except Exception:  # pragma: no cover - a judge must never fail the check
            got = None
        if got not in (SUPPORTED, UNSUPPORTED):
            out.append(sv)
            continue
        out.append(SentenceVerdict(sv.sentence, got, sv.coverage, sv.missing,
                                   sv.content_words, True))
    return AnswerReport(tuple(out))


def separation(matched: Sequence[float], mismatched: Sequence[float]) -> Dict[str, float]:
    """How well coverage tells real pairings from mismatched ones.

    ``auc`` is the probability a randomly chosen matched sentence scores above a
    randomly chosen mismatched one — 0.5 is a coin, 1.0 is perfect. Without this
    the threshold would be a number somebody liked.
    """
    if not matched or not mismatched:
        return {"auc": 0.0, "matched_mean": 0.0, "mismatched_mean": 0.0}
    wins = ties = 0
    for m in matched:
        for x in mismatched:
            if m > x:
                wins += 1
            elif m == x:
                ties += 1
    total = len(matched) * len(mismatched)
    return {
        "auc": (wins + 0.5 * ties) / total,
        "matched_mean": sum(matched) / len(matched),
        "mismatched_mean": sum(mismatched) / len(mismatched),
    }

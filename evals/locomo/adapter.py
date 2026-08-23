"""The LoCoMo memory backend: stage a conversation, compile it, query it.

Modelled directly on :mod:`evals.lme_mab.adapter`, and reusing its pieces
wherever the two benchmarks genuinely share one — ``guard_work_dir``,
``document_title``, ``document_index``, ``evidence_text``. Those are facts about
how THIS repository stages a session document and recovers it from a retrieved
node's ``source_path``, not facts about LongMemEval, and a second spelling of
``session-%04d.md`` that drifts by one zero maps every hit to nothing while
printing a plausible number.

What is NOT shared, and why each one is different here:

* **One project per CONVERSATION, not one per run.** Speaker names repeat
  across LoCoMo's ten conversations, so a pooled corpus lets a question about
  one conversation retrieve another conversation's turns about a different
  person with the same name — undetectable from any reported number. Each
  conversation gets ``<work>/<sample_id>/``.
* **Gold alignment is a dictionary lookup.** LoCoMo names its evidence as
  ``dia_id`` strings, so :mod:`evals.locomo.retrieval` resolves them directly
  and LongMemEval's content-signature machinery has nothing to do here.
* **The document number is the session's own.** ``session_1`` stages as
  ``session-0001.md``, so ``D1:3`` resolves without a table.
* **The evidence cap is the whole session.** See
  :data:`EVIDENCE_SOURCE_CHARS`.

Nothing here reads a wall clock. Every filename and every document body is a
function of the conversation's own bytes, so re-staging is byte-identical.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..lme_mab.adapter import (
    REPO,
    MabHit,
    RefusedToCompileInRepo,
    document_index,
    document_title,
    evidence_text,
    guard_work_dir,
)
# Imported at module level, unlike `rerank`'s constants: `fanout` is pure
# Python over the same modules `hybrid_search` already needs (0.05 s cold), so
# there is nothing here to defer and no reason to duplicate a default that
# could then drift from the library's.
from tesserae.retrieval.fanout import DEFAULT_OVERFETCH, DEFAULT_SOURCE_CAP
from tesserae.retrieval.query_decompose import DEFAULT_UBIQUITY_DF_RATIO
from .dataset import Conversation, LocomoSession, parse_dia_ids

# --------------------------------------------------------------------------
# The published protocol, as constants
# --------------------------------------------------------------------------

#: The judge Protocol B fixes — the ``gpt-4o-mini`` grader that produced the
#: ~66% era numbers and has since been copied verbatim into most memory-benchmark
#: harnesses. **There is no judge running in this repository today**, which is
#: why :func:`protocol_blockers` reports this control UNMET on every run this
#: phase can perform. That refusal is the feature: the deterministic judge in
#: :mod:`evals.locomo.judge` measures something real and is not this.
PROTOCOL_JUDGE = "gpt-4o-mini"

#: The judge temperature Protocol B fixes.
PROTOCOL_JUDGE_TEMPERATURE = 0.0

#: Protocol B grades every question three times and reports the mean and the
#: standard deviation ACROSS whole-run accuracies. It is the one piece of good
#: hygiene in this corner of the field and it is copied deliberately: a
#: generative arm in this repo has swung 0.043 token F1 between two runs of an
#: identical configuration, so a single generative number is not a measurement.
PROTOCOL_JUDGE_RUNS = 3

#: The answerer Protocol B fixes. Same model as the judge.
PROTOCOL_BACKBONE = "gpt-4o-mini"

#: sha256 prefix of the ``locomo10.json`` this harness was written against,
#: measured this phase from the checkout of ``snap-research/locomo`` at
#: ``3eb6f2c``. Declared per run by :func:`evals.locomo.dataset.dataset_revision`
#: and compared here: a benchmark whose answer key changed under it has not
#: measured what its report says it measured.
PROTOCOL_DATASET_REVISION = "sha256:79fa87e90f04"

#: Set when the published protocol fixes no value for a control. The control is
#: still REQUIRED to be declared — "we did not record which embedder retrieved"
#: is not a run anybody can reproduce — but it cannot be compared against a
#: constant, because there is no constant to compare it to. LoCoMo's published
#: protocols let every compared system bring its own retriever and its own
#: evidence budget, and pretending otherwise would invent a control the field
#: never agreed on.
UNFIXED = None


@dataclass(frozen=True)
class Control:
    """One protocol control: what it is, what it must equal, and why it matters."""

    key: str
    required: Optional[str]
    why: str

    @property
    def is_fixed(self) -> bool:
        return self.required is not None


#: The controls every artifact declares. Order is the order the report prints.
PROTOCOL_CONTROLS: Sequence[Control] = (
    Control(
        "llm_model", PROTOCOL_BACKBONE,
        f"the answering backbone must be {PROTOCOL_BACKBONE} — a different "
        f"model measures the model, not the memory, and the published spread "
        f"between two LoCoMo headlines is partly a spread between two backbones",
    ),
    Control(
        "judge", PROTOCOL_JUDGE,
        f"accuracy must be graded by {PROTOCOL_JUDGE} at temperature "
        f"{PROTOCOL_JUDGE_TEMPERATURE:g} — a different judge rescales every "
        f"score, and an independent audit of this benchmark's judges reports a "
        f"false-accept rate larger than the gaps people argue about",
    ),
    Control(
        "judge_runs", str(PROTOCOL_JUDGE_RUNS),
        f"the grade must be the mean of {PROTOCOL_JUDGE_RUNS} independent runs "
        f"with the spread reported — a single generative number is not a "
        f"measurement",
    ),
    Control(
        "dataset_revision", PROTOCOL_DATASET_REVISION,
        "the answer key must be the one this harness was written against — a "
        "changed locomo10.json changes every denominator in the report",
    ),
    Control(
        "embedding_model", UNFIXED,
        "the published protocols let every compared system bring its own "
        "retriever, so there is no value to match — it is declared so the run "
        "is reproducible, and any comparison that rests on it is this "
        "machine's own",
    ),
    Control(
        "evidence_budget", UNFIXED,
        "K is not fixed by the published protocols either — one of them "
        "retrieves 200 memories per question — so it is declared rather than "
        "matched, and every retrieval table here prints its own K",
    ),
)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def protocol_blockers(meta: Mapping[str, Any]) -> List[str]:
    """Reasons this run's numbers may NOT be printed as published-comparable.

    A **missing** declaration blocks on the same terms as a wrong one: "we did
    not record which model answered" is not "the model matched". A control the
    publication does not fix (:data:`UNFIXED`) blocks only when it is missing.

    Declarations are CLAIMS, so evidence is required on top of them — the same
    hole :func:`evals.lme_mab.adapter.protocol_blockers` closed after a
    hand-written answers file asserted every control and unlocked a comparable
    table with no run behind it. ``llm_judge_calls`` is counted separately from
    any other judging: the deterministic judge in :mod:`evals.locomo.judge`
    grades every question and calls no model, so a run that used it declares
    zero here and is blocked, which is correct.
    """
    blockers: List[str] = []
    for control in PROTOCOL_CONTROLS:
        declared = meta.get(control.key)
        if declared in (None, ""):
            blockers.append(f"{control.key}: not declared — {control.why}")
            continue
        if control.is_fixed and str(declared) != control.required:
            blockers.append(
                f"{control.key}: this run used {declared}, the protocol fixes "
                f"{control.required} — {control.why}"
            )

    evidence = meta.get("evidence")
    if not isinstance(evidence, Mapping):
        blockers.append(
            "evidence: absent — every control above is an unverified claim. A "
            "run records what it actually did; a hand-written declaration "
            "cannot, and must not unlock a comparable table"
        )
        return blockers
    if not _as_int(evidence.get("llm_judge_calls")):
        blockers.append(
            f"llm_judge_calls: 0 — nothing graded these answers with "
            f"{PROTOCOL_JUDGE}. The deterministic judge grades without a model "
            f"and is honest about it; declaring a judge model is not judging"
        )
    if not _as_int(evidence.get("answer_calls")):
        blockers.append(
            "answer_calls: 0 — no answers were generated by this run, so there "
            "is nothing for a judge to have graded"
        )
    if not _as_int(evidence.get("canary_calls")):
        blockers.append(
            "canary_calls: 0 — no canary proved the backbone was alive. A dead "
            "provider chain returns None, which becomes \"\", which is_refusal "
            "reads as a refusal: the run then prints refusal_rate 1.000 with "
            "error_rate 0.000, and on the adversarial category that broken "
            "system scores a perfect result"
        )
    return blockers


# --------------------------------------------------------------------------
# Staging
# --------------------------------------------------------------------------


def document_name(session_number: int) -> str:
    """``session-0001.md`` for ``session_1``.

    The session's OWN number, not a zero-based position, so
    :func:`evals.lme_mab.adapter.document_index` inverts a retrieved
    ``source_path`` straight back to the number a ``dia_id`` names. A position
    would put a lookup table between ``D1:3`` and its document, and a lookup
    table is a place for an off-by-one to hide inside a plausible recall score.
    """
    return f"session-{session_number:04d}.md"


def render_session(session: LocomoSession) -> str:
    """The markdown document a compile reads for one session.

    The date is in the BODY and not only in a heading: the lexical and embedding
    lanes score document text, and a date that exists only in a filename is a
    date the temporal category cannot retrieve. 321 of the 1,986 questions are
    temporal.

    Turn rendering is :meth:`evals.locomo.dataset.Turn.render` — the published
    ``<speaker> said, "<text>" and shared <caption>`` form, so this corpus is
    the same text the reference harness embeds, plus the ``dia_id``.
    """
    lines = [f"# {document_title(session.number)}", ""]
    lines += [f"Chat Time: {session.date}" if session.date
              else "Chat Time: not recorded in this conversation.", ""]
    for turn in session.turns:
        lines += [turn.render(), ""]
    return "\n".join(lines).rstrip() + "\n"


#: How much of a document anchor's own session file joins its ANSWERING
#: evidence.
#:
#: 8,000 — the WHOLE session, every time — and it is re-derived here rather than
#: carried over from :data:`evals.lme_mab.adapter.EVIDENCE_SOURCE_CHARS` (2,400,
#: which that module derives from LongMemEval's own mean round length). The
#: arithmetic inverts between the two benchmarks. Measured this phase over all
#: 272 staged documents of ``locomo10.json``, one renders to 3,553 characters on
#: average (median 3,247, p90 5,253, max 7,275, min 1,558) — so ZERO of them
#: exceed ``tesserae.retrieval.hybrid``'s ``SOURCE_LEXICAL_CHARS`` of 8,000.
#:
#: That equality is the point, and it removes a confound rather than adding a
#: knob: the lexical lane ranks a document on the first 8,000 characters of its
#: source, so at this cap the text the backbone reads is exactly the text the
#: retriever scored. A smaller cap would reintroduce the failure LongMemEval's
#: constant exists to bound — a session that ranks first on a term in its
#: opening paragraph, with the answer past the cap, is a perfect retrieval and
#: an unanswerable prompt — and here it would do so for no saving, because
#: there is no session long enough for the cap to bind.
EVIDENCE_SOURCE_CHARS = 8_000

#: How many candidates the lanes are asked for per unit of budget WHEN a
#: cross-encoder is reranking them.
#:
#: A reranker can only reorder what it is handed, so the candidate set is the
#: recall ceiling of the whole stage: at overfetch 1 it can never move a
#: document the lanes ranked 11th into a top-10 budget, and the run measures
#: nothing but reordering noise. 4 is the smallest multiple that lets rank 40
#: reach rank 1, and costs 4x the cross-encoder forward passes — the only cost
#: that scales with it, because the lanes were already scoring the whole corpus.
RERANK_OVERFETCH = 4

#: Tokens per (query, candidate) pair the cross-encoder reads.
#:
#: Duplicated from :data:`tesserae.retrieval.rerank.DEFAULT_MAX_LENGTH` rather
#: than imported, because importing that module pulls in torch and this one is
#: imported by every LoCoMo run including the ones with no reranker. The two
#: are pinned equal by ``test_the_harness_default_matches_the_library_default``
#: so the duplication cannot drift silently.
RERANK_MAX_LENGTH = 512

#: How much session text ONE question's evidence may carry BEYOND what document
#: anchors already bring.
#:
#: :data:`EVIDENCE_SOURCE_CHARS` bounds a single document; this bounds the extra
#: expansions :meth:`LocomoMemory.answer_evidence` gained when it stopped
#: requiring a hit to BE its session before pasting it. It is deliberately a
#: budget on the ADDITION and not a cap on the total, and that is the whole
#: safety argument: every document the anchors-only rule pasted is still pasted,
#: so no question can come out of this change with less evidence than it had.
#:
#: A total cap was measured first and rejected. Spending one 12,000-character
#: budget across anchors and concept hits alike — even with anchors given first
#: claim — moved 24 of the 150 gradeable conv-26 questions INTO gold-session
#: coverage and moved **14 out of it**, because the anchors-only rule had no
#: budget at all and a question whose ten hits were nine anchors used to paste
#: all nine (prompt max 40,826 characters). The aggregate hid the regression:
#: coverage still rose, 53.3% -> 60.0%. Losses only reached zero at a 32,000
#: budget, by which point the prompt was larger than the additive design's. A
#: net gain that silently takes 14 questions backwards is not the change worth
#: shipping when a strictly additive one is available for the same prompt.
#:
#: 8,000 — measured, over one frozen retrieval of all 199 conv-26 questions of
#: the 2026-08-21 run. It buys 1.76 more expanded sessions per question (2.68 ->
#: 4.44, and the minimum rises from 0 to 1, so no prompt is summaries alone),
#: takes gold-session coverage on the 150 gradeable questions from 53.3% to
#: 78.0% — 37 questions gained the gold session's text and 0 lost it — and on
#: the 30 refusals specifically from 5/30 to 15/30. The cost is the prompt:
#: mean 14,143 -> 20,798 over
#: all 199, and on the adversarial category — the one a refusal fix most
#: endangers — 15,277 -> 21,697. THAT COST IS NOT ESTIMATED HERE. Abstention on
#: adversarial questions is measured beside accuracy on every run, and the
#: decision rule is fixed before the run rather than after it: accuracy must rise
#: by more than adversarial abstention falls.
#:
#: The next rung, 12,000, reaches 85.3% coverage for a 24,850-character
#: adversarial prompt. It is the obvious follow-up if this budget's abstention
#: holds, and the obvious thing not to have shipped if it does not.
EVIDENCE_EXTRA_SOURCE_CHARS = 8_000

# --------------------------------------------------------------------------
# The tiered evidence budget — heads, then receipts, then sessions
#
# Opt-in through ``LocomoMemory(tiered_evidence=True)`` / ``--tiered-evidence``.
# Absent that, none of the constants below is read and the answering path is
# :meth:`LocomoMemory._answer_evidence_sessions`, which is the shipped body
# moved verbatim.
#
# WHY a second tier exists at all: measured pooled over the ten conversations,
# 87.3% of the shipped prompt is pasted session text and 37% of each fact head
# is an absolute filesystem path the backbone cannot open. The receipt tier
# spends 477 characters buying the exact transcript turns the retrieved facts
# were extracted from, at +0.096 multi-hop turn coverage per 1,000 characters
# against the session tier's +0.014 at the same point.
# --------------------------------------------------------------------------

#: How much RECEIPT TURN text one question's evidence may carry, beyond the
#: fact heads and before the session tier spends anything.
#:
#: A BOUND, not a target, and it does not bind on this corpus. Measured spend at
#: :data:`RECEIPT_WINDOW` 0 is 477 characters per prompt (2.7 lines of ~175),
#: because tier 3 is selected FIRST and a receipt whose whole session is about
#: to be pasted is never emitted. 6,000 and 8,000 produce byte-identical output,
#: so the tier saturates well below this default. Set to the same magnitude as
#: :data:`EVIDENCE_EXTRA_SOURCE_CHARS` so the two budgets read side by side.
#:
#: 0 is the kill control, and it is exact: with no receipt budget the tiered
#: path reproduces the shipped multi-hop 0.465 / overall 0.790 to three
#: decimals, which is what proves every point of the tiered gain is the receipt
#: tier and not the path strip that pays for it.
EVIDENCE_RECEIPT_CHARS = 8_000

#: How many resolved turns ONE fact may spend the receipt budget on.
#:
#: MEASURED INERT: 1, 2, 4 and unbounded give turn coverage identical to three
#: decimals, twice, in two independent measurements — most facts have exactly
#: one resolvable witness. It ships as a BOUND against a future compile that
#: hangs twenty spans off a single Claim and lets that one fact eat the tier.
#: **It is not a tuning dial; do not sweep it** — there is nothing on the other
#: side of it on this corpus, and a sweep would report noise as a finding.
RECEIPT_TURNS_PER_FACT = 2

#: Turns either side of a receipt turn emitted with it, in FILE ORDER.
#:
#: 0 — off — because it is unmeasured IN THIS FRAME and the two measurements
#: that exist disagree for a mechanical reason rather than a substantive one.
#: Gold turns cluster beside span turns (a span's own turn reaches 0.602 of the
#: gold turns, +-1 reaches 0.715, +-2 reaches 0.826) and a design with NO
#: session paste measured +0.139 overall coverage from the window; a design
#: whose fill pasted whole sessions measured it bit-for-bit INERT, because the
#: windowed turns were already in the prompt. Tier 3 here pastes 4.44 of ~19
#: sessions, so the window is live only for the ~15 it does not — a condition
#: neither measurement covers. Off until one does.
#:
#: FILE ORDER, never arithmetic on the turn number: ``D1:7`` need not be
#: followed by ``D1:8``, and a gap in the numbering would silently shift the
#: window onto turns that are not adjacent to anything.
RECEIPT_WINDOW = 0

#: The order tier 2 SPENDS its budget in. Never who is admitted to it.
#:
#: Measured provenance by node type over n=5,596 retrieved nodes: Claim 0.95,
#: EvidenceSpan 0.96, SessionInsight 0.93, Event 0.63, Project 0.38, Concept
#: 0.32, SourceDocument 0.00 — and SourceDocument alone is 28% of everything
#: retrieved. Walking in this order means a SourceDocument hit never consumes
#: the budget ahead of a Claim that has a receipt to redeem.
#:
#: CURRENTLY INERT, and this says so rather than claiming a gain: spend is 477
#: of 8,000 characters, so the budget never binds and every eligible receipt is
#: emitted whatever the order. It exists for the :data:`RECEIPT_WINDOW`
#: settings, which do make the budget bind, and for a compile that emits more
#: spans per fact than this one. Types not named here follow in rank order —
#: which is where ``SourceDocument`` and ``Session``, the two that carry no
#: ``evidenced_by`` edge at all, land.
RECEIPT_YIELD_ORDER: Tuple[str, ...] = (
    "EvidenceSpan", "Claim", "SessionInsight", "SessionTakeaway",
    "SessionDecision", "ContributionClaim", "SessionTODO", "Task",
    "CausalClaim", "Event",
)

# --------------------------------------------------------------------------
# Turnpack — the session stays the RETRIEVAL unit, the turn becomes the
# READING unit
#
# Opt-in through ``LocomoMemory(evidence_unit="turn")`` / ``--evidence-unit
# turn``. Absent that, none of the constants below is read and the answering
# path is :meth:`LocomoMemory._answer_evidence_sessions`, the shipped body,
# unchanged.
#
# WHY a third unit exists. Measured over all 199 conv-26 questions of the
# 2026-08-21 run under the shipped fan-out arm (``--fanout --source-cap 1
# --prefer-anchor-text``), the prompt is 43,413 characters of which 40,875
# (94.2%) is whole staged session files: 9.78 of 19 sessions, 52.5% of the
# entire conversation, pasted into every single question. The gold-evidence
# turns those questions actually turn on are 1.35 turns / 318 characters.
# Signal density 0.73%.
#
# RETRIEVAL IS UNTOUCHED. ``query_hits`` still runs, still names the same
# sessions, and ``documents_of`` still scores exactly those. This is a READING
# change over a frozen ranking, which is what makes it measurable against the
# shipped arm without a second checkout — and it is also why ALL-gold-doc@10
# becomes a pure control here rather than a description of the prompt. A report
# that quotes document recall as though it described what the backbone read
# would now be wrong in a new way.
# --------------------------------------------------------------------------

#: The ONE total budget a turn-unit prompt may spend, session headers included.
#:
#: 28,000 — a point on a measured frontier, not a round number, and the target
#: this design was commissioned against (~7,000 Qwen3 tokens at the corpus's
#: measured 3.95 chars/token for dialogue, against ~10,900 today).
#: Gold-evidence-TURN-in-prompt over the 150 conv-26 questions with parseable
#: gold turns, the only proxy this branch licenses:
#:
#:   whole sessions, 9.78 of them (the shipped fan-out arm)   42,951   0.941
#:   whole sessions, top 6 only (the budget-matched control)  27,149   0.858
#:   turn units at this budget, pool = retrieved sessions     27,918   0.928
#:   turn units at 24,000,      pool = retrieved sessions     23,979   0.923
#:   turn units at 20,000,      pool = retrieved sessions     19,980   0.900
#:
#: The CONTROL row is the load-bearing one, and it is what stops this being
#: "the gain was the budget cut". At the same ~27,100 characters and the same
#: document count, truncating to whole sessions gives 0.858 where packing turns
#: gives 0.928; that +0.070 is the packing. 107 of the 150 questions sit at
#: their coverage ceiling below 20,000 characters — they pay 43,413 today to
#: buy nothing — and multi-hop is the only category that genuinely consumes the
#: budget (temporal and single-hop are at ceiling from 20,000 up).
#:
#: This budget REPLACES :data:`EVIDENCE_SOURCE_CHARS` (a per-document cap) and
#: :data:`EVIDENCE_EXTRA_SOURCE_CHARS` (an extra-source pool) ON THE TURN PATH
#: ONLY. Both stay exactly as they are on the session path, which is what keeps
#: the shipped arm byte-identical.
#:
#: A FIXED cap, and that is a concession rather than a preference: two adaptive
#: rules were measured and neither shipped. Score-threshold stopping is a LOSS
#: (tau=0.45 reaches mean 9,775 characters but multi-hop coverage collapses
#: 0.867 -> 0.651 — the score distribution is not calibrated to "do I have the
#: answer yet"). Demand-proportional budgeting reaches mean 20,767 at 0.917
#: coverage, a real cheaper operating point, but it does not discriminate by
#: category, because demand is 6.3-7.3 everywhere. Nothing here is adaptive and
#: no writeup may say it is.
EVIDENCE_PACK_CHARS = 28_000

#: Turns either side of a candidate turn that are VISIBLE WHILE IT IS SCORED.
#:
#: 1 — worth +0.023 turn coverage at a fixed 28,000 budget (0.878 -> 0.901),
#: which is EXIT's "+1.2 EM for judging a sentence with its passage as context"
#: reproduced on this corpus. A bare ``[D<n>:<t>]`` line is one utterance with
#: its referents in the lines around it; scoring it alone throws them away.
#:
#: FILE ORDER, never arithmetic on the turn number: ``D1:7`` need not be
#: followed by ``D1:8``, and a gap in the numbering would silently score a turn
#: against neighbours it does not have.
TURN_SCORE_WINDOW = 1

#: Turns either side of an ADMITTED turn that are EMITTED with it.
#:
#: 0 — off — and this is the one non-obvious knob in the design, measured in
#: BOTH directions rather than assumed. Emitting the +/-1 neighbourhood COSTS
#: 0.015 coverage at a fixed budget (0.923 -> 0.908), because the neighbours eat
#: slots that scoring would otherwise have given to turns that earned them.
#: Score WITH context, emit WITHOUT it. Raising this buys connective tissue at
#: a measured price in coverage; it is a legibility decision, not a quality one.
#:
#: Not to be confused with :data:`RECEIPT_WINDOW`, which is off for an entirely
#: different reason (it is unmeasured in its frame; this one is measured and
#: negative).
TURN_EMIT_WINDOW = 0

#: What the three FREE scoring lanes contribute, each z-scored before summing.
#:
#: Measured additively at a fixed 28,000 budget: contextual BM25 is essentially
#: the whole effect, model2vec cosine on top is +0.006 turn coverage, and
#: session rank on top of that is ~0. Dense and rank ship at 0.5 rather than 0
#: because they cost nothing — all three lanes already exist in
#: ``tesserae.retrieval.hybrid``, nothing new is imported and no model is
#: downloaded — and because a lane worth +0.006 on ONE conversation is not a
#: lane this branch has the power to call worthless.
#:
#: Z-SCORING IS WHAT MAKES THE SUM MEAN ANYTHING. BM25 is unbounded and cosine
#: is [-1, 1]; a raw weighted sum would be BM25 plus rounding error whatever the
#: weights said. A lane whose scores are all equal contributes exactly zero
#: rather than dividing by zero.
#:
#: ``dense`` at exactly 0.0 skips the embedding backend entirely rather than
#: multiplying its output by zero, so a BM25-only arm loads no model and needs
#: no network.
TURN_WEIGHTS: Mapping[str, float] = MappingProxyType({
    "bm25_ctx": 1.0,
    "dense": 0.5,
    "rank": 0.5,
})

#: The lanes :data:`TURN_WEIGHTS` may name. An unknown key is an error rather
#: than a silently ignored typo: a sweep that misspells ``bm25_ctx`` would
#: otherwise report a lexical-lane result with the lexical lane switched off.
TURN_LANES: Tuple[str, ...] = ("bm25_ctx", "dense", "rank")

#: How many sessions the SESSION STAGE is asked for when widening the candidate
#: pool, independently of how many the answer-time budget retrieved.
#:
#: 0 — OFF, and off is byte-identical to every turn-unit run before this: the
#: pool is exactly the ~9.9 documents the answer-time hits named. It is not a
#: default because the arm that measured the win pooled every staged session
#: through the ``turn_pool="corpus"`` glob, and a glob is a measurement stand-in,
#: not an implementation — on a 19-32-session conversation it is
#: indistinguishable from reading the whole corpus, which would make a coverage
#: number a retrieval bypass wearing a packing hat. Set this to a real widened
#: ``k`` (22 reproduces the measured pool's ~22.4 distinct sessions on this
#: corpus) and the same widening transfers to a corpus where the pool cannot be
#: everything.
#:
#: WHY WIDEN AT ALL. Instrumented over all ten conversations, 2,360 gold
#: evidence turns (cats 1-4), every MISSING gold turn split into "never entered
#: the candidate pool" against "was in the pool and the admission rule refused
#: it":
#:
#:     category      n     in pool   in prompt   lost:pool   lost:pack
#:     multi-hop    882     0.738      0.717       0.262       0.022
#:     temporal     375     0.968      0.968       0.032       0.000
#:     open-domain  208     0.649      0.615       0.351       0.034
#:     single-hop   895     0.966      0.965       0.034       0.001
#:     ALL         2360     0.853      0.842       0.147       0.011
#:
#: Only 1.1% of gold turns are losable by the ADMISSION RULE; 14.7% never become
#: candidates. That is why this branch widens the pool rather than sharpening
#: the objective — and the published budgeted-submodular packer was implemented
#: and measured on this corpus before that conclusion was drawn: pooled over
#: 1,536 questions it is NEUTRAL at 28,000 characters (0.909 against 0.909),
#: NEGATIVE at 24,000 and 20,000, and collapses at 8,000 (0.560). Do not ship a
#: packer here.
TURN_POOL_K = 0

#: The most SESSION BLOCKS a turn pack may open. 0 — OFF, uncapped, which is
#: byte-identical to every turn-unit run before this.
#:
#: THE CAP IS NOT A SAFETY VALVE, it is half the mechanism. Pooled, n=1,536,
#: 28,000 characters in every cell:
#:
#:     arm            chars   turns  sess   cover  allgold   multi  temporal  open-dom  single
#:     narrow (ships) 27,949  167.3   9.9   0.909   0.850    0.739   0.971     0.681    0.968
#:     wide/cap8      26,987  165.0   8.0   0.848   0.785    0.622   0.886     0.638    0.933
#:     wide/cap10     27,979  167.5  10.0   0.906   0.849    0.716   0.952     0.668    0.977
#:     wide/cap12     27,985  164.7  12.0   0.919   0.864    0.764   0.965     0.682    0.980
#:     wide/cap16     27,987  161.2  16.0   0.930   0.877    0.803   0.971     0.695    0.983
#:     wide/cap99     27,987  158.2  22.4   0.928   0.876    0.806   0.971     0.706    0.976
#:
#: cap16 beats the UNCAPPED wide pool with 6.4 fewer blocks, because a session
#: opened for one turn pays its header and crowds out turns; and cap8 is worse
#: than narrow on every axis, so the curve has a real interior optimum rather
#: than "more sessions is better".
#:
#: 16 IS THE KNEE OF A FIVE-POINT CURVE COMPUTED ON THE BENCHMARK, which is
#: selection on the test set. Every point >= 12 beats narrow and the direction
#: holds in 9 of 10 conversations, so the DIRECTION is robust; the exact 16 is
#: not, and no writeup may present it as a tuned optimum. The fallback if graded
#: multi-hop falls to Levy's distractor penalty (Findings of EMNLP 2025: 5-20%
#: F1 lost on multi-hop when document count rises at fixed length, and 68% of
#: this gain IS multi-hop) is cap12 — +0.010 coverage at 2.1 more documents than
#: today.
TURN_SESSION_CAP = 0


#: The edge that carries a receipt, and the ONLY one walked.
#:
#: Of 1,050 first-resolvable receipts at one hop, 1,043 are ``fact
#: -evidenced_by-> EvidenceSpan``. Walking two hops would raise apparent
#: reachability to 0.992, and it would be false: 202 of 286 two-hop paths route
#: through a ``Person`` hub, land in the fact's own session 30.1% of the time
#: against 99.0% at one hop, and score lexical containment 0.175 against 0.509.
#: Two of five sampled two-hop receipts recovered a turn about a different
#: subject entirely. The honest number is the one-hop number.
_EVIDENCED_BY = "evidenced_by"

#: The node type whose whole job is to point at a turn — the denominator of
#: :attr:`LocomoMemory.unresolvable_spans`.
_EVIDENCE_SPAN_TYPE = "EvidenceSpan"

#: A metadata VALUE that is a turn locator and nothing else.
#:
#: Matched by value shape against EVERY metadata value, never by key name,
#: because the extraction wrote the turn id under 14 different keys across the
#: ten compiled graphs: ``turn`` 1,352, ``message_id`` 96, ``ref`` 42,
#: ``locator`` 31, ``utterance`` 13, ``line`` 9, ``utterance_id`` 9, ``id`` 7,
#: ``turn_id`` 7, ``messageId`` 7, ``span_id`` 6, ``line_ref`` 6,
#: ``message_ref`` 6, ``quote_id`` 3. Reading ``metadata["turn"]`` alone
#: resolves 1,352 of 1,668 spans (81.1%) and silently reports the other 242 as
#: having no receipt; the shape test resolves 95.6%. Zero spans carry two
#: DISTINCT turn ids, so the shape test is unambiguous rather than a guess.
#:
#: Compound values — ``D5:6,D5:8``, ``D17:24-25``, ``D6:11;D6:13;D6:15``,
#: ``D15:14/D15:16``, five delimiter spellings across 46 of 1,580 values — match
#: here and are then split by :func:`evals.locomo.dataset.parse_dia_ids`, the
#: ANSWER KEY's own parser. A second parser here would be both duplication and a
#: place for a 7% silent loss to hide. ``D17:24-25`` yields ``D17:24`` only:
#: a documented one-turn loss, inherited from the answer key's parser and not
#: repaired here, because repairing it would mean guessing.
_TURN_LOCATOR = re.compile(r"^\s*D\d+:\d+(?:\s*[-,;/|]\s*(?:D\d+:\d+|\d+))*\s*$")

#: The marker :meth:`evals.locomo.dataset.Turn.render` prefixes to every staged
#: line. Receipt recovery depends ENTIRELY on it: anything that changes the
#: staged rendering zeroes the receipts and the coverage instrument at the same
#: time and in silence, which is what :attr:`LocomoMemory.unresolvable_spans`
#: and :attr:`LocomoMemory.dangling_receipts` exist to make visible.
_STAGED_TURN = re.compile(r"^\[(D\d+:\d+)\] ", re.MULTILINE)

#: Above this fraction of unresolvable spans, the adapter says so on stderr.
#: A compile that stopped writing turn locators would otherwise present as a
#: quiet coverage regression with no cause attached to it.
_UNRESOLVABLE_WARN_RATIO = 0.10


def _claimed_turn_ids(node: Any) -> List[str]:
    """Every ``D<n>:<t>`` this node's metadata claims, in metadata order.

    Matched by VALUE SHAPE over every metadata value — see
    :data:`_TURN_LOCATOR` for the 14 key names that made key-based reading wrong
    — and split by the answer key's own :func:`~evals.locomo.dataset.parse_dia_ids`.

    A claim is not a receipt. :meth:`LocomoMemory._receipt_index` still requires
    the named turn to be a line in the node's own staged document before it
    counts; reachability alone is not provenance.
    """
    metadata = getattr(node, "metadata", None)
    if not isinstance(metadata, Mapping):
        return []
    ids: List[str] = []
    for value in metadata.values():
        text = str(value or "")
        if not text or not _TURN_LOCATOR.match(text):
            continue
        for session, turn in parse_dia_ids(text):
            dia = f"D{session}:{turn}"
            if dia not in ids:
                ids.append(dia)
    return ids


#: ``Chat Time:`` as :func:`render_session` writes it, read back off a staged
#: document. Bounded to the document's head because a turn is free to contain
#: the words "Chat Time:" and the header is line 3.
_CHAT_TIME = re.compile(r"^Chat Time:[ \t]*(.+)$", re.MULTILINE)
_CHAT_TIME_HEAD_CHARS = 400
#: A leading clock reading and whatever joins it to the date — ``1:56 pm on``,
#: ``2:35 pm,``. Both spellings occur in ``locomo10.json``.
_CLOCK = re.compile(r"^\d{1,2}:\d{2}\s*(?:am|pm)?\s*(?:on\b|,)?\s*", re.I)
_YEAR_COMMA = re.compile(r",\s*((?:19|20)\d{2})\b")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")


def session_date(document: str) -> str:
    """The calendar date a staged session states about itself, or ``""``.

    ``"1:56 pm on 8 May, 2023"`` -> ``"8 May 2023"``. The clock reading is
    dropped because no LoCoMo gold answer is a time of day, and the comma before
    the year is dropped because the golds are written ``"8 May 2023"``.

    **A year is required.** :func:`render_session` writes ``Chat Time: not
    recorded in this conversation.`` for a session the file did not date, and
    stamping an evidence item with that sentence would put a confident-looking
    non-date next to a claim. No date is the honest rendering of no date.

    This reads the STAGED DOCUMENT and not node metadata on purpose. The
    extractor's dating is at the model's discretion — the compiled conv-26 graph
    carries nine distinct date-ish keys in two incompatible formats across 27 of
    its 345 nodes, and 218 nodes carry none — whereas the header is written by
    :func:`render_session` from ``session_<n>_date_time`` and is present on every
    document this adapter stages.
    """
    match = _CHAT_TIME.search((document or "")[:_CHAT_TIME_HEAD_CHARS])
    if not match:
        return ""
    text = _YEAR_COMMA.sub(r" \1", _CLOCK.sub("", match.group(1).strip()).strip())
    return text if _YEAR.search(text) else ""


def _zscore(values: Sequence[float]) -> List[float]:
    """``values`` centred and scaled to unit variance, or all zeros.

    The turn packer sums an unbounded BM25 score, a cosine in [-1, 1] and a
    negated integer rank. A raw weighted sum of those three is BM25 plus
    rounding error whatever the weights say, so each lane is standardised
    first and the weights then mean what they read as.

    A lane whose values are all equal — an empty query against BM25, one
    candidate, every candidate from one session — contributes exactly 0.0
    rather than dividing by zero. Population variance, not sample: the
    candidates ARE the population here, and the n-1 correction would make a
    two-candidate question score differently from a two-hundred-candidate one
    for no reason a reader could name.
    """
    count = len(values)
    if count < 2:
        return [0.0] * count
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / count
    if variance <= 0.0:
        return [0.0] * count
    deviation = math.sqrt(variance)
    return [(value - mean) / deviation for value in values]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity, in pure Python and without numpy.

    ``tesserae.retrieval.hybrid`` reaches for numpy when it is installed
    because it scores a whole corpus per query; this scores ~419 short turns,
    where the import guard costs more than the arithmetic saves. Keeping it
    stdlib is also what lets the turn tests run on an install with neither
    numpy nor torch.

    A zero-length, ragged or zero-norm pair scores 0.0 — the honest reading of
    "these two are not comparable" — rather than raising inside a scoring loop.
    """
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(x * y for x, y in zip(left, right))
    left_norm = math.sqrt(sum(x * x for x in left))
    right_norm = math.sqrt(sum(y * y for y in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _packed_turn_text(raw: str) -> str:
    """One turn as the pack emits it: trailing blank lines removed.

    :meth:`LocomoMemory._turn_lines` reads a turn as every line from its header
    up to the next one, which is what makes a turn with embedded newlines
    arrive whole — and which also absorbs the blank separator
    :func:`render_session` writes after each turn. Every turn but a document's
    last therefore ends in a newline.

    The session units never noticed: they paste whole files, where that blank
    is the separator it was written as. The pack DOES notice twice over — the
    blank is charged to :data:`EVIDENCE_PACK_CHARS`, and it doubles the newline
    between two turns the pack chose to put side by side. Normalised here, at
    the one place the pack reads a turn, so the budget arithmetic and the
    rendered bytes cannot disagree about what a turn is.
    """
    return (raw or "").rstrip()


def _pack_sort_key(source_path: str) -> Tuple[int, int, str]:
    """Session NUMBER order, with anything unnumbered last and stable.

    ``document_index`` is the session's own number, so this is chronological on
    this corpus. A path that maps to no staged document sorts after every one
    that does, by its own string, so the render order is total and never
    depends on dict or set iteration.
    """
    index = document_index(source_path)
    return (1, 0, source_path) if index is None else (0, index, "")


@dataclass
class IngestResult:
    """What one conversation's ingest put on disk, and what it cost in units."""

    conversation: str
    work: Path
    corpus_dir: Path
    documents: int
    turns: int
    chars: int
    dated_sessions: int
    captioned_turns: int
    compiled: bool
    reused: bool = False

    @property
    def approx_tokens(self) -> int:
        """Chars/4 — deliberately crude, and only ever printed as an estimate."""
        return self.chars // 4


def _graph_missing_sessions(graph_path: Path, corpus: Path) -> set:
    """Staged documents the compiled graph does not index, by basename."""
    import json as _json

    try:
        payload = _json.loads(graph_path.read_bytes())
    except (OSError, ValueError) as exc:
        # An UNREADABLE graph is not an empty one. Returning set() here made the
        # caller read "no missing documents" — i.e. "the graph indexes every
        # staged document" — from a file it could not parse. Measured: a
        # truncated graph.json ('{"nodes": [{"source_path": "corp') was ACCEPTED
        # by --reuse-compile, and only the well-formed '{}' case that the tests
        # exercise was refused.
        #
        # This is the identical defect fixed in evals/lme_mab/adapter.py, and it
        # was reproduced here by copying the shape without the reasoning. Refuse
        # loudly: a graph that cannot be read cannot be reused.
        raise ValueError(
            f"--reuse-compile: {graph_path} could not be parsed "
            f"({type(exc).__name__}: {exc}). A graph that cannot be read cannot "
            f"be verified against the staged corpus; recompile."
        ) from exc
    indexed = set()
    for node in payload.get("nodes") or []:
        source = node.get("source_path") if isinstance(node, dict) else None
        if source:
            indexed.add(Path(str(source)).name)
    return {p.name for p in corpus.glob("*.md")} - indexed


def _verify_staged(corpus: Path, sessions: Sequence[LocomoSession]) -> tuple:
    """``(turns, chars)``, having proved this conversation is already staged there.

    Raises unless every session renders byte for byte to the file already on
    disk, and unless the directory holds nothing else. A CHANGED document means
    the compiled graph was built from text this run would not stage; an EXTRA
    one means the graph indexes a session this conversation does not contain —
    retrievable evidence from a conversation the questions were never asked
    about. Either way the reused graph is not this conversation's graph.
    """
    if not corpus.is_dir():
        raise FileNotFoundError(
            f"--reuse-compile: no staged corpus at {corpus}. There is nothing "
            f"to reuse; run without the flag to stage and compile."
        )
    turns = chars = 0
    mismatched: List[str] = []
    for session in sessions:
        body = render_session(session)
        staged = corpus / document_name(session.number)
        if not staged.is_file():
            mismatched.append(f"{document_name(session.number)} (missing)")
        elif staged.read_bytes() != body.encode("utf-8"):
            mismatched.append(f"{document_name(session.number)} (differs)")
        turns += len(session.turns)
        chars += len(body)
    expected = {document_name(s.number) for s in sessions}
    extra = sorted(p.name for p in corpus.glob("*.md") if p.name not in expected)
    if mismatched or extra:
        raise ValueError(
            f"--reuse-compile: {corpus} is not this conversation's corpus — "
            f"{len(mismatched)} document(s) missing or changed"
            f"{': ' + ', '.join(mismatched[:5]) if mismatched else ''}"
            f"{f'; {len(extra)} unexpected: ' + ', '.join(extra[:5]) if extra else ''}. "
            f"The compiled graph there answers about a different conversation. "
            f"Re-run without --reuse-compile to rebuild it."
        )
    return turns, chars


def _default_compile(work: Path) -> None:
    """``tesserae init`` then ``tesserae compile``, in ``work``.

    The checkout's own venv when there is one, else the running interpreter —
    the resolution ``evals/growth/run.py`` settled on after hardcoding the first
    form killed every run inside a git worktree.
    """
    venv = REPO / ".venv" / "bin" / "python"
    python = str(venv if venv.is_file() else sys.executable)
    subprocess.run(
        [python, "-m", "tesserae", "init", "--yes", "--source", "./corpus"],
        cwd=work, check=True, capture_output=True,
    )
    result = subprocess.run(
        [python, "-m", "tesserae", "compile"],
        cwd=work, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"compile failed in {work}:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
        )


class LocomoMemory:
    """A memory system under test: stage one conversation, retrieve from it.

    ``compile_fn`` and ``search_fn`` are injection points and not decoration.
    Every test in this package passes stubs for both, because the real pair is
    an hours-long LLM extraction and a metered embedding call, and a harness
    whose wiring can only be checked by running the benchmark does not get
    checked.

    One instance per CONVERSATION. :meth:`ingest` resolves
    ``<work>/<sample_id>/`` and compiles there, so ten conversations are ten
    isolated graphs and no question can retrieve a conversation it was not
    asked about.
    """

    def __init__(
        self,
        *,
        compile_fn: Optional[Callable[[Path], None]] = None,
        search_fn: Optional[Callable[..., Any]] = None,
        backend: Any = None,
        embedding_prefer: str = "model2vec",
        mode: str = "hybrid",
        weights: Optional[Dict[str, float]] = None,
        reranker: Any = None,
        rerank_overfetch: int = RERANK_OVERFETCH,
        fanout: bool = False,
        fanout_overfetch: int = DEFAULT_OVERFETCH,
        source_cap: Optional[int] = DEFAULT_SOURCE_CAP,
        ubiquity_df_ratio: float = DEFAULT_UBIQUITY_DF_RATIO,
        extra_facets: int = 0,
        prefer_anchor_text: bool = False,
        tiered_evidence: bool = False,
        evidence_receipt_chars: int = EVIDENCE_RECEIPT_CHARS,
        receipt_window: int = RECEIPT_WINDOW,
        evidence_unit: str = "session",
        evidence_pack_chars: int = EVIDENCE_PACK_CHARS,
        turn_pool: str = "retrieved",
        turn_pool_k: int = TURN_POOL_K,
        turn_session_cap: int = TURN_SESSION_CAP,
        turn_score_window: int = TURN_SCORE_WINDOW,
        turn_emit_window: int = TURN_EMIT_WINDOW,
        turn_heads: str = "none",
        turn_weights: Optional[Mapping[str, float]] = None,
    ) -> None:
        self._compile_fn = compile_fn or _default_compile
        self._search_fn = search_fn
        self._backend = backend
        self._embedding_prefer = embedding_prefer
        self._mode = mode
        self._weights = weights
        #: Optional cross-encoder. ``None`` is the shipped path and returns the
        #: fused ranking untouched — not a degraded version of it.
        self._reranker = reranker
        if rerank_overfetch < 1:
            raise ValueError("rerank_overfetch must be >= 1")
        self._rerank_overfetch = rerank_overfetch
        #: Query fan-out with a document-disjoint merge. ``False`` is the
        #: shipped path and asks the lanes for exactly the budget, as before.
        self._fanout = fanout
        if fanout_overfetch < 1:
            raise ValueError("fanout_overfetch must be >= 1")
        self._fanout_overfetch = fanout_overfetch
        #: 1 HERE and ``None`` in the library, deliberately. This adapter's
        #: corpus is one node-set per session document, which is the shape one
        #: hit per document is correct for; a graph where thousands of nodes
        #: share one path is the shape it is wrong for, so the library refuses
        #: to choose on a caller's behalf.
        self._source_cap = source_cap
        self._ubiquity_df_ratio = ubiquity_df_ratio
        self._extra_facets = extra_facets
        self._prefer_anchor_text = prefer_anchor_text
        #: Spend the evidence budget cheapest-per-unit-of-coverage first: fact
        #: heads, then the transcript turns their ``evidenced_by`` edges point
        #: at, then today's session paste unchanged. ``False`` is the shipped
        #: path and reaches :meth:`_answer_evidence_sessions`, which is today's
        #: body verbatim — see :meth:`answer_evidence`.
        self._tiered_evidence = tiered_evidence
        if evidence_receipt_chars < 0:
            raise ValueError("evidence_receipt_chars must be >= 0")
        self._evidence_receipt_chars = evidence_receipt_chars
        if receipt_window < 0:
            raise ValueError("receipt_window must be >= 0")
        self._receipt_window = receipt_window
        #: What ONE evidence item is. ``"session"`` is the shipped path and
        #: reaches :meth:`_answer_evidence_sessions` / :meth:`
        #: _answer_evidence_tiered` exactly as before; ``"turn"`` reaches
        #: :meth:`_answer_evidence_turns`, which keeps the SESSION as the
        #: retrieval unit and makes the TURN the reading unit. See
        #: :data:`EVIDENCE_PACK_CHARS`.
        if evidence_unit not in ("session", "turn"):
            raise ValueError(
                f"evidence_unit must be 'session' or 'turn', not "
                f"{evidence_unit!r}"
            )
        self._evidence_unit = evidence_unit
        # Refused rather than ordered, and for the same reason `--tiered-
        # evidence` refuses `--prefer-anchor-text`: tiering's tier 2 IS a
        # degenerate Turnpack (receipt turns, then whole sessions), so composing
        # them would produce a prompt neither design's numbers describe and a
        # meta block that named two budgets only one of which bound.
        if evidence_unit == "turn" and tiered_evidence:
            raise ValueError(
                "evidence_unit='turn' and tiered_evidence=True are mutually "
                "exclusive: tiering's receipt tier is a degenerate special case "
                "of the turn pack, and running both would spend two budgets "
                "over one prompt"
            )
        if evidence_pack_chars < 0:
            raise ValueError("evidence_pack_chars must be >= 0")
        self._evidence_pack_chars = evidence_pack_chars
        #: Which sessions' turns may be scored. ``"retrieved"`` is the default
        #: and holds the pack to the SAME distinct-session count the shipped arm
        #: has, which is what makes it Levy-safe by construction; ``"corpus"``
        #: scores every staged session and reaches +0.014 coverage at 17.3
        #: distinct sessions instead of 9.8. See :meth:`_turn_pool_paths`.
        if turn_pool not in ("retrieved", "corpus"):
            raise ValueError(
                f"turn_pool must be 'retrieved' or 'corpus', not {turn_pool!r}"
            )
        self._turn_pool = turn_pool
        #: Retrieve-wide. 0 is today: the pool is exactly the documents the
        #: answer-time hits named. Above 0, the session stage is asked a SECOND,
        #: free time at this k and its documents join the pool — a retrieval
        #: result with a stated k, never a filesystem glob. See
        #: :data:`TURN_POOL_K`.
        if turn_pool_k < 0:
            raise ValueError("turn_pool_k must be >= 0")
        self._turn_pool_k = turn_pool_k
        #: Pack-narrow. 0 is today: uncapped. Above 0, admission refuses to open
        #: the (cap+1)-th session block, receipts included. See
        #: :data:`TURN_SESSION_CAP`.
        if turn_session_cap < 0:
            raise ValueError("turn_session_cap must be >= 0")
        self._turn_session_cap = turn_session_cap
        if turn_score_window < 0:
            raise ValueError("turn_score_window must be >= 0")
        self._turn_score_window = turn_score_window
        if turn_emit_window < 0:
            raise ValueError("turn_emit_window must be >= 0")
        self._turn_emit_window = turn_emit_window
        if turn_heads not in ("none", "fact"):
            raise ValueError(
                f"turn_heads must be 'none' or 'fact', not {turn_heads!r}"
            )
        self._turn_heads = turn_heads
        weights = dict(TURN_WEIGHTS if turn_weights is None else turn_weights)
        unknown = sorted(set(weights) - set(TURN_LANES))
        if unknown:
            # Loud, because the failure is invisible: a sweep that misspells a
            # lane name would otherwise report a three-lane result with two
            # lanes running and never say so.
            raise ValueError(
                f"turn_weights names lanes that do not exist: {unknown}. "
                f"Known lanes: {list(TURN_LANES)}"
            )
        # A COMPLETE mapping, never a patch: a lane the caller did not name is
        # 0.0, not its default weight. `{"bm25_ctx": 1.0}` therefore means
        # "lexical only" — which is what a no-model arm asks for — rather than
        # "the defaults, with bm25 restated".
        self._turn_weights = {lane: float(weights.get(lane, 0.0))
                              for lane in TURN_LANES}
        if evidence_unit == "turn" and not any(self._turn_weights.values()):
            # Every lane off is not a control, it is a pack chosen by tie-break
            # alone — the first turns of the lowest-numbered session, whatever
            # the question said. That is the same failure the empty-question
            # guard in `answer_evidence` refuses, reached by a different door.
            raise ValueError(
                f"turn_weights leaves every lane at 0.0, so no candidate turn "
                f"would be scored against the question and the pack would be "
                f"the first turns of the first session. Give at least one of "
                f"{list(TURN_LANES)} a non-zero weight."
            )
        self.work: Optional[Path] = None
        self.conversation: Optional[str] = None
        self._graph: Any = None
        #: ``source_path`` -> the node that STANDS FOR that file, built once per
        #: graph. Only consulted when ``prefer_anchor_text`` is on.
        self._anchor_by_path: Optional[Dict[str, Any]] = None
        #: node id -> the receipts that resolved on disk, built once per graph
        #: for the same reason ``_anchor_by_path`` is: it walks every node and
        #: every edge, and conv-26 alone would otherwise repeat that 199 times.
        self._receipts_by_node: Optional[Dict[str, List[Tuple[str, str]]]] = None
        #: node id -> node type name, filled by the same walk. The receipt
        #: budget is spent in :data:`RECEIPT_YIELD_ORDER`, and ``MabHit`` does
        #: not carry a type.
        self._node_type_by_id: Dict[str, str] = {}
        #: ``source_path`` -> (dia ids in FILE ORDER, dia id -> (position, line)).
        self._turn_lines_by_path: Dict[str, Any] = {}
        #: Scoring-context text -> its model2vec vector, for the whole run.
        #:
        #: A candidate turn's context does not depend on the QUESTION, but
        #: :meth:`_turn_scores` used to re-embed every one of them for every
        #: question. Memoising makes the widened pool cost-neutral — without it
        #: `turn_pool_k` roughly doubles a per-question embed pass — and makes
        #: today's narrow path faster too. Keyed by the context string rather
        #: than by ``(path, dia)`` so a change to ``turn_score_window`` cannot
        #: serve a vector built under the other window. Invalidated with
        #: ``_turn_lines_by_path``, for the same reason.
        self._turn_vector_by_context: Dict[str, Any] = {}
        #: Shared by every ``_confined_source`` read on this memory, so a staged
        #: document is read from disk once per run rather than once per receipt.
        self._source_cache: Dict[str, str] = {}
        #: One entry per query that returned fewer than K items. Never padded.
        self.shortfalls: List[Dict[str, Any]] = []
        #: Retrieved nodes whose provenance is not a staged session document.
        self.n_unmapped_hits = 0
        #: Tier-2 spend, accumulated over every question this memory assembled.
        self.receipt_chars = 0
        self.receipt_lines = 0
        #: Hits tier 2 considered, and how many of them had a receipt that
        #: resolved on disk. Their ratio is :attr:`witness_yield`, the GRAPH
        #: property that bounds every redeemability number — never quote one
        #: without it.
        self.receipt_hits = 0
        self.receipt_witnessed_hits = 0
        #: Turn-pack spend, accumulated over every question this memory
        #: assembled. `pack_chars` is the same arithmetic `run.py` records as
        #: `evidence_chars` per row — headers included, because the cap covers
        #: them — so a run whose meta and whose rows disagree is a bug in one of
        #: the two rather than a matter of interpretation.
        self.pack_chars = 0
        self.pack_turns = 0
        self.pack_sessions = 0
        #: Compile tripwires, ACCUMULATED across every conversation this memory
        #: indexes -- the same scope as every counter above, because `run.py`
        #: builds one memory for a whole run and writes meta once at the end.
        #: ``unresolvable`` counts EvidenceSpans whose metadata names no turn at
        #: all; ``dangling`` counts distinct (document, turn) pairs that were
        #: named but are not a line in that document. Both belong in the run
        #: record: they are the only signal that a change to the staged
        #: rendering has silently zeroed the receipts, and a tripwire scoped to
        #: whichever conversation ran last is not that signal.
        self.unresolvable_spans = 0
        self.dangling_receipts = 0

    # ------------------------------------------------------------------ ingest

    def project_dir(self, root: Path, conversation: Conversation) -> Path:
        """``<root>/<sample_id>`` — this conversation's own project."""
        return guard_work_dir(root) / conversation.sample_id

    def ingest(
        self,
        conversation: Conversation,
        *,
        work: Path,
        compile_project: bool = True,
        reuse_compiled: bool = False,
    ) -> IngestResult:
        """Stage one document per session under ``<work>/<sample_id>``, then compile.

        The corpus directory is removed and rebuilt so a re-run cannot inherit
        documents from another conversation — a stale ``session-0030.md`` from a
        longer conversation would be retrievable evidence from a corpus this run
        never saw.

        ``reuse_compiled`` measures against a graph a PREVIOUS run compiled,
        which is the only way to re-measure without paying the compile again. It
        writes nothing: it verifies that every document this conversation would
        stage is already on disk byte for byte AND that the compiled graph
        indexes them, then reuses. Verifying the corpus alone is not enough —
        ``ingest`` restages before compiling, so a directory can hold one
        conversation's fresh documents beside another's graph.
        """
        resolved = self.project_dir(work, conversation)
        resolved.mkdir(parents=True, exist_ok=True)
        sessions = list(conversation.sessions)
        if not sessions:
            raise ValueError(
                f"{conversation.sample_id} holds no session_<n> dialogue, so it "
                f"would stage an empty corpus and score zero — which is not "
                f"what a memory system failing to answer looks like"
            )
        corpus = resolved / "corpus"

        if reuse_compiled:
            turns, chars = _verify_staged(corpus, sessions)
            graph_path = resolved / ".tesserae" / "graph.json"
            if not graph_path.is_file():
                raise FileNotFoundError(
                    f"--reuse-compile: no compiled graph at {graph_path}. There "
                    f"is nothing to reuse; run without the flag to compile."
                )
            missing = _graph_missing_sessions(graph_path, corpus)
            if missing:
                raise ValueError(
                    f"--reuse-compile: the graph at {graph_path} does not index "
                    f"{len(missing)} of the {len(sessions)} staged session "
                    f"documents (e.g. {sorted(missing)[:3]}). It was compiled "
                    f"from a different conversation or an older corpus; recompile."
                )
        else:
            shutil.rmtree(corpus, ignore_errors=True)
            corpus.mkdir(parents=True, exist_ok=True)
            turns = chars = 0
            for session in sessions:
                body = render_session(session)
                (corpus / document_name(session.number)).write_text(
                    body, encoding="utf-8")
                turns += len(session.turns)
                chars += len(body)
            if compile_project:
                self._compile_fn(resolved)

        self.work = resolved
        self.conversation = conversation.sample_id
        self._graph = None  # a new corpus invalidates any graph already loaded
        self._anchor_by_path = None  # ...and every index derived from it
        self._receipts_by_node = None
        self._node_type_by_id = {}
        # ...and every read cached off the OLD corpus. `_confined_source`'s
        # cache is keyed on (root, path), and `ingest` restages under the same
        # root, so a stale entry here would serve the previous conversation's
        # turns as this one's receipts.
        self._turn_lines_by_path = {}
        self._turn_vector_by_context = {}
        self._source_cache = {}

        return IngestResult(
            conversation=conversation.sample_id,
            work=resolved,
            corpus_dir=corpus,
            documents=len(sessions),
            turns=turns,
            chars=chars,
            dated_sessions=sum(1 for s in sessions if s.date),
            captioned_turns=sum(1 for s in sessions for t in s.turns
                                if t.blip_caption),
            compiled=compile_project and not reuse_compiled,
            reused=reuse_compiled,
        )

    # ------------------------------------------------------------------- query

    def _resolve_graph(self) -> Any:
        if self._graph is not None:
            return self._graph
        if self.work is None:
            raise RuntimeError("query() before ingest() — there is no compiled graph")
        graph_path = self.work / ".tesserae" / "graph.json"
        if not graph_path.is_file():
            raise FileNotFoundError(
                f"no compiled graph at {graph_path} — ingest() ran with "
                f"compile_project=False, or the compile failed"
            )
        from tesserae.project import load_graph_file

        self._graph = load_graph_file(graph_path)
        return self._graph

    def embedding_backend(self) -> Any:
        """The backend retrieval will use, constructed on first call.

        Public because the runner declares its ``name`` and ``dim`` into every
        artifact, and a declaration read from anywhere other than the live
        object is a declaration that can be true while the run did something
        else.
        """
        if self._backend is not None:
            return self._backend
        from tesserae.retrieval.hybrid import active_embedding_backend

        self._backend = active_embedding_backend(self._embedding_prefer)
        return self._backend

    def _resolve_search(self) -> Callable[..., Any]:
        if self._search_fn is not None:
            return self._search_fn
        if self._fanout:
            # `fanout_search` takes every parameter `hybrid_search` takes and
            # adds its own, so this swap is the whole wiring. An injected
            # `search_fn` still wins — the tests drive stubs through it.
            from tesserae.retrieval.fanout import fanout_search

            self._search_fn = fanout_search
            return self._search_fn
        from tesserae.retrieval.hybrid import hybrid_search

        self._search_fn = hybrid_search
        return self._search_fn

    def _anchor_index(self) -> Dict[str, Any]:
        """``source_path`` -> the node that STANDS FOR that file.

        Selection is by IDENTITY — the node whose name IS the file's H1 — and
        NOT by ``hybrid._SOURCE_ANCHOR_TYPES``, for the reason
        :meth:`MabHit.is_document_anchor` documents at length: type matches 214
        nodes of the compiled group-0 graph and only 111 of them are the
        transcripts. The other 103 are things somebody talked about, and they
        carry a ``session-NNNN.md`` path all the same.

        The two tests must agree because they are two halves of one decision.
        This one chooses the node a hit is REWRITTEN to; ``is_document_anchor``
        then decides whether that node's session text is pasted
        unconditionally. Choose an impostor here and the rewritten hit fails
        the second test, falls back into the shared
        :data:`EVIDENCE_EXTRA_SOURCE_CHARS` pool, and starves — which is
        precisely the failure ``prefer_anchor_text`` exists to repair.

        Measured on the type test, pooled over 272 session files: 33 picked a
        node that then failed ``is_document_anchor`` — conv-30 9 of 19,
        conv-47 11 of 31, conv-48 4 of 30, conv-50 4 of 30, and conv-26 zero,
        which is why a conv-26-only sweep could not see this at all.
        ``session-0013.md`` chose the Project "Fashion Styling Video
        Presentation"; ``session-0001.md`` chose "Dog walking and pet care
        app". On conv-47 the flag delivered 43.7% of hits still non-anchor
        after substitution and +0.084 gold-text coverage against conv-26's
        +0.244.

        Built once per graph, never per query: this walks every node, and
        conv-26 alone would repeat that 199 times a run otherwise.
        """
        if self._anchor_by_path is not None:
            return self._anchor_by_path
        index: Dict[str, Any] = {}
        for node in getattr(self._resolve_graph(), "nodes", []):
            path = str(getattr(node, "source_path", "") or "")
            if not path or path in index:
                continue
            document = document_index(path)
            if document is None:
                continue
            if str(getattr(node, "name", "") or "") == document_title(document):
                index[path] = node
        self._anchor_by_path = index
        return index

    def _hit_nodes(self, scored: Sequence[Any]) -> List[Any]:
        """The nodes ``scored`` becomes hits from, anchors substituted or not.

        REQUIRED whenever ``source_cap`` is on, and the reason is a measured
        regression rather than a preference. With one hit per session, ten
        sessions compete for the same 8,000-character
        :data:`EVIDENCE_EXTRA_SOURCE_CHARS` and fewer of them get their raw text
        pasted, so the DOCUMENT metric rises while the PROMPT starves: pooled,
        ALL-doc@10 0.823 -> 0.883 but gold-evidence-turn coverage 0.791 ->
        0.751, multi-hop turn coverage 0.468 -> 0.420, all-gold-turns-present
        20.6% -> 16.3%. Rebuilding each hit from the node that STANDS FOR its
        file — which ``answer_evidence`` expands unconditionally — is what
        repairs that: multi-hop turn coverage 0.436 -> 0.555 at a matched
        character budget.

        KNOWN SIDE EFFECT, stated rather than hidden: ``MabHit`` derives
        :attr:`~evals.lme_mab.adapter.MabHit.is_document_anchor` from ``name``,
        and ``answer_evidence`` expands anchors UNCONDITIONALLY before the extra
        budget opens, capping each only at :data:`EVIDENCE_SOURCE_CHARS` with no
        total. Ten anchors therefore bypass the extra-source budget entirely and
        grow the prompt from 19,547 to 37,213 characters. That is why the
        acceptance gate for this flag is run at a MATCHED budget. Giving
        ``answer_evidence`` a total anchor budget is a separate change that has
        to be measured on its own.

        Two hits from one session collapse to the same anchor node here, so this
        is only coherent alongside a ``source_cap`` that already made the head
        document-disjoint. ``answer_evidence``'s ``spent`` set still pastes each
        file once either way.
        """
        if not self._prefer_anchor_text:
            return [item.node for item in scored]
        index = self._anchor_index()
        out: List[Any] = []
        for item in scored:
            node = item.node
            path = str(getattr(node, "source_path", "") or "")
            out.append(index.get(path, node))
        return out

    # ------------------------------------------------------------- receipts

    @staticmethod
    def _fact_text(node: Any) -> str:
        """``name — description``. The fact, without the filesystem path.

        :func:`evals.lme_mab.adapter.evidence_text` appends
        ``" — source: <absolute path>"``, and on this corpus that suffix is 99.0
        of a head's 264.4 characters (37%) — 990 characters of every 10-hit
        prompt, naming a file the backbone cannot open. Dropping it is FREE, not
        a trade: pooled it costs 18,588 characters / 5,111 tokens against 19,578
        / 5,561, with multi-hop turn coverage 0.465 and overall 0.790 unchanged
        to three decimals. No gold turn marker ever lived in a path.

        The provenance the path was standing in for is not deleted with it — it
        is replaced by the receipt tier, which pastes the actual turn.
        """
        parts = [str(getattr(node, "name", "") or "").strip(),
                 str(getattr(node, "description", "") or "").strip()]
        return " — ".join(part for part in parts if part)

    def _turn_lines(self, source_path: str) -> Tuple[List[str], Dict[str, Any]]:
        """One staged document as ``(dia ids in FILE ORDER, id -> (pos, line))``.

        FILE ORDER is what defines "neighbour" for :data:`RECEIPT_WINDOW`, and
        it is deliberately not arithmetic on the turn number: ``D1:7`` need not
        be followed by ``D1:8``.

        Read through ``hybrid._confined_source`` rooted at :attr:`work`, which is
        the same untrusted-frontmatter guard :meth:`answer_evidence` already
        applies before pasting anything. A ``source_path`` that escapes the
        staging root reads as ``""`` and yields no turns, so it can contribute no
        receipt — a document declaring ``source_path: /etc/passwd`` must not be
        able to put a line of it in a prompt through this door either.

        Memoised per path for the same reason ``_anchor_index`` is memoised per
        graph: this splits a whole session document, and the receipt walk visits
        each one once per fact that points into it.
        """
        cached = self._turn_lines_by_path.get(source_path)
        if cached is not None:
            return cached
        from tesserae.retrieval.hybrid import _confined_source

        text = ""
        if self.work is not None and source_path:
            text = _confined_source(source_path, self.work, self._source_cache)
        ordered: List[str] = []
        by_id: Dict[str, Any] = {}
        # A turn is every line from its header UP TO the next header, not one
        # physical line. `Turn.render()` emits the turn text verbatim and LoCoMo
        # turn text contains embedded newlines: 37 continuation lines exist
        # across the 272 staged documents and 13 of them belong to a turn some
        # resolvable receipt actually points at. Keyed on the first line alone,
        # those receipts were pasted truncated at the newline -- conv-49
        # session-0021 D21:19 rendered as `[D21:19] Sam said, "` with ZERO
        # content and an unterminated quote, which is worse than no receipt
        # because it reads as evidence. The coverage instrument could not see it
        # either: it matches the `[D<n>:<t>]` marker, and the marker survives the
        # truncation that removes the words.
        pending_dia = ""
        pending: List[str] = []

        def _flush() -> None:
            # Trailing BLANK lines are the renderer's separator between turns,
            # not part of the turn. Keeping them charged every receipt for a
            # newline it did not need and, at a binding budget, pushed the next
            # turn out — `render_session` writes "\n\n" between turns, so every
            # turn would carry one. Interior blanks are kept: those sit inside a
            # turn's own text, which is what this fix exists to preserve.
            if not pending_dia:
                return
            body = list(pending)
            while body and not body[-1].strip():
                body.pop()
            by_id[pending_dia] = (len(ordered), "\n".join(body))
            ordered.append(pending_dia)

        for line in text.splitlines():
            match = _STAGED_TURN.match(line)
            if match:
                dia = match.group(1)
                _flush()
                if dia in by_id:
                    # A repeated id keeps its FIRST rendering, as before.
                    pending_dia, pending = "", []
                    continue
                pending_dia, pending = dia, [line]
                continue
            if pending_dia:
                pending.append(line)
        _flush()
        result = (ordered, by_id)
        self._turn_lines_by_path[source_path] = result
        return result

    def _receipt_index(self) -> Dict[str, List[Tuple[str, str]]]:
        """node id -> ``[(source_path, dia_id), ...]``, RESOLVED ON DISK.

        Three properties, each of them measured rather than assumed:

        * **The turn id is matched by VALUE SHAPE**, over every metadata value
          — see :data:`_TURN_LOCATOR`. Reading ``metadata["turn"]`` resolves
          81.1% of 1,668 spans and silently reports the other 242 as having no
          receipt; the shape test resolves 95.6%.
        * **``evidenced_by`` is walked UNDIRECTED, ONE HOP**, plus hop 0 for the
          198 assertion nodes (13.3%) that carry a turn id themselves. Undirected
          because the compile emits 6-7 reversed ``EvidenceSpan -evidenced_by->
          fact`` edges per graph and a directed walk drops them. One hop because
          two hops route through ``Person`` hubs — see :data:`_EVIDENCED_BY`.
        * **A candidate counts only when its ``[D<n>:<t>]`` really is a line in
          its own ``source_path`` document.** Reachability is not provenance: a
          span that names a turn nobody staged is counted into
          :attr:`dangling_receipts` and contributes nothing.

        Built once per graph. Facts with no resolvable receipt are absent rather
        than present-and-empty, so a lookup miss and an empty list are the same
        answer and the caller needs no special case.

        THE FALSIFIER GOES HERE, and this return shape is the whole seam for it:
        replace each fact's ids with the same NUMBER of ids drawn from a seeded
        shuffle of the conversation's own resolvable turns, keep everything else
        — types, budget, tier 3 — and re-measure. If multi-hop coverage does not
        fall back towards the untiered number, ``evidenced_by`` carries nothing
        the ranking did not already have and "receipt" is a label on a random
        line. That world would still report the same provenance rate, which is
        why the shuffle is a stronger control than a zero receipt budget: the
        budget proves the TIER is load-bearing, the shuffle proves the EDGE is.
        No shuffle ships here — a measurement harness substitutes this method.
        """
        if self._receipts_by_node is not None:
            return self._receipts_by_node
        graph = self._resolve_graph()

        claimed: Dict[str, List[Tuple[str, str]]] = {}
        types: Dict[str, str] = {}
        spans = unresolvable = 0
        for node in getattr(graph, "nodes", None) or ():
            node_id = str(getattr(node, "id", "") or "")
            if not node_id or node_id in claimed:
                continue
            node_type = getattr(node, "type", "")
            types[node_id] = str(getattr(node_type, "value", node_type) or "")
            path = str(getattr(node, "source_path", "") or "")
            ids = _claimed_turn_ids(node)
            claimed[node_id] = [(path, dia) for dia in ids]
            if types[node_id] == _EVIDENCE_SPAN_TYPE:
                spans += 1
                if not ids:
                    unresolvable += 1

        neighbours: Dict[str, List[str]] = {}
        for edge in getattr(graph, "edges", None) or ():
            if str(getattr(edge, "type", "") or "") != _EVIDENCED_BY:
                continue
            source = str(getattr(edge, "source", "") or "")
            target = str(getattr(edge, "target", "") or "")
            if not source or not target:
                continue
            neighbours.setdefault(source, []).append(target)
            neighbours.setdefault(target, []).append(source)

        dangling: set = set()
        index: Dict[str, List[Tuple[str, str]]] = {}
        for node_id, own in claimed.items():
            candidates: List[Tuple[str, str]] = list(own)
            for other in neighbours.get(node_id, ()):
                candidates.extend(claimed.get(other, ()))
            receipts: List[Tuple[str, str]] = []
            for candidate in candidates:
                if candidate in receipts:
                    continue
                path, dia = candidate
                if dia not in self._turn_lines(path)[1]:
                    dangling.add(candidate)
                    continue
                receipts.append(candidate)
            if receipts:
                index[node_id] = receipts

        self._node_type_by_id = types
        # ACCUMULATE, like every other counter on this class. These were
        # assigned per graph while `receipt_chars`, `receipt_lines`,
        # `receipt_hits` and `receipt_witnessed_hits` summed across the run --
        # and `run.py` builds ONE memory for all ten conversations and writes
        # meta once at the end, so the artifact reported run-wide spend beside
        # last-conversation-only tripwires. Measured over the 2026-08-21 graphs
        # the true total is 52 unresolvable spans (conv-41 9, conv-43 20,
        # conv-48 7, conv-49 8, conv-50 8, the rest 0); the artifact printed
        # whichever conversation happened to be last, which for a run ending on
        # conv-26 is 0. A tripwire that reads 0 because of iteration order is
        # worse than no tripwire.
        self.unresolvable_spans += unresolvable
        self.dangling_receipts += len(dangling)
        if spans and unresolvable > spans * _UNRESOLVABLE_WARN_RATIO:
            # Loud, because the alternative is a coverage regression with no
            # cause attached to it. Every receipt in this design comes from the
            # marker `Turn.render` writes; a compile that stopped emitting turn
            # locators zeroes the receipts and the instrument at once.
            print(
                f"WARNING: {unresolvable} of {spans} EvidenceSpan nodes in "
                f"{self.conversation or self.work} name no turn "
                f"({unresolvable / spans:.1%}); receipts are being lost at the "
                f"compile, not here",
                file=sys.stderr,
            )
        self._receipts_by_node = index
        return index

    def _receipt_yield_order(self, hits: Sequence[MabHit]) -> List[int]:
        """Ranks of ``hits``, ordered by :data:`RECEIPT_YIELD_ORDER` then rank.

        Allocation order ONLY. The prompt is rendered in rank order; this
        decides who reaches the budget first when it binds, which on this corpus
        it does not — see :data:`RECEIPT_YIELD_ORDER`.
        """
        last = len(RECEIPT_YIELD_ORDER)
        ranking = {name: i for i, name in enumerate(RECEIPT_YIELD_ORDER)}
        return sorted(
            range(len(hits)),
            key=lambda rank: (
                ranking.get(self._node_type_by_id.get(hits[rank].node_id, ""), last),
                rank,
            ),
        )

    def query_hits(self, question: str, *, k: int,
                   record_shortfall: bool = True) -> List[MabHit]:
        """Up to ``k`` hits for ``question``. Never more, never padded.

        The one search both the answering path and the retrieval score are built
        on, so the evidence a run answers from and the documents its retrieval is
        scored on can never come from two different rankings. Fewer than ``k`` is
        recorded in :attr:`shortfalls` and returned short: padding would make an
        under-filled budget indistinguishable from a full one.

        ``record_shortfall=False`` is for the ONE caller that is not spending
        the evidence budget: :meth:`_turn_pool_paths` widening the candidate
        pool. A pool query asks for more sessions than a 19-session conversation
        has BY DESIGN, so ledgering it would report "every query returned fewer
        than k" and drown the shortfalls that mean the answering budget went
        unfilled. The hits it returns are still capped at ``k`` and still never
        padded; only the ledger entry is skipped.

        RERANKER ORDERING, AND IT IS UNTESTED. ``rerank_nodes`` has no notion of
        documents and will happily re-cluster several hits from one session,
        undoing the cap. So with both stages on, the fan-out runs UNCAPPED, the
        cross-encoder reorders, and the cap is applied to what it produced.
        ``fanout`` and ``reranker`` are never both on in the sweep this was
        built for, so that ordering has been reasoned about and not measured.
        """
        # With a reranker the lanes are a CANDIDATE GENERATOR, not the final
        # ranking, so they are asked for more than the budget and the
        # cross-encoder chooses k of them. Without one, `top_k` is k exactly and
        # this line is what it always was.
        search_k = k * self._rerank_overfetch if self._reranker else k
        extra: Dict[str, Any] = {}
        if self._fanout:
            extra = {
                "overfetch": self._fanout_overfetch,
                # Capped here only when nothing downstream would undo it.
                "source_cap": None if self._reranker else self._source_cap,
                "ubiquity_df_ratio": self._ubiquity_df_ratio,
                "extra_facets": self._extra_facets,
            }
        result = self._resolve_search()(
            self._resolve_graph(),
            question,
            top_k=search_k,
            weights=self._weights,
            mode=self._mode,
            backend=self.embedding_backend(),
            # The extraction pipeline builds a node's searchable text from its
            # name and description, so a whole chat session would be retrievable
            # only through a short concept summary. Handing the lexical lanes
            # the session file itself is what closes that gap, and it is
            # confined to the directory this adapter staged into.
            source_root=self.work,
            **extra,
        )
        scored_nodes = result.scored
        capped_after_rerank = (
            self._reranker is not None
            and self._fanout
            and self._source_cap is not None
        )
        if self._reranker:
            from tesserae.retrieval.rerank import rerank_nodes

            scored_nodes = rerank_nodes(
                question,
                scored_nodes,
                # The cap is what bounds the result when it runs after this, and
                # it can only choose among what it is handed: truncating to k
                # here would leave it k items that may all name one session, and
                # the no-shrink clamp would then refill with that same session.
                top_n=None if capped_after_rerank else k,
                reranker=self._reranker,
                # The same text the lexical lanes scored. A reranker reading
                # different text would be reordering a ranking it never saw.
                source_root=self.work,
            )
            if capped_after_rerank:
                from tesserae.retrieval.fanout import (
                    _merge_document_disjoint,
                    _source_path_key,
                )

                scored_nodes = _merge_document_disjoint(
                    [scored_nodes],
                    top_k=k,
                    source_cap=self._source_cap,
                    group_key=_source_path_key,
                )
        # `search_k` is UNCHANGED by tiering: the receipt tier buys coverage
        # from the SAME ten hits, not from a deeper slice. That is what keeps
        # `documents_of` seeing identical hits, so ALL-gold-doc@10 cannot move
        # — the document metric stays a control rather than becoming a second
        # thing the change is credited for.
        hits = [
            MabHit(
                text=(self._fact_text(node) if self._tiered_evidence
                      else evidence_text(node)),
                source_path=str(getattr(node, "source_path", "") or ""),
                name=str(getattr(node, "name", "") or ""),
                # Set on every path, read on none but the tiered and turn ones.
                # It costs a string per hit and reaches no prompt byte; gating
                # it would only make the off path differ from the on path in a
                # second place.
                #
                # OFF THE PRE-SUBSTITUTION NODE, and that pairing is the whole
                # fix. `_hit_nodes` rewrites a hit to the ANCHOR that stands for
                # its session file, and anchors — SourceDocument / Session —
                # carry no `evidenced_by` edge at all. Reading the id off the
                # substituted node therefore handed `_receipt_index` an id with
                # no receipts behind it: measured on the shipped fan-out arm
                # (`--fanout --source-cap 1 --prefer-anchor-text`), 0.9 receipts
                # per question flagging 1 of 183 gold turns (0.5%). Taking the
                # id from the node the RANKING chose, while text/name/path stay
                # the anchor's, gives 6.7 receipts per question flagging 65 of
                # 183 (35.5%).
                #
                # `_hit_nodes` returns one node per `scored_nodes` item, in
                # order, so the zip is positional by construction rather than by
                # coincidence — and it is what makes this fix free: the session
                # path-set is bit-identical either way, matched 199/199 over all
                # conv-26 questions.
                node_id=str(getattr(scored_item.node, "id", "") or ""),
            )
            for scored_item, node in zip(scored_nodes,
                                         self._hit_nodes(scored_nodes))
        ][:k]
        if len(hits) < k and record_shortfall:
            self.shortfalls.append({
                "question": question,
                "conversation": self.conversation,
                "requested": k,
                "returned": len(hits),
                "total_matches": int(getattr(result, "total_matches", 0) or 0),
            })
        return hits

    def answer_evidence(self, hits: Sequence[MabHit], *,
                        expand: bool = True,
                        question: str = "") -> List[str]:
        """``hits`` as the strings the BACKBONE reads — the answering path only.

        Four lines, and that is the whole opt-in guarantee: with
        ``evidence_unit`` left at ``"session"`` and ``tiered_evidence`` off this
        reaches :meth:`_answer_evidence_sessions`, which is the body this method
        had before either branch existed, moved verbatim. Neither other branch
        is reachable unless the constructor was told otherwise, so
        "byte-identical when off" is a property of the code rather than a result
        somebody measured once.

        ``question`` is READ ONLY by the turn unit, which scores every candidate
        turn against it. The session units never saw the question and still do
        not, which is why the parameter is defaulted rather than positional:
        every existing call site stays correct, and the one path that needs it
        refuses to run without it rather than quietly packing a prompt on an
        all-zero lexical lane.
        """
        if not expand:
            return [hit.text for hit in hits]
        if self._evidence_unit == "turn":
            if not question.strip():
                raise ValueError(
                    "evidence_unit='turn' scores every candidate turn against "
                    "the question, so answer_evidence() needs it. Without one "
                    "the lexical lane is uniformly zero and the pack would be "
                    "chosen by session rank alone — a plausible prompt built "
                    "from nothing the question said."
                )
            return self._answer_evidence_turns(hits, question)
        if not self._tiered_evidence:
            return self._answer_evidence_sessions(hits)
        return self._answer_evidence_tiered(hits)

    # ------------------------------------------------------------- turn pack

    def _document_text(self, source_path: str) -> str:
        """One staged document, whole, through the confinement guard.

        Uncapped where :meth:`_answer_evidence_sessions` caps at
        :data:`EVIDENCE_SOURCE_CHARS`, because nothing here pastes it: the turn
        path reads a document to split it into turns and to find its ``Chat
        Time`` line, and a cap would silently drop the tail turns of a long
        session from the CANDIDATE POOL rather than from the prompt.
        """
        from tesserae.retrieval.hybrid import _confined_source

        if self.work is None or not source_path:
            return ""
        return _confined_source(source_path, self.work, self._source_cache)

    def _session_header(self, source_path: str) -> str:
        """``## Session 0003 — 3 May 2023``. ~40 characters, once per session.

        The date moves HERE from the per-item ``" — session date: ..."`` stamp
        the session units append. Same information, paid for once per session
        instead of once per hit — 390 characters against 298 + 530 of
        ``Chat Time:`` headers inside the pasted files — and in the one place a
        reader resolving "yesterday" in a turn beneath it will look.

        A document outside the staging root has no title and no date; it also
        has no turns, so it never reaches this.
        """
        index = document_index(source_path)
        title = (document_title(index) if index is not None
                 else Path(source_path).stem)
        stamp = session_date(self._document_text(source_path))
        return f"## {title} — {stamp}" if stamp else f"## {title}"

    @staticmethod
    def _head_text(hit: MabHit) -> str:
        """``hit.text`` with the absolute filesystem path taken back off.

        Exactly inverts the ``" — source: <path>"`` suffix
        :func:`evals.lme_mab.adapter.evidence_text` appends, using the hit's own
        ``source_path`` as the needle so the match cannot be approximate. That
        suffix is 99.0 of a head's 264.4 characters on this corpus — 990
        characters of every 10-hit prompt naming a file the backbone cannot open
        — and dropping it is free: measured pooled, 18,588 characters against
        19,578 with multi-hop turn coverage 0.465 and overall 0.790 unchanged to
        three decimals.

        Done here rather than in ``query_hits`` on purpose: the ranking's
        ``MabHit`` stays what every other arm's is, and the strip reaches only
        the path that prints it.
        """
        suffix = f" — source: {hit.source_path}"
        if hit.source_path and hit.text.endswith(suffix):
            return hit.text[:-len(suffix)]
        return hit.text

    def _turn_pool_paths(self, hits: Sequence[MabHit],
                         question: str = "") -> List[str]:
        """The staged documents whose turns may be scored, RETRIEVED ONES FIRST.

        ``turn_pool="retrieved"`` with ``turn_pool_k=0`` is the default and
        returns exactly the documents the hits named, in rank order. That holds
        the pack to the SAME distinct-session count the shipped arm has (9.78 on
        conv-26), which makes it Levy-safe by construction and costs 0.013 turn
        coverage (0.928 against 0.941).

        ``turn_pool_k`` above 0 is RETRIEVE-WIDE: the session stage is asked a
        second, free time at that k — BM25 + model2vec over the same compiled
        graph, no LLM call — and its documents are appended after the answer-time
        ones, in its own rank order, de-duplicated by session NUMBER. It is a
        retrieval result with a stated k and NOT the ``turn_pool="corpus"`` glob
        below, deliberately: 14.7% of gold evidence turns never enter the pool
        at all (against 1.1% lost to the admission rule), so widening is where
        the headroom is, but a pool bought by globbing the corpus would not
        transfer to any corpus where the pool cannot be everything. Widening
        alone is not the design — pair it with ``turn_session_cap``, which is
        what bounds the distractor count the widening would otherwise raise.

        ``turn_pool="corpus"`` appends every other staged session, in file-name
        order. It reaches full coverage parity (0.942) but at 17.3 distinct
        sessions, and Levy et al. (Findings of EMNLP 2025) measure 5-20% F1 lost
        on multi-hop when document count rises at a fixed length, with
        gold-documents-only beating every condition containing distractors.
        Their one exception is Qwen2.5 and our reader is neither. **Do not
        assume the wide pool is free because its coverage number is bigger** —
        it is a second arm, not a better default.

        AND IT IS NOT GENERAL YET, which any writeup quoting the wide pool has
        to say. conv-26 is 19 sessions / 419 turns, so scoring the whole corpus
        per query is trivially cheap and ``"corpus"`` is indistinguishable from
        reading everything — a retrieval bypass wearing a packing hat. On a real
        corpus the pool must come from the session stage at a widened ``k``,
        which is why ``"retrieved"`` is the default and why this design is
        retrieve-wide / prune-narrow rather than score-everything.

        De-duplication is by session NUMBER, not by string, and retrieved paths
        keep their own spelling: ``source_path`` arrives from frontmatter and
        the glob arrives from the filesystem, and two spellings of one session
        would put its turns in the pool twice.
        """
        paths: List[str] = []
        seen: set = set()

        def _offer(source_path: str) -> None:
            """Pool ``source_path`` unless its SESSION is already pooled.

            By session NUMBER and not by string: ``source_path`` arrives from
            frontmatter and the glob arrives from the filesystem, and two
            spellings of one session would put its turns in the pool twice.
            """
            if not source_path or source_path in paths:
                return
            index = document_index(source_path)
            if index is not None:
                if index in seen:
                    return
                seen.add(index)
            paths.append(source_path)

        for hit in hits:
            _offer(hit.source_path)
        if self._turn_pool_k and question.strip():
            # A SECOND retrieval at a stated k, not a glob, and its shortfall is
            # not ledgered: asking for more sessions than the conversation has
            # is what this query is FOR.
            for hit in self.query_hits(question, k=self._turn_pool_k,
                                       record_shortfall=False):
                _offer(hit.source_path)
        if self._turn_pool == "corpus" and self.work is not None:
            for path in sorted((self.work / "corpus").glob("session-*.md")):
                if document_index(str(path)) is None:
                    continue
                _offer(str(path))
        return paths

    def _turn_scores(self, question: str, contexts: Sequence[str],
                     ranks: Sequence[int]) -> List[float]:
        """One score per candidate turn: the weighted sum of z-scored lanes.

        Every lane is FREE — BM25 and the tokenizer are
        ``tesserae.retrieval.hybrid``'s, the vectors are the same model2vec
        backend the retrieval already resolved, and the rank is an integer the
        caller already had. 419 BM25 scorings and 419 vectors per question over
        199 questions completes in seconds with the backend resident.

        A lane weighted 0.0 is not computed at all, which is what lets a
        BM25-only arm run with no embedding backend, no model download and no
        network. See :data:`TURN_WEIGHTS` for why the z-score is load-bearing
        rather than tidy.
        """
        from tesserae.retrieval.hybrid import _bm25_scores, _tokenize

        weights = self._turn_weights
        lanes: Dict[str, Sequence[float]] = {}
        if weights["bm25_ctx"]:
            lanes["bm25_ctx"] = _bm25_scores(
                _tokenize(question), [_tokenize(text) for text in contexts])
        if weights["dense"]:
            backend = self.embedding_backend()
            # ONLY the question and the contexts not already embedded on this
            # memory. A context does not depend on the question, so re-embedding
            # every candidate for every question was pure repetition — and it is
            # what would have made a widened pool cost a second embed pass per
            # question instead of nothing. `dict.fromkeys` de-duplicates while
            # keeping first-seen order, so the batch handed to the backend is
            # deterministic.
            cache = self._turn_vector_by_context
            missing = [text for text in dict.fromkeys(contexts)
                       if text not in cache]
            fresh = backend.embed([question, *missing])
            query = fresh[0] if fresh else []
            for text, vector in zip(missing, fresh[1:]):
                cache[text] = vector
            lanes["dense"] = [_cosine(query, cache.get(text) or [])
                              for text in contexts]
        if weights["rank"]:
            # NEGATED: rank 0 is the best session, and every other lane scores
            # high for good. Sign errors here are invisible in an aggregate.
            lanes["rank"] = [-float(rank) for rank in ranks]

        total = [0.0] * len(contexts)
        for lane, raw in lanes.items():
            weight = weights[lane]
            for i, value in enumerate(_zscore(raw)):
                total[i] += weight * value
        return total

    def _answer_evidence_turns(self, hits: Sequence[MabHit],
                               question: str) -> List[str]:
        """``hits`` as query-selected TURNS, grouped by session. Opt-in.

        RETURN CONTRACT: one string per CONTRIBUTING SESSION rather than one per
        hit. ``run.py`` records ``evidence_chars`` as ``sum(len(text) for text
        in items)``, so that metric's meaning is unchanged and comparable to the
        20,798 / 43,413 session-unit rows; ``n_evidence`` falls from 10 to ~9.8
        and is already persisted per row; ``build_backbone``'s ``"[{i}] {text}"``
        numbering handles any length. No change to the answering path is needed.

        THE ORDER OF ADMISSION IS THE DESIGN:

        1. **Fact heads**, only when ``turn_heads="fact"``. OFF by default and
           measured: reinstating them costs 2,230 characters, which at a fixed
           28,000 cap is ~14 turns, and the turns are worth more — coverage
           0.942 -> 0.927 wide-pool, open-domain 0.773 -> 0.682. Kept as a flag
           because LongMemEval §5.2 finds fact decomposition helps multi-session
           reasoning and nothing else, so a router may yet want them on
           multi-hop alone; :meth:`_head_text` is what makes them affordable.
        2. **Receipt turns**, force-admitted: the exact transcript turns the
           retrieved facts' ``evidenced_by`` edges point at. ~6.7 turns / ~1,200
           characters, 4.3% of the budget. They are the one signal here that
           comes from the GRAPH rather than from a scorer, and they only became
           available at all when ``query_hits`` stopped reading ``node_id`` off
           the anchor-substituted node.
        3. **Everything else by score**, greedily, until the cap.

        ``turn_session_cap`` bounds tiers 1-3 together: admission refuses to
        open the (cap+1)-th SESSION BLOCK, receipts included, and nothing else
        moves — receipts still run first, the score loop still breaks on an
        exhausted budget, ties still break on ``(session, position)``, and the
        terminal overrun check still guards the cap. It is the other half of
        ``turn_pool_k``: widening the pool recovers the 14.7% of gold turns that
        never became candidates, and the cap is what stops that widening paying
        for it in distractors. Pooled, the pair is worth +0.021 gold-turn
        coverage at 27,985 characters against 27,949 — the budget does not move,
        6.1 turns are traded for 6.1 session headers. See
        :data:`TURN_SESSION_CAP` for the curve, and for why 16 is a knee found
        on the benchmark rather than a tuned optimum.

        Each admitted turn brings its :data:`TURN_EMIT_WINDOW` neighbours with
        it, competing for the same budget — which is why that window is 0.

        THIS BREAKS THE INVARIANT :data:`EVIDENCE_SOURCE_CHARS` EXISTS TO
        DEFEND, and the break is deliberate rather than overlooked.
        :meth:`_answer_evidence_sessions` admits a document WHOLE OR NOT AT ALL
        because a session truncated mid-way is the ranked-but-unanswerable
        failure. The defence is that query-conditioned SELECTION is not blind
        truncation — the packer keeps the turns that scored, where truncation
        keeps the first 8,000 characters — and the whole of that defence is the
        coverage table on :data:`EVIDENCE_PACK_CHARS`: at a matched ~27,100
        characters and a matched document count, truncating gives 0.858 and
        packing gives 0.928. It must be argued there, in the open.

        THE PROXY IS THE BET. Every number quoted in this design is
        gold-evidence-TURN-in-prompt, not a graded answer. Coverage parity at
        0.928 could still ANSWER worse than 0.941 spread over whole sessions if
        the reader needs surrounding dialogue to resolve a reference the packer
        cut. That is what the pilot is for, and its budget-matched control arm
        is not optional: this branch has already lost two thirds of one headline
        to exactly that omission.
        """
        pool = self._turn_pool_paths(hits, question)
        rank_of: Dict[str, int] = {}
        for path in pool:
            rank_of[path] = len(rank_of)

        # ---- candidates: every [D<n>:<t>] line of every pooled session, scored
        # with its neighbours visible and emitted without them.
        units: List[Tuple[str, str, int]] = []
        contexts: List[str] = []
        for path in pool:
            ordered, by_id = self._turn_lines(path)
            for position, dia in enumerate(ordered):
                low = max(0, position - self._turn_score_window)
                high = position + self._turn_score_window + 1
                contexts.append("\n".join(_packed_turn_text(by_id[d][1])
                                          for d in ordered[low:high]))
                units.append((path, dia, position))
        if not units:
            # No staged turns reachable — an un-ingested memory, or hits whose
            # every path escaped the staging root. Empty rather than invented:
            # `evidence_chars` 0 on the row is a visible failure, a fabricated
            # prompt is not.
            return []

        scores = self._turn_scores(
            question, contexts, [rank_of.get(path, len(pool))
                                 for path, _, _ in units])

        budget = self._evidence_pack_chars
        chosen: Dict[str, set] = {}
        heads: Dict[str, List[str]] = {}
        lines_of: Dict[str, Dict[str, Any]] = {}
        order_of: Dict[str, List[str]] = {}
        for path in pool:
            order_of[path], lines_of[path] = self._turn_lines(path)

        def spend(path: str, text: str) -> bool:
            """Charge ``text`` as one more line of ``path``'s block, or refuse.

            EXACT, not approximate: an item is ``header + "\n" + "\n".join(
            lines)``, so the first line of a session costs its header and a
            newline as well and every later one costs a newline. That is what
            makes :data:`EVIDENCE_PACK_CHARS` a cap on the number ``run.py``
            records rather than on an internal proxy for it.
            """
            nonlocal budget
            cost = len(text) + 1
            if path not in chosen:
                cost += len(self._session_header(path))
            if not text or cost > budget:
                # SKIP, not break — `answer_evidence`'s idiom for a unit that
                # does not fit, so leftover budget can still buy a cheaper turn
                # further down the ranking.
                return False
            budget -= cost
            chosen.setdefault(path, set())
            return True

        def admit(path: str, dia: str) -> bool:
            entry = lines_of.get(path, {}).get(dia)
            if entry is None or dia in chosen.get(path, ()):
                return False
            # PACK-NARROW. Refuse to open the (cap+1)-th session block — and
            # refuse it to RECEIPTS too, which is the deliberate part: receipts
            # run first, so they spend the cap rather than being exempted from
            # it, and a one-hop `evidenced_by` receipt lands in its fact's own
            # session 99.0% of the time so it almost never wants a new block.
            # A turn in an ALREADY-OPEN block is always still admissible.
            if (self._turn_session_cap and path not in chosen
                    and len(chosen) >= self._turn_session_cap):
                return False
            if not spend(path, _packed_turn_text(entry[1])):
                return False
            chosen[path].add(dia)
            self.pack_turns += 1
            # The emission window rides along with its turn and out of the same
            # budget. Measured NEGATIVE at a fixed cap (0.923 -> 0.908), hence
            # TURN_EMIT_WINDOW = 0.
            if self._turn_emit_window:
                ordered = order_of[path]
                position = entry[0]
                low = max(0, position - self._turn_emit_window)
                high = position + self._turn_emit_window + 1
                for neighbour in ordered[low:high]:
                    if neighbour in chosen[path]:
                        continue
                    neighbour_line = _packed_turn_text(
                        lines_of[path][neighbour][1])
                    if spend(path, neighbour_line):
                        chosen[path].add(neighbour)
                        self.pack_turns += 1
            return True

        # ---- 1. fact heads, off by default.
        if self._turn_heads == "fact":
            for hit in hits:
                path = hit.source_path
                if path not in lines_of:
                    continue
                # The cap counts SESSION BLOCKS, and a head renders inside one,
                # so a head may not open the (cap+1)-th block either. `admit`
                # carries the same guard; this tier bypasses it by calling
                # `spend` directly, which is exactly how an invariant enforced
                # in one place only goes quietly wrong.
                if (self._turn_session_cap and path not in chosen
                        and len(chosen) >= self._turn_session_cap):
                    continue
                text = self._head_text(hit)
                if not text or text in heads.get(path, ()):
                    continue
                if spend(path, text):
                    heads.setdefault(path, []).append(text)

        # ---- 2. receipts, force-admitted ahead of the score.
        if any(hit.node_id for hit in hits):
            index = self._receipt_index()
            for rank in self._receipt_yield_order(hits):
                hit = hits[rank]
                if not hit.node_id:
                    continue
                self.receipt_hits += 1
                witnesses = index.get(hit.node_id) or ()
                if witnesses:
                    self.receipt_witnessed_hits += 1
                for path, dia in witnesses[:RECEIPT_TURNS_PER_FACT]:
                    # Confined to the POOL. A receipt in a session the pool does
                    # not hold would add a document to the prompt and inflate
                    # the distinct-session count the retrieved pool exists to
                    # bound — and it costs almost nothing to refuse, because a
                    # one-hop `evidenced_by` receipt lands in the fact's own
                    # session 99.0% of the time.
                    if admit(path, dia):
                        self.receipt_lines += 1

        # ---- 3. everything else, greedily by score. Ties break on (session,
        # position) so two runs of the same question pack the same prompt: this
        # repository has been bitten four times by wall-clock and set-iteration
        # order reaching an artifact.
        for _, path, dia in sorted(
            ((-score, path, dia)
             for score, (path, dia, _) in zip(scores, units)),
        ):
            if budget <= 0:
                break
            admit(path, dia)

        # ---- render: session order, turns in FILE order beneath one header.
        # SESSION order and not rank order, unlike every other unit here,
        # because the pack is now fragments: a reader resolving "yesterday" in
        # one turn against a date in another needs them in the order they
        # happened, and rank order would interleave three months at random.
        items: List[str] = []
        for path in sorted(chosen, key=_pack_sort_key):
            block = [self._session_header(path)]
            block.extend(heads.get(path, ()))
            block.extend(_packed_turn_text(lines_of[path][dia][1])
                         for dia in order_of[path] if dia in chosen[path])
            items.append("\n".join(block))

        spent = sum(len(item) for item in items)
        if spent > self._evidence_pack_chars:
            # Loud, because the whole claim of this design is a number: a pack
            # that overran its cap would report a coverage win bought with
            # characters it was not allowed to spend.
            raise RuntimeError(
                f"turn pack overran its budget: {spent} characters against "
                f"{self._evidence_pack_chars}. The cost model in spend() and "
                f"the rendering below it have drifted apart."
            )
        self.pack_chars += spent
        self.pack_sessions += len(items)
        return items

    def _answer_evidence_tiered(self, hits: Sequence[MabHit]) -> List[str]:
        """``hits`` as heads, then receipts, then sessions. Opt-in.

        The order below is the design. **Tier 3 is SELECTED FIRST**, before a
        character of receipt budget is spent, so tier 2 never pays for a turn
        whose whole session is about to arrive anyway — that ordering is what
        keeps measured tier-2 spend at 477 characters instead of thousands, and
        it is why raising :data:`EVIDENCE_EXTRA_SOURCE_CHARS` degrades this
        design back towards today's (the receipt tier is worth +0.032 overall
        coverage at 8,000 and +0.007 at 20,000).

        Tier 3's selection rule is :meth:`_answer_evidence_sessions`' rule byte
        for byte — anchors unconditionally, then the rest in rank order while
        the extra budget lasts, whole-or-nothing, skipping rather than breaking.
        Keeping it identical is what makes this strictly additive: every
        document the shipped rule pastes is still pasted, so no question can
        come out of tiering with less session text than it had.

        What is NOT solved here, stated rather than buried: tier 3 keeps that
        rule's unconditional, uncapped anchor pass, so ten anchors in one top-10
        still produce a ~36,000-character prompt. Tiering neither causes that
        nor fixes it.
        """
        from tesserae.retrieval.hybrid import _confined_source

        root = self.work

        def source_of(hit: MabHit) -> str:
            if root is None:
                return ""
            return _confined_source(hit.source_path, root, self._source_cache)[
                :EVIDENCE_SOURCE_CHARS]

        # ---- tier 3, selected (not yet rendered). Verbatim from the shipped
        # rule; see `_answer_evidence_sessions` for why each half is what it is.
        chosen: set = set()
        for hit in hits:
            if hit.is_document_anchor and source_of(hit):
                chosen.add(hit.source_path)
        budget = EVIDENCE_EXTRA_SOURCE_CHARS
        for hit in hits:
            source = source_of(hit)
            if source and hit.source_path not in chosen and len(source) <= budget:
                chosen.add(hit.source_path)
                budget -= len(source)

        # ---- tier 2: the exact transcript turn each fact was extracted from.
        receipts: Dict[int, List[str]] = {}
        receipt_budget = self._evidence_receipt_chars
        if receipt_budget > 0:
            emitted: set = set()
            window = self._receipt_window
            index = self._receipt_index()
            for rank in self._receipt_yield_order(hits):
                hit = hits[rank]
                if not hit.node_id:
                    continue
                self.receipt_hits += 1
                witnesses = index.get(hit.node_id) or ()
                if witnesses:
                    self.receipt_witnessed_hits += 1
                for path, dia in witnesses[:RECEIPT_TURNS_PER_FACT]:
                    # `chosen` holds hit source paths and this is a span's, both
                    # written by the same compile from the same frontmatter, so
                    # the string comparison is the right one. If a future
                    # compile ever spelled the two differently this fails SAFE:
                    # the receipt is emitted beside a session that also carries
                    # it, costing ~175 characters and losing nothing.
                    if path in chosen:
                        continue  # tier 3 brings the whole session anyway
                    ordered, by_id = self._turn_lines(path)
                    position = by_id[dia][0]
                    neighbourhood = ordered[max(0, position - window):
                                            position + window + 1]
                    for neighbour in neighbourhood:
                        if neighbour in emitted:
                            continue
                        line = by_id[neighbour][1]
                        # SKIP, not break — `answer_evidence`'s own idiom for a
                        # unit that does not fit, so leftover budget can still
                        # buy a cheaper line further down the ranking.
                        if not line or len(line) + 1 > receipt_budget:
                            continue
                        receipt_budget -= len(line) + 1
                        emitted.add(neighbour)
                        receipts.setdefault(rank, []).append(line)
                        self.receipt_chars += len(line) + 1
                        self.receipt_lines += 1

        # ---- render, in RANK order. Tier 2's yield ordering allocates the
        # budget; it never reorders the prompt, because the rank order is what
        # the retrieval scored and what every other arm reads.
        spent: set = set()
        evidence: List[str] = []
        for rank, hit in enumerate(hits):
            source = source_of(hit)
            raw = ""
            if source and hit.source_path in chosen and hit.source_path not in spent:
                raw = source
                spent.add(hit.source_path)
            head = hit.text
            stamp = session_date(source)
            if stamp:
                head = f"{head} — session date: {stamp}"
            parts = (head, "\n".join(receipts.get(rank) or ()), raw)
            evidence.append("\n".join(part for part in parts if part))
        return evidence

    @property
    def witness_yield(self) -> float:
        """Of the hits tier 2 considered, the share with a receipt on disk.

        A property of the GRAPH and the retrieval, not of the assembly. It is
        the ceiling on how much of a prompt can carry a redeemable receipt, so
        a redeemability number quoted without it is unbounded and therefore
        unreadable. 0.0 when nothing has been assembled yet.
        """
        if not self.receipt_hits:
            return 0.0
        return self.receipt_witnessed_hits / self.receipt_hits

    def _answer_evidence_sessions(self, hits: Sequence[MabHit]) -> List[str]:
        """``hits`` as the strings the BACKBONE reads — the answering path only.

        **A hit expands the session it came from, whether or not it IS that
        session.** Document anchors expand unconditionally, exactly as before;
        the remaining hits then expand in rank order until
        :data:`EVIDENCE_EXTRA_SOURCE_CHARS` of further source has been spent.
        Each file is pasted at most once, at the first hit that names it, and
        every item — expanded or not — is stamped with its session's date.

        Restricting expansion to :attr:`MabHit.is_document_anchor` is what this
        method used to do, and measured on the 150 gradeable conv-26 questions
        of the 2026-08-21 run it is the largest single loss in the benchmark.
        ``documents_of`` credits a session for ANY hit whose ``source_path``
        names it, so retrieval scored the gold session as retrieved for 140 of
        150 questions (93.3%) — but the gold session's TEXT reached the prompt
        for only 80 (53.3%), because the other 60 were reached through a concept
        node, which contributed its name and a description whose median length
        is 75 characters. Refusal tracks that gap and nothing else: 6.2% (5/80)
        when the session's text was in the prompt against 35.7% (25/70) when it
        was not, and reading all 30 refusals, 25 were the model correctly
        declining a prompt that did not contain the answer. That is why two
        successive prompt fixes did not move the refusal rate: the prompt was
        never the defect.

        The anchor test is still the right answer to the question it was asked —
        which node STANDS FOR a file, so ``documents_of`` can score one document
        per file — and :attr:`MabHit.is_document_anchor` keeps doing that job
        here, deciding who is expanded before the budget opens. It was the wrong
        answer to a different question: which hits are worth spending prompt on.
        The duplication its docstring exists to prevent — eleven concepts from
        one chat pasting one file eleven times — is prevented by ``chosen`` and
        ``spent``, which are keyed on the FILE.

        **The addition is strictly additive, and that is a property of the code
        rather than a result.** Anchors are chosen before the budget is
        consulted, so every document the old rule pasted is still pasted and no
        question can end up with less evidence than it had. The alternative —
        one budget spanning anchors and concept hits alike — was implemented and
        measured first, and it regressed 14 of the 150 gradeable questions while
        the aggregate coverage still rose. See
        :data:`EVIDENCE_EXTRA_SOURCE_CHARS`.

        ``source_path`` arrives from document frontmatter and is UNTRUSTED, and
        this is the side where that matters most — ranking buries a stolen file
        in a score, answering pastes it into a prompt — so the read goes through
        ``hybrid._confined_source`` rooted at the directory this adapter staged
        into. Widening expansion widens nothing here: a path that escapes the
        staging root reads as ``""``, expands to nothing, and is stamped with no
        date, exactly as before.

        ``expand=False`` returns the node summaries alone, unstamped. It is the
        control arm that makes the expansion measurable over ONE frozen
        retrieval rather than across two checkouts; nothing selects it by
        default.
        """
        from tesserae.retrieval.hybrid import _confined_source

        root = self.work
        cache: Dict[str, str] = {}

        def source_of(hit: MabHit) -> str:
            if root is None:
                return ""
            return _confined_source(hit.source_path, root, cache)[
                :EVIDENCE_SOURCE_CHARS]

        # Who gets pasted, decided before anything is rendered. Anchors first
        # and unconditionally — that ordering is what makes this a superset of
        # the rule it replaces — then the rest until the extra budget runs out.
        # A document is admitted WHOLE OR NOT AT ALL: a session truncated
        # mid-way is the ranked-but-unanswerable failure EVIDENCE_SOURCE_CHARS
        # exists to prevent. One that does not fit is skipped rather than
        # ending the loop, so leftover budget can still buy a smaller one
        # further down the ranking.
        chosen: set = set()
        for hit in hits:
            if hit.is_document_anchor and source_of(hit):
                chosen.add(hit.source_path)
        budget = EVIDENCE_EXTRA_SOURCE_CHARS
        for hit in hits:
            source = source_of(hit)
            if source and hit.source_path not in chosen and len(source) <= budget:
                chosen.add(hit.source_path)
                budget -= len(source)

        spent: set = set()
        evidence: List[str] = []
        for hit in hits:
            source = source_of(hit)
            raw = ""
            if source and hit.source_path in chosen and hit.source_path not in spent:
                raw = source
                spent.add(hit.source_path)
            head = hit.text
            stamp = session_date(source)
            if stamp:
                head = f"{head} — session date: {stamp}"
            evidence.append(f"{head}\n{raw}" if raw else head)
        return evidence

    def documents_of(self, hits: Sequence[MabHit]) -> List[int]:
        """The session NUMBERS behind ``hits``, ranked and de-duplicated.

        Two hits from one session are one document at their FIRST rank: a node
        and its neighbour are not two pieces of evidence about where the answer
        lives. So a full ``k`` hits can yield fewer than ``k`` documents, and
        that is the budget doing its job rather than a shortfall to fix.

        **A LOWER BOUND on what the memory retrieved, and the report says so.**
        A node keeps one ``source_path``, and canonicalization keeps the
        canonical node's when it collapses a concept extracted from many
        sessions — so a concept mentioned in twenty sessions points at one of
        them. Hits that map to no staged document are counted in
        :attr:`n_unmapped_hits` and dropped, never resolved to a nearby index:
        a fabricated document number scores better than the honest one and
        means nothing.
        """
        documents: List[int] = []
        for hit in hits:
            index = document_index(hit.source_path)
            if index is None:
                self.n_unmapped_hits += 1
                continue
            if index not in documents:
                documents.append(index)
        return documents

    def search_documents(self, question: str, *, k: int) -> List[int]:
        """:meth:`documents_of` of :meth:`query_hits`. One search, both answers."""
        return self.documents_of(self.query_hits(question, k=k))


__all__ = [
    "EVIDENCE_EXTRA_SOURCE_CHARS",
    "EVIDENCE_PACK_CHARS",
    "EVIDENCE_RECEIPT_CHARS",
    "EVIDENCE_SOURCE_CHARS",
    "PROTOCOL_BACKBONE",
    "PROTOCOL_CONTROLS",
    "PROTOCOL_DATASET_REVISION",
    "PROTOCOL_JUDGE",
    "PROTOCOL_JUDGE_RUNS",
    "PROTOCOL_JUDGE_TEMPERATURE",
    "RECEIPT_TURNS_PER_FACT",
    "RECEIPT_WINDOW",
    "RECEIPT_YIELD_ORDER",
    "TURN_EMIT_WINDOW",
    "TURN_LANES",
    "TURN_SCORE_WINDOW",
    "TURN_WEIGHTS",
    "UNFIXED",
    "Control",
    "IngestResult",
    "LocomoMemory",
    "RefusedToCompileInRepo",
    "document_name",
    "guard_work_dir",
    "protocol_blockers",
    "render_session",
    "session_date",
]

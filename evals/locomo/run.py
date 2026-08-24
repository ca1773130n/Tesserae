"""Runner for LoCoMo: estimate, stage, retrieve, answer, judge, report.

    # what would it cost? prints the banner and stops
    uv run python -m evals.locomo.run --conversations conv-26

    # stage the corpus and stop — no compile, no LLM, no network
    uv run python -m evals.locomo.run --conversations conv-26 --stage-only \
        --work ~/.blackhole/Tesserae/locomo/work

    # recall@{1,2,3,5,10} and MRR of the gold session, against the random floor.
    # No backbone, no judge, no quota — and reproducible to the last decimal.
    uv run python -m evals.locomo.run --conversations conv-26 \
        --arms bm25,dense --retrieval-only

    # re-grade a saved answers file with a different judge. Offline for the
    # deterministic one; this is the whole point of the judge boundary.
    uv run python -m evals.locomo.run --score answers.json --judge deterministic

Four things stand between an invocation and a bill, in the order they fire:

1. **CI.** ``CI`` in the environment prints SKIP and exits 0 whatever was asked
   for. This must never run there: it compiles ten conversations and grades
   1,986 questions.
2. **The cost banner**, in the units this phase actually measured — documents,
   extraction calls, questions and characters. Not dollars: no LLM call has been
   made against this corpus, so a token figure would be a guess wearing a
   measurement's clothes.
3. **Explicit consent to spend.** Anything reaching an LLM refuses without
   ``--i-know-this-costs-money`` and then asks for a typed confirmation unless
   ``--yes``.
4. **Prerequisites**, on ``evals/qa/run_qa_eval.py``'s model: a missing dataset,
   a work directory inside the repo, or an unknown arm prints ``SKIP: <what>``
   plus the command that fixes it and exits 0.

And one thing stands between a measured pass and a report: **the canary**. Before
any question is answered, the backbone answers a question whose answer is in the
evidence it is handed, and the judge grades a right answer and a wrong one. Both
must come back right. A dead provider chain returns None, which becomes "",
which reads as a refusal — and on LoCoMo's adversarial category a refusal is the
gold answer, so a wholly broken system scores 446 of 446 there. That failure has
to be impossible to reach, not merely unlikely.

The report carries no timestamps: the same answers in must produce the same
bytes out.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..lme_mab.adapter import RefusedToCompileInRepo
from ..lme_mab.baselines import LOCAL_EMBEDDING_PREFER, DenseArm, LexicalArm
from ..qa.run_qa_eval import Skip, _num, _rate, _table
from .adapter import (
    DEFAULT_OVERFETCH,
    DEFAULT_SOURCE_CAP,
    DEFAULT_UBIQUITY_DF_RATIO,
    EVIDENCE_EXTRA_SOURCE_CHARS,
    EVIDENCE_PACK_CHARS,
    EVIDENCE_RECEIPT_CHARS,
    EVIDENCE_SOURCE_CHARS,
    PROTOCOL_BACKBONE,
    PROTOCOL_CONTROLS,
    PROTOCOL_JUDGE,
    PROTOCOL_JUDGE_RUNS,
    RECEIPT_WINDOW,
    RERANK_MAX_LENGTH,
    RERANK_OVERFETCH,
    TURN_EMIT_WINDOW,
    TURN_POOL_K,
    TURN_SCORE_WINDOW,
    TURN_SESSION_CAP,
    IngestResult,
    LocomoMemory,
    guard_work_dir,
    protocol_blockers,
    render_session,
)
from .dataset import (
    ADVERSARIAL_CATEGORY,
    CATEGORY_NAMES,
    JUDGED_CATEGORIES,
    Conversation,
    dataset_revision,
    load_conversations,
    select_conversations,
)
from .judge import (
    CANARY_GOLD,
    CANARY_QUESTION,
    DeadBackbone,
    DeadJudge,
    Judge,
    build_judge,
)
from .retrieval import (
    NOT_COMPARABLE,
    PROTOCOL_KS,
    GoldAlignment,
    align_gold,
    alignment_summary,
    require_ks,
    retrieval_rows,
    score_at_ks,
)
from .scoring import (
    GradedRow,
    decompose,
    gap_decomposition,
    grade,
    question_key,
    replicate_spread,
)

#: Where the dataset is expected. Outside the repo — it is 1.3MB of somebody
#: else's benchmark and does not belong in this checkout.
DEFAULT_DATA = (
    Path.home() / ".blackhole" / "Tesserae" / "2026-08-21" / "benchmarks"
    / "locomo" / "data" / "locomo10.json"
)
DEFAULT_WORK = Path.home() / ".blackhole" / "Tesserae" / "locomo" / "work"
#: No date in the path. A default output path that moves at midnight is a wall
#: clock in a harness held to byte-identical re-runs.
DEFAULT_REPORT = Path.home() / ".blackhole" / "Tesserae" / "locomo" / "report.md"

#: The memory systems ``--arms`` accepts, in the order the report prints them.
#: Exactly one of them spends: ``tesserae`` compiles a corpus and answers with
#: an LLM, while ``bm25`` and ``dense`` are arithmetic over the same staged
#: bytes. That split is the whole reason the flag exists.
ARMS = ("tesserae", "bm25", "dense")
_BASELINE_ARMS = {"bm25": LexicalArm, "dense": DenseArm}

#: The answer shape asked for. Short spans, because exact match and token F1 are
#: computed over the whole answer string and cited prose scores near zero
#: against a one-word gold however right it is.
ANSWER_SHAPE = "short-span"

#: The answering instruction. Modelled on the PAPER's — "write an answer in the
#: form of a short phrase" — and NOT on the protocol that forbids abstention.
#: That protocol's answerer is told never to say "not mentioned", which on a
#: benchmark whose adversarial category rewards exactly that would be scoring a
#: system for following an instruction rather than for remembering.
#:
#: THE DECLINING PHRASE IS "Not mentioned." AND THAT IS NOT COSMETIC.
#: The reference grader accepts exactly two phrases — "no information available"
#: and "not mentioned" (:data:`evals.locomo.judge._REFERENCE_ABSTENTION`).
#: ``evals.qa.scorer.is_refusal`` accepts all three, "I don't know" included.
#: So an answerer told to say "I don't know" abstains correctly under OUR rule
#: and WRONGLY under the published one: measured on conv-26's 141 adversarial
#: questions, the same answers scored 66.7% ours and 0.0% theirs. An entire
#: category turned over on a phrasing choice that carries no meaning.
#:
#: Picking a phrase both rules accept costs nothing and removes the artifact.
#: It is NOT teaching to the test: the model still decides WHETHER to abstain,
#: which is the thing being measured; only the word it declines in is fixed, and
#: it is fixed to the benchmark's own vocabulary rather than to ours.
#: ABSTENTION IS FOR UNSUPPORTED, NOT FOR UNSTATED. An earlier wording asked for
#: "the shortest exact answer ... use exact words from the evidence" and to
#: decline when "the evidence does not contain the answer". The model obeyed,
#: and refused 21 of 21 open-domain questions whose gold session it had
#: retrieved — 100%, which is a categorical failure and never a quality one.
#:
#: LoCoMo's open-domain category is INFERENCE: "Would Caroline likely have
#: Dr. Seuss books?" -> "Yes, since she collects classic children's books". The
#: answer is entailed by the evidence and appears nowhere in it verbatim, so an
#: extraction-only instruction cannot answer the category at all. Measured
#: across every answerable category, 111 of 420 questions (26%) were refused
#: with the gold session retrieved — a synthesis failure, not a retrieval one,
#: and open-domain was the whole of one end of it.
#:
#: This is not loosening the guard. Reasoning FROM retrieved evidence is the
#: task; abstaining when the evidence supports no answer is still required, and
#: the adversarial category still rewards it.
#:
#: A WHEN QUESTION WANTS A CALENDAR DATE, AND "Prefer the evidence's own words"
#: WAS TELLING THE MODEL OTHERWISE. Every evidence item now carries the date of
#: the session it came from (:func:`evals.locomo.adapter.session_date`), so the
#: anchor a relative expression needs is in the prompt; without a rule naming it,
#: the model copies the deixis instead. Measured on the 45 wrong answers of the
#: 2026-08-21 conv-26 run, 13 (28.9%) — the largest single class of wrong answer
#: — are a relative expression where the gold is a date: "Yesterday" against
#: "2 July 2023", "Last week", "the prior weekend". Three of those 13 already had
#: the session's own ``Chat Time`` line pasted into the prompt and copied the
#: deixis anyway, which is what makes this a prompt defect and not only an
#: evidence one. The rule is narrow on purpose: it fires on time, it does not
#: license paraphrase anywhere else, and it does not touch abstention.
#: The half of the prompt that is about FORM and nothing else: what shape an
#: answer takes, and how a time is written. 672 characters, and every branch
#: below carries it verbatim — the Modal Gate splits the ABSTENTION rule, never
#: the formatting rule, so no arm can win by being told to answer more tersely.
_ANSWER_FORMAT_RULES = (
    "You answer questions about a long conversation using the evidence given. "
    "Each evidence item is stamped with the date of the session it came from. "
    "Reply with the shortest answer that the evidence supports — a name, a "
    "date, a number, yes/no, or a short phrase — and nothing else. Prefer the "
    "evidence's own words for facts it states outright, and reason from it when "
    "the question asks what is likely, implied, or would follow. When the answer "
    "is a time, give a calendar date: resolve relative expressions such as "
    "\"yesterday\", \"last week\" or \"the other day\" against the session date "
    "of the evidence item that states them, and never answer with the relative "
    "expression itself. "
)

#: The abstention rule the shipped prompt carries: BOTH branches, delivered to
#: every question. 776 characters. Unchanged, and still what runs unless
#: ``--modal-gate`` is passed — an A/B on this text is in flight and a run that
#: silently answered under a different rule would corrupt it.
_BOTH_BRANCHES_RULE = (
    # The refusal bar, cut by WHAT IS ASKED FOR rather than by how much evidence
    # what cost open-domain 27 points. Measured on conv-26 under the published
    # gpt-4o-mini grader: category 3 scored 0.500, and the failures were not
    # wrong answers but "Not mentioned." against gold like "Likely no; though
    # she likes reading, she wants to be a counselor" and "Somewhat, but not
    # extremely religious". The old single sentence -- "if the evidence
    # supports no answer at all" -- was read as "does not STATE it", so a
    # question answerable only by inference read as unanswerable.
    #
    # The gold answers for that category are themselves hedged, which is the
    # shape this asks for. What is NOT relaxed is the adversarial case: on
    # category 5 declining IS the gold answer, 446 questions of the benchmark
    # turn on it, and those ask about subjects the corpus never raises at all --
    # which is exactly the first case below and still refuses.
    "Two kinds of question need opposite treatment, and the difference is what "
    "is being ASKED FOR, not how much evidence there is. A question about what "
    "someone is LIKE -- their character, beliefs, preferences, or what they "
    "would probably do -- is answerable from how they have behaved, so answer "
    "it hedged as far as the evidence warrants (\"Likely no\", \"Somewhat\", "
    "\"Probably a teacher\") and never decline it. A question that asserts a "
    "SPECIFIC EVENT, object or fact -- what someone said about X, when they "
    "did Y, which Z they bought -- needs the evidence to establish that event; "
    "related material about the same people does not establish it. If the "
    "evidence supports no answer at all, reply exactly: Not mentioned."
)

#: The shipped prompt: form, then both abstention branches. 1,448 characters,
#: assembled from the two constants above rather than duplicated, so the head
#: the Modal Gate shares with it cannot drift away from it in a later edit.
_SYSTEM_PROMPT = _ANSWER_FORMAT_RULES + _BOTH_BRANCHES_RULE


# --------------------------------------------------------------------------
# The Deliberation Field — reason in a discarded key, answer in a declared one
#
# Opt-in through ``--deliberate``. Absent it, :data:`_ANSWER_FORMAT_RULES` above
# is the head every question is answered under, byte for byte. This replaces the
# FORMATTING half only; every abstention rule below is appended unchanged, so a
# deliberate run and a shipped run refuse on identical terms and this measures
# form and never the refusal bar.
# --------------------------------------------------------------------------

#: The alternative head: name the two output keys, then two CONTENT rules in
#: place of "the shortest answer ... and nothing else".
#:
#: THREE CHANGES, and it is worth saying what is NOT here.
#:
#: 1. IT NAMES THE KEYS. ``tesserae.llm_json._stitch_json_prompt`` sends only
#:    "Respond with valid JSON only ... Schema name: locomo_answer" — it never
#:    names a key — while :func:`build_backbone` reads ``payload["answer"]``.
#:    The model was guessing. A 24-call raw probe at fan-out prompt size found 6
#:    shape failures (25%) and ZERO transport failures; in the paired A/B, 8 of
#:    76 shipped calls needed shape recovery against 0 of 76 here (one-sided
#:    Fisher p=0.0032).
#: 2. TWO CONTENT RULES. Be specific (name the thing, not its category); give
#:    every item a list-, count- or set-shaped answer needs. 66.3% of multi-hop
#:    golds are list-shaped and multi-hop is our weakest gradeable category.
#: 3. A DISCARDED ``reasoning`` KEY. Free-form, mean 32 words, never sent to the
#:    judge — Tam et al. (EMNLP 2024: format restriction degrades reasoning)
#:    obtained without lengthening the graded string. It is OUTPUT, so it costs
#:    nothing against the evidence budget.
#:
#: NOT HERE, deliberately: any lifting of an output-length cap. The premise that
#: this arm sits in "the 4.5-word configuration" does not survive our own data —
#: our answers average 4.67 words against LoCoMo's gold at 4.89, accuracy is
#: flat across answer-length buckets, and the WITHIN-arm r(answer_words,
#: correct) is +0.071, not the between-system 0.60. The measured null-padding
#: control says the judge's length channel is ~0 on our judge and our data.
#:
#: ALSO NOT HERE, and this one was tried and rejected: a date-granularity
#: clause. "Give the date to the granularity the evidence fixes" cost temporal
#: -0.071 — ``23 August 2023`` became ``The week of 21 August 2023`` against
#: gold "The week of 23 August 2023". Temporal answers correctly 0.938 of the
#: time already; there was nothing to win and a correct row to lose. The shipped
#: date rule below is the shipped one, verbatim.
_ANSWER_FORMAT_RULES_V2 = (
    # Sentences 1-2: the shipped head's opening, kept to the word.
    "You answer questions about a long conversation using the evidence given. "
    "Each evidence item is stamped with the date of the session it came from. "
    # THE OUTPUT CONTRACT, named rather than guessed at.
    "Reply with a JSON object carrying exactly two keys. \"reasoning\" is "
    "yours: work through the evidence there in as many words as you need. "
    "\"answer\" is the only key that is read, and it carries the answer alone "
    "— a name, a date, a number, yes/no, or a short phrase — with no working "
    "and no restatement of the question. "
    # CONTENT RULE ONE: specificity.
    "In \"answer\", be as specific as the evidence allows: name the place, "
    "person, object or work rather than the category it belongs to. "
    # CONTENT RULE TWO: enumeration.
    "Give every item the evidence supports: when the answer is a list, a count "
    "or a set, none of it may be left out. "
    # The shipped head's inference and time clauses, kept to the word.
    "Prefer the evidence's own words for facts it states outright, and reason "
    "from it when the question asks what is likely, implied, or would follow. "
    "When the answer is a time, give a calendar date: resolve relative "
    "expressions such as \"yesterday\", \"last week\" or \"the other day\" "
    "against the session date of the evidence item that states them, and never "
    "answer with the relative expression itself. "
)

#: The deliberate head under each abstention rule, assembled the same way the
#: shipped three are — so the Modal Gate branches inherit the new head instead
#: of silently reverting to the old one when both flags are on.
_SYSTEM_PROMPT_V2 = _ANSWER_FORMAT_RULES_V2 + _BOTH_BRANCHES_RULE


# --------------------------------------------------------------------------
# The Modal Gate — two abstention rules, one selected per question BEFORE
# retrieval, neither containing the other's text
#
# Opt-in through ``--modal-gate``. Absent it, :data:`_SYSTEM_PROMPT` above is
# what every question is answered under, byte for byte.
#
# THE DIAGNOSIS. Measured on the gpt-4o-mini-graded fan-out rows of the
# 2026-08-23 conv-26 run: of 26 open-domain rows, 13 are correct, 11 are the
# literal string "Not mentioned.", and exactly 2 are answered-but-wrong. When
# the model ANSWERS a category-3 question it is right 13/15 = 0.867. The whole
# deficit is refusal, not reasoning — and the golds it refuses are themselves
# hedged verdicts ("Likely no, she does not refer to herself as part of it",
# "Somewhat, but not extremely religious").
#
# WHY A GATE AND NOT A SOFTER RULE. The failed edit gated on how much the
# evidence "bears on" the question. Evidence sufficiency is an axis BOTH
# categories sit low on — an open-domain question has evidence that implies but
# does not state the answer, an adversarial question has topically related
# evidence that supports nothing — so any threshold on it moves both classes the
# same way, which is exactly what was measured: open-domain refusals 54% -> 0%
# AND adversarial 72% -> 49%, +7 questions against -11. Modality is orthogonal
# BY CONSTRUCTION: it is a property of what is ASKED, fixed before a document is
# retrieved, and it cannot move when retrieval quality changes. The dispositional
# instruction is not softened for adversarial questions; it is never shown to
# them.
#
# AND IT DOES NOT WORK. The reasoning above is sound and the measurement refutes
# it anyway, which is why the flag ships OFF and this paragraph is longer than
# the argument it corrects. Measured on conv-26, paired, identical frozen
# evidence, 133 scored rows plus 20 empty-reply retries:
#
#   open-domain (cat 3, n=13)   refused 8.3% -> 0.0%    +1 question
#   adversarial (cat 5, n=47)   refused 68.4% -> 59.6%  -4 questions
#
# Under every reading the gate lands below the 72% adversarial bar it was built
# to protect: paired 68.4% -> 57.9%, unpaired 68.4% -> 59.6%, empties-counted-as
# -refusals 74.5% -> 59.6%. One-sided exact McNemar on the 4:0 discordant pairs
# is p=0.0625 — short of significance on ONE replicate, and stated here rather
# than rounded, because the point estimate is negative under every reading and a
# second replicate would not change the decision.
#
# THE CAUSE, from a disclosure control of 13 extra calls: a category-3 question
# answered under :data:`_EVENT_SYSTEM` refuses 41.7% of the time, against 8.3%
# under the both-branches prompt and 0.0% under :data:`_DISPOSITIONAL_SYSTEM`.
# The event branch is worse calibrated than the prompt it replaced, so every
# routing error costs more than a correct route saves. Modality being orthogonal
# to evidence sufficiency does not help when one of the two branches is a worse
# instruction than the undivided one.
#
# Kept, off, and documented rather than deleted: the diagnosis above (the whole
# category-3 deficit is refusal, and a model that answers is right 13 of 15) is
# still the correct reading, and the next attempt should fix the EVENT branch's
# calibration rather than re-derive the routing.
#
# WHAT IT IS NOT. Two entity-based gates were tried and FALSIFIED on this
# corpus, and neither should be proposed again without new evidence. Share of a
# question's content words present in raw conv-26 text: adversarial mean 0.874
# with 53.2% fully present, single-hop mean 0.878 with 51.4% fully present.
# ADVERSARIAL QUESTIONS HAVE MORE OF THEIR VOCABULARY IN THE CORPUS THAN
# ANSWERABLE ONES DO — LoCoMo builds them by recombining PRESENT entities into
# events that never happened. A retrieval-confidence gate is unattractive for
# the same reason from the other side: RippleMem measures first-hop confidence
# as actively misleading on exactly these questions.
# --------------------------------------------------------------------------

#: The modals that mark a DISPOSITIONAL question, as one word-boundary
#: alternation over the raw question string.
#:
#: SIX TERMS, AND THE FRONTIER IS EXACTLY HERE. Measured on all 1,986 LoCoMo
#: questions and then re-measured on the nine conversations HELD OUT from the
#: rule's design (conv-26 excluded): 0 of 399 held-out adversarial questions and
#: 0 of 1,305 held-out category-1/2/4 questions route dispositional. Adding
#: {attributes, personality, traits, describe} raises category-3 recall 0.396 ->
#: 0.406 and breaks leakage from 0.0000 to 0.0224 adversarial and 0.0166 on
#: 1/2/4. **Do not widen this for recall.** The design's entire claim is that
#: the two populations are lexically DISJOINT on this feature, not that they
#: rarely overlap, and a single leaked adversarial question falsifies it.
#:
#: Computed on the QUESTION STRING ALONE, before retrieval, which is what makes
#: it orthogonal to evidence sufficiency and free to audit: every branch can be
#: checked over the whole benchmark without an LLM call.
_DISPOSITIONAL_MODALS = re.compile(
    r"\b(?:would|might|likely|probably|be considered|prefer)\b", re.I)


def dispositional_question(question: str) -> bool:
    """Does ``question`` ask what someone WOULD do or IS LIKE?

    Public because the falsifier is public: the router is validated by building
    prompts for all ten conversations and checking that no category-5 question
    lands here, and that check must be runnable without constructing a backbone
    or spending a call.
    """
    return bool(_DISPOSITIONAL_MODALS.search(question or ""))


#: The abstention rule for a DISPOSITIONAL question. 
#:
#: IT CONTAINS NO ABSTENTION STRING AT ALL, and that literalness is the
#: mechanism rather than a stylistic choice. Abstention Inflation
#: (arXiv:2507.16199v6) finds the inflation is STRUCTURAL, not semantic — the
#: presence of a decline option raises declining, whatever it is worded as — so
#: only absence works. There is no "Not mentioned.", no "refuse", no "decline",
#: no "insufficient" below, deliberately.
#:
#: THE SECOND SENTENCE IS THE ONE THAT CONVERTS THE REFUSALS. Read the golds it
#: is written against: "Would Melanie be considered a member of the LGBTQ
#: community?" -> "Likely no, she does not refer to herself as part of it".
#: The corpus never says so, and that ABSENCE IS THE ANSWER. Every one of the
#: five questions refused in both replicates has a short hedged verdict as gold.
#: This instruction cannot leak to the adversarial category because 0 of its 446
#: questions reach this branch.
_DISPOSITIONAL_RULE = (
    # SENTENCE ONE is the shipped rule's dispositional half, kept to the word
    # apart from "and never decline it", which is dropped for the reason above:
    # an abstention token in this branch is the thing the branch exists to
    # remove. Keeping the rest verbatim is what stops a gate-on/gate-off
    # comparison measuring a rewrite instead of a routing decision.
    "This question is about what someone is LIKE -- their character, beliefs, "
    "preferences, or what they would probably do -- which is answerable from "
    "how they have behaved, so answer it hedged as far as the evidence warrants "
    "(\"Likely no\", \"Somewhat\", \"Probably a teacher\"). "
    # SENTENCE TWO is the addition, and it is the whole mechanism.
    "What the evidence never shows is itself informative: when nothing in it "
    "shows the trait, the habit or the belief being asked about, that supports "
    "a negative verdict such as \"Likely no\", which is an answer and is often "
    "the right one."
)

#: The abstention rule for an EVENT question — the shipped rule's second half,
#: with the dispositional carve-out DELETED and "Not mentioned." kept exactly.
#:
#: THE DELETION IS A POSITIVE MECHANISM, not merely tidying. The carve-out is
#: measurably leaking onto adversarial questions today. Splitting conv-26's
#: adversarial rows by whether the question asks for a feeling, meaning, motive
#: or evaluation: interpretive-shaped refuse at 0.450 (n=20) against 0.611 for
#: factual-shaped (n=72), and the 11 interpretive rows that answered returned
#: exactly the hedged inferences the carve-out licenses — "Tiny and in awe of
#: the universe" for "How did Caroline feel while watching the meteor shower?".
#: None of those carry a modal, so all of them route here, where that text no
#: longer exists.
#:
#: THE REFUSAL SENTENCE IS COPIED FROM :data:`_BOTH_BRANCHES_RULE`, CHARACTER
#: FOR CHARACTER, and ``test_the_event_branch_refuses_in_the_shipped_words``
#: pins it there. That phrase is the one BOTH the published grader's abstention
#: rule and ours accept, and an earlier run turned an entire category over on
#: the choice — the same answers scored 66.7% under our rule and 0.0% under the
#: published one. It is ALSO the control: an EVENT branch that refuses in
#: different words than the arm it is compared against is not a controlled
#: comparison, it is a prompt rewrite wearing a router's clothes. That prompt is
#: under active A/B on this branch and its wording has already moved once
#: mid-edit; if the pin goes red, update this constant to match rather than
#: relaxing the test.
_EVENT_RULE = (
    # The shipped rule's event half, kept to the word, minus the sentence that
    # introduced the pair ("Two kinds of question need opposite treatment...")
    # — there is only one kind of question in this branch.
    "This question asserts a SPECIFIC EVENT, object or fact -- what someone "
    "said about X, when they did Y, which Z they bought -- and needs the "
    "evidence to establish that event; related material about the same people "
    "does not establish it. If the evidence supports no answer at all, reply "
    "exactly: Not mentioned."
)

#: The two prompts the gate chooses between. Each is the shared formatting head
#: plus ONE rule, so neither carries the other's text — which is the whole
#: design — and each is ~400 characters SHORTER than the single prompt that
#: delivers both. The gate is therefore token-neutral to marginally
#: token-negative; it neither funds nor obstructs the packing budget, and no
#: writeup may credit it with either.
_DISPOSITIONAL_SYSTEM = _ANSWER_FORMAT_RULES + _DISPOSITIONAL_RULE
_EVENT_SYSTEM = _ANSWER_FORMAT_RULES + _EVENT_RULE

#: The same two branches over the deliberate head. The abstention rules are the
#: SAME OBJECTS — ``--deliberate`` changes the formatting half and nothing else,
#: which is what lets the two flags be measured independently and composed.
_DISPOSITIONAL_SYSTEM_V2 = _ANSWER_FORMAT_RULES_V2 + _DISPOSITIONAL_RULE
_EVENT_SYSTEM_V2 = _ANSWER_FORMAT_RULES_V2 + _EVENT_RULE


def system_for(question: str, *, modal_gate: bool, deliberate: bool = False) -> str:
    """The system prompt ``question`` is answered under.

    ``modal_gate=False`` with ``deliberate=False`` returns :data:`_SYSTEM_PROMPT`
    for every question, which is the shipped behaviour and the only behaviour
    any run before these flags had.

    The two flags are ORTHOGONAL and compose: ``deliberate`` picks the head
    (:data:`_ANSWER_FORMAT_RULES` or :data:`_ANSWER_FORMAT_RULES_V2`),
    ``modal_gate`` picks the abstention rule appended to it. That is deliberate
    — an arm that changed both halves at once could not attribute its own delta,
    and the abstention rules are shared objects here rather than copies so they
    cannot drift apart per head.

    KNOWN CONFOUND, declared rather than buried: LoCoMo-Plus (arXiv:2602.10715)
    finds that revealing the question TYPE systematically inflates LoCoMo
    scores, and this router does exactly that implicitly. Part of any category
    gain is prompt adaptation rather than memory quality. The owed control is
    running each branch's prompt on the other branch's questions to size the
    disclosure effect, and it must be run before the gain is described as a
    memory result.

    GENERALISATION, also declared: the router reaches 13 of 13 open-domain
    questions on conv-26 but only 25 of 83 (0.301) on the held-out nine. About
    60% of LoCoMo category 3 is not dispositional at all — "What console does
    Nate own?" is a factual question the benchmark happens to label 3 — so
    across ten conversations the branch touches roughly 29 of 96 open-domain
    questions and the measured gain will be far smaller than conv-26 implies.
    conv-26 is an unusually favourable slice and must not be sold as
    representative.
    """
    if not modal_gate:
        return _SYSTEM_PROMPT_V2 if deliberate else _SYSTEM_PROMPT
    if dispositional_question(question):
        return _DISPOSITIONAL_SYSTEM_V2 if deliberate else _DISPOSITIONAL_SYSTEM
    return _EVENT_SYSTEM_V2 if deliberate else _EVENT_SYSTEM

#: The canary's planted evidence. The question and the expected token are
#: :mod:`evals.locomo.judge`'s — ONE owner for the pair, because a backbone
#: canary asking one question and a judge canary grading another would drift
#: apart, and the drift would show up as a canary that passes on a machine where
#: the two disagree.
CANARY_EVIDENCE = (
    f"Priya said, \"I finally bought the bicycle — the {CANARY_GOLD} one, in "
    f"March.\"",
)


# --------------------------------------------------------------------------
# Cost, in the units this phase measured
# --------------------------------------------------------------------------


def cost_banner(conversations: Sequence[Conversation], *, replicates: int) -> str:
    """The bill, in documents, calls, questions and characters.

    Deliberately not in tokens or dollars. No LLM call has been made against
    this corpus in this phase, so a token estimate would be an inference
    presented in the same shape as a measurement — which is the specific thing
    the rest of this package refuses to do.
    """
    documents = sum(len(c.sessions) for c in conversations)
    questions = sum(len(c.questions) for c in conversations)
    answerable = sum(1 for c in conversations for q in c.questions
                     if q.category in JUDGED_CATEGORIES)
    chars = sum(c.chars for c in conversations)
    return "\n".join([
        "─" * 72,
        f"LoCoMo — ESTIMATED COST for {len(conversations)} conversation(s), "
        f"before anything runs",
        "─" * 72,
        f"  corpus            {documents:>8,} session documents "
        f"({chars:,} chars of dialogue)",
        f"  compile           {documents:>8,} extraction calls, one per document, "
        f"none chunked",
        f"  questions         {questions:>8,} total, {answerable:,} with a gold "
        f"answer to grade",
        f"  answering         {questions * replicates:>8,} backbone calls "
        f"({replicates} replicate(s))",
        f"  judging           {answerable * replicates:>8,} judge calls if an LLM "
        f"judge is used",
        "",
        "  Counts are measured from the dataset itself. Per-call token cost and "
        "wall-clock",
        "  are NOT measured for this corpus and are not estimated here.",
        "─" * 72,
    ])


# --------------------------------------------------------------------------
# Prerequisites
# --------------------------------------------------------------------------


def parse_arms(value: str) -> List[str]:
    """``--arms`` as a de-duplicated list in :data:`ARMS` order.

    Canonical order rather than the order typed, so the report is
    byte-identical for the same run. An unrecognised name refuses rather than
    being ignored: silently dropping a typo would print a one-row table that
    looks like a comparison.
    """
    names = {name.strip().lower() for name in str(value).split(",") if name.strip()}
    unknown = sorted(name for name in names if name not in ARMS)
    if unknown:
        raise Skip(f"no such arm(s): {', '.join(unknown)}",
                   f"--arms takes a comma list of {', '.join(ARMS)}")
    if not names:
        raise Skip("--arms is empty, so there is nothing to measure",
                   f"--arms {','.join(ARMS)}")
    return [name for name in ARMS if name in names]


def require_data(path: Path) -> Path:
    if not path.is_file():
        raise Skip(
            f"locomo10.json not found at {path}",
            "clone github.com/snap-research/locomo into a scratch directory "
            "and pass --data <checkout>/data/locomo10.json; it is somebody "
            "else's benchmark and is deliberately not in this repo",
        )
    return path


def require_work_dir(work: Path) -> Path:
    try:
        return guard_work_dir(work)
    except RefusedToCompileInRepo as exc:
        raise Skip(str(exc), "pass --work ~/.blackhole/Tesserae/locomo/work") from exc


def select_or_skip(conversations: Sequence[Conversation],
                   names: Optional[Sequence[str]]) -> List[Conversation]:
    try:
        chosen = select_conversations(conversations, names)
    except KeyError as exc:
        raise Skip(str(exc).strip("'"),
                   "drop --conversations to run them all, or pass an id that "
                   "exists") from exc
    if not chosen:
        raise Skip("no conversations selected", "drop --conversations")
    return chosen


# --------------------------------------------------------------------------
# The backbone, and the canary in front of it
# --------------------------------------------------------------------------

#: The longest raw non-JSON reply the extractor will accept as an answer.
#:
#: LoCoMo's gold answers run 4.89 words / median 3.0, and the two bare-prose
#: replies the raw probe found were ``LGBTQ+ individuals`` and ``counseling or
#: mental health``. 200 characters admits a sentence and refuses a reasoning
#: trace — which matters because the judge is told to "be generous ... as long
#: as it touches on the same topic" and would accept a trace exactly as the
#: 62.81% false-accept floor predicts. Belt-and-braces on top of the hard gate:
#: this rung is switched OFF entirely whenever ``deliberate`` is on.
_BARE_PROSE_MAX_CHARS = 200

#: Per-thread record of WHICH rung of the extractor ladder produced the answer.
#:
#: ``"answer-key"`` = the contract was honoured. Everything else is a shape
#: failure the extractor recovered from, and the point of recording it is that
#: the fix must not HIDE the provider's failure rate: it is persisted per row
#: beside ``empty_replies`` so it stays a reported number.
#:
#: Thread-local, and cleared by the caller before every backbone call, for the
#: same reason ``tesserae.llm_json._LAST_FAILURE`` is: a stale note read after a
#: stub answerer would attribute one call's shape to another's.
_LAST_SHAPE = threading.local()


def _note_answer_shape(kind: Optional[str]) -> None:
    """Record which extractor rung produced this thread's answer."""
    _LAST_SHAPE.kind = kind


def last_answer_shape() -> Optional[str]:
    """``"answer-key"`` | ``"sole-string-value"`` | ``"bare-json-string"`` |
    ``"bare-prose"`` | ``"unusable"`` | ``None`` for this thread's most recent
    backbone call. ``None`` means no backbone built by :func:`build_backbone`
    ran — a stub answerer, or a row whose search failed before one was made."""
    return getattr(_LAST_SHAPE, "kind", None)


def extract_answer(payload: Any, raw: Optional[str], *,
                   deliberate: bool) -> Tuple[str, str]:
    """``(answer, shape)`` from whatever the provider actually returned.

    A LADDER, cheapest and most trustworthy rung first. Every rung below the
    first exists because the raw probe found that exact shape at fan-out prompt
    size, carrying an answer we had already paid for:

    1. ``answer`` key — the contract, and after :data:`_ANSWER_FORMAT_RULES_V2`
       names it, 76 of 76 calls in the A/B.
    2. the dict's SOLE string value — ``{"name": "Progressive"}``: right answer,
       wrong key. With ``deliberate`` on, the declared ``reasoning`` key is
       dropped from the candidates BEFORE the sole-value test, so a model that
       emits reasoning plus a misnamed answer still resolves, and a model that
       emits reasoning ALONE resolves to nothing rather than to its own trace.
    3. a bare JSON string — ``"Not mentioned."``, a correct refusal on the
       adversarial category that scored as an error because ``json.loads``
       returns a ``str`` and the old reader tested ``isinstance(payload,
       Mapping)``.
    4. short bare prose — the reply that never parsed at all. **Disabled
       whenever ``deliberate`` is on**: with a reasoning key in the contract,
       unparsed text is far likelier to be a truncated trace than an answer,
       and handing a trace to a lenient judge is the one way this change could
       manufacture a score. Fail loudly (``"unusable"``, an empty answer, a
       counted ``empty_replies``) rather than fall back to full text.

    ``""`` with shape ``"unusable"`` is the honest reading of "the system
    returned nothing" — the canary in front of the run is what stops a whole
    run of them being read as caution.
    """
    if isinstance(payload, Mapping):
        if payload.get("answer") is not None:
            return str(payload["answer"]), "answer-key"
        candidates = [(key, value) for key, value in payload.items()
                      if isinstance(value, str) and value.strip()
                      and not (deliberate and key == "reasoning")]
        if len(candidates) == 1:
            return candidates[0][1], "sole-string-value"
        return "", "unusable"
    if isinstance(payload, str) and payload.strip():
        # `parse_json_tolerant` is annotated dict|list but returns whatever
        # `json.loads` gives it, so a bare JSON string arrives here parsed.
        return payload, "bare-json-string"
    if payload is not None:
        return "", "unusable"
    text = (raw or "").strip()
    if deliberate or not text or len(text) > _BARE_PROSE_MAX_CHARS:
        return "", "unusable"
    if text.startswith(("{", "[")):
        # It tried to be JSON and failed. That is a bad generation, not prose.
        return "", "unusable"
    return text, "bare-prose"


def build_backbone(model: str, *,
                   modal_gate: bool = False,
                   deliberate: bool = False
                   ) -> Callable[[str, Sequence[str]], str]:
    """An ``(question, evidence) -> short answer`` callable on ``model``.

    A closure rather than a class so the tests pass any callable and never
    construct an LLM client.

    ``modal_gate=False`` answers every question under :data:`_SYSTEM_PROMPT`,
    which is what every run before the flag did. With it on, the system prompt
    is chosen per question by :func:`system_for` — see the Modal Gate block
    above for why the split is on the question's MODALITY and not on how much
    the evidence supports.

    ``deliberate=False`` reads ``payload["answer"]`` exactly as before; what is
    NOT conditional on the flag is :func:`extract_answer`'s ladder, which runs
    either way. That is on purpose: the recovery rungs cost no calls, recover
    answers already paid for, and their absence was measured at 12 of 304
    gradeable rows scoring zero on ``Error: the backbone returned an empty
    answer``. The flag only decides whether the prose rung is available at all.
    """
    from tesserae.llm_json import build_default_json_client

    client = build_default_json_client(model=model)
    if client is None:
        raise Skip(
            f"no LLM client available for the {model} backbone",
            "configure a provider, or run --retrieval-only, which scores "
            "recall and MRR and spends nothing",
        )

    from tesserae.llm_json import last_raw_reply

    def answer(question: str, evidence: Sequence[str]) -> str:
        numbered = "\n\n".join(f"[{i}] {text}"
                               for i, text in enumerate(evidence, start=1))
        payload = client.complete_json(
            system=system_for(question, modal_gate=modal_gate,
                              deliberate=deliberate),
            user=f"Evidence:\n{numbered}\n\nQuestion: {question}",
            schema_name="locomo_answer",
        )
        # Read the raw reply IMMEDIATELY: it is thread-local and describes only
        # the call that just returned.
        text, shape = extract_answer(payload, last_raw_reply(),
                                     deliberate=deliberate)
        _note_answer_shape(shape)
        return text

    return answer


def canary_backbone(answer_fn: Callable[[str, Sequence[str]], str]) -> int:
    """One call. The answer must contain the token planted in the evidence.

    Raises :class:`evals.locomo.judge.DeadBackbone` otherwise, and that is a
    RuntimeError rather than a :class:`Skip` on purpose: a Skip prints and exits
    0, which is right for a missing input and wrong for a backbone that is
    returning nothing. Returns the number of calls it spent, which the run
    declares as evidence that a canary ran at all.

    The check is containment and not equality, so a backbone that answers "teal"
    and one that answers "It was teal." both pass. What it catches is the whole
    class of failures that produce an empty string: a provider chain handed a
    model it does not have, an expired credential, a quota wall. Each of those
    prints ``refusal_rate 1.000`` with ``error_rate 0.000`` and reads as a
    cautious system.
    """
    try:
        answer = answer_fn(CANARY_QUESTION, list(CANARY_EVIDENCE))
    except Exception as exc:  # noqa: BLE001 — any failure here is fatal
        raise DeadBackbone(
            f"the backbone raised on the canary question: {exc!r}. Nothing was "
            f"measured; fix the provider before re-running."
        ) from exc
    if CANARY_GOLD not in str(answer or "").lower():
        raise DeadBackbone(
            f"the backbone answered {str(answer)[:120]!r} to a question whose "
            f"answer ({CANARY_GOLD!r}) was in the one piece of evidence it "
            f"was handed. A backbone returning nothing scores refusal_rate "
            f"1.000 with error_rate 0.000, which reads as caution — and on "
            f"category {ADVERSARIAL_CATEGORY}, where declining is the gold "
            f"answer, it would score perfectly. Nothing was measured."
        )
    return 1


# --------------------------------------------------------------------------
# Answering and retrieving
# --------------------------------------------------------------------------


def search_conversation(
    memory: LocomoMemory,
    conversation: Conversation,
    *,
    k: int,
    answer_k: Optional[int] = None,
    expand_evidence: bool = True,
    build_evidence: bool = True,
    progress: bool = False,
) -> Tuple[List[List[int]], List[List[str]], List[str]]:
    """Retrieve for every question ONCE. ``(documents, evidence, errors)``.

    Searching is separated from answering because it does not vary between
    replicates and generation does. Running the search again for every replicate
    would record every shortfall three times, spend three times the retrieval,
    and — worst — let the retrieval table drift between replicates of a run whose
    whole purpose is to isolate the generative variance.

    A search that raises is recorded in ``errors[i]`` and leaves that question
    with no documents and no evidence. The other questions survive: one bad
    question is not a run.

    ``build_evidence=False`` skips assembling the prompt entirely, which is what
    ``--retrieval-only`` passes: expanding a document anchor reads its session
    file off disk, and a run that will never answer has no use for the bytes.
    The RANKING is untouched either way, so recall and MRR cannot move by a
    byte between the two modes.
    """
    documents: List[List[int]] = []
    evidence: List[List[str]] = []
    errors: List[str] = []
    for index, question in enumerate(conversation.questions):
        if progress:
            print(f"[{conversation.sample_id}] search [{index + 1}/"
                  f"{len(conversation.questions)}]", file=sys.stderr)
        try:
            hits = memory.query_hits(question.question, k=k)
        except Exception as exc:  # recorded, not raised
            documents.append([])
            evidence.append([])
            errors.append(f"Error: {exc}")
            # A RETRIEVAL canary, the counterpart of the backbone one. Without
            # it, a retriever whose every search raised produced a complete,
            # exit-0, byte-reproducible report with a clean 0.000-recall table —
            # measured, on all 199 conv-26 questions. A recall of zero is a
            # publishable claim about a memory; a broken retriever is not, and
            # the report could not tell them apart.
            #
            # The first _RETRIEVAL_CANARY searches must not ALL fail. Beyond
            # that a run is scored and per-question errors are reported, because
            # a retriever that works and then degrades is a real result.
            # "The first N searches ALL failed" — not "N have failed". Both
            # lists grow on success and failure alike, so comparing their
            # lengths was always true and would have aborted a merely flaky
            # retriever on its Nth failure, whenever that came.
            if len(errors) >= _RETRIEVAL_CANARY and all(errors):
                raise RuntimeError(
                    f"retrieval canary: the first {len(errors)} searches all "
                    f"failed (last: {exc}). Refusing to score a run whose "
                    f"retriever never returned — a 0.000 recall table would be "
                    f"indistinguishable from a memory that found nothing."
                ) from exc
            continue
        documents.append(memory.documents_of(hits))
        if build_evidence:
            budget = hits if answer_k is None else hits[:answer_k]
            # The QUESTION reaches the assembly, not only the ranking. Read by
            # `--evidence-unit turn` alone, which scores every candidate turn
            # against it; the session units ignore it and their bytes are
            # unchanged.
            evidence.append(memory.answer_evidence(
                budget, expand=expand_evidence, question=question.question))
        else:
            evidence.append([])
        errors.append("")
    return documents, evidence, errors


#: What an EMPTY backbone reply is recorded as. ``Error:`` is
#: :data:`evals.qa.scorer._ERROR_PREFIX`, so this lands in the errored column and
#: in neither the correct one nor the refused one.
#:
#: A silent failure was being counted as a decision. ``is_refusal("")`` is True —
#: correctly, as a scorer predicate, because a system that returns nothing has
#: declined — but the backbone returning nothing is not the SYSTEM declining, it
#: is the harness losing a call. Measured on the 2026-08-21 conv-26 run,
#: ``gpt-5.6-luna`` returned the empty string on 11 of 199 answering calls
#: (5.5%), spread from 2,267 to 32,324 evidence characters against a non-empty
#: mean of 14,186 — no prompt-size relationship, and all 11 were filed as
#: refusals. Five of them were adversarial, where an empty string scored zero
#: under the published abstention rule while being counted as an abstention
#: under ours: the two rules disagreed about eleven rows for a reason that was
#: not a property of the memory at all.
#:
_EMPTY_ANSWER = "Error: the backbone returned an empty answer"

#: How many times an empty reply is asked again before it is recorded as one.
#:
#: This started at 0 — "recorded rather than retried, because a retry spends
#: calls to hide a defect nobody has characterised". The defect is now
#: characterised, and it is the shape a retry is FOR.
#:
#: Measured over 398 answering calls of the 2026-08-22 conv-26 run, at a mean
#: prompt of 20,798 characters: 66 empty replies (16.6%), against 11 of 199
#: (5.5%) on the 2026-08-21 run at a mean of 14,143. Three facts about those 66:
#:
#: * They do not cluster in TIME. Spread evenly across both replicates in
#:   20-question blocks, so this is not a throttle burst.
#: * They are very nearly INDEPENDENT PER CALL. 33 questions came back empty in
#:   replicate 0 and 33 in replicate 1, and only 7 in both — chance alone
#:   predicts 5.5. So it is not a property of particular prompts either.
#: * Within the run the rate rises monotonically with prompt size: 2.0% below
#:   10,534 characters, 10-14% through the middle, 22-24% above 20,000.
#:
#: An independent per-call failure at rate p becomes p^2 with one retry: 16.6%
#: -> ~2.8% expected. THAT EXPECTATION IS ARITHMETIC, NOT A MEASUREMENT — no run
#: has been scored with this constant above 0. What is measured is the three
#: facts above, and they are what justify a retry over a workaround: crippling
#: the evidence budget to keep prompts small would be tuning the memory around a
#: flaky provider.
#:
#: Nothing is hidden by it. Every row persists its own ``empty_replies`` count
#: and the run's meta sums them, so the provider's failure rate stays a reported
#: number whether or not the retry rescued the answer.
#:
#: AND THE DIAGNOSIS ABOVE IS NOW KNOWN TO BE HALF WRONG, which is worth leaving
#: in place rather than rewriting. A 24-call raw probe at fan-out prompt size
#: found 6 shape failures and ZERO transport failures: the provider ANSWERED and
#: :func:`build_backbone` could not read the shape. That is a parse contract the
#: answerer was never told, not a flaky provider — see
#: :data:`_ANSWER_FORMAT_RULES_V2` and :func:`extract_answer`, which cost no
#: calls. The retry stays because the two failures are independent and this one
#: still catches a genuinely empty reply; what changed is that it is no longer
#: the only thing standing between a paid-for answer and a zero.
_EMPTY_ANSWER_RETRIES = 1


def answer_conversation(
    conversation: Conversation,
    evidence: Sequence[Sequence[str]],
    errors: Sequence[str],
    answer_fn: Callable[[str, Sequence[str]], str],
    *,
    replicate: int,
    arm: str = "tesserae",
    evidence_content: str = "source",
    modal_gate: bool = False,
    progress: bool = True,
) -> List[Dict[str, Any]]:
    """Answer every question from evidence :func:`search_conversation` already got.

    A search error becomes the row's answer verbatim and no backbone call is
    made for it: grading a traceback spends a call to learn what the string
    already says, and ``evals.qa.scorer.is_error`` keeps it out of both the
    refusal count and the correct count.

    A backbone failure is caught per question, so one 429 does not erase the
    retrieval that had already ranked gold first for every other question. An
    EMPTY reply is such a failure and is recorded as one — see
    :data:`_EMPTY_ANSWER`.
    """
    rows: List[Dict[str, Any]] = []
    for index, question in enumerate(conversation.questions):
        if progress:
            print(f"[{conversation.sample_id}] [rep {replicate}] "
                  f"[{index + 1}/{len(conversation.questions)}] "
                  f"{question.question}", file=sys.stderr)
        items = list(evidence[index]) if index < len(evidence) else []
        failure = errors[index] if index < len(errors) else ""
        empty_replies = 0
        shape = None
        if failure:
            answer = failure
        else:
            for _ in range(_EMPTY_ANSWER_RETRIES + 1):
                # Cleared before every call, and read straight after it: the
                # note is thread-local and describes exactly one call. A stub
                # answerer writes none, which stays None and records "".
                _note_answer_shape(None)
                try:
                    answer = answer_fn(question.question, items)
                except Exception as exc:  # the backbone failed, not the search
                    answer = f"Error: {exc}"
                    shape = last_answer_shape()
                    break
                shape = last_answer_shape()
                if str(answer or "").strip():
                    break
                empty_replies += 1
                answer = _EMPTY_ANSWER
        rows.append({
            # The provider's own failure rate, persisted per question rather
            # than left in a log. 1 with an answer beside it means the retry
            # saved the question; 2 means it did not.
            "empty_replies": empty_replies,
            # WHICH rung of `extract_answer` produced the answer that shipped —
            # of the LAST call, because that is the one whose text is in
            # `answer`. "answer-key" is the contract honoured; anything else is
            # a provider shape failure the extractor recovered from, and it is
            # persisted so the fix reports that rate rather than hiding it.
            # "" means no backbone built by `build_backbone` ran.
            "answer_shape_recovery": shape or "",
            "key": question_key(question, index),
            "arm": arm,
            "replicate": replicate,
            "conversation": conversation.sample_id,
            "question_index": index,
            "question": question.question,
            "category": question.category,
            "answer": answer,
            # Which abstention rule this question was answered under, persisted
            # so the router is auditable in the answers file rather than
            # re-derivable from a regex someone has to trust. "" when the gate
            # is off, which is not the same claim as "event": one says the
            # question was never routed, the other says it was and landed there.
            "branch": (("dispositional" if dispositional_question(question.question)
                        else "event") if modal_gate else ""),
            "n_evidence": len(items),
            # What the backbone actually read, in characters. An identical
            # generative config has swung 0.043 token F1 between two runs in
            # this repo; the size of the prompt is the one thing worth
            # persisting per row when the answer itself does not reproduce.
            "evidence_chars": sum(len(text) for text in items),
            "evidence_content": evidence_content,
        })
    return rows


def retrieve_conversation(arm: Any, conversation: Conversation, *, k: int,
                          progress: bool = False) -> List[List[int]]:
    """``arm.search_documents`` for every question, in question order."""
    retrieved: List[List[int]] = []
    for index, question in enumerate(conversation.questions):
        if progress:
            print(f"[{conversation.sample_id}] [{index + 1}/"
                  f"{len(conversation.questions)}]", file=sys.stderr)
        retrieved.append(arm.search_documents(question.question, k=k))
    return retrieved


class _StagedDocument:
    """A staged session, as the baseline arms want it: ``index`` and ``render()``.

    The shim exists so the baselines index the SAME bytes the Tesserae corpus
    was staged from — ``adapter.render_session`` — rather than a second
    rendering that would make the arms disagree about what the corpus is. Its
    ``index`` is the session's own number, which is what a ``dia_id`` names and
    what gold alignment is keyed on.
    """

    __slots__ = ("index", "_body")

    def __init__(self, index: int, body: str) -> None:
        self.index = index
        self._body = body

    def render(self) -> str:
        return self._body


def staged_documents(conversation: Conversation) -> List[_StagedDocument]:
    return [_StagedDocument(s.number, render_session(s))
            for s in conversation.sessions]


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------


def _corpus_section(conversations: Sequence[Conversation],
                    ingests: Sequence[IngestResult]) -> List[str]:
    by_id = {i.conversation: i for i in ingests}
    rows = []
    for conversation in conversations:
        ingest = by_id.get(conversation.sample_id)
        rows.append([
            conversation.sample_id,
            f"{len(conversation.sessions):,}",
            f"{conversation.n_turns:,}",
            f"{conversation.chars:,}",
            f"{len(conversation.questions):,}",
            f"{sum(1 for t in (t for s in conversation.sessions for t in s.turns) if t.blip_caption):,}",
            ("yes" if ingest and ingest.compiled else
             "reused (earlier run)" if ingest and ingest.reused else
             "**staged only**" if ingest else "not staged"),
        ])
    lines = _table(
        ["conversation", "sessions (documents)", "turns", "chars", "questions",
         "captioned turns", "compiled"],
        rows,
    )
    lines += [
        "",
        "One document per **session**, and one PROJECT per conversation. The "
        "isolation is not stylistic: speaker names repeat across LoCoMo's ten "
        "conversations, so a pooled corpus would let a question about one "
        "conversation retrieve another conversation's turns about a different "
        "person of the same name — and nothing in a reported number would show "
        "it.",
        "",
        "The corpus is dialogue text plus every BLIP caption, and nothing else. "
        "`img_url`, `query`, `observation`, `session_summary` and "
        "`event_summary` are never ingested. That freeze is necessary because "
        "the reference code contains two paths that disagree about the "
        "captions, and it is recorded here because it changes every score.",
    ]
    return lines


def _alignment_section(summary: Mapping[str, int]) -> List[str]:
    lines = _table(
        ["questions", "no gold resolved", "empty evidence", "malformed evidence",
         "unparseable", "dangling ids"],
        [[f"{summary['n_questions']:,}", f"{summary['n_no_gold']:,}",
          f"{summary['n_empty_evidence']:,}", f"{summary['n_malformed']:,}",
          f"{summary['n_unparseable']:,}", f"{summary['n_dangling']:,}"]],
    )
    lines += [
        "",
        "Gold evidence is named by `dia_id`, so alignment is a dictionary "
        "lookup rather than a content signature. Every annotation this harness "
        "could not resolve is counted above rather than repaired quietly: a "
        "benchmark that fixes an answer key without saying so has changed the "
        "key.",
        "",
        "A question whose gold did not resolve is **excluded** from the "
        "retrieval metrics, not scored zero. Zero is a claim that an arm failed "
        "to retrieve something; there was nothing to retrieve.",
    ]
    return lines


def _retrieval_section(reports_by_arm: Mapping[str, Sequence[Mapping[str, Any]]]
                       ) -> List[str]:
    if not reports_by_arm:
        return ["No retrieval was scored in this run."]
    lines = [NOT_COMPARABLE, ""]
    ks = [int(r["k"]) for r in next(iter(reports_by_arm.values()))]
    rows = []
    for arm, reports in reports_by_arm.items():
        for report in reports:
            overall = report["overall"]
            floor = report.get("random_floor") or {}
            n = int(overall["n_scored"])
            rows.append([
                arm,
                str(report["k"]),
                _num(overall["recall_at_k"], n),
                _num(float(floor.get("recall_at_k") or 0.0), n),
                _num(overall["mrr"], n),
                _num(float(floor.get("mrr") or 0.0), n),
                str(n),
            ])
    lines += _table(
        ["arm", "K", "recall@K", "random floor", "MRR", "random floor", "n"],
        rows,
    )
    lines += [
        "",
        f"**Every K is printed, and the set — {', '.join(str(k) for k in ks)} — "
        f"was fixed in code before any result existed.** A LoCoMo conversation "
        f"holds only 19 to 32 sessions, so K=10 is more than half the corpus on "
        f"the smallest of them and a uniformly random ranker already scores "
        f"about 0.53 recall there. A single K would be a K that was chosen; the "
        f"floor column beside each row is what makes the rest readable.",
        "",
        "MRR is the headline of this section: it is the metric a large K cannot "
        "inflate.",
        "",
        "The published harness scores retrieval per **evidence id**, dividing by "
        "the number of ids a question lists; this scores per **gold session**, "
        "dividing by `min(|gold sessions|, K)`. The two differ on every question "
        "whose gold spans more than one session.",
    ]
    return lines


def _three_number_section(decomposition: Any) -> List[str]:
    """§4. Three numbers or none — see :mod:`evals.locomo.scoring`."""
    if not decomposition.complete:
        return [
            "**Withheld.** This report prints three numbers or none: every "
            "scorable question with refusals scoring zero, the subset every arm "
            "answered, and each arm's refusal count. One of them could not be "
            "computed, so none of them is printed — a headline without its "
            "decomposition is the failure this section exists to prevent.",
            "",
        ] + [f"- {reason}" for reason in decomposition.missing]

    arms = list(decomposition.all_questions)
    rows = []
    for arm in arms:
        all_scores = decomposition.all_questions[arm]
        lfl = decomposition.like_for_like[arm]
        rows.append([
            arm,
            str(all_scores.n),
            _rate(all_scores.accuracy, all_scores.n),
            _num(all_scores.graded_score, all_scores.n),
            str(lfl.n),
            _rate(lfl.accuracy, lfl.n),
            _num(lfl.graded_score, lfl.n),
            f"{decomposition.refusals[arm]:,}",
            f"{decomposition.errors[arm]:,}",
        ])
    lines = _table(
        ["arm", "n (all)", "accuracy (all)", "graded score (all)",
         "n (like-for-like)", "accuracy (like-for-like)",
         "graded score (like-for-like)", "refusals", "errors"],
        rows,
    )
    lines += [
        "",
        f"**(1) all** is every scorable question — categories "
        f"{', '.join(str(c) for c in JUDGED_CATEGORIES)} — with a refusal "
        f"scoring zero. It is the number this field quotes. **(2) "
        f"like-for-like** is the "
        f"{decomposition.n_like_for_like:,} of {decomposition.n_all:,} questions "
        f"that NO arm refused and NO arm errored on. **(3) refusals** is why the "
        f"two differ.",
        "",
        "A gap that lives entirely in column (1) is a gap in willingness to "
        "answer, not in memory. Measured elsewhere in this repository: a +0.077 "
        "headline gap was 72% one arm refusing and scoring zero, and its "
        "like-for-like gap was +0.021.",
    ]
    if len(arms) >= 2:
        gap_rows = []
        for other in arms[1:]:
            gap = gap_decomposition(decomposition, arms[0], other)
            if gap is None:
                continue
            share = gap.answer_rate_share
            gap_rows.append([
                f"{gap.a} − {gap.b}",
                _num(gap.gap_all, gap.n_all),
                _num(gap.gap_like_for_like, gap.n_like_for_like),
                "n/a" if share is None else f"{100.0 * share:.1f}%",
            ])
        if gap_rows:
            lines += ["", "### Where the gap comes from", ""]
            lines += _table(
                ["pair", "gap (all)", "gap (like-for-like)",
                 "share attributable to answer rate"],
                gap_rows,
            )
            lines += [
                "",
                "The last column is `1 − gap_like_for_like / gap_all`. It is "
                "`n/a` when the headline gap is zero, because the share of "
                "nothing is not a quantity.",
            ]
    return lines


def _adversarial_section(decomposition: Any) -> List[str]:
    if not decomposition.adversarial:
        return [f"No category-{ADVERSARIAL_CATEGORY} question was answered in "
                f"this run."]
    rows = []
    for arm, scores in decomposition.adversarial.items():
        reference = decomposition.adversarial_reference.get(arm)
        rows.append([
            arm,
            str(scores.n),
            _rate(scores.accuracy, scores.n),
            _rate(reference.accuracy, reference.n) if reference else "n/a",
            f"{scores.n_errored:,}",
        ])
    lines = _table(
        ["arm", "n", "abstained (scorer rule)", "abstained (published rule)",
         "errors"],
        rows,
    )
    lines += [
        "",
        f"**This number means nothing without the answerable numbers in the "
        f"section above it.** On category {ADVERSARIAL_CATEGORY} the gold "
        f"behaviour is to decline, and a wholly dead backbone returns nothing, "
        f"which reads as declining — so a broken system scores 100% here while "
        f"scoring 0% on everything else. It is printed beside the answerable "
        f"result and never on its own, and it is held out of every refusal rate "
        f"in this report.",
        "",
        "The two rules differ and both are shown. `evals/qa/scorer.py` carries "
        "twenty refusal markers; the reference harness accepts exactly two "
        "phrases — \"no information available\" and \"not mentioned\". A modern "
        "model that declines in any other words is WRONG under the published "
        "rule and CORRECT under ours, and this column is how large that "
        "difference is on these questions.",
    ]
    return lines


def _replicate_section(spreads: Mapping[str, Any], replicates: int) -> List[str]:
    if replicates < 2:
        return [
            f"**One replicate.** No spread is reported, because the spread of "
            f"one number is not zero — it is unmeasured, and printing 0.0 would "
            f"claim a reproducibility this run did not observe. The published "
            f"protocol grades every question {PROTOCOL_JUDGE_RUNS} times and "
            f"reports the mean and the standard deviation across whole-run "
            f"accuracies; re-run with `--replicates {PROTOCOL_JUDGE_RUNS}` to "
            f"match it.",
            "",
            "Retrieval, in the section above, needs no replicates: it reads no "
            "model and reproduces exactly.",
        ]
    rows = []
    for arm, spread in spreads.items():
        rows.append([
            arm,
            str(spread.n),
            _num(spread.mean, spread.n),
            "n/a" if spread.sd is None else f"{spread.sd:.4f}",
            "n/a" if spread.spread is None else f"{spread.spread:.4f}",
            ", ".join(f"{v:.4f}" for v in spread.values),
        ])
    lines = _table(["arm", "runs", "mean accuracy", "sd (population)",
                    "max − min", "per-run"], rows)
    lines += [
        "",
        "`sd` is the population standard deviation across whole-run "
        "accuracies, which is what the reference grader computes over its three "
        "runs. A generative arm in this repository has moved 0.043 token F1 "
        "between two runs of an identical configuration, so any gap smaller "
        "than this spread is not a result.",
    ]
    return lines


def _controls_section(meta: Mapping[str, Any], blockers: Sequence[str]) -> List[str]:
    rows = []
    for control in PROTOCOL_CONTROLS:
        declared = meta.get(control.key)
        shown = str(declared) if declared not in (None, "") else "—"
        if not control.is_fixed:
            status = "declared" if declared not in (None, "") else "**UNDECLARED**"
            required = "not fixed by publication"
        else:
            required = control.required
            status = ("met" if declared not in (None, "")
                      and str(declared) == control.required else "**UNMET**")
        rows.append([control.key, required, shown, status])
    lines = _table(["control", "protocol fixes", "this run declared", "status"], rows)
    if blockers:
        lines += ["", "**This run is NOT comparable with published LoCoMo "
                      "numbers.** Each line below is sufficient on its own:", ""]
        lines += [f"- {blocker}" for blocker in blockers]
    else:
        lines += ["", "Every control matches the published protocol."]
    return lines


def _comparable_section(decomposition: Any, blockers: Sequence[str]) -> List[str]:
    """The quotable table — withheld entirely when any control is unmet.

    Printed above the reasons rather than below them: this is the part of a
    report that gets screenshotted, and an invalid number must not appear at all
    rather than appear with a retraction underneath.
    """
    if blockers:
        failed = ", ".join(sorted({b.split(":", 1)[0] for b in blockers}))
        return [
            "**Withheld — see the controls below.** These answers were produced "
            "under a protocol that does not match any published one, so printing "
            "them in a published table's shape would state a comparison this run "
            "does not support. The numbers above stand as an INTERNAL "
            f"measurement and nothing more. Unmet or undeclared: {failed}.",
            "",
            "The published figures — Mem0's 92.5 self-report, Letta's ~83, Zep's "
            "94.7 under a different model and setup — are **not** reproduced "
            "here. This repository holds none of their runs, and quoting numbers "
            "it never measured is exactly the practice this arm was built to "
            "avoid.",
        ]
    if not decomposition.complete:
        return ["**Withheld.** The controls are met but the three-number "
                "decomposition is not, and a comparable headline without it is "
                "the thing this harness refuses to print."]
    arms = list(decomposition.all_questions)
    rows = [[
        arm,
        PROTOCOL_BACKBONE,
        PROTOCOL_JUDGE,
        str(decomposition.all_questions[arm].n),
        _rate(decomposition.all_questions[arm].accuracy,
              decomposition.all_questions[arm].n),
        _rate(decomposition.like_for_like[arm].accuracy,
              decomposition.like_for_like[arm].n),
    ] for arm in arms]
    return _table(
        ["method", "backbone", "judge", "n", "accuracy (all)",
         "accuracy (like-for-like)"],
        rows,
    ) + [
        "",
        f"Every control is met, so these rows are in the published protocol's "
        f"units over its own denominator — categories "
        f"{', '.join(str(c) for c in JUDGED_CATEGORIES)}, which is "
        f"{decomposition.n_all:,} questions here. The published baselines' own "
        f"numbers are still not reproduced in this repository.",
    ]


#: Consecutive failed searches, from the start of a run, that abort it. Small on
#: purpose: the failure this guards is TOTAL retriever death, and three is
#: enough to distinguish that from a hard question.
_RETRIEVAL_CANARY = 3


def _shortfall_section(shortfalls: Sequence[Mapping[str, Any]],
                       n_questions: int, meta: Mapping[str, Any],
                       n_searched: Optional[int] = None) -> List[str]:
    """Evidence-budget shortfalls, and never silence read as success.

    ``shortfalls`` is appended only AFTER a search returns, so a search that
    RAISES never increments it — and zero successful queries produced exactly
    the same empty list as 199 successful ones. Measured: with every search
    raising, this section printed "Every one of the 199 queries returned the
    full evidence budget." The absence of a complaint was being reported as
    proof of success.

    ``n_searched`` is how many searches actually returned. When it is short of
    ``n_questions`` the reassuring sentence is withheld and the gap is named.
    """
    lines: List[str] = []
    if n_searched is not None and n_searched < n_questions:
        lines.append(
            f"**{n_questions - n_searched:,} of {n_questions:,} queries did not "
            f"complete a search.** No shortfall can be reported for them: this "
            f"counter only increments after a search returns, so its silence "
            f"here is missing data, not a full evidence budget."
        )
    elif not shortfalls:
        # Deliberately NOT "every one of the N queries returned the full
        # budget". This counter increments only after a search RETURNS, so an
        # empty list means "no returning search was short" and says nothing
        # about searches that raised. The retrieval canary (_RETRIEVAL_CANARY)
        # makes total retriever death unreachable, but a run that degrades
        # part-way would still land here, and the old sentence asserted
        # completeness the counter cannot see.
        lines.append(
            f"No search that returned was short of its evidence budget. This "
            f"counter is incremented on return, so it reports nothing about a "
            f"search that raised; per-question failures are in §3's error "
            f"column, and a run whose retriever never returned aborts before "
            f"this section is reached."
        )
    else:
        rows = [[str(s["question"])[:70], str(s.get("conversation") or "—"),
                 str(s["requested"]), str(s["returned"]),
                 str(s.get("total_matches", "—"))]
                for s in shortfalls[:20]]
        lines += _table(["question", "conversation", "requested", "returned",
                         "candidates"], rows)
        if len(shortfalls) > 20:
            lines += ["", f"...and {len(shortfalls) - 20:,} more."]
        lines += [
            "",
            f"**{len(shortfalls):,} of {n_questions:,} queries returned fewer "
            f"than the budget.** The evidence list is never padded — a padded "
            f"list makes an under-filled budget indistinguishable from a full "
            f"one — so a shortfall means this run gave itself LESS context than "
            f"the baselines had, not more.",
        ]
    mean = meta.get("evidence_chars_mean")
    if mean is not None:
        lines += [
            "",
            f"**Evidence size, in characters rather than items.** Mean "
            f"{int(mean):,} per question, median "
            f"{int(meta.get('evidence_chars_median') or 0):,}, max "
            f"{int(meta.get('evidence_chars_max') or 0):,}. An item is a "
            f"retrieved node's name and description, stamped with the date of "
            f"the session it came from, plus that session's own file — up to "
            f"{EVIDENCE_SOURCE_CHARS:,} characters of it. Measured this phase, "
            f"no session in this corpus renders larger than that, so the "
            f"backbone reads exactly the text the retriever scored. Every node "
            f"that IS a staged session brings its file; the remaining hits "
            f"bring theirs until a further {EVIDENCE_EXTRA_SOURCE_CHARS:,} "
            f"characters are spent, and each file is pasted at most once.",
        ]
    return lines


def _evidence_chars(rows: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    sizes = sorted(int(r.get("evidence_chars") or 0) for r in rows
                   if r.get("evidence_chars") is not None)
    if not sizes:
        return {}
    mid = len(sizes) // 2
    median = sizes[mid] if len(sizes) % 2 else (sizes[mid - 1] + sizes[mid]) // 2
    return {"evidence_chars_mean": round(sum(sizes) / len(sizes)),
            "evidence_chars_median": median,
            "evidence_chars_max": sizes[-1]}


def build_report(
    *,
    conversations: Sequence[Conversation],
    ingests: Sequence[IngestResult] = (),
    alignment: Optional[Mapping[str, int]] = None,
    retrieval: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    decomposition: Any = None,
    spreads: Optional[Mapping[str, Any]] = None,
    replicates: int = 1,
    shortfalls: Sequence[Mapping[str, Any]] = (),
    meta: Optional[Mapping[str, Any]] = None,
    data: str = "undeclared",
) -> str:
    """The markdown report. No timestamps: same inputs in, same bytes out.

    Section numbers are stable across flag combinations. A section with nothing
    in it says so and keeps its number, because a report is quoted BY number and
    a heading that moves with the flags makes two reports of the same run
    disagree about where its result is.
    """
    meta = dict(meta or {})
    blockers = protocol_blockers(meta)
    questions = sum(len(c.questions) for c in conversations)
    names = ", ".join(c.sample_id for c in conversations)
    lines = [
        f"# LoCoMo — {names}",
        "",
        f"Dataset: `{data}` (snap-research/locomo, `locomo10.json`, "
        f"{meta.get('dataset_revision') or 'revision undeclared'}). "
        f"Conversations: {len(conversations)}. Questions: {questions:,}. "
        f"Retrieval scorer: `evals/locomo/retrieval.py` (recall@K and MRR of the "
        f"gold session). Answer judge: "
        f"`{meta.get('judge') or 'none — nothing was graded'}`.",
        "",
        "**Latency is not measured and must not be inferred from this run.**",
        "",
        "## 1. Corpus",
        "",
    ]
    lines += _corpus_section(conversations, ingests)
    lines += ["", "## 2. Gold alignment and answer-key integrity", ""]
    lines += (_alignment_section(alignment) if alignment else
              ["Gold was not aligned in this run."])
    lines += ["", "## 3. Retrieval (deterministic — no model, no quota)", ""]
    lines += _retrieval_section(retrieval or {})
    lines += ["", "## 4. Answer scoring — three numbers or none", ""]
    lines += (_three_number_section(decomposition) if decomposition is not None else
              ["No arm answered a question in this run — `--retrieval-only`, or "
               "an `--arms` list without `tesserae`. The result is §3."])
    lines += ["", f"## 5. Category {ADVERSARIAL_CATEGORY} "
                  f"({CATEGORY_NAMES[ADVERSARIAL_CATEGORY]}) — scored apart", ""]
    # Gate on completeness exactly as §7 does. Rendering whenever a
    # decomposition merely EXISTS printed a clean 100.0% adversarial rate with
    # no answerable number beside it — the scenario this module's docstring
    # names as the nightmare — for a backbone that returned "" to every real
    # question after passing the canary.
    if decomposition is None:
        lines += ["Nothing was answered, so nothing was scored here."]
    elif not getattr(decomposition, "complete", True):
        lines += ["**Withheld.** The like-for-like subset is empty, so an "
                  "adversarial rate here would have no answerable rate to be "
                  "read against. See §4."]
    else:
        lines += _adversarial_section(decomposition)
    lines += ["", "## 6. Replicate spread", ""]
    lines += _replicate_section(spreads or {}, replicates)
    lines += ["", "## 7. Published-comparable result", ""]
    lines += (_comparable_section(decomposition, blockers) if decomposition is not None
              else ["Nothing was answered, so there is no result to compare. "
                    "The controls below are still checked, and still unmet."])
    lines += ["", "## 8. Protocol controls", ""]
    lines += _controls_section(meta, blockers)
    lines += ["", "### Declared", ""]
    keys = sorted(k for k in meta if k not in ("shortfalls", "evidence"))
    lines += (_table(["key", "value"], [[k, str(meta[k])] for k in keys]) if keys
              else ["Nothing declared — an undeclared run cannot be published."])
    lines += ["", "## 9. Retrieval shortfalls and evidence size", ""]
    lines += _shortfall_section(shortfalls, questions, meta,
                                n_searched=meta.get('n_searched'))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA,
                        help=f"locomo10.json (default: {DEFAULT_DATA})")
    parser.add_argument("--conversations", nargs="+", default=None,
                        help="sample ids to run, e.g. --conversations conv-26 "
                             "(19 documents, 199 questions — the cheapest "
                             "conversation that populates all five categories)")
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK,
                        help=f"scratch project root, NEVER inside this repo "
                             f"(default: {DEFAULT_WORK}). Each conversation "
                             f"compiles into its own subdirectory")
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT,
                        help=f"where to write the report (default: "
                             f"{DEFAULT_REPORT}, outside the repo)")
    parser.add_argument("--answers-out", type=Path, default=None,
                        help="save the raw answers so they can be re-graded by "
                             "another judge without re-answering")
    parser.add_argument("--score", type=Path, default=None,
                        help="re-grade a saved answers file — no backbone, no "
                             "retrieval, and no network at all with the "
                             "deterministic judge")
    parser.add_argument("--arms", default="tesserae",
                        help=f"comma list of memory systems ({', '.join(ARMS)}; "
                             f"default: tesserae). Only tesserae spends")
    parser.add_argument("--retrieval-only", action="store_true",
                        help="score recall@K and MRR and skip answering "
                             "entirely: no backbone and no judge. It does NOT "
                             "make a tesserae run free — that arm still "
                             "compiles the corpus, which is the expensive half. "
                             "--arms bm25,dense --retrieval-only spends nothing "
                             "at all")
    parser.add_argument("--stage-only", action="store_true",
                        help="write the session documents and stop: no compile, "
                             "no LLM, no network")
    parser.add_argument("--reuse-compile", action="store_true",
                        help="measure against the graph already compiled under "
                             "--work instead of compiling again; verifies the "
                             "staged corpus byte for byte and refuses otherwise")
    parser.add_argument("--replicates", type=int, default=1,
                        help=f"how many times to answer every question. The "
                             f"published protocol grades {PROTOCOL_JUDGE_RUNS} "
                             f"times and reports the spread; a single "
                             f"generative number is not a measurement")
    parser.add_argument("--judge", default="deterministic",
                        help="deterministic (free, reproducible, runs today) or "
                             f"llm:<model> (the published grader is "
                             f"{PROTOCOL_JUDGE})")
    parser.add_argument("--backbone", default=PROTOCOL_BACKBONE,
                        help=f"answering model (the published protocol fixes "
                             f"{PROTOCOL_BACKBONE})")
    parser.add_argument("--k", type=int, nargs="+", default=None,
                        help=f"evidence budgets to score at (default: "
                             f"{' '.join(str(k) for k in PROTOCOL_KS)}, fixed in "
                             f"code before any result existed)")
    parser.add_argument("--answer-k", type=int, default=max(PROTOCOL_KS),
                        help="how many evidence items the backbone reads. "
                             "Separate from --k, which is the reporting set")
    parser.add_argument("--answer-evidence", choices=("source", "summary"),
                        default="source",
                        help="what the backbone reads per hit: the node summary "
                             "plus its own session text (source, the default), "
                             "or the node summary alone (summary, the control)")
    parser.add_argument("--judge-rules", default="protocol-b",
                        choices=("protocol-b", "mem0-2026"),
                        help="which grader's RULES run. protocol-b is the "
                             "published LoCoMo grader (default). mem0-2026 is "
                             "Mem0's own, reproduced from their benchmark repo: "
                             "partial credit on list golds, 14-day date "
                             "tolerance, 50%% duration tolerance. It is LENIENT "
                             "and raises any system's score without changing "
                             "the system — their 92.5 is graded by it. Recorded "
                             "in the run artifact as `judge_rules`; never quote "
                             "a number from one beside a number from the other")
    parser.add_argument("--semicolon-gold", action="store_true",
                        help="score category 3 on the first `;`-separated "
                             "clause only, as the reference harness does")
    parser.add_argument("--embedding-prefer", default=LOCAL_EMBEDDING_PREFER,
                        help=f"embedding backend preference (default: "
                             f"{LOCAL_EMBEDDING_PREFER}, local and free)")
    parser.add_argument("--rerank", default=None, metavar="MODEL",
                        help="rerank the retrieved candidates with a "
                             "cross-encoder before answering (e.g. "
                             "Qwen/Qwen3-Reranker-0.6B). Needs the `rerank` "
                             "extra. Off by default: this is not the shipped "
                             "retrieval path")
    parser.add_argument("--rerank-overfetch", type=int, default=RERANK_OVERFETCH,
                        help=f"how many candidates per budget unit the lanes "
                             f"produce for the reranker to choose from "
                             f"(default: {RERANK_OVERFETCH}). Ignored without "
                             f"--rerank")
    parser.add_argument("--rerank-max-length", type=int, default=RERANK_MAX_LENGTH,
                        help=f"tokens per (query, candidate) pair the "
                             f"cross-encoder reads (default: "
                             f"{RERANK_MAX_LENGTH}). The knob that decides "
                             f"whether reranking is affordable; see "
                             f"tesserae.retrieval.rerank for the measured "
                             f"curve. Ignored without --rerank")
    parser.add_argument("--fanout", action="store_true",
                        help="run the lanes a second time with the "
                             "corpus-ubiquitous terms stripped, and merge the "
                             "two rankings admitting at most --source-cap hits "
                             "per session. Free, local, deterministic. Off by "
                             "default: this is not the shipped retrieval path")
    parser.add_argument("--fanout-overfetch", type=int, default=DEFAULT_OVERFETCH,
                        help=f"how many candidates per budget unit EACH "
                             f"sub-query produces for the merge to choose from "
                             f"(default: {DEFAULT_OVERFETCH}; saturates there). "
                             f"Ignored without --fanout")
    parser.add_argument("--source-cap", type=int, default=DEFAULT_SOURCE_CAP,
                        help=f"hits per session admitted into the head of the "
                             f"merged result (default: {DEFAULT_SOURCE_CAP}). 0 "
                             f"disables the cap, which isolates how much of the "
                             f"gain is the fan-out alone. Ignored without "
                             f"--fanout")
    parser.add_argument("--ubiquity-df-ratio", type=float,
                        default=DEFAULT_UBIQUITY_DF_RATIO,
                        help=f"a term in this fraction of the corpus or more is "
                             f"stripped from the sub-query (default: "
                             f"{DEFAULT_UBIQUITY_DF_RATIO}). Ignored without "
                             f"--fanout")
    parser.add_argument("--extra-facets", type=int, default=0,
                        help="extra single-token sub-queries beyond the "
                             "stripped one, rarest term first (default: 0). "
                             "Ignored without --fanout")
    parser.add_argument("--prefer-anchor-text", action="store_true",
                        help="build each hit from the node that STANDS FOR its "
                             "session file rather than the node retrieved. "
                             "Repairs the prompt starvation --source-cap "
                             "otherwise causes; see LocomoMemory._hit_nodes")
    parser.add_argument("--tiered-evidence", action="store_true",
                        help="spend the evidence budget cheapest first: fact "
                             "heads with the filesystem path stripped, then the "
                             "transcript turn each fact's evidenced_by edge "
                             "points at, then today's session paste unchanged. "
                             "Free, local, deterministic. Off by default: "
                             "without it the answering path is the same bytes "
                             "it is today. Refuses --prefer-anchor-text")
    parser.add_argument("--evidence-receipt-chars", type=int,
                        default=EVIDENCE_RECEIPT_CHARS,
                        help=f"characters of receipt TURN text one question may "
                             f"carry (default: {EVIDENCE_RECEIPT_CHARS}). A "
                             f"bound, not a target — measured spend is 477, so "
                             f"it does not bind on this corpus. 0 is the kill "
                             f"control and reproduces the shipped numbers "
                             f"exactly. Ignored without --tiered-evidence")
    parser.add_argument("--receipt-window", type=int, default=RECEIPT_WINDOW,
                        help=f"turns either side of a receipt turn to emit with "
                             f"it, in FILE order (default: {RECEIPT_WINDOW}). "
                             f"Off because it is unmeasured in this frame: two "
                             f"prior measurements disagree, and the one that "
                             f"found it inert had already pasted the "
                             f"neighbours. Ignored without --tiered-evidence")
    parser.add_argument("--modal-gate", action="store_true",
                        help="choose the abstention rule PER QUESTION from the "
                             "question's modality, computed before retrieval: a "
                             "dispositional question (would / might / likely / "
                             "probably / be considered / prefer) is answered "
                             "under a prompt containing NO abstention string at "
                             "all, everything else under one containing no "
                             "dispositional carve-out. Off by default: without "
                             "it every question is answered under the single "
                             "shipped prompt, byte for byte. Free — it changes "
                             "no evidence and costs ~400 characters LESS than "
                             "the prompt it replaces")
    parser.add_argument("--deliberate", action="store_true",
                        help="answer under the two-key contract: a free-form "
                             "\"reasoning\" key the judge never sees, and an "
                             "\"answer\" key that is NAMED to the model rather "
                             "than guessed at, plus two content rules (be "
                             "specific; enumerate list-shaped answers) in place "
                             "of \"the shortest answer ... and nothing else\". "
                             "Off by default: without it every question is "
                             "answered under the single shipped prompt, byte "
                             "for byte. Costs ~148 evidence-budget tokens "
                             "(2.1% of 7,069) because the system prompt is "
                             "inside the budget; the reasoning field is OUTPUT "
                             "and costs nothing. It does NOT change the "
                             "abstention rule, and it does NOT lift an "
                             "output-length cap — measured, the judge's length "
                             "channel is ~0 on this judge and this data. The "
                             "extractor ladder that recovers a misshapen reply "
                             "runs either way; this flag only decides whether "
                             "its bare-prose rung is available, and it is OFF "
                             "under --deliberate so a reasoning trace can never "
                             "reach the judge")
    parser.add_argument("--evidence-unit", choices=("session", "turn"),
                        default="session",
                        help="what ONE evidence item is. 'session' is the "
                             "shipped path and pastes whole session files; "
                             "'turn' keeps the session as the RETRIEVAL unit "
                             "and makes the turn the READING unit, packing "
                             "query-selected [D<n>:<t>] lines from the same "
                             "retrieved sessions into --evidence-pack-chars. "
                             "Free, local, deterministic. Off by default: "
                             "without it the answering path is the same bytes "
                             "it is today. Refuses --tiered-evidence")
    parser.add_argument("--evidence-pack-chars", type=int,
                        default=EVIDENCE_PACK_CHARS,
                        help=f"the ONE total budget a turn-unit prompt may "
                             f"spend, session headers included (default: "
                             f"{EVIDENCE_PACK_CHARS}, ~7,000 tokens against the "
                             f"~10,900 the fan-out arm spends today). Replaces "
                             f"the per-document and extra-source budgets on "
                             f"this path only. Ignored without --evidence-unit "
                             f"turn")
    parser.add_argument("--turn-pool", choices=("retrieved", "corpus"),
                        default="retrieved",
                        help="which sessions' turns may be scored. 'retrieved' "
                             "is the default and holds the pack to the same "
                             "distinct-session count the shipped arm has "
                             "(9.78 on conv-26); 'corpus' scores every staged "
                             "session, reaching +0.014 turn coverage at 17.3 "
                             "distinct sessions — a SECOND ARM against Levy's "
                             "distractor penalty, not a better default, and on "
                             "a 19-session corpus indistinguishable from "
                             "reading everything. Ignored without "
                             "--evidence-unit turn")
    parser.add_argument("--turn-pool-k", type=int, default=TURN_POOL_K,
                        help=f"RETRIEVE-WIDE. Ask the session stage a second, "
                             f"free time at this k and pool those sessions' "
                             f"turns as well as the answer-time hits' (default: "
                             f"{TURN_POOL_K} — off, byte-identical to today). "
                             f"14.7%% of gold evidence turns never enter the "
                             f"candidate pool at all, against 1.1%% lost to the "
                             f"admission rule, so this is where the headroom is. "
                             f"BM25 + model2vec over the same graph: zero LLM "
                             f"calls. Pair it with --turn-session-cap, which is "
                             f"what bounds the distractor count it raises; on "
                             f"its own the wide pool scores 0.928 turn coverage "
                             f"against 0.930 capped at 16 and 0.909 today. "
                             f"Ignored without --evidence-unit turn")
    parser.add_argument("--turn-session-cap", type=int,
                        default=TURN_SESSION_CAP,
                        help=f"PACK-NARROW. The most SESSION BLOCKS the pack "
                             f"may open, receipts included (default: "
                             f"{TURN_SESSION_CAP} — uncapped, byte-identical to "
                             f"today). Pooled over 1,536 questions at 28,000 "
                             f"characters: narrow 0.909 coverage at 9.9 blocks, "
                             f"wide/cap8 0.848, cap12 0.919, cap16 0.930, "
                             f"uncapped-wide 0.928 at 22.4 — an interior "
                             f"optimum, not 'more sessions is better'. 16 is a "
                             f"knee found ON the benchmark; the direction holds "
                             f"in 9 of 10 conversations, the exact value is not "
                             f"a tuned optimum, and cap12 is the fallback if "
                             f"graded multi-hop falls to Levy's distractor "
                             f"penalty. Ignored without --evidence-unit turn")
    parser.add_argument("--turn-emit-window", type=int,
                        default=TURN_EMIT_WINDOW,
                        help=f"turns either side of an ADMITTED turn to emit "
                             f"with it, in FILE order (default: "
                             f"{TURN_EMIT_WINDOW}). Off because it is measured "
                             f"NEGATIVE at a fixed budget — 0.923 -> 0.908 turn "
                             f"coverage, because the neighbours eat slots. "
                             f"Score with context, emit without it. Ignored "
                             f"without --evidence-unit turn")
    parser.add_argument("--turn-heads", choices=("none", "fact"),
                        default="none",
                        help="whether the fact heads join the pack. 'none' by "
                             "default and measured: they cost 2,230 characters, "
                             "which at a fixed cap is ~14 turns, and the turns "
                             "are worth more (coverage 0.942 -> 0.927, "
                             "open-domain 0.773 -> 0.682). 'fact' emits them "
                             "with the absolute filesystem path stripped. "
                             "Ignored without --evidence-unit turn")
    parser.add_argument("--i-know-this-costs-money", action="store_true",
                        help="required for anything that reaches an LLM")
    parser.add_argument("--yes", action="store_true",
                        help="skip the typed confirmation after the cost banner")
    return parser


def _confirm(conversations: Sequence[Conversation], assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("SKIP: not a terminal, so the cost above cannot be confirmed\n"
              "      re-run with --yes if you have read the estimate")
        return False
    reply = input(f"Proceed and spend roughly the above on "
                  f"{len(conversations)} conversation(s)? type 'yes': ")
    if reply.strip().lower() != "yes":
        print("SKIP: not confirmed — nothing was staged")
        return False
    return True


#: What :func:`_fold_verdicts` copies from a :class:`GradedRow` onto the answer
#: row it graded. Not the whole verdict: ``key``, ``arm``, ``replicate``,
#: ``question`` and ``category`` are already on the answer row and re-writing
#: them would let a fold silently disagree with the row it folded onto.
_VERDICT_FIELDS = ("correct", "score", "label", "judge", "refused", "errored",
                   "reference_correct")


def _fold_verdicts(rows: List[Dict[str, Any]],
                   graded: Sequence[GradedRow]) -> None:
    """Write each verdict back onto the answer row it graded, in place.

    **The judge's per-question labels were not being persisted anywhere.** The
    saved answers carried the answer, its category and its evidence size; the
    verdicts lived only inside the printed report's aggregates. So the report
    could say "32% wrong with the gold retrieved" and nobody could list WHICH
    32% — re-deriving the join cost a re-grade, and grepping the judge log finds
    the words CORRECT and WRONG inside the judge's own instruction text rather
    than its answers. Reading the failures is how the last three defects in this
    module were found, and it should not need a second paid run.

    Silent on an empty grade — a retrieval-only run has verdicts for nothing —
    and RAISES on a length mismatch rather than zipping short. :func:`_grade_rows`
    returns one row per answer in order, so a mismatch means that stopped being
    true, and a short zip would silently label row N with row N's verdict for the
    first few and leave the rest unlabelled, which reads as "the judge did not
    grade these" rather than as a bug.
    """
    if not graded:
        return
    if len(graded) != len(rows):
        raise RuntimeError(
            f"graded {len(graded)} rows against {len(rows)} answers — the "
            f"verdict/answer join is no longer positional and folding it would "
            f"mislabel every row after the first divergence"
        )
    for row, verdict in zip(rows, graded):
        payload = verdict.as_dict()
        row.update({field: payload[field] for field in _VERDICT_FIELDS})


def _grade_rows(
    rows: Sequence[Mapping[str, Any]],
    conversations: Sequence[Conversation],
    judge: Judge,
) -> List[GradedRow]:
    """Grade saved answer rows against the questions they came from."""
    by_id = {c.sample_id: c for c in conversations}
    graded: List[GradedRow] = []
    for row in rows:
        conversation = by_id.get(str(row.get("conversation") or ""))
        if conversation is None:
            raise Skip(
                f"answers reference conversation "
                f"{row.get('conversation')!r}, which this dataset does not hold",
                "pass --data pointing at the locomo10.json the answers were "
                "produced from",
            )
        index = int(row.get("question_index") or 0)
        if not 0 <= index < len(conversation.questions):
            raise Skip(
                f"answers reference question {index} of "
                f"{conversation.sample_id}, which has "
                f"{len(conversation.questions)} — the answer key has changed "
                f"under these answers",
                "re-answer against this dataset, or pass the dataset revision "
                "the answers were produced from",
            )
        question = conversation.questions[index]
        graded.append(grade(
            judge, question, row.get("answer"),
            key=str(row.get("key") or question_key(question, index)),
            arm=str(row.get("arm") or "tesserae"),
            replicate=int(row.get("replicate") or 0),
        ))
    return graded


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Guard 0 — a flag pair that would silently produce the failure the other
    # flag exists to prevent. `--prefer-anchor-text` rewrites EVERY hit to a
    # SourceDocument/Session anchor, and those node types carry no
    # `evidenced_by` edge at all: measured, it drives provenance to exactly
    # 0.000. Composing the two would report a tiered run whose receipt tier
    # emitted nothing, which is the "worse Mem0" this design exists to avoid.
    # A hard refusal rather than a Skip: this is an argument error, and a Skip
    # exits 0, which reads as a run that measured something.
    #
    # `--prefer-anchor-text` is NOT gated on `--fanout`, so absent this it
    # composes with anything.
    if args.tiered_evidence and args.prefer_anchor_text:
        parser.error(
            "--tiered-evidence and --prefer-anchor-text contradict each other: "
            "--prefer-anchor-text rewrites every hit to its session's anchor "
            "node, and SourceDocument/Session nodes carry no evidenced_by "
            "edges, so the receipt tier would resolve nothing and provenance "
            "would read 0.000. Pick one."
        )

    # Guard 0b — the second incoherent pair, refused on the same terms.
    # Tiering's tier 2 (receipt turns) IS a degenerate turn pack, and its tier 3
    # pastes whole sessions the pack budget knows nothing about. Composing them
    # would produce a prompt neither design's coverage table describes and a
    # meta block naming two budgets only one of which bound.
    if args.evidence_unit == "turn" and args.tiered_evidence:
        parser.error(
            "--evidence-unit turn and --tiered-evidence are mutually "
            "exclusive: tiering's receipt tier is a degenerate special case of "
            "the turn pack, and its session tier spends a budget the pack does "
            "not account for. Pick one."
        )

    # Guard 1 — CI, before anything reads a file.
    if os.environ.get("CI"):
        print("SKIP: CI is set — the LoCoMo arm never runs in CI\n"
              "      it compiles ten conversations and grades 1,986 questions; "
              "run it by hand instead")
        return 0

    try:
        judge = build_judge(args.judge, split_semicolon_gold=args.semicolon_gold,
                            judge_rules=args.judge_rules)
        arms = parse_arms(args.arms)
        ks = require_ks(args.k)
        data = require_data(args.data)
        conversations = load_conversations(data)
        revision = dataset_revision(data)

        # ---------------------------------------------------------- re-score
        if args.score:
            if not args.score.is_file():
                raise Skip(f"answers file not found at {args.score}",
                           "produce one with --answers-out")
            payload = json.loads(args.score.read_text(encoding="utf-8"))
            rows = payload.get("rows") or []
            saved_meta = dict(payload.get("meta") or {})
            if saved_meta.get("dataset_revision") not in (None, revision):
                raise Skip(
                    f"these answers were produced against dataset "
                    f"{saved_meta['dataset_revision']} and --data is {revision}",
                    "pass the same locomo10.json — re-grading against a "
                    "different answer key silently changes every verdict",
                )
            chosen = [c for c in conversations
                      if c.sample_id in {str(r.get("conversation")) for r in rows}]
            judge.canary()
            graded = _grade_rows(rows, chosen, judge)
            replicates = len({g.replicate for g in graded})
            decomposition = decompose(graded)
            meta = {
                **saved_meta, **judge.config,
                "dataset_revision": revision,
                "judge_runs": replicates,
                "evidence": {
                    **(saved_meta.get("evidence") or {}),
                    "llm_judge_calls": judge.llm_calls,
                },
            }
            text = build_report(
                conversations=chosen,
                decomposition=decomposition,
                spreads=replicate_spread(graded),
                replicates=replicates,
                meta=meta,
                data=str(data),
            )
            # Re-grading is the CHEAP way to get per-question verdicts back — no
            # backbone, no retrieval — so it is the last path that should drop
            # them. Folding only on the answering path meant the one way to
            # re-derive a decomposition without paying for answers again threw
            # the labels away and printed aggregates.
            _fold_verdicts(rows, graded)
            if args.answers_out:
                args.answers_out.parent.mkdir(parents=True, exist_ok=True)
                args.answers_out.write_text(
                    json.dumps({"meta": meta, "rows": rows},
                               indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
                print(f"wrote {args.answers_out}")
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(text, encoding="utf-8")
            print(text)
            print(f"wrote {args.out}")
            return 0

        chosen = select_or_skip(conversations, args.conversations)
        spends = "tesserae" in arms
        if args.stage_only and not spends:
            raise Skip(
                f"--stage-only stages the Tesserae corpus, and --arms "
                f"{args.arms} does not include it",
                "add tesserae to --arms, or drop --stage-only — the baselines "
                "index the staged bytes in memory and write nothing",
            )
        if args.reuse_compile and args.stage_only:
            print("SKIP: --reuse-compile and --stage-only contradict each other "
                  "— one measures against an existing graph, the other refuses "
                  "to build or use one")
            return 0
        if args.replicates < 1:
            raise Skip(f"--replicates {args.replicates}: a run answers each "
                       f"question at least once",
                       f"pass --replicates {PROTOCOL_JUDGE_RUNS}")

        answering = spends and not args.stage_only and not args.retrieval_only
        # Every arm retrieves the same number of documents, and it is the
        # largest budget anything in this run reads: scoring at K=20 off a
        # ranking that only ever returned 10 would report a shortfall as a
        # ranking failure.
        search_k = max(max(ks), int(args.answer_k))

        # Guard 2 — the estimate, before anything is staged.
        if spends:
            print(cost_banner(chosen, replicates=args.replicates))

        # Guard 3 — consent, ahead of the remaining prerequisites so an
        # operator who forgot the flag learns about the flag.
        if spends and not args.stage_only and not args.i_know_this_costs_money:
            print("SKIP: this run compiles a corpus and answers every question "
                  "— both spend LLM quota\n"
                  "      re-run with --i-know-this-costs-money, or "
                  "--stage-only to write the documents and stop, or "
                  "--retrieval-only to score retrieval for free")
            return 0

        # Guard 4 — prerequisites.
        work = require_work_dir(args.work)

        # Gold alignment BEFORE anything is staged or compiled: it refuses
        # rather than guessing, and a refusal after a compile throws away the
        # expensive half of the run to say something knowable from the file.
        alignments: Dict[str, GoldAlignment] = {
            c.sample_id: align_gold(c) for c in chosen}

        if spends and not args.stage_only and not _confirm(chosen, args.yes):
            return 0

        reranker = None
        if args.rerank:
            from tesserae.retrieval.rerank import Qwen3Reranker

            reranker = Qwen3Reranker(
                args.rerank, max_length=args.rerank_max_length
            )
        memory = LocomoMemory(
            embedding_prefer=args.embedding_prefer,
            reranker=reranker,
            rerank_overfetch=args.rerank_overfetch,
            fanout=args.fanout,
            fanout_overfetch=args.fanout_overfetch,
            # 0 on the command line means "no cap", which is the control arm
            # that separates the fan-out's own contribution from the merge's —
            # the two are multiplicative and reporting only the total would
            # credit the wrong half.
            source_cap=args.source_cap if args.source_cap > 0 else None,
            ubiquity_df_ratio=args.ubiquity_df_ratio,
            extra_facets=args.extra_facets,
            prefer_anchor_text=args.prefer_anchor_text,
            tiered_evidence=args.tiered_evidence,
            evidence_receipt_chars=args.evidence_receipt_chars,
            receipt_window=args.receipt_window,
            evidence_unit=args.evidence_unit,
            evidence_pack_chars=args.evidence_pack_chars,
            turn_pool=args.turn_pool,
            turn_pool_k=args.turn_pool_k,
            turn_session_cap=args.turn_session_cap,
            turn_emit_window=args.turn_emit_window,
            turn_heads=args.turn_heads,
        ) if spends else None
        answer_fn = (build_backbone(args.backbone,
                                  modal_gate=args.modal_gate,
                                  deliberate=args.deliberate)
                     if answering else None)

        # THE CANARY. Before any question is answered, and before the judge
        # grades anything. Both halves, in both directions.
        #
        # Both are conditioned on ``answering`` because a retrieval-only run
        # generates nothing and grades nothing: canarying an LLM judge there
        # would spend two metered calls to check a grader this run will never
        # use. The deterministic judge's canary is free either way, and the
        # ``--score`` path above canaries unconditionally because grading is
        # the only thing it does.
        canary_calls = 0
        if answering:
            assert answer_fn is not None
            canary_calls = canary_backbone(answer_fn)
            judge.canary()

        ingests: List[IngestResult] = []
        answer_rows: List[Dict[str, Any]] = []
        retrieval_by_arm: Dict[str, List[Dict[str, Any]]] = {n: [] for n in arms}
        arm_objects: Dict[str, Any] = {}
        refused: Dict[str, Skip] = {}

        for conversation in chosen:
            alignment = alignments[conversation.sample_id]
            if memory is not None:
                ingests.append(memory.ingest(
                    conversation, work=work,
                    compile_project=not args.stage_only,
                    reuse_compiled=args.reuse_compile))
                print(f"{conversation.sample_id}: staged "
                      f"{ingests[-1].documents} sessions to "
                      f"{ingests[-1].corpus_dir}", file=sys.stderr)
            if args.stage_only:
                continue
            for name in arms:
                if name in refused:
                    continue
                try:
                    if name == "tesserae":
                        assert memory is not None
                        # ONE search, then N generations off it. The retrieval
                        # does not vary between replicates; only the backbone
                        # does, and isolating that is the point of replicates.
                        retrieved, evidence, errors = search_conversation(
                            memory, conversation, k=search_k,
                            answer_k=args.answer_k,
                            expand_evidence=args.answer_evidence == "source",
                            build_evidence=answering,
                            # Always, not `not answering`. Retrieval used to go
                            # silent whenever the run was also answering,
                            # because its progress would have duplicated the
                            # answering loop's. That was free when retrieval
                            # cost 8 seconds. With `--rerank` it costs 199
                            # searches x 40 candidates and takes ~37 minutes on
                            # MPS, and a phase that prints NOTHING for 37
                            # minutes is indistinguishable from a hang — this
                            # session killed a healthy run at 34 minutes on
                            # exactly that evidence. The two phases are
                            # sequential, so printing both interleaves nothing.
                            progress=True)
                        if answering:
                            assert answer_fn is not None
                            for replicate in range(args.replicates):
                                answer_rows += answer_conversation(
                                    conversation, evidence, errors, answer_fn,
                                    replicate=replicate,
                                    evidence_content=args.answer_evidence,
                                    modal_gate=args.modal_gate)
                    else:
                        # One arm per CONVERSATION: an index carried across
                        # conversations would rank a question against a corpus
                        # it was never asked about.
                        arm = _BASELINE_ARMS[name](staged_documents(conversation))
                        arm_objects[name] = arm
                        retrieved = retrieve_conversation(
                            arm, conversation, k=search_k)
                except Skip as refusal:
                    refused[name] = refusal
                    retrieval_by_arm[name] = []
                    arm_objects.pop(name, None)
                    if name == "tesserae":
                        answer_rows.clear()
                    print(f"SKIP ({name} arm): {refusal.what}\n"
                          f"      {refusal.remedy}", file=sys.stderr)
                    continue
                retrieval_by_arm[name] += retrieval_rows(
                    conversation, alignment, retrieved)

        if args.stage_only:
            print(f"\nNOTHING HAS BEEN COMPILED. "
                  f"{sum(i.documents for i in ingests):,} session documents are "
                  f"under {work}.\nRe-run without --stage-only (and with "
                  f"--i-know-this-costs-money) to compile and answer.")
            return 0

        scored_arms = [name for name in arms if name not in refused]
        if not scored_arms:
            raise next(iter(refused.values()))

        embedder = (getattr(memory.embedding_backend(), "name", None)
                    if memory is not None else None)
        retrieval = {
            name: score_at_ks(
                retrieval_by_arm[name],
                system=("Tesserae" if name == "tesserae"
                        else _BASELINE_ARMS[name].name),
                ks=ks,
                meta={
                    "corpus": f"{sum(len(c.sessions) for c in chosen):,} session "
                              f"documents over {len(chosen)} conversation(s)",
                    "embedder": (embedder if name == "tesserae"
                                 else arm_objects[name].embedder),
                },
            )
            for name in scored_arms if retrieval_by_arm[name]
        }

        graded = _grade_rows(answer_rows, chosen, judge) if answer_rows else []
        decomposition = decompose(graded) if graded else None
        spreads = replicate_spread(graded) if graded else {}
        _fold_verdicts(answer_rows, graded)

        meta: Dict[str, Any] = {
            "answer_shape": ANSWER_SHAPE,
            "llm_model": args.backbone if answering else "",
            "embedding_model": embedder or LOCAL_EMBEDDING_PREFER,
            # "" rather than an omitted key: a result whose meta is silent about
            # reranking is a result from before the stage existed, and one that
            # says "" ran the shipped fused ranking. Those are different claims.
            "rerank_model": args.rerank or "",
            "rerank_overfetch": args.rerank_overfetch if args.rerank else 0,
            "rerank_max_length": args.rerank_max_length if args.rerank else 0,
            # False rather than an omitted key, on the same terms as
            # `rerank_model`: a result whose meta is silent about fan-out
            # predates the stage, and one that says False ran the shipped
            # single-pass ranking. The knobs read 0 when the stage is off so a
            # sweep cannot be mistaken for a run that used them.
            "fanout": bool(args.fanout),
            "fanout_overfetch": args.fanout_overfetch if args.fanout else 0,
            "source_cap": args.source_cap if args.fanout else 0,
            "ubiquity_df_ratio": args.ubiquity_df_ratio if args.fanout else 0.0,
            "extra_facets": args.extra_facets if args.fanout else 0,
            # NOT gated on --fanout: this one changes what the backbone reads
            # whether or not the fan-out ran, so a silent key would be a lie.
            "prefer_anchor_text": bool(args.prefer_anchor_text),
            # False/0 rather than omitted, on the same terms as `fanout`: a
            # result whose meta is silent about tiering predates the stage, and
            # one that says False assembled whole sessions the shipped way.
            "tiered_evidence": bool(args.tiered_evidence),
            "evidence_receipt_chars": (args.evidence_receipt_chars
                                       if args.tiered_evidence else 0),
            "receipt_window": (args.receipt_window
                               if args.tiered_evidence else 0),
            # False rather than omitted, on the same terms as `fanout`: a result
            # whose meta is silent about the gate predates it, and one that says
            # False answered every question under the single shipped prompt. The
            # per-question branch is on the ROW, not here — this only says
            # whether routing happened at all.
            "modal_gate": bool(args.modal_gate),
            # "shipped"/"deliberate" rather than a bool, and never omitted: a
            # result whose meta is silent about the answering contract predates
            # the flag, and one that says "shipped" answered under the single
            # prompt every earlier run used. The two rubrics' reports come from
            # ONE answers file, so this is what tells a reader which prompt the
            # dual-rubric differential below was produced under.
            "answer_prompt": "deliberate" if args.deliberate else "shipped",
            # "session"/0 rather than omitted, on the same terms as `fanout`: a
            # result whose meta is silent about the unit predates the stage, and
            # one that says "session" pasted whole sessions the shipped way. The
            # budget travels with it because a turn arm's ENTIRE claim is
            # quality at a declared character cost — a coverage number without
            # the budget it was bought at is unreadable.
            "evidence_unit": args.evidence_unit,
            "evidence_pack_chars": (args.evidence_pack_chars
                                    if args.evidence_unit == "turn" else 0),
            "turn_pool": (args.turn_pool
                          if args.evidence_unit == "turn" else ""),
            # 0 rather than omitted, on the same terms as `turn_emit_window`: a
            # result silent about these predates the stage, and one that says 0
            # pooled exactly the retrieved sessions and opened as many session
            # blocks as it liked. They travel TOGETHER because neither is the
            # design on its own — widening without the cap raises the distractor
            # count Levy's penalty aims at, and the cap without widening is
            # strictly worse than today.
            "turn_pool_k": (args.turn_pool_k
                            if args.evidence_unit == "turn" else 0),
            "turn_session_cap": (args.turn_session_cap
                                 if args.evidence_unit == "turn" else 0),
            "turn_emit_window": (args.turn_emit_window
                                 if args.evidence_unit == "turn" else 0),
            # From the CONSTANT, not a flag — it has none, because the design
            # decided it (+0.023 turn coverage at a fixed budget) and a sweep
            # would report noise. Declared anyway, on the same terms as
            # `evidence_source_chars`: it changes WHICH turns the backbone read,
            # and a run scored before and after a change to it are not the same
            # measurement.
            "turn_score_window": (TURN_SCORE_WINDOW
                                  if args.evidence_unit == "turn" else 0),
            "turn_heads": (args.turn_heads
                           if args.evidence_unit == "turn" else ""),
            # What the pack actually spent, against what it was allowed to.
            # `pack_sessions` is the distinct-session count the Levy distractor
            # risk is argued on, so it belongs beside the budget rather than in
            # a notebook.
            "pack_chars": memory.pack_chars if memory else 0,
            "pack_turns": memory.pack_turns if memory else 0,
            "pack_sessions": memory.pack_sessions if memory else 0,
            # What the tier actually spent and what the GRAPH allowed it to.
            # `witness_yield` travels beside the spend deliberately: it is the
            # ceiling on redeemable receipts, and a redeemability number quoted
            # without it is unbounded. `unresolvable_spans` and
            # `dangling_receipts` are the compile tripwires — receipts are
            # recovered entirely through the `[D<n>:<t>]` marker
            # `render_session` writes, so a change to the staged rendering
            # zeroes them and the coverage instrument at the same time, in
            # silence, and these two counters are the only warning.
            "receipt_chars": memory.receipt_chars if memory else 0,
            "receipt_lines": memory.receipt_lines if memory else 0,
            "witness_yield": round(memory.witness_yield, 4) if memory else 0.0,
            "unresolvable_spans": memory.unresolvable_spans if memory else 0,
            "dangling_receipts": memory.dangling_receipts if memory else 0,
            "evidence_budget": args.answer_k,
            "evidence_content": args.answer_evidence,
            "evidence_source_chars": (EVIDENCE_SOURCE_CHARS
                                      if args.answer_evidence == "source" else 0),
            # Declared because it changes what the backbone read. A run scored
            # before this budget existed and a run scored after it are not the
            # same measurement, and nothing else in meta would say so.
            "evidence_extra_source_chars": (EVIDENCE_EXTRA_SOURCE_CHARS
                                            if args.answer_evidence == "source"
                                            else 0),
            "reported_ks": ",".join(str(k) for k in ks),
            "dataset": str(data),
            "dataset_revision": revision,
            "conversations": ",".join(c.sample_id for c in chosen),
            "arms": ",".join(scored_arms),
            "corpus_definition": "dialogue text + all BLIP captions",
            **judge.config,
            "judge_runs": args.replicates,
            **_evidence_chars(answer_rows),
            "evidence": {
                # Calls, not rows. A question whose first reply was empty cost
                # two, and a run that reports 398 while having made 464 is
                # understating what it spent.
                "answer_calls": (len(answer_rows)
                                 + sum(int(r.get("empty_replies") or 0)
                                       for r in answer_rows)),
                "empty_replies": sum(int(r.get("empty_replies") or 0)
                                     for r in answer_rows),
                # Calls whose SHAPE the provider got wrong and the extractor
                # recovered from. Reported beside `empty_replies` rather than
                # folded into it: a fix that made this number invisible would
                # be hiding the defect it exists to answer.
                "answer_shape_recoveries": sum(
                    1 for r in answer_rows
                    if r.get("answer_shape_recovery")
                    not in ("", "answer-key")),
                "llm_judge_calls": judge.llm_calls,
                "canary_calls": canary_calls,
            },
        }

        text = build_report(
            conversations=chosen,
            ingests=ingests,
            alignment=alignment_summary(list(alignments.values())),
            retrieval=retrieval,
            decomposition=decomposition,
            spreads=spreads,
            replicates=args.replicates,
            shortfalls=memory.shortfalls if memory else (),
            meta=meta,
            data=str(data),
        )
        if args.answers_out and not answer_rows:
            print("no answers to write — this run measured retrieval only",
                  file=sys.stderr)
        elif args.answers_out:
            args.answers_out.parent.mkdir(parents=True, exist_ok=True)
            args.answers_out.write_text(
                json.dumps({"meta": meta, "rows": answer_rows},
                           indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            print(f"wrote {args.answers_out}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(text)
        print(f"wrote {args.out}")
        return 0
    except Skip as skip:
        return skip.emit()
    except (DeadBackbone, DeadJudge) as dead:
        # Loud, and NOT exit 0. A Skip means "a prerequisite was missing and
        # nothing ran"; this means "something answered, and what it answered
        # was garbage that would have scored".
        print("\n" + "!" * 72, file=sys.stderr)
        print(f"CANARY FAILED — nothing was measured.\n\n{dead}", file=sys.stderr)
        print("!" * 72, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

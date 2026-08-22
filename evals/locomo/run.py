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
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ..lme_mab.adapter import RefusedToCompileInRepo
from ..lme_mab.baselines import LOCAL_EMBEDDING_PREFER, DenseArm, LexicalArm
from ..qa.run_qa_eval import Skip, _num, _rate, _table
from .adapter import (
    EVIDENCE_EXTRA_SOURCE_CHARS,
    EVIDENCE_SOURCE_CHARS,
    PROTOCOL_BACKBONE,
    PROTOCOL_CONTROLS,
    PROTOCOL_JUDGE,
    PROTOCOL_JUDGE_RUNS,
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
_SYSTEM_PROMPT = (
    "You answer questions about a long conversation using the evidence given. "
    "Each evidence item is stamped with the date of the session it came from. "
    "Reply with the shortest answer that the evidence supports — a name, a "
    "date, a number, yes/no, or a short phrase — and nothing else. Prefer the "
    "evidence's own words for facts it states outright, and reason from it when "
    "the question asks what is likely, implied, or would follow. When the answer "
    "is a time, give a calendar date: resolve relative expressions such as "
    "\"yesterday\", \"last week\" or \"the other day\" against the session date "
    "of the evidence item that states them, and never answer with the relative "
    "expression itself. If the "
    "evidence supports no answer at all, reply exactly: Not mentioned."
)

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


def build_backbone(model: str) -> Callable[[str, Sequence[str]], str]:
    """An ``(question, evidence) -> short answer`` callable on ``model``.

    A closure rather than a class so the tests pass any callable and never
    construct an LLM client.
    """
    from tesserae.llm_json import build_default_json_client

    client = build_default_json_client(model=model)
    if client is None:
        raise Skip(
            f"no LLM client available for the {model} backbone",
            "configure a provider, or run --retrieval-only, which scores "
            "recall and MRR and spends nothing",
        )

    def answer(question: str, evidence: Sequence[str]) -> str:
        numbered = "\n\n".join(f"[{i}] {text}"
                               for i, text in enumerate(evidence, start=1))
        payload = client.complete_json(
            system=_SYSTEM_PROMPT,
            user=f"Evidence:\n{numbered}\n\nQuestion: {question}",
            schema_name="locomo_answer",
        )
        if isinstance(payload, Mapping) and payload.get("answer") is not None:
            return str(payload["answer"])
        # No answer key is neither a refusal nor an answer. "" is the honest
        # reading of "the system returned nothing" — and the canary above is
        # what stops a whole run of them being read as caution.
        return ""

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
            evidence.append(memory.answer_evidence(budget, expand=expand_evidence))
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
        if failure:
            answer = failure
        else:
            for _ in range(_EMPTY_ANSWER_RETRIES + 1):
                try:
                    answer = answer_fn(question.question, items)
                except Exception as exc:  # the backbone failed, not the search
                    answer = f"Error: {exc}"
                    break
                if str(answer or "").strip():
                    break
                empty_replies += 1
                answer = _EMPTY_ANSWER
        rows.append({
            # The provider's own failure rate, persisted per question rather
            # than left in a log. 1 with an answer beside it means the retry
            # saved the question; 2 means it did not.
            "empty_replies": empty_replies,
            "key": question_key(question, index),
            "arm": arm,
            "replicate": replicate,
            "conversation": conversation.sample_id,
            "question_index": index,
            "question": question.question,
            "category": question.category,
            "answer": answer,
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
    parser.add_argument("--semicolon-gold", action="store_true",
                        help="score category 3 on the first `;`-separated "
                             "clause only, as the reference harness does")
    parser.add_argument("--embedding-prefer", default=LOCAL_EMBEDDING_PREFER,
                        help=f"embedding backend preference (default: "
                             f"{LOCAL_EMBEDDING_PREFER}, local and free)")
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
    args = build_parser().parse_args(argv)

    # Guard 1 — CI, before anything reads a file.
    if os.environ.get("CI"):
        print("SKIP: CI is set — the LoCoMo arm never runs in CI\n"
              "      it compiles ten conversations and grades 1,986 questions; "
              "run it by hand instead")
        return 0

    try:
        judge = build_judge(args.judge, split_semicolon_gold=args.semicolon_gold)
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

        memory = LocomoMemory(embedding_prefer=args.embedding_prefer) if spends else None
        answer_fn = build_backbone(args.backbone) if answering else None

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
                            progress=not answering)
                        if answering:
                            assert answer_fn is not None
                            for replicate in range(args.replicates):
                                answer_rows += answer_conversation(
                                    conversation, evidence, errors, answer_fn,
                                    replicate=replicate,
                                    evidence_content=args.answer_evidence)
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

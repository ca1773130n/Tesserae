"""The judge, as a BOUNDARY.

LoCoMo's published headline numbers are produced by an LLM grading free-text
answers against short gold strings. This machine cannot run that grader today —
the key it would need has no credit — and the response to that is not to
hardcode a substitute and hope. It is to make "what graded these answers" a
value the run declares, with more than one implementation behind it:

* :class:`DeterministicJudge` grades with :mod:`evals.qa.scorer` — exact match,
  token F1, and the abstention rule for the adversarial category. It calls no
  model, costs nothing, reproduces exactly, and is what runs today.
* :class:`LLMJudge` grades with a model named at construction. Its prompt is
  the published Protocol-B grader's, verbatim. Swapping ``gpt-4o-mini`` in when
  there is credit is ``--judge llm:gpt-4o-mini`` — a flag, not a rewrite.

The protocol gate in :func:`evals.locomo.adapter.protocol_blockers` reads the
judge's declaration and the COUNT of judge calls a model actually served, so a
deterministic run cannot unlock a published-comparable table by declaring a
model name. It declares zero LLM judge calls, and zero is a blocker.

Both judges expose :meth:`Judge.canary`, and the runner calls it before any
measured pass. A judge stuck on one label is as invisible in a final number as a
dead backbone is, so the canary grades a pair it knows the answer to in BOTH
directions and refuses if either comes back wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

from ..qa.run_qa_eval import Skip
from ..qa.scorer import exact_match, is_error, is_refusal, token_f1
from .dataset import LocomoQuestion

#: The reference harness's adversarial rule, verbatim in effect: a category-5
#: answer is correct when it contains "no information available" or "not
#: mentioned", lowercased. It is far narrower than
#: :func:`evals.qa.scorer.is_refusal`, which carries twenty markers, so a modern
#: model that declines in any other words is scored WRONG by the published rule
#: and CORRECT by ours. Both are computed on every adversarial question and both
#: are printed: this is the one place where a rule choice moves 446 of 1,986
#: questions, and a report that shows one number hides that.
_REFERENCE_ABSTENTION = ("no information available", "not mentioned")


def reference_abstains(answer: Optional[str]) -> bool:
    """The published category-5 rule. See :data:`_REFERENCE_ABSTENTION`."""
    lowered = str(answer or "").lower()
    return any(marker in lowered for marker in _REFERENCE_ABSTENTION)


@dataclass(frozen=True)
class Verdict:
    """One question graded. ``score`` is graded, ``correct`` is binary.

    Both are kept because the two published protocol families disagree about
    which one a headline is: the paper reports token F1 (continuous), and every
    LLM-judge protocol since reports a binary accuracy. A verdict carrying only
    one of them cannot produce the other, and re-running to get it is exactly
    the thing that does not reproduce.
    """

    correct: bool
    score: float
    label: str
    judge: str
    #: True when the answer declined. Held separately from ``correct`` because
    #: on the adversarial category they coincide, and everywhere else a refusal
    #: is a zero that must be counted as a refusal rather than as a wrong answer.
    refused: bool = False
    errored: bool = False
    #: The published rule's verdict on an adversarial question, where it differs
    #: from this judge's. ``None`` on every other question.
    reference_correct: Optional[bool] = None
    detail: str = ""


class Judge:
    """The interface. ``name`` and ``config`` are declared onto every artifact."""

    name: str = "judge"

    @property
    def config(self) -> Dict[str, Any]:
        """What a reader needs to reproduce this judge's verdicts."""
        raise NotImplementedError

    @property
    def llm_calls(self) -> int:
        """How many verdicts a MODEL served. Zero for a deterministic judge."""
        return 0

    def canary(self) -> None:
        """Prove the judge distinguishes a right answer from a wrong one.

        Raises :class:`DeadJudge` otherwise. Called before any measured pass.
        """
        raise NotImplementedError

    def grade(self, question: LocomoQuestion, answer: Optional[str]) -> Verdict:
        raise NotImplementedError


class DeadJudge(RuntimeError):
    """The judge failed its canary. Loud, and never a Skip.

    A :class:`Skip` prints and exits 0, which is right for a missing input and
    wrong for a grader that is answering. A judge that returns the same label
    for a right answer and a wrong one produces a complete, plausible,
    meaningless report, and the run must stop rather than write it.
    """


class DeadBackbone(RuntimeError):
    """The answering backbone failed its canary. Loud, and never a Skip.

    This is the failure mode that makes the canary mandatory rather than nice.
    A provider chain handed a model it does not have returns ``None``; the
    runner turns ``None`` into ``""``; ``is_refusal("")`` is True. The run then
    prints ``refusal_rate 1.000`` with ``error_rate 0.000`` — a system that looks
    cautious rather than broken. On LoCoMo it is worse than that: abstention is
    the GOLD on the 446 adversarial questions, so a wholly dead backbone scores
    446 of 446 there and produces this project's best-looking headline.
    """


# --------------------------------------------------------------------------
# The deterministic judge
# --------------------------------------------------------------------------

#: The canary pair. Deliberately trivial and deliberately fixed: the point is to
#: catch a grader that cannot tell the two apart, not to probe its edges.
CANARY_QUESTION = "What colour was the bicycle Priya bought in March?"
CANARY_GOLD = "teal"
CANARY_RIGHT = "teal"
CANARY_WRONG = "a silver kettle"


def _canary_question() -> LocomoQuestion:
    return LocomoQuestion(
        question=CANARY_QUESTION,
        category=4,
        evidence=["D1:1"],
        conversation="canary",
        answer=CANARY_GOLD,
    )


class DeterministicJudge(Judge):
    """Grade with :mod:`evals.qa.scorer`. No model, no network, no cost.

    Two decisions, both stated rather than tuned:

    * **Binary correctness is EXACT MATCH after normalisation, and the graded
      score is token F1.** Turning an F1 into a binary needs a threshold, and a
      threshold is a knob that moves every headline; exact match needs none. The
      paper's own metric is token F1, so that is what ``score`` carries, and the
      report prints both rather than choosing.
    * **The adversarial category is graded by abstention**, with
      :func:`evals.qa.scorer.is_refusal` as the rule and the published narrower
      rule recorded beside it in :attr:`Verdict.reference_correct`.

    A harness ``Error:`` string is neither correct nor a refusal — it is a
    broken call, and counting it as caution is how a broken run reads as a
    careful one.
    """

    name = "deterministic"

    def __init__(self, *, split_semicolon_gold: bool = False) -> None:
        self._split_semicolon = split_semicolon_gold

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "judge": self.name,
            "judge_scorer": "evals/qa/scorer.py",
            "judge_binary_rule": "exact match after normalize_answer",
            "judge_graded_rule": "token F1",
            "judge_adversarial_rule": "evals.qa.scorer.is_refusal",
            "judge_semicolon_gold": self._split_semicolon,
            "judge_runs": 1,
        }

    def canary(self) -> None:
        question = _canary_question()
        right = self.grade(question, CANARY_RIGHT)
        wrong = self.grade(question, CANARY_WRONG)
        if not right.correct or wrong.correct:
            raise DeadJudge(
                f"the deterministic judge graded {CANARY_RIGHT!r} as "
                f"{right.label} and {CANARY_WRONG!r} as {wrong.label} against "
                f"gold {CANARY_GOLD!r}; it cannot tell a right answer from a "
                f"wrong one, so nothing it grades means anything"
            )

    def grade(self, question: LocomoQuestion, answer: Optional[str]) -> Verdict:
        errored = is_error(answer)
        refused = is_refusal(answer)
        if question.is_adversarial:
            correct = refused and not errored
            return Verdict(
                correct=correct,
                score=1.0 if correct else 0.0,
                label="ABSTAINED" if correct else ("ERROR" if errored else "ANSWERED"),
                judge=self.name,
                refused=refused,
                errored=errored,
                reference_correct=reference_abstains(answer) and not errored,
                detail="adversarial: the gold behaviour is to decline",
            )
        golds = question.gold_answers(split_semicolon=self._split_semicolon)
        if not golds:
            return Verdict(
                correct=False, score=0.0, label="NO-GOLD", judge=self.name,
                refused=refused, errored=errored,
                detail="the file carried no answer for this question",
            )
        best_f1 = 0.0
        best_em = False
        for gold in golds:
            best_f1 = max(best_f1, float(token_f1(answer, gold)["f1"]))
            best_em = best_em or exact_match(answer, gold)
        return Verdict(
            correct=best_em and not errored,
            score=0.0 if errored else best_f1,
            label="ERROR" if errored else ("CORRECT" if best_em else "WRONG"),
            judge=self.name,
            refused=refused,
            errored=errored,
        )


# --------------------------------------------------------------------------
# The LLM judge
# --------------------------------------------------------------------------

#: The Protocol-B grader's system prompt, verbatim. This is the prompt behind
#: the ~66% era of LoCoMo numbers and it has been copied unchanged into most
#: memory-benchmark harnesses since, which is precisely why it is reproduced
#: exactly rather than paraphrased: a judge prompt that drifts is a judge, and
#: the point of this class is to be the SAME judge.
JUDGE_SYSTEM_PROMPT = (
    "You are an expert grader that determines if answers to questions match a "
    "gold standard answer"
)

#: The Protocol-B grader's user prompt, verbatim, with the three fields it
#: formats. Its leniency is part of the published protocol and is not softened
#: here — the date clause in particular is what an independent audit identifies
#: as reintroducing the errors it was written to forgive, and a harness that
#: quietly tightened it would not be measuring the published protocol.
JUDGE_USER_PROMPT = """Your task is to label an answer to a question as 'CORRECT' or 'WRONG'. You will be given the following data:
    (1) a question (posed by one user to another user),
    (2) a 'gold' (ground truth) answer,
    (3) a generated answer
which you will score as CORRECT/WRONG.

The point of the question is to ask about something one user should know about the other user based on their prior conversations.
The gold answer will usually be a concise and short answer that includes the referenced topic, for example:
Question: Do you remember what I got the last time I went to Hawaii?
Gold answer: A shell necklace
The generated answer might be much longer, but you should be generous with your grading - as long as it touches on the same topic as the gold answer, it should be counted as CORRECT.

For time related questions, the gold answer will be a specific date, month, year, etc. The generated answer might be much longer or use relative time references (like "last Tuesday" or "next month"), but you should be generous with your grading - as long as it refers to the same date or time period as the gold answer, it should be counted as CORRECT. Even if the format differs (e.g., "May 7th" vs "7 May"), consider it CORRECT if it's the same date.

Now it's time for the real question:
Question: {question}
Gold answer: {gold_answer}
Generated answer: {response}

First, provide a short (one sentence) explanation of your reasoning, then finish with CORRECT or WRONG.
Do NOT include both CORRECT and WRONG in your response, or it will break the evaluation script.

Just return the label CORRECT or WRONG in a json format with the key as "label"."""

_LABEL = re.compile(r"correct|wrong", re.IGNORECASE)


class LLMJudge(Judge):
    """Grade with a named model, using the published Protocol-B prompt.

    ``client_factory`` is an injection point, not decoration: every test of this
    class passes a stub, because the real client is a metered call and a judge
    whose wiring can only be checked by spending money does not get checked.

    Three behaviours are the published grader's and are reproduced deliberately:
    the label space is strictly binary, the temperature is 0, and a reply that
    does not parse is scored WRONG. The last of those is a real hazard — it lets
    a broken judge deflate every arm equally and silently — so unparseable
    replies are COUNTED in :attr:`n_unparseable` and printed. The reference
    harness calls ``exit()`` on an exception instead; this records the failure
    on the row and lets the other questions finish, on the same reasoning
    ``evals/lme_mab/run.py`` separates a failed search from a failed answer.

    The adversarial category is NOT sent to this judge. 444 of its 446 questions
    carry no gold answer to grade against, so there is nothing to put in the
    prompt's ``Gold answer:`` slot; those questions are graded by abstention,
    exactly as :class:`DeterministicJudge` grades them, and the report says which
    rule scored which category.
    """

    name = "llm"

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0.0,
        client_factory: Optional[Callable[[str], Any]] = None,
        split_semicolon_gold: bool = False,
    ) -> None:
        if not str(model).strip():
            raise Skip(
                "--judge llm: needs a model name",
                "pass --judge llm:gpt-4o-mini — the published protocol fixes "
                "that model, and naming it is what the protocol gate checks",
            )
        self.model = str(model).strip()
        self.temperature = float(temperature)
        self._client_factory = client_factory
        self._client: Any = None
        self._abstention = DeterministicJudge(split_semicolon_gold=split_semicolon_gold)
        self._split_semicolon = split_semicolon_gold
        self._llm_calls = 0
        #: Replies that carried no CORRECT/WRONG label. Scored WRONG, as the
        #: reference does, and counted here so a judge that stopped answering
        #: cannot masquerade as a benchmark full of wrong answers.
        self.n_unparseable = 0

    @property
    def config(self) -> Dict[str, Any]:
        return {
            "judge": self.model,
            "judge_family": self.name,
            "judge_temperature": self.temperature,
            "judge_prompt": "LoCoMo Protocol B grader, verbatim",
            "judge_adversarial_rule": "evals.qa.scorer.is_refusal (not sent to "
                                      "the model — 444 of 446 carry no gold)",
            "judge_semicolon_gold": self._split_semicolon,
        }

    @property
    def llm_calls(self) -> int:
        return self._llm_calls

    def _resolve_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory(self.model)
        else:
            from tesserae.llm_json import (
                build_default_json_client, build_rotating_client,
            )

            # Route by MODEL FAMILY, not by the default chain's preference.
            # `build_default_json_client` sent "sonnet" to the Codex CLI, which
            # answers `The 'sonnet' model is not supported when using Codex with
            # a ChatGPT account` — so the judge graded both "teal" and "a silver
            # kettle" as UNPARSEABLE against gold "teal". The canary caught it
            # and refused to run, which is the only reason it is not a silent
            # 0.000 in a report.
            #
            # A cross-provider judge is also the point when the published
            # grader is unreachable: the answerer must not grade itself, and
            # "which provider owns this model" is a property of the name, not a
            # preference the caller should have to encode.
            family = str(self.model or "").casefold()
            if any(tag in family for tag in ("sonnet", "haiku", "opus", "claude")):
                self._client = build_rotating_client(
                    model_claude=self.model, model_codex=self.model,
                    provider="claude",
                )
            else:
                self._client = build_default_json_client(model=self.model)
        if self._client is None:
            raise Skip(
                f"no LLM client available for the {self.model} judge",
                "configure a provider, or run --judge deterministic, which "
                "grades with evals/qa/scorer.py and declares that it did",
            )
        return self._client

    def canary(self) -> None:
        """Grade a right answer and a wrong one. Both must come back right."""
        question = _canary_question()
        right = self.grade(question, CANARY_RIGHT)
        wrong = self.grade(question, CANARY_WRONG)
        if not right.correct or wrong.correct:
            raise DeadJudge(
                f"the {self.model} judge graded {CANARY_RIGHT!r} as "
                f"{right.label} and {CANARY_WRONG!r} as {wrong.label} against "
                f"gold {CANARY_GOLD!r}. A judge that cannot separate those two "
                f"produces a complete, plausible, meaningless report — every "
                f"arm scored by it would move together and none of it would "
                f"mean anything. {right.detail or wrong.detail}"
            )

    def grade(self, question: LocomoQuestion, answer: Optional[str]) -> Verdict:
        if question.is_adversarial:
            # No gold string exists to grade against; abstention is the rule.
            return self._abstention.grade(question, answer)
        golds = question.gold_answers(split_semicolon=self._split_semicolon)
        if not golds:
            return Verdict(correct=False, score=0.0, label="NO-GOLD",
                           judge=self.model,
                           detail="the file carried no answer for this question")
        if is_error(answer):
            # A harness error never reaches the judge: grading a traceback
            # spends a call to learn something the string already said.
            return Verdict(correct=False, score=0.0, label="ERROR",
                           judge=self.model, errored=True,
                           detail=str(answer)[:200])
        client = self._resolve_client()
        prompt = JUDGE_USER_PROMPT.format(
            question=question.question,
            gold_answer=golds[0],
            response=answer or "",
        )
        self._llm_calls += 1
        payload = client.complete_json(
            system=JUDGE_SYSTEM_PROMPT,
            user=prompt,
            schema_name="locomo_judge_label",
        )
        label = ""
        if isinstance(payload, Mapping):
            label = str(payload.get("label") or "")
        match = _LABEL.search(label)
        if not match:
            self.n_unparseable += 1
            return Verdict(
                correct=False, score=0.0, label="UNPARSEABLE", judge=self.model,
                refused=is_refusal(answer),
                detail=f"judge returned {label[:80]!r}",
            )
        correct = match.group(0).lower() == "correct"
        return Verdict(
            correct=correct,
            score=1.0 if correct else 0.0,
            label="CORRECT" if correct else "WRONG",
            judge=self.model,
            refused=is_refusal(answer),
        )


def build_judge(spec: str, *, split_semicolon_gold: bool = False,
                client_factory: Optional[Callable[[str], Any]] = None) -> Judge:
    """``deterministic`` or ``llm:<model>`` — the whole judge boundary, as a flag.

    This function is the reason "swap gpt-4o-mini in later" is a config change
    rather than a rewrite. Nothing above it names a grader; nothing below it
    knows which one ran.
    """
    text = str(spec or "").strip()
    if text in ("", "deterministic", "det"):
        return DeterministicJudge(split_semicolon_gold=split_semicolon_gold)
    family, _, model = text.partition(":")
    if family.strip().lower() in ("llm", "model"):
        return LLMJudge(model, client_factory=client_factory,
                        split_semicolon_gold=split_semicolon_gold)
    raise Skip(
        f"--judge {spec!r} names no judge this harness has",
        "pass --judge deterministic (free, reproducible, runs today) or "
        "--judge llm:gpt-4o-mini (the published grader, needs credit)",
    )


__all__ = [
    "CANARY_GOLD",
    "CANARY_QUESTION",
    "CANARY_RIGHT",
    "CANARY_WRONG",
    "DeadBackbone",
    "DeadJudge",
    "DeterministicJudge",
    "JUDGE_SYSTEM_PROMPT",
    "JUDGE_USER_PROMPT",
    "Judge",
    "LLMJudge",
    "Verdict",
    "build_judge",
    "reference_abstains",
]

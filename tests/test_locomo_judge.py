"""LoCoMo judge — the boundary, both implementations, and both canaries.

Offline: the LLM judge runs against a stub client, on the same reasoning the
adapter stubs its search lane. A judge whose wiring can only be checked by
spending money does not get checked, and this one has to be right before there
is any money to spend on it.

What is pinned:

* the judge is chosen by a string, so adding ``gpt-4o-mini`` later is config;
* the deterministic judge grades the adversarial category by abstention, and
  records the published narrower rule beside its own;
* a harness ``Error:`` is neither correct nor a refusal;
* both canaries catch a grader stuck on one label, in BOTH directions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from evals.locomo.dataset import LocomoQuestion
from evals.locomo.judge import (
    JUDGE_SYSTEM_PROMPT,
    JUDGE_USER_PROMPT,
    DeadJudge,
    DeterministicJudge,
    LLMJudge,
    build_judge,
    reference_abstains,
)
from evals.qa.run_qa_eval import Skip


def _question(category: int = 4, answer: Optional[str] = "teal",
              adversarial: Optional[str] = None) -> LocomoQuestion:
    return LocomoQuestion(
        question="What colour was the bike?", category=category,
        evidence=["D1:1"], conversation="conv-test", answer=answer,
        adversarial_answer=adversarial,
    )


class _StubClient:
    """A judge client that answers from a script and records what it was asked."""

    def __init__(self, labels: List[Any]) -> None:
        self.labels = list(labels)
        self.calls: List[Dict[str, str]] = []

    def complete_json(self, *, system: str, user: str, schema_name: str,
                      **_: Any) -> Any:
        self.calls.append({"system": system, "user": user,
                           "schema_name": schema_name})
        return self.labels.pop(0) if self.labels else None


class _AlwaysCorrect(_StubClient):
    def complete_json(self, **kwargs: Any) -> Any:
        super().complete_json(**kwargs)
        return {"label": "CORRECT"}


# ---------------------------------------------------------------- the boundary


def test_the_judge_is_chosen_by_a_string():
    assert isinstance(build_judge("deterministic"), DeterministicJudge)
    assert isinstance(build_judge(""), DeterministicJudge)
    judge = build_judge("llm:gpt-4o-mini")
    assert isinstance(judge, LLMJudge) and judge.model == "gpt-4o-mini"


def test_an_unknown_judge_refuses_by_name():
    with pytest.raises(Skip):
        build_judge("vibes")


def test_an_llm_judge_without_a_model_refuses():
    with pytest.raises(Skip):
        build_judge("llm:")


def test_each_judge_declares_what_it_is():
    """The declaration is what the protocol gate reads, so it must be honest."""
    assert build_judge("deterministic").config["judge"] == "deterministic"
    assert build_judge("llm:gpt-4o-mini").config["judge"] == "gpt-4o-mini"


def test_the_deterministic_judge_serves_no_model_calls():
    """Which is why the protocol gate blocks a deterministic run. See
    ``tests/test_locomo_adapter.py``."""
    assert build_judge("deterministic").llm_calls == 0


# --------------------------------------------------- the deterministic judge


def test_a_matching_answer_is_correct_and_scores_one():
    verdict = DeterministicJudge().grade(_question(), "teal")
    assert verdict.correct and verdict.score == 1.0 and verdict.label == "CORRECT"


def test_normalisation_decides_exact_match_not_raw_equality():
    assert DeterministicJudge().grade(_question(), "The teal.").correct


def test_a_partly_right_answer_is_wrong_but_keeps_its_graded_score():
    """Binary correctness needs no threshold; the graded score is token F1.

    Both travel on the verdict because the paper's headline is F1 and every
    LLM-judge protocol since reports a binary accuracy — a verdict carrying one
    cannot produce the other.
    """
    question = _question(answer="a teal racing bicycle")
    verdict = DeterministicJudge().grade(question, "a teal bicycle")
    assert not verdict.correct
    assert 0.0 < verdict.score < 1.0


def test_a_refusal_is_recorded_as_a_refusal_and_scores_zero():
    verdict = DeterministicJudge().grade(_question(), "I don't know.")
    assert verdict.refused and not verdict.correct and verdict.score == 0.0


def test_an_empty_answer_is_a_refusal():
    """None becomes "", and "" is how a dead backbone looks. It must not read
    as an answer."""
    assert DeterministicJudge().grade(_question(), "").refused


def test_a_harness_error_is_neither_correct_nor_a_refusal():
    """Counting a broken call as caution is how a broken run reads as a careful one."""
    verdict = DeterministicJudge().grade(_question(), "Error: 429 rate limited")
    assert verdict.errored and not verdict.correct and not verdict.refused


def test_the_semicolon_rule_changes_the_verdict_when_it_is_asked_for():
    question = LocomoQuestion(question="q", category=3, evidence=["D1:1"],
                              conversation="c", answer="a bakery; a florist")
    assert DeterministicJudge(split_semicolon_gold=True).grade(
        question, "a bakery").correct
    assert not DeterministicJudge().grade(question, "a bakery").correct


# ----------------------------------------------------------- the adversarial


def test_declining_an_adversarial_question_is_correct():
    verdict = DeterministicJudge().grade(
        _question(category=5, answer=None, adversarial="a canoe"),
        "I don't know.")
    assert verdict.correct and verdict.label == "ABSTAINED"


def test_answering_an_adversarial_question_is_wrong():
    verdict = DeterministicJudge().grade(
        _question(category=5, answer=None, adversarial="a canoe"), "a canoe")
    assert not verdict.correct and verdict.label == "ANSWERED"


def test_the_published_narrower_rule_is_recorded_beside_ours():
    """The one place a rule choice moves 446 of 1,986 questions.

    "I don't know" is a refusal under ``evals/qa/scorer.py``'s twenty markers
    and is NOT one of the two phrases the reference harness accepts, so the two
    rules disagree on exactly this answer — and the report prints both columns.
    """
    verdict = DeterministicJudge().grade(
        _question(category=5, answer=None), "I don't know.")
    assert verdict.correct
    assert verdict.reference_correct is False


def test_the_published_rule_accepts_its_own_two_phrases():
    assert reference_abstains("No information available in the conversation.")
    assert reference_abstains("That was not mentioned.")
    assert not reference_abstains("I don't know.")


def test_an_errored_adversarial_answer_is_not_scored_as_abstention():
    """A dead backbone must not collect the adversarial category for free."""
    verdict = DeterministicJudge().grade(
        _question(category=5, answer=None), "Error: no client")
    assert not verdict.correct and verdict.errored


# ------------------------------------------------------------- the LLM judge


def test_the_llm_judge_sends_the_published_prompt_verbatim():
    client = _StubClient([{"label": "CORRECT"}])
    judge = LLMJudge("gpt-4o-mini", client_factory=lambda model: client)
    judge.grade(_question(), "teal")
    sent = client.calls[0]
    assert sent["system"] == JUDGE_SYSTEM_PROMPT
    assert sent["user"] == JUDGE_USER_PROMPT.format(
        question="What colour was the bike?", gold_answer="teal", response="teal")


def test_the_llm_judge_counts_the_calls_a_model_served():
    client = _StubClient([{"label": "CORRECT"}, {"label": "WRONG"}])
    judge = LLMJudge("gpt-4o-mini", client_factory=lambda model: client)
    judge.grade(_question(), "teal")
    judge.grade(_question(), "crimson")
    assert judge.llm_calls == 2


def test_a_wrong_label_is_wrong():
    client = _StubClient([{"label": "WRONG"}])
    judge = LLMJudge("gpt-4o-mini", client_factory=lambda model: client)
    assert not judge.grade(_question(), "crimson").correct


def _no_fallthrough(monkeypatch):
    """Stub ``tesserae.llm_json`` so provider choice is observable offline."""
    import tesserae.llm_json as llm_json

    class _Claude:
        def __init__(self, model: Optional[str] = None, **kwargs: Any) -> None:
            self.model, self.kwargs = model, kwargs

    monkeypatch.setattr(llm_json, "ClaudeCLIJsonClient", _Claude, raising=False)
    monkeypatch.setattr(
        llm_json, "build_rotating_client",
        lambda **kw: pytest.fail("the judge composed a provider-fallthrough client"),
        raising=False)
    return _Claude


def test_a_claude_family_judge_gets_one_provider_and_no_fallthrough(monkeypatch):
    """A busy provider must stop the judge, never silently swap it.

    Routing by model family through ``build_rotating_client`` still composed
    Claude, the Anthropic SDK and Codex into a ``CompositeCLIClient`` that falls
    through on exhaustion — and passed it ``model_codex=self.model``, so every
    call reaching the fallback was guaranteed to 400 on a model Codex cannot
    serve. Measured on a 2026-08-22 conv-26 run: the canary passed, then 12 of
    304 judge calls fell through and returned UNPARSEABLE, which this module
    scores WRONG. Four percent of answers marked wrong because the grader's
    provider was busy is exactly the silent failure the canary exists to stop,
    arriving after the canary had already run.
    """
    import tesserae.llm_json as llm_json

    claude = _no_fallthrough(monkeypatch)
    monkeypatch.setattr(
        llm_json, "build_default_json_client",
        lambda **kw: pytest.fail("a Claude model took the default provider chain"),
        raising=False)

    for model in ("sonnet", "claude-sonnet-4-6", "haiku", "opus"):
        client = LLMJudge(model)._resolve_client()
        assert isinstance(client, claude), f"{model} did not resolve to Claude"
        assert client.model == model


def test_a_non_claude_judge_still_takes_the_default_chain(monkeypatch):
    """A non-regression pin: family routing must not capture every model.

    Passes on the parent too. It is here so the narrowing above cannot quietly
    become "every judge is a Claude judge", which would make the published
    ``gpt-4o-mini`` grader unreachable by name.
    """
    import tesserae.llm_json as llm_json

    _no_fallthrough(monkeypatch)
    # The OpenAI-family branch takes the HTTP client whenever a key is in the
    # environment and only falls through to the default chain without one. A
    # developer shell that exports OPENAI_API_KEY therefore never reached the
    # assertion below — this test is about the fallthrough, so pin its premise.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    seen: Dict[str, Any] = {}

    def _default(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return _StubClient([{"label": "CORRECT"}])

    monkeypatch.setattr(llm_json, "build_default_json_client", _default,
                        raising=False)
    assert LLMJudge("gpt-4o-mini")._resolve_client() is not None
    assert seen == {"model": "gpt-4o-mini"}


def test_an_unparseable_reply_scores_wrong_and_is_counted():
    """The reference scores it WRONG, which lets a broken judge deflate every
    arm equally and silently. The score follows the reference; the count is what
    makes the failure visible."""
    client = _StubClient([{"label": "maybe?"}])
    judge = LLMJudge("gpt-4o-mini", client_factory=lambda model: client)
    verdict = judge.grade(_question(), "teal")
    assert not verdict.correct and verdict.label == "UNPARSEABLE"
    assert judge.n_unparseable == 1


def test_a_missing_reply_is_unparseable_rather_than_a_grade():
    client = _StubClient([None])
    judge = LLMJudge("gpt-4o-mini", client_factory=lambda model: client)
    assert judge.grade(_question(), "teal").label == "UNPARSEABLE"


def test_the_adversarial_category_never_reaches_the_model():
    """444 of its 446 questions carry no gold, so there is nothing to grade
    against — and spending a call to find that out would be a call spent on
    nothing."""
    client = _StubClient([{"label": "CORRECT"}])
    judge = LLMJudge("gpt-4o-mini", client_factory=lambda model: client)
    verdict = judge.grade(_question(category=5, answer=None), "I don't know.")
    assert verdict.correct and client.calls == []
    assert judge.llm_calls == 0


def test_a_harness_error_never_reaches_the_model():
    client = _StubClient([{"label": "CORRECT"}])
    judge = LLMJudge("gpt-4o-mini", client_factory=lambda model: client)
    verdict = judge.grade(_question(), "Error: 429 rate limited")
    assert verdict.errored and client.calls == []


def test_the_llm_judge_refuses_when_no_client_can_be_built():
    judge = LLMJudge("gpt-4o-mini", client_factory=lambda model: None)
    with pytest.raises(Skip):
        judge.grade(_question(), "teal")


# --------------------------------------------------------------- the canaries


def test_the_deterministic_canary_passes():
    DeterministicJudge().canary()


def test_a_judge_that_says_correct_to_everything_fails_its_canary():
    """A judge stuck on one label produces a complete, plausible, meaningless
    report. Catching it needs BOTH directions — a canary that only checks the
    right answer would pass this one."""
    judge = LLMJudge("gpt-4o-mini",
                     client_factory=lambda model: _AlwaysCorrect([]))
    with pytest.raises(DeadJudge):
        judge.canary()


def test_a_judge_that_says_wrong_to_everything_fails_its_canary():
    client = _StubClient([{"label": "WRONG"}, {"label": "WRONG"}])
    judge = LLMJudge("gpt-4o-mini", client_factory=lambda model: client)
    with pytest.raises(DeadJudge):
        judge.canary()


def test_a_judge_that_answers_correctly_in_both_directions_passes():
    client = _StubClient([{"label": "CORRECT"}, {"label": "WRONG"}])
    judge = LLMJudge("gpt-4o-mini", client_factory=lambda model: client)
    judge.canary()
    assert judge.llm_calls == 2


def test_a_dead_judge_is_not_a_skip():
    """A Skip prints and exits 0, which is right for a missing input and wrong
    for a grader that is answering."""
    assert not issubclass(DeadJudge, Skip)


def test_the_lenient_rule_set_declares_itself_in_the_artifact():
    """Two graders, and a run that cannot say which one ran is a wrong record.

    `mem0-2026` reproduces Mem0's own grader: partial credit on list golds,
    14-day date tolerance, 50% duration tolerance. It is LENIENT, and on this
    repository's own saved conv-26 answers — the same answers, no system change
    — it moves the fan-out arm 0.816 -> 0.859, almost all of it multi-hop
    (0.766 -> 0.875) where LoCoMo's gold is a list.

    The first version of this threading reported `judge_prompt: "LoCoMo Protocol
    B grader, verbatim"` while running Mem0's rules. That artifact would have
    been quoted against a strict number as though the two were one instrument.
    """
    from evals.locomo.judge import JUDGE_RULE_SETS, build_judge

    assert set(JUDGE_RULE_SETS) == {"protocol-b", "mem0-2026"}

    strict = build_judge("llm:gpt-4o-mini").config
    lenient = build_judge("llm:gpt-4o-mini", judge_rules="mem0-2026").config

    assert strict["judge_rules"] == "protocol-b"
    assert lenient["judge_rules"] == "mem0-2026"
    assert "Protocol B" in strict["judge_prompt"]
    assert "LENIENT" in lenient["judge_prompt"], (
        "a report quoting this number must be able to see, from the artifact "
        "alone, that its grader gives partial credit"
    )
    assert strict["judge_prompt"] != lenient["judge_prompt"]


def test_an_unknown_rule_set_is_refused_rather_than_defaulted():
    """Silently falling back to Protocol B would mislabel every row."""
    from evals.qa.run_qa_eval import Skip

    with pytest.raises(Skip):
        build_judge("llm:gpt-4o-mini", judge_rules="whatever")

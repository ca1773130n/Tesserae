"""LLM plumbing for the lifecycle harness: prompts, clients, a hard call budget.

Three properties every LLM call here has to hold, and where each is enforced:

* **It cannot re-open the self-capture loop.** Each system prompt below opens
  with a signature listed in ``tesserae.harness_sessions._TESSERAE_PROMPT_SIGNATURES``
  (asserted by ``tests/test_lifecycle_eval.py``), so a CLI transcript of a judge
  or reader call is dropped by session discovery like every other Tesserae
  call. The runner also moves its working directory outside the repository
  before the first call, so the CLI files those transcripts under a directory
  no Tesserae project owns. The prompt is identified by how it is PASSED
  (``system=``), never by its wording — see the self-capture memory.
* **A re-run is a re-run.** :func:`fresh_answers` switches the on-disk CLI
  response cache off for the duration of a run. Without it the second run of a
  test-retest pair replays the first run's answers byte for byte and kappa is
  1.0 by construction — agreement with a cache, not with itself.
* **It stops at a budget.** :class:`BudgetedClient` answers ``None`` once the
  invocation's :class:`CallBudget` is spent, which every Tesserae caller
  already treats as a failed call, and the budget counts each refusal so the
  result file says how many of its verdicts never reached the model.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any, Dict, Iterator, Optional

from ..qa.run_qa_eval import Skip

logger = logging.getLogger(__name__)

EXTRACTION_JUDGE_SYSTEM = (
    "You grade a memory extractor against gold memory points. You are given "
    "the gold points a careful annotator stored for one conversation (TARGET "
    "points the system should store, and INTERFERENCE points that are planted "
    "falsehoods it must not store) and the memories an extractor produced. For "
    "each extracted memory, name the ONE gold point that states the same fact "
    "(a paraphrase counts; a related but different fact does not), or none."
)

READER_SYSTEM = (
    "You answer a question from a user's stored memories. Use only the "
    "memories given. When two memories disagree, the one dated later is "
    "current. If the memories do not contain the answer, say that you do not "
    "know rather than guessing. Answer in one or two sentences."
)

#: LongMemEval's own grader sends its rubric as a bare user message. Ours has
#: to carry a system prompt (the CLI clients stitch one in front), so this one
#: says nothing beyond what the verbatim rubric in the user slot already does.
QA_JUDGE_SYSTEM = (
    "You grade an answer against the LongMemEval rubric that follows. Reply "
    "with yes or no only."
)

SYSTEM_PROMPTS = (EXTRACTION_JUDGE_SYSTEM, READER_SYSTEM, QA_JUDGE_SYSTEM)


class CallBudget:
    """One hard cap on LLM calls for a whole invocation, shared by every role
    (extractor, judge, reader), with a per-role tally of what was spent."""

    def __init__(self, max_calls: Optional[int]) -> None:
        self.max_calls = max_calls
        self.calls: Dict[str, int] = {}
        self.failed: Dict[str, int] = {}
        self.refused: Dict[str, int] = {}

    def admit(self, role: str) -> bool:
        if self.max_calls is not None and sum(self.calls.values()) >= self.max_calls:
            if not self.refused:
                logger.warning(
                    "lifecycle eval: LLM budget of %d calls spent; further calls "
                    "are refused and counted, not made", self.max_calls,
                )
            self.refused[role] = self.refused.get(role, 0) + 1
            return False
        self.calls[role] = self.calls.get(role, 0) + 1
        return True

    def note_failure(self, role: str) -> None:
        self.failed[role] = self.failed.get(role, 0) + 1

    def tally(self) -> dict:
        return {"max_calls": self.max_calls, "calls": dict(self.calls),
                "failed": dict(self.failed), "refused_over_budget": dict(self.refused)}


class BudgetedClient:
    """An ``LLMJsonClient`` that draws on a :class:`CallBudget`.

    Past the cap every call returns ``None`` without reaching the model — what
    every Tesserae caller already treats as a failed call — and the budget
    counts it, so the result file says how many verdicts never reached a model.
    """

    def __init__(self, inner: Any, budget: CallBudget, role: str) -> None:
        self.inner = inner
        self.budget = budget
        self.role = role

    def complete_json(self, *, system: str, user: str, schema_name: str,
                      cache_key: Optional[str] = None, max_retries: int = 2):
        if not self.budget.admit(self.role):
            return None
        out = self.inner.complete_json(system=system, user=user, schema_name=schema_name,
                                       cache_key=cache_key, max_retries=max_retries)
        if out is None:
            self.budget.note_failure(self.role)
        return out

    def complete_text(self, *, system: str, user: str, max_retries: int = 2):
        if not self.budget.admit(self.role):
            return None
        out = self.inner.complete_text(system=system, user=user, max_retries=max_retries)
        if out is None:
            self.budget.note_failure(self.role)
        return out


def build_client(model: str) -> Any:
    """One provider per model family, no fallthrough — the routing
    ``evals/locomo/judge.py`` arrived at after a fallback silently changed the
    grader mid-run. A Claude-family name goes to the Claude CLI; anything else
    goes to ``build_default_json_client`` pinned to that model."""
    from tesserae.llm_json import ClaudeCLIJsonClient, build_default_json_client

    name = str(model or "").strip()
    if not name:
        raise Skip("no model named", "pass a model, e.g. --llm haiku")
    family = name.casefold()
    if any(tag in family for tag in ("sonnet", "haiku", "opus", "claude")):
        client = ClaudeCLIJsonClient(model=name)
    else:
        client = build_default_json_client(model=name)
    if client is None:
        raise Skip(f"no LLM client available for {name}",
                   "configure a provider (`tesserae test` proves one answers)")
    return client


def client_identity(client: Any, model: str) -> str:
    inner = getattr(client, "inner", client)
    ident = getattr(inner, "identity", None)
    if callable(ident):
        try:
            return str(ident())
        except Exception as exc:  # identity is diagnostic only; never fatal
            logger.warning("lifecycle eval: identity() of %r failed: %s", inner, exc)
    return f"{type(inner).__name__} model={model}"


@contextlib.contextmanager
def fresh_answers() -> Iterator[None]:
    """Disable the CLI response cache (``TESSERAE_LLM_CACHE=0``) for one run."""
    prior = os.environ.get("TESSERAE_LLM_CACHE")
    os.environ["TESSERAE_LLM_CACHE"] = "0"
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("TESSERAE_LLM_CACHE", None)
        else:
            os.environ["TESSERAE_LLM_CACHE"] = prior

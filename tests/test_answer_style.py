"""Tesserae could not be scored by any standard QA metric until it could answer short.

`evals/qa/scorer.py::ANSWER_SHAPES` states the problem exactly: exact match and
token F1 are computed over the WHOLE answer string, so 60-220 words of cited
prose scores near zero against a one-phrase gold answer however correct it is —
and `fairness_blockers` correctly refuses to publish a comparison across two
shapes. HotpotQA, LongMemEval and MemoryAgentBench all score that way.

So the house style was not a style preference. It was an unstated decision that
this system is unmeasurable next to any competitor, and these tests guard the
mode that undoes it.
"""

from __future__ import annotations

from inspect import signature
from pathlib import Path

import pytest

from tesserae.query import (
    ANSWER_STYLES,
    _SHORT_SPAN_PREAMBLE_HEADER,
    _SYSTEM_PREAMBLE_HEADER,
    WikiQuery,
    ask_project,
)


def test_both_styles_are_reachable_from_the_public_entry_point() -> None:
    assert set(ANSWER_STYLES) == {"prose-cited", "short-span"}
    assert signature(ask_project).parameters["answer_style"].default == "prose-cited", (
        "the default must not change — every existing caller expects cited prose"
    )


def test_short_span_forbids_the_citations_the_default_requires(tmp_path: Path) -> None:
    """Not an oversight in the shape: a bracket citation inside a one-phrase
    answer is scored as answer tokens and penalises the metric this mode exists
    to be scored on."""
    assert "CITE EVERY FACTUAL CLAIM" in _SYSTEM_PREAMBLE_HEADER
    assert "no citations" in _SHORT_SPAN_PREAMBLE_HEADER.lower()
    assert "60-220 words" in _SYSTEM_PREAMBLE_HEADER
    assert "60-220 words" not in _SHORT_SPAN_PREAMBLE_HEADER

    # LEAN ON PURPOSE, and the leanness is the fix — not a style preference.
    # At 1,688 chars this prompt stated "do not invent" three separate ways
    # (persona line, rule 1, "never invent papers, numbers, names, or claims")
    # and Tesserae refused 59.9% of answerable questions where a 391-char
    # baseline refused 6.3%, with 93% of those refusals holding a gold document
    # in the bundle. Re-adding grounding boilerplate here re-creates that.
    assert len(_SHORT_SPAN_PREAMBLE_HEADER) < 600, (
        f"short-span prompt is {len(_SHORT_SPAN_PREAMBLE_HEADER)} chars; "
        "grounding boilerplate converts present evidence into refusals"
    )
    assert "invent" not in _SHORT_SPAN_PREAMBLE_HEADER.lower()
    assert "strictly" not in _SHORT_SPAN_PREAMBLE_HEADER.lower()
    # Exactly one refusal clause, matching the baselines.
    assert _SHORT_SPAN_PREAMBLE_HEADER.count("I don't know") == 1


def test_the_preamble_cache_cannot_serve_one_style_to_the_other(tmp_path: Path) -> None:
    """`_system_blocks` memoises per instance. Switching style on a live
    instance without keying the cache would answer in the previous shape while
    reporting the new one — a fairness declaration that does not match what was
    actually sent."""
    q = WikiQuery(tmp_path, top_k=5)

    q.answer_style = "prose-cited"
    prose = q._system_blocks()[0]["text"]
    q.answer_style = "short-span"
    short = q._system_blocks()[0]["text"]

    assert prose != short, "the cache served the first style's preamble to the second"
    assert "shortest exact answer" in short.lower()
    assert "shortest exact answer" not in prose.lower()
    # The short prompt must not inherit the prose prompt's framing.
    assert len(short) < len(prose) / 3, "short-span picked up the overview/ontology again"


def test_the_default_instance_still_answers_in_the_house_style(tmp_path: Path) -> None:
    """No caller asked for this change; every existing one must be unaffected."""
    q = WikiQuery(tmp_path, top_k=5)
    assert "CITE EVERY FACTUAL CLAIM" in q._system_blocks()[0]["text"]


def test_the_planner_route_honours_the_style_it_is_handed() -> None:
    """The hole in the first fix, found by a 332-question benchmark run.

    `ask_project` routes graph-shaped questions to `plan_and_answer`, which
    builds its OWN WikiQuery. The style was threaded to `wiki.query` and not to
    the planner, so the MAIN route ignored it: Tesserae answered at 87.5 words
    mean against 10-15 for every other arm while declaring `short-span`.
    """
    from inspect import signature

    from tesserae.ask_planner import plan_and_answer

    assert signature(plan_and_answer).parameters["answer_style"].default == "prose-cited"


def test_short_span_is_exempt_from_the_citation_gate() -> None:
    """The planner drops any answer without a bracket citation. Short-span
    FORBIDS citations, so applying the gate there would reject every planner
    answer and fall back to a different retrieval path — making the two styles
    differ in what they retrieved, not only in how they phrased it."""
    import inspect

    from tesserae import ask_planner

    src = inspect.getsource(ask_planner)
    gate = 'answer_style != "short-span" and not NODE_CITATION_RE.search(body)'
    assert gate in src, "the citation gate must be style-aware, or short-span never plans"

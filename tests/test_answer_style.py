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
    assert "NO CITATIONS" in _SHORT_SPAN_PREAMBLE_HEADER
    assert "60-220 words" in _SYSTEM_PREAMBLE_HEADER
    assert "60-220 words" not in _SHORT_SPAN_PREAMBLE_HEADER
    # The rules that are not about shape are house rules and must survive.
    for rule in ("RESTATE, DO NOT INVENT", "NEUTRAL VOICE", "NO FRONTMATTER"):
        assert rule in _SHORT_SPAN_PREAMBLE_HEADER, f"{rule} was dropped"


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
    assert "SHORTEST EXACT ANSWER" in short
    assert "SHORTEST EXACT ANSWER" not in prose


def test_the_default_instance_still_answers_in_the_house_style(tmp_path: Path) -> None:
    """No caller asked for this change; every existing one must be unaffected."""
    q = WikiQuery(tmp_path, top_k=5)
    assert "CITE EVERY FACTUAL CLAIM" in q._system_blocks()[0]["text"]

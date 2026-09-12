"""A heavily-used agent must not be the one agent distill cannot serve.

`ARTIFACT_CHAR_BUDGET` is a one-read bound: an L1 artifact exists to be read in
one go. When the notes overflow it, `fit_to_budget` sheds INDEX entries only —
so on an agent with thousands of sessions, the thing that overflows is exactly
the thing truncation cannot shrink, and the pass refused with no way forward
and no remedy in the message.

Measured on a real project: 4 of 7 agents failed, including every agent with
real history. The bound stays the default, because it protects a real contract;
what was missing was a declared way past it and an error that says so.
"""

from __future__ import annotations

import argparse

import pytest

from tesserae.agent_distill import ARTIFACT_CHAR_BUDGET, DistillOptions


def test_the_bound_is_still_a_module_constant():
    """Not env-configurable, on purpose: two checkouts must render equal bytes."""
    import os

    assert ARTIFACT_CHAR_BUDGET == 48_000
    os.environ["TESSERAE_LLM_CHUNK_CHARS"] = "999"
    try:
        from importlib import reload

        import tesserae.agent_distill as mod

        reload(mod)
        assert mod.ARTIFACT_CHAR_BUDGET == 48_000
    finally:
        os.environ.pop("TESSERAE_LLM_CHUNK_CHARS", None)


def test_options_carry_an_explicit_override():
    assert DistillOptions().artifact_char_budget is None
    assert DistillOptions(artifact_char_budget=80_000).artifact_char_budget == 80_000


def test_the_cli_exposes_the_override_and_defaults_it_off():
    from tesserae.cli import _build_distill_parser

    parser = _build_distill_parser()
    args = parser.parse_args([])
    assert args.artifact_chars is None, "the default must not move the bound"
    assert parser.parse_args(["--artifact-chars", "80000"]).artifact_chars == 80_000


def test_the_size_refusal_names_a_remedy():
    """An error a user cannot act on is a dead end, and this one was the only
    thing standing between a real project and its agent memory."""
    import inspect

    from tesserae import agent_distill

    source = inspect.getsource(agent_distill)
    start = source.index("exceeds the one-read bound")
    window = source[start : start + 900]
    assert "--artifact-chars" in window
    assert "one read" in window, "say what the bound protects, not just that it exists"

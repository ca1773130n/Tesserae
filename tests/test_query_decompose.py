"""Tests for :func:`tesserae.retrieval.query_decompose.decompose_query`.

No network / no real LLM — the LLM path is exercised with hand-rolled stub
clients. Covers: blank input, single-clause passthrough, multi-clause split
with cap + dedupe, a JSON-array-returning stub merged with the original, and
a raising stub falling back deterministically (never raises).
"""

from __future__ import annotations

from typing import List, Optional, Union

from tesserae.retrieval.query_decompose import decompose_query


class _StubArrayClient:
    """LLMJsonClient stub returning a fixed parsed JSON array."""

    def __init__(self, payload: Union[list, dict, str, None]) -> None:
        self.payload = payload
        self.calls = 0

    def complete_json(self, **kwargs) -> Optional[Union[dict, list]]:
        self.calls += 1
        return self.payload

    def complete_text(self, **kwargs) -> Optional[str]:  # pragma: no cover
        return None


class _RaisingClient:
    """LLMJsonClient stub whose complete_json always raises."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, **kwargs) -> Optional[Union[dict, list]]:
        self.calls += 1
        raise RuntimeError("boom")

    def complete_text(self, **kwargs) -> Optional[str]:  # pragma: no cover
        return None


# ---------------------------------------------------------------------------
# Blank input
# ---------------------------------------------------------------------------


def test_blank_input_returns_empty_list():
    assert decompose_query("") == []
    assert decompose_query("   ") == []
    assert decompose_query("\n\t ") == []


# ---------------------------------------------------------------------------
# Single clause -> [query]
# ---------------------------------------------------------------------------


def test_single_clause_returns_just_the_query():
    assert decompose_query("how do I run the seeder") == ["how do I run the seeder"]


def test_single_clause_is_stripped():
    assert decompose_query("  why does compile fail  ") == ["why does compile fail"]


# ---------------------------------------------------------------------------
# Multi-clause fallback: original first, then fragments, deduped + capped
# ---------------------------------------------------------------------------


def test_multi_clause_original_first_then_fragments():
    q = "how do I run the seeder and why does it fail on a fresh DB"
    result = decompose_query(q)
    assert result[0] == q  # original always leads
    assert "how do I run the seeder" in result
    assert "why does it fail on a fresh DB" in result
    # original + 2 fragments
    assert len(result) == 3


def test_multi_clause_respects_cap():
    q = "first thing, second thing, third thing, fourth thing, fifth thing"
    result = decompose_query(q, max_subqueries=3)
    assert len(result) == 3
    assert result[0] == q


def test_multi_clause_dedupes_repeated_fragments():
    q = "reset the cache, reset the cache, clear the index"
    result = decompose_query(q)
    # original + "reset the cache" (once) + "clear the index"
    lowered = [r.lower() for r in result]
    assert len(lowered) == len(set(lowered))
    assert "clear the index" in result


def test_split_on_question_marks_and_semicolons():
    q = "what broke? when did it break; who touched it"
    result = decompose_query(q)
    assert result[0] == q
    assert "what broke" in result
    assert "when did it break" in result
    assert "who touched it" in result


def test_trivial_fragments_are_dropped():
    # "a" is < 3 chars and must not appear as its own sub-query.
    q = "fix the bug, a, restart the worker"
    result = decompose_query(q)
    assert "a" not in result
    assert "fix the bug" in result
    assert "restart the worker" in result


# ---------------------------------------------------------------------------
# LLM stub returning a JSON array -> merged with original
# ---------------------------------------------------------------------------


def test_llm_array_merged_with_original():
    client = _StubArrayClient(["run the seeder", "fix the fresh-DB failure"])
    q = "how do I run the seeder and fix the fresh-DB failure"
    result = decompose_query(q, json_client=client)
    assert client.calls == 1
    assert result[0] == q  # original still leads
    assert "run the seeder" in result
    assert "fix the fresh-DB failure" in result


def test_llm_path_respects_cap():
    client = _StubArrayClient(["one", "two", "three", "four", "five", "six"])
    result = decompose_query("a big multi part question", json_client=client, max_subqueries=3)
    assert len(result) == 3
    assert result[0] == "a big multi part question"


def test_llm_dedupes_against_original():
    q = "run the seeder"
    # LLM echoes the original (case-variant) — must not duplicate.
    client = _StubArrayClient(["Run The Seeder", "check the DB"])
    result = decompose_query(q, json_client=client)
    lowered = [r.lower() for r in result]
    assert len(lowered) == len(set(lowered))
    assert result[0] == q


def test_llm_empty_array_falls_back_to_deterministic():
    client = _StubArrayClient([])
    q = "run the seeder and check the DB"
    result = decompose_query(q, json_client=client)
    # empty LLM output -> deterministic clause split (original + fragments)
    assert result[0] == q
    assert "run the seeder" in result
    assert "check the DB" in result


def test_llm_invalid_payload_falls_back():
    # Not a list and not a parseable JSON string -> fallback.
    client = _StubArrayClient({"not": "a list"})
    q = "run the seeder and check the DB"
    result = decompose_query(q, json_client=client)
    assert result[0] == q
    assert "check the DB" in result


# ---------------------------------------------------------------------------
# Raising client -> deterministic fallback, never raises
# ---------------------------------------------------------------------------


def test_raising_client_falls_back_never_raises():
    client = _RaisingClient()
    q = "how do I run the seeder and why does it fail"
    result = decompose_query(q, json_client=client)  # must not raise
    assert client.calls == 1
    assert result[0] == q
    assert "how do I run the seeder" in result
    assert "why does it fail" in result


def test_raising_client_single_clause_returns_query():
    client = _RaisingClient()
    result = decompose_query("just one thing here", json_client=client)
    assert result == ["just one thing here"]


# ---------------------------------------------------------------------------
# Determinism / purity
# ---------------------------------------------------------------------------


def test_deterministic_repeated_calls():
    q = "alpha and beta, gamma; delta? epsilon"
    first = decompose_query(q)
    second = decompose_query(q)
    assert first == second
    # And the LLM path is deterministic for a fixed stub too.
    client = _StubArrayClient(["alpha", "beta", "gamma"])
    a = decompose_query(q, json_client=client)
    b = decompose_query(q, json_client=_StubArrayClient(["alpha", "beta", "gamma"]))
    assert a == b

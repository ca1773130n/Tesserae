"""Tests for :func:`tesserae.retrieval.query_decompose.decompose_query`.

No network / no real LLM — the LLM path is exercised with hand-rolled stub
clients. Covers: blank input, single-clause passthrough, multi-clause split
with cap + dedupe, a JSON-array-returning stub merged with the original, and
a raising stub falling back deterministically (never raises).
"""

from __future__ import annotations

from typing import List, Optional, Union

from tesserae.retrieval.query_decompose import (
    decompose_query,
    discriminative_subquery,
)


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


# ---------------------------------------------------------------------------
# discriminative_subquery — the ubiquity filter
# ---------------------------------------------------------------------------


def test_discriminative_subquery_drops_corpus_ubiquitous_terms():
    """The corpus-DEPENDENT half of the filter.

    ``melanie`` is in 60% of the corpus and discriminates nothing; ``pottery``
    is in 2% and is the only term that names a session. Stripping the first is
    what lets the lanes rank on the second.
    """
    doc_freq = {"melanie": 60, "pottery": 2, "kids": 5}
    assert discriminative_subquery(
        "What types of pottery have Melanie and her kids made?",
        doc_freq=doc_freq, n_docs=100,
    ) == "types pottery kids made"


def test_discriminative_subquery_drops_stopwords_and_short_tokens():
    """The corpus-INDEPENDENT half, reusing grounding.STOPWORDS.

    Every question word here is absent from ``doc_freq`` entirely, so the DF
    rule alone would keep all of them — the stoplist is what removes them, and
    the two halves are measured to be worth 47.2% / 48.9% / 50.4% apart and
    together.
    """
    assert discriminative_subquery(
        "When did they go to the kiln?", doc_freq={"kiln": 1}, n_docs=100,
    ) == "kiln"


def test_discriminative_subquery_returns_empty_when_nothing_is_filtered():
    """``""`` is the caller's signal to run ONE pass, never the query itself."""
    # Nothing filtered: every token is a rare content word, so a second search
    # would repeat the first exactly.
    assert discriminative_subquery(
        "pottery kiln glaze", doc_freq={"pottery": 1, "kiln": 1, "glaze": 1},
        n_docs=100,
    ) == ""
    # Everything filtered: the sub-query would be empty and BM25 would score
    # the whole corpus at zero.
    assert discriminative_subquery(
        "what is it", doc_freq={}, n_docs=100,
    ) == ""
    assert discriminative_subquery(
        "melanie caroline", doc_freq={"melanie": 60, "caroline": 90}, n_docs=100,
    ) == ""


def test_discriminative_subquery_degenerate_inputs_never_raise():
    """Pure, total, and no clock: the conventions this module declares."""
    assert discriminative_subquery("", doc_freq={}, n_docs=10) == ""
    assert discriminative_subquery("   ", doc_freq={}, n_docs=10) == ""
    assert discriminative_subquery("pottery", doc_freq={}, n_docs=0) == ""
    assert discriminative_subquery("!!! ???", doc_freq={}, n_docs=10) == ""


def test_discriminative_subquery_keeps_repeated_content_tokens():
    """A repeated term is a repeated term to BM25, so the repeat stays."""
    assert discriminative_subquery(
        "pottery pottery kiln", doc_freq={"pottery": 1, "kiln": 1, "melanie": 90},
        n_docs=100,
    ) == ""
    assert discriminative_subquery(
        "melanie pottery pottery", doc_freq={"pottery": 1, "melanie": 90},
        n_docs=100,
    ) == "pottery pottery"


def test_discriminative_subquery_ratio_is_a_knob():
    """The 0.30 default is MEDIUM confidence off LoCoMo, so it is tunable."""
    doc_freq = {"melanie": 40, "pottery": 2}
    assert discriminative_subquery(
        "melanie pottery", doc_freq=doc_freq, n_docs=100,
    ) == "pottery"
    # Raise the ceiling above melanie's 0.40 and nothing is ubiquitous any
    # more, so the whole query survives and there is no second pass.
    assert discriminative_subquery(
        "melanie pottery", doc_freq=doc_freq, n_docs=100, ubiquity_df_ratio=0.5,
    ) == ""


def test_discriminative_subquery_is_deterministic():
    doc_freq = {"melanie": 60, "pottery": 2}
    q = "What did Melanie say about pottery?"
    assert (discriminative_subquery(q, doc_freq=doc_freq, n_docs=100)
            == discriminative_subquery(q, doc_freq=doc_freq, n_docs=100))

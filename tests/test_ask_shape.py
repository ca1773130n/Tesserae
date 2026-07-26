"""Question-SHAPE classifier: which ask path answers, planner or BM25."""

from __future__ import annotations

import pytest

from tesserae.ask_shape import SHAPE_GRAPH, SHAPE_LOOKUP, classify_ask_shape


def test_what_is_question_routes_to_lookup():
    # Contract: a single-entity definitional question is answerable from wiki
    # BM25 hits, so it must NOT pay for a planner roundtrip.
    assert classify_ask_shape("what is the hybrid retriever?").shape == SHAPE_LOOKUP
    assert classify_ask_shape("define entity resolution").shape == SHAPE_LOOKUP
    assert classify_ask_shape("who is the maintainer").shape == SHAPE_LOOKUP


def test_causal_and_decision_questions_route_to_graph():
    # The reasoning-bearing cue set: causality and decisions are exactly the
    # multi-hop shape where the graph beats vector RAG.
    assert classify_ask_shape("why did we choose SQLite?").shape == SHAPE_GRAPH
    assert classify_ask_shape("who decided to drop cognee?").shape == SHAPE_GRAPH
    assert classify_ask_shape("what happened recently").shape == SHAPE_GRAPH


def test_graph_cue_beats_lookup_prefix():
    # THE ordering pin. Both of these OPEN with a lookup prefix but carry a
    # graph cue; the cue must win. Fails the moment the prefix check runs first.
    assert classify_ask_shape("what is the current status of the compiler?").shape == SHAPE_GRAPH
    assert classify_ask_shape("what is the difference between the wiki and the graph?").shape == SHAPE_GRAPH


def test_unclassified_question_defaults_to_graph():
    # Safe-default direction: a mis-route to graph costs money and still
    # answers; a mis-route to lookup is silently shallower.
    shape = classify_ask_shape("tell me about the retrieval stack")
    assert shape.shape == SHAPE_GRAPH
    assert "default" in shape.reason


def test_classification_is_pure_and_stable():
    q = "why did the timeline change recently?"
    first, second = classify_ask_shape(q), classify_ask_shape(q)
    assert first == second
    # reason quotes the LEFTMOST cue ('why' precedes 'change'/'recently'), not a
    # joined set — pins determinism against a 'collect all cues' refactor.
    assert "why" in first.reason
    assert "recently" not in first.reason


def test_empty_question_does_not_crash():
    assert classify_ask_shape("").shape == SHAPE_GRAPH
    assert classify_ask_shape(None).shape == SHAPE_GRAPH  # type: ignore[arg-type]


# ---------------------------------------------- stemming holes (cost/silence)


@pytest.mark.parametrize(
    "left,right",
    [
        ("What is the most recent decision on retry handling?",
         "What is the latest decision on retry handling?"),
        ("What is the current owner of the compile lock?",
         "What is the currently assigned owner of the compile lock?"),
        ("What is the decision about semantic canonicalization?",
         "What was decided about semantic canonicalization?"),
        ("How does extraction handle stale sessions?",
         "How has extraction changed for stale sessions?"),
    ],
)
def test_paraphrases_do_not_split_across_backends(left, right):
    """`recent`/`recently`, `current`/`currently`, `decision`/`decided` were each
    one unstemmed-literal hole: two phrasings of ONE question got different
    backends and different depths of answer. Both sides must reach the graph."""
    assert classify_ask_shape(left).shape == SHAPE_GRAPH, left
    assert classify_ask_shape(right).shape == SHAPE_GRAPH, right


@pytest.mark.parametrize(
    "question",
    [
        "What is blocking the v0.26 release?",
        "Who is responsible for the canonicalization module?",
        "What is the current state of the extract pipeline?",
        "What are the open questions about the graph engine?",
        "Which module owns contradicts_claim?",
        "What is the newest approach to entity resolution?",
        "How does this interact with the determinism regressions we hit?",
        "Who is working on the verify tool?",
    ],
)
def test_multi_hop_and_temporal_questions_reach_the_graph(question):
    """Silent under-answering is the failure mode that matters: these are the
    multi-hop / temporal categories where the graph beats BM25 by the widest
    margin, and every one of them used to fall into the cheap band."""
    assert classify_ask_shape(question).shape == SHAPE_GRAPH


@pytest.mark.parametrize(
    "question",
    [
        "What is contradicts_claim?",
        "Define ResearchGraph",
        "Where is the compile entrypoint?",
        "Which file defines TemporalFact?",
        "meaning of byte-idempotence",
        "who is the maintainer",
    ],
)
def test_the_cheap_band_is_narrowed_not_deleted(question):
    """Narrowing must not turn `lookup` into dead code — a single wiki page
    genuinely IS the answer for these, and paying the planner is pure waste."""
    assert classify_ask_shape(question).shape == SHAPE_LOOKUP

"""Question-SHAPE classifier: which ask path answers, planner or BM25."""

from __future__ import annotations

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

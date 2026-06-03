"""Unit tests for the in-process Pipeline runner.

Covers: step sequencing, data normalization, fail-fast abort, exception
capture, no-reraise, and the empty-pipeline no-op. Synthetic steps only --
no fixtures, no real ingest/compile/publish work.
"""

import pytest

from tesserae.engine.pipeline import Pipeline, StepResult


def test_pipeline_runs_steps_in_order():
    order = []
    results = Pipeline(
        [("a", lambda: order.append("a")), ("b", lambda: order.append("b"))]
    ).run()
    assert order == ["a", "b"]
    assert all(r.ok for r in results)
    assert [r.name for r in results] == ["a", "b"]


def test_pipeline_returns_step_data():
    results = Pipeline([("a", lambda: {"x": 1}), ("b", lambda: None)]).run()
    assert results[0].data == {"x": 1}
    assert results[1].data == {}


def test_pipeline_stops_on_failure():
    calls = []

    def boom():
        raise RuntimeError("boom")

    results = Pipeline(
        [
            ("a", lambda: calls.append("a")),
            ("b", boom),
            ("c", lambda: calls.append("c")),
        ]
    ).run()
    assert "c" not in calls
    assert len(results) == 2
    assert results[1].ok is False
    assert isinstance(results[1].error, RuntimeError)


def test_pipeline_does_not_reraise():
    def boom():
        raise RuntimeError("boom")

    # No try/except: run() must return normally rather than propagate.
    results = Pipeline([("a", lambda: None), ("b", boom)]).run()
    assert len(results) == 2
    assert results[0].ok is True
    assert results[1].ok is False


def test_pipeline_empty_is_noop():
    assert Pipeline([]).run() == []

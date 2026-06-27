"""Regression guard on the federation semantic-link defaults.

Runs the eval harness with the REAL embedding backend and asserts the shipped
defaults (min_cosine, edge weight) remain the data-backed choice — so a code or
embedding-model change that degrades linking precision or lets cross-project
bridges swamp within-project structure fails CI. Skipped without tesserae[semantic].
"""

from __future__ import annotations

import pytest

pytest.importorskip("model2vec")  # the eval needs real embeddings

from evals.federation.run_eval import (  # noqa: E402
    DEFAULT_EDGE_WEIGHT,
    DEFAULT_MIN_COSINE,
    compute_threshold_rows,
    compute_weight_rows,
)


def test_threshold_default_is_near_optimal_and_high_precision():
    rows = compute_threshold_rows()
    best_f1 = max(r["f1"] for r in rows)
    assert best_f1 >= 0.75, "eval not discriminating — fixture/model broken"
    default = next(r for r in rows if abs(r["threshold"] - DEFAULT_MIN_COSINE) < 1e-9)
    # Adding edges makes false links costly, so the default must stay near the
    # F1 frontier AND high-precision. Locks the data-backed 0.55 + catches drift.
    assert default["f1"] >= best_f1 - 0.06
    assert default["precision"] >= 0.85


def test_weight_default_surfaces_bridge_without_swamping():
    rows, meta = compute_weight_rows()
    assert meta["bridge_linked"], "the semantic bridge a::rw <-> b::ppr did not form"
    by_weight = {r["weight"]: r for r in rows}

    # No bridge (weight 0) -> the cross-project B node is unreachable.
    assert by_weight[0.0]["B_bridged"] is None

    # Default weight surfaces B AND keeps A's OWN content ranked above it.
    default = by_weight[DEFAULT_EDGE_WEIGHT]
    assert default["B_bridged"] is not None
    assert default["A_neighbour"] < default["B_bridged"], "default weight swamps A"

    # A heavy weight DOES swamp (B overtakes A) — confirms the nudge matters.
    assert by_weight[2.0]["B_bridged"] < by_weight[2.0]["A_neighbour"]

    # Unrelated B noise never surfaces at any weight.
    assert all(r["B_unrelated"] is None for r in rows)

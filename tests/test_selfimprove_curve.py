"""The self-improvement curve's correctness hinges on one thing: seeing the
overlay. These tests guard that, and the honesty of the report around it."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.selfimprove.curve import Point, load_graph_with_overlay, render
from tesserae.memory.associate import persist_links
from tesserae.research_graph import (
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


def _graph() -> ResearchGraph:
    return ResearchGraph(
        nodes=[
            ResearchNode(id=f"Concept:c{i}", name=f"c{i}", type=ResearchNodeType.CONCEPT)
            for i in range(3)
        ],
        edges=[],
    )


def _write(work: Path, graph: ResearchGraph) -> None:
    (work / ".tesserae").mkdir(parents=True, exist_ok=True)
    (work / ".tesserae" / "graph.json").write_text(graph.to_json(), encoding="utf-8")


def test_the_loader_sees_overlay_edges_graph_json_does_not_have(tmp_path: Path) -> None:
    """The trap this whole harness exists to avoid.

    `associate` writes discovered edges to a sidecar overlay, never into
    graph.json. A loader that reads graph.json directly — which is what
    `evals/growth/run.py` correctly does for ITS question — would score every
    cycle identically here and manufacture a null result: "consolidation does
    nothing", produced by the loader rather than observed in the system.
    """
    graph = _graph()
    _write(tmp_path, graph)

    before = load_graph_with_overlay(tmp_path)
    n_before = len(before.edges)

    persist_links(tmp_path, [("Concept:c0", "Concept:c1", 0.91)])

    after = load_graph_with_overlay(tmp_path)
    assert len(after.edges) > n_before, (
        "the overlay is invisible to the loader — every cycle would score the "
        "same and the experiment would report a false null"
    )


def test_graph_json_itself_is_never_mutated_by_the_overlay(tmp_path: Path) -> None:
    """The merge is in-memory only; byte-determinism of graph.json is a
    project-wide invariant and an experiment must not be what breaks it."""
    _write(tmp_path, _graph())
    raw = (tmp_path / ".tesserae" / "graph.json").read_bytes()

    persist_links(tmp_path, [("Concept:c0", "Concept:c2", 0.88)])
    load_graph_with_overlay(tmp_path)

    assert (tmp_path / ".tesserae" / "graph.json").read_bytes() == raw


def test_the_report_names_a_flat_arm_as_flat_rather_than_omitting_it() -> None:
    """A baseline that cannot move is the comparison, so it has to appear."""
    points = [
        Point(0, "Tesserae", 5, 5, 0, 15, 100, 200),
        Point(0, "BM25", 3, 3, 0, 15, 40, 0),
        Point(1, "Tesserae", 9, 9, 0, 15, 100, 240),
        Point(1, "BM25", 3, 3, 0, 15, 40, 0),
    ]
    out = render(points)
    assert "BM25" in out
    assert "Δ **+4**" in out, "the graph arm's lift must be stated"
    assert "Δ **+0**" in out, "the baseline's flat line must be stated, not omitted"


def test_a_fired_control_is_shouted_about() -> None:
    """Controls ask what the corpus cannot answer. A path between their anchors
    means the checker is finding spurious connections and every number is
    suspect — that cannot be a quiet column."""
    out = render([Point(0, "Tesserae", 5, 5, 2, 15, 100, 200)])
    assert "Controls fired: 2" in out
    assert "suspect" in out

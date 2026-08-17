"""The self-improvement curve's correctness hinges on one thing: seeing the
overlay. These tests guard that, and the honesty of the report around it."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.growth.run import adjacency, grounded_sources, load_questions
from evals.selfimprove.curve import (
    Point,
    _readable_docs,
    as_dict,
    load_graph_with_overlay,
    measure_baseline,
    render,
    staged_corpus,
)
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


def _point(arm: str, cycle: int, mrr: float, r5: float, **kw) -> Point:
    """Keyword-only, because the positional form broke the moment `Point` grew
    a field — which is the same hazard the distill sweep hit with DistillResult."""
    return Point(
        cycle=cycle, arm=arm, mrr=mrr,
        recall_at={1: r5 / 2, 3: r5, 5: r5, 10: r5},
        mrr_null=kw.get("mrr_null", 0.05),
        mrr_edge_blind=kw.get("mrr_edge_blind", mrr - 0.1),
        answerable=kw.get("answerable", 5), connected=kw.get("connected", 5),
        controls_fired=kw.get("controls_fired", 0), n_questions=kw.get("n_questions", 59),
        nodes=kw.get("nodes", 100), edges=kw.get("edges", 200),
    )


def test_the_report_names_a_flat_arm_as_flat_rather_than_omitting_it() -> None:
    """A baseline that cannot move is the comparison, so it has to appear."""
    points = [
        _point("Tesserae", 0, 0.500, 0.60),
        _point("BM25", 0, 0.300, 0.40),
        _point("Tesserae", 1, 0.700, 0.80),
        _point("BM25", 1, 0.300, 0.40),
    ]
    out = render(points)
    assert "BM25" in out
    assert "Δ **+0.200**" in out, "the graph arm's lift must be stated"
    assert "Δ **+0.000**" in out, "the baseline's flat line must be stated, not omitted"


def test_the_report_states_a_LOSS_with_its_sign(subtests=None) -> None:
    """The point of the whole metric change: consolidation must be able to hurt,
    and when it does the report has to say so rather than rendering a bare
    magnitude. `answerable` could never produce this row at all."""
    out = render([
        _point("Tesserae", 0, 0.747, 0.833),
        _point("Tesserae", 1, 0.719, 0.600),
    ])
    assert "Δ **-0.028**" in out, "an MRR regression must be signed, not absolute"
    assert "Δ **-0.233**" in out, "a recall regression must be signed too"


def test_a_fired_control_is_reported(the_headline_no_longer_depends_on_it=None) -> None:
    """Controls remain the cheapest signal that spurious links exist, but they no
    longer invalidate the table — the ranked score is what prices the harm."""
    out = render([_point("Tesserae", 0, 0.5, 0.6, controls_fired=2)])
    assert "Controls fired: 2" in out


def test_both_nulls_appear_in_every_report() -> None:
    """A shuffled-gold score near the real one means the ranking has no
    question-specific signal; an edge-blind score equal to the real one means
    edges contributed nothing. Either silently invalidates the run, so neither
    may be an optional column."""
    out = render([
        _point("Tesserae", 0, 0.747, 0.833, mrr_null=0.054, mrr_edge_blind=0.691),
        _point("Tesserae", 1, 0.719, 0.600, mrr_null=0.046, mrr_edge_blind=0.691),
    ])
    assert "Shuffled gold" in out and "0.054" in out
    assert "Edge-blind" in out


def test_growth_can_index_into_what_as_dict_hands_it(tmp_path: Path) -> None:
    """`as_dict` feeds `evals/growth`, which does `node["source_path"]`.

    So its output has to be mappings, not the typed objects the graph holds.
    The first version guessed the serialiser's name, missed, and passed the
    objects through — every existing test here still passed, because they all
    stop at `load_graph_with_overlay` and none of them hands the result to the
    module that consumes it. Asserting on the conversion in isolation would
    repeat that mistake, so this calls growth's own functions.
    """
    graph = _graph()
    _write(tmp_path, graph)
    persist_links(tmp_path, [("Concept:c0", "Concept:c1", 0.91)])

    raw = as_dict(load_graph_with_overlay(tmp_path))

    # Nodes: grounded_sources reads .get("source_path") off every one.
    assert grounded_sources(raw, {tmp_path / "absent.md"}) == set()
    # Edges: adjacency reads .get("source")/.get("target"), and the overlay
    # edge has to arrive as a mapping too.
    adj = adjacency(raw)
    assert adj.get("Concept:c0") == {"Concept:c1"}


def test_an_unserialisable_member_is_refused_rather_than_passed_through(
    tmp_path: Path,
) -> None:
    """The failure mode that made the last one hard to read.

    Passing an unknown object through produced an AttributeError three frames
    away, inside growth, naming a type growth has never heard of. Raising here
    names the type and the reason.
    """
    class Opaque:
        pass

    class FakeGraph:
        nodes = [Opaque()]
        edges: list = []

    with pytest.raises(TypeError, match="Opaque"):
        as_dict(FakeGraph())


def test_a_baseline_can_read_a_corpus_of_directories(tmp_path: Path) -> None:
    """The shape `corpus_docs()` actually returns.

    A paper is a directory of markdown, not a file, so the first run of this
    harness reached the baselines and died on IsADirectoryError. The graph arm
    had already been measured by then — which is the trap: an exception here
    looks like "the baselines are broken" when the corpus shape was never the
    file list this code assumed.
    """
    paper = tmp_path / "arxiv-0001"
    paper.mkdir()
    (paper / "abstract.md").write_text("alpha appears here", encoding="utf-8")
    (paper / "paper.md").write_text("beta appears here", encoding="utf-8")
    loose = tmp_path / "note.md"
    loose.write_text("alpha and beta together", encoding="utf-8")

    assert _readable_docs([paper, loose]) == [
        paper / "abstract.md", paper / "paper.md", loose,
    ]

    point = measure_baseline(
        "BM25",
        [paper, loose],
        [{"id": "q1", "text": "where do alpha and beta meet?",
          "anchors": ["alpha", "beta"], "requires": []}],
        cycle=0,
    )
    # Three files, not two directory entries: the baselines index what
    # Tesserae compiled, so neither arm gets a different corpus.
    assert point.nodes == 3


def test_every_question_has_its_sources_on_the_frozen_corpus() -> None:
    """The whole corpus is staged, so `have_sources` must hold for every question.

    `answerable` is `connected AND have_sources`, and `have_sources` compares
    `requires` against arxiv ids. The first run built that set out of the wrong
    tuple field and filled it with dates, so nothing matched and Tesserae
    scored 0/15 with 12 questions connected — a null that looks like a verdict
    on the architecture and is a verdict on an unpacking.
    """
    _, _, staged_arxiv = staged_corpus()
    required = {r for q in load_questions() for r in (q.get("requires") or [])}

    assert required, "the question set declares no sources; this guards nothing"
    assert required <= staged_arxiv, (
        "questions require papers the full corpus does not contain: "
        f"{sorted(required - staged_arxiv)[:5]}"
    )

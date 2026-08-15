"""Guard the synthetic generator's fidelity to the shape it claims to replay.

The scale measurement in ``docs/superpowers/specs/2026-08-14-scale-measurement.md``
is only worth the bytes it is written in if the graphs it measured actually
looked like a Tesserae graph. These tests pin the properties the measurement
depends on, at a size that costs milliseconds — no LLM, no compile, no disk.

The tolerances are wide on purpose. This is a ratchet against the generator
silently drifting into producing a uniform random graph (which would make every
number in the report optimistic), not a bit-exactness test on a sampler.
"""

from __future__ import annotations

import collections

import pytest

from tesserae.research_graph import ALLOWED_EDGE_TYPES, ResearchNodeType
from tests.scale_graph import _load_profile, generate_graph

SIZE = 4000


@pytest.fixture(scope="module")
def graph():
    return generate_graph(SIZE, seed=11)


def test_node_and_edge_counts_follow_the_profile_ratio(graph) -> None:
    profile = _load_profile()
    assert len(graph.nodes) == SIZE
    expected = SIZE * float(profile["edges_per_node"])
    # Hub allocation rounds per hub, so the count lands within a few edges.
    assert abs(len(graph.edges) - expected) < 10


def test_every_generated_type_is_one_the_schema_accepts(graph) -> None:
    # The generator replays type names from a profile derived off a live graph.
    # If the schema ever drops one, the measurement would be sampling a type
    # the engine can no longer produce, so fail here rather than in a sweep.
    known_nodes = {member.value for member in ResearchNodeType}
    assert {node.type.value for node in graph.nodes} <= known_nodes
    assert {edge.type for edge in graph.edges} <= set(ALLOWED_EDGE_TYPES)


def test_degree_distribution_keeps_the_live_graph_s_heavy_tail(graph) -> None:
    """The tail is the whole point: PPR and the depth-2 walk are priced by it.

    A uniform random graph of the same node and edge count would have a maximum
    degree in the low tens. The live graph's busiest node holds 29% of all edge
    endpoints, and a generator that lost that would report a PPR cost that no
    real deployment would ever see.
    """
    degree: collections.Counter = collections.Counter()
    for edge in graph.edges:
        degree[edge.source] += 1
        degree[edge.target] += 1
    values = sorted(degree.get(node.id, 0) for node in graph.nodes)

    mean = sum(values) / len(values)
    assert 4.0 < mean < 4.9, f"live graph mean degree is 4.44, got {mean}"
    assert values[len(values) // 2] <= 4, "median degree should stay low"
    assert values[int(len(values) * 0.9)] <= 10, "p90 degree should stay low"
    # The dominant hub carries ~29% of endpoints in the live graph.
    assert values[-1] > 0.2 * len(graph.edges), "the dominant hub vanished"


def test_isolated_node_share_tracks_the_live_graph(graph) -> None:
    # 3.9% live. Isolated nodes matter because they are the cheap case: a
    # generator that produced far too many would understate traversal cost.
    degree: collections.Counter = collections.Counter()
    for edge in graph.edges:
        degree[edge.source] += 1
        degree[edge.target] += 1
    isolated = sum(1 for node in graph.nodes if degree.get(node.id, 0) == 0)
    assert 0.01 < isolated / len(graph.nodes) < 0.09


def test_corpus_vocabulary_repeats_so_bm25_has_real_posting_lists(graph) -> None:
    """Terms must recur across documents, or the BM25 lane measures nothing.

    Filling nodes with unique random strings gives every term a posting list of
    length one, which makes the lane look almost free. The sweep hit exactly
    this failure in a different form — a query sharing no vocabulary with the
    corpus scored 0 documents — so the property is pinned here.
    """
    counts: collections.Counter = collections.Counter()
    for node in graph.nodes:
        counts.update(set(node.name.split()))
    assert counts, "nodes have no name text at all"
    top_term, top_count = counts.most_common(1)[0]
    assert top_count > len(graph.nodes) * 0.02, (
        f"most common term {top_term!r} appears in only {top_count} of "
        f"{len(graph.nodes)} nodes — posting lists are degenerate"
    )


def test_generation_is_deterministic_for_a_seed() -> None:
    # The sweep compares sizes against each other; a generator that drifted
    # between runs would put noise into every comparison.
    left = generate_graph(500, seed=3)
    right = generate_graph(500, seed=3)
    assert [n.id for n in left.nodes] == [n.id for n in right.nodes]
    assert [(e.source, e.type, e.target) for e in left.edges] == [
        (e.source, e.type, e.target) for e in right.edges
    ]
    assert generate_graph(500, seed=4).nodes[0].id != left.nodes[0].id


def test_serialized_size_per_node_matches_the_live_graph(graph) -> None:
    # The live graph is 55.1 MB of indented JSON over 47,132 nodes: 1,168 bytes
    # per node. Serialization and parse cost are close to linear in bytes, so a
    # generator that got this wrong would scale the wrong quantity.
    per_node = len(graph.to_json(indent=1)) / len(graph.nodes)
    assert 850 < per_node < 1500, f"{per_node:.0f} bytes/node vs 1168 live"


def test_rejects_a_nonsensical_size() -> None:
    with pytest.raises(ValueError):
        generate_graph(0)


# --------------------------------------------------------------------------- #
# the sweep runner's repeat aggregation
# --------------------------------------------------------------------------- #


def _summarize():
    # ``scripts/`` is not a package, and the runner imports psutil and the whole
    # retrieval stack only inside its child path, so loading the module by path
    # keeps this test to the pure aggregation function it is about.
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "scale_measure.py"
    spec = importlib.util.spec_from_file_location("_scale_measure_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.summarize


def _run(n_nodes, seconds, *, error=""):
    return {
        "n_nodes": n_nodes,
        "phases": [
            {"phase": "hybrid_search_warm", "seconds": seconds, "peak_rss_mb": 100.0, "error": error}
        ],
    }


def test_repeated_runs_report_the_median_not_the_mean() -> None:
    # The distribution being smoothed is one-sided: a run whose vector sidecar
    # was evicted from the OS page cache pays a cold read, and nothing makes a
    # run faster than its warm case. This exact shape was measured on
    # 2026-08-15 — 0.99 / 1.00 / 3.31 s at 100,000 nodes — and a mean would have
    # reported 1.77 s, turning a 4x speedup into a regression.
    summary = _summarize()([_run(100_000, 0.99), _run(100_000, 1.00), _run(100_000, 3.31)])
    cell = summary[("hybrid_search_warm", 100_000)]
    assert cell["seconds"] == pytest.approx(1.00)
    assert cell["runs"] == 3
    assert cell["seconds_min"] == pytest.approx(0.99)
    assert cell["seconds_max"] == pytest.approx(3.31)


def test_a_phase_that_raised_is_excluded_from_its_median() -> None:
    # A phase that failed recorded whatever ``time.perf_counter`` measured up to
    # the exception, which is a duration for an abandoned attempt, not for the
    # work. Averaging it in would quietly make a broken size look fast.
    summary = _summarize()(
        [_run(1000, 2.0), _run(1000, 0.01, error="MemoryError: "), _run(1000, 4.0)]
    )
    cell = summary[("hybrid_search_warm", 1000)]
    assert cell["runs"] == 2
    assert cell["seconds"] == pytest.approx(3.0)


def test_sizes_are_summarized_independently() -> None:
    summary = _summarize()([_run(1000, 1.0), _run(2000, 9.0)])
    assert summary[("hybrid_search_warm", 1000)]["seconds"] == pytest.approx(1.0)
    assert summary[("hybrid_search_warm", 2000)]["seconds"] == pytest.approx(9.0)


def _module():
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "scale_measure.py"
    spec = importlib.util.spec_from_file_location("_scale_measure_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_a_phase_that_ran_with_no_free_memory_is_flagged() -> None:
    # The failure this catches is subtle enough to have cost most of 2026-08-15:
    # a starved phase reports MORE seconds at LESS peak RSS, which reads as a
    # performance regression rather than as a machine that refused the process
    # its pages. Without this flag a single run cannot be told apart from a good
    # one without a prior sweep to compare against.
    mod = _module()
    results = [
        {"n_nodes": 250_000, "phases": [
            {"phase": "hybrid_search_warm", "seconds": 11.9, "peak_rss_mb": 2189.0,
             "avail_min_mb": 900.0, "error": ""},
            {"phase": "json_dumps", "seconds": 2.8, "peak_rss_mb": 2358.0,
             "avail_min_mb": 6200.0, "error": ""},
        ]}
    ]
    flagged = mod.starved(results)
    assert flagged == [("hybrid_search_warm", 250_000, 900.0)]


def test_results_predating_the_field_are_not_reported_as_starved() -> None:
    # An older result file carries no availability sample. Treating a missing
    # reading as zero free memory would flag every historical row and make the
    # warning worthless the first time anyone re-ran an old sweep.
    mod = _module()
    results = [{"n_nodes": 47_132, "phases": [
        {"phase": "hybrid_search_warm", "seconds": 0.42, "peak_rss_mb": 817.0, "error": ""}
    ]}]
    assert mod.starved(results) == []

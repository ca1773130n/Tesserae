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

"""The merge ledger: collection, persistence, chain resolution, and the read
surface that consumes it.

The load-bearing test in here is
``test_node_context_resolves_a_stale_id_to_its_survivor`` — a ledger nothing
reads back is the ``:ConsolidationRun`` failure mode (written on every run,
consumed by no code path), so "a read surface answers with it" is the
acceptance criterion for this feature, not "the file exists".

Nothing here compiles a graph or touches an LLM: the merge passes are pure
functions over in-memory nodes, and the one test that covers the compile-time
wiring stubs ``_ingest`` rather than extracting a corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

from tesserae.canonicalization import GraphCanonicalizer
from tesserae.merge_ledger import (
    BASIS_AGGRESSIVE_KEY,
    BASIS_CROSS_TYPE,
    BASIS_EXACT_KEY,
    MERGE_LEDGER_SCHEMA_VERSION,
    MergeLedger,
    MergeRecord,
    collect_merges,
    load_merge_ledger,
    merge_ledger_path,
    publish_merge_ledger,
)
from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
    merge_cross_type_duplicates,
    merge_same_type_aliased_duplicates,
)


def _concept(node_id: str, name: str) -> ResearchNode:
    return ResearchNode(
        id=node_id, name=name, type=ResearchNodeType.METHODOLOGICAL_CONCEPT
    )


def _write_raw_ledger(project_root: Path, rows: list) -> Path:
    """Plant ledger bytes the publisher would never produce.

    Used where the point is that a READ path holds up against a ledger it did
    not write — a hand-edited one, or one left by an older schema.
    """
    path = merge_ledger_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": MERGE_LEDGER_SCHEMA_VERSION, "records": rows}),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# Collection — the three passes report the map they used to discard           #
# --------------------------------------------------------------------------- #


def test_aggressive_key_merge_is_recorded_with_the_loser_name_and_type():
    # "pre-training" and "pretraining" collide under _aggressive_dedup_key; the
    # longer display name wins, so the short one is the loser.
    winner = _concept("MethodologicalConcept:pre-training", "Pre-Training")
    loser = _concept("MethodologicalConcept:pretraining", "pretraining")

    with collect_merges() as records:
        nodes, _edges = merge_same_type_aliased_duplicates([winner, loser], [])

    assert [n.id for n in nodes] == [winner.id]
    assert records == [
        MergeRecord(
            loser_id=loser.id,
            survivor_id=winner.id,
            basis=BASIS_AGGRESSIVE_KEY,
            loser_name="pretraining",
            loser_type="MethodologicalConcept",
        )
    ]


def test_cross_type_merge_is_recorded():
    paper = ResearchNode(
        id="Paper:gs", name="Gaussian Splatting", type=ResearchNodeType.PAPER
    )
    family = ResearchNode(
        id="ApproachFamily:gs",
        name="Gaussian Splatting",
        type=ResearchNodeType.APPROACH_FAMILY,
    )

    with collect_merges() as records:
        nodes, _edges = merge_cross_type_duplicates([paper, family], [])

    assert [n.id for n in nodes] == [paper.id]
    assert [(r.loser_id, r.survivor_id, r.basis) for r in records] == [
        (family.id, paper.id, BASIS_CROSS_TYPE)
    ]
    assert records[0].loser_type == "ApproachFamily"


def test_canonicalizer_exact_key_map_is_recorded_rather_than_discarded():
    canonical = _concept("MethodologicalConcept:gaussian-splatting", "Gaussian Splatting")
    canonical = ResearchNode(
        id=canonical.id,
        name=canonical.name,
        type=canonical.type,
        aliases=["3DGS"],
    )
    alias = _concept("MethodologicalConcept:3dgs", "3DGS")
    graph = ResearchGraph(nodes=[canonical, alias], edges=[])

    with collect_merges() as records:
        result = GraphCanonicalizer().canonicalize(graph)

    assert result.merged_nodes == {alias.id: canonical.id}
    assert [(r.loser_id, r.survivor_id, r.basis) for r in records] == [
        (alias.id, canonical.id, BASIS_EXACT_KEY)
    ]


def test_merges_outside_a_collector_are_a_no_op_and_change_nothing():
    """The sink is write-only and optional: with no collector open the passes
    must return exactly what they returned before the ledger existed."""
    winner = _concept("MethodologicalConcept:multi-head-attention", "Multi-Head Attention")
    loser = _concept("MethodologicalConcept:multihead-attention", "MultiHead Attention")
    edges = [ResearchEdge(source="Paper:p", target=loser.id, type="uses")]

    uncollected_nodes, uncollected_edges = merge_same_type_aliased_duplicates(
        [winner, loser], list(edges)
    )
    with collect_merges() as records:
        collected_nodes, collected_edges = merge_same_type_aliased_duplicates(
            [winner, loser], list(edges)
        )

    assert records  # the collected run really did merge something
    assert [n.id for n in uncollected_nodes] == [n.id for n in collected_nodes]
    assert [(e.source, e.type, e.target) for e in uncollected_edges] == [
        (e.source, e.type, e.target) for e in collected_edges
    ]


def test_nested_collectors_do_not_leak_into_each_other():
    # Same _aggressive_dedup_key within each pair ("aa" / "bb"); the longer
    # display name wins, so the unpunctuated spelling is the loser.
    outer_loser = _concept("MethodologicalConcept:aa", "AA")
    outer_winner = _concept("MethodologicalConcept:a-a", "A-A")
    inner_loser = _concept("MethodologicalConcept:bb", "BB")
    inner_winner = _concept("MethodologicalConcept:b-b", "B-B")

    with collect_merges() as outer:
        merge_same_type_aliased_duplicates([outer_winner, outer_loser], [])
        with collect_merges() as inner:
            merge_same_type_aliased_duplicates([inner_winner, inner_loser], [])
        merge_same_type_aliased_duplicates([outer_winner, outer_loser], [])

    assert [r.loser_id for r in inner] == [inner_loser.id]
    assert [r.loser_id for r in outer] == [outer_loser.id, outer_loser.id]


# --------------------------------------------------------------------------- #
# Resolution                                                                   #
# --------------------------------------------------------------------------- #


def test_resolve_walks_a_chain_to_the_last_survivor():
    # The ordinary shape: an aggressive-key collapse feeding a cross-type one.
    ledger = MergeLedger(
        [
            MergeRecord("a", "b", BASIS_AGGRESSIVE_KEY),
            MergeRecord("b", "c", BASIS_CROSS_TYPE),
        ]
    )
    assert ledger.resolve("a") == "c"
    assert ledger.resolve("b") == "c"
    assert ledger.resolve("c") is None  # a live id is not a loser
    assert ledger.resolve("never-seen") is None


def test_resolve_terminates_on_a_cycle():
    # The passes cannot mint a cycle; a hand-edited or half-written ledger can,
    # and an infinite loop on a read path is worse than an imperfect answer.
    ledger = MergeLedger(
        [MergeRecord("a", "b", BASIS_CROSS_TYPE), MergeRecord("b", "a", BASIS_CROSS_TYPE)]
    )
    assert ledger.resolve("a") in {"a", "b"}


# --------------------------------------------------------------------------- #
# Persistence                                                                  #
# --------------------------------------------------------------------------- #


def test_ledger_round_trips_and_is_byte_stable_for_the_same_record_set(tmp_path: Path):
    records = [
        MergeRecord("z-loser", "survivor", BASIS_CROSS_TYPE, "Z", "Paper"),
        MergeRecord("a-loser", "survivor", BASIS_AGGRESSIVE_KEY, "A", "Paper"),
    ]
    path = merge_ledger_path(tmp_path)

    assert publish_merge_ledger(path, records, ["survivor"]) == 2
    first = path.read_bytes()
    assert publish_merge_ledger(path, list(reversed(records)), ["survivor"]) == 2
    assert path.read_bytes() == first, "ledger bytes depend on record ORDER"

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == MERGE_LEDGER_SCHEMA_VERSION
    assert [row["loser_id"] for row in payload["records"]] == ["a-loser", "z-loser"]

    ledger = load_merge_ledger(tmp_path)
    assert ledger.resolve("z-loser") == "survivor"
    assert ledger.record_for("a-loser").loser_name == "A"


def test_an_incremental_compile_that_observes_nothing_keeps_the_redirects(tmp_path: Path):
    """The incremental arm feeds on the previous graph.json — survivors only —
    so it never performs the merges that produced them and observes nothing.
    Overwriting from observations alone would wipe every redirect here, which
    ``tests/test_incremental_parity.py`` sees as an incremental arm that is not
    byte-identical to a full one."""
    path = merge_ledger_path(tmp_path)
    publish_merge_ledger(path, [MergeRecord("dead", "alive", BASIS_CROSS_TYPE)], ["alive"])
    full_bytes = path.read_bytes()

    assert publish_merge_ledger(path, [], ["alive"]) == 1
    assert path.read_bytes() == full_bytes
    assert load_merge_ledger(tmp_path).resolve("dead") == "alive"


def test_a_record_is_pruned_once_its_chain_stops_landing_on_a_live_node(tmp_path: Path):
    """The ledger is pruned to the published graph rather than appended to, so
    it cannot become an append-only history of everything that ever merged."""
    path = merge_ledger_path(tmp_path)
    publish_merge_ledger(
        path,
        [
            MergeRecord("dead", "middle", BASIS_AGGRESSIVE_KEY),
            MergeRecord("middle", "alive", BASIS_CROSS_TYPE),
            MergeRecord("orphan", "dropped-from-corpus", BASIS_CROSS_TYPE),
        ],
        ["alive"],
    )

    ledger = load_merge_ledger(tmp_path)
    assert ledger.resolve("dead") == "alive"  # the whole chain is kept
    assert ledger.resolve("orphan") is None  # its survivor left the graph

    # "dead" comes back to life in a later compile: it resolves to itself now,
    # so the redirect must go rather than send a read surface on a detour.
    publish_merge_ledger(path, [], ["alive", "middle", "dead"])
    assert load_merge_ledger(tmp_path).resolve("dead") is None


def test_a_chain_is_truncated_at_a_node_that_came_back_to_life(tmp_path: Path):
    """``dead -> middle -> alive`` with ``middle`` live again must redirect to
    ``middle``, not sail past it to ``alive``."""
    path = merge_ledger_path(tmp_path)
    publish_merge_ledger(
        path,
        [
            MergeRecord("dead", "middle", BASIS_AGGRESSIVE_KEY),
            MergeRecord("middle", "alive", BASIS_CROSS_TYPE),
        ],
        ["alive"],
    )
    assert load_merge_ledger(tmp_path).resolve("dead") == "alive"

    publish_merge_ledger(path, [], ["alive", "middle"])
    assert load_merge_ledger(tmp_path).resolve("dead") == "middle"


def test_this_compiles_observation_beats_a_stored_one_for_the_same_loser(tmp_path: Path):
    path = merge_ledger_path(tmp_path)
    publish_merge_ledger(path, [MergeRecord("x", "old", BASIS_CROSS_TYPE)], ["old", "new"])
    publish_merge_ledger(path, [MergeRecord("x", "new", BASIS_CROSS_TYPE)], ["old", "new"])

    assert load_merge_ledger(tmp_path).resolve("x") == "new"


def test_a_missing_or_corrupt_ledger_reads_as_empty_and_never_raises(tmp_path: Path):
    assert not load_merge_ledger(tmp_path)  # absent

    path = merge_ledger_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert not load_merge_ledger(tmp_path)

    path.write_text(json.dumps({"schema_version": 99, "records": []}), encoding="utf-8")
    assert not load_merge_ledger(tmp_path)

    # Rows missing the two mandatory ids are dropped, the sane row survives.
    path.write_text(
        json.dumps(
            {
                "schema_version": MERGE_LEDGER_SCHEMA_VERSION,
                "records": [{"loser_id": "x"}, {"loser_id": "y", "survivor_id": "z"}],
            }
        ),
        encoding="utf-8",
    )
    assert load_merge_ledger(tmp_path).resolve("y") == "z"


# --------------------------------------------------------------------------- #
# The read surface — the acceptance criterion                                  #
# --------------------------------------------------------------------------- #


def _survivor_graph() -> ResearchGraph:
    survivor = _concept("MethodologicalConcept:pre-training", "Pre-Training")
    paper = ResearchNode(id="Paper:p", name="Paper A", type=ResearchNodeType.PAPER)
    return ResearchGraph(
        nodes=[survivor, paper],
        edges=[ResearchEdge(source=paper.id, target=survivor.id, type="uses")],
    )


def test_node_context_resolves_a_stale_id_to_its_survivor(tmp_path: Path):
    graph = _survivor_graph()
    publish_merge_ledger(
        merge_ledger_path(tmp_path),
        [
            MergeRecord(
                "MethodologicalConcept:pretraining",
                "MethodologicalConcept:pre-training",
                BASIS_AGGRESSIVE_KEY,
                "pretraining",
                "MethodologicalConcept",
            )
        ],
        [node.id for node in graph.nodes],
    )
    server = LLMWikiMCPServer()

    payload = server.node_context(
        graph, tmp_path, node_id="MethodologicalConcept:pretraining"
    )

    assert payload["node"]["id"] == "MethodologicalConcept:pre-training"
    assert payload["status"] == "merged"
    assert payload["merged_from"] == "MethodologicalConcept:pretraining"
    assert payload["merged_into"] == "MethodologicalConcept:pre-training"
    # The redirect answers the question too — one call, not two.
    assert [n["id"] for n in payload["neighbors"]] == ["Paper:p"]


def test_node_context_says_nothing_about_merges_on_a_live_id(tmp_path: Path):
    """The ledger is consulted only on a MISS, so a live id is never redirected
    and its payload keeps the exact shape it had before the ledger shipped."""
    graph = _survivor_graph()
    # Written straight to disk: ``publish_merge_ledger`` would prune this row
    # for exactly the reason under test, and the point here is that the READ
    # path is independently safe against a ledger that names a live id.
    _write_raw_ledger(
        tmp_path,
        [
            {
                "loser_id": "MethodologicalConcept:pre-training",  # a LIVE id
                "survivor_id": "Paper:p",
                "basis": BASIS_CROSS_TYPE,
            }
        ],
    )
    server = LLMWikiMCPServer()

    payload = server.node_context(
        graph, tmp_path, node_id="MethodologicalConcept:pre-training"
    )

    assert payload["node"]["id"] == "MethodologicalConcept:pre-training"
    assert "status" not in payload and "merged_into" not in payload


def test_an_id_absent_from_graph_and_ledger_still_raises(tmp_path: Path):
    server = LLMWikiMCPServer()
    try:
        server.node_context(_survivor_graph(), tmp_path, node_id="Paper:never-existed")
    except ValueError as exc:
        assert "not found" in str(exc)
    else:  # pragma: no cover - the assertion below is the failure message
        raise AssertionError("an unknown id must still be an error, not a redirect")


def test_node_context_without_a_project_root_degrades_instead_of_raising():
    # No root means no ledger to consult; the miss must stay a miss rather than
    # becoming a crash on a read path.
    server = LLMWikiMCPServer()
    try:
        server.node_context(_survivor_graph(), None, node_id="MethodologicalConcept:pretraining")
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected the ordinary not-found error")


# --------------------------------------------------------------------------- #
# Compile-time wiring                                                          #
# --------------------------------------------------------------------------- #


def test_ingest_publishes_the_ledger_for_the_merges_its_pipeline_performed(
    tmp_path: Path, monkeypatch
):
    """``ingest`` must hold the collector open around the WHOLE pipeline.

    The merge passes fire from ~10 sites inside it and a node absorbed at the
    first is gone by the last, so a collector opened anywhere narrower yields an
    empty ledger on every real corpus. ``_ingest`` is stubbed with a body that
    merges two nodes — no extraction, no LLM, no corpus — because what is under
    test is the collector's scope and the publish, not the pipeline.
    """
    from tesserae.project import ProjectWiki

    wiki = ProjectWiki(tmp_path)
    wiki.paths.root.mkdir(parents=True, exist_ok=True)

    def _fake_ingest(self, inputs, **kwargs):
        nodes, _edges = merge_same_type_aliased_duplicates(
            [
                _concept("MethodologicalConcept:pre-training", "Pre-Training"),
                _concept("MethodologicalConcept:pretraining", "pretraining"),
            ],
            [],
        )
        # What ``_write_artifacts`` publishes; the ledger is pruned against it.
        self._published_node_ids = {node.id for node in nodes}
        return {"node_count": len(nodes)}

    monkeypatch.setattr(ProjectWiki, "_ingest", _fake_ingest)
    result = wiki.ingest([])

    assert result["merges_recorded"] == 1
    assert load_merge_ledger(tmp_path).resolve("MethodologicalConcept:pretraining") == (
        "MethodologicalConcept:pre-training"
    )


def test_ledger_records_never_reach_graph_json(tmp_path: Path):
    """Structural guard for the leak class this repo has hit four times: the
    ledger's keys must exist in the sidecar and nowhere in the serialized graph.

    The merge passes run with a collector open and their OUTPUT is serialized
    exactly as it would be for graph.json; a pass that stashed the redirect in
    node metadata instead of the sink would fail here.
    """
    winner = _concept("MethodologicalConcept:pre-training", "Pre-Training")
    loser = _concept("MethodologicalConcept:pretraining", "pretraining")

    with collect_merges() as records:
        nodes, edges = merge_same_type_aliased_duplicates([winner, loser], [])
    payload = ResearchGraph(nodes=nodes, edges=edges).to_json(indent=2)

    assert records
    for key in ("merged_into", "merged_from", "loser_id", "survivor_id"):
        assert key not in payload, f"{key} leaked into the serialized graph"

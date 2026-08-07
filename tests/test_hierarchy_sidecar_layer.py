"""The Descent sidecar and ``graph.json`` share ONE node universe (F-11).

``.tesserae/hierarchy.json`` used to be built from a union graph that
``_write_artifacts`` then split: the research layer landed in ``graph.json``,
the code layer in ``code-graph.json``, and up to 10% of the member ids the
sidecar advertised were ids ``graph_map`` could never resolve — dead "Untitled
community" root cards with ``live_member_count == 0``, on runs that completed
cleanly. Measured on live artifacts: ai-accounts 169/1360 sidecar members
absent from ``graph.json``, 100% of them present in ``code-graph.json``.

That was fixed by making both producers partition first. The split itself is
now retired — nothing mints a code layer, so there is nothing left to
subtract — and these tests pin what survives that change:

* the sidecar names only ids the graph carries, and invents none;
* its hubs are the graph's hubs, resolvable rather than silently dropped;
* it clusters the WHOLE graph — no filter of any kind;
* the community-summary pass clusters the same object, so its minted
  ``cid``s stay equal to the sidecar's coarsest-level keys;
* and the ordering window between the two is still real, because the passes
  in between rebind ``graph``.

Hand-built graphs only — no ``compile()``, no LLM, no subprocess.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from tesserae import project as project_mod
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

from tests.test_artifact_split import _graph, _seed_project


# --------------------------------------------------------------------- helpers


def _sidecar(wiki) -> dict:  # noqa: ANN001 — ProjectWiki
    return json.loads(wiki.paths.hierarchy.read_text(encoding="utf-8"))


def _members(payload: dict) -> set[str]:
    """Every member id the sidecar advertises, across all levels."""
    return {m for level in payload["levels"] for ms in level.values() for m in ms}


class _ScriptedClient:
    """LLMJsonClient stub — same shape as ``tests/test_graph_map.py``.

    Titles MUST differ per call: ``merge_graphs`` dedupes same-type nodes by
    name, so a constant title silently collapses two COMMUNITY_SUMMARY nodes
    into one and every lockstep assertion below would pass vacuously.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete_json(self, *, system, user, schema_name, cache_key=None, **_):  # noqa: ANN001
        self.calls.append(user)
        return {
            "title": f"Demo Cluster {len(self.calls)}",
            "description": "A cluster of demo research nodes.",
            "tags": ["demo", "graph", "kg", "research", "cluster"],
        }


@pytest.fixture(autouse=True)
def _reset_community_client():
    # Reset BEFORE (in case a prior test leaked) and AFTER (so a scripted
    # client injected here can never leak into another test's compiles).
    project_mod.set_community_summaries_test_client(None)
    yield
    project_mod.set_community_summaries_test_client(None)


# ------------------------------------------------------------------ the sidecar


def test_hierarchy_sidecar_names_only_nodes_graph_json_carries(tmp_path: Path) -> None:
    """The dendrogram is built over the SAME layer ``graph.json`` gets (F-11),
    so ``graph_map`` can resolve every member the sidecar advertises."""
    wiki = _seed_project(tmp_path / "project")
    graph = _graph()
    graph_ids = {n.id for n in graph.nodes}

    wiki._write_hierarchy_sidecar(graph)
    payload = _sidecar(wiki)
    members = _members(payload)

    assert members <= graph_ids, (
        f"sidecar names ids the graph does not carry: {sorted(members - graph_ids)}"
    )
    assert set(payload["hubs"]) <= graph_ids
    # A level may legitimately filter to zero clusters (all singletons), but a
    # cluster that survives always has members — an empty member list would be
    # an unresolvable card with nothing to resolve.
    assert all(ms for level in payload["levels"] for ms in level.values())
    # Non-vacuous: the graph still produces a real community.
    assert members


def test_sidecar_hubs_are_degrees_on_the_graph_it_writes(tmp_path: Path) -> None:
    """``hub_node_ids`` must see the same projection PPR walks.

    ``retrieval/ppr.py`` silently drops hub ids it can't find in
    ``graph.json``, so a hub the sidecar names but the graph lacks is
    invisible dead weight. This fixture used to make the hub a ``SourceFile``
    precisely because ``partition_graph`` then threw it away, and the
    assertion was ``hubs == []``. With no partition the hub is a document, it
    is real, and the sidecar must publish it rather than swallow it.
    """
    from tesserae.community_summaries import HUB_DEGREE_THRESHOLD, hub_node_ids

    fan = HUB_DEGREE_THRESHOLD + 1
    nodes = [
        ResearchNode(
            id="SourceDocument:hot.md",
            name="hot.md",
            type=ResearchNodeType.SOURCE_DOCUMENT,
        )
    ]
    edges = []
    for i in range(fan):
        nodes.append(
            ResearchNode(
                id=f"Concept:c{i}",
                name=f"concept {i}",
                type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            )
        )
        edges.append(
            ResearchEdge(
                source=f"Concept:c{i}",
                target="SourceDocument:hot.md",
                type="mentioned_in",
            )
        )
    # A genuine second cluster so the dendrogram is not all singletons.
    edges.append(ResearchEdge(source="Concept:c0", target="Concept:c1", type="mentioned_in"))
    graph = ResearchGraph(nodes=nodes, edges=edges)

    assert hub_node_ids(graph) == ["SourceDocument:hot.md"], "fixture must make the doc a hub"

    wiki = _seed_project(tmp_path / "project")
    wiki._write_hierarchy_sidecar(graph)
    assert _sidecar(wiki)["hubs"] == ["SourceDocument:hot.md"]

    # ...and it resolves, which is the whole point of publishing it.
    wiki._write_artifacts(graph)
    graph_ids = {n["id"] for n in json.loads(wiki.paths.graph.read_text())["nodes"]}
    assert "SourceDocument:hot.md" in graph_ids


def test_sidecar_logs_the_whole_graph_it_clustered(tmp_path: Path, caplog) -> None:
    """The log used to report how many code-layer nodes the pass EXCLUDED.

    It excludes nothing now, so the number that matters is the one it took in:
    a count below ``len(graph.nodes)`` would mean a filter crept back.
    """
    wiki = _seed_project(tmp_path / "project")
    graph = _graph()

    with caplog.at_level(logging.INFO, logger="tesserae.project"):
        wiki._write_hierarchy_sidecar(graph)

    line = next(m for m in caplog.messages if m.startswith("hierarchy sidecar:"))
    assert f"over {len(graph.nodes)} node(s)" in line


# -------------------------------------------------- lockstep with the summaries


def test_community_summary_ids_are_hierarchy_coarsest_cids(tmp_path: Path, monkeypatch) -> None:
    """``cid = hash(member ids)``: the summary pass and the sidecar must cluster
    the same universe or every card loses ``quality="llm"`` and
    ``prune_stale_summary_caches`` deletes the caches this compile just wrote."""
    monkeypatch.delenv("TESSERAE_COMMUNITY_SUMMARIES", raising=False)
    project_mod.set_community_summaries_test_client(_ScriptedClient())

    wiki = _seed_project(tmp_path / "project")
    live_cids = wiki._write_hierarchy_sidecar(_graph())
    # ``min_size`` must be lowered: the wiring default of 5 filters the small
    # cluster in the fixture and the test would pass vacuously with zero
    # minted summaries.
    merged = wiki._merge_community_summaries(
        _graph(), {"community_summaries": {"enabled": True, "min_size": 2}}
    )

    minted = {
        n.id for n in merged.nodes if n.type is ResearchNodeType.COMMUNITY_SUMMARY
    }
    coarsest = set(_sidecar(wiki)["levels"][-1])
    assert minted, "fixture must mint at least one COMMUNITY_SUMMARY node"
    assert minted <= coarsest
    # Same key space as prune_stale_summary_caches' liveness manifest, so the
    # pass cannot delete the caches it just wrote.
    assert minted <= live_cids


def test_merge_community_summaries_adds_to_the_graph_without_dropping_it(
    tmp_path: Path, monkeypatch
) -> None:
    """The pass is additive. ``_write_artifacts`` (SQLite, provenance,
    Graphiti, vault, graph.json) needs every input node back, so clustering
    must never be allowed to become a filter on the way through."""
    monkeypatch.delenv("TESSERAE_COMMUNITY_SUMMARIES", raising=False)
    project_mod.set_community_summaries_test_client(_ScriptedClient())

    wiki = _seed_project(tmp_path / "project")
    graph = _graph()

    merged = wiki._merge_community_summaries(
        graph, {"community_summaries": {"enabled": True, "min_size": 2}}
    )

    assert {n.id for n in graph.nodes} <= {n.id for n in merged.nodes}
    # Every summary describes members that are actually present.
    merged_ids = {n.id for n in merged.nodes}
    for node in merged.nodes:
        if node.type is ResearchNodeType.COMMUNITY_SUMMARY:
            assert set(node.metadata.get("member_ids") or []) <= merged_ids


def test_the_sidecar_and_graph_json_can_still_diverge(tmp_path: Path) -> None:
    """The residual ordering window is REAL, so nothing may claim otherwise.

    Retiring the split removed the *loudest* way a sidecar member could vanish
    from ``graph.json`` (a schema-drift rename retyped a node across the two
    files), but not the window itself: ``compile()`` rebinds ``graph`` through
    ``_merge_community_summaries`` / ``_merge_distillation``, and
    ``_write_artifacts`` rebinds it again through ``_apply_vault_overlay`` —
    which HARVESTS DELETIONS — before writing. So the two passes can still see
    different objects, which is exactly what ``live_member_count`` on each
    hierarchy card exists to report. Demonstrated here directly: the sidecar
    names a node, and the graph that reaches disk no longer has it.
    """
    graph = _graph()
    victim_id = "Concept:gs"

    wiki = _seed_project(tmp_path / "project")
    wiki._write_hierarchy_sidecar(graph)
    named = _members(_sidecar(wiki))
    assert victim_id in named, "fixture must place the victim in a community"

    # A later pass drops it, exactly as harvesting a deleted vault page does.
    without_victim = ResearchGraph(
        nodes=[n for n in graph.nodes if n.id != victim_id],
        edges=[e for e in graph.edges if victim_id not in (e.source, e.target)],
    )
    wiki._write_artifacts(without_victim)

    graph_ids = {n["id"] for n in json.loads(wiki.paths.graph.read_text())["nodes"]}
    assert victim_id not in graph_ids
    assert not named <= graph_ids, "the ordering window is real; the prose must say so"

    doc = project_mod.ProjectWiki._write_hierarchy_sidecar.__doc__ or ""
    assert "cannot diverge by construction" not in doc, (
        "the sidecar and graph.json CAN diverge — this test just did it"
    )


def test_every_sidecar_member_resolves_when_nothing_intervenes(tmp_path: Path) -> None:
    """The happy path, end-to-end on the file that matters: hand the same graph
    to both passes and every id the sidecar advertises is in ``graph.json``."""
    wiki = _seed_project(tmp_path / "project")
    graph = _graph()

    wiki._write_hierarchy_sidecar(graph)
    wiki._write_artifacts(graph)

    graph_ids = {n["id"] for n in json.loads(wiki.paths.graph.read_text())["nodes"]}
    assert _members(_sidecar(wiki)) <= graph_ids
    assert not wiki.paths.code_graph.exists()

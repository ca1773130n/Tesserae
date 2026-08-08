"""The Descent sidecar and ``graph.json`` share ONE node universe (F-11).

``_write_artifacts`` splits the union ``ResearchGraph`` with
:func:`partition_graph`: the research layer lands in ``.tesserae/graph.json``,
the code layer in ``.tesserae/code-graph.json``, and a code-graph node
*never* appears in ``graph.json``. ``.tesserae/hierarchy.json`` used to be
built from the union, so up to 10% of the member ids it advertised were ids
``graph_map`` could never resolve — dead "Untitled community" root cards with
``live_member_count == 0``, on runs that completed cleanly. Measured on live
artifacts: ai-accounts 169/1360 sidecar members absent from ``graph.json``,
100% of them present in ``code-graph.json``.

These tests pin the by-construction fix: both producers (the sidecar and the
community-summary pass) cluster the research layer only, so the two can no
longer diverge and their community ids stay in lockstep.

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
from tesserae.wiki_projector import partition_graph

from tests.test_artifact_split import _mixed_graph, _seed_project


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
    graph = _mixed_graph()
    research, code = partition_graph(graph)
    code_ids = {n.id for n in code.nodes}
    research_ids = {n.id for n in research.nodes}

    wiki._write_hierarchy_sidecar(graph)
    payload = _sidecar(wiki)
    members = _members(payload)

    # Before the fix Louvain over the union minted a code-ONLY community at
    # every level (e.g. CommunitySummary:1c72c3e6b2bababc =
    # [CodeClass:ProjectWiki, CodeFunction:compile, CodeProject:Tesserae,
    # SourceFile:project.py]) — four ids graph.json will never carry.
    assert members.isdisjoint(code_ids), (
        f"sidecar names code-layer ids graph.json never carries: {sorted(members & code_ids)}"
    )
    assert members <= research_ids
    assert set(payload["hubs"]).isdisjoint(code_ids)
    # A level may legitimately filter to zero clusters (all singletons), but a
    # cluster that survives always has members — an empty member list would be
    # an unresolvable card with nothing to resolve.
    assert all(ms for level in payload["levels"] for ms in level.values())
    # Non-vacuous: the research layer still produces a real community.
    assert members


def test_sidecar_hubs_are_degrees_on_the_research_projection(tmp_path: Path) -> None:
    """``hub_node_ids`` must see the same projection PPR walks.

    ``retrieval/ppr.py`` silently drops hub ids it can't find in
    ``graph.json``, so a code-layer hub is invisible dead weight. Degree here
    comes only from cross-layer edges, which ``partition_graph`` drops.
    """
    from tesserae.community_summaries import HUB_DEGREE_THRESHOLD

    fan = HUB_DEGREE_THRESHOLD + 1
    nodes = [
        ResearchNode(
            id="SourceFile:hot.py", name="hot.py", type=ResearchNodeType.SOURCE_FILE
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
                source=f"Concept:c{i}", target="SourceFile:hot.py", type="mentioned_in"
            )
        )
    # A genuine research-layer cluster so the dendrogram is not all singletons.
    edges.append(ResearchEdge(source="Concept:c0", target="Concept:c1", type="mentioned_in"))
    graph = ResearchGraph(nodes=nodes, edges=edges)

    from tesserae.community_summaries import hub_node_ids

    assert hub_node_ids(graph) == ["SourceFile:hot.py"], "fixture must make the code node a hub"

    wiki = _seed_project(tmp_path / "project")
    wiki._write_hierarchy_sidecar(graph)
    assert _sidecar(wiki)["hubs"] == []


def test_sidecar_logs_how_many_code_nodes_it_excluded(tmp_path: Path, caplog) -> None:
    """The exclusion is silent in the artifact, so it is stated in the log."""
    wiki = _seed_project(tmp_path / "project")
    graph = _mixed_graph()
    n_code = len(partition_graph(graph)[1].nodes)

    with caplog.at_level(logging.INFO, logger="tesserae.project"):
        wiki._write_hierarchy_sidecar(graph)

    line = next(m for m in caplog.messages if m.startswith("hierarchy sidecar:"))
    assert f"{n_code} code-layer node(s) excluded" in line


# -------------------------------------------------- lockstep with the summaries


def test_community_summary_ids_are_hierarchy_coarsest_cids(tmp_path: Path, monkeypatch) -> None:
    """``cid = hash(member ids)``: the summary pass and the sidecar must cluster
    the same universe or every card loses ``quality="llm"`` and
    ``prune_stale_summary_caches`` deletes the caches this compile just wrote."""
    monkeypatch.delenv("TESSERAE_COMMUNITY_SUMMARIES", raising=False)
    project_mod.set_community_summaries_test_client(_ScriptedClient())

    wiki = _seed_project(tmp_path / "project")
    live_cids = wiki._write_hierarchy_sidecar(_mixed_graph())
    # ``min_size`` must be lowered: the wiring default of 5 filters the
    # 4-member research cluster in the fixture and the test would pass
    # vacuously with zero minted summaries.
    merged = wiki._merge_community_summaries(
        _mixed_graph(), {"community_summaries": {"enabled": True, "min_size": 2}}
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


def test_merge_community_summaries_still_returns_the_union(tmp_path: Path, monkeypatch) -> None:
    """Only the *clustering* input is partitioned. ``_write_artifacts`` (SQLite,
    provenance, Graphiti, vault, code-graph.json) still needs the union back."""
    monkeypatch.delenv("TESSERAE_COMMUNITY_SUMMARIES", raising=False)
    project_mod.set_community_summaries_test_client(_ScriptedClient())

    wiki = _seed_project(tmp_path / "project")
    graph = _mixed_graph()
    code_ids = {n.id for n in partition_graph(graph)[1].nodes}

    merged = wiki._merge_community_summaries(
        graph, {"community_summaries": {"enabled": True, "min_size": 2}}
    )

    assert code_ids <= {n.id for n in merged.nodes}
    # ...and no summary is minted *for* the code layer any more.
    for node in merged.nodes:
        if node.type is ResearchNodeType.COMMUNITY_SUMMARY:
            assert set(node.metadata.get("member_ids") or []).isdisjoint(code_ids)


def test_sidecar_docstring_does_not_promise_more_than_the_ordering_allows(
    tmp_path: Path,
) -> None:
    """The residual window is REAL, so the docstring must name it, not deny it.

    Two halves, and neither works alone. The demonstration shows a node can
    still leave ``graph.json`` AFTER the sidecar named it: the fix above is
    systematic (both producers partition identically) but not
    *by construction*, because ``compile()`` rebinds ``graph`` through
    ``_merge_community_summaries`` / ``_merge_distillation``, and
    ``_write_artifacts`` rebinds it again through ``_apply_vault_overlay`` /
    ``SynthesisProjector.project`` / ``_run_memory_passes`` before reaching its
    own ``partition_graph`` call. ``apply_schema_drift`` — inside the last of
    those, opt-in via ``TESSERAE_SCHEMA_DRIFT_APPLY`` — RENAMES ``node.type``,
    and ``is_code_graph_node`` dispatches on type, so a node crosses the split
    with no id change at all: the original F4 symptom, reproduced here.

    The text half then pins the prose, following
    ``tests/test_docs_install_and_detach_claims.py``: the defect was a false
    claim, and prose is the only place a false claim can regress.
    """
    graph = _mixed_graph()
    victim_id = "Concept:gs"

    wiki = _seed_project(tmp_path / "project")
    wiki._write_hierarchy_sidecar(graph)
    named = _members(_sidecar(wiki))
    assert victim_id in named, "fixture must place the victim in a community"

    # A later pass retypes it, exactly as an approved schema-drift rename does,
    # and ``_write_artifacts`` re-derives the split from ITS graph.
    retyped = ResearchGraph(
        nodes=[
            n
            if n.id != victim_id
            else ResearchNode(id=n.id, name=n.name, type=ResearchNodeType.CODE_FUNCTION)
            for n in graph.nodes
        ],
        edges=list(graph.edges),
    )
    wiki._write_artifacts(retyped)

    graph_ids = {n["id"] for n in json.loads(wiki.paths.graph.read_text())["nodes"]}
    code_ids = {n["id"] for n in json.loads(wiki.paths.code_graph.read_text())["nodes"]}
    assert victim_id in code_ids and victim_id not in graph_ids
    assert not named <= graph_ids, "the ordering window is real; the docstring must say so"

    doc = project_mod.ProjectWiki._write_hierarchy_sidecar.__doc__ or ""
    assert "cannot diverge by construction" not in doc, (
        "the sidecar and graph.json CAN diverge — this test just did it"
    )
    for cause in ("_run_memory_passes", "apply_schema_drift", "_write_artifacts"):
        assert cause in doc, f"docstring must name the window's cause: {cause}"


def test_write_artifacts_still_splits_after_the_sidecar_ran(tmp_path: Path) -> None:
    """End-to-end on the two files that matter: every sidecar member resolves
    in ``graph.json``, and ``code-graph.json`` is byte-unaffected by the fix."""
    wiki = _seed_project(tmp_path / "project")
    graph = _mixed_graph()

    wiki._write_hierarchy_sidecar(graph)
    wiki._write_artifacts(graph)

    graph_ids = {n["id"] for n in json.loads(wiki.paths.graph.read_text())["nodes"]}
    code_ids = {n["id"] for n in json.loads(wiki.paths.code_graph.read_text())["nodes"]}

    assert _members(_sidecar(wiki)) <= graph_ids
    assert code_ids == {n.id for n in partition_graph(graph)[1].nodes}

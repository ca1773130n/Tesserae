"""Tests for the post-compile community-summary pass.

Covers:
* Louvain / label-propagation detects the expected clusters on a tiny
  hand-rolled graph.
* :func:`compile_community_summaries` mints one COMMUNITY_SUMMARY node
  per cluster, plus ``summarizes`` edges to every member.
* Per-cluster cache files land under the configured cache dir and a
  membership-stable re-run skips the LLM (call count is unchanged).
* The MCP ``list_communities`` tool returns the minted nodes ranked by
  member count and respects ``min_size`` / ``limit``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from pathlib import Path
from typing import Any, List, Optional, Union

import pytest

from tesserae.community_summaries import (
    _cache_path,
    community_id,
    compile_community_summaries,
    detect_communities,
    detect_community_levels,
    hub_node_ids,
    is_enabled_via_env,
    level_cache_path,
    materialize_community_summary,
    prune_stale_summary_caches,
    read_warm_summary,
)
from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class _ScriptedClient:
    """LLMJsonClient stub. Counts calls and returns deterministic JSON."""

    def __init__(
        self,
        scripted: Optional[List[Optional[Union[dict, list]]]] = None,
    ) -> None:
        # When ``scripted`` is None we return a generated payload per call
        # so the test doesn't have to enumerate every cluster in advance.
        self._scripted = list(scripted) if scripted is not None else None
        self.calls: List[dict] = []

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        cache_key: Any = None,
        max_retries: int = 2,
    ) -> Optional[Union[dict, list]]:
        self.calls.append(
            {
                "schema_name": schema_name,
                "cache_key": cache_key,
                "system": system,
                "user": user,
            }
        )
        if self._scripted is not None:
            return self._scripted.pop(0) if self._scripted else None
        index = len(self.calls)
        return {
            "title": f"Cluster {index}",
            "description": f"Test description for cluster {index}.",
            "tags": ["alpha", "beta", "gamma", "delta", "epsilon"],
        }


def _two_cluster_graph() -> ResearchGraph:
    """Two densely-connected triangles wired across the ``shares_concept_with``
    edge type so an undirected community detector splits them cleanly."""
    nodes = [
        ResearchNode(id=f"Concept:a{i}", name=f"A{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ] + [
        ResearchNode(id=f"Concept:b{i}", name=f"B{i}", type=ResearchNodeType.CONCEPT)
        for i in range(3)
    ]
    edges = []
    for i in range(3):
        for j in range(i + 1, 3):
            edges.append(
                ResearchEdge(
                    source=f"Concept:a{i}",
                    target=f"Concept:a{j}",
                    type="shares_concept_with",
                )
            )
            edges.append(
                ResearchEdge(
                    source=f"Concept:b{i}",
                    target=f"Concept:b{j}",
                    type="shares_concept_with",
                )
            )
    # A single bridge edge that should still leave Louvain seeing two
    # communities (the bridge weight is dwarfed by the dense intra-cluster
    # edges).
    edges.append(
        ResearchEdge(
            source="Concept:a0",
            target="Concept:b0",
            type="shares_concept_with",
        )
    )
    return ResearchGraph(nodes=nodes, edges=edges)


def _hierarchical_graph() -> ResearchGraph:
    """Eight triangles ring-bridged into two super-groups, plus one isolate.

    Louvain (seed=0) on this fixture produces a >= 2-level dendrogram: the
    finest level is the eight triangles; a coarser level merges some of them
    along the ring bridges. The isolated node stays a singleton community at
    every level, exercising the per-level ``len > 1`` filter.
    """
    nodes = [
        ResearchNode(id=f"Concept:n{b}_{i}", name=f"N{b}{i}", type=ResearchNodeType.CONCEPT)
        for b in range(8)
        for i in range(3)
    ]
    nodes.append(
        ResearchNode(id="Concept:isolated", name="Iso", type=ResearchNodeType.CONCEPT)
    )
    edges = []
    for b in range(8):
        for i in range(3):
            for j in range(i + 1, 3):
                edges.append(
                    ResearchEdge(
                        source=f"Concept:n{b}_{i}",
                        target=f"Concept:n{b}_{j}",
                        type="shares_concept_with",
                    )
                )
    # Ring of bridges inside each super-group {0..3} / {4..7}, plus a single
    # cross-group edge — dense enough for Louvain to aggregate past the
    # triangle level, sparse enough to keep the triangles as the finest level.
    for group in (range(0, 4), range(4, 8)):
        members = list(group)
        for k, b in enumerate(members):
            nxt = members[(k + 1) % len(members)]
            edges.append(
                ResearchEdge(
                    source=f"Concept:n{b}_0",
                    target=f"Concept:n{nxt}_1",
                    type="shares_concept_with",
                )
            )
    edges.append(
        ResearchEdge(
            source="Concept:n0_2",
            target="Concept:n4_2",
            type="shares_concept_with",
        )
    )
    return ResearchGraph(nodes=nodes, edges=edges)


def _legacy_detect_communities(graph: ResearchGraph) -> List[List[str]]:
    """Verbatim pre-dendrogram body of :func:`detect_communities`.

    Kept as the parity oracle for the ``louvain_partitions`` swap: the
    refactored ``detect_communities`` must return byte-identical output
    (Descent PR3 / CMP-03), because community ids flow into ``graph.json``.
    """
    nodes = sorted(n.id for n in graph.nodes)
    if not nodes:
        return []
    node_set = set(nodes)
    edge_pairs = set()
    for edge in graph.edges:
        if edge.source == edge.target:
            continue
        if edge.source not in node_set or edge.target not in node_set:
            continue
        lo, hi = (edge.source, edge.target) if edge.source < edge.target else (edge.target, edge.source)
        edge_pairs.add((lo, hi))

    import networkx as nx

    g = nx.Graph()
    g.add_nodes_from(nodes)
    g.add_edges_from(sorted(edge_pairs))
    clusters = nx.community.louvain_communities(g, seed=0)
    return [sorted(c) for c in clusters if len(c) > 1]


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detect_communities_returns_two_clusters() -> None:
    graph = _two_cluster_graph()
    clusters = detect_communities(graph)
    # We expect exactly 2 non-singleton communities of size 3 each.
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [3, 3], f"expected two 3-member clusters, got {clusters!r}"

    # Members of each cluster share a common prefix ("a" or "b") given the
    # construction above.
    for cluster in clusters:
        prefixes = {member.split(":")[1][0] for member in cluster}
        assert len(prefixes) == 1, f"cluster {cluster!r} bridged the divide"


def test_detect_communities_empty_graph_returns_empty_list() -> None:
    assert detect_communities(ResearchGraph()) == []


def test_community_id_is_stable_for_same_members() -> None:
    a = community_id(["x", "y", "z"])
    b = community_id(["z", "y", "x"])
    assert a == b
    assert a.startswith("CommunitySummary:")


def test_detect_communities_matches_legacy_louvain_communities() -> None:
    # PR3 parity oracle: the ``louvain_partitions`` internals must reproduce
    # the old direct ``louvain_communities(seed=0)`` output exactly — same
    # clusters, same cluster order, same member order — on every fixture.
    for graph in (_two_cluster_graph(), _hierarchical_graph()):
        assert detect_communities(graph) == _legacy_detect_communities(graph)


def test_detect_community_levels_empty_graph_returns_empty_list() -> None:
    assert detect_community_levels(ResearchGraph()) == []


def test_detect_community_levels_coarsest_equals_detect_communities() -> None:
    for graph in (_two_cluster_graph(), _hierarchical_graph()):
        levels = detect_community_levels(graph)
        assert levels, "expected at least one dendrogram level"
        assert levels[-1] == detect_communities(graph)


def test_detect_community_levels_finest_to_coarsest_refinement() -> None:
    levels = detect_community_levels(_hierarchical_graph())
    assert len(levels) >= 2, f"fixture should dendrogram past one level, got {levels!r}"
    # Finest-to-coarsest: every finer cluster nests inside one coarser cluster.
    for finer, coarser in zip(levels, levels[1:]):
        for cluster in finer:
            assert any(
                set(cluster) <= set(parent) for parent in coarser
            ), f"cluster {cluster!r} is not nested in the next-coarser level"
    # The finest level is the eight triangles.
    assert sorted(len(c) for c in levels[0]) == [3] * 8


def test_detect_community_levels_filters_singletons_per_level() -> None:
    # The isolate stays a singleton community at every level; the per-level
    # filter (same ``len > 1`` rule detect_communities always applied to the
    # coarsest level) must drop it everywhere, and members stay sorted.
    levels = detect_community_levels(_hierarchical_graph())
    for level in levels:
        for cluster in level:
            assert len(cluster) > 1
            assert cluster == sorted(cluster)
            assert "Concept:isolated" not in cluster


def test_detect_community_levels_is_deterministic() -> None:
    graph = _hierarchical_graph()
    assert detect_community_levels(graph) == detect_community_levels(graph)


# ---------------------------------------------------------------------------
# Hub detection (Descent PR4 — hierarchy sidecar ``hubs`` list)
# ---------------------------------------------------------------------------


def _star_graph(leaves: int) -> ResearchGraph:
    """One hub wired to ``leaves`` leaf nodes, with dedup/self-loop noise.

    Includes a duplicate reversed edge and a self-loop so the test proves the
    hub degree is computed over the SAME deduped undirected projection Louvain
    uses (parallel/reversed edges count once, self-loops never).
    """
    nodes = [
        ResearchNode(id="Concept:hub", name="Hub", type=ResearchNodeType.CONCEPT)
    ] + [
        ResearchNode(id=f"Concept:leaf{i}", name=f"L{i}", type=ResearchNodeType.CONCEPT)
        for i in range(leaves)
    ]
    edges = [
        ResearchEdge(source="Concept:hub", target=f"Concept:leaf{i}", type="shares_concept_with")
        for i in range(leaves)
    ]
    # Reversed duplicate of the first spoke + a self-loop: both must not
    # inflate the hub's undirected degree.
    edges.append(
        ResearchEdge(source="Concept:leaf0", target="Concept:hub", type="shares_concept_with")
    )
    edges.append(
        ResearchEdge(source="Concept:hub", target="Concept:hub", type="shares_concept_with")
    )
    return ResearchGraph(nodes=nodes, edges=edges)


def test_hub_node_ids_flags_only_nodes_above_threshold() -> None:
    graph = _star_graph(5)
    # Hub degree is exactly 5 on the deduped projection (reversed duplicate
    # and self-loop excluded), so threshold 4 flags it and threshold 5 does not.
    assert hub_node_ids(graph, degree_threshold=4) == ["Concept:hub"]
    assert hub_node_ids(graph, degree_threshold=5) == []


def test_hub_node_ids_empty_graph_and_default_threshold() -> None:
    assert hub_node_ids(ResearchGraph()) == []
    # Fixture degrees are tiny, so the production default (200, the Descent
    # degree cap) flags nothing.
    assert hub_node_ids(_hierarchical_graph()) == []


# ---------------------------------------------------------------------------
# Cache pruning (Descent PR4 — live-cid manifest across ALL levels, §9.5)
# ---------------------------------------------------------------------------


def _seed_cache_files(cache_dir: Path, cids: List[str]) -> List[Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for cid in cids:
        path = cache_dir / f"{cid.replace(':', '_')}.json"
        path.write_text("{}", encoding="utf-8")
        paths.append(path)
    return paths


def test_prune_deletes_only_cids_live_at_no_level(tmp_path: Path) -> None:
    cache_dir = tmp_path / "community_summaries"
    live = community_id(["a", "b"])
    dead = community_id(["x", "y"])
    live_path, dead_path = _seed_cache_files(cache_dir, [live, dead])
    deleted = prune_stale_summary_caches(cache_dir, {live})
    assert deleted == [dead_path.name]
    assert live_path.exists()
    assert not dead_path.exists()


def test_prune_after_leaf_shift_keeps_untouched_level_caches(tmp_path: Path) -> None:
    """§9.5: pruning keys on ALL-level liveness, not just the coarsest level.

    A one-leaf membership shift re-mints the cids of exactly the communities
    that contain the shifted leaf (at every level). Caches for untouched
    communities — including live-but-unvisited ones — must survive the prune;
    only the pre-shift cids that exist at NO post-shift level are deleted.
    """
    cache_dir = tmp_path / "community_summaries"
    # Two dendrogram levels before the shift: fine {ab, cd} -> coarse {abcd}.
    levels_before = [[["a", "b"], ["c", "d"]], [["a", "b", "c", "d"]]]
    before_cids = [
        community_id(members) for level in levels_before for members in level
    ]
    _seed_cache_files(cache_dir, before_cids)
    # Leaf "e" joins the {a,b} community: its cid and its ancestor's cid
    # change; {c,d} is untouched at its level.
    levels_after = [[["a", "b", "e"], ["c", "d"]], [["a", "b", "c", "d", "e"]]]
    live = {
        community_id(members) for level in levels_after for members in level
    }
    deleted = prune_stale_summary_caches(cache_dir, live)
    untouched = community_id(["c", "d"])
    assert _cache_path(cache_dir, untouched).exists(), (
        "prune deleted the cache of an untouched community — it must key on "
        "all-level liveness, not visit history"
    )
    assert sorted(deleted) == sorted(
        _cache_path(cache_dir, community_id(members)).name
        for members in (["a", "b"], ["a", "b", "c", "d"])
    )


def test_prune_ignores_foreign_files_and_missing_dir(tmp_path: Path) -> None:
    cache_dir = tmp_path / "community_summaries"
    cache_dir.mkdir(parents=True)
    foreign = cache_dir / "notes.json"
    foreign.write_text("{}", encoding="utf-8")
    tmp_file = cache_dir / "CommunitySummary_abc.tmp.123.deadbeef"
    tmp_file.write_text("{}", encoding="utf-8")
    assert prune_stale_summary_caches(cache_dir, set()) == []
    assert foreign.exists()
    assert tmp_file.exists()
    assert prune_stale_summary_caches(tmp_path / "does-not-exist", {"x"}) == []


# ---------------------------------------------------------------------------
# Compile pass
# ---------------------------------------------------------------------------


def test_compile_mints_summary_nodes_and_summarizes_edges(tmp_path: Path) -> None:
    graph = _two_cluster_graph()
    client = _ScriptedClient()
    cache_dir = tmp_path / "community_summaries"

    slice_graph = compile_community_summaries(
        graph,
        cache_dir=cache_dir,
        json_client=client,
        min_size=3,
    )

    # Two clusters → two COMMUNITY_SUMMARY nodes; 3 members each → 6 edges.
    summary_nodes = [
        n for n in slice_graph.nodes
        if n.type == ResearchNodeType.COMMUNITY_SUMMARY
    ]
    assert len(summary_nodes) == 2

    summarizes_edges = [e for e in slice_graph.edges if e.type == "summarizes"]
    assert len(summarizes_edges) == 6
    for edge in summarizes_edges:
        source_node = next(n for n in summary_nodes if n.id == edge.source)
        assert edge.target in source_node.metadata["member_ids"]

    # The LLM was called exactly once per cluster.
    assert len(client.calls) == 2

    # Cache files exist on disk, one per cluster.
    cache_files = list(cache_dir.glob("CommunitySummary_*.json"))
    assert len(cache_files) == 2
    # Each cache file is well-formed JSON carrying the validated summary.
    for path in cache_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert {"title", "description", "tags"} <= set(payload["summary"].keys())
        assert isinstance(payload["member_ids"], list) and payload["member_ids"]
        # Content digest persisted so member edits invalidate the cache.
        assert isinstance(payload["members_digest"], str) and payload["members_digest"]


def test_rerun_with_same_membership_skips_llm(tmp_path: Path) -> None:
    graph = _two_cluster_graph()
    cache_dir = tmp_path / "community_summaries"

    first = _ScriptedClient()
    slice_first = compile_community_summaries(
        graph, cache_dir=cache_dir, json_client=first, min_size=3,
    )
    assert len(first.calls) == 2
    assert {n.type for n in slice_first.nodes} == {ResearchNodeType.COMMUNITY_SUMMARY}

    # Re-run with a fresh client; cache should service every cluster so the
    # LLM is NEVER called again.
    second = _ScriptedClient()
    slice_second = compile_community_summaries(
        graph, cache_dir=cache_dir, json_client=second, min_size=3,
    )
    assert second.calls == [], "cache miss: LLM was re-invoked"
    # Same set of community ids minted both times — membership is stable.
    assert {n.id for n in slice_first.nodes} == {n.id for n in slice_second.nodes}
    # The cached run produces byte-identical node metadata to the first run:
    # COMMUNITY_SUMMARY nodes are persisted into site/graph.json, which §13
    # requires to be stable across re-compiles, so no per-run provenance (such
    # as a cache-hit flag) may leak into the node. The stronger
    # ``second.calls == []`` assertion above already proves the cache served
    # every cluster without re-invoking the LLM.
    meta_first = {n.id: n.metadata for n in slice_first.nodes}
    for node in slice_second.nodes:
        assert node.metadata == meta_first[node.id]
        assert "cache_hit" not in node.metadata


def _with_changed_description(graph: ResearchGraph, node_id: str) -> ResearchGraph:
    """Same membership/edges, one member's description rewritten."""
    return ResearchGraph(
        nodes=[
            dataclasses.replace(n, description="rewritten description")
            if n.id == node_id
            else n
            for n in graph.nodes
        ],
        edges=graph.edges,
    )


def test_changed_member_description_invalidates_only_that_cluster(
    tmp_path: Path,
) -> None:
    graph = _two_cluster_graph()
    cache_dir = tmp_path / "community_summaries"
    first = _ScriptedClient()
    compile_community_summaries(
        graph, cache_dir=cache_dir, json_client=first, min_size=3,
    )
    assert len(first.calls) == 2

    # Same membership (same community ids), but one member of the "a"
    # cluster changes its description → only that cluster re-summarizes.
    changed = _with_changed_description(graph, "Concept:a0")
    second = _ScriptedClient()
    slice_second = compile_community_summaries(
        changed, cache_dir=cache_dir, json_client=second, min_size=3,
    )
    assert len(second.calls) == 1, "content drift did not trigger re-summarize"
    # Node identity is membership-keyed, so ids are unchanged.
    assert {n.id for n in slice_second.nodes} == {
        community_id([f"Concept:a{i}" for i in range(3)]),
        community_id([f"Concept:b{i}" for i in range(3)]),
    }


def test_legacy_cache_without_digest_hits_once_and_backfills(tmp_path: Path) -> None:
    graph = _two_cluster_graph()
    cache_dir = tmp_path / "community_summaries"
    first = _ScriptedClient()
    compile_community_summaries(
        graph, cache_dir=cache_dir, json_client=first, min_size=3,
    )

    # Simulate pre-digest caches by stripping the digest field.
    for path in cache_dir.glob("CommunitySummary_*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("members_digest", None)
        path.write_text(json.dumps(payload), encoding="utf-8")

    # Legacy caches are honoured once (no LLM stampede)...
    second = _ScriptedClient()
    compile_community_summaries(
        graph, cache_dir=cache_dir, json_client=second, min_size=3,
    )
    assert second.calls == [], "legacy cache without digest was not honoured"
    # ...and the digest is backfilled on disk.
    for path in cache_dir.glob("CommunitySummary_*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["members_digest"]

    # Subsequent runs are digest-verified pure hits.
    third = _ScriptedClient()
    compile_community_summaries(
        graph, cache_dir=cache_dir, json_client=third, min_size=3,
    )
    assert third.calls == []


def test_stale_cache_without_llm_serves_stale_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    graph = _two_cluster_graph()
    cache_dir = tmp_path / "community_summaries"
    compile_community_summaries(
        graph, cache_dir=cache_dir, json_client=_ScriptedClient(), min_size=3,
    )

    changed = _with_changed_description(graph, "Concept:a0")
    with caplog.at_level(logging.WARNING, logger="tesserae.community_summaries"):
        slice_graph = compile_community_summaries(
            changed, cache_dir=cache_dir, json_client=None, min_size=3,
        )
    # No LLM available: the stale summary is served (node still minted,
    # no graph churn) and the staleness is surfaced as a warning.
    assert len(slice_graph.nodes) == 2
    assert any("stale" in record.getMessage() for record in caplog.records)


def test_compile_returns_empty_when_no_cluster_meets_min_size() -> None:
    graph = _two_cluster_graph()
    client = _ScriptedClient()
    # Clusters are size 3; ``min_size=5`` filters them out.
    slice_graph = compile_community_summaries(
        graph, cache_dir=Path("/tmp/never-written"), json_client=client, min_size=5,
    )
    assert slice_graph.nodes == []
    assert slice_graph.edges == []
    assert client.calls == []


def test_compile_drops_cluster_when_llm_returns_invalid_payload(tmp_path: Path) -> None:
    graph = _two_cluster_graph()
    # Both calls return missing-tags payloads → validator rejects them.
    bad = _ScriptedClient(
        scripted=[
            {"title": "T1", "description": "D1"},  # no tags
            {"title": "T2", "description": "D2", "tags": []},  # empty tags
        ]
    )
    slice_graph = compile_community_summaries(
        graph, cache_dir=tmp_path / "cache", json_client=bad, min_size=3,
    )
    assert slice_graph.nodes == []
    assert slice_graph.edges == []
    # No cache files written on failure (we only persist validated summaries).
    assert list((tmp_path / "cache").glob("*.json")) == []


# ---------------------------------------------------------------------------
# Lazy materialization (§5.2, PR6)
# ---------------------------------------------------------------------------


def _lazy_members(n: int = 3) -> List[ResearchNode]:
    return [
        ResearchNode(
            id=f"Concept:m{i}",
            name=f"M{i}",
            type=ResearchNodeType.CONCEPT,
            description=f"description of member {i}",
        )
        for i in range(n)
    ]


def test_level_cache_path_is_level_scoped(tmp_path: Path) -> None:
    cid = community_id(["a", "b"])
    path = level_cache_path(tmp_path, 2, cid)
    assert path == tmp_path / "2" / f"{cid.replace(':', '_')}.json"


def test_materialize_writes_level_scoped_envelope_once(tmp_path: Path) -> None:
    members = _lazy_members()
    member_ids = [n.id for n in members]
    cid = community_id(member_ids)
    client = _ScriptedClient()

    summary = materialize_community_summary(
        members,
        cid=cid,
        member_ids=member_ids,
        level=1,
        cache_dir=tmp_path,
        json_client=client,
    )
    assert summary == (
        "Cluster 1",
        "Test description for cluster 1.",
        ["alpha", "beta", "gamma", "delta", "epsilon"],
    )
    assert len(client.calls) == 1

    # Cache landed under the level subdir with the compile pass's envelope.
    payload = json.loads(
        level_cache_path(tmp_path, 1, cid).read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == 1
    assert payload["community_id"] == cid
    assert payload["member_ids"] == member_ids
    assert isinstance(payload["members_digest"], str) and payload["members_digest"]
    assert {"title", "description", "tags"} <= set(payload["summary"].keys())

    # Warm re-visit: cache hit, the LLM is never called again.
    again = materialize_community_summary(
        members,
        cid=cid,
        member_ids=member_ids,
        level=1,
        cache_dir=tmp_path,
        json_client=_ScriptedClient(),
    )
    assert again == summary
    assert read_warm_summary(tmp_path, 1, cid, members) == summary


def test_materialize_without_client_returns_none(tmp_path: Path) -> None:
    members = _lazy_members()
    cid = community_id([n.id for n in members])
    assert (
        materialize_community_summary(
            members,
            cid=cid,
            member_ids=[n.id for n in members],
            level=0,
            cache_dir=tmp_path,
            json_client=None,
        )
        is None
    )
    assert not (tmp_path / "0").exists()


def test_materialize_never_raises(tmp_path: Path) -> None:
    class _ExplodingClient:
        def complete_json(self, **kwargs: Any) -> None:
            raise RuntimeError("boom")

    members = _lazy_members()
    cid = community_id([n.id for n in members])
    assert (
        materialize_community_summary(
            members,
            cid=cid,
            member_ids=[n.id for n in members],
            level=0,
            cache_dir=tmp_path,
            json_client=_ExplodingClient(),
        )
        is None
    )
    assert not (tmp_path / "0").exists()


def test_materialize_invalid_payload_not_cached(tmp_path: Path) -> None:
    members = _lazy_members()
    cid = community_id([n.id for n in members])
    bad = _ScriptedClient(scripted=[{"title": "T", "description": "D"}])  # no tags
    assert (
        materialize_community_summary(
            members,
            cid=cid,
            member_ids=[n.id for n in members],
            level=0,
            cache_dir=tmp_path,
            json_client=bad,
        )
        is None
    )
    assert not (tmp_path / "0").exists()


def test_citation_prompt_lists_child_cids(tmp_path: Path) -> None:
    members = _lazy_members()
    cid = community_id([n.id for n in members])
    child_a = community_id(["Concept:m0", "Concept:m1"])
    child_b = community_id(["Concept:m2", "Concept:m3"])
    client = _ScriptedClient(
        scripted=[
            {
                "title": "Cited",
                "description": f"Spans {child_a} and friends.",
                "tags": ["a", "b", "c", "d", "e"],
            }
        ]
    )
    summary = materialize_community_summary(
        members,
        cid=cid,
        member_ids=[n.id for n in members],
        level=1,
        cache_dir=tmp_path,
        json_client=client,
        child_cids=[child_a, child_b],
    )
    assert summary is not None and summary[0] == "Cited"
    # The prompt lists every child cid and the system prompt demands citation.
    assert child_a in client.calls[0]["user"]
    assert child_b in client.calls[0]["user"]
    assert "cite at least one" in client.calls[0]["system"]
    # Accepted output IS cached as llm-quality.
    assert level_cache_path(tmp_path, 1, cid).is_file()


def test_citation_rejection_falls_back_and_is_not_cached(tmp_path: Path) -> None:
    """§5.2: prose citing NO child community id is rejected — structural
    fallback, nothing cached, so a later visit may still retry the LLM."""
    members = _lazy_members()
    cid = community_id([n.id for n in members])
    child_cids = [community_id(["Concept:m0", "Concept:m1"])]
    uncited = {
        "title": "Vague",
        "description": "A summary that cites nothing at all.",
        "tags": ["a", "b", "c", "d", "e"],
    }
    client = _ScriptedClient(scripted=[uncited, uncited])
    for _ in range(2):  # rejection is not sticky — both visits re-attempt
        assert (
            materialize_community_summary(
                members,
                cid=cid,
                member_ids=[n.id for n in members],
                level=1,
                cache_dir=tmp_path,
                json_client=client,
                child_cids=child_cids,
            )
            is None
        )
    assert len(client.calls) == 2
    assert not level_cache_path(tmp_path, 1, cid).exists()


def test_compile_prompt_has_no_citation_section(tmp_path: Path) -> None:
    """The compile pass summarizes leaf members (child_cids empty) — its
    prompts and system message are byte-identical to the pre-refactor code."""
    client = _ScriptedClient()
    compile_community_summaries(
        _two_cluster_graph(), cache_dir=tmp_path / "cs", json_client=client, min_size=3,
    )
    for call in client.calls:
        assert "Child sub-communities" not in call["user"]
        assert "cite at least one" not in call["system"]


def test_read_warm_summary_rejects_digest_drift(tmp_path: Path) -> None:
    members = _lazy_members()
    member_ids = [n.id for n in members]
    cid = community_id(member_ids)
    materialize_community_summary(
        members,
        cid=cid,
        member_ids=member_ids,
        level=0,
        cache_dir=tmp_path,
        json_client=_ScriptedClient(),
    )
    assert read_warm_summary(tmp_path, 0, cid, members) is not None
    drifted = [
        dataclasses.replace(members[0], description="edited description")
    ] + members[1:]
    # Strict: content drift is a MISS (no stale llm-quality cards), and the
    # wrong level is a miss too (the layout is level-scoped).
    assert read_warm_summary(tmp_path, 0, cid, drifted) is None
    assert read_warm_summary(tmp_path, 1, cid, members) is None


def test_prune_recurses_into_level_subdirs(tmp_path: Path) -> None:
    cache_dir = tmp_path / "community_summaries"
    live = community_id(["a", "b"])
    dead = community_id(["x", "y"])
    _seed_cache_files(cache_dir, [live, dead])
    _seed_cache_files(cache_dir / "2", [live, dead])
    # Non-numeric subdirs are foreign — never touched.
    _seed_cache_files(cache_dir / "backup", [dead])

    deleted = prune_stale_summary_caches(cache_dir, {live})
    dead_name = f"{dead.replace(':', '_')}.json"
    assert deleted == sorted([dead_name, f"2/{dead_name}"])
    assert _cache_path(cache_dir, live).exists()
    assert _cache_path(cache_dir / "2", live).exists()
    assert _cache_path(cache_dir / "backup", dead).exists()


# ---------------------------------------------------------------------------
# Env opt-out (default-on; mirrors PR #13 / insight-symbol-link)
# ---------------------------------------------------------------------------


def test_env_unset_defaults_on() -> None:
    # Default-on: env var unset → enabled.
    assert is_enabled_via_env({}) is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", "On"])
def test_env_truthy_values_stay_enabled(value: str) -> None:
    # Explicit truthy spellings → enabled.
    assert is_enabled_via_env({"TESSERAE_COMMUNITY_SUMMARIES": value}) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "NO", "off", "Off"])
def test_env_explicit_opt_out_disables(value: str) -> None:
    # Only the four canonical opt-out spellings disable the pass.
    assert is_enabled_via_env({"TESSERAE_COMMUNITY_SUMMARIES": value}) is False


@pytest.mark.parametrize("value", ["", "   ", "\t", "\n  "])
def test_env_empty_and_whitespace_default_on(value: str) -> None:
    # Empty / whitespace → default (enabled).
    assert is_enabled_via_env({"TESSERAE_COMMUNITY_SUMMARIES": value}) is True


@pytest.mark.parametrize("value", ["maybe", "kinda", "disable", "enable", "garbage"])
def test_env_unknown_values_default_on(value: str) -> None:
    # Conservative: only explicit opt-out spellings disable.
    assert is_enabled_via_env({"TESSERAE_COMMUNITY_SUMMARIES": value}) is True


# ---------------------------------------------------------------------------
# MCP integration
# ---------------------------------------------------------------------------


def test_mcp_list_communities_ranks_by_member_count_and_filters(
    tmp_path: Path,
) -> None:
    graph = _two_cluster_graph()
    client = _ScriptedClient()
    slice_graph = compile_community_summaries(
        graph,
        cache_dir=tmp_path / "cs",
        json_client=client,
        min_size=3,
    )
    # Merge the slice back into the source graph so MCP sees both.
    union = ResearchGraph(
        nodes=graph.nodes + slice_graph.nodes,
        edges=graph.edges + slice_graph.edges,
    )

    server = LLMWikiMCPServer()
    result = server._mcp_list_communities(union, min_size=3, limit=10)
    assert result["total"] == 2
    titles = [item["title"] for item in result["communities"]]
    # All entries are non-empty strings.
    assert all(isinstance(t, str) and t for t in titles)
    for entry in result["communities"]:
        assert entry["community_id"].startswith("CommunitySummary:")
        assert entry["member_count"] == 3
        # Descent PR1 safety clamp: the unbounded member id list never
        # enters context inline — only a count plus a content-keyed handle.
        assert "member_ids" not in entry
        assert entry["member_ids_handle"].startswith("h_")
        assert len(entry["tags"]) == 5

    # min_size=5 filters every cluster out.
    empty = server._mcp_list_communities(union, min_size=5, limit=10)
    assert empty == {"communities": [], "total": 0}

    # limit=1 returns only the top-ranked entry (still 2 total clusters
    # were considered, but only 1 surfaces).
    capped = server._mcp_list_communities(union, min_size=3, limit=1)
    assert len(capped["communities"]) == 1


def test_mcp_list_communities_member_ids_handle_round_trips(
    tmp_path: Path,
) -> None:
    """The member_ids handle pages the full list back via ``get_handle``."""
    graph = _two_cluster_graph()
    client = _ScriptedClient()
    slice_graph = compile_community_summaries(
        graph,
        cache_dir=tmp_path / "cs",
        json_client=client,
        min_size=3,
    )
    union = ResearchGraph(
        nodes=graph.nodes + slice_graph.nodes,
        edges=graph.edges + slice_graph.edges,
    )

    server = LLMWikiMCPServer()
    result = server._mcp_list_communities(union, min_size=3, limit=10)
    entry = result["communities"][0]
    expected = next(
        list((n.metadata or {}).get("member_ids") or [])
        for n in union.nodes
        if n.id == entry["community_id"]
    )

    sliced = server.call_tool("get_handle", {"handle": entry["member_ids_handle"]})
    assert sliced["found"] is True and sliced["eof"] is True
    assert json.loads(sliced["slice"]) == expected

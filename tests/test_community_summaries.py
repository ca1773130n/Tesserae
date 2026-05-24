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

import json
import os
from pathlib import Path
from typing import Any, List, Optional, Union

import pytest

from tesserae.community_summaries import (
    community_id,
    compile_community_summaries,
    detect_communities,
    is_enabled_via_env,
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
        self.calls.append({"schema_name": schema_name, "cache_key": cache_key})
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
    # And every node from the cached run reports cache_hit=True.
    for node in slice_second.nodes:
        assert node.metadata.get("cache_hit") is True


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
        assert len(entry["member_ids"]) == 3
        assert len(entry["tags"]) == 5

    # min_size=5 filters every cluster out.
    empty = server._mcp_list_communities(union, min_size=5, limit=10)
    assert empty == {"communities": [], "total": 0}

    # limit=1 returns only the top-ranked entry (still 2 total clusters
    # were considered, but only 1 surfaces).
    capped = server._mcp_list_communities(union, min_size=3, limit=1)
    assert len(capped["communities"]) == 1

"""compile_context Descent additions — scope= + strategy='hierarchical' (§5.4, PR8).

Synthetic fixture in the ``test_graph_map`` style: a hand-written
``.tesserae/hierarchy.json`` sidecar (the loader trusts the PR4 pass, so tests
need not depend on Louvain partitioning a crafted graph) over a graph with two
communities bridged by one edge:

* ``A`` (3 alpha-telemetry concepts) — has an in-graph COMMUNITY_SUMMARY node,
  the coarse summary layer.
* ``B`` (3 beta-caching concepts) — no in-graph summary; its finest-level
  child ``B1`` has a WARM cache file only, the fine summary layer.

The ``a1 — b1`` bridge is the hub-explosion stand-in: default-path PPR seeded
in A leaks into B through it; ``scope=`` must kill that structurally.
All tests are CI-safe and deterministic: ``synthesize=False``, no network/LLM,
explicit :class:`HashEmbeddingBackend`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import List

import pytest

from tesserae.community_summaries import community_id
from tesserae.context_compiler import compile_context
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.retrieval.hybrid import HashEmbeddingBackend

A_MEMBERS = ["Concept:a1", "Concept:a2", "Concept:a3"]
B_MEMBERS = ["Concept:b1", "Concept:b2", "Concept:b3"]
B1_MEMBERS = ["Concept:b1", "Concept:b2"]

CID_A = community_id(A_MEMBERS)
CID_B = community_id(B_MEMBERS)
CID_B1 = community_id(B1_MEMBERS)

#: sha256 of the default-path bundle body for the fixture graph + query below,
#: captured from the pre-PR8 compile_context (commit 07870094a7, before scope=/
#: strategy= existed). The default path must stay byte-identical forever.
_PRE_DESCENT_BODY_SHA256 = (
    "5d975004a409584e02d61edf085d74191ed9bbdfd0617ea43db6d5299bf35f8f"
)


def _backend() -> HashEmbeddingBackend:
    return HashEmbeddingBackend()


def _fixture_graph() -> ResearchGraph:
    def _concept(nid: str, text: str) -> ResearchNode:
        return ResearchNode(
            id=nid,
            name=f"Node {nid.split(':')[1].upper()}",
            type=ResearchNodeType.CONCEPT,
            description=text * 4,
        )

    nodes: List[ResearchNode] = [
        _concept("Concept:a1", "The alpha telemetry pipeline samples sensors. "),
        _concept("Concept:a2", "Alpha telemetry batches sensor readings hourly. "),
        _concept("Concept:a3", "Telemetry dashboards chart the alpha stream. "),
        _concept("Concept:b1", "The beta caching layer memoizes hot lookups. "),
        _concept("Concept:b2", "Beta caching evicts entries least recently used. "),
        _concept("Concept:b3", "Cache misses in beta fall through to disk. "),
    ]
    nodes.append(
        ResearchNode(
            id=CID_A,
            name="Alpha Telemetry",
            type=ResearchNodeType.COMMUNITY_SUMMARY,
            description="Community around the alpha telemetry pipeline and its sensors.",
            metadata={
                "member_ids": list(A_MEMBERS),
                "member_count": len(A_MEMBERS),
                "tags": ["alpha", "telemetry", "sensors"],
            },
        )
    )
    edges = [
        ResearchEdge(source="Concept:a1", target="Concept:a2", type="shares_concept_with"),
        ResearchEdge(source="Concept:a2", target="Concept:a3", type="shares_concept_with"),
        ResearchEdge(source="Concept:b1", target="Concept:b2", type="shares_concept_with"),
        ResearchEdge(source="Concept:b2", target="Concept:b3", type="shares_concept_with"),
        # The cross-community bridge scope= must sever.
        ResearchEdge(source="Concept:a1", target="Concept:b1", type="references"),
    ]
    edges.extend(
        ResearchEdge(source=CID_A, target=mid, type="summarizes", metadata={"community_id": CID_A})
        for mid in A_MEMBERS
    )
    return ResearchGraph(nodes=nodes, edges=edges)


def _write_project(tmp_path: Path, *, warm_b1_cache: bool = False) -> Path:
    """Write ``.tesserae/hierarchy.json`` (+ optional B1 summary cache)."""
    tess = tmp_path / ".tesserae"
    tess.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "levels": [
            {CID_A: A_MEMBERS, CID_B1: B1_MEMBERS},  # finest: b3 is loose
            {CID_A: A_MEMBERS, CID_B: B_MEMBERS},  # coarsest
        ],
        "hubs": [],
    }
    (tess / "hierarchy.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if warm_b1_cache:
        cache_dir = tess / "community_summaries"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache = {
            "schema_version": 1,
            "community_id": CID_B1,
            "member_ids": list(B1_MEMBERS),
            "members_digest": "test-digest",
            "summary": {
                "title": "Beta Caching",
                "description": "Warm summary of the beta caching cluster.",
                "tags": ["beta", "caching"],
            },
        }
        (cache_dir / f"{CID_B1.replace(':', '_')}.json").write_text(
            json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return tmp_path


# -- default path: byte-identical, budget=0 invariant ------------------------


def test_default_path_byte_identical_to_pre_descent() -> None:
    """No scope/strategy/tame_hubs -> the exact pre-PR8 bytes."""
    bundle = compile_context(
        _fixture_graph(), project_root=None, query="alpha telemetry",
        backend=_backend(),
    )
    assert (
        hashlib.sha256(bundle.body.encode("utf-8")).hexdigest()
        == _PRE_DESCENT_BODY_SHA256
    )
    # Explicitly spelling out the new defaults is the same call.
    explicit = compile_context(
        _fixture_graph(), project_root=None, query="alpha telemetry",
        backend=_backend(), scope=None, strategy="default", tame_hubs=False,
    )
    assert explicit.body == bundle.body
    assert explicit.ranked_nodes == bundle.ranked_nodes
    assert explicit.selected_nodes == bundle.selected_nodes


def test_default_path_budget_zero_stays_uncapped() -> None:
    bundle = compile_context(
        _fixture_graph(), project_root=None, query="alpha telemetry",
        budget=0, backend=_backend(),
    )
    assert bundle.char_budget_total == 0
    assert "…[truncated]" not in bundle.body
    assert len(bundle.selected_nodes) == len(bundle.ranked_nodes)


# -- scope=<cid>: PPR restricted to the community-induced subgraph -----------


def test_scope_restricts_to_community_members(tmp_path: Path) -> None:
    root = _write_project(tmp_path)
    graph = _fixture_graph()
    # Default path: PPR seeded at a1 leaks into B through the a1—b1 bridge.
    unscoped = compile_context(
        graph, project_root=str(root), seeds=["Concept:a1"], depth=2,
        backend=_backend(),
    )
    assert any(nid.startswith("Concept:b") for nid in unscoped.ranked_nodes)
    # scope= severs the bridge structurally: nothing outside A can rank.
    scoped = compile_context(
        graph, project_root=str(root), seeds=["Concept:a1"], depth=2,
        backend=_backend(), scope=CID_A,
    )
    assert scoped.ranked_nodes
    assert set(scoped.ranked_nodes) <= set(A_MEMBERS)
    assert set(scoped.selected_nodes) <= set(A_MEMBERS)


def test_scope_drops_out_of_scope_seeds(tmp_path: Path) -> None:
    root = _write_project(tmp_path)
    bundle = compile_context(
        _fixture_graph(), project_root=str(root),
        seeds=["Concept:a1", "Concept:b1"], backend=_backend(), scope=CID_A,
    )
    assert bundle.seeds_used == ["Concept:a1"]


def test_scope_budget_zero_stays_uncapped(tmp_path: Path) -> None:
    root = _write_project(tmp_path)
    bundle = compile_context(
        _fixture_graph(), project_root=str(root), seeds=["Concept:a1"],
        depth=2, budget=0, backend=_backend(), scope=CID_A,
    )
    assert bundle.char_budget_total == 0
    assert sorted(bundle.selected_nodes) == sorted(A_MEMBERS)
    assert "…[truncated]" not in bundle.body


def test_scope_unknown_cid_fails_loud(tmp_path: Path) -> None:
    root = _write_project(tmp_path)
    with pytest.raises(ValueError, match="unknown scope"):
        compile_context(
            _fixture_graph(), project_root=str(root), seeds=["Concept:a1"],
            backend=_backend(), scope="CommunitySummary:deadbeefdeadbeef",
        )


def test_scope_requires_project_root() -> None:
    with pytest.raises(ValueError, match="project_root"):
        compile_context(
            _fixture_graph(), project_root=None, seeds=["Concept:a1"],
            backend=_backend(), scope=CID_A,
        )


def test_unknown_strategy_fails_loud() -> None:
    with pytest.raises(ValueError, match="unknown strategy"):
        compile_context(
            _fixture_graph(), project_root=None, query="alpha",
            backend=_backend(), strategy="galactic",
        )


# -- strategy='hierarchical': summary-layer seeding + branch descent ---------


def test_hierarchical_descends_matched_coarse_branch(tmp_path: Path) -> None:
    """Query matching the in-graph coarse summary lands inside that branch."""
    root = _write_project(tmp_path)
    bundle = compile_context(
        _fixture_graph(), project_root=str(root),
        query="alpha telemetry sensors", depth=1, backend=_backend(),
        strategy="hierarchical",
    )
    assert bundle.selected_nodes
    # depth=1 -> one branch; despite the a1—b1 bridge, B never ranks.
    assert set(bundle.ranked_nodes) <= set(A_MEMBERS)
    assert set(bundle.selected_nodes) <= set(A_MEMBERS)


def test_hierarchical_seeds_from_warm_fine_cache(tmp_path: Path) -> None:
    """A warm cached fine summary (no in-graph node) seeds descent into B1."""
    root = _write_project(tmp_path, warm_b1_cache=True)
    bundle = compile_context(
        _fixture_graph(), project_root=str(root),
        query="beta caching evicts entries", depth=1, backend=_backend(),
        strategy="hierarchical",
    )
    assert bundle.selected_nodes
    # The matched branch is the FINE community B1 — b3 (loose member of the
    # coarse B, outside B1) is excluded, proving the cache index was searched.
    assert set(bundle.ranked_nodes) <= set(B1_MEMBERS)
    assert set(bundle.selected_nodes) <= set(B1_MEMBERS)


def test_hierarchical_cold_cache_degrades_to_default(tmp_path: Path) -> None:
    """Without the warm B1 cache no summary matches the beta query — the
    strategy degrades to the default path rather than guessing a branch."""
    root = _write_project(tmp_path, warm_b1_cache=False)
    hierarchical = compile_context(
        _fixture_graph(), project_root=str(root),
        query="beta caching evicts entries", depth=1, backend=_backend(),
        strategy="hierarchical",
    )
    default = compile_context(
        _fixture_graph(), project_root=str(root),
        query="beta caching evicts entries", depth=1, backend=_backend(),
    )
    assert hierarchical.body == default.body
    assert hierarchical.ranked_nodes == default.ranked_nodes


def test_hierarchical_without_query_degrades_to_default(tmp_path: Path) -> None:
    root = _write_project(tmp_path, warm_b1_cache=True)
    default = compile_context(
        _fixture_graph(), project_root=str(root), seeds=["Concept:a1"],
        backend=_backend(),
    )
    hierarchical = compile_context(
        _fixture_graph(), project_root=str(root), seeds=["Concept:a1"],
        backend=_backend(), strategy="hierarchical",
    )
    assert hierarchical.body == default.body
    assert hierarchical.ranked_nodes == default.ranked_nodes


def test_hierarchical_respects_explicit_scope(tmp_path: Path) -> None:
    """scope= is the harder contract: branches outside it cannot widen it."""
    root = _write_project(tmp_path, warm_b1_cache=True)
    bundle = compile_context(
        _fixture_graph(), project_root=str(root),
        query="beta caching evicts entries", depth=1, backend=_backend(),
        strategy="hierarchical", scope=CID_A,
    )
    assert set(bundle.ranked_nodes) <= set(A_MEMBERS)


# -- tame_hubs wiring (flag-gated, default OFF) ------------------------------


def test_tame_hubs_flag_smoke(tmp_path: Path) -> None:
    """tame_hubs=True completes deterministically with and without a sidecar."""
    root = _write_project(tmp_path)
    with_sidecar = compile_context(
        _fixture_graph(), project_root=str(root), seeds=["Concept:a1"],
        backend=_backend(), tame_hubs=True,
    )
    again = compile_context(
        _fixture_graph(), project_root=str(root), seeds=["Concept:a1"],
        backend=_backend(), tame_hubs=True,
    )
    assert with_sidecar.body == again.body
    # Missing sidecar degrades to the PR1 fanout scan — never raises.
    no_sidecar = compile_context(
        _fixture_graph(), project_root=str(tmp_path / "elsewhere"),
        seeds=["Concept:a1"], backend=_backend(), tame_hubs=True,
    )
    assert no_sidecar.ranked_nodes

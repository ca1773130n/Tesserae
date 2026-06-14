"""Tests for the cross-session Runbook/Gotcha distillation pass.

No network / LLM: the LLM path is exercised via an in-process stub client.
Covers clustering determinism, Runbook vs Gotcha classification, node +
``derived_from`` minting, the deterministic fallback (no client), the stub-LLM
title/body override, byte-idempotent rerun, env+config enable resolution, and
the below-``min_cluster_size`` no-op.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.memory.distill import (
    DERIVED_FROM_EDGE,
    distillation_enabled,
    run_distillation_pass,
    set_distillation_test_client,
)
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _finding(
    node_id: str,
    name: str,
    *,
    node_type: ResearchNodeType = ResearchNodeType.SESSION_INSIGHT,
    session_id: str = "s1",
    first_seen_at: str | None = None,
    description: str = "",
) -> ResearchNode:
    meta: dict = {"session_id": session_id}
    if first_seen_at:
        meta["first_seen_at"] = first_seen_at
    return ResearchNode(
        id=node_id,
        name=name,
        type=node_type,
        description=description,
        metadata=meta,
    )


def _two_cluster_graph() -> ResearchGraph:
    """Two clusters: a procedure-ish one and a pitfall-ish one.

    Cluster A (procedure-ish): deploy/release runbook steps — high name
    overlap, no pitfall keywords -> Runbook.
    Cluster B (pitfall-ish): a recurring build failure -> Gotcha.
    """
    nodes = [
        # Cluster A — procedure-ish (Runbook). Shared tokens "deploy release steps".
        _finding(
            "a1",
            "deploy release steps run migration then restart",
            session_id="s1",
            first_seen_at="2026-06-01T10:00:00Z",
        ),
        _finding(
            "a2",
            "deploy release steps run migration then verify",
            session_id="s2",
            first_seen_at="2026-06-02T10:00:00Z",
        ),
        # Cluster B — pitfall-ish (Gotcha). Shared tokens + "error broke".
        _finding(
            "b1",
            "build error broke the docker cache layer rebuild",
            session_id="s1",
            first_seen_at="2026-06-03T10:00:00Z",
        ),
        _finding(
            "b2",
            "build error broke the docker cache layer again",
            session_id="s3",
            first_seen_at="2026-06-04T10:00:00Z",
        ),
    ]
    return ResearchGraph(nodes=list(nodes), edges=[])


class _StubClient:
    """Returns a canned title/body so we can assert the LLM override path."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, **kwargs):  # noqa: ANN003
        self.calls += 1
        return {"title": "STUB TITLE", "body": "STUB BODY"}

    def complete_text(self, **kwargs):  # noqa: ANN003
        return None


@pytest.fixture(autouse=True)
def _clear_test_client():
    set_distillation_test_client(None)
    yield
    set_distillation_test_client(None)


# ---------------------------------------------------------------------------
# Classification + minting (deterministic fallback)
# ---------------------------------------------------------------------------


def test_mints_runbook_and_gotcha_with_derived_from_edges(tmp_path: Path):
    graph = _two_cluster_graph()
    out = run_distillation_pass(graph, json_client=None, cache_dir=tmp_path)

    minted = [n for n in out.nodes if n.id.startswith(("Runbook:", "Gotcha:"))]
    kinds = {n.type for n in minted}
    assert ResearchNodeType.RUNBOOK in kinds
    assert ResearchNodeType.GOTCHA in kinds
    assert len(minted) == 2

    # Each distilled node has derived_from edges to exactly its 2 members.
    for node in minted:
        members = sorted(node.metadata["member_ids"])
        edges = sorted(
            e.target
            for e in out.edges
            if e.type == DERIVED_FROM_EDGE and e.source == node.id
        )
        assert edges == members
        assert len(members) == 2

    # The Gotcha cluster is the build-error one; Runbook is the deploy one.
    gotcha = next(n for n in minted if n.type is ResearchNodeType.GOTCHA)
    assert set(gotcha.metadata["member_ids"]) == {"b1", "b2"}
    runbook = next(n for n in minted if n.type is ResearchNodeType.RUNBOOK)
    assert set(runbook.metadata["member_ids"]) == {"a1", "a2"}


def test_first_seen_at_is_earliest_member(tmp_path: Path):
    graph = _two_cluster_graph()
    out = run_distillation_pass(graph, json_client=None, cache_dir=tmp_path)
    runbook = next(
        n for n in out.nodes if n.type is ResearchNodeType.RUNBOOK
    )
    # Earliest of a1 (06-01) and a2 (06-02).
    assert runbook.metadata["first_seen_at"] == "2026-06-01T10:00:00Z"


def test_deterministic_fallback_title_lists_members(tmp_path: Path):
    graph = _two_cluster_graph()
    out = run_distillation_pass(graph, json_client=None, cache_dir=tmp_path)
    runbook = next(n for n in out.nodes if n.type is ResearchNodeType.RUNBOOK)
    assert runbook.name.startswith("Runbook:")
    assert "deploy release steps" in runbook.description
    assert runbook.description.count("- ") == 2


def test_preserves_existing_nodes_and_edges(tmp_path: Path):
    graph = _two_cluster_graph()
    n_before = len(graph.nodes)
    extra = ResearchEdge(source="a1", target="a2", type="references")
    graph.edges.append(extra)
    out = run_distillation_pass(graph, json_client=None, cache_dir=tmp_path)
    assert all(orig in out.nodes for orig in graph.nodes[:n_before])
    assert extra in out.edges


# ---------------------------------------------------------------------------
# LLM stub override
# ---------------------------------------------------------------------------


def test_stub_client_overrides_title_and_body(tmp_path: Path):
    graph = _two_cluster_graph()
    client = _StubClient()
    out = run_distillation_pass(graph, json_client=client, cache_dir=tmp_path)
    minted = [n for n in out.nodes if n.id.startswith(("Runbook:", "Gotcha:"))]
    assert minted
    for node in minted:
        assert node.name == "STUB TITLE"
        assert node.description == "STUB BODY"
    assert client.calls == 2  # one per cluster
    # Cache files were written.
    assert list(tmp_path.glob("*.json"))


def test_llm_failure_falls_back_deterministically(tmp_path: Path):
    class _Boom:
        def complete_json(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("nope")

        def complete_text(self, **kwargs):  # noqa: ANN003
            return None

    graph = _two_cluster_graph()
    out = run_distillation_pass(graph, json_client=_Boom(), cache_dir=tmp_path)
    runbook = next(n for n in out.nodes if n.type is ResearchNodeType.RUNBOOK)
    assert runbook.name.startswith("Runbook:")  # deterministic fallback used


# ---------------------------------------------------------------------------
# Byte-idempotence
# ---------------------------------------------------------------------------


def test_rerun_is_byte_identical(tmp_path: Path):
    g1 = run_distillation_pass(
        _two_cluster_graph(), json_client=None, cache_dir=tmp_path
    )
    g2 = run_distillation_pass(
        _two_cluster_graph(), json_client=None, cache_dir=tmp_path
    )
    assert g1.to_json(sort_keys=True) == g2.to_json(sort_keys=True)


def test_idempotent_on_already_distilled_graph(tmp_path: Path):
    graph = _two_cluster_graph()
    once = run_distillation_pass(graph, json_client=None, cache_dir=tmp_path)
    n_nodes, n_edges = len(once.nodes), len(once.edges)
    # Re-running on the SAME (already-distilled) graph mints nothing new.
    twice = run_distillation_pass(once, json_client=None, cache_dir=tmp_path)
    assert len(twice.nodes) == n_nodes
    assert len(twice.edges) == n_edges


# ---------------------------------------------------------------------------
# min_cluster_size + layers gating
# ---------------------------------------------------------------------------


def test_below_min_cluster_size_mints_nothing(tmp_path: Path):
    # A single isolated finding — no cluster reaches size 2.
    graph = ResearchGraph(
        nodes=[_finding("solo", "a totally unique standalone observation")],
        edges=[],
    )
    out = run_distillation_pass(graph, json_client=None, cache_dir=tmp_path)
    assert not [n for n in out.nodes if n.id.startswith(("Runbook:", "Gotcha:"))]


def test_min_cluster_size_three_skips_pairs(tmp_path: Path):
    graph = _two_cluster_graph()
    out = run_distillation_pass(
        graph, json_client=None, cache_dir=tmp_path, min_cluster_size=3
    )
    assert not [n for n in out.nodes if n.id.startswith(("Runbook:", "Gotcha:"))]


def test_layers_filter_only_mints_requested_kind(tmp_path: Path):
    graph = _two_cluster_graph()
    out = run_distillation_pass(
        graph, json_client=None, cache_dir=tmp_path, layers=("gotcha",)
    )
    minted = [n for n in out.nodes if n.id.startswith(("Runbook:", "Gotcha:"))]
    assert minted
    assert all(n.type is ResearchNodeType.GOTCHA for n in minted)


def test_supersedes_edge_unions_into_one_cluster(tmp_path: Path):
    # Two findings with low name overlap but linked by a supersedes edge; from
    # two DISTINCT sessions so the cross-session (min_sessions=2) gate is met.
    nodes = [
        _finding("x1", "alpha note about config flag handling", session_id="s1"),
        _finding("x2", "beta different wording entirely here now", session_id="s2"),
    ]
    graph = ResearchGraph(
        nodes=nodes,
        edges=[ResearchEdge(source="x2", target="x1", type="supersedes")],
    )
    out = run_distillation_pass(graph, json_client=None, cache_dir=tmp_path)
    minted = [n for n in out.nodes if n.id.startswith(("Runbook:", "Gotcha:"))]
    assert len(minted) == 1
    assert set(minted[0].metadata["member_ids"]) == {"x1", "x2"}


def test_single_session_cluster_is_not_distilled(tmp_path: Path):
    """A cluster confined to ONE session is not a cross-session Runbook/Gotcha
    by default (min_sessions=2) — but min_sessions=1 opts back in."""
    nodes = [
        _finding("a1", "deploy step run the database migration first", session_id="s1"),
        _finding("a2", "deploy step run the database migration again", session_id="s1"),
    ]
    graph = ResearchGraph(nodes=nodes, edges=[
        ResearchEdge(source="a2", target="a1", type="supersedes"),
    ])
    out = run_distillation_pass(graph, json_client=None, cache_dir=tmp_path)
    assert not [n for n in out.nodes if n.id.startswith(("Runbook:", "Gotcha:"))]

    out1 = run_distillation_pass(
        ResearchGraph(nodes=list(nodes), edges=[
            ResearchEdge(source="a2", target="a1", type="supersedes"),
        ]),
        json_client=None, cache_dir=tmp_path, min_sessions=1,
    )
    assert len([n for n in out1.nodes if n.id.startswith(("Runbook:", "Gotcha:"))]) == 1


def test_distilled_node_inherits_event_provenance(tmp_path: Path):
    """A distilled node gets derived_from edges to the Event nodes its member
    findings derive from (event provenance flows up)."""
    from tesserae.research_graph import ResearchNodeType

    nodes = [
        _finding("f1", "deploy step run the database migration first", session_id="s1"),
        _finding("f2", "deploy step run the database migration again", session_id="s2"),
        ResearchNode(id="ev1", name="Event 1", type=ResearchNodeType.EVENT,
                     description="ran migration", metadata={"session_id": "s1"}),
    ]
    graph = ResearchGraph(nodes=nodes, edges=[
        ResearchEdge(source="f2", target="f1", type="supersedes"),
        ResearchEdge(source="f1", target="ev1", type="derived_from"),
    ])
    out = run_distillation_pass(graph, json_client=None, cache_dir=tmp_path)
    distilled = [n for n in out.nodes if n.id.startswith(("Runbook:", "Gotcha:"))]
    assert len(distilled) == 1
    did = distilled[0].id
    assert any(
        e.source == did and e.target == "ev1" and e.type == "derived_from"
        for e in out.edges
    ), "distilled node must inherit derived_from edge to the Event"


# ---------------------------------------------------------------------------
# distillation_enabled — env + config resolution
# ---------------------------------------------------------------------------


def test_enabled_default_off():
    assert distillation_enabled(cfg=None, env={}) is False
    assert distillation_enabled(cfg={}, env={}) is False


def test_enabled_via_config():
    assert distillation_enabled(cfg={"distillation": {"enabled": True}}, env={})
    assert (
        distillation_enabled(cfg={"distillation": {"enabled": False}}, env={})
        is False
    )


def test_enabled_via_env_overrides():
    # Env truthy enables even when config is absent.
    assert distillation_enabled(
        cfg=None, env={"TESSERAE_RUNBOOK_DISTILLATION": "1"}
    )
    # Env falsy disables even when config says enabled.
    assert (
        distillation_enabled(
            cfg={"distillation": {"enabled": True}},
            env={"TESSERAE_RUNBOOK_DISTILLATION": "off"},
        )
        is False
    )


def test_enabled_env_falsy_spellings():
    for val in ("0", "false", "no", "off", "FALSE", " Off "):
        assert (
            distillation_enabled(cfg=None, env={"TESSERAE_RUNBOOK_DISTILLATION": val})
            is False
        ), val

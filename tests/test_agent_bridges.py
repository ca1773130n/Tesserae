"""Tests for Phase 5 — opt-in cross-agent semantic bridges (spec §8.3-step-5/§12).

``resolve_agent_view(..., bridges=True)`` threads the manager/org federation
through :func:`tesserae.federation.add_semantic_links` so RELATED (not identical)
distillates from DIFFERENT reports get ``shares_concept_with`` edges — with agent
keys as the federation aliases. The contract under test:

- default ``bridges=False`` is byte-identical to Phase-4 behavior (no bridge
  edges, no ``bridges`` info key);
- ``bridges=True`` adds ONLY edges — node counts are unchanged (bridges never
  fuse nodes);
- the view cache key encodes the flag, so an ON and an OFF resolution over the
  same on-disk inputs never serve each other's graph;
- honest degradation — with the hash stub (no real model) bridges are skipped
  and ``info['bridges']['semantic_skipped']`` says so, rather than silently
  dropping the request.

The embedding backend is always the deterministic ``_FakeBackend`` stub from the
federation tests' pattern — no model2vec, no real embeddings, no LLM calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.agent_identity import AgentRegistry
from tesserae.agent_distill import agent_artifact_path
from tesserae.agent_view import resolve_agent_view
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType

MANAGER = "claude-code:me:manager"
CHILD_A = "claude-code:me:reviewer"
CHILD_B = "codex:you:planner"


class _FakeBackend:
    """Deterministic orthogonal-unit embeddings keyed on content (no model2vec).

    Mirrors tests/test_federation.py::_FakeBackend so the bridge path here uses
    the same honest-stub contract add_semantic_links is designed against.
    """

    name = "fake-test"

    def embed(self, texts):
        out = []
        for t in texts:
            tl = t.lower()
            if "pagerank" in tl or "ppr" in tl:
                out.append([1.0, 0.0, 0.0])
            elif "banana" in tl:
                out.append([0.0, 1.0, 0.0])
            else:
                out.append([0.0, 0.0, 1.0])
        return out


def _concept_artifact(nodes):
    return ResearchGraph(
        nodes=[
            ResearchNode(id=i, name=n, type=ResearchNodeType.CONCEPT, description=d)
            for i, n, d in nodes
        ],
        edges=[],
    )


def _write_artifact(project: Path, agent: str, graph: ResearchGraph) -> None:
    path = agent_artifact_path(project, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(graph.to_json(indent=2), encoding="utf-8")


def _org(tmp_path: Path):
    """A manager over two reports whose L1 artifacts each carry a related concept.

    CHILD_A knows "Personalized PageRank"; CHILD_B knows "PPR algorithm" — under
    the fake backend both embed to the PPR axis, so a cross-agent bridge is
    expected when (and only when) bridges are on. A "Banana" note on each side
    provides a node that must NOT bridge (different axis).
    """
    project = tmp_path / "proj"
    (project / ".tesserae").mkdir(parents=True, exist_ok=True)
    l0 = _concept_artifact([("Concept:root", "Project", "root")])
    (project / ".tesserae" / "graph.json").write_text(l0.to_json(indent=2), encoding="utf-8")

    registry = AgentRegistry.for_project(project)
    data = registry.load()
    agents = data.setdefault("agents", {})
    agents[MANAGER] = {"label": "Manager", "parent": "org:root", "aliases": [], "match": []}
    agents[CHILD_A] = {"label": "Reviewer", "parent": MANAGER, "aliases": [], "match": []}
    agents[CHILD_B] = {"label": "Planner", "parent": MANAGER, "aliases": [], "match": []}
    registry.save(data)

    _write_artifact(project, CHILD_A, _concept_artifact([
        ("Concept:ppr:a", "Personalized PageRank", "ranking"),
        ("Concept:fruit:a", "Banana", "fruit"),
    ]))
    _write_artifact(project, CHILD_B, _concept_artifact([
        ("Concept:ppr:b", "PPR algorithm", "pagerank ranking"),
        ("Concept:fruit:b", "Banana", "fruit"),
    ]))
    return project, l0


def _bridge_edges(graph: ResearchGraph):
    return sorted(
        (e.source, e.target) for e in graph.edges if e.type == "shares_concept_with"
    )


# --------------------------------------------------------------------------- default OFF


def test_bridges_default_off_adds_no_semantic_edges(tmp_path: Path) -> None:
    project, l0 = _org(tmp_path)
    view, info = resolve_agent_view(project, MANAGER, l0)
    assert info["mode"] == "manager"
    assert _bridge_edges(view) == []
    # Off path never advertises a bridges result (byte-identical to Phase 4).
    assert "bridges" not in info


def test_bridges_default_off_backend_never_consulted(tmp_path: Path) -> None:
    """The default path must not touch the embedding backend at all."""
    project, l0 = _org(tmp_path)

    class _BoomBackend:
        name = "boom"

        def embed(self, texts):  # pragma: no cover - must never run
            raise AssertionError("embedding backend consulted on the OFF path")

    view, _ = resolve_agent_view(project, MANAGER, l0, bridge_backend=_BoomBackend())
    assert _bridge_edges(view) == []


# --------------------------------------------------------------------------- ON adds only edges


def test_bridges_on_adds_edges_only_never_fuses_nodes(tmp_path: Path) -> None:
    project, l0 = _org(tmp_path)
    off, _ = resolve_agent_view(project, MANAGER, l0)
    on, info = resolve_agent_view(project, MANAGER, l0, bridges=True, bridge_backend=_FakeBackend())

    # Related cross-agent PPR concepts get bridged; same-axis banana does not
    # (banana is cross-agent but the two banana nodes ARE similar to each other —
    # so they bridge too; assert the PPR bridge specifically is present).
    edges = _bridge_edges(on)
    assert ("claude-code:me:reviewer::Concept:ppr:a",
            "codex:you:planner::Concept:ppr:b") in edges

    # NEVER fuse: bridges are edges, so node count is unchanged by the flag.
    assert len(on.nodes) == len(off.nodes)
    assert {n.id for n in on.nodes} == {n.id for n in off.nodes}
    # ON is strictly a superset of edges over OFF.
    assert len(on.edges) > len(off.edges)
    assert info["bridges"]["semantic_added"] >= 1
    assert info["bridges"]["semantic_backend"] == "fake-test"


def test_bridges_only_cross_agent_never_same_agent(tmp_path: Path) -> None:
    project, l0 = _org(tmp_path)
    on, _ = resolve_agent_view(project, MANAGER, l0, bridges=True, bridge_backend=_FakeBackend())
    for source, target in _bridge_edges(on):
        alias_s = source.split("::", 1)[0]
        alias_t = target.split("::", 1)[0]
        assert alias_s != alias_t, f"same-agent bridge: {source} -> {target}"


def test_bridges_work_on_org_view(tmp_path: Path) -> None:
    project, l0 = _org(tmp_path)
    off, _ = resolve_agent_view(project, "org", l0)
    on, info = resolve_agent_view(project, "org", l0, bridges=True, bridge_backend=_FakeBackend())
    assert info["mode"] == "org"
    assert len(on.nodes) == len(off.nodes)
    assert _bridge_edges(on) and not _bridge_edges(off)


# --------------------------------------------------------------------------- cache separation


def test_view_cache_keeps_on_and_off_views_separate(tmp_path: Path) -> None:
    """The flag is in the cache key: an OFF call warms the cache, a following ON
    call over identical on-disk inputs must NOT be served the OFF (bridge-free)
    graph, and vice versa."""
    project, l0 = _org(tmp_path)

    off1, _ = resolve_agent_view(project, MANAGER, l0)
    on1, _ = resolve_agent_view(project, MANAGER, l0, bridges=True, bridge_backend=_FakeBackend())
    off2, _ = resolve_agent_view(project, MANAGER, l0)

    assert _bridge_edges(off1) == []
    assert _bridge_edges(on1)  # ON did not get served the warm OFF entry
    assert _bridge_edges(off2) == []  # OFF did not get served the warm ON entry


def test_view_cache_serves_repeat_on_call(tmp_path: Path) -> None:
    project, l0 = _org(tmp_path)
    first, _ = resolve_agent_view(project, MANAGER, l0, bridges=True, bridge_backend=_FakeBackend())
    again, _ = resolve_agent_view(project, MANAGER, l0, bridges=True, bridge_backend=_FakeBackend())
    assert again is first  # same object -> served from the (flag-aware) view cache


# --------------------------------------------------------------------------- honest degradation


def test_bridges_skipped_on_hash_stub_is_surfaced(tmp_path: Path) -> None:
    """No real embedding backend -> bridges are skipped, but the request is
    surfaced (requested-but-skipped), not silently dropped; the graph is
    otherwise identical to the OFF view."""
    from tesserae.retrieval.hybrid import HashEmbeddingBackend

    project, l0 = _org(tmp_path)
    off, _ = resolve_agent_view(project, MANAGER, l0)
    on, info = resolve_agent_view(
        project, MANAGER, l0, bridges=True, bridge_backend=HashEmbeddingBackend()
    )
    assert info["bridges"]["semantic_added"] == 0
    assert "semantic_skipped" in info["bridges"]
    assert _bridge_edges(on) == []
    assert len(on.nodes) == len(off.nodes)
    assert len(on.edges) == len(off.edges)


# --------------------------------------------------------------------------- worker no-op


def test_bridges_are_noop_on_worker_view(tmp_path: Path) -> None:
    """A worker view does not federate, so the flag is a harmless no-op there."""
    project, l0 = _org(tmp_path)
    # CHILD_A is a leaf (its own children set is empty) -> worker mode.
    view, info = resolve_agent_view(project, CHILD_A, l0, bridges=True, bridge_backend=_FakeBackend())
    assert info["mode"] == "worker"
    assert _bridge_edges(view) == []
    assert "bridges" not in info

"""Tests for the pure on-demand context compiler (CTX-01 / CTX-04).

All tests are CI-safe and deterministic: ``synthesize=False`` everywhere, no
network/LLM, and an explicit :class:`HashEmbeddingBackend` so hybrid-search
results are isolation-stable. ``project_root=None`` so node bodies come from
``node.description`` (no wiki layer needed).
"""

from __future__ import annotations

from typing import List

from tesserae.context_compiler import (
    ContextBundle,
    ContextCitation,
    compile_context,
)
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.retrieval.hybrid import HashEmbeddingBackend


def _backend() -> HashEmbeddingBackend:
    return HashEmbeddingBackend()


def _connected_graph() -> ResearchGraph:
    """A small connected graph with sizeable, distinct bodies."""
    nodes: List[ResearchNode] = [
        ResearchNode(
            id="splat",
            name="Gaussian Splatting",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            description="Gaussian splatting renders radiance fields with "
            "anisotropic 3D gaussians. " * 8,
        ),
        ResearchNode(
            id="nerf",
            name="Neural Radiance Fields",
            type=ResearchNodeType.PAPER,
            description="NeRF represents a scene as a continuous volumetric "
            "function queried by a neural network. " * 8,
        ),
        ResearchNode(
            id="claim",
            name="Realtime Rendering Claim",
            type=ResearchNodeType.PERFORMANCE_CLAIM,
            description="Achieves realtime framerates at high resolution "
            "on commodity GPUs. " * 8,
        ),
        ResearchNode(
            id="okapi",
            name="Okapi BM25",
            type=ResearchNodeType.CONCEPT,
            description="BM25 is a bag-of-words ranking function used in "
            "lexical information retrieval. " * 8,
        ),
    ]
    edges = [
        ResearchEdge(source="splat", target="nerf", type="references"),
        ResearchEdge(source="splat", target="claim", type="supports_claim"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _disconnected_graph() -> ResearchGraph:
    """Nodes with NO edges -> PPR returns [] for any seed."""
    nodes = [
        ResearchNode(
            id="lonely",
            name="Gaussian Splatting",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            description="Isolated gaussian splatting concept with no edges.",
        ),
        ResearchNode(
            id="lonely2",
            name="Diffusion Models",
            type=ResearchNodeType.CONCEPT,
            description="Isolated diffusion concept with no edges.",
        ),
    ]
    return ResearchGraph(nodes=nodes, edges=[])


def test_bundle_shape() -> None:
    graph = _connected_graph()
    bundle = compile_context(
        graph, project_root=None, query="gaussian splatting", backend=_backend()
    )
    assert isinstance(bundle, ContextBundle)
    assert bundle.body.startswith("# Context:")
    assert "## Citations" in bundle.body
    assert isinstance(bundle.citations, list)
    assert all(isinstance(c, ContextCitation) for c in bundle.citations)


def test_citation_integrity() -> None:
    graph = _connected_graph()
    bundle = compile_context(
        graph, project_root=None, query="gaussian splatting", backend=_backend()
    )
    node_ids = {n.id for n in graph.nodes}
    assert bundle.citations  # non-empty
    assert all(c.node_id in node_ids for c in bundle.citations)
    assert len(bundle.citations) == len(bundle.selected_nodes)
    # Every citation node_id is also surfaced in the rendered citation block.
    for c in bundle.citations:
        assert f"node_id={c.node_id}" in bundle.body


def test_deterministic_no_llm() -> None:
    graph = _connected_graph()
    b1 = compile_context(
        graph, project_root=None, query="gaussian splatting",
        depth=2, budget=8000, synthesize=False, backend=_backend(),
    )
    b2 = compile_context(
        graph, project_root=None, query="gaussian splatting",
        depth=2, budget=8000, synthesize=False, backend=_backend(),
    )
    assert b1.body == b2.body  # byte-identical: no timestamp embedded
    assert b1.selected_nodes == b2.selected_nodes
    assert b1.ranked_nodes == b2.ranked_nodes


def test_budget_bound() -> None:
    graph = _connected_graph()
    small = compile_context(
        graph, project_root=None, query="gaussian splatting",
        budget=200, backend=_backend(),
    )
    assert small.char_budget_used <= 200
    # Total bodies far exceed 200 chars, so selection must be bounded.
    assert len(small.selected_nodes) < len(graph.nodes)

    uncapped = compile_context(
        graph, project_root=None, query="gaussian splatting",
        budget=0, backend=_backend(),
    )
    # budget<=0 -> uncapped: selects everything reachable from the seeds.
    assert len(uncapped.selected_nodes) >= len(small.selected_nodes)
    assert len(uncapped.selected_nodes) == len(uncapped.ranked_nodes)


def test_ppr_fallback() -> None:
    graph = _disconnected_graph()
    bundle = compile_context(
        graph, project_root=None, query="gaussian splatting", backend=_backend()
    )
    # PPR returns [] for edge-less seeds -> fall back to hybrid/seed ids.
    assert bundle.ranked_nodes
    assert bundle.citations
    node_ids = {n.id for n in graph.nodes}
    assert all(c.node_id in node_ids for c in bundle.citations)


def test_explicit_seeds() -> None:
    graph = _connected_graph()
    bundle = compile_context(
        graph, project_root=None, query="", seeds=["okapi"], backend=_backend()
    )
    assert "okapi" in bundle.seeds_used
    assert bundle.selected_nodes  # non-empty
    assert bundle.citations


def _two_hop_graph() -> ResearchGraph:
    """A->B->C chain: ``C`` is only reachable from ``A`` in 2 hops."""
    nodes = [
        ResearchNode(
            id="a",
            name="Gaussian Splatting",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            description="Seed node A about gaussian splatting. " * 8,
        ),
        ResearchNode(
            id="b",
            name="Neural Radiance Fields",
            type=ResearchNodeType.PAPER,
            description="One hop from A. " * 8,
        ),
        ResearchNode(
            id="c",
            name="Two Hop Only",
            type=ResearchNodeType.CONCEPT,
            description="Two hops from A; unreachable at depth 1. " * 8,
        ),
    ]
    edges = [
        ResearchEdge(source="a", target="b", type="references"),
        ResearchEdge(source="b", target="c", type="references"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_depth_bounds_hop_distance() -> None:
    """depth must bound hop-distance, not just scale top_k (codex major)."""
    graph = _two_hop_graph()
    shallow = compile_context(
        graph, project_root=None, query="", seeds=["a"],
        depth=1, budget=0, backend=_backend(),
    )
    deep = compile_context(
        graph, project_root=None, query="", seeds=["a"],
        depth=2, budget=0, backend=_backend(),
    )
    # Node "c" is only reachable from "a" in 2 hops.
    assert "c" not in shallow.ranked_nodes
    assert "c" not in shallow.selected_nodes
    assert "c" in deep.ranked_nodes
    # Determinism preserved.
    again = compile_context(
        graph, project_root=None, query="", seeds=["a"],
        depth=1, budget=0, backend=_backend(),
    )
    assert again.ranked_nodes == shallow.ranked_nodes


def _depth_window_graph() -> ResearchGraph:
    """Seed with one in-depth (1-hop) neighbour + many out-of-depth high-PPR nodes.

    Topology:
        seed --references--> indepth                 (1 hop from seed)
        hubN --references--> seed  (12 hub nodes, each also cross-linked to a
                                    dense ``core`` clique so they accrue high PPR
                                    mass) -- all are 1 hop, BUT we put them OUTSIDE
                                    the depth window by routing through ``mid``.

    To force the underfill we instead make the high-PPR nodes reachable only at
    2 hops while the legitimate in-depth node sits at 1 hop. PPR over the full
    graph ranks the densely-connected 2-hop cluster above the lone 1-hop node;
    with depth=1 and the OLD (cap-before-filter) logic those out-of-depth nodes
    consume the top_k window and ``indepth`` is dropped.
    """
    nodes: List[ResearchNode] = [
        ResearchNode(
            id="seed",
            name="Gaussian Splatting",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            description="Seed about gaussian splatting. " * 8,
        ),
        ResearchNode(
            id="indepth",
            name="Neural Radiance Fields",
            type=ResearchNodeType.PAPER,
            description="One hop from the seed; must survive depth=1. " * 8,
        ),
        ResearchNode(
            id="mid",
            name="Bridge Node",
            type=ResearchNodeType.CONCEPT,
            description="A bridge one hop from the seed into the dense cluster. " * 8,
        ),
    ]
    edges = [
        ResearchEdge(source="seed", target="indepth", type="references"),
        ResearchEdge(source="seed", target="mid", type="references"),
    ]
    # Dense 2-hop cluster behind ``mid``: many mutually-connected hubs that
    # accrue high PPR mass and are exactly 2 hops from the seed.
    hub_ids = [f"hub{i}" for i in range(15)]
    for hid in hub_ids:
        nodes.append(
            ResearchNode(
                id=hid,
                name=f"Hub {hid}",
                type=ResearchNodeType.CONCEPT,
                description=f"Densely connected hub {hid}. " * 8,
            )
        )
        edges.append(ResearchEdge(source="mid", target=hid, type="references"))
    # Cross-link the hubs into a clique so they hold high PPR mass.
    for i, a in enumerate(hub_ids):
        for b in hub_ids[i + 1 :]:
            edges.append(ResearchEdge(source=a, target=b, type="references"))
    return ResearchGraph(nodes=nodes, edges=edges)


def test_depth_filter_runs_before_top_k_cap() -> None:
    """In-depth nodes survive the cap even amid many out-of-depth high-PPR nodes.

    With depth=1 only ``seed``, ``indepth`` and ``mid`` are in-window. The dense
    2-hop hub cluster ranks higher in full-graph PPR; the OLD logic (cap BEFORE
    depth filter) let those hubs consume the top_k window, dropping ``indepth``.
    The fix filters to the depth set BEFORE capping, so ``indepth`` is kept.
    """
    graph = _depth_window_graph()
    shallow = compile_context(
        graph, project_root=None, query="", seeds=["seed"],
        depth=1, budget=0, backend=_backend(),
    )
    # In-depth nodes must be present; out-of-depth hubs must NOT leak in.
    assert "indepth" in shallow.ranked_nodes
    assert "indepth" in shallow.selected_nodes
    assert all(not nid.startswith("hub") for nid in shallow.ranked_nodes)
    # Determinism preserved.
    again = compile_context(
        graph, project_root=None, query="", seeds=["seed"],
        depth=1, budget=0, backend=_backend(),
    )
    assert again.ranked_nodes == shallow.ranked_nodes


def test_first_body_over_budget_still_returns_one_node() -> None:
    """A budget smaller than the first body returns 1 truncated cited node, not zero."""
    graph = _connected_graph()
    bundle = compile_context(
        graph, project_root=None, query="gaussian splatting",
        budget=10, backend=_backend(),
    )
    # Without the fix this would select ZERO nodes (first body overflows).
    assert len(bundle.selected_nodes) == 1
    assert len(bundle.citations) == 1
    # Budget pressure is reported: the truncated body never exceeds the budget.
    assert bundle.char_budget_used <= 10
    assert "## Citations" in bundle.body


def test_first_body_truncation_has_word_boundary_marker() -> None:
    """Over-budget first body truncates at a boundary + appends a marker (codex nit).

    With a budget large enough to fit the marker, the truncated body must end
    with the marker and stay within budget; the cut lands on a word/newline
    boundary (no mid-word slice).
    """
    graph = _connected_graph()
    budget = 120
    bundle = compile_context(
        graph, project_root=None, query="gaussian splatting",
        budget=budget, backend=_backend(),
    )
    assert len(bundle.selected_nodes) == 1
    # The selected body fits the budget AND carries the truncation marker.
    assert bundle.char_budget_used <= budget
    assert "…[truncated]" in bundle.body
    # The body section before the marker did not slice through a word: the char
    # immediately before the marker is whitespace-trimmed (boundary cut).
    section = bundle.body.split("…[truncated]")[0]
    assert not section.endswith(" ")  # cut on the boundary, trailing space removed
    # Determinism preserved across runs.
    again = compile_context(
        graph, project_root=None, query="gaussian splatting",
        budget=budget, backend=_backend(),
    )
    assert again.body == bundle.body


def test_synthesize_without_key_falls_back(monkeypatch) -> None:
    """synthesize=True with NO API key returns the deterministic body (no raise)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    graph = _connected_graph()
    det = compile_context(
        graph, project_root=None, query="gaussian splatting",
        budget=8000, synthesize=False, backend=_backend(),
    )
    syn = compile_context(
        graph, project_root=None, query="gaussian splatting",
        budget=8000, synthesize=True, backend=_backend(),
    )
    # No exception, synthesis disabled, deterministic body preserved.
    assert syn.synthesized is False
    assert syn.body == det.body


def test_empty_query_no_seeds_is_valid() -> None:
    graph = _connected_graph()
    bundle = compile_context(graph, project_root=None, query="", backend=_backend())
    assert isinstance(bundle, ContextBundle)
    assert bundle.body.startswith("# Context:")
    assert bundle.char_budget_used == 0
    assert bundle.citations == []


def _dated(nid: str, name: str, date: str) -> ResearchNode:
    return ResearchNode(
        id=nid, name=name, type=ResearchNodeType.SESSION_INSIGHT,
        description=(name + ". ") * 20, metadata={"first_seen_at": date},
    )


def test_recency_blend_promotes_recent_over_old():
    """A recency-weighted ask surfaces a newer node above an older, equally
    relevant one — the 'old review-of-all-work synthesis magnet' fix."""
    from datetime import datetime, timezone

    seed = _dated("Session:s", "work session", "2026-06-10T00:00:00Z")
    old = _dated("SessionInsight:old", "2026-05-18 review ALL improvements just made", "2026-05-18T00:00:00Z")
    new = _dated("SessionInsight:new", "2026-06-08 latest change", "2026-06-08T00:00:00Z")
    edges = [
        ResearchEdge(source="Session:s", target="SessionInsight:old", type="discusses", evidence="", metadata={}),
        ResearchEdge(source="Session:s", target="SessionInsight:new", type="discusses", evidence="", metadata={}),
    ]
    g = ResearchGraph(nodes=[seed, old, new], edges=edges)
    now = datetime(2026, 6, 12, tzinfo=timezone.utc)

    plain = compile_context(g, query="recent improvements", seeds=["Session:s"], budget=8000, backend=_backend())
    recent = compile_context(g, query="recent improvements", seeds=["Session:s"], budget=8000,
                             backend=_backend(), recency_now=now, recency_weight=0.4)

    def pos(bundle, key):
        return next(i for i, n in enumerate(bundle.ranked_nodes) if key in n)

    assert pos(recent, "new") < pos(recent, "old")           # recent surfaces above old
    assert recent.ranked_nodes != plain.ranked_nodes          # ranking actually changed


def test_recency_default_off_is_byte_identical():
    """No recency params (or weight 0) -> ranking unchanged; compiled/export
    artifacts stay byte-deterministic."""
    from datetime import datetime, timezone

    g = _connected_graph()
    a = compile_context(g, query="splatting", budget=8000, backend=_backend())
    b = compile_context(g, query="splatting", budget=8000, backend=_backend(),
                        recency_now=datetime(2026, 6, 12, tzinfo=timezone.utc), recency_weight=0.0)
    assert a.ranked_nodes == b.ranked_nodes and a.body == b.body


def test_recency_undated_synthesis_is_not_max_fresh():
    """An UNDATED synthesis node (timestamps omitted for byte-idempotence) is
    treated as NEUTRAL, not max-fresh — else the 'Review ALL improvements' magnet
    survives the recency blend (codex review)."""
    from datetime import datetime, timezone

    seed = _dated("Session:s", "work session", "2026-06-10T00:00:00Z")
    synth = ResearchNode(  # no date in metadata OR name
        id="Synthesis:pulse", name="Project Pulse — review of all work",
        type=ResearchNodeType.SYNTHESIS, description=("project pulse. ") * 20, metadata={},
    )
    new = _dated("SessionInsight:new", "2026-06-08 latest change", "2026-06-08T00:00:00Z")
    edges = [
        ResearchEdge(source="Session:s", target="Synthesis:pulse", type="discusses", evidence="", metadata={}),
        ResearchEdge(source="Session:s", target="SessionInsight:new", type="discusses", evidence="", metadata={}),
    ]
    g = ResearchGraph(nodes=[seed, synth, new], edges=edges)
    now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    recent = compile_context(g, query="recent work", seeds=["Session:s"], budget=8000,
                             backend=_backend(), recency_now=now, recency_weight=0.4)

    def pos(b, key):
        return next(i for i, n in enumerate(b.ranked_nodes) if key in n)

    assert pos(recent, "new") < pos(recent, "pulse")  # recent dated beats neutral undated


def test_recency_falls_back_to_a_leading_date_in_the_name():
    """When metadata has no timestamp, a leading YYYY-MM-DD in the NAME anchors
    recency — exactly the dated session/synthesis titles in the bug report."""
    from datetime import datetime, timezone

    seed = ResearchNode(id="Session:s", name="work session",
                        type=ResearchNodeType.SESSION_INSIGHT, description="seed. " * 20, metadata={})
    old = ResearchNode(  # date ONLY in the name (no metadata) — the reported case
        id="SessionInsight:old", name="2026-05-18 — Review ALL improvements just made",
        type=ResearchNodeType.SESSION_INSIGHT, description=("review. ") * 20, metadata={})
    new = ResearchNode(
        id="SessionInsight:new", name="2026-06-09 — latest change",
        type=ResearchNodeType.SESSION_INSIGHT, description=("latest. ") * 20, metadata={})
    edges = [
        ResearchEdge(source="Session:s", target="SessionInsight:old", type="discusses", evidence="", metadata={}),
        ResearchEdge(source="Session:s", target="SessionInsight:new", type="discusses", evidence="", metadata={}),
    ]
    g = ResearchGraph(nodes=[seed, old, new], edges=edges)
    now = datetime(2026, 6, 12, tzinfo=timezone.utc)
    recent = compile_context(g, query="recent improvements", seeds=["Session:s"], budget=8000,
                             backend=_backend(), recency_now=now, recency_weight=0.4)

    def pos(b, key):
        return next(i for i, n in enumerate(b.ranked_nodes) if key in n)

    assert pos(recent, "new") < pos(recent, "old")  # name-date makes May-18 sink under June

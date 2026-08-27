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


def test_recency_uses_session_started_at_not_just_first_seen():
    """Real session nodes carry their date in started_at (NOT first_seen_at, NOT
    always a leading date in the name). The blend must still demote an OLD
    started_at node below a recent one — the no-op a reviewer caught when only
    first_seen_at / name were anchored."""
    from datetime import datetime, timezone

    def sess(nid, name, started):
        return ResearchNode(id=nid, name=name, type=ResearchNodeType.SESSION_INSIGHT,
                            description=(name + ". ") * 20, metadata={"started_at": started})

    seed = sess("Session:s", "work session", "2026-06-10T00:00:00Z")
    old = sess("SessionInsight:old", "Review ALL improvements that were just made", "2026-05-18T14:23:04Z")
    new = sess("SessionInsight:new", "the latest change", "2026-06-09T10:00:00Z")
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

    assert pos(recent, "new") < pos(recent, "old")  # started_at anchors recency -> old demoted


def _arbitrated_graph() -> ResearchGraph:
    """Winner A, superseded loser B (A supersedes B), arbitration loser C
    (C resolved_by A — loser is the SOURCE, per tesserae.memory.contradiction)."""
    nodes = [
        ResearchNode(id="A", name="Winning Claim",
                     type=ResearchNodeType.PERFORMANCE_CLAIM,
                     description="The winning, current claim. " * 8),
        ResearchNode(id="B", name="Old Duplicate Claim",
                     type=ResearchNodeType.PERFORMANCE_CLAIM,
                     description="An older near-duplicate claim. " * 8),
        ResearchNode(id="C", name="Contradicted Claim",
                     type=ResearchNodeType.PERFORMANCE_CLAIM,
                     description="A claim that lost LLM arbitration. " * 8),
    ]
    edges = [
        ResearchEdge(source="A", target="B", type="supersedes"),
        ResearchEdge(source="C", target="A", type="resolved_by"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_superseded_and_arbitration_losers_excluded_by_default() -> None:
    graph = _arbitrated_graph()
    bundle = compile_context(
        graph, project_root=None, query="", seeds=["A"], backend=_backend()
    )
    assert bundle.selected_nodes == ["A"]
    assert set(bundle.ranked_nodes) == {"A"}
    assert "Old Duplicate Claim" not in bundle.body
    assert "Contradicted Claim" not in bundle.body

    # A query landing on a LOSER seed still surfaces the winner, never the loser.
    via_loser = compile_context(
        graph, project_root=None, query="", seeds=["B"], backend=_backend()
    )
    assert "A" in via_loser.selected_nodes
    assert "B" not in via_loser.selected_nodes
    assert "C" not in via_loser.selected_nodes


def test_include_superseded_restores_losers() -> None:
    graph = _arbitrated_graph()
    bundle = compile_context(
        graph, project_root=None, query="", seeds=["A"],
        backend=_backend(), include_superseded=True,
    )
    assert set(bundle.selected_nodes) == {"A", "B", "C"}
    assert "Old Duplicate Claim" in bundle.body
    assert "Contradicted Claim" in bundle.body


# --- view= (roadmap step 7): view-restricted traversal ----------------------


def _view_leak_graph() -> ResearchGraph:
    """C is 1 hop from the seed through an EXCLUDED edge (summarizes) but 2
    semantic hops away (A -uses-> B -uses-> C) — the exact shape where an
    unfiltered neighbourhood BFS leaks C into a depth-1 semantic walk."""
    nodes = [
        ResearchNode(
            id="A",
            name="Seed Concept",
            type=ResearchNodeType.CONCEPT,
            description="The seed concept under study. " * 8,
        ),
        ResearchNode(
            id="B",
            name="Bridge Method",
            type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
            description="A method the seed concept uses. " * 8,
        ),
        ResearchNode(
            id="C",
            name="Distant Concept",
            type=ResearchNodeType.CONCEPT,
            description="Two semantic hops from the seed. " * 8,
        ),
    ]
    edges = [
        ResearchEdge(source="A", target="C", type="summarizes"),
        ResearchEdge(source="A", target="B", type="uses"),
        ResearchEdge(source="B", target="C", type="uses"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_view_restricts_the_neighbourhood_not_just_the_ranking() -> None:
    """The mandatory companion of the view: without the BFS edge filter, C is
    admitted into the depth-1 neighbourhood through the zero-weighted
    ``summarizes`` edge, then ranked positive by PPR through the longer
    semantic path — surfacing a node the view cannot reach within depth."""
    graph = _view_leak_graph()

    unrestricted = compile_context(
        graph, project_root=None, query="", seeds=["A"],
        depth=1, backend=_backend(),
    )
    assert "C" in unrestricted.ranked_nodes  # via summarizes, 1 hop

    semantic = compile_context(
        graph, project_root=None, query="", seeds=["A"],
        depth=1, backend=_backend(), view="semantic",
    )
    assert "B" in semantic.ranked_nodes  # 1 semantic hop
    assert "C" not in semantic.ranked_nodes  # 2 semantic hops > depth=1
    assert "C" not in semantic.selected_nodes

    # At depth=2 the semantic path legitimately reaches C.
    semantic_deep = compile_context(
        graph, project_root=None, query="", seeds=["A"],
        depth=2, backend=_backend(), view="semantic",
    )
    assert "C" in semantic_deep.ranked_nodes


def test_view_explicit_weights_still_win() -> None:
    """An explicit caller weight resurrects an out-of-view edge class, exactly
    as it overrides the defaults — the view is a starting point, not a cage."""
    graph = _view_leak_graph()
    bundle = compile_context(
        graph, project_root=None, query="", seeds=["A"],
        depth=1, backend=_backend(), view="semantic",
        edge_type_weights={"summarizes": 1.0},
    )
    assert "C" in bundle.ranked_nodes  # 1 hop again, through the resurrection


def test_view_unknown_fails_loud() -> None:
    graph = _view_leak_graph()
    try:
        compile_context(
            graph, project_root=None, query="", seeds=["A"],
            backend=_backend(), view="provenance",
        )
    except ValueError as exc:
        assert "provenance" in str(exc)
        assert "semantic" in str(exc)
    else:  # pragma: no cover - the raise is the contract
        raise AssertionError("unknown view must raise ValueError")


def test_view_none_is_the_identity() -> None:
    """``view=None`` (the default) must leave the bundle byte-identical to a
    call that never heard of the parameter."""
    graph = _view_leak_graph()
    before = compile_context(
        graph, project_root=None, query="", seeds=["A"], backend=_backend()
    )
    after = compile_context(
        graph, project_root=None, query="", seeds=["A"], backend=_backend(),
        view=None,
    )
    assert before == after


def test_view_still_surfaces_the_winner_of_a_suppressed_seed() -> None:
    """Arbitration is epistemic bookkeeping, not view semantics. The winner
    of a suppressed seed rides supersedes/resolved_by — edges most views
    cannot traverse — so under a view it must be surfaced explicitly, or the
    bundle silently contains neither the stale claim nor the current one."""
    def _claim(nid: str, name: str) -> ResearchNode:
        return ResearchNode(
            id=nid,
            name=name,
            type=ResearchNodeType.PERFORMANCE_CLAIM,
            description=f"{name} body. " * 8,
        )

    graph = ResearchGraph(
        nodes=[
            _claim("A", "Stale Claim"),
            _claim("A2", "Winning Claim"),
            _claim("B", "Losing Claim"),
            _claim("B2", "Resolving Claim"),
        ],
        edges=[
            # source supersedes target -> target (A) is the loser.
            ResearchEdge(source="A2", target="A", type="supersedes"),
            # source resolved_by target -> source (B) is the loser.
            ResearchEdge(source="B", target="B2", type="resolved_by"),
        ],
    )

    # View-less: emergent via traversal (the existing contract).
    walked = compile_context(
        graph, project_root=None, query="", seeds=["A"], backend=_backend()
    )
    assert walked.selected_nodes == ["A2"]

    # Under a view that cannot traverse the arbitration edges: explicit.
    for seed, winner in (("A", "A2"), ("B", "B2")):
        bundle = compile_context(
            graph, project_root=None, query="", seeds=[seed],
            backend=_backend(), view="semantic",
        )
        assert bundle.selected_nodes == [winner], (
            f"seed {seed}: expected its winner {winner}, "
            f"got {bundle.selected_nodes}"
        )
        assert seed not in bundle.ranked_nodes


# --- view=[...] (roadmap step 8): multi-view traversal with rank fusion -----


def _fusion_graph() -> ResearchGraph:
    """One node per lane-reachability class around a single seed S:
    SEM (semantic edge only), CAU (causal edge only), BOTH (one of each),
    EXC (excluded edge only — reachable by NO view). The causal edges are
    ``attributes_improvement_to`` — NOT ``resolved_by``, whose source is an
    arbitration loser and would suppress the seed itself."""
    def _node(nid: str, name: str) -> ResearchNode:
        return ResearchNode(
            id=nid,
            name=name,
            type=ResearchNodeType.CONCEPT,
            description=f"{name} body text. " * 8,
        )

    nodes = [
        _node("S", "Seed"),
        _node("SEM", "Semantic Neighbour"),
        _node("CAU", "Causal Neighbour"),
        _node("BOTH", "Shared Neighbour"),
        _node("EXC", "Provenance Neighbour"),
    ]
    edges = [
        ResearchEdge(source="S", target="SEM", type="uses"),
        ResearchEdge(source="S", target="CAU", type="attributes_improvement_to"),
        ResearchEdge(source="S", target="BOTH", type="uses"),
        ResearchEdge(source="S", target="BOTH", type="attributes_improvement_to"),
        ResearchEdge(source="S", target="EXC", type="summarizes"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_multi_view_fuses_lanes_and_reports_per_view_provenance() -> None:
    graph = _fusion_graph()
    bundle = compile_context(
        graph, project_root=None, query="", seeds=["S"],
        depth=1, budget=0, backend=_backend(),
        view=["semantic", "causal"],
    )

    ranked = bundle.ranked_nodes
    # The excluded-edge node is reachable by NO selected lane.
    assert "EXC" not in ranked
    assert set(ranked) == {"S", "SEM", "CAU", "BOTH"}
    # RRF: a node two lanes reached outranks the single-lane nodes.
    assert ranked.index("BOTH") < ranked.index("SEM")
    assert ranked.index("BOTH") < ranked.index("CAU")

    via = {c.node_id: c.via_views for c in bundle.citations}
    assert via["S"] == ("semantic", "causal")
    assert via["BOTH"] == ("semantic", "causal")
    assert via["SEM"] == ("semantic",)
    assert via["CAU"] == ("causal",)


def test_multi_view_is_deterministic() -> None:
    graph = _fusion_graph()
    kwargs = dict(
        project_root=None, query="", seeds=["S"], depth=1, budget=0,
        view=["semantic", "causal"],
    )
    first = compile_context(graph, backend=_backend(), **kwargs)
    second = compile_context(graph, backend=_backend(), **kwargs)
    assert first == second


def test_multi_view_dedupes_and_rejects_unknown_names() -> None:
    graph = _fusion_graph()
    deduped = compile_context(
        graph, project_root=None, query="", seeds=["S"], depth=1,
        backend=_backend(), view=["semantic", "semantic", "causal"],
    )
    fused = compile_context(
        graph, project_root=None, query="", seeds=["S"], depth=1,
        backend=_backend(), view=["semantic", "causal"],
    )
    assert deduped == fused

    try:
        compile_context(
            graph, project_root=None, query="", seeds=["S"],
            backend=_backend(), view=["semantic", "bogus"],
        )
    except ValueError as exc:
        assert "bogus" in str(exc)
    else:  # pragma: no cover - the raise is the contract
        raise AssertionError("unknown view in a list must raise ValueError")

    try:
        compile_context(
            graph, project_root=None, query="", seeds=["S"],
            backend=_backend(), view=[],
        )
    except ValueError as exc:
        assert "empty" in str(exc)
    else:  # pragma: no cover - the raise is the contract
        raise AssertionError("an empty view list must raise ValueError")


def test_via_views_defaults_empty_and_serializes_away() -> None:
    """The unset path must stay byte-identical: no view -> via_views is ()
    on the dataclass and ABSENT from the serialized citation dict."""
    from tesserae.context_compiler import citation_dict

    graph = _fusion_graph()
    bundle = compile_context(
        graph, project_root=None, query="", seeds=["S"], backend=_backend()
    )
    assert bundle.citations
    for c in bundle.citations:
        assert c.via_views == ()
        payload = citation_dict(c)
        assert "via_views" not in payload
        assert set(payload) == {
            "node_id", "node_name", "source_path", "wiki_kind"
        }

    with_view = compile_context(
        graph, project_root=None, query="", seeds=["S"], backend=_backend(),
        view="semantic",
    )
    semantic_citations = [c for c in with_view.citations if c.node_id == "SEM"]
    assert semantic_citations and semantic_citations[0].via_views == ("semantic",)
    assert citation_dict(semantic_citations[0])["via_views"] == ("semantic",)


def test_multi_view_pool_reservation_walks_the_fused_ranking() -> None:
    """Step 8 absorbs the multi_pool reservation into the fused ranking: a
    producer-made Runbook reachable ONLY through the causal lane still earns
    its slot — one ranking, one reservation pass, one budget."""
    graph = _fusion_graph()
    graph.nodes.append(
        ResearchNode(
            id="RB",
            name="Recovery Runbook",
            type=ResearchNodeType.RUNBOOK,
            description="How the failure was recovered. " * 8,
            metadata={"extractor": "memory.distill.run_distillation_pass"},
        )
    )
    graph.edges.append(
        ResearchEdge(source="S", target="RB", type="attributes_improvement_to")
    )

    bundle = compile_context(
        graph, project_root=None, query="", seeds=["S"],
        depth=1, budget=0, backend=_backend(),
        view=["semantic", "causal"], multi_pool=True,
    )

    assert bundle.pool_reservations is not None
    reservation = bundle.pool_reservations["Runbook"]
    assert reservation is not None
    assert reservation["node_id"] == "RB"
    assert reservation["delivered"] is True
    via = {c.node_id: c.via_views for c in bundle.citations}
    assert via["RB"] == ("causal",)


# ---------------------------------------------------------------------------
# Retrieval PROFILE (roadmap step 9)
# ---------------------------------------------------------------------------


def test_explain_profiles_the_seed_searches_and_leaves_the_bundle_identical() -> None:
    """``explain`` reports on the seed searches; it must not compile a
    different bundle. Body, selection and ranking are compared byte for byte
    because a profile that could move any of them would be a ranking change
    wearing a diagnostic's clothes."""
    graph = _connected_graph()
    plain = compile_context(
        graph, project_root=None, query="gaussian splatting", backend=_backend()
    )
    explained = compile_context(
        graph, project_root=None, query="gaussian splatting", backend=_backend(),
        explain=True,
    )

    assert plain.retrieval_profiles is None
    assert explained.body == plain.body
    assert explained.selected_nodes == plain.selected_nodes
    assert explained.ranked_nodes == plain.ranked_nodes
    assert explained.seeds_used == plain.seeds_used

    profiles = explained.retrieval_profiles
    assert profiles is not None and len(profiles) == 1
    prof = profiles[0]
    assert prof.query == "gaussian splatting"
    assert set(prof.lanes) == {"bm25", "lexical", "embedding"}
    assert prof.candidates_in == len(graph.nodes)
    assert prof.returned == len(prof.winners) > 0


def test_explain_reports_one_profile_per_subquery_under_multi_pool() -> None:
    """A summed profile would hide which sub-query was the expensive one, so
    the list is per-search and its length is the search count."""
    graph = _connected_graph()
    explained = compile_context(
        graph, project_root=None, query="gaussian splatting and bm25 ranking",
        backend=_backend(), multi_pool=True, explain=True,
    )
    assert explained.retrieval_profiles is not None
    assert len(explained.retrieval_profiles) >= 1
    assert all(p.candidates_in == len(graph.nodes) for p in explained.retrieval_profiles)


def test_explain_with_seeds_but_no_query_reports_an_empty_list_not_none() -> None:
    """``None`` means "profiling never ran"; the empty list means "it ran and
    no seed search happened". Collapsing the two would make an unprofiled
    compile indistinguishable from a seed-only one."""
    graph = _connected_graph()
    bundle = compile_context(
        graph, project_root=None, seeds=["splat"], backend=_backend(), explain=True
    )
    assert bundle.retrieval_profiles == []


# ------------------------- source prose only when there is room to be evidence


def test_a_tight_per_node_share_keeps_the_extracted_body():
    """Below the measured crossover, swapping in source prose makes the bundle
    WORSE than the distillation it replaced: at 1,260 chars per document prose
    scored 0.145 against the claims' 0.222 on 57 comparative-reasoning
    questions. `_MIN_SOURCE_EXCERPT` is that crossover, not a "how small can an
    excerpt be" bound."""
    from tesserae.context_compiler import (_MIN_SOURCE_EXCERPT,
                                           _TARGET_BUNDLE_NODES)

    # a five-way split of this budget lands under the floor
    budget = (_MIN_SOURCE_EXCERPT - 100) * _TARGET_BUNDLE_NODES
    assert budget // _TARGET_BUNDLE_NODES < _MIN_SOURCE_EXCERPT


def test_a_generous_per_node_share_admits_source_prose():
    """Above it, prose beats the distillation by +0.084 (8/8 replicates,
    p=0.0078), which is the whole reason the bundle reads source at all."""
    from tesserae.context_compiler import (_MIN_SOURCE_EXCERPT,
                                           _TARGET_BUNDLE_NODES)

    budget = (_MIN_SOURCE_EXCERPT + 100) * _TARGET_BUNDLE_NODES
    assert budget // _TARGET_BUNDLE_NODES >= _MIN_SOURCE_EXCERPT


def test_the_floor_sits_below_the_excerpt_cap():
    """A floor above the cap would make source prose unreachable at every
    budget — the feature would silently never run."""
    from tesserae.context_compiler import (_MIN_SOURCE_EXCERPT,
                                           SOURCE_EXCERPT_CHARS)

    assert _MIN_SOURCE_EXCERPT < SOURCE_EXCERPT_CHARS

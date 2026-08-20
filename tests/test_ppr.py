"""Tests for tesserae.retrieval.ppr.personalized_pagerank."""

from __future__ import annotations

import pytest

from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.retrieval.ppr import (
    DEFAULT_EDGE_TYPE_WEIGHTS,
    HUB_DEGREE_CAP,
    PROVENANCE_EDGE_TYPES,
    personalized_pagerank,
)


def _make_graph() -> ResearchGraph:
    """5-node fixture: a Session with two Insights and one Decision, plus
    an isolated Paper that nothing points to.

    Topology (undirected for PPR purposes):

        session ---(derived_from_session)--- insight_a
        session ---(derived_from_session)--- insight_b
        insight_a -(references)--- decision
        paper (disconnected)
    """

    nodes = [
        ResearchNode(id="session", name="Session 1", type=ResearchNodeType.SESSION),
        ResearchNode(
            id="insight_a", name="Insight A", type=ResearchNodeType.SESSION_INSIGHT
        ),
        ResearchNode(
            id="insight_b", name="Insight B", type=ResearchNodeType.SESSION_INSIGHT
        ),
        ResearchNode(
            id="decision", name="Decision 1", type=ResearchNodeType.SESSION_DECISION
        ),
        ResearchNode(id="paper", name="Orphan Paper", type=ResearchNodeType.PAPER),
    ]
    edges = [
        ResearchEdge(source="insight_a", target="session", type="derived_from_session"),
        ResearchEdge(source="insight_b", target="session", type="derived_from_session"),
        ResearchEdge(source="insight_a", target="decision", type="references"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_ppr_scores_sum_to_approximately_one() -> None:
    graph = _make_graph()
    ranked = personalized_pagerank(graph, seed_ids=["insight_a"], top_k=10)
    total = sum(score for _node_id, score in ranked)
    assert total == pytest.approx(1.0, abs=1e-3)


def test_seed_is_top_ranked() -> None:
    graph = _make_graph()
    ranked = personalized_pagerank(graph, seed_ids=["insight_a"], top_k=5)
    assert ranked, "expected non-empty ranking"
    assert ranked[0][0] == "insight_a", f"seed not first: {ranked}"


def test_connected_nodes_outrank_disconnected_paper() -> None:
    graph = _make_graph()
    ranked = personalized_pagerank(graph, seed_ids=["insight_a"], top_k=10)
    by_id = {node_id: score for node_id, score in ranked}
    # The disconnected ``paper`` node must not appear at all — PPR mass
    # never reached it. (See ``test_top_k_excludes_unreachable_zero_score_nodes``
    # for the explicit regression on this behavior.)
    assert "paper" not in by_id, f"disconnected paper leaked into results: {by_id}"
    # Every reachable node should have positive score.
    for connected in ("insight_a", "session", "decision", "insight_b"):
        assert by_id.get(connected, 0.0) > 0.0, (
            f"{connected} missing or zero in results: {by_id}"
        )


def test_unknown_seed_returns_empty() -> None:
    graph = _make_graph()
    assert personalized_pagerank(graph, seed_ids=["does-not-exist"]) == []


def test_multi_seed_balances_mass_across_components() -> None:
    graph = _make_graph()
    multi = personalized_pagerank(
        graph, seed_ids=["insight_a", "paper"], top_k=5
    )
    by_id = {node_id: score for node_id, score in multi}
    # Both seeds should be in the top ranks because mass starts there.
    assert by_id["insight_a"] > 0.0
    assert by_id["paper"] > 0.0


def test_default_edge_type_weights_upweight_session_edges() -> None:
    # Spec quality bar: defaults must favor session-finding edges so PPR
    # from an Insight tends to revisit related Insights/Decisions/Sessions.
    assert DEFAULT_EDGE_TYPE_WEIGHTS["derived_from_session"] > 1.0
    assert DEFAULT_EDGE_TYPE_WEIGHTS["references"] > 1.0


def test_top_k_truncates_results() -> None:
    graph = _make_graph()
    ranked = personalized_pagerank(graph, seed_ids=["insight_a"], top_k=2)
    assert len(ranked) == 2


def test_top_k_excludes_unreachable_zero_score_nodes() -> None:
    """Regression for codex P2: when ``top_k`` exceeds the seed's connected
    component, the disconnected ``paper`` node would be returned with a
    score of 0.0. PPR must only return nodes that actually received mass.
    """
    graph = _make_graph()
    # top_k (10) > seed-component size (4: insight_a, session, insight_b,
    # decision); the orphan ``paper`` is unreachable and must be excluded.
    ranked = personalized_pagerank(graph, seed_ids=["insight_a"], top_k=10)
    node_ids_returned = {node_id for node_id, _score in ranked}
    assert "paper" not in node_ids_returned, (
        f"unreachable disconnected node leaked into results: {ranked}"
    )
    assert all(score > 0.0 for _node_id, score in ranked), (
        f"zero-score node returned: {ranked}"
    )
    # Reachable component size is 4; we must return exactly those 4 even
    # though ``top_k`` was 10.
    assert len(ranked) == 4


# -- tame_hubs: degree cap + provenance downweight (Descent PR1) -------------


def test_provenance_edge_types_cover_the_bookkeeping_classes() -> None:
    # Spec (Descent §5.4): exactly these five edge classes carry provenance
    # rather than semantic relatedness; the cap is 200 per the design doc.
    assert PROVENANCE_EDGE_TYPES == frozenset(
        {"authored_by", "discussed_in", "evidenced_by", "mentioned_in", "part_of"}
    )
    assert HUB_DEGREE_CAP == 200


def test_tame_hubs_off_by_default_leaves_ranking_unchanged() -> None:
    graph = _make_graph()
    default = personalized_pagerank(graph, seed_ids=["insight_a"], top_k=10)
    explicit_off = personalized_pagerank(
        graph, seed_ids=["insight_a"], top_k=10, tame_hubs=False
    )
    assert default == explicit_off


def _provenance_vs_semantic_graph() -> ResearchGraph:
    """Seed with two neighbours: one over a provenance edge, one semantic.

        prov ---(discussed_in)--- seed ---(references)--- sem

    Both edge types default to weight 1.5, so without ``tame_hubs`` the
    two neighbours receive identical PPR mass.
    """
    nodes = [
        ResearchNode(id="seed", name="Seed", type=ResearchNodeType.SESSION_INSIGHT),
        ResearchNode(id="prov", name="Provenance", type=ResearchNodeType.SESSION),
        ResearchNode(id="sem", name="Semantic", type=ResearchNodeType.SESSION_DECISION),
    ]
    edges = [
        ResearchEdge(source="seed", target="prov", type="discussed_in"),
        ResearchEdge(source="seed", target="sem", type="references"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_tame_hubs_downweights_provenance_edges_vs_semantic() -> None:
    graph = _provenance_vs_semantic_graph()

    untamed = dict(personalized_pagerank(graph, seed_ids=["seed"], top_k=10))
    assert untamed["prov"] == pytest.approx(untamed["sem"]), (
        f"fixture broken: both edge types weigh 1.5 by default: {untamed}"
    )

    tamed = dict(
        personalized_pagerank(graph, seed_ids=["seed"], top_k=10, tame_hubs=True)
    )
    assert tamed["sem"] > tamed["prov"], (
        f"provenance edge not downweighted relative to semantic: {tamed}"
    )


def _hub_graph(n_leaves: int) -> ResearchGraph:
    """A hub with ``n_leaves`` provenance-attached leaves plus one seed.

        seed ---(references)--- hub ---(mentioned_in)--- leaf_000..leaf_NNN
    """
    nodes = [
        ResearchNode(id="seed", name="Seed", type=ResearchNodeType.SESSION_INSIGHT),
        ResearchNode(id="hub", name="Hub", type=ResearchNodeType.SESSION),
    ]
    edges = [ResearchEdge(source="seed", target="hub", type="references")]
    for i in range(n_leaves):
        leaf = f"leaf_{i:03d}"
        nodes.append(
            ResearchNode(id=leaf, name=f"Leaf {i}", type=ResearchNodeType.PAPER)
        )
        edges.append(ResearchEdge(source="hub", target=leaf, type="mentioned_in"))
    return ResearchGraph(nodes=nodes, edges=edges)


def test_tame_hubs_degree_cap_bounds_hub_fanout() -> None:
    # 250 leaves + seed = 251 hub neighbours, over the 200 cap.
    graph = _hub_graph(250)

    untamed = personalized_pagerank(graph, seed_ids=["seed"], top_k=300)
    # Without the cap, every leaf receives mass through the hub.
    assert len(untamed) == 252

    tamed = personalized_pagerank(
        graph, seed_ids=["seed"], top_k=300, tame_hubs=True
    )
    # With the cap the hub keeps its 200 strongest ties: the semantic seed
    # edge (weight 1.5) plus the 199 lowest-index leaves (deterministic
    # tie-break). The remaining 51 leaves never receive mass.
    assert len(tamed) == 201
    tamed_ids = {node_id for node_id, _score in tamed}
    assert {"seed", "hub"} <= tamed_ids
    assert "leaf_000" in tamed_ids
    assert "leaf_249" not in tamed_ids


def test_tame_hubs_degree_cap_is_deterministic() -> None:
    graph = _hub_graph(250)
    first = personalized_pagerank(graph, seed_ids=["seed"], top_k=300, tame_hubs=True)
    second = personalized_pagerank(graph, seed_ids=["seed"], top_k=300, tame_hubs=True)
    assert first == second


def test_hub_ids_scopes_the_degree_cap_to_listed_nodes() -> None:
    """The sidecar hub list (Descent PR8) replaces the fanout scan verbatim."""
    graph = _hub_graph(250)

    scan = personalized_pagerank(graph, seed_ids=["seed"], top_k=300, tame_hubs=True)
    listed = personalized_pagerank(
        graph, seed_ids=["seed"], top_k=300, tame_hubs=True, hub_ids=["hub"]
    )
    # Naming the actual hub reproduces the scan's cap byte-for-byte.
    assert listed == scan

    # An EMPTY hub list means "no hubs" — nothing is capped (unlike None,
    # which keeps the PR1 scan): every leaf receives mass again.
    uncapped = personalized_pagerank(
        graph, seed_ids=["seed"], top_k=300, tame_hubs=True, hub_ids=[]
    )
    assert len(uncapped) == 252

    # Unknown ids are dropped silently (seed_ids contract) — same as empty.
    unknown = personalized_pagerank(
        graph, seed_ids=["seed"], top_k=300, tame_hubs=True, hub_ids=["ghost"]
    )
    assert unknown == uncapped


def test_hub_ids_ignored_without_tame_hubs() -> None:
    """hub_ids is flag-gated: without tame_hubs it must change nothing."""
    graph = _hub_graph(250)
    plain = personalized_pagerank(graph, seed_ids=["seed"], top_k=300)
    with_list = personalized_pagerank(
        graph, seed_ids=["seed"], top_k=300, tame_hubs=False, hub_ids=["hub"]
    )
    assert with_list == plain


# -- MCP tool wiring ---------------------------------------------------------


def _write_graph_json(tmp_path, graph: ResearchGraph):
    path = tmp_path / "graph.json"
    path.write_text(graph.to_json(), encoding="utf-8")
    return path


def test_graph_ppr_listed_in_mcp_tool_registry() -> None:
    from tesserae.mcp_server import LLMWikiMCPServer

    tools = LLMWikiMCPServer().list_tools()
    names = {tool["name"] for tool in tools}
    assert "graph_ppr" in names
    ppr_tool = next(tool for tool in tools if tool["name"] == "graph_ppr")
    assert "seed_node_id" in ppr_tool["inputSchema"]["properties"]
    assert ppr_tool["inputSchema"]["required"] == ["seed_node_id"]


def test_graph_ppr_mcp_call_returns_ranked_results(tmp_path) -> None:
    from tesserae.mcp_server import LLMWikiMCPServer

    graph_path = _write_graph_json(tmp_path, _make_graph())
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    payload = server.call_tool(
        "graph_ppr", {"seed_node_id": "insight_a", "top_k": 5}
    )

    assert payload["seed_ids"] == ["insight_a"]
    assert payload["results"], "expected non-empty results"
    assert payload["results"][0]["node_id"] == "insight_a"
    # Each result carries the decorated metadata an agent needs.
    for item in payload["results"]:
        assert {"node_id", "name", "type", "score"} <= set(item)


def test_graph_ppr_mcp_call_accepts_list_seeds(tmp_path) -> None:
    from tesserae.mcp_server import LLMWikiMCPServer

    graph_path = _write_graph_json(tmp_path, _make_graph())
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    payload = server.call_tool(
        "graph_ppr",
        {"seed_node_id": ["insight_a", "decision"], "top_k": 3},
    )
    assert sorted(payload["seed_ids"]) == ["decision", "insight_a"]
    assert len(payload["results"]) == 3


def test_graph_ppr_mcp_schema_excludes_zero_alpha() -> None:
    """Regression for codex P3: schema must declare ``alpha > 0`` so MCP
    clients can't pass ``alpha: 0`` past the contract."""
    from tesserae.mcp_server import LLMWikiMCPServer

    tools = LLMWikiMCPServer().list_tools()
    ppr_tool = next(tool for tool in tools if tool["name"] == "graph_ppr")
    alpha_schema = ppr_tool["inputSchema"]["properties"]["alpha"]
    assert alpha_schema.get("exclusiveMinimum") == 0.0, (
        f"alpha schema must use exclusiveMinimum=0 (not inclusive minimum=0): "
        f"{alpha_schema}"
    )
    # And inclusive ``minimum: 0`` must be gone so the contract is unambiguous.
    assert "minimum" not in alpha_schema or alpha_schema["minimum"] > 0


def test_graph_ppr_mcp_call_preserves_explicit_alpha(tmp_path) -> None:
    """Regression for codex P3: an explicit ``alpha=0.05`` must not be
    silently swapped for the 0.15 default by ``alpha or 0.15``."""
    from tesserae.mcp_server import LLMWikiMCPServer

    graph_path = _write_graph_json(tmp_path, _make_graph())
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    payload_low = server.call_tool(
        "graph_ppr",
        {"seed_node_id": "insight_a", "top_k": 5, "alpha": 0.05},
    )
    payload_default = server.call_tool(
        "graph_ppr",
        {"seed_node_id": "insight_a", "top_k": 5},
    )
    # With a much smaller teleport probability the walk wanders further from
    # the seed, so the seed's own score must be strictly lower than it is
    # under the default alpha=0.15. If alpha were silently overridden the
    # two payloads would be identical.
    seed_low = next(
        item["score"] for item in payload_low["results"]
        if item["node_id"] == "insight_a"
    )
    seed_default = next(
        item["score"] for item in payload_default["results"]
        if item["node_id"] == "insight_a"
    )
    assert seed_low < seed_default, (
        f"explicit alpha=0.05 appears to have been overridden: "
        f"seed_low={seed_low}, seed_default={seed_default}"
    )


def test_graph_ppr_exclude_direct_neighbors_surfaces_multi_hop_only(tmp_path) -> None:
    """``exclude_direct_neighbors=true`` drops the seeds and their 1-hop
    neighbourhood so only 2+ hop "unexpected" connections are returned."""
    from tesserae.mcp_server import LLMWikiMCPServer

    tools = LLMWikiMCPServer().list_tools()
    ppr_tool = next(tool for tool in tools if tool["name"] == "graph_ppr")
    flag_schema = ppr_tool["inputSchema"]["properties"]["exclude_direct_neighbors"]
    assert flag_schema["type"] == "boolean"
    assert flag_schema["default"] is False

    graph_path = _write_graph_json(tmp_path, _make_graph())
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    # Default: the 1-hop neighbourhood dominates — ``session`` (direct
    # neighbour of the seed) outranks ``insight_b`` (2 hops away).
    payload_default = server.call_tool(
        "graph_ppr", {"seed_node_id": "insight_a", "top_k": 10}
    )
    ids_default = [item["node_id"] for item in payload_default["results"]]
    assert ids_default.index("session") < ids_default.index("insight_b")

    payload = server.call_tool(
        "graph_ppr",
        {"seed_node_id": "insight_a", "top_k": 10, "exclude_direct_neighbors": True},
    )
    assert payload["exclude_direct_neighbors"] is True
    ids = [item["node_id"] for item in payload["results"]]
    # The seed (insight_a) and its direct neighbours (session via
    # derived_from_session, decision via references — both edge directions
    # count) are dropped; only the 2-hop ``insight_b`` survives. The
    # disconnected ``paper`` never receives PPR mass.
    assert ids == ["insight_b"], f"expected only the 2-hop node: {ids}"


def test_graph_ppr_mcp_call_rejects_zero_alpha(tmp_path) -> None:
    """Regression for codex P3: ``alpha=0`` must be rejected end-to-end,
    not silently coerced to the default."""
    from tesserae.mcp_server import LLMWikiMCPServer

    graph_path = _write_graph_json(tmp_path, _make_graph())
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    with pytest.raises(ValueError, match="alpha"):
        server.call_tool(
            "graph_ppr",
            {"seed_node_id": "insight_a", "top_k": 5, "alpha": 0},
        )


# ---------------------------------------------------------------------------
# seed_weights: relevance-proportional teleport mass
# ---------------------------------------------------------------------------


def _weighted_fixture() -> ResearchGraph:
    """Two disjoint chains, one seed on each, so mass cannot leak between them."""
    nodes = [
        ResearchNode(id=c, name=c, type=ResearchNodeType.CONCEPT, description=c)
        for c in "ABCDE"
    ]
    edges = [
        ResearchEdge(source="A", target="B", type="uses"),
        ResearchEdge(source="C", target="D", type="uses"),
        ResearchEdge(source="D", target="E", type="uses"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_equal_seed_weights_reproduce_uniform_exactly():
    """The default path must stay byte-for-byte what it was."""
    graph = _weighted_fixture()
    assert personalized_pagerank(graph, ["A", "C"], top_k=5) == personalized_pagerank(
        graph, ["A", "C"], top_k=5, seed_weights={"A": 1.0, "C": 1.0}
    )


def test_equal_seed_weights_reproduce_uniform_with_dangling_nodes():
    """Dangling mass is redistributed over the personalization vector, so a
    graph with sinks is where a 1/len(seeds) shortcut would diverge."""
    nodes = [
        ResearchNode(id=c, name=c, type=ResearchNodeType.CONCEPT, description=c)
        for c in "ABCDE"
    ]
    graph = ResearchGraph(
        nodes=nodes, edges=[ResearchEdge(source="A", target="B", type="uses")]
    )
    assert personalized_pagerank(graph, ["A", "C"], top_k=5) == personalized_pagerank(
        graph, ["A", "C"], top_k=5, seed_weights={"A": 1.0, "C": 1.0}
    )


def test_seed_weights_shift_mass_toward_the_heavier_seed():
    graph = _weighted_fixture()
    skewed = dict(personalized_pagerank(
        graph, ["A", "C"], top_k=5, seed_weights={"A": 9.0, "C": 1.0}
    ))
    even = dict(personalized_pagerank(graph, ["A", "C"], top_k=5))
    assert skewed["A"] > even["A"]
    assert skewed["C"] < even["C"]
    # ...and the effect propagates along A's chain, not just to A itself.
    assert skewed["B"] > even["B"]


def test_seed_weights_only_ratios_matter():
    graph = _weighted_fixture()
    a = personalized_pagerank(graph, ["A", "C"], top_k=5, seed_weights={"A": 3.0, "C": 1.0})
    b = personalized_pagerank(graph, ["A", "C"], top_k=5, seed_weights={"A": 300.0, "C": 100.0})
    assert a == b


def test_seed_weights_summing_to_zero_raises():
    """Falling back to uniform would answer a different question than the caller
    asked; returning [] would read as "the graph knows nothing"."""
    graph = _weighted_fixture()
    with pytest.raises(ValueError, match="summed to zero"):
        personalized_pagerank(graph, ["A", "C"], seed_weights={"A": 0.0, "C": 0.0})


def test_negative_seed_weights_raise():
    graph = _weighted_fixture()
    with pytest.raises(ValueError, match="non-negative"):
        personalized_pagerank(graph, ["A", "C"], seed_weights={"A": -1.0, "C": 2.0})


def test_unnamed_seeds_get_zero_mass_not_an_equal_share():
    """A caller who weights some seeds and not others gets what they asked for:
    the named ones carry the walk. Silently topping up the unnamed would make a
    broad seed set behave like a narrow one without saying so."""
    graph = _weighted_fixture()
    only_a = personalized_pagerank(graph, ["A", "C"], top_k=5, seed_weights={"A": 1.0})
    just_a = personalized_pagerank(graph, ["A"], top_k=5)
    assert dict(only_a)["A"] == pytest.approx(dict(just_a)["A"])

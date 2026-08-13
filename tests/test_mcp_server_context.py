"""Tests for the MCP transport layer of the Phase-7 context compiler.

Covers CTX-02 (the ``compile_context`` MCP tool round-trip through
``call_tool``) and CTX-03 (the opt-in ``use_ppr`` ranking path on
``node_context``, with the default path preserved). All deterministic and
CI-safe: ``synthesize`` defaults to ``False`` so no LLM backend is invoked.
"""

from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)


def _multihop_graph_path(tmp_path):
    """A small graph with a genuine 2-hop neighbourhood.

    Topology (undirected for PPR):
        Paper:focal --uses--> Method:hop1 --extends--> Concept:hop2
        Paper:focal --supports_claim--> Claim:hop1b

    ``Concept:hop2`` is NOT a 1-hop neighbour of the focal node, so the strict
    1-hop walk cannot surface it, but PPR seeded at the focal node can.
    """
    focal = ResearchNode(
        id="Paper:focal",
        name="Focal Paper",
        type=ResearchNodeType.PAPER,
        description="The focal paper under inspection.",
    )
    hop1 = ResearchNode(
        id="Method:hop1",
        name="Hop One Method",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        description="A method used directly by the focal paper.",
    )
    hop1b = ResearchNode(
        id="Claim:hop1b",
        name="Hop One Claim",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="A claim supported directly by the focal paper.",
    )
    hop2 = ResearchNode(
        id="Concept:hop2",
        name="Hop Two Concept",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        description="A concept two hops from the focal paper via the method.",
    )
    graph = ResearchGraph(
        nodes=[focal, hop1, hop1b, hop2],
        edges=[
            ResearchEdge(source=focal.id, target=hop1.id, type="uses", evidence="uses hop1"),
            ResearchEdge(source=focal.id, target=hop1b.id, type="supports_claim", evidence="supports hop1b"),
            ResearchEdge(source=hop1.id, target=hop2.id, type="extends", evidence="hop1 extends hop2"),
        ],
    )
    path = tmp_path / "graph.json"
    path.write_text(graph.to_json(indent=2), encoding="utf-8")
    return path, {n.id for n in graph.nodes}


def test_compile_context_tool_roundtrip(tmp_path):
    graph_path, node_ids = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool(
        "compile_context",
        {"seeds": ["Paper:focal"], "query": "focal paper method", "depth": 2},
    )

    # Transport-layer contract: the dict carries the full bundle payload.
    assert set(
        ["body", "citations", "selected_node_ids", "char_budget_used", "synthesized"]
    ).issubset(result.keys())
    assert isinstance(result["body"], str)
    assert result["body"].startswith("# Context:")
    assert result["synthesized"] is False
    assert isinstance(result["selected_node_ids"], list)
    assert result["selected_node_ids"]  # at least the seed survives
    # Citation integrity survives the JSON-able transport conversion: every
    # cited node_id resolves back to a node in the graph.
    assert result["citations"]
    for citation in result["citations"]:
        assert citation["node_id"] in node_ids


def test_node_context_use_ppr_differs(tmp_path):
    graph_path, node_ids = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    one_hop = server.call_tool("node_context", {"node_id": "Paper:focal", "use_ppr": False})
    ppr = server.call_tool("node_context", {"node_id": "Paper:focal", "use_ppr": True})

    one_hop_ids = {n["id"] for n in one_hop["neighbors"]}
    ppr_ids = {n["id"] for n in ppr["neighbors"]}

    # Both paths are well-formed and exclude the focal node itself.
    assert "Paper:focal" not in one_hop_ids
    assert "Paper:focal" not in ppr_ids
    assert ppr_ids  # PPR returns a non-empty ranked neighbourhood

    # The strict 1-hop walk can only see direct neighbours.
    assert "Concept:hop2" not in one_hop_ids
    # PPR seeded at the focal node can surface the 2-hop concept that the
    # 1-hop walk structurally cannot — the two sets are not required to match.
    assert "Concept:hop2" in ppr_ids


def test_node_context_use_ppr_excludes_suppressed(tmp_path):
    # Suppress the direct method node; PPR must not surface it (nor the focal).
    focal = ResearchNode(
        id="Paper:focal",
        name="Focal Paper",
        type=ResearchNodeType.PAPER,
        description="Focal.",
    )
    live = ResearchNode(
        id="Claim:live",
        name="Live Claim",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="A live neighbour.",
    )
    dead = ResearchNode(
        id="Method:dead",
        name="Dead Method",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        description="A superseded neighbour.",
    )
    winner = ResearchNode(
        id="Method:winner",
        name="Winner Method",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        description="Supersedes the dead method.",
    )
    graph = ResearchGraph(
        nodes=[focal, live, dead, winner],
        edges=[
            ResearchEdge(source=focal.id, target=live.id, type="supports_claim", evidence="x"),
            ResearchEdge(source=focal.id, target=dead.id, type="uses", evidence="y"),
            # winner supersedes dead -> dead is suppressed (target of supersedes).
            ResearchEdge(source=winner.id, target=dead.id, type="supersedes", evidence="z"),
        ],
    )
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(graph.to_json(indent=2), encoding="utf-8")
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    ppr = server.call_tool("node_context", {"node_id": "Paper:focal", "use_ppr": True})
    ppr_ids = {n["id"] for n in ppr["neighbors"]}

    assert "Method:dead" not in ppr_ids
    assert "Paper:focal" not in ppr_ids


def test_node_context_default_unchanged(tmp_path):
    graph_path, _node_ids = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    # No use_ppr arg at all: structure + 1-hop semantics preserved.
    ctx = server.call_tool("node_context", {"node_id": "Paper:focal"})

    assert ctx["node"]["name"] == "Focal Paper"
    assert "neighbors" in ctx and "edges" in ctx
    neighbor_ids = {n["id"] for n in ctx["neighbors"]}
    assert neighbor_ids == {"Method:hop1", "Claim:hop1b"}


def test_compile_context_budget_zero_is_uncapped(tmp_path):
    """MCP budget=0 must pass through as uncapped (codex major).

    The handler previously coerced 0 -> 32000 via ``... or 32_000``, so the
    documented uncapped mode (core treats ``budget <= 0`` as no cap) was
    unreachable via MCP. budget=0 must now match the uncapped core semantics:
    every reachable node is selected.
    """
    graph_path, _node_ids = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    uncapped = server.call_tool(
        "compile_context",
        {"seeds": ["Paper:focal"], "query": "focal paper method",
         "depth": 3, "budget": 0},
    )
    tiny = server.call_tool(
        "compile_context",
        {"seeds": ["Paper:focal"], "query": "focal paper method",
         "depth": 3, "budget": 50},
    )
    # Uncapped selects strictly more than a tiny cap, proving 0 != 32000-default
    # and != a small budget.
    assert len(uncapped["selected_node_ids"]) > len(tiny["selected_node_ids"])
    # The schema documents 0 = uncapped (minimum lowered from 1 to 0).
    by_name = {t["name"]: t for t in server.list_tools()}
    budget_schema = by_name["compile_context"]["inputSchema"]["properties"]["budget"]
    assert budget_schema["minimum"] == 0


def test_node_context_use_ppr_limit_one_returns_neighbor_and_edge(tmp_path):
    """use_ppr=True with limit=1 returns one neighbor WITH its edge (codex minor).

    The handler requested top_k=limit then filtered out the focal node, so
    limit=1 routinely yielded zero neighbours; it also capped incident edges
    before applying the PPR neighbour set, losing edges. After the fix, top_k is
    limit+1, self-exclusion happens before the cap, and edges derive from the
    selected neighbour set over the full edge list.
    """
    graph_path, _node_ids = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    ppr = server.call_tool(
        "node_context",
        {"node_id": "Paper:focal", "use_ppr": True, "limit": 1},
    )
    assert len(ppr["neighbors"]) == 1
    neighbor_id = ppr["neighbors"][0]["id"]
    assert neighbor_id != "Paper:focal"
    # The neighbour's incident edge to the focal node is returned.
    assert len(ppr["edges"]) >= 1
    edge_endpoints = {
        (e["source"], e["target"]) for e in ppr["edges"]
    }
    assert any(
        neighbor_id in pair and "Paper:focal" in pair
        for pair in edge_endpoints
    )


def test_node_context_use_ppr_fills_limit_past_suppressed(tmp_path):
    """A high-ranked superseded neighbour must not steal a live slot (codex minor).

    Topology: focal links to two live neighbours and one superseded neighbour
    (``Method:dead``, superseded by ``Method:winner``). With limit=2 and
    use_ppr=True, the OLD logic fetched top_k=limit+1=3 then filtered self +
    suppressed, so the superseded neighbour could occupy one of the 3 slots and
    leave only 1 live neighbour. After the fix (over-fetch, filter before cap),
    use_ppr returns 2 LIVE neighbours.
    """
    focal = ResearchNode(
        id="Paper:focal", name="Focal Paper",
        type=ResearchNodeType.PAPER, description="Focal.",
    )
    live1 = ResearchNode(
        id="Claim:live1", name="Live One",
        type=ResearchNodeType.PERFORMANCE_CLAIM, description="Live neighbour 1.",
    )
    live2 = ResearchNode(
        id="Claim:live2", name="Live Two",
        type=ResearchNodeType.PERFORMANCE_CLAIM, description="Live neighbour 2.",
    )
    dead = ResearchNode(
        id="Method:dead", name="Dead Method",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="Superseded.",
    )
    winner = ResearchNode(
        id="Method:winner", name="Winner Method",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="Supersedes dead.",
    )
    graph = ResearchGraph(
        nodes=[focal, live1, live2, dead, winner],
        edges=[
            ResearchEdge(source=focal.id, target=live1.id, type="supports_claim", evidence="a"),
            ResearchEdge(source=focal.id, target=live2.id, type="supports_claim", evidence="b"),
            # dead is strongly tied to focal so it ranks high in PPR.
            ResearchEdge(source=focal.id, target=dead.id, type="uses", evidence="c"),
            ResearchEdge(source=dead.id, target=focal.id, type="references", evidence="c2"),
            # winner supersedes dead -> dead is suppressed.
            ResearchEdge(source=winner.id, target=dead.id, type="supersedes", evidence="d"),
        ],
    )
    graph_path = tmp_path / "graph.json"
    graph_path.write_text(graph.to_json(indent=2), encoding="utf-8")
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    ppr = server.call_tool(
        "node_context",
        {"node_id": "Paper:focal", "use_ppr": True, "limit": 2},
    )
    ppr_ids = {n["id"] for n in ppr["neighbors"]}
    assert "Method:dead" not in ppr_ids
    # Two LIVE neighbours returned (not under-filled by the suppressed one).
    assert len(ppr["neighbors"]) == 2
    assert ppr_ids == {"Claim:live1", "Claim:live2"}


def test_compile_context_tool_in_listing():
    tools = LLMWikiMCPServer().list_tools()
    by_name = {t["name"]: t for t in tools}
    assert "compile_context" in by_name
    props = by_name["compile_context"]["inputSchema"]["properties"]
    assert set(["query", "seeds", "depth", "budget", "synthesize"]).issubset(props)
    # node_context advertises the opt-in PPR flag.
    nc_props = by_name["node_context"]["inputSchema"]["properties"]
    assert nc_props["use_ppr"]["type"] == "boolean"


def test_compile_context_advertises_the_retrieval_knobs():
    """``compile_context`` takes these five and the dispatcher used to forward
    none of them, so they were unreachable from MCP despite being tested."""
    by_name = {t["name"]: t for t in LLMWikiMCPServer().list_tools()}
    schema = by_name["compile_context"]["inputSchema"]
    props = schema["properties"]

    assert set(
        ["strategy", "scope", "edge_type_weights", "tame_hubs", "recency_weight"]
    ).issubset(props)
    # additionalProperties is False, so an unadvertised knob is rejected at the
    # schema boundary — advertising is what makes it reachable at all.
    assert schema["additionalProperties"] is False
    assert props["strategy"]["enum"] == ["default", "hierarchical"]
    assert props["tame_hubs"]["type"] == "boolean"
    # A negative weight is indistinguishable from zero downstream (ppr.py cuts
    # on `w <= 0.0`), i.e. it silently deletes an edge class rather than
    # penalising it. Refuse it at the boundary instead of surprising the caller.
    assert props["edge_type_weights"]["additionalProperties"] == {
        "type": "number",
        "minimum": 0,
    }
    assert props["recency_weight"]["minimum"] == 0.0
    assert props["recency_weight"]["maximum"] == 1.0


def test_compile_context_forwards_edge_type_weights_to_ppr(tmp_path):
    """The knob has to reach personalized_pagerank, not just validate.

    A schema property without a matching dispatcher kwarg still validates and
    still returns a bundle — a silent no-op. Zeroing the ``uses`` class severs
    the focal paper's only outgoing edge, which demonstrably reorders the
    ranked selection.
    """
    graph_path, _ = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    call = {"seeds": ["Paper:focal"], "query": "focal paper method", "depth": 2}

    default = server.call_tool("compile_context", dict(call))
    severed = server.call_tool(
        "compile_context", dict(call, edge_type_weights={"uses": 0.0})
    )

    assert default["selected_node_ids"][0] == "Paper:focal"
    # Same membership (the neighbourhood walk still reaches them), different
    # ranking — proof the weights reached PPR rather than being dropped.
    assert set(severed["selected_node_ids"]) == set(default["selected_node_ids"])
    assert severed["selected_node_ids"] != default["selected_node_ids"]
    assert severed["selected_node_ids"][0] != "Paper:focal"


def test_compile_context_reports_which_knobs_ran(tmp_path):
    """Same honesty split search_nodes uses for ``mode``: the artifact bytes
    stay idempotent, and what actually ran is reported rather than buried."""
    graph_path, _ = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    default = server.call_tool(
        "compile_context", {"seeds": ["Paper:focal"], "query": "focal"}
    )
    tuned = server.call_tool(
        "compile_context",
        {
            "seeds": ["Paper:focal"],
            "query": "focal",
            "tame_hubs": True,
            "edge_type_weights": {"uses": 0.5},
        },
    )

    assert default["knobs"] == {
        "strategy": "default",
        "scope": None,
        "edge_type_weights": None,
        "tame_hubs": False,
        "view": None,
        "recency_weight": 0.0,
        "recency_now": None,
        # Exact-dict on purpose: a knob that runs but is not reported is the
        # bug this test exists to catch, so a new one must land here too.
        "multi_pool": False,
        "pool_reservations": None,
    }
    assert tuned["knobs"]["tame_hubs"] is True
    assert tuned["knobs"]["edge_type_weights"] == {"uses": 0.5}


def test_compile_context_recency_weight_is_not_a_dead_knob(tmp_path):
    """``recency_weight`` alone does nothing: context_compiler gates the whole
    recency block on ``recency_now is not None and recency_weight > 0``. The
    dispatcher must supply a pivot, or the knob reports as having run while
    changing nothing."""
    graph_path, _ = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool(
        "compile_context",
        {"seeds": ["Paper:focal"], "query": "focal", "recency_weight": 0.5},
    )

    assert result["knobs"]["recency_weight"] == 0.5
    assert result["knobs"]["recency_now"] is not None


def test_compile_context_rejects_an_unknown_strategy_as_a_tool_error(tmp_path):
    """compile_context validates ``strategy`` by raising; an uncaught raise in
    the dispatcher is a transport fault, not an answerable tool result."""
    graph_path, _ = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool(
        "compile_context", {"seeds": ["Paper:focal"], "strategy": "bogus"}
    )

    assert "error" in result
    assert "bogus" in result["error"]
    assert "body" not in result


def test_compile_context_scope_without_a_project_root_is_a_tool_error(tmp_path):
    """``scope`` resolves community members from the hierarchy sidecar, so a
    bare graph path cannot honour it. Say so instead of silently ignoring it."""
    graph_path, _ = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool(
        "compile_context", {"seeds": ["Paper:focal"], "scope": "CommunitySummary:nope"}
    )

    assert "error" in result
    assert "body" not in result


def test_compile_context_reports_whether_the_procedural_pools_ran(tmp_path):
    """An empty procedural pool must not look like a working one.

    Producer-scoped reservation (roadmap step 4) means the Runbook / Gotcha /
    Event / DistilledNote / ExpertiseProfile pools legitimately come back empty
    on a graph whose only such nodes are document extractions. A caller that
    cannot tell "reservation ran and found nothing" from "reservation never
    ran" is being told a silent story about its own procedural memory — the
    class of degradation step 2 existed to remove.
    """
    graph_path, _ = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    args = {"seeds": ["Paper:focal"], "query": "focal paper method", "depth": 2}
    off = server.call_tool("compile_context", dict(args))
    on = server.call_tool("compile_context", dict(args, multi_pool=True))

    assert off["knobs"].get("multi_pool") is False, (
        "the knobs report must say whether pool reservation ran; "
        f"got {off['knobs']}"
    )
    assert off["knobs"].get("pool_reservations") is None, (
        "no reservation ran, so there is nothing to report per pool"
    )
    assert on["knobs"].get("multi_pool") is True
    # This graph holds no producer-made procedural node at all, so every pool
    # is legitimately empty — and says so, pool by pool.
    assert on["knobs"].get("pool_reservations") == {
        "Runbook": None,
        "Gotcha": None,
        "Event": None,
        "DistilledNote": None,
        "ExpertiseProfile": None,
    }, f"got {on['knobs'].get('pool_reservations')!r}"


def test_compile_context_advertises_the_view_knob():
    """``view`` follows the step-1 rule: advertised in the schema (a closed
    enum — the registry names the only valid values) or unreachable at all."""
    by_name = {t["name"]: t for t in LLMWikiMCPServer().list_tools()}
    props = by_name["compile_context"]["inputSchema"]["properties"]

    assert props["view"]["enum"] == ["semantic", "temporal", "causal", "entity"]
    # No default key: absence means the full graph, same as scope.
    assert "default" not in props["view"]


def test_compile_context_forwards_view_to_the_walk(tmp_path):
    """The knob has to reach the compiler, not just validate. Every edge in
    this graph is semantic (uses / supports_claim / extends), so the entity
    view zeroes them all and the walk collapses to the seed — while the
    semantic view keeps the default membership.

    Seeds only, no query: hybrid-search hits become seeds themselves, and a
    view restricts the WALK, never seed admission — with a query the whole
    graph would enter as seeds and mask the restriction this test proves."""
    graph_path, _ = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    call = {"seeds": ["Paper:focal"], "depth": 2}

    default = server.call_tool("compile_context", dict(call))
    entity = server.call_tool("compile_context", dict(call, view="entity"))
    semantic = server.call_tool("compile_context", dict(call, view="semantic"))

    assert len(default["selected_node_ids"]) > 1
    assert entity["selected_node_ids"] == ["Paper:focal"]
    assert set(semantic["selected_node_ids"]) == set(default["selected_node_ids"])
    # And what ran is reported, not buried.
    assert default["knobs"]["view"] is None
    assert entity["knobs"]["view"] == "entity"
    assert semantic["knobs"]["view"] == "semantic"


def test_compile_context_rejects_an_unknown_view_as_a_tool_error(tmp_path):
    """The registry validates by raising; an uncaught raise in the dispatcher
    is a transport fault, not an answerable tool result."""
    graph_path, _ = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool(
        "compile_context", {"seeds": ["Paper:focal"], "view": "provenance"}
    )

    assert "error" in result
    assert "provenance" in result["error"]
    assert "body" not in result

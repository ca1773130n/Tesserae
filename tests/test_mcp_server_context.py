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
    """``view`` follows the step-1 rule: advertised in the schema (closed
    enums — the registry names the only valid values) or unreachable at all.
    Step 8 widens the shape: one name, or an array of names to fuse."""
    by_name = {t["name"]: t for t in LLMWikiMCPServer().list_tools()}
    props = by_name["compile_context"]["inputSchema"]["properties"]

    single, many = props["view"]["anyOf"]
    assert single["enum"] == ["semantic", "temporal", "causal", "entity"]
    assert many["type"] == "array"
    assert many["items"]["enum"] == ["semantic", "temporal", "causal", "entity"]
    assert many["minItems"] == 1
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


def test_compile_context_fuses_an_array_of_views(tmp_path):
    """An array runs one walk per view and fuses. Every edge here is semantic,
    so the entity lane holds only the seed — the fused result keeps the
    semantic membership, and each citation says which lanes reached it."""
    graph_path, _ = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    call = {"seeds": ["Paper:focal"], "depth": 2}

    default = server.call_tool("compile_context", dict(call))
    fused = server.call_tool(
        "compile_context", dict(call, view=["semantic", "entity"])
    )

    assert set(fused["selected_node_ids"]) == set(default["selected_node_ids"])
    assert fused["knobs"]["view"] == ["semantic", "entity"]
    by_id = {c["node_id"]: c for c in fused["citations"]}
    # The seed is reachable in BOTH lanes (a seed is always in its own
    # neighbourhood); the hop nodes only through semantic edges.
    assert by_id["Paper:focal"]["via_views"] == ("semantic", "entity")
    assert by_id["Method:hop1"]["via_views"] == ("semantic",)


def test_compile_context_citations_omit_via_views_when_no_view_ran(tmp_path):
    """The non-preview response shape is documented back-compat for
    byte-sensitive callers: a call that never heard of views must get
    citation dicts byte-identical to the pre-via_views ones — the key is
    omitted, not emitted empty."""
    graph_path, _ = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool(
        "compile_context",
        {"seeds": ["Paper:focal"], "query": "focal paper method", "depth": 2},
    )

    assert result["citations"]
    for citation in result["citations"]:
        assert "via_views" not in citation
        assert set(citation) == {
            "node_id", "node_name", "source_path", "wiki_kind"
        }


def test_compile_context_explain_adds_a_profile_and_leaves_the_default_shape(tmp_path):
    """Opt-in per-lane accounting (roadmap step 9). Off, the response is the
    exact shape byte/order-sensitive callers already depend on; on, it gains
    one ``profile`` key and nothing else moves."""
    graph_path, _ = _multihop_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    plain = server.call_tool("compile_context", {"query": "focal paper"})
    explained = server.call_tool(
        "compile_context", {"query": "focal paper", "explain": True}
    )

    assert "profile" not in plain
    assert {k: v for k, v in explained.items() if k != "profile"} == plain

    profile = explained["profile"]
    # A list, one entry per seed search — a summed number would hide which
    # sub-query cost what.
    assert isinstance(profile, list) and len(profile) == 1
    lanes = profile[0]["lanes"]
    assert set(lanes) == {"bm25", "lexical", "embedding"}
    assert profile[0]["returned"] == len(profile[0]["winners"])
    assert all(w["lanes"] for w in profile[0]["winners"])


# --------------------------------------------------------------------------- #
# node_context(keyed=true) — the same answer, fetched by key                   #
#                                                                             #
# The bar for these is not "the keyed path returns something reasonable". It   #
# is that the payload is IDENTICAL to the whole-graph payload, because the     #
# flag is sold as a cheaper route to the same answer. Each test below pins a   #
# specific way a keyed read could quietly return a different one.             #
# --------------------------------------------------------------------------- #

import json as _json

import pytest

from tesserae.persistence import SQLiteResearchGraphStore


def _keyed_project(tmp_path, graph, *, sync_mirror: bool = True):
    """Write ``graph`` the way ``ProjectWiki._publish`` writes it.

    Canonicalize ONCE, then write graph.json and the SQLite mirror from that
    same object — the exact sequence at ``project.py``'s publish step. Both
    halves matter to what is being tested here: the canonical order is what
    makes ``graph.json`` order well-defined, and ``write_graph(replace=True)``
    is the truncate-and-reinsert that stops the mirror drifting by keeping rows
    a later compile dropped.

    ``sync_mirror=False`` skips the mirror so the staleness guard can be
    tested.
    """
    published = graph.canonicalized()
    root = tmp_path / "proj"
    (root / ".tesserae").mkdir(parents=True, exist_ok=True)
    graph_path = root / ".tesserae" / "graph.json"
    graph_path.write_text(published.to_json(indent=2) + "\n", encoding="utf-8")
    if sync_mirror:
        SQLiteResearchGraphStore(root / ".tesserae" / "sqlite.db").write_graph(
            published, replace=True
        )
    return root, graph_path


def _both(server, graph_path, **args):
    """``node_context`` via the whole graph and via the keyed store."""
    base = {"graph_path": str(graph_path), **args}
    full = server._dispatch_tool("node_context", dict(base))
    keyed = server._dispatch_tool("node_context", {**base, "keyed": True})
    return full, keyed


def _suppression_graph():
    """A graph whose suppression evidence is deliberately out of 1-hop reach.

    ``Method:live`` and ``Method:dead`` are both neighbours of the focal paper.
    ``Method:dead`` is superseded by ``Method:winner``, which is NOT a
    neighbour of the focal node — so the edge that makes ``Method:dead``
    invisible hangs off the neighbour, one hop further out than the payload
    itself. A keyed read that fetched only the focal node's own edges would
    serve a superseded node as current knowledge.
    """
    nodes = [
        ResearchNode(id="Paper:focal", name="Focal Paper", type=ResearchNodeType.PAPER,
                     description="the focal paper"),
        ResearchNode(id="Method:live", name="Live Method",
                     type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="current"),
        ResearchNode(id="Method:dead", name="Dead Method",
                     type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="superseded"),
        ResearchNode(id="Method:winner", name="Winning Method",
                     type=ResearchNodeType.METHODOLOGICAL_CONCEPT, description="the winner"),
        ResearchNode(id="Claim:retracted", name="Retracted Claim",
                     type=ResearchNodeType.PERFORMANCE_CLAIM, description="withdrawn"),
        ResearchNode(id="Claim:retractor", name="Retracting Note",
                     type=ResearchNodeType.PERFORMANCE_CLAIM, description="the retraction"),
        ResearchNode(id="Concept:far", name="Far Concept",
                     type=ResearchNodeType.CONCEPT, description="two hops out"),
    ]
    edges = [
        ResearchEdge(source="Paper:focal", target="Method:live", type="uses", evidence="a"),
        ResearchEdge(source="Paper:focal", target="Method:dead", type="uses", evidence="b"),
        ResearchEdge(source="Paper:focal", target="Claim:retracted",
                     type="supports_claim", evidence="c"),
        # Suppression evidence, one hop beyond the payload:
        ResearchEdge(source="Method:winner", target="Method:dead", type="supersedes", evidence="d"),
        ResearchEdge(source="Claim:retractor", target="Claim:retracted", type="retracts", evidence="e"),
        # Pure distance, so the graph is genuinely bigger than the neighbourhood:
        ResearchEdge(source="Method:live", target="Concept:far", type="extends", evidence="f"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_keyed_node_context_matches_the_whole_graph_payload(tmp_path):
    """Byte-identical payloads for every node, on a graph with suppression.

    Iterating EVERY node rather than a chosen one is deliberate: a keyed read
    that happened to be right about the focal node and wrong about a hub, or
    right about a node with neighbours and wrong about an isolated one, would
    pass a single-node test.
    """
    graph = _suppression_graph()
    _root, graph_path = _keyed_project(tmp_path, graph)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    for node in graph.nodes:
        for extra in ({}, {"include_superseded": True}, {"limit": 1}, {"budget_chars": 300}):
            full, keyed = _both(server, graph_path, node_id=node.id, **extra)
            assert keyed == full, (
                f"keyed payload diverged for {node.id} with {extra}:\n"
                f"full  = {_json.dumps(full, indent=2, sort_keys=True)}\n"
                f"keyed = {_json.dumps(keyed, indent=2, sort_keys=True)}"
            )


def test_keyed_node_context_suppresses_a_neighbour_condemned_out_of_reach(tmp_path):
    """The suppression probe is load-bearing, not decorative.

    Pins the ACTUAL behaviour rather than only "the two paths agree": if both
    paths stopped suppressing, an equality assertion would still pass.
    """
    graph = _suppression_graph()
    _root, graph_path = _keyed_project(tmp_path, graph)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    keyed = server._dispatch_tool(
        "node_context", {"graph_path": str(graph_path), "node_id": "Paper:focal", "keyed": True}
    )
    surfaced = {n["id"] for n in keyed["neighbors"]}
    assert surfaced == {"Method:live"}
    assert "Method:dead" not in surfaced       # superseded by a non-neighbour
    assert "Claim:retracted" not in surfaced   # retracted by a non-neighbour
    assert {(e["source"], e["target"]) for e in keyed["edges"]} == {
        ("Paper:focal", "Method:live")
    }

    opted_in = server._dispatch_tool(
        "node_context",
        {"graph_path": str(graph_path), "node_id": "Paper:focal", "keyed": True,
         "include_superseded": True},
    )
    assert {n["id"] for n in opted_in["neighbors"]} == {
        "Method:live", "Method:dead", "Claim:retracted"
    }


def test_keyed_node_context_keeps_graph_json_edge_order_under_a_limit(tmp_path):
    """``limit`` slices the SAME edges the in-memory scan would slice.

    ``node_context`` filters incident edges in ``graph.edges`` order and takes
    the first N. Hand it edges in query-planner order instead and a bounded
    call returns a different, silently arbitrary subset.
    """
    focal = ResearchNode(id="Paper:hub", name="Hub", type=ResearchNodeType.PAPER,
                         description="hub")
    others = [
        ResearchNode(id=f"Concept:{i:02d}", name=f"Concept {i}",
                     type=ResearchNodeType.CONCEPT, description=f"c{i}")
        for i in range(30)
    ]
    edges = [
        ResearchEdge(source="Paper:hub", target=f"Concept:{i:02d}", type="uses",
                     evidence=f"e{i}")
        if i % 2 == 0
        else ResearchEdge(source=f"Concept:{i:02d}", target="Paper:hub", type="extends",
                          evidence=f"e{i}")
        for i in range(30)
    ]
    graph = ResearchGraph(nodes=[focal, *others], edges=edges)
    _root, graph_path = _keyed_project(tmp_path, graph)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    published = graph.canonicalized()
    expected = [
        e.evidence for e in published.edges if "Paper:hub" in (e.source, e.target)
    ]
    for limit in (1, 3, 7, 29):
        full, keyed = _both(server, graph_path, node_id="Paper:hub", limit=limit)
        assert keyed == full
        assert [e["evidence"] for e in keyed["edges"]] == expected[:limit]
    # Not vacuous: the fixture's insertion order is not the published order, so
    # a store answering in insertion order would fail the slice above.
    assert [e.evidence for e in graph.edges] != [e.evidence for e in published.edges]


def test_keyed_node_context_keeps_the_discovered_link_overlay(tmp_path):
    """An overlay edge whose partner is outside the 1-hop ring still lands.

    ``apply_overlay`` skips any edge with an endpoint absent from the view it
    is handed, so applying it to a bare neighbourhood would silently drop
    exactly the discovered connections the overlay exists to add. The keyed
    path folds the overlay partners into the seed set first; this test fails if
    that step is removed.
    """
    graph = ResearchGraph(
        nodes=[
            ResearchNode(id="Paper:focal", name="Focal", type=ResearchNodeType.PAPER,
                         description="focal"),
            ResearchNode(id="Concept:near", name="Near", type=ResearchNodeType.CONCEPT,
                         description="one hop"),
            ResearchNode(id="Concept:stranger", name="Stranger",
                         type=ResearchNodeType.CONCEPT, description="no graph edge at all"),
        ],
        edges=[ResearchEdge(source="Paper:focal", target="Concept:near", type="uses",
                            evidence="a")],
    )
    root, graph_path = _keyed_project(tmp_path, graph)
    (root / ".tesserae" / "discovered_links.json").write_text(
        _json.dumps([["Paper:focal", "Concept:stranger", 0.9]]), encoding="utf-8"
    )
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    full, keyed = _both(server, graph_path, node_id="Paper:focal")
    assert keyed == full
    assert "Concept:stranger" in {n["id"] for n in keyed["neighbors"]}


def test_keyed_node_context_resolves_names_and_merged_ids_like_the_graph(tmp_path):
    """Name lookup and the merge-ledger redirect behave identically.

    Both are ``_find_node`` behaviours the keyed path had to re-implement
    against the store, so both are pinned against the in-memory answer.
    """
    from tesserae.merge_ledger import MergeRecord, merge_ledger_path, publish_merge_ledger

    graph = _suppression_graph()
    root, graph_path = _keyed_project(tmp_path, graph)
    publish_merge_ledger(
        merge_ledger_path(root),
        [MergeRecord(loser_id="Method:retired", survivor_id="Method:live", basis="test")],
        [n.id for n in graph.nodes],
    )
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    by_name_full, by_name_keyed = _both(server, graph_path, name="Live Method")
    assert by_name_keyed == by_name_full
    assert by_name_keyed["node"]["id"] == "Method:live"

    merged_full, merged_keyed = _both(server, graph_path, node_id="Method:retired")
    assert merged_keyed == merged_full
    assert merged_keyed["status"] == "merged"
    assert merged_keyed["merged_from"] == "Method:retired"
    assert merged_keyed["merged_into"] == "Method:live"


def test_keyed_node_context_default_is_untouched(tmp_path):
    """Without ``keyed`` nothing changes — the flag is opt-in at the call site."""
    graph = _suppression_graph()
    _root, graph_path = _keyed_project(tmp_path, graph)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    # No mirror at all: the default path must not notice, let alone fail.
    (graph_path.parent / "sqlite.db").unlink()
    out = server._dispatch_tool(
        "node_context", {"graph_path": str(graph_path), "node_id": "Paper:focal"}
    )
    assert {n["id"] for n in out["neighbors"]} == {"Method:live"}


def test_keyed_node_context_refuses_rather_than_approximating(tmp_path):
    """Every shape a keyed read cannot serve fails loudly.

    Each of these would otherwise return a plausible but different answer —
    a PPR ranking over the wrong graph, an unfiltered agent view, or a payload
    read from a mirror that no longer matches graph.json. A silently different
    answer is the one failure mode this whole change must not have.
    """
    graph = _suppression_graph()
    root, graph_path = _keyed_project(tmp_path, graph)
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    base = {"graph_path": str(graph_path), "node_id": "Paper:focal", "keyed": True}

    with pytest.raises(ValueError, match="use_ppr"):
        server._dispatch_tool("node_context", {**base, "use_ppr": True})
    with pytest.raises(ValueError, match="agent"):
        server._dispatch_tool("node_context", {**base, "agent": "worker"})

    # graph.json republished without the mirror following it.
    db = root / ".tesserae" / "sqlite.db"
    import os, time as _time
    stale = db.stat().st_mtime
    os.utime(graph_path, (stale + 10, stale + 10))
    with pytest.raises(ValueError, match="NEWER than its SQLite mirror"):
        server._dispatch_tool("node_context", dict(base))

    # No mirror at all.
    db.unlink()
    os.utime(graph_path, (stale, stale))
    with pytest.raises(ValueError, match="does not exist"):
        server._dispatch_tool("node_context", dict(base))


def test_keyed_node_context_is_advertised_in_the_tool_listing(tmp_path):
    """An opt-in nobody can discover is not opt-in."""
    server = LLMWikiMCPServer()
    spec = next(t for t in server.list_tools() if t["name"] == "node_context")
    keyed = spec["inputSchema"]["properties"]["keyed"]
    assert keyed["default"] is False
    assert "use_ppr" in keyed["description"]

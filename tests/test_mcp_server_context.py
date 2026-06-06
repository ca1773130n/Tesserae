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


def test_compile_context_tool_in_listing():
    tools = LLMWikiMCPServer().list_tools()
    by_name = {t["name"]: t for t in tools}
    assert "compile_context" in by_name
    props = by_name["compile_context"]["inputSchema"]["properties"]
    assert set(["query", "seeds", "depth", "budget", "synthesize"]).issubset(props)
    # node_context advertises the opt-in PPR flag.
    nc_props = by_name["node_context"]["inputSchema"]["properties"]
    assert nc_props["use_ppr"]["type"] == "boolean"

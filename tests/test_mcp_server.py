import json

import pytest

from llm_wiki.graph_stores import SqliteGraphStore
from llm_wiki.graph_stores.url_resolver import resolve_graph_store
from llm_wiki.mcp_server import LLMWikiMCPServer, MCPRequestHandler
from llm_wiki.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def sample_graph_path(tmp_path):
    paper = ResearchNode(
        id="Paper:dual-splat",
        name="DualSplat",
        type=ResearchNodeType.PAPER,
        description="Robust Gaussian Splatting paper",
        metadata={"arxiv_id": "2601.17835"},
    )
    method = ResearchNode(
        id="MethodologicalConcept:gaussian-splatting",
        name="Gaussian Splatting",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        aliases=["3DGS"],
        description="Point-based 3D reconstruction method",
    )
    claim = ResearchNode(
        id="PerformanceClaim:best-shape",
        name="Best shape reconstruction claim",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="DualSplat reports strong shape reconstruction results",
    )
    graph = ResearchGraph(
        nodes=[paper, method, claim],
        edges=[
            ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="uses Gaussian Splatting"),
            ResearchEdge(source=paper.id, target=claim.id, type="supports_claim", evidence="reports best shape reconstruction"),
        ],
    )
    path = tmp_path / "graph.json"
    path.write_text(graph.to_json(indent=2), encoding="utf-8")
    return path


def test_mcp_server_lists_research_tools():
    tools = LLMWikiMCPServer().list_tools()

    names = {tool["name"] for tool in tools}
    assert {"schema", "graph_summary", "search_nodes", "node_context"}.issubset(names)
    search_tool = next(tool for tool in tools if tool["name"] == "search_nodes")
    assert search_tool["inputSchema"]["properties"]["query"]["type"] == "string"


def test_graph_summary_returns_counts_by_type(tmp_path):
    graph_path = sample_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    summary = server.call_tool("graph_summary", {})

    assert summary["node_count"] == 3
    assert summary["edge_count"] == 2
    assert summary["node_types"]["Paper"] == 1
    assert summary["edge_types"]["uses"] == 1


def test_search_nodes_matches_name_alias_description_and_type(tmp_path):
    graph_path = sample_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool("search_nodes", {"query": "3dgs shape", "types": ["MethodologicalConcept", "PerformanceClaim"], "limit": 5})

    names = [node["name"] for node in result["nodes"]]
    assert names == ["Gaussian Splatting", "Best shape reconstruction claim"]
    assert result["total_matches"] == 2


def test_node_context_returns_incident_edges_and_neighbor_nodes(tmp_path):
    graph_path = sample_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    context = server.call_tool("node_context", {"node_id": "Paper:dual-splat"})

    assert context["node"]["name"] == "DualSplat"
    assert {edge["type"] for edge in context["edges"]} == {"uses", "supports_claim"}
    assert {node["name"] for node in context["neighbors"]} == {"Gaussian Splatting", "Best shape reconstruction claim"}


def test_json_rpc_handler_responds_to_initialize_tools_list_and_tools_call(tmp_path):
    graph_path = sample_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)
    handler = MCPRequestHandler(server)

    init_response = handler.handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    list_response = handler.handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    call_response = handler.handle_message(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "graph_summary", "arguments": {}}}
    )

    assert init_response["result"]["serverInfo"]["name"] == "llm-wiki"
    assert any(tool["name"] == "search_nodes" for tool in list_response["result"]["tools"])
    payload = json.loads(call_response["result"]["content"][0]["text"])
    assert payload["node_count"] == 3


def test_mcp_server_exposes_temporal_fact_search_and_timeline(tmp_path):
    graph_path = sample_graph_path(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    tools = {tool["name"] for tool in server.list_tools()}
    facts = server.call_tool("search_facts", {"query": "Gaussian", "limit": 5})
    timeline = server.call_tool("timeline", {"query": "DualSplat"})

    assert {"search_facts", "timeline"}.issubset(tools)
    assert facts["total_matches"] >= 1
    assert facts["facts"][0]["predicate"] == "uses"
    assert timeline["events"]
    assert timeline["events"][0]["valid_from"]


def test_json_rpc_notifications_do_not_emit_response():
    handler = MCPRequestHandler(LLMWikiMCPServer())

    assert handler.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}) is None


def _seed_sqlite_graph_store(db_path):
    """Seed a SqliteGraphStore mirroring sample_graph_path content."""
    store = SqliteGraphStore(db_path)
    paper = ResearchNode(
        id="Paper:dual-splat",
        name="DualSplat",
        type=ResearchNodeType.PAPER,
        description="Robust Gaussian Splatting paper",
        metadata={"arxiv_id": "2601.17835"},
    )
    method = ResearchNode(
        id="MethodologicalConcept:gaussian-splatting",
        name="Gaussian Splatting",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        aliases=["3DGS"],
        description="Point-based 3D reconstruction method",
    )
    claim = ResearchNode(
        id="PerformanceClaim:best-shape",
        name="Best shape reconstruction claim",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="DualSplat reports strong shape reconstruction results",
    )
    for node in (paper, method, claim):
        store.upsert_node(node)
    store.upsert_edge(
        ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="uses Gaussian Splatting")
    )
    store.upsert_edge(
        ResearchEdge(source=paper.id, target=claim.id, type="supports_claim", evidence="reports best shape reconstruction")
    )
    return store


def test_resolve_graph_store_sqlite_url(tmp_path):
    """Sqlite URL resolves to SqliteGraphStore."""
    db = tmp_path / "g.db"
    store = resolve_graph_store(f"sqlite:///{db}")
    assert isinstance(store, SqliteGraphStore)


def test_resolve_graph_store_postgres_url_raises_notimplementederror():
    """Postgres URLs are reserved for the HypePaper integration."""
    with pytest.raises(NotImplementedError, match="HypePaper"):
        resolve_graph_store("postgresql://localhost/x")
    with pytest.raises(NotImplementedError):
        resolve_graph_store("postgres://localhost/x")
    with pytest.raises(NotImplementedError):
        resolve_graph_store("postgresql+asyncpg://localhost/x")
    with pytest.raises(NotImplementedError):
        resolve_graph_store("hypepaper-postgres://localhost/x")


def test_resolve_graph_store_unknown_scheme_raises_valueerror():
    """Unknown schemes raise ValueError."""
    with pytest.raises(ValueError, match="Unsupported"):
        resolve_graph_store("redis://localhost/0")


def test_mcp_server_search_nodes_with_graph_store(tmp_path):
    """End-to-end: server backed by SqliteGraphStore returns search results."""
    db = tmp_path / "g.db"
    _seed_sqlite_graph_store(db)
    server = LLMWikiMCPServer(graph_store=SqliteGraphStore(db))

    result = server.call_tool("search_nodes", {"query": "Gaussian", "limit": 5})

    names = [node["name"] for node in result["nodes"]]
    assert "Gaussian Splatting" in names


def test_mcp_server_graph_summary_with_graph_store(tmp_path):
    """Graph summary works against a GraphStore-backed server."""
    db = tmp_path / "g.db"
    _seed_sqlite_graph_store(db)
    server = LLMWikiMCPServer(graph_store=SqliteGraphStore(db))

    summary = server.call_tool("graph_summary", {})

    assert summary["node_count"] == 3
    assert summary["edge_count"] == 2
    assert summary["node_types"]["Paper"] == 1
    assert summary["edge_types"]["uses"] == 1


def test_mcp_server_node_context_with_graph_store(tmp_path):
    """node_context resolves edges and neighbors via GraphStore."""
    db = tmp_path / "g.db"
    _seed_sqlite_graph_store(db)
    server = LLMWikiMCPServer(graph_store=SqliteGraphStore(db))

    context = server.call_tool("node_context", {"node_id": "Paper:dual-splat"})

    assert context["node"]["name"] == "DualSplat"
    assert {edge["type"] for edge in context["edges"]} == {"uses", "supports_claim"}
    assert {node["name"] for node in context["neighbors"]} == {"Gaussian Splatting", "Best shape reconstruction claim"}


def test_main_accepts_graph_store_url_flag(tmp_path, monkeypatch):
    """The CLI accepts --graph-store-url and resolves it without erroring before serve."""
    from llm_wiki import mcp_server as mcp_module

    db = tmp_path / "g.db"
    _seed_sqlite_graph_store(db)

    captured = {}

    def fake_serve(server, *args, **kwargs):
        captured["server"] = server

    monkeypatch.setattr(mcp_module, "serve_stdio", fake_serve)
    rc = mcp_module.main(["--graph-store-url", f"sqlite:///{db}"])
    assert rc == 0
    assert captured["server"].graph_store is not None
    assert isinstance(captured["server"].graph_store, SqliteGraphStore)

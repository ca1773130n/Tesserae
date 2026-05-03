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


def test_resolve_graph_store_postgres_url_requires_hypepaper(monkeypatch):
    """Postgres URLs lazy-import the HypePaper backend.

    When the HypePaper backend is NOT importable (the LLM-Wiki repo's
    standalone test environment), resolve_graph_store should raise
    ImportError with a clear message pointing at the HypePaper
    integration. When it IS importable, it should return a
    GraphStore-conforming wrapper.
    """
    import builtins
    import importlib

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        # Block the HypePaper-side imports so we exercise the ImportError branch
        if name.startswith("src.features.wiki") or name.startswith("src.core.database"):
            raise ImportError(f"No module named {name!r} (test stub)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # Force re-import of the resolver under the fake_import scope so the
    # lazy import inside resolve_graph_store triggers our stub.
    importlib.invalidate_caches()

    for url in (
        "postgresql://localhost/x",
        "postgres://localhost/x",
        "postgresql+asyncpg://localhost/x",
        "hypepaper-postgres://localhost/x",
    ):
        with pytest.raises(ImportError, match="HypePaper"):
            resolve_graph_store(url)


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


def test_main_auth_token_resolves_user_and_scopes_postgres_store(monkeypatch):
    """--auth-token resolves to a user_id and is forwarded to the resolver.

    Mocks both the HypePaper-side token lookup and the resolver so we
    exercise the CLI plumbing without needing the HypePaper backend
    importable in the LLM-Wiki test environment.
    """
    from llm_wiki import mcp_server as mcp_module

    captured = {}

    # Stub the auth-token lookup to return a stable user_id.
    monkeypatch.setattr(
        mcp_module,
        "_resolve_auth_token_to_user_id",
        lambda token: "11111111-2222-3333-4444-555555555555",
    )

    # Stub the resolver to capture the owner_user_id keyword.
    def fake_resolve_graph_store(url, *, owner_user_id=None):
        captured["url"] = url
        captured["owner_user_id"] = owner_user_id
        # Return a sentinel so LLMWikiMCPServer accepts it.
        sentinel = object()
        return sentinel

    monkeypatch.setattr(
        "llm_wiki.graph_stores.url_resolver.resolve_graph_store",
        fake_resolve_graph_store,
    )

    def fake_serve(server, *args, **kwargs):
        captured["server"] = server

    monkeypatch.setattr(mcp_module, "serve_stdio", fake_serve)

    rc = mcp_module.main(
        [
            "--graph-store-url",
            "hypepaper-postgres://user:pw@localhost/hypepaper",
            "--auth-token",
            "tok_abc123",
        ]
    )

    assert rc == 0
    assert captured["url"] == "hypepaper-postgres://user:pw@localhost/hypepaper"
    assert captured["owner_user_id"] == "11111111-2222-3333-4444-555555555555"


def test_main_auth_token_rejects_invalid_token(monkeypatch):
    """When --auth-token is invalid, main exits with a clear RuntimeError."""
    from llm_wiki import mcp_server as mcp_module

    def fake_resolver(token):
        raise RuntimeError("Auth token is invalid, expired, or revoked.")

    monkeypatch.setattr(mcp_module, "_resolve_auth_token_to_user_id", fake_resolver)

    with pytest.raises(RuntimeError, match="invalid"):
        mcp_module.main(
            [
                "--graph-store-url",
                "hypepaper-postgres://localhost/x",
                "--auth-token",
                "bogus",
            ]
        )


# ---------------------------------------------------------------------------
# Modernized MCP surface: ontology-aware filters and code-graph exclusion
# ---------------------------------------------------------------------------


def _project_with_wiki_and_lint(tmp_path):
    """Build a tmp project with .llm-wiki/graph.json + a wiki page + lint-report.

    Mirrors the canonical layout (``<root>/.llm-wiki/...``) so the MCP
    server's project-root inference and filesystem-backed tools (wiki_page,
    raw_source, lint_report) all resolve correctly. Includes a ``Synthesis``
    node with both ``synthesizes`` and ``summarizes`` edges and a
    ``CodeFunction`` so we can assert it never surfaces in search results.
    """
    paper = ResearchNode(
        id="Paper:vision-paper",
        name="Vision Paper",
        type=ResearchNodeType.PAPER,
        description="A paper about computer vision.",
        metadata={"arxiv_id": "2026.00001", "title_quality": "verified"},
    )
    concept = ResearchNode(
        id="MethodologicalConcept:gaussian-splatting",
        name="Gaussian Splatting",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
        description="3D reconstruction method.",
    )
    syn = ResearchNode(
        id="Synthesis:pulse:abc",
        name="Daily Pulse",
        type=ResearchNodeType.SYNTHESIS,
        description="Synthesis prose tying things together.",
        metadata={"synthesis_kind": "pulse"},
    )
    # Code-graph node — must never appear in MCP search results.
    code_fn = ResearchNode(
        id="CodeFunction:llm_wiki/example.py:vision_helper",
        name="vision_helper",
        type=ResearchNodeType.CODE_FUNCTION,
        description="Helper for the Vision Paper code path.",
    )
    graph = ResearchGraph(
        nodes=[paper, concept, syn, code_fn],
        edges=[
            ResearchEdge(source=paper.id, target=concept.id, type="uses"),
            ResearchEdge(source=syn.id, target=paper.id, type="synthesizes"),
            ResearchEdge(source=syn.id, target=concept.id, type="summarizes"),
        ],
    )
    project_root = tmp_path / "proj"
    wiki_dir = project_root / ".llm-wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    graph_path = wiki_dir / "graph.json"
    graph_path.write_text(graph.to_json(indent=2), encoding="utf-8")

    # Render a minimal wiki page for the Paper.
    papers_dir = wiki_dir / "wiki" / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    (papers_dir / "vision-paper.md").write_text(
        "---\ntitle: Vision Paper\nkind: papers\nnode_id: Paper:vision-paper\n---\n"
        "# Vision Paper\n\nThis paper introduces [[Gaussian Splatting]] for 3D vision.\n"
        "See also [related work](concepts/gaussian-splatting.md) and https://arxiv.org/abs/2026.00001.\n",
        encoding="utf-8",
    )

    # And a wiki page for the Synthesis (to exercise wiki_page on Synthesis).
    syn_dir = wiki_dir / "wiki" / "syntheses"
    syn_dir.mkdir(parents=True, exist_ok=True)
    (syn_dir / "daily-pulse.md").write_text(
        "---\ntitle: Daily Pulse\nkind: syntheses\n---\n# Daily Pulse\n\nSummary body.\n",
        encoding="utf-8",
    )

    # And a raw source file behind the paper.
    src_dir = project_root / "data" / "research" / "weekly" / "2026-W18"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "raw.md").write_text("# Raw paper notes\n\nbody body body\n", encoding="utf-8")

    # And a lint report.
    (wiki_dir / "lint-report.md").write_text(
        "# Lint report\n\n## Summary\n\n- Total findings: 0\n",
        encoding="utf-8",
    )

    return project_root, graph_path


def test_search_nodes_honours_singular_type_filter(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool("search_nodes", {"type": "Paper", "q": "vision"})

    types = {node["type"] for node in result["nodes"]}
    assert types == {"Paper"}
    assert all("vision" in (node["name"] + node.get("description", "")).lower() for node in result["nodes"])


def test_search_nodes_honours_kind_filter(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool("search_nodes", {"kind": "syntheses"})

    types = {node["type"] for node in result["nodes"]}
    assert types == {"Synthesis"}


def test_search_nodes_excludes_code_graph_nodes_even_on_name_match(tmp_path):
    """CodeFunction must never surface, even when q matches its name verbatim."""
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    result = server.call_tool("search_nodes", {"q": "vision_helper"})

    assert result["total_matches"] == 0
    assert all(node["type"] != "CodeFunction" for node in result["nodes"])


def test_graph_summary_excludes_code_graph_types(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    summary = server.call_tool("graph_summary", {})

    assert "CodeFunction" not in summary["node_types"]
    # Paper + Concept + Synthesis = 3 (CodeFunction filtered).
    assert summary["node_count"] == 3


def test_schema_omits_code_graph_types_and_lists_wiki_kinds(tmp_path):
    server = LLMWikiMCPServer()
    schema = server.call_tool("schema", {})

    for hidden in ("CodeProject", "SourceFile", "CodeClass", "CodeFunction", "CodeModule", "Dependency"):
        assert hidden not in schema["node_types"], f"{hidden} leaked into MCP schema"
    for public_type in ("Paper", "Repository", "Concept", "Synthesis", "OpenQuestion", "SourceDocument"):
        assert public_type in schema["node_types"]
    assert "wiki_kinds" in schema
    assert "papers" in schema["wiki_kinds"]
    assert "syntheses" in schema["wiki_kinds"]


def test_node_context_for_synthesis_returns_synthesizes_and_summarizes_edges(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    ctx = server.call_tool("node_context", {"node_id": "Synthesis:pulse:abc"})

    edge_types = {edge["type"] for edge in ctx["edges"]}
    assert {"synthesizes", "summarizes"}.issubset(edge_types)
    neighbour_names = {n["name"] for n in ctx["neighbors"]}
    assert {"Vision Paper", "Gaussian Splatting"}.issubset(neighbour_names)


def test_wiki_page_returns_body_and_internal_links(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    page = server.call_tool("wiki_page", {"node_id": "Paper:vision-paper"})

    assert page["kind"] == "papers"
    assert page["slug"] == "vision-paper"
    assert "Vision Paper" in page["body"]
    hrefs = {link["href"] for link in page["internal_links"]}
    assert "Gaussian Splatting" in hrefs  # wikilink
    assert any(link["kind"] == "wikilink" for link in page["internal_links"])
    # External https link must not be in internal_links.
    assert all(not link["href"].startswith("http") for link in page["internal_links"])


def test_wiki_page_unknown_node_id_raises_clear_error(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    with pytest.raises(ValueError, match="not found"):
        server.call_tool("wiki_page", {"node_id": "Paper:does-not-exist"})


def test_wiki_page_for_node_without_public_kind_raises(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    with pytest.raises(ValueError, match="no public wiki page|wiki_page"):
        server.call_tool(
            "wiki_page",
            {"node_id": "CodeFunction:llm_wiki/example.py:vision_helper"},
        )


def test_raw_source_returns_markdown_body(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    out = server.call_tool(
        "raw_source",
        {"source_path": "data/research/weekly/2026-W18/raw.md"},
    )

    assert "Raw paper notes" in out["body"]
    assert out["truncated"] is False
    assert out["byte_count"] > 0


def test_raw_source_rejects_path_escape(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    with pytest.raises(ValueError, match="escapes|outside|not found"):
        server.call_tool("raw_source", {"source_path": "../../../etc/passwd"})


def test_lint_report_returns_body_when_present(tmp_path):
    _, graph_path = _project_with_wiki_and_lint(tmp_path)
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    out = server.call_tool("lint_report", {})

    assert out["exists"] is True
    assert "Lint report" in out["body"]
    assert out["byte_count"] > 0


def test_lint_report_returns_empty_when_absent(tmp_path):
    """A project with no lint-report.md returns exists=False with empty body."""
    project_root, graph_path = _project_with_wiki_and_lint(tmp_path)
    (project_root / ".llm-wiki" / "lint-report.md").unlink()
    server = LLMWikiMCPServer(default_graph_path=graph_path)

    out = server.call_tool("lint_report", {})

    assert out["exists"] is False
    assert out["body"] == ""
    assert out["byte_count"] == 0


def test_new_tools_listed_in_tool_registry():
    tools = LLMWikiMCPServer().list_tools()
    names = {tool["name"] for tool in tools}
    assert {"wiki_page", "raw_source", "lint_report"}.issubset(names)

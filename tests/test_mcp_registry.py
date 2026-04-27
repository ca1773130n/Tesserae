"""Tests for the multi-project registry (Serena-style) on the MCP server."""
import json
from pathlib import Path

import pytest

from llm_wiki.mcp_server import LLMWikiMCPServer
from llm_wiki.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def _write_graph(graph_dir: Path) -> Path:
    paper = ResearchNode(
        id="Paper:p1",
        name="P1",
        type=ResearchNodeType.PAPER,
    )
    method = ResearchNode(
        id="MethodologicalConcept:m1",
        name="M1",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
    )
    graph = ResearchGraph(
        nodes=[paper, method],
        edges=[ResearchEdge(source=paper.id, target=method.id, type="uses")],
    )
    graph_dir.mkdir(parents=True, exist_ok=True)
    out = graph_dir / "graph.json"
    out.write_text(graph.to_json(indent=2), encoding="utf-8")
    return out


def _make_project(tmp_path: Path, name: str) -> Path:
    """Create <tmp>/<name>/.llm-wiki/graph.json and return the project root."""
    root = tmp_path / name
    _write_graph(root / ".llm-wiki")
    return root


def _server_with_registry(tmp_path: Path) -> LLMWikiMCPServer:
    return LLMWikiMCPServer(registry_path=tmp_path / "registry.json")


# ---------------------------------------------------------------------------
# tools/list exposure
# ---------------------------------------------------------------------------

def test_registry_tools_are_listed():
    tools = LLMWikiMCPServer().list_tools()
    names = {tool["name"] for tool in tools}
    assert {
        "list_projects",
        "register_project",
        "activate_project",
        "unregister_project",
    }.issubset(names)


def test_existing_tools_advertise_optional_project_argument():
    tools = LLMWikiMCPServer().list_tools()
    by_name = {tool["name"]: tool for tool in tools}
    for tool_name in ("graph_summary", "search_nodes", "node_context", "search_facts", "timeline"):
        props = by_name[tool_name]["inputSchema"]["properties"]
        assert "project" in props, f"{tool_name} should accept optional 'project' argument"
        assert props["project"]["type"] == "string"


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------

def test_list_projects_empty_registry(tmp_path):
    server = _server_with_registry(tmp_path)
    result = server.call_tool("list_projects", {})
    assert result == {"active": None, "projects": []}


# ---------------------------------------------------------------------------
# register_project
# ---------------------------------------------------------------------------

def test_register_project_from_project_root_discovers_graph(tmp_path):
    project_root = _make_project(tmp_path, "alpha")
    server = _server_with_registry(tmp_path)

    entry = server.call_tool("register_project", {"path": str(project_root)})

    assert entry["name"] == "alpha"
    assert entry["root"] == str(project_root.resolve())
    assert Path(entry["graph_path"]).is_file()


def test_register_project_from_dotllmwiki_dir(tmp_path):
    project_root = _make_project(tmp_path, "beta")
    server = _server_with_registry(tmp_path)

    entry = server.call_tool(
        "register_project", {"path": str(project_root / ".llm-wiki")}
    )

    assert entry["name"] == "beta"
    assert entry["root"] == str(project_root.resolve())


def test_register_project_from_graph_json_path(tmp_path):
    project_root = _make_project(tmp_path, "gamma")
    graph_json = project_root / ".llm-wiki" / "graph.json"
    server = _server_with_registry(tmp_path)

    entry = server.call_tool("register_project", {"path": str(graph_json)})

    assert entry["graph_path"] == str(graph_json.resolve())
    assert entry["root"] == str(project_root.resolve())


def test_register_project_with_explicit_name_overrides_default(tmp_path):
    project_root = _make_project(tmp_path, "delta")
    server = _server_with_registry(tmp_path)

    entry = server.call_tool(
        "register_project", {"path": str(project_root), "name": "my_alias"}
    )

    assert entry["name"] == "my_alias"
    listed = server.call_tool("list_projects", {})
    assert [p["name"] for p in listed["projects"]] == ["my_alias"]


def test_register_project_persists_to_registry_file(tmp_path):
    project_root = _make_project(tmp_path, "epsilon")
    registry_path = tmp_path / "registry.json"
    server = LLMWikiMCPServer(registry_path=registry_path)

    server.call_tool("register_project", {"path": str(project_root)})

    payload = json.loads(registry_path.read_text())
    assert "epsilon" in payload["projects"]


def test_register_project_is_idempotent(tmp_path):
    project_root = _make_project(tmp_path, "zeta")
    server = _server_with_registry(tmp_path)

    server.call_tool("register_project", {"path": str(project_root)})
    server.call_tool("register_project", {"path": str(project_root)})

    listed = server.call_tool("list_projects", {})
    names = [p["name"] for p in listed["projects"]]
    assert names.count("zeta") == 1


def test_register_project_rejects_path_without_graph(tmp_path):
    bare = tmp_path / "no_wiki"
    bare.mkdir()
    server = _server_with_registry(tmp_path)

    with pytest.raises(Exception) as excinfo:
        server.call_tool("register_project", {"path": str(bare)})
    assert "graph" in str(excinfo.value).lower() or "no .llm-wiki" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# activate_project
# ---------------------------------------------------------------------------

def test_activate_project_sets_active_in_registry(tmp_path):
    project_root = _make_project(tmp_path, "eta")
    server = _server_with_registry(tmp_path)
    server.call_tool("register_project", {"path": str(project_root)})

    activated = server.call_tool("activate_project", {"name": "eta"})

    assert activated["name"] == "eta"
    listed = server.call_tool("list_projects", {})
    assert listed["active"] == "eta"


def test_activate_project_unknown_name_raises(tmp_path):
    server = _server_with_registry(tmp_path)
    with pytest.raises(Exception):
        server.call_tool("activate_project", {"name": "does_not_exist"})


# ---------------------------------------------------------------------------
# unregister_project
# ---------------------------------------------------------------------------

def test_unregister_project_removes_entry(tmp_path):
    project_root = _make_project(tmp_path, "theta")
    server = _server_with_registry(tmp_path)
    server.call_tool("register_project", {"path": str(project_root)})

    server.call_tool("unregister_project", {"name": "theta"})

    listed = server.call_tool("list_projects", {})
    assert listed["projects"] == []


def test_unregister_active_project_clears_active(tmp_path):
    project_root = _make_project(tmp_path, "iota")
    server = _server_with_registry(tmp_path)
    server.call_tool("register_project", {"path": str(project_root)})
    server.call_tool("activate_project", {"name": "iota"})

    server.call_tool("unregister_project", {"name": "iota"})

    listed = server.call_tool("list_projects", {})
    assert listed["active"] is None


# ---------------------------------------------------------------------------
# Resolution priority in tool calls
# ---------------------------------------------------------------------------

def test_tool_call_resolves_project_arg_via_registry(tmp_path):
    p_root = _make_project(tmp_path, "kappa")
    server = _server_with_registry(tmp_path)
    server.call_tool("register_project", {"path": str(p_root)})

    summary = server.call_tool("graph_summary", {"project": "kappa"})

    assert summary["node_count"] == 2
    assert summary["edge_count"] == 1


def test_tool_call_falls_back_to_active_project(tmp_path):
    p_root = _make_project(tmp_path, "lambda")
    server = _server_with_registry(tmp_path)
    server.call_tool("register_project", {"path": str(p_root)})
    server.call_tool("activate_project", {"name": "lambda"})

    summary = server.call_tool("graph_summary", {})

    assert summary["node_count"] == 2


def test_tool_call_unknown_project_raises(tmp_path):
    server = _server_with_registry(tmp_path)
    with pytest.raises(Exception) as excinfo:
        server.call_tool("graph_summary", {"project": "ghost"})
    assert "ghost" in str(excinfo.value) or "unknown" in str(excinfo.value).lower()


def test_explicit_graph_path_takes_priority_over_active(tmp_path):
    p_active = _make_project(tmp_path, "mu")
    p_other = _make_project(tmp_path, "nu")
    server = _server_with_registry(tmp_path)
    server.call_tool("register_project", {"path": str(p_active)})
    server.call_tool("activate_project", {"name": "mu"})

    summary = server.call_tool(
        "graph_summary",
        {"graph_path": str(p_other / ".llm-wiki" / "graph.json")},
    )

    # Both fixtures have the same shape (2/1) — but explicit-path branch must not
    # raise even when active is set, which is what we're verifying here.
    assert summary["node_count"] == 2


def test_no_resolution_sources_raises(tmp_path):
    server = _server_with_registry(tmp_path)
    with pytest.raises(Exception) as excinfo:
        server.call_tool("graph_summary", {})
    msg = str(excinfo.value).lower()
    assert "graph" in msg


# ---------------------------------------------------------------------------
# Backward compat
# ---------------------------------------------------------------------------

def test_default_graph_path_still_works_when_no_registry_used(tmp_path):
    p_root = _make_project(tmp_path, "xi")
    graph = p_root / ".llm-wiki" / "graph.json"
    server = LLMWikiMCPServer(
        default_graph_path=graph, registry_path=tmp_path / "registry.json"
    )

    summary = server.call_tool("graph_summary", {})

    assert summary["node_count"] == 2

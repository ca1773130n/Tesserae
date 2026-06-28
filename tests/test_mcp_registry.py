"""Tests for the multi-project registry (Serena-style) on the MCP server."""
import json
from pathlib import Path

import pytest

from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


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
    """Create <tmp>/<name>/.tesserae/graph.json and return the project root."""
    root = tmp_path / name
    _write_graph(root / ".tesserae")
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
        "unregister_project",
    }.issubset(names)
    assert "activate_project" not in names  # active-project concept removed


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
    assert result == {"projects": []}


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
        "register_project", {"path": str(project_root / ".tesserae")}
    )

    assert entry["name"] == "beta"
    assert entry["root"] == str(project_root.resolve())


def test_register_project_from_graph_json_path(tmp_path):
    project_root = _make_project(tmp_path, "gamma")
    graph_json = project_root / ".tesserae" / "graph.json"
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
    assert "graph" in str(excinfo.value).lower() or "no .tesserae" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# activate_project
# ---------------------------------------------------------------------------

def test_activate_project_tool_is_gone(tmp_path):
    # active-project concept removed: the tool no longer exists.
    server = _server_with_registry(tmp_path)
    with pytest.raises(Exception):
        server.call_tool("activate_project", {"name": "anything"})


def test_resolve_project_by_cwd_and_all_names(tmp_path):
    from tesserae.mcp_server import ProjectRegistry

    reg = ProjectRegistry(tmp_path / "reg.json")
    reg.register(str(_make_project(tmp_path, "alpha")))
    reg.register(str(_make_project(tmp_path, "beta")))
    assert reg.all_project_names() == ["alpha", "beta"]
    # standing deep inside alpha resolves to alpha's root
    assert reg.resolve_project_by_cwd(tmp_path / "alpha" / "src" / "deep") == (tmp_path / "alpha").resolve()
    # outside every registered root -> None (the new "no active" state)
    assert reg.resolve_project_by_cwd(tmp_path / "nowhere") is None


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


def test_tool_call_unknown_project_raises(tmp_path):
    server = _server_with_registry(tmp_path)
    with pytest.raises(Exception) as excinfo:
        server.call_tool("graph_summary", {"project": "ghost"})
    assert "ghost" in str(excinfo.value) or "unknown" in str(excinfo.value).lower()


def test_explicit_graph_path_takes_priority(tmp_path):
    p_other = _make_project(tmp_path, "nu")
    server = _server_with_registry(tmp_path)
    server.call_tool("register_project", {"path": str(_make_project(tmp_path, "mu"))})

    summary = server.call_tool(
        "graph_summary",
        {"graph_path": str(p_other / ".tesserae" / "graph.json")},
    )

    # explicit graph_path wins over any cwd/default resolution.
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
    graph = p_root / ".tesserae" / "graph.json"
    server = LLMWikiMCPServer(
        default_graph_path=graph, registry_path=tmp_path / "registry.json"
    )

    summary = server.call_tool("graph_summary", {})

    assert summary["node_count"] == 2

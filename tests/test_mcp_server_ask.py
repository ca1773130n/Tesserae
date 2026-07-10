"""Tests for the MCP `ask` tool that mirrors the top-level ``tesserae ask``.

Contract (spec §1): ask = LLM-planned answer over the compiled graph, with a
boolean ``llm`` knob (default true, matching the CLI). The old ``backend`` /
``claude_config_dir`` params were removed — explicit raganything/cognee
retrieval lives on ``tesserae query --backend ...``.
"""
import json
from pathlib import Path

import pytest


def _write_minimal_project(project: Path, *, raganything_enabled: bool = True, cognee_enabled: bool = False) -> None:
    """Create a minimal .tesserae layout with a graph.json so the MCP registry accepts it."""
    cfg_dir = project / ".tesserae"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "name": "demo",
        "sources": ["README.md"],
        "external_tools": [],
        "memory_backends": {
            "raganything": {
                "enabled": raganything_enabled,
                "working_dir": "wd",
                "parser": "docling",
                "query_mode": "hybrid",
            },
            "cognee": {"enabled": cognee_enabled},
        },
    }
    (cfg_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    # Empty but valid graph.json so register_project accepts the path.
    (cfg_dir / "graph.json").write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
    (project / "README.md").write_text("# demo", encoding="utf-8")


def test_mcp_lists_ask_tool():
    from tesserae.mcp_server import LLMWikiMCPServer

    tools = LLMWikiMCPServer().list_tools()
    by_name = {tool["name"]: tool for tool in tools}
    assert "ask" in by_name
    schema = by_name["ask"]["inputSchema"]
    assert "question" in schema["properties"]
    assert "question" in schema["required"]
    # Backend selection left ask entirely (moved to `tesserae query`); the
    # claude_config_dir raganything shim went with it.
    assert "backend" not in schema["properties"]
    assert "claude_config_dir" not in schema["properties"]
    # The llm knob defaults ON, in lockstep with the CLI.
    assert schema["properties"]["llm"]["type"] == "boolean"
    assert schema["properties"]["llm"]["default"] is True
    # top_k default unified at 8 across ask/query surfaces.
    assert schema["properties"]["top_k"]["default"] == 8


def test_mcp_ask_requires_question(tmp_path):
    from tesserae.mcp_server import LLMWikiMCPServer

    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    with pytest.raises(ValueError, match="ask requires 'question'"):
        server.call_tool("ask", {"question": "  "})


def test_mcp_ask_backend_param_rejected(tmp_path):
    """Passing the removed `backend` param errors with a pointer at `query`."""
    from tesserae.mcp_server import LLMWikiMCPServer

    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    with pytest.raises(ValueError, match="has moved → tesserae query"):
        server.call_tool("ask", {"question": "hello?", "backend": "raganything"})


def test_mcp_ask_claude_config_dir_param_rejected(tmp_path):
    from tesserae.mcp_server import LLMWikiMCPServer

    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    with pytest.raises(ValueError, match="claude_config_dir"):
        server.call_tool(
            "ask", {"question": "hello?", "claude_config_dir": "/tmp/claude"}
        )


def test_mcp_ask_never_enters_raganything(tmp_path, monkeypatch):
    """Even with raganything enabled in config, ask goes straight to the
    wiki/planner path (auto never enters optional backends)."""
    from tesserae.mcp_server import LLMWikiMCPServer

    project = tmp_path / "demo"
    _write_minimal_project(project, raganything_enabled=True, cognee_enabled=False)

    import tesserae.raganything_query as rq

    def _boom(*a, **k):
        raise AssertionError("ask must not call raganything")

    monkeypatch.setattr(rq, "query", _boom)

    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    server.registry.register(str(project), name="demo")

    result = server.call_tool("ask", {"question": "anything", "project": "demo"})
    assert result["backend"] == "wiki"
    assert result["question"] == "anything"


def test_mcp_ask_llm_defaults_true_and_threads(tmp_path, monkeypatch):
    from tesserae.mcp_server import LLMWikiMCPServer

    project = tmp_path / "demo"
    _write_minimal_project(project)

    captured = {}

    def fake_ask(wiki, question, **kwargs):
        captured["question"] = question
        captured.update(kwargs)
        return {"backend": "wiki", "question": question, "answer": "a", "hits": [], "used_llm": True}

    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)

    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    server.registry.register(str(project), name="demo")

    result = server.call_tool("ask", {"question": "hello?", "project": "demo"})
    assert result["used_llm"] is True
    assert captured["use_llm"] is True
    assert captured["no_llm"] is False
    assert captured["top_k"] == 8


def test_mcp_ask_llm_false_pins_search_only(tmp_path, monkeypatch):
    """llm:false is the MCP equivalent of --no-llm: force-off, beats env."""
    from tesserae.mcp_server import LLMWikiMCPServer

    project = tmp_path / "demo"
    _write_minimal_project(project)
    monkeypatch.setenv("TESSERAE_QUERY_LLM", "1")

    captured = {}

    def fake_ask(wiki, question, **kwargs):
        captured.update(kwargs)
        return {"backend": "wiki", "question": question, "answer": None, "hits": [], "used_llm": False}

    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)

    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    server.registry.register(str(project), name="demo")

    result = server.call_tool("ask", {"question": "hello?", "project": "demo", "llm": False})
    assert result["used_llm"] is False
    assert captured["use_llm"] is False
    assert captured["no_llm"] is True


def test_mcp_ask_llm_false_end_to_end_skips_planner(tmp_path, monkeypatch):
    """No ask_project stub: llm:false must return the search-only wiki envelope
    without ever invoking the planner."""
    from tesserae.mcp_server import LLMWikiMCPServer

    project = tmp_path / "demo"
    _write_minimal_project(project)

    def _boom(*a, **k):
        raise AssertionError("planner must not run when llm=false")

    monkeypatch.setattr("tesserae.ask_planner.plan_and_answer", _boom)

    server = LLMWikiMCPServer(registry_path=tmp_path / "registry.json")
    server.registry.register(str(project), name="demo")

    result = server.call_tool("ask", {"question": "anything", "project": "demo", "llm": False})
    assert result["backend"] == "wiki"
    assert result["used_llm"] is False

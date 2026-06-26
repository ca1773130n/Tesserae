"""Tests for ``tesserae ask --scope`` (B2 — cross-project fan-out).

The ``--scope all-registered`` mode iterates every registered project
in the persistent registry, calls ``ask_project`` against each, and
aggregates the responses into a single envelope keyed by alias.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_shared_graph(proj: Path, prefix: str, desc: str) -> None:
    """Overwrite a project's graph.json with a Paper (shared arxiv) + a Concept."""
    graph = {
        "nodes": [
            {"id": f"Paper:t:{prefix}", "name": "arXiv:1706.03762", "type": "Paper",
             "aliases": [], "description": "", "source_path": None,
             "metadata": {"arxiv_id": "1706.03762"}},
            {"id": f"Concept:c:{prefix}", "name": "Attention", "type": "Concept",
             "aliases": [], "description": desc, "source_path": None, "metadata": {}},
        ],
        "edges": [{"source": f"Concept:c:{prefix}", "target": f"Paper:t:{prefix}",
                   "type": "references", "evidence": None, "metadata": {}}],
    }
    (proj / ".tesserae" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")


def _bootstrap_project(root: Path, name: str) -> Path:
    """Create a minimal .tesserae layout the registry will accept."""
    proj = root / name
    proj.mkdir()
    cfg_dir = proj / ".tesserae"
    cfg_dir.mkdir()
    cfg = {
        "name": name,
        "sources": ["README.md"],
        "external_tools": [],
        "memory_backends": {},
    }
    (cfg_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (cfg_dir / "graph.json").write_text(
        json.dumps({"nodes": [], "edges": []}), encoding="utf-8"
    )
    (proj / "README.md").write_text(f"# {name}", encoding="utf-8")
    return proj


def test_top_level_ask_scope_all_registered_iterates_each_project(
    tmp_path, monkeypatch, capsys,
):
    """Each registered project gets exactly one ask_project call."""
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    p1 = _bootstrap_project(tmp_path, "p1")
    p2 = _bootstrap_project(tmp_path, "p2")
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    cli.main(["projects", "register", str(p1), "--name", "p1"])
    cli.main(["projects", "register", str(p2), "--name", "p2"])
    capsys.readouterr()

    called: list[str] = []

    def fake_ask(wiki, question, **kwargs):
        called.append(wiki.project_root.name)
        return {
            "backend": "wiki",
            "question": question,
            "answer": f"answer-from-{wiki.project_root.name}",
            "hits": [],
            "used_llm": False,
        }

    monkeypatch.setattr("tesserae.cli.ask_project", fake_ask, raising=False)
    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)

    rc = cli.main(["ask", "hello?", "--scope", "all-registered", "--json"])
    assert rc == 0
    assert sorted(called) == ["p1", "p2"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"] == "all-registered"
    assert payload["question"] == "hello?"
    assert set(payload["by_project"].keys()) == {"p1", "p2"}
    assert payload["by_project"]["p1"]["answer"] == "answer-from-p1"
    assert payload["by_project"]["p2"]["answer"] == "answer-from-p2"


def test_top_level_ask_scope_aliases_restricts_subset(
    tmp_path, monkeypatch, capsys,
):
    """--scope-aliases restricts the fan-out to a hand-picked subset."""
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    p1 = _bootstrap_project(tmp_path, "p1")
    p2 = _bootstrap_project(tmp_path, "p2")
    p3 = _bootstrap_project(tmp_path, "p3")
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    cli.main(["projects", "register", str(p1), "--name", "p1"])
    cli.main(["projects", "register", str(p2), "--name", "p2"])
    cli.main(["projects", "register", str(p3), "--name", "p3"])
    capsys.readouterr()

    called: list[str] = []

    def fake_ask(wiki, question, **kwargs):
        called.append(wiki.project_root.name)
        return {"backend": "wiki", "question": question, "answer": "x", "hits": []}

    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)

    rc = cli.main([
        "ask", "hello?",
        "--scope", "all-registered",
        "--scope-aliases", "p1", "p3",
        "--json",
    ])
    assert rc == 0
    assert sorted(called) == ["p1", "p3"]
    payload = json.loads(capsys.readouterr().out)
    assert set(payload["by_project"].keys()) == {"p1", "p3"}


def test_top_level_ask_scope_all_registered_handles_per_project_failure(
    tmp_path, monkeypatch, capsys,
):
    """One project failing must not abort the fan-out."""
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    p1 = _bootstrap_project(tmp_path, "p1")
    p2 = _bootstrap_project(tmp_path, "p2")
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    cli.main(["projects", "register", str(p1), "--name", "p1"])
    cli.main(["projects", "register", str(p2), "--name", "p2"])
    capsys.readouterr()

    def fake_ask(wiki, question, **kwargs):
        if wiki.project_root.name == "p1":
            raise RuntimeError("p1 backend exploded")
        return {"backend": "wiki", "question": question, "answer": "ok", "hits": []}

    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)

    rc = cli.main(["ask", "hello?", "--scope", "all-registered", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "error" in payload["by_project"]["p1"]
    assert "exploded" in payload["by_project"]["p1"]["error"]
    assert payload["by_project"]["p2"]["answer"] == "ok"


def test_top_level_ask_scope_all_registered_fails_when_empty(
    tmp_path, monkeypatch, capsys,
):
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    rc = cli.main(["ask", "hello?", "--scope", "all-registered"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "register" in err.lower() or "no projects" in err.lower()


def test_top_level_ask_scope_default_is_current(tmp_path, monkeypatch, capsys):
    """Default scope is 'current' so existing call sites do not regress."""
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    p1 = _bootstrap_project(tmp_path, "p1")
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)
    cli.main(["projects", "register", str(p1), "--name", "p1", "--activate"])
    capsys.readouterr()

    called: list[str] = []

    def fake_ask(wiki, question, **kwargs):
        called.append(wiki.project_root.name)
        return {"backend": "wiki", "question": question, "answer": "single", "hits": []}

    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)

    rc = cli.main(["ask", "hello?", "--json"])
    assert rc == 0
    assert called == ["p1"]
    payload = json.loads(capsys.readouterr().out)
    # Single-project envelope is NOT wrapped in by_project for back-compat.
    assert "by_project" not in payload
    assert payload["answer"] == "single"


def test_mcp_ask_scope_all_registered(tmp_path, monkeypatch):
    """The MCP ask tool exposes the same fan-out behaviour as the CLI."""
    import tesserae.mcp_server as mcp_server
    from tesserae.mcp_server import LLMWikiMCPServer

    p1 = _bootstrap_project(tmp_path, "p1")
    p2 = _bootstrap_project(tmp_path, "p2")
    registry_path = tmp_path / "registry.json"

    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)
    server = LLMWikiMCPServer(registry_path=registry_path)
    server.registry.register(str(p1), name="p1")
    server.registry.register(str(p2), name="p2")

    def fake_ask(wiki, question, **kwargs):
        return {
            "backend": "wiki",
            "question": question,
            "answer": f"a-{wiki.project_root.name}",
            "hits": [],
        }

    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)

    result = server.call_tool(
        "ask",
        {"question": "hello?", "scope": "all-registered"},
    )
    assert result["scope"] == "all-registered"
    assert set(result["by_project"].keys()) == {"p1", "p2"}
    assert result["by_project"]["p1"]["answer"] == "a-p1"


def test_top_level_ask_scope_federated_requires_aliases(tmp_path, monkeypatch, capsys):
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", tmp_path / "registry.json")
    rc = cli.main(["ask", "hi?", "--scope", "federated"])
    assert rc == 2
    assert "scope-aliases" in capsys.readouterr().err.lower()


def test_top_level_ask_scope_federated_merges_and_cross_references(tmp_path, monkeypatch, capsys):
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", tmp_path / "registry.json")
    p1 = _bootstrap_project(tmp_path, "work")
    _write_shared_graph(p1, "w", "work view of attention")
    p2 = _bootstrap_project(tmp_path, "research")
    _write_shared_graph(p2, "r", "research view of attention")
    cli.main(["projects", "register", str(p1), "--name", "work"])
    cli.main(["projects", "register", str(p2), "--name", "research"])
    capsys.readouterr()

    rc = cli.main(["ask", "what is attention", "--scope", "federated",
                   "--scope-aliases", "work", "research", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope"] == "federated"
    assert payload["stats"]["merged_groups"] == 1  # the two same-arxiv papers merged
    cited = {c["node_id"].split("::", 1)[0] for c in payload["citations"]}
    assert cited == {"work", "research"}  # a single answer spanning both projects


def test_mcp_ask_scope_federated(tmp_path, monkeypatch):
    import tesserae.mcp_server as mcp_server
    from tesserae.mcp_server import LLMWikiMCPServer

    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)
    server = LLMWikiMCPServer(registry_path=registry_path)
    p1 = _bootstrap_project(tmp_path, "work"); _write_shared_graph(p1, "w", "work")
    p2 = _bootstrap_project(tmp_path, "research"); _write_shared_graph(p2, "r", "research")
    server.registry.register(str(p1), name="work")
    server.registry.register(str(p2), name="research")

    result = server.call_tool("ask", {"question": "attention", "scope": "federated",
                                       "scope_aliases": ["work", "research"]})
    assert result["scope"] == "federated"
    assert result["stats"]["merged_groups"] == 1
    with pytest.raises(ValueError, match="requires scope_aliases"):
        server.call_tool("ask", {"question": "x", "scope": "federated"})


def test_mcp_ask_scope_enum_matches_validation():
    """Schema enum must list 'federated' (drift guard vs the handler's set)."""
    from tesserae.mcp_server import LLMWikiMCPServer

    tools = {t["name"]: t for t in LLMWikiMCPServer().list_tools()}
    enum = tools["ask"]["inputSchema"]["properties"]["scope"]["enum"]
    assert set(enum) == {"current", "all-registered", "federated"}

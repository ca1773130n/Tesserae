"""Tests for the top-level ``llm_wiki ask`` and ``llm_wiki wiki`` commands.

These exercise the new project resolution surface that hits the persistent
multi-project registry (``ProjectRegistry``) shared with the MCP server, and
the shared ``ask_project`` dispatcher used by both the top-level command and
the existing ``project ask`` handler.
"""

from __future__ import annotations

import json
from pathlib import Path


def _bootstrap_project(tmp_path: Path) -> Path:
    """Create a minimal .llm-wiki layout the registry will accept."""
    project = tmp_path / "demo"
    project.mkdir()
    cfg_dir = project / ".llm-wiki"
    cfg_dir.mkdir()
    cfg = {
        "name": "demo",
        "sources": ["README.md"],
        "external_tools": [],
        "memory_backends": {
            "raganything": {
                "enabled": True,
                "working_dir": "wd",
                "parser": "docling",
                "query_mode": "hybrid",
            }
        },
    }
    (cfg_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (project / "README.md").write_text("# demo", encoding="utf-8")
    # graph.json so ProjectRegistry.register can resolve it.
    (cfg_dir / "graph.json").write_text(
        json.dumps({"nodes": [], "edges": []}), encoding="utf-8"
    )
    return project


def test_top_level_ask_resolves_project_via_path(tmp_path, monkeypatch, capsys):
    from llm_wiki import cli
    import llm_wiki.raganything_query as rq

    project = _bootstrap_project(tmp_path)
    monkeypatch.setattr(rq, "query", lambda q, *, backend_config: "by-path-answer")

    rc = cli.main(
        [
            "ask",
            "hello?",
            "--project",
            str(project),
            "--backend",
            "raganything",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "by-path-answer" in out


def test_top_level_ask_resolves_project_via_wiki_name(tmp_path, monkeypatch, capsys):
    from llm_wiki import cli
    import llm_wiki.mcp_server as mcp_server
    import llm_wiki.raganything_query as rq

    project = _bootstrap_project(tmp_path)
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    rc = cli.main(["wiki", "register", str(project), "--name", "demo-alias"])
    assert rc == 0
    assert registry_path.exists()
    capsys.readouterr()

    monkeypatch.setattr(rq, "query", lambda q, *, backend_config: "by-name-answer")
    rc = cli.main(
        [
            "ask",
            "hello?",
            "--wiki",
            "demo-alias",
            "--backend",
            "raganything",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "by-name-answer" in out


def test_top_level_ask_uses_active_project_when_no_args(tmp_path, monkeypatch, capsys):
    from llm_wiki import cli
    import llm_wiki.mcp_server as mcp_server
    import llm_wiki.raganything_query as rq

    project = _bootstrap_project(tmp_path)
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    rc = cli.main(
        ["wiki", "register", str(project), "--name", "demo-active", "--activate"]
    )
    assert rc == 0
    capsys.readouterr()

    monkeypatch.setattr(rq, "query", lambda q, *, backend_config: "active-answer")
    rc = cli.main(["ask", "hello?", "--backend", "raganything"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "active-answer" in out


def test_top_level_ask_fails_helpfully_when_no_project(tmp_path, monkeypatch, capsys):
    from llm_wiki import cli
    import llm_wiki.mcp_server as mcp_server

    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    rc = cli.main(["ask", "hello?"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "active project" in err.lower() or "wiki list" in err.lower()


def test_top_level_ask_unknown_wiki_name(tmp_path, monkeypatch, capsys):
    from llm_wiki import cli
    import llm_wiki.mcp_server as mcp_server

    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    rc = cli.main(["ask", "hello?", "--wiki", "missing"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "missing" in err
    assert "wiki list" in err.lower() or "register" in err.lower()


def test_top_level_ask_json_envelope(tmp_path, monkeypatch, capsys):
    from llm_wiki import cli
    import llm_wiki.raganything_query as rq

    project = _bootstrap_project(tmp_path)
    monkeypatch.setattr(rq, "query", lambda q, *, backend_config: "json-answer")

    rc = cli.main(
        [
            "ask",
            "hello?",
            "--project",
            str(project),
            "--backend",
            "raganything",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["backend"] == "raganything"
    assert payload["answer"] == "json-answer"
    assert payload["question"] == "hello?"


def test_wiki_list_command(tmp_path, monkeypatch, capsys):
    from llm_wiki import cli
    import llm_wiki.mcp_server as mcp_server

    project = _bootstrap_project(tmp_path)
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    cli.main(["wiki", "register", str(project), "--name", "demo", "--activate"])
    capsys.readouterr()

    rc = cli.main(["wiki", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Active: demo" in out
    assert "* demo" in out


def test_wiki_list_json(tmp_path, monkeypatch, capsys):
    from llm_wiki import cli
    import llm_wiki.mcp_server as mcp_server

    project = _bootstrap_project(tmp_path)
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    cli.main(["wiki", "register", str(project), "--name", "demo"])
    capsys.readouterr()

    rc = cli.main(["wiki", "list", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["active"] is None
    assert any(p["name"] == "demo" for p in payload["projects"])


def test_wiki_unregister_command(tmp_path, monkeypatch, capsys):
    from llm_wiki import cli
    import llm_wiki.mcp_server as mcp_server

    project = _bootstrap_project(tmp_path)
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    cli.main(["wiki", "register", str(project), "--name", "demo"])
    capsys.readouterr()

    rc = cli.main(["wiki", "unregister", "demo"])
    assert rc == 0
    capsys.readouterr()

    rc = cli.main(["wiki", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No projects registered" in out


def test_wiki_activate_command(tmp_path, monkeypatch, capsys):
    from llm_wiki import cli
    import llm_wiki.mcp_server as mcp_server

    project = _bootstrap_project(tmp_path)
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    cli.main(["wiki", "register", str(project), "--name", "demo"])
    capsys.readouterr()

    rc = cli.main(["wiki", "activate", "demo"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Active: demo" in out

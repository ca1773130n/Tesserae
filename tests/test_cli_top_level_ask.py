"""Tests for the top-level ``tesserae ask`` and ``tesserae wiki`` commands.

These exercise the new project resolution surface that hits the persistent
multi-project registry (``ProjectRegistry``) shared with the MCP server, and
the shared ``ask_project`` dispatcher used by both the top-level command and
the existing ``project ask`` handler.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _bootstrap_project(tmp_path: Path) -> Path:
    """Create a minimal .tesserae layout the registry will accept."""
    project = tmp_path / "demo"
    project.mkdir()
    cfg_dir = project / ".tesserae"
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


def _stub_wiki_envelope(answer: str):
    """A capture-friendly ask_project stand-in returning a wiki envelope."""
    captured: dict = {}

    def fake_ask(wiki, question, **kwargs):
        captured["project_root"] = wiki.project_root
        captured["question"] = question
        captured.update(kwargs)
        return {
            "backend": "wiki",
            "question": question,
            "answer": answer,
            "hits": [],
            "used_llm": True,
        }

    return fake_ask, captured


def test_top_level_ask_resolves_project_via_path(tmp_path, monkeypatch, capsys):
    from tesserae import cli

    project = _bootstrap_project(tmp_path)
    fake_ask, captured = _stub_wiki_envelope("by-path-answer")
    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)

    rc = cli.main(["ask", "hello?", "--project", str(project)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "by-path-answer" in out
    assert captured["project_root"] == project.resolve()
    # ask defaults to the LLM-planned answer and threads the knobs through.
    assert captured["use_llm"] is True
    assert captured["no_llm"] is False


def test_top_level_ask_resolves_project_via_name(tmp_path, monkeypatch, capsys):
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    project = _bootstrap_project(tmp_path)
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    rc = cli.main(["projects", "register", str(project), "--name", "demo-alias"])
    assert rc == 0
    assert registry_path.exists()
    capsys.readouterr()

    fake_ask, _ = _stub_wiki_envelope("by-name-answer")
    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)
    rc = cli.main(["ask", "hello?", "--name", "demo-alias"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "by-name-answer" in out


def test_bare_ask_routes_to_sole_registered_project(tmp_path, monkeypatch, capsys):
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    project = _bootstrap_project(tmp_path)
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    rc = cli.main(["projects", "register", str(project), "--name", "demo-sole"])
    assert rc == 0
    capsys.readouterr()

    # No --project/--scope: the router sends a single-project registry to that project.
    fake_ask, _ = _stub_wiki_envelope("routed-answer")
    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)
    rc = cli.main(["ask", "hello?"])
    assert rc == 0
    assert "routed-answer" in capsys.readouterr().out


def test_ask_no_llm_threads_force_off(tmp_path, monkeypatch, capsys):
    """--no-llm pins search-only even with TESSERAE_QUERY_LLM=1 in the env
    (the force-off is threaded as use_llm=False + no_llm=True)."""
    from tesserae import cli

    project = _bootstrap_project(tmp_path)
    monkeypatch.setenv("TESSERAE_QUERY_LLM", "1")
    fake_ask, captured = _stub_wiki_envelope("search-only")
    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)

    rc = cli.main(["ask", "hello?", "--project", str(project), "--no-llm"])
    assert rc == 0
    assert captured["use_llm"] is False
    assert captured["no_llm"] is True


def test_ask_no_llm_beats_env_end_to_end(tmp_path, monkeypatch, capsys):
    """End-to-end (no ask_project stub): --no-llm + TESSERAE_QUERY_LLM=1 must
    return a search-only envelope — the planner never runs."""
    from tesserae import cli

    project = _bootstrap_project(tmp_path)
    monkeypatch.setenv("TESSERAE_QUERY_LLM", "1")

    def _boom(*a, **k):
        raise AssertionError("planner must not run under --no-llm")

    monkeypatch.setattr("tesserae.ask_planner.plan_and_answer", _boom)

    rc = cli.main(["ask", "hello?", "--project", str(project), "--no-llm", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["backend"] == "wiki"
    assert payload["used_llm"] is False


# ---------------------------------------------------------------------------
# Removed-flag stubs: one-line stderr + exit 2 (clean break, never an alias).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv_flag", "stub"),
    [
        (["--llm"], "ask: --llm is now the default; use --no-llm to disable"),
        (["--wiki", "demo"], "ask: --wiki has moved → --name"),
        (["--backend", "raganything"], "ask: backend flags have moved → tesserae query"),
        (["--cognee-search-type", "CHUNKS"], "ask: --cognee-search-type was removed in 0.19"),
        (["--cognee-dataset", "d"], "ask: --cognee-dataset was removed in 0.19"),
    ],
)
def test_ask_removed_flags_exit_2_with_stub(argv_flag, stub, capsys):
    from tesserae import cli

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["ask", "hello?", *argv_flag])
    assert exc_info.value.code == 2
    assert stub in capsys.readouterr().err


def test_top_level_ask_fails_helpfully_when_no_project(tmp_path, monkeypatch, capsys):
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    rc = cli.main(["ask", "hello?"])
    assert rc == 2
    err = capsys.readouterr().err.lower()
    assert "not inside a registered project" in err or "register" in err


def test_top_level_ask_unknown_name(tmp_path, monkeypatch, capsys):
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    rc = cli.main(["ask", "hello?", "--name", "missing"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "missing" in err
    assert "projects list" in err.lower() or "register" in err.lower()


def test_top_level_ask_json_envelope(tmp_path, monkeypatch, capsys):
    from tesserae import cli

    project = _bootstrap_project(tmp_path)
    fake_ask, _ = _stub_wiki_envelope("json-answer")
    monkeypatch.setattr("tesserae.query.ask_project", fake_ask)

    rc = cli.main(["ask", "hello?", "--project", str(project), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["backend"] == "wiki"
    assert payload["answer"] == "json-answer"
    assert payload["question"] == "hello?"
    assert payload["used_llm"] is True


def test_wiki_list_command(tmp_path, monkeypatch, capsys):
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    project = _bootstrap_project(tmp_path)
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    cli.main(["projects", "register", str(project), "--name", "demo"])
    capsys.readouterr()

    rc = cli.main(["projects", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "demo" in out
    assert "Active:" not in out  # no privileged project anymore


def test_wiki_list_json(tmp_path, monkeypatch, capsys):
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    project = _bootstrap_project(tmp_path)
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    cli.main(["projects", "register", str(project), "--name", "demo"])
    capsys.readouterr()

    rc = cli.main(["projects", "list", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "active" not in payload  # active-project concept removed
    assert any(p["name"] == "demo" for p in payload["projects"])


def test_wiki_unregister_command(tmp_path, monkeypatch, capsys):
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    project = _bootstrap_project(tmp_path)
    registry_path = tmp_path / "registry.json"
    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", registry_path)

    cli.main(["projects", "register", str(project), "--name", "demo"])
    capsys.readouterr()

    rc = cli.main(["projects", "unregister", "demo"])
    assert rc == 0
    capsys.readouterr()

    rc = cli.main(["projects", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No projects registered" in out


def test_projects_activate_command_is_gone(tmp_path, monkeypatch, capsys):
    from tesserae import cli
    import tesserae.mcp_server as mcp_server

    monkeypatch.setattr(mcp_server, "DEFAULT_REGISTRY_PATH", tmp_path / "registry.json")
    # `projects activate` was removed -> terminal explanation stub, exit 2.
    rc = cli.main(["projects", "activate", "demo"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "was removed — all registered projects are active" in err
    assert "tesserae projects list" in err

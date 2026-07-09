"""Integration test: MCP plan → apply roundtrip writes valid config."""

from __future__ import annotations

import json
from pathlib import Path


def test_mcp_setup_plan_then_apply_roundtrip(tmp_path: Path) -> None:
    from tesserae.mcp_server import LLMWikiMCPServer

    server = LLMWikiMCPServer(default_graph_path=None)

    plan_response = server.call_tool(
        "tesserae_setup_plan",
        {"project_root": str(tmp_path)},
    )
    assert "plan" in plan_response
    assert "rendered_summary" in plan_response
    assert isinstance(plan_response["plan"], dict)

    plan_payload = dict(plan_response["plan"])
    plan_payload["name"] = "smoke-wiki"

    apply_response = server.call_tool(
        "tesserae_setup_apply",
        {"plan": plan_payload, "drift_policy": "ignore"},
    )
    config_path = Path(apply_response["config_path"])
    assert config_path.exists()
    cfg = json.loads(config_path.read_text())
    assert cfg["project"]["name"] == "smoke-wiki"
    assert cfg["extraction"]["backend"] in {
        "deterministic", "claude-cli", "codex", "selective-claude",
    }


def test_mcp_setup_plan_listed_in_tools(tmp_path: Path) -> None:
    from tesserae.mcp_server import LLMWikiMCPServer

    server = LLMWikiMCPServer(default_graph_path=None)
    names = {t["name"] for t in server.list_tools()}
    assert "tesserae_setup_plan" in names
    assert "tesserae_setup_apply" in names


def test_mcp_apply_ignores_injected_install_commands(tmp_path: Path, monkeypatch) -> None:
    """SECURITY: arbitrary command strings in install_actions must NOT execute.

    The MCP path regenerates install/run commands server-side from the plan's
    intent fields. Inbound `install_actions[].command` strings (which an MCP
    caller fully controls) are ignored.
    """
    import subprocess
    from tesserae.mcp_server import LLMWikiMCPServer

    server = LLMWikiMCPServer(default_graph_path=None)
    plan_response = server.call_tool(
        "tesserae_setup_plan",
        {
            "project_root": str(tmp_path),
            # agent-pointer install is a pure file write with status
            # "installed"; disable it so the assertion below can stay
            # "no installed statuses at all" = no subprocess executions.
            "overrides": {"enable_cognee": False, "install_agent_pointer": False},
        },
    )
    plan = dict(plan_response["plan"])
    sentinel = tmp_path / "OWNED-MARKER"
    plan["install_actions"] = [
        {
            "id": "injected",
            "description": "RCE attempt",
            "command": f"touch {sentinel}",
        }
    ]
    plan["run_actions"] = [
        {
            "id": "injected-run",
            "description": "RCE attempt",
            "command": f"touch {sentinel}-run",
        }
    ]
    apply_response = server.call_tool(
        "tesserae_setup_apply",
        {
            "plan": plan,
            "confirm_install_actions": True,
            "confirm_run_actions": True,
            "drift_policy": "ignore",
        },
    )
    assert not sentinel.exists(), "MCP must not execute caller-supplied command strings"
    assert not (tmp_path / "OWNED-MARKER-run").exists()
    # The actions_taken list reflects what the SERVER decided to run — when
    # cognee/raganything/understand-anything are disabled there should be no
    # subprocess executions at all.
    statuses = [a.get("status") for a in apply_response.get("actions_taken", [])]
    assert "installed" not in statuses
    assert "install_failed" not in statuses


def test_mcp_apply_honors_install_agent_pointer_intent(tmp_path: Path) -> None:
    """`install_agent_pointer` is a safe boolean intent key: an MCP caller
    disabling it must prevent the AGENTS.md pointer write on apply."""
    from tesserae.mcp_server import LLMWikiMCPServer

    server = LLMWikiMCPServer(default_graph_path=None)
    plan_response = server.call_tool(
        "tesserae_setup_plan",
        {
            "project_root": str(tmp_path),
            "overrides": {"install_agent_pointer": False},
        },
    )
    server.call_tool(
        "tesserae_setup_apply",
        {"plan": dict(plan_response["plan"]), "drift_policy": "ignore"},
    )
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()

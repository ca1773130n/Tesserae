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
    # The unread "extraction" block is no longer written.
    assert "extraction" not in cfg
    assert isinstance(cfg["memory_backends"], dict)


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


def test_mcp_setup_plan_redacts_api_key_everywhere(tmp_path: Path) -> None:
    """llm_api_key must never round-trip through the MCP plan response —
    neither as the top-level field, nor in the recorded intent, nor in the
    rendered summary."""
    from tesserae.mcp_server import LLMWikiMCPServer

    server = LLMWikiMCPServer(default_graph_path=None)
    response = server.call_tool(
        "tesserae_setup_plan",
        {
            "project_root": str(tmp_path),
            "overrides": {
                "llm_provider": "custom",
                "llm_base_url": "https://llm.example/v1",
                "llm_api_key": "sk-mcp-secret",
                "llm_model": "claude-opus-4-6",
            },
        },
    )
    blob = json.dumps(response)
    assert "sk-mcp-secret" not in blob
    plan = response["plan"]
    assert not plan.get("llm_api_key")
    assert "llm_api_key" not in (plan.get("intent") or {})
    # the non-secret llm keys still flow through
    assert plan["llm_provider"] == "custom"
    assert plan["llm_base_url"] == "https://llm.example/v1"
    assert plan["llm_model"] == "claude-opus-4-6"


def test_mcp_apply_allowlists_llm_keys_but_never_api_key(tmp_path: Path) -> None:
    """llm_provider/llm_model/llm_base_url/codex_home are safe intent keys the
    apply path honors; an injected llm_api_key must NOT reach config.json."""
    from tesserae.mcp_server import LLMWikiMCPServer

    server = LLMWikiMCPServer(default_graph_path=None)
    plan_response = server.call_tool(
        "tesserae_setup_plan",
        {
            "project_root": str(tmp_path),
            "overrides": {
                "llm_provider": "custom",
                "llm_base_url": "https://llm.example/v1",
                "llm_model": "claude-opus-4-6",
                "codex_home": "/h/.codex-personal1",
                "enable_cognee": False,
            },
        },
    )
    plan = dict(plan_response["plan"])
    # A hostile client smuggles a key into both surfaces the server reads.
    plan["llm_api_key"] = "sk-injected"
    plan.setdefault("intent", {})["llm_api_key"] = "sk-injected"
    apply_response = server.call_tool(
        "tesserae_setup_apply",
        {"plan": plan, "drift_policy": "ignore"},
    )
    cfg = json.loads(Path(apply_response["config_path"]).read_text())
    assert cfg["llm_provider"] == "custom"
    assert cfg["llm_base_url"] == "https://llm.example/v1"
    assert cfg["llm_model"] == "claude-opus-4-6"
    assert cfg["llm_codex_home"] == "/h/.codex-personal1"
    assert "llm_api_key" not in cfg
    assert "sk-injected" not in json.dumps(apply_response)


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

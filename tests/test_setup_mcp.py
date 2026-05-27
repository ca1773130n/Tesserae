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

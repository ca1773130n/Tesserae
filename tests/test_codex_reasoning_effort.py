"""Tesserae pins its own codex reasoning effort (default medium).

A user's ~/.codex/config.toml may set ``model_reasoning_effort = "xhigh"`` for
interactive work, which makes a multi-chunk ``tesserae compile`` many times
slower. Tesserae passes ``-c model_reasoning_effort=<effort>`` on its own codex
calls (default ``medium``) so it overrides config.toml for THIS process only,
without touching the user's global codex config.
"""

from __future__ import annotations

from pathlib import Path

import tesserae.llm_json as lj
from tesserae.llm_json import CodexCLIJsonClient, resolve_llm_client_settings


class _Proc:
    def __init__(self, rc: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def _fake_run_cli_capturing(captured: dict):
    def _fake(cmd, *, prompt, env, timeout):
        captured["cmd"] = cmd
        out = cmd[cmd.index("--output-last-message") + 1]
        Path(out).write_text('{"findings": []}', encoding="utf-8")
        return _Proc(rc=0)
    return _fake


def test_codex_cmd_carries_medium_effort(monkeypatch, tmp_path):
    captured: dict = {}
    monkeypatch.setattr(lj, "_run_cli", _fake_run_cli_capturing(captured))
    client = CodexCLIJsonClient(reasoning_effort="medium", codex_homes=[str(tmp_path)])
    client.complete_json(system="s", user="u", schema_name="x", cache_key="k")
    cmd = captured["cmd"]
    assert "-c" in cmd
    assert "model_reasoning_effort=medium" in cmd


def test_codex_effort_none_inherits_config_toml(monkeypatch, tmp_path):
    captured: dict = {}
    monkeypatch.setattr(lj, "_run_cli", _fake_run_cli_capturing(captured))
    CodexCLIJsonClient(reasoning_effort=None, codex_homes=[str(tmp_path)]).complete_json(
        system="s", user="u", schema_name="x", cache_key="k"
    )
    assert "model_reasoning_effort=" not in " ".join(captured["cmd"])


def test_resolve_effort_default_is_medium(monkeypatch):
    monkeypatch.delenv("TESSERAE_CODEX_REASONING_EFFORT", raising=False)
    monkeypatch.setattr(lj, "_load_global_llm_config", lambda: {})
    assert resolve_llm_client_settings({})["codex_reasoning_effort"] == "medium"


def test_resolve_effort_config_and_env_override(monkeypatch):
    monkeypatch.setattr(lj, "_load_global_llm_config", lambda: {})
    monkeypatch.delenv("TESSERAE_CODEX_REASONING_EFFORT", raising=False)
    assert resolve_llm_client_settings(
        {"llm_codex_reasoning_effort": "low"}
    )["codex_reasoning_effort"] == "low"
    monkeypatch.setenv("TESSERAE_CODEX_REASONING_EFFORT", "high")
    assert resolve_llm_client_settings(
        {"llm_codex_reasoning_effort": "low"}
    )["codex_reasoning_effort"] == "high"  # env beats config

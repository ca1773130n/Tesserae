"""`tesserae config status` — resolved LLM backend view + liveness ping.

The whole point is making a dead backend visible: a rate-limited / mis-authed
codex account silently makes extraction produce zero findings, so `status`
pings the backend and reports OK/FAILED with a non-zero exit on failure.
Hermetic: the resolver, global-config loader, and client builder are stubbed —
no real LLM call, no dependence on the machine's ~/.tesserae.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import tesserae.llm_json as lj
from tesserae.cli import _handle_config_status


class _OkClient:
    def complete_json(self, **kwargs):
        return {"ok": True}


class _DeadClient:
    def complete_json(self, **kwargs):
        return None  # rate-limit / auth / wrong model


@pytest.fixture
def _stub_resolution(monkeypatch):
    monkeypatch.setattr(lj, "resolve_llm_client_settings",
                        lambda cfg=None: {"provider": "codex", "codex_home": None, "claude_config_dirs": []})
    monkeypatch.setattr(lj, "_load_global_llm_config", lambda: {})


def test_status_reports_resolved_backend_and_live_ok(_stub_resolution, monkeypatch, capsys):
    monkeypatch.setattr(lj, "build_default_json_client", lambda **kw: _OkClient())
    rc = _handle_config_status(SimpleNamespace(project=None, ping=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "provider   : codex" in out
    assert "codex_home : ~/.codex (OS default)" in out
    assert "liveness   : ✓ OK" in out


def test_status_flags_dead_backend_nonzero(_stub_resolution, monkeypatch, capsys):
    monkeypatch.setattr(lj, "build_default_json_client", lambda **kw: _DeadClient())
    rc = _handle_config_status(SimpleNamespace(project=None, ping=True))
    out = capsys.readouterr().out
    assert rc == 1
    assert "liveness   : ✗ FAILED" in out
    assert "zero findings" in out


def test_status_no_ping_skips_live_call(_stub_resolution, monkeypatch, capsys):
    called = {"built": False}

    def _builder(**kw):
        called["built"] = True
        return _OkClient()

    monkeypatch.setattr(lj, "build_default_json_client", _builder)
    rc = _handle_config_status(SimpleNamespace(project=None, ping=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "liveness" not in out
    assert called["built"] is False


def test_status_anthropic_branch_masks_api_key(monkeypatch, capsys):
    """anthropic/custom get their own branch: model + base_url + a MASKED
    api_key ('set'/'unset') — the key value itself must never print."""
    monkeypatch.setattr(lj, "resolve_llm_client_settings", lambda cfg=None: {
        "provider": "anthropic", "codex_home": None, "claude_config_dirs": [],
        "model": "claude-opus-4-6", "base_url": "https://llm.example/v1",
        "api_key": "sk-status-secret",
    })
    monkeypatch.setattr(lj, "_load_global_llm_config", lambda: {})
    rc = _handle_config_status(SimpleNamespace(project=None, ping=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "provider   : anthropic" in out
    assert "model      : claude-opus-4-6" in out
    assert "base_url   : https://llm.example/v1" in out
    assert "api_key    : set" in out
    assert "sk-status-secret" not in out
    # the CLI-dir lines belong to the claude/codex branches only
    assert "claude_dirs" not in out and "codex_home" not in out


def test_status_custom_branch_reports_unset_key(monkeypatch, capsys):
    monkeypatch.setattr(lj, "resolve_llm_client_settings", lambda cfg=None: {
        "provider": "custom", "codex_home": None, "claude_config_dirs": [],
        "model": None, "base_url": None, "api_key": None,
    })
    monkeypatch.setattr(lj, "_load_global_llm_config", lambda: {})
    rc = _handle_config_status(SimpleNamespace(project=None, ping=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "provider   : custom" in out
    assert "api_key    : unset" in out


def test_status_parser_defaults_project_to_cwd():
    """`config status` resolves the CURRENT project by default (--project '.'),
    so a project-local llm_provider shows without extra flags."""
    from tesserae.cli import _build_config_parser

    args = _build_config_parser().parse_args(["status"])
    assert args.project == "."


def test_config_llm_persists_endpoint_keys_and_masks_api_key(monkeypatch, tmp_path, capsys):
    """`config llm --llm-model/--llm-base-url/--llm-api-key` write the global
    llm_* keys; the api key is warned about (plaintext) and echoed masked."""
    import json

    from tesserae import cli

    gpath = tmp_path / "config.json"
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", gpath)
    rc = cli.main([
        "config", "llm", "--llm-provider", "custom",
        "--llm-model", "claude-opus-4-6",
        "--llm-base-url", "https://llm.example/v1",
        "--llm-api-key", "sk-global-secret",
    ])
    assert rc == 0
    written = json.loads(gpath.read_text())
    assert written["llm_provider"] == "custom"
    assert written["llm_model"] == "claude-opus-4-6"
    assert written["llm_base_url"] == "https://llm.example/v1"
    assert written["llm_api_key"] == "sk-global-secret"
    captured = capsys.readouterr()
    assert captured.err.count("is stored in plaintext") == 1
    assert "sk-global-secret" not in captured.out  # echoed as (set), never raw


def test_config_show_masks_api_key_and_includes_new_keys(monkeypatch, tmp_path, capsys):
    import json

    from tesserae import cli

    gpath = tmp_path / "config.json"
    gpath.write_text(json.dumps({
        "llm_provider": "custom", "llm_model": "m1",
        "llm_base_url": "https://llm.example/v1",
        "llm_api_key": "sk-show-secret",
        "llm_codex_reasoning_effort": "high",
    }))
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", gpath)
    monkeypatch.setattr(lj, "_load_global_llm_config", lambda: json.loads(gpath.read_text()))
    rc = cli.main(["config", "show"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"llm_codex_reasoning_effort": "high"' in out
    assert '"llm_model": "m1"' in out
    assert '"llm_base_url": "https://llm.example/v1"' in out
    assert '"llm_api_key": "set"' in out
    assert "sk-show-secret" not in out


def test_status_shows_machine_wide_settings_and_deps(_stub_resolution, monkeypatch, capsys):
    # status must report more than the LLM backend: the machine-wide cognee
    # setting and optional-dependency status too.
    monkeypatch.setattr(
        lj, "_load_global_llm_config",
        lambda: {"memory_backends": {"cognee": {"enabled": True, "mode": "codex_cognify"}}},
    )
    rc = _handle_config_status(SimpleNamespace(project=None, ping=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Machine-wide settings" in out
    assert "cognee     : enabled (mode=codex_cognify)" in out
    assert "Optional dependencies:" in out
    assert "memex" in out and "raganything" in out
    assert "understand-anything" not in out  # backend removed

"""Machine-wide cognee enable: global config layer + `tesserae setup --enable-cognee`."""

from __future__ import annotations

import argparse
import json

import tesserae.llm_json as lj
from tesserae import cli
from tesserae.project import cognee_backend_config


def test_global_cognee_applies_to_a_project_without_its_own(monkeypatch):
    monkeypatch.setattr(
        lj, "_load_global_llm_config",
        lambda: {"memory_backends": {"cognee": {"enabled": True, "auto_cognify": True, "mode": "cognify"}}},
    )
    # Project config has no cognee section of its own.
    bc = cognee_backend_config({"name": "proj", "memory_backends": {}})
    assert bc["enabled"] is True and bc["auto_cognify"] is True and bc["mode"] == "cognify"


def test_project_overrides_global(monkeypatch):
    monkeypatch.setattr(
        lj, "_load_global_llm_config",
        lambda: {"memory_backends": {"cognee": {"enabled": True, "auto_cognify": True, "mode": "cognify"}}},
    )
    # Project turns it back off and changes the mode — project wins.
    bc = cognee_backend_config({"name": "proj", "memory_backends": {"cognee": {"enabled": False, "mode": "add"}}})
    assert bc["enabled"] is False and bc["mode"] == "add"


def test_no_global_no_project_returns_defaults(monkeypatch):
    monkeypatch.setattr(lj, "_load_global_llm_config", lambda: {})
    bc = cognee_backend_config({"name": "proj", "memory_backends": {}})
    # Demotion: without an explicit opt-in anywhere, cognee is disabled.
    assert bc["enabled"] is False and bc["auto_cognify"] is False


def test_config_setup_enable_cognee_writes_global(monkeypatch, tmp_path):
    gpath = tmp_path / "config.json"
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", gpath)
    args = argparse.Namespace(
        llm_provider=None, claude_config_dir=[], codex_home=None, reasoning_effort=None,
        install=[], install_all=False, enable_cognee=True, cognee_mode="cognify",
    )
    rc = cli._handle_config_setup(args)
    assert rc == 0
    written = json.loads(gpath.read_text())
    cognee = written["memory_backends"]["cognee"]
    assert cognee == {"enabled": True, "auto_cognify": True, "mode": "cognify"}


def test_config_setup_enable_cognee_preserves_existing_keys(monkeypatch, tmp_path):
    gpath = tmp_path / "config.json"
    gpath.write_text(json.dumps({"llm_provider": "codex", "memory_backends": {"raganything": {"enabled": True}}}))
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", gpath)
    args = argparse.Namespace(
        llm_provider=None, claude_config_dir=[], codex_home=None, reasoning_effort=None,
        install=[], install_all=False, enable_cognee=True, cognee_mode="add",
    )
    cli._handle_config_setup(args)
    written = json.loads(gpath.read_text())
    assert written["llm_provider"] == "codex"  # untouched
    assert written["memory_backends"]["raganything"] == {"enabled": True}  # sibling backend kept
    assert written["memory_backends"]["cognee"]["mode"] == "add"

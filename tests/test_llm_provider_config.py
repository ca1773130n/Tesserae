"""LLM provider selection: config.json keys + CLI flags (claude | codex).

Covers the wiring that lets a project durably prefer the Codex CLI for the
synthesis/insights JSON client ("use codex instead of claude code"):

- ``ProjectWiki.init`` persists ``llm_provider`` / ``llm_claude_config_dirs``
  / ``llm_codex_home`` into ``config.json``.
- ``ProjectWiki._build_json_client`` resolves those keys (env beats config)
  and returns the matching CLI client.
- ``tesserae project init|compile`` accept ``--llm-provider``,
  ``--claude-config-dir`` and ``--codex-home``; the compile handler surfaces
  them as env vars so every internal ``build_default_json_client`` call and
  the extractors see them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _isolate_env(monkeypatch):
    monkeypatch.delenv("TESSERAE_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_project_init_persists_llm_keys(tmp_path: Path):
    from tesserae.project import ProjectWiki

    wiki = ProjectWiki.init(
        tmp_path,
        name="llm-config-test",
        llm_provider="codex",
        llm_codex_home="/home/u/.codex-personal1",
        llm_claude_config_dirs=["/home/u/.claude-personal2"],
    )
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    assert cfg["llm_provider"] == "codex"
    assert cfg["llm_codex_home"] == "/home/u/.codex-personal1"
    assert cfg["llm_claude_config_dirs"] == ["/home/u/.claude-personal2"]


def test_project_init_omits_llm_keys_by_default(tmp_path: Path):
    from tesserae.project import ProjectWiki

    wiki = ProjectWiki.init(tmp_path, name="llm-config-default")
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    assert "llm_provider" not in cfg
    assert "llm_codex_home" not in cfg
    assert "llm_claude_config_dirs" not in cfg


def test_build_json_client_honors_config_provider(tmp_path: Path, monkeypatch):
    import tesserae.llm_json as lj
    from tesserae.project import ProjectWiki

    _isolate_env(monkeypatch)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: True)

    wiki = ProjectWiki.init(
        tmp_path, name="llm-codex", llm_provider="codex", llm_codex_home="/x/codex"
    )
    client = wiki._build_json_client()
    assert isinstance(client, lj.CodexCLIJsonClient)
    assert client.codex_homes == ["/x/codex"]

    # env (i.e. CLI flag) beats the persisted config
    monkeypatch.setenv("TESSERAE_LLM_PROVIDER", "claude")
    client = wiki._build_json_client()
    assert isinstance(client, lj.ClaudeCLIJsonClient)


def test_build_json_client_defaults_to_claude_without_config(tmp_path: Path, monkeypatch):
    import tesserae.llm_json as lj
    from tesserae.project import ProjectWiki

    _isolate_env(monkeypatch)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: True)

    wiki = ProjectWiki.init(tmp_path, name="llm-default")
    client = wiki._build_json_client()
    assert isinstance(client, lj.ClaudeCLIJsonClient)


@pytest.mark.parametrize("command", ["init", "compile"])
def test_cli_accepts_llm_provider_flags(command, tmp_path: Path, capsys):
    """The flags must be DEFINED on the parser (invalid choice, not
    unrecognized argument)."""
    from tesserae.cli import project_main

    with pytest.raises(SystemExit):
        project_main([command, "--project", str(tmp_path), "--llm-provider", "bogus-provider"])
    err = capsys.readouterr().err
    assert "invalid choice" in err and "bogus-provider" in err, err
    assert "unrecognized arguments" not in err, err


def test_apply_llm_cli_env_sets_env_vars(monkeypatch):
    import argparse

    from tesserae.cli import _apply_llm_cli_env

    _isolate_env(monkeypatch)
    args = argparse.Namespace(
        llm_provider="codex",
        claude_config_dir=["/a/.claude-x"],
        codex_home="/a/.codex-y",
    )
    _apply_llm_cli_env(args)

    import os

    assert os.environ["TESSERAE_LLM_PROVIDER"] == "codex"
    assert os.environ["CLAUDE_CONFIG_DIR"] == "/a/.claude-x"
    assert os.environ["CODEX_HOME"] == "/a/.codex-y"


def test_apply_llm_cli_env_leaves_env_alone_when_flags_absent(monkeypatch):
    import argparse
    import os

    from tesserae.cli import _apply_llm_cli_env

    _isolate_env(monkeypatch)
    args = argparse.Namespace(llm_provider=None, claude_config_dir=[], codex_home=None)
    _apply_llm_cli_env(args)

    assert "TESSERAE_LLM_PROVIDER" not in os.environ
    assert "CLAUDE_CONFIG_DIR" not in os.environ
    assert "CODEX_HOME" not in os.environ

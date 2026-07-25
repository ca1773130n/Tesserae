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
    from tesserae.cli import main

    with pytest.raises(SystemExit):
        main([command, "--project", str(tmp_path), "--llm-provider", "bogus-provider"])
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


# ---------------------------------------------------------------------------
# Global defaults: ~/.tesserae/config.json (machine-wide, no CODEX_HOME needed)
# ---------------------------------------------------------------------------


def test_resolve_settings_falls_back_to_global_config(tmp_path: Path, monkeypatch):
    import tesserae.llm_json as lj

    _isolate_env(monkeypatch)
    global_cfg = tmp_path / "global-config.json"
    global_cfg.write_text(
        json.dumps(
            {
                "llm_provider": "codex",
                "llm_codex_home": "/global/.codex-personal1",
                "llm_claude_config_dirs": ["/global/.claude-personal2"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", global_cfg)

    settings = lj.resolve_llm_client_settings({})
    assert settings["provider"] == "codex"
    assert settings["codex_home"] == "/global/.codex-personal1"
    assert settings["claude_config_dirs"] == ["/global/.claude-personal2"]


def test_resolve_settings_project_config_beats_global(tmp_path: Path, monkeypatch):
    import tesserae.llm_json as lj

    _isolate_env(monkeypatch)
    global_cfg = tmp_path / "global-config.json"
    global_cfg.write_text(
        json.dumps({"llm_provider": "claude", "llm_codex_home": "/global/home"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", global_cfg)

    settings = lj.resolve_llm_client_settings(
        {"llm_provider": "codex", "llm_codex_home": "/project/home"}
    )
    assert settings["provider"] == "codex"
    assert settings["codex_home"] == "/project/home"


def test_resolve_settings_missing_or_corrupt_global_is_safe(tmp_path: Path, monkeypatch):
    import tesserae.llm_json as lj

    _isolate_env(monkeypatch)
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", tmp_path / "does-not-exist.json")
    assert lj.resolve_llm_client_settings({})["provider"] is None

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", corrupt)
    assert lj.resolve_llm_client_settings({})["provider"] is None


def test_build_json_client_uses_global_when_project_silent(tmp_path: Path, monkeypatch):
    import tesserae.llm_json as lj
    from tesserae.project import ProjectWiki

    _isolate_env(monkeypatch)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: True)
    global_cfg = tmp_path / "global-config.json"
    global_cfg.write_text(
        json.dumps({"llm_provider": "codex", "llm_codex_home": "/g/.codex-personal1"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", global_cfg)

    wiki = ProjectWiki.init(tmp_path / "proj", name="global-llm")
    client = wiki._build_json_client()
    assert isinstance(client, lj.CodexCLIJsonClient)
    assert client.codex_homes == ["/g/.codex-personal1"]


def test_cli_llm_defaults_writes_global_config(tmp_path: Path, monkeypatch, capsys):
    import tesserae.llm_json as lj
    from tesserae.cli import main

    _isolate_env(monkeypatch)
    global_cfg = tmp_path / "tesserae" / "config.json"
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", global_cfg)

    rc = main(
        [
            "config", "llm",
            "--llm-provider", "codex",
            "--codex-home", "/home/u/.codex-personal1",
        ]
    )
    assert rc == 0
    saved = json.loads(global_cfg.read_text(encoding="utf-8"))
    assert saved["llm_provider"] == "codex"
    assert saved["llm_codex_home"] == "/home/u/.codex-personal1"


def test_cli_llm_defaults_merges_existing_keys(tmp_path: Path, monkeypatch):
    import tesserae.llm_json as lj
    from tesserae.cli import main

    _isolate_env(monkeypatch)
    global_cfg = tmp_path / "config.json"
    global_cfg.write_text(
        json.dumps({"unrelated_key": "keep-me", "llm_provider": "claude"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", global_cfg)

    rc = main(["config", "llm", "--llm-provider", "codex"])
    assert rc == 0
    saved = json.loads(global_cfg.read_text(encoding="utf-8"))
    assert saved["llm_provider"] == "codex"
    assert saved["unrelated_key"] == "keep-me"


def test_cli_llm_defaults_show_prints_effective_settings(tmp_path: Path, monkeypatch, capsys):
    import tesserae.llm_json as lj
    from tesserae.cli import main

    _isolate_env(monkeypatch)
    global_cfg = tmp_path / "config.json"
    global_cfg.write_text(
        json.dumps({"llm_provider": "codex", "llm_codex_home": "/x"}), encoding="utf-8"
    )
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", global_cfg)

    rc = main(["config", "show"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "codex" in out and "/x" in out


def test_community_summaries_honors_configured_provider(tmp_path, monkeypatch):
    """The community-summaries pass must build its LLM client through the
    provider-aware _build_json_client, NOT the bare build_default_json_client
    (which always defaults to claude regardless of llm_provider=codex)."""
    from tesserae.project import ProjectWiki
    from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType

    wiki = ProjectWiki.init(tmp_path, name="t", llm_provider="codex")
    seen = {}

    def _recorder(model=None):
        seen["called"] = True
        return None  # no client -> pass logs "skipping" and returns the graph

    monkeypatch.setattr(wiki, "_build_json_client", _recorder)
    graph = ResearchGraph(
        nodes=[ResearchNode(id="Concept:x", name="x", type=ResearchNodeType.CONCEPT)],
        edges=[],
    )
    wiki._merge_community_summaries(graph, wiki.config())
    assert seen.get("called") is True, (
        "community summaries must build its client via _build_json_client "
        "so llm_provider=codex is honored"
    )


def test_memory_passes_honor_configured_provider(tmp_path, monkeypatch):
    """The Phase-5 memory-passes LLM gate must also route through the
    provider-aware _build_json_client."""
    from tesserae.project import ProjectWiki
    from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType

    monkeypatch.setenv("TESSERAE_ENABLE_LLM_PASSES", "1")
    wiki = ProjectWiki.init(tmp_path, name="t", llm_provider="codex")
    seen = {}

    def _recorder(model=None):
        seen["called"] = True
        return None

    monkeypatch.setattr(wiki, "_build_json_client", _recorder)
    graph = ResearchGraph(
        nodes=[ResearchNode(id="Concept:x", name="x", type=ResearchNodeType.CONCEPT)],
        edges=[],
    )
    wiki._run_memory_passes(graph, None)
    assert seen.get("called") is True, (
        "memory passes must build their client via _build_json_client"
    )


# ---------------------------------------------------------------------------
# Custom claude-compatible endpoint: llm_model / llm_base_url / llm_api_key
# ---------------------------------------------------------------------------


def _isolate_endpoint_env(monkeypatch):
    _isolate_env(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("TESSERAE_LLM_MODEL", raising=False)


def _write_global_cfg(tmp_path: Path, monkeypatch, payload: dict) -> None:
    import tesserae.llm_json as lj

    global_cfg = tmp_path / "global-config.json"
    global_cfg.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(lj, "GLOBAL_CONFIG_PATH", global_cfg)


def test_resolve_settings_returns_endpoint_knobs_from_config(tmp_path: Path, monkeypatch):
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    _write_global_cfg(
        tmp_path,
        monkeypatch,
        {
            "llm_provider": "custom",
            "llm_model": "glm-4.7",
            "llm_base_url": "https://llm.example.com/api",
            "llm_api_key": "sk-global",
        },
    )

    settings = lj.resolve_llm_client_settings({})
    assert settings["provider"] == "custom"
    assert settings["model"] == "glm-4.7"
    assert settings["base_url"] == "https://llm.example.com/api"
    assert settings["api_key"] == "sk-global"


def test_resolve_settings_project_endpoint_beats_global(tmp_path: Path, monkeypatch):
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    _write_global_cfg(
        tmp_path,
        monkeypatch,
        {"llm_model": "global-model", "llm_base_url": "https://global.example.com"},
    )

    settings = lj.resolve_llm_client_settings(
        {
            "llm_model": "project-model",
            "llm_base_url": "https://project.example.com",
            "llm_api_key": "sk-project",
        }
    )
    assert settings["model"] == "project-model"
    assert settings["base_url"] == "https://project.example.com"
    assert settings["api_key"] == "sk-project"


def test_resolve_settings_env_beats_config_for_endpoint_knobs(tmp_path: Path, monkeypatch):
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    _write_global_cfg(tmp_path, monkeypatch, {})
    monkeypatch.setenv("TESSERAE_LLM_MODEL", "env-model")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env.example.com")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")

    settings = lj.resolve_llm_client_settings(
        {
            "llm_model": "cfg-model",
            "llm_base_url": "https://cfg.example.com",
            "llm_api_key": "sk-cfg",
        }
    )
    assert settings["model"] == "env-model"
    assert settings["base_url"] == "https://env.example.com"
    assert settings["api_key"] == "sk-env"


def test_build_default_custom_provider_uses_configured_endpoint(tmp_path: Path, monkeypatch):
    """provider=custom builds the Anthropic client from config — WITHOUT
    ANTHROPIC_API_KEY in the environment."""
    import tesserae.llm_json as lj

    pytest.importorskip("anthropic")
    _isolate_endpoint_env(monkeypatch)
    monkeypatch.setattr(lj, "_CLIENT_FACTORY", None, raising=False)
    _write_global_cfg(
        tmp_path,
        monkeypatch,
        {
            "llm_provider": "custom",
            "llm_model": "glm-4.7",
            "llm_base_url": "https://llm.example.com/api",
            "llm_api_key": "sk-cfg",
        },
    )

    # provider comes from config alone — no arg, no env var
    client = lj.build_default_json_client()
    assert isinstance(client, lj.AnthropicLLMJsonClient)
    assert client.model == "glm-4.7"
    assert client.base_url == "https://llm.example.com/api"
    assert client._client.api_key == "sk-cfg"
    assert str(client._client.base_url).startswith("https://llm.example.com")


def test_build_rotating_client_custom_provider_uses_configured_endpoint(
    tmp_path: Path, monkeypatch
):
    import tesserae.llm_json as lj

    pytest.importorskip("anthropic")
    _isolate_endpoint_env(monkeypatch)
    monkeypatch.setattr(lj, "_CLIENT_FACTORY", None, raising=False)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: False)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: False)
    _write_global_cfg(
        tmp_path,
        monkeypatch,
        {
            "llm_provider": "custom",
            "llm_model": "glm-4.7",
            "llm_base_url": "https://llm.example.com/api",
            "llm_api_key": "sk-cfg",
        },
    )

    client = lj.build_rotating_client()
    assert isinstance(client, lj.AnthropicLLMJsonClient)
    assert client.model == "glm-4.7"
    assert client.base_url == "https://llm.example.com/api"


def test_configured_llm_model_is_provider_scoped_fallback(tmp_path: Path, monkeypatch):
    """llm_model replaces the hardcoded default for the configured provider
    only; explicit model args still win, other providers keep their own
    defaults so a claude model name never lands on the codex CLI."""
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    _write_global_cfg(
        tmp_path, monkeypatch, {"llm_provider": "claude", "llm_model": "opus"}
    )

    assert lj.ClaudeCLIJsonClient(config_dirs=["/x"]).model == "opus"
    assert lj.ClaudeCLIJsonClient(model="sonnet", config_dirs=["/x"]).model == "sonnet"
    # provider-scoped: the codex client keeps its native default
    assert lj.CodexCLIJsonClient(codex_homes=["/y"]).model == "gpt-5.6-luna"

    # env beats config
    monkeypatch.setenv("TESSERAE_LLM_MODEL", "env-model")
    assert lj.ClaudeCLIJsonClient(config_dirs=["/x"]).model == "env-model"


def test_claude_cli_child_env_gets_custom_endpoint(monkeypatch):
    """When base_url/api_key are resolved, the claude CLI child process gets
    ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN injected."""
    import subprocess

    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    seen = {}

    def fake_run(cmd, prompt=None, env=None, timeout=None):
        seen["env"] = env
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok")

    monkeypatch.setattr(lj, "_run_cli", fake_run)
    client = lj.ClaudeCLIJsonClient(
        config_dirs=["/tmp/fake-claude"],
        base_url="https://llm.example.com/api",
        api_key="sk-cfg",
    )
    assert client.complete_text(system="s", user="u") == "ok"
    assert seen["env"]["ANTHROPIC_BASE_URL"] == "https://llm.example.com/api"
    assert seen["env"]["ANTHROPIC_AUTH_TOKEN"] == "sk-cfg"


def test_claude_cli_child_env_untouched_without_endpoint(monkeypatch):
    import subprocess

    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    seen = {}

    def fake_run(cmd, prompt=None, env=None, timeout=None):
        seen["env"] = env
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok")

    monkeypatch.setattr(lj, "_run_cli", fake_run)
    client = lj.ClaudeCLIJsonClient(config_dirs=["/tmp/fake-claude"])
    assert client.complete_text(system="s", user="u") == "ok"
    assert "ANTHROPIC_BASE_URL" not in seen["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in seen["env"]


def test_build_default_threads_endpoint_to_claude_cli_only_with_base_url(
    tmp_path: Path, monkeypatch
):
    """A configured base_url routes the claude CLI at the custom endpoint,
    but a configured api_key ALONE must not flip the CLI off OAuth."""
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    monkeypatch.setattr(lj, "_CLIENT_FACTORY", None, raising=False)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: False)

    _write_global_cfg(
        tmp_path,
        monkeypatch,
        {"llm_base_url": "https://llm.example.com/api", "llm_api_key": "sk-cfg"},
    )
    client = lj.build_default_json_client()
    assert isinstance(client, lj.ClaudeCLIJsonClient)
    assert client.base_url == "https://llm.example.com/api"
    assert client.api_key == "sk-cfg"

    _write_global_cfg(tmp_path, monkeypatch, {"llm_api_key": "sk-cfg"})
    client = lj.build_default_json_client()
    assert isinstance(client, lj.ClaudeCLIJsonClient)
    assert client.base_url is None
    assert client.api_key is None

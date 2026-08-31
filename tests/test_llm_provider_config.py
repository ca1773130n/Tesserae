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
    # _apply_llm_cli_env writes os.environ directly (not via monkeypatch), so
    # this leaks across tests exactly as CLAUDE_CONFIG_DIR above already did.
    monkeypatch.delenv("TESSERAE_CLAUDE_CONFIG_DIRS", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    # Same leak, same reason: _apply_llm_cli_env now also writes the
    # Tesserae-owned channels, which outrank config and would otherwise carry
    # one test's --codex-home/--llm-base-url into every test after it.
    monkeypatch.delenv("TESSERAE_CODEX_HOMES", raising=False)
    monkeypatch.delenv("TESSERAE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TESSERAE_LLM_API_KEY", raising=False)
    monkeypatch.delenv("TESSERAE_LLM_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TESSERAE_LLM_API_STYLE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


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


# ---------------------------------------------------------------------------
# The PROJECT config must reach the client on the primary compile path
# ---------------------------------------------------------------------------


def test_project_endpoint_config_reaches_the_built_client(tmp_path: Path, monkeypatch):
    """A custom endpoint set in .tesserae/config.json must actually be used.

    ``ProjectWiki._build_json_client`` resolved the project config and then
    called ``build_default_json_client`` without it, so the factory
    re-resolved against env + the GLOBAL config only and every project-level
    llm_model / llm_base_url / llm_api_key was silently discarded. The compile
    then ran on the default backend while the config said otherwise.
    """
    import tesserae.llm_json as lj
    from tesserae.project import ProjectWiki

    pytest.importorskip("anthropic")
    _isolate_endpoint_env(monkeypatch)
    monkeypatch.setattr(lj, "_CLIENT_FACTORY", None, raising=False)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: False)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: False)
    # The global layer says nothing — everything below comes from the project.
    _write_global_cfg(tmp_path, monkeypatch, {})

    wiki = ProjectWiki.init(tmp_path / "proj", name="endpoint-project")
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg.update(
        {
            "llm_provider": "custom",
            "llm_model": "glm-4.7",
            "llm_base_url": "https://project.example.com/api",
            "llm_api_key": "sk-project",
        }
    )
    wiki.paths.config.write_text(json.dumps(cfg), encoding="utf-8")

    client = wiki._build_json_client()
    assert isinstance(client, lj.AnthropicLLMJsonClient)
    assert client.model == "glm-4.7"
    assert client.base_url == "https://project.example.com/api"
    assert client._client.api_key == "sk-project"


def test_project_model_is_provider_scoped_when_threaded(tmp_path: Path, monkeypatch):
    """Threading the project settings must not leak a claude-shaped model
    onto the codex CLI when the availability chain falls through."""
    import tesserae.llm_json as lj
    from tesserae.project import ProjectWiki

    _isolate_endpoint_env(monkeypatch)
    monkeypatch.setattr(lj, "_CLIENT_FACTORY", None, raising=False)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: False)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: True)
    _write_global_cfg(tmp_path, monkeypatch, {})

    wiki = ProjectWiki.init(tmp_path / "proj", name="scoped-model")
    cfg = json.loads(wiki.paths.config.read_text(encoding="utf-8"))
    cfg.update({"llm_provider": "claude", "llm_model": "claude-opus-4-6"})
    wiki.paths.config.write_text(json.dumps(cfg), encoding="utf-8")

    client = wiki._build_json_client()
    assert isinstance(client, lj.CodexCLIJsonClient)
    assert client.model != "claude-opus-4-6"
    assert client.model == lj.CODEX_DEFAULT_MODEL


def test_repeated_claude_config_dir_flag_keeps_every_account(monkeypatch, tmp_path: Path):
    """--claude-config-dir repeated must not collapse to the first account.

    CLAUDE_CONFIG_DIR is a scalar, so the rotation the user spelled out was
    silently pinned to one quota.
    """
    import argparse

    import tesserae.llm_json as lj
    from tesserae import cli

    _isolate_endpoint_env(monkeypatch)
    monkeypatch.delenv("TESSERAE_CLAUDE_CONFIG_DIRS", raising=False)
    _write_global_cfg(tmp_path, monkeypatch, {})

    args = argparse.Namespace(claude_config_dir=["/a/.claude-1", "/b/.claude-2"])
    cli._apply_llm_cli_env(args)

    settings = lj.resolve_llm_client_settings({})
    assert settings["claude_config_dirs"] == ["/a/.claude-1", "/b/.claude-2"]


# ------------------------------------------------ custom endpoints, both wires ---
#
# The defect these cover, reported 2026-08-31: a user set a base URL, a model and
# an API key for a custom provider and Tesserae reported a wrong/unsupported
# model. Three separate causes, each with a test below.


def test_custom_openai_style_reaches_an_openai_compatible_endpoint(monkeypatch):
    """vLLM / LiteLLM / OpenRouter / Ollama / LM Studio all speak this wire.

    Before: provider=custom always built the Anthropic client, which POSTs
    /v1/messages — every OpenAI-compatible server 404s, and the 404 was reported
    as "unavailable", indistinguishable from having no LLM at all.
    """
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    monkeypatch.setattr(lj, "_CLIENT_FACTORY", None, raising=False)
    settings = lj.resolve_llm_client_settings({
        "llm_provider": "custom",
        "llm_api_style": "openai",
        "llm_base_url": "http://localhost:8000/v1",
        "llm_model": "qwen3-coder",
        "llm_auth_token": "sk-local",
    })
    client = lj.build_default_json_client(settings=settings)
    assert isinstance(client, lj.OpenAIAPIJsonClient)
    assert client.model == "qwen3-coder"          # not "claude-sonnet-4-6"
    assert client.base_url == "http://localhost:8000/v1"
    assert client.available


def test_the_configured_model_is_not_dropped_when_provider_comes_from_elsewhere(monkeypatch):
    """The literal reported symptom: "it says wrong model".

    The model used to be scoped against the config layer's provider string, so
    an explicit provider (CLI flag / env) plus a configured model meant the model
    was discarded and the hardcoded claude-sonnet-4-6 was sent to the user's
    endpoint, which rejected a model they never chose.
    """
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    monkeypatch.setattr(lj, "_CLIENT_FACTORY", None, raising=False)
    settings = lj.resolve_llm_client_settings({
        "llm_model": "deepseek-chat",
        "llm_base_url": "https://gw.example/v1",
        "llm_api_key": "sk-x",
    })
    client = lj.build_default_json_client(provider="custom", settings=settings)
    assert client.model == "deepseek-chat"


def test_an_anthropic_base_url_is_not_doubled(monkeypatch):
    """The SDK appends /v1/messages, so the documented .../v1 became /v1/v1."""
    import tesserae.llm_json as lj

    assert lj._normalize_base_url("https://gw.example/v1", "anthropic") == "https://gw.example"
    assert lj._normalize_base_url("https://gw.example", "anthropic") == "https://gw.example"
    # the openai wire is the mirror image: this code appends /chat/completions
    assert lj._normalize_base_url("https://gw.example", "openai") == "https://gw.example/v1"
    assert lj._normalize_base_url("https://gw.example/v1/", "openai") == "https://gw.example/v1"


def test_a_bearer_gateway_is_reachable(monkeypatch):
    """A gateway wanting Authorization: Bearer had no channel at all: the one
    credential field was sent as X-Api-Key by the SDK."""
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    settings = lj.resolve_llm_client_settings({"llm_auth_token": "tok", "llm_provider": "custom"})
    assert settings["auth_token"] == "tok"
    client = lj.OpenAIAPIJsonClient("m", auth_token="tok", base_url="http://h/v1")
    assert client.available and "auth_token" in client.identity


def test_a_keyless_local_endpoint_is_usable(monkeypatch):
    """Ollama and LM Studio take no credential; requiring one made them unusable."""
    import tesserae.llm_json as lj

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert lj.OpenAIAPIJsonClient("llama3", base_url="http://localhost:11434/v1").available
    # ...but the default host still requires one, so no unauthenticated call is
    # ever sent to api.openai.com.
    assert not lj.OpenAIAPIJsonClient("gpt-4o-mini").available


def test_an_endpoint_provider_never_degrades_into_another_backend(monkeypatch):
    """The silent fall-through that produced the confusing error.

    A custom endpoint that cannot be built used to fall to the Claude CLI, which
    was spawned with --model sonnet against the user's own base URL. Now it
    raises, naming provider, wire, URL and model.
    """
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    monkeypatch.setattr(lj, "_CLIENT_FACTORY", None, raising=False)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(lj, "OpenAIAPIJsonClient",
                        lambda *a, **k: type("Dead", (), {"available": False})())
    settings = lj.resolve_llm_client_settings({
        "llm_provider": "custom", "llm_api_style": "openai",
        "llm_base_url": "https://gw.example/v1", "llm_model": "qwen3-coder",
    })
    with pytest.raises(lj.LLMProviderConfigError) as err:
        lj.build_default_json_client(settings=settings)
    for expected in ("custom", "openai", "gw.example", "qwen3-coder"):
        assert expected in str(err.value)

    # ...unless the operator asks for the old chaining back.
    settings["allow_fallback"] = True
    assert isinstance(lj.build_default_json_client(settings=settings), lj.ClaudeCLIJsonClient)


def test_a_misspelled_provider_is_reported_not_silently_claude(monkeypatch):
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    with pytest.raises(lj.LLMProviderConfigError) as err:
        lj.resolve_llm_client_settings({"llm_provider": "openrouter"})
    assert "openrouter" in str(err.value) and "custom" in str(err.value)


def test_the_resolver_records_which_layer_won_each_key(tmp_path: Path, monkeypatch):
    """`config status` used to GUESS the source and credited env vars the
    resolver deliberately ignores."""
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    _write_global_cfg(tmp_path, monkeypatch, {"llm_model": "from-global", "llm_base_url": "https://global"})
    settings = lj.resolve_llm_client_settings({"llm_base_url": "https://project"})
    assert settings["base_url"] == "https://project"
    assert settings["sources"]["base_url"] == "project .tesserae/config.json"
    assert settings["model"] == "from-global"
    assert settings["sources"]["model"] == "~/.tesserae/config.json"
    monkeypatch.setenv("TESSERAE_LLM_MODEL", "from-env")
    assert lj.resolve_llm_client_settings({})["sources"]["model"] == "env TESSERAE_LLM_MODEL"


def test_the_ask_path_can_see_a_project_custom_provider(monkeypatch):
    """build_rotating_client had no settings= at all, so `tesserae ask`, query,
    summaries and the daemon could not see a project-level endpoint."""
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    monkeypatch.setattr(lj, "_CLIENT_FACTORY", None, raising=False)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: True)
    settings = lj.resolve_llm_client_settings({
        "llm_provider": "custom", "llm_api_style": "openai",
        "llm_base_url": "http://localhost:8000/v1", "llm_model": "qwen3-coder",
        "llm_auth_token": "sk-local",
    })
    client = lj.build_rotating_client(settings=settings)
    assert isinstance(client, lj.OpenAIAPIJsonClient)
    assert client.model == "qwen3-coder"


def test_setup_persists_the_wire_and_the_bearer_token():
    """Both parsers accepted --llm-api-style / --llm-auth-token and wrote neither,
    so a project stayed on the anthropic wire while the user believed otherwise.

    Exercises the payload builder directly with a stand-in plan: constructing a
    real SetupPlan needs a full DetectionReport, which this has nothing to say about.
    """
    from types import SimpleNamespace

    from tesserae.setup.apply import _build_config_payload

    plan = SimpleNamespace(
        name="wire-test", source_kind="Repository", sources=[],
        claude_config_dir=None, codex_home=None,
        external_tools=[], memory_backends=[],
        llm_provider="custom", llm_model="qwen3-coder",
        llm_base_url="http://localhost:8000/v1",
        llm_api_key=None, llm_auth_token="sk-local", llm_api_style="openai",
    )
    payload = _build_config_payload(plan)
    assert payload["llm_api_style"] == "openai"
    assert payload["llm_auth_token"] == "sk-local"
    assert payload["llm_base_url"] == "http://localhost:8000/v1"
    assert payload["llm_model"] == "qwen3-coder"

def test_allow_fallback_is_parsed_not_merely_present(monkeypatch):
    """An env flag read with bool() is ON for "0" and "false"."""
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    monkeypatch.setenv("TESSERAE_LLM_ALLOW_FALLBACK", "0")
    assert lj.resolve_llm_client_settings({})["allow_fallback"] is False
    monkeypatch.setenv("TESSERAE_LLM_ALLOW_FALLBACK", "false")
    assert lj.resolve_llm_client_settings({})["allow_fallback"] is False
    monkeypatch.setenv("TESSERAE_LLM_ALLOW_FALLBACK", "1")
    assert lj.resolve_llm_client_settings({})["allow_fallback"] is True


def test_the_openai_wire_does_not_demand_the_anthropic_sdk(monkeypatch):
    """provider=custom + api_style=openai uses stdlib urllib, so the install
    hint must not tell the user to install an SDK they do not need."""
    import tesserae.llm_json as lj

    _isolate_endpoint_env(monkeypatch)
    monkeypatch.setattr(lj, "_CLIENT_FACTORY", None, raising=False)
    monkeypatch.setattr(lj, "_claude_cli_available", lambda: False)
    monkeypatch.setattr(lj, "_codex_cli_available", lambda: False)
    monkeypatch.setattr(lj, "OpenAIAPIJsonClient",
                        lambda *a, **k: type("Dead", (), {"available": False})())
    settings = lj.resolve_llm_client_settings({
        "llm_provider": "custom", "llm_api_style": "openai",
        "llm_base_url": "http://h/v1", "llm_model": "m"})
    with pytest.raises(lj.LLMProviderConfigError) as err:
        lj.build_default_json_client(settings=settings)
    assert "synthesis-llm" not in str(err.value)

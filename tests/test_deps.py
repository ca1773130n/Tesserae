"""Optional-dependency registry: detect + install, and the global config merge."""

from __future__ import annotations

import types

from tesserae import deps
from tesserae.cli import _merge_global_llm_config, _resolve_dep_targets


def test_status_shape_covers_known_deps():
    s = deps.status()
    assert {d["name"] for d in s} >= {"memex", "cognee", "raganything", "understand-anything"}
    assert all(set(d) == {"name", "summary", "installed", "note"} for d in s)


def test_install_unknown_dep():
    r = deps.install("does-not-exist")
    assert r["ok"] is False and "unknown" in r["error"]


def test_install_already_present_is_noop(monkeypatch):
    monkeypatch.setitem(deps.DEPS_BY_NAME, "x", deps.Dep("x", "s", lambda: True, ["false"]))
    # subprocess must NOT run when it's already installed.
    monkeypatch.setattr(deps.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    r = deps.install("x")
    assert r["ok"] is True and r["already"] is True


def test_install_runs_then_detects_success(monkeypatch):
    seen = {"n": 0}

    def detect():
        seen["n"] += 1
        return seen["n"] > 1  # absent before install, present after

    monkeypatch.setitem(deps.DEPS_BY_NAME, "x", deps.Dep("x", "s", detect, ["true"]))
    monkeypatch.setattr(deps.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    r = deps.install("x")
    assert r["ok"] is True and not r.get("already")


def test_install_failure_surfaces_stderr(monkeypatch):
    monkeypatch.setitem(deps.DEPS_BY_NAME, "x", deps.Dep("x", "s", lambda: False, ["true"]))
    monkeypatch.setattr(deps.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    r = deps.install("x")
    assert r["ok"] is False and "boom" in r["error"]


def test_install_missing_installer_degrades(monkeypatch):
    monkeypatch.setitem(deps.DEPS_BY_NAME, "x", deps.Dep("x", "s", lambda: False, ["nope-cmd"]))

    def boom(*a, **k):
        raise OSError("cargo not found")
    monkeypatch.setattr(deps.subprocess, "run", boom)
    r = deps.install("x")
    assert r["ok"] is False and "could not run" in r["error"]


def test_detect_exception_never_propagates(monkeypatch):
    def boom():
        raise RuntimeError("which blew up")
    monkeypatch.setitem(deps.DEPS_BY_NAME, "x", deps.Dep("x", "s", boom, ["true"]))
    monkeypatch.setattr(deps.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    # status() and install() must both swallow a detect exception.
    assert deps.status()  # does not raise
    r = deps.install("x")
    assert r["ok"] is False  # detect-after-install raised -> treated as absent


def test_pip_install_falls_back_to_uv_when_pip_absent(monkeypatch):
    # uv tool envs ship without pip -> must not emit a dead `python -m pip` argv.
    monkeypatch.setattr(deps, "_module_present", lambda n: False)
    monkeypatch.setattr(deps, "_binary_present", lambda n: n == "uv")
    argv = deps._pip_install_argv(["cognee"])
    assert argv[:3] == ["uv", "pip", "install"] and "--python" in argv and argv[-1] == "cognee"
    # when pip IS importable, use it directly
    monkeypatch.setattr(deps, "_module_present", lambda n: n == "pip")
    assert deps._pip_install_argv(["cognee"])[1:3] == ["-m", "pip"]


def test_ua_detect_uses_install_marker_not_binary(monkeypatch, tmp_path):
    # UA installs a plugin/skills tree, no PATH binary -> detect a real completion
    # marker (repo/install.sh), NOT a bare leftover dir a failed install leaves.
    monkeypatch.setattr(deps, "_binary_present", lambda n: False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert deps._ua_installed() is False
    (tmp_path / ".understand-anything").mkdir()
    assert deps._ua_installed() is False  # bare dir is not enough
    repo = tmp_path / ".understand-anything" / "repo"
    repo.mkdir()
    (repo / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    assert deps._ua_installed() is True


def test_install_uses_uv_argv_for_pip_dep_without_pip(monkeypatch):
    monkeypatch.setattr(deps, "_module_present", lambda n: False)  # nothing importable
    monkeypatch.setattr(deps, "_binary_present", lambda n: n == "uv")
    seen = {}

    def fake_run(argv, **k):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(deps.subprocess, "run", fake_run)
    deps.install("cognee")
    assert seen["argv"][:3] == ["uv", "pip", "install"]  # not `python -m pip`


def test_setup_is_top_level_command_interactive_by_default():
    from tesserae.cli import _build_setup_parser, _setup_wants_interactive
    from tesserae.cli_tree import KNOWN_COMMANDS

    assert "setup" in KNOWN_COMMANDS  # `tesserae setup`, not just `config setup`
    flagged = _build_setup_parser().parse_args(["--install", "all"])
    assert flagged._handler == "_handle_setup_machine"
    assert _setup_wants_interactive(flagged) is False  # flags given -> skip prompts
    # bare invocation under a non-TTY (CI/scripts) must NOT block on input
    assert _setup_wants_interactive(_build_setup_parser().parse_args([])) is False
    # only top-level `setup` opts into interactive; the `config setup` alias keeps
    # its legacy no-op = status behavior (no _interactive_default flag).
    from tesserae.cli import _build_config_parser

    cfg_setup = _build_config_parser().parse_args(["setup"])
    assert getattr(cfg_setup, "_interactive_default", False) is False
    assert _setup_wants_interactive(cfg_setup) is False


def test_resolve_targets_expands_all_and_dedups():
    targets, unknown = _resolve_dep_targets(["all"], False)
    assert targets == deps.DEP_NAMES and unknown == []
    targets, unknown = _resolve_dep_targets(["memex", "memex", "cognee"], False)
    assert targets == ["memex", "cognee"] and unknown == []
    targets, unknown = _resolve_dep_targets(["bogus"], False)
    assert unknown == ["bogus"]


def test_merge_global_llm_config_only_changes_passed_keys():
    m = _merge_global_llm_config({"keep": 1}, llm_provider="codex", reasoning_effort="medium")
    assert m["keep"] == 1
    assert m["llm_provider"] == "codex"
    assert m["llm_codex_reasoning_effort"] == "medium"
    # An unrelated existing provider survives when not passed.
    m2 = _merge_global_llm_config({"llm_provider": "claude"}, reasoning_effort="high")
    assert m2["llm_provider"] == "claude" and m2["llm_codex_reasoning_effort"] == "high"

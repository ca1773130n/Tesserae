"""Optional-dependency registry: detect + install, and the global config merge."""

from __future__ import annotations

import types

from tesserae import deps
from tesserae.cli import _merge_global_llm_config


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


def test_merge_global_llm_config_only_changes_passed_keys():
    m = _merge_global_llm_config({"keep": 1}, llm_provider="codex", reasoning_effort="medium")
    assert m["keep"] == 1
    assert m["llm_provider"] == "codex"
    assert m["llm_codex_reasoning_effort"] == "medium"
    # An unrelated existing provider survives when not passed.
    m2 = _merge_global_llm_config({"llm_provider": "claude"}, reasoning_effort="high")
    assert m2["llm_provider"] == "claude" and m2["llm_codex_reasoning_effort"] == "high"

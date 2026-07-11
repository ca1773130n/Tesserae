"""Tests for tesserae.setup.apply."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tesserae.setup import SetupPlan, build_plan, detect
from tesserae.setup.apply import DriftError, SetupResult, apply_plan
from tesserae.setup.plan import InstallAction


def test_apply_writes_config_without_running_installs(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(report)
    plan.install_actions = []
    plan.run_actions = []
    result = apply_plan(plan)
    assert isinstance(result, SetupResult)
    assert result.config_path.exists()
    payload = json.loads(result.config_path.read_text())
    assert payload["project"]["name"] == plan.name
    # The unread "extraction" block is gone; runtime llm_* keys replace it.
    assert "extraction" not in payload


def test_apply_skips_install_actions_without_confirmation(tmp_path: Path, monkeypatch) -> None:
    report = detect(tmp_path)
    plan = build_plan(report)
    plan.install_actions = [
        InstallAction(id="fake", description="install fake", command="echo fake-sentinel")
    ]

    seen_commands: list = []
    original_run = subprocess.run

    def recording_run(args, *rest, **kwargs):
        seen_commands.append(args)
        return original_run(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording_run)
    result = apply_plan(plan, confirm_install_actions=False)
    assert not any("fake-sentinel" in str(cmd) for cmd in seen_commands)
    assert any(a.get("status") == "skipped" for a in result.actions_taken)


def test_apply_runs_install_actions_when_confirmed(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(report)
    plan.install_actions = [
        InstallAction(id="fake", description="echo", command="echo hello")
    ]
    result = apply_plan(plan, confirm_install_actions=True)
    statuses = [a.get("status") for a in result.actions_taken]
    assert "installed" in statuses
    install_row = next(a for a in result.actions_taken if a.get("id") == "fake")
    assert "hello" in install_row.get("stdout", "")


def test_apply_installs_agent_pointer(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(report)
    apply_plan(plan)
    text = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "tesserae:pointer:begin" in text
    result = apply_plan(plan)  # second apply: pointer must be byte-stable
    entry = next(a for a in result.actions_taken if a["id"] == "agent-pointer")
    assert entry["files"]["AGENTS.md"] == "current"


def test_apply_skips_agent_pointer_when_disabled(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(report, overrides={"install_agent_pointer": False})
    result = apply_plan(plan)
    assert not (tmp_path / "AGENTS.md").exists()
    assert all(a["id"] != "agent-pointer" for a in result.actions_taken)


def test_apply_custom_provider_persists_all_llm_keys(
    tmp_path: Path, capsys
) -> None:
    report = detect(tmp_path)
    plan = build_plan(
        report,
        overrides={
            "llm_provider": "custom",
            "llm_base_url": "https://llm.example/v1",
            "llm_api_key": "sk-apply-secret",
            "llm_model": "claude-opus-4-6",
        },
    )
    plan.install_actions = []
    plan.run_actions = []
    result = apply_plan(plan)
    cfg = json.loads(result.config_path.read_text())
    assert cfg["llm_provider"] == "custom"
    assert cfg["llm_base_url"] == "https://llm.example/v1"
    assert cfg["llm_api_key"] == "sk-apply-secret"
    assert cfg["llm_model"] == "claude-opus-4-6"
    assert "extraction" not in cfg
    # persisting a plaintext key prints a one-line warning and records it
    assert "plaintext" in capsys.readouterr().err
    assert any("plaintext" in w for w in result.warnings)


def test_apply_reinit_merges_sources_and_memory_backends(tmp_path: Path) -> None:
    plan = build_plan(detect(tmp_path), overrides={"sources": ["README.md"]})
    plan.install_actions = []
    plan.run_actions = []
    result = apply_plan(plan)
    cfg = json.loads(result.config_path.read_text())

    # Simulate user edits between inits. A legacy cognee section (backend
    # removed in 0.19) is user data too — the merge must not drop it.
    cfg["sources"].append("notes/extra.md")
    cfg.setdefault("memory_backends", {})["cognee"] = {
        "enabled": True,
        "user_marker": "keep-me",
    }
    cfg["custom_top_level"] = {"keep": True}
    result.config_path.write_text(json.dumps(cfg, indent=2) + "\n")

    plan2 = build_plan(
        detect(tmp_path), overrides={"sources": ["README.md", "docs"]}
    )
    plan2.install_actions = []
    plan2.run_actions = []
    result2 = apply_plan(plan2)
    cfg2 = json.loads(result2.config_path.read_text())
    # union of existing + plan sources, no duplicates
    assert "notes/extra.md" in cfg2["sources"]
    assert "docs" in cfg2["sources"]
    assert cfg2["sources"].count("README.md") == 1
    # user-tuned memory backends survive re-init
    assert cfg2["memory_backends"]["cognee"]["enabled"] is True
    assert cfg2["memory_backends"]["cognee"]["user_marker"] == "keep-me"
    # unknown user keys survive too
    assert cfg2["custom_top_level"] == {"keep": True}


def test_apply_detects_drift_with_warn_policy(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(report)
    plan.detection.python.version = "0.0.0"
    result = apply_plan(plan, drift_policy="warn")
    assert any("drift" in w.lower() or "python" in w.lower() for w in result.warnings)


def test_apply_aborts_on_drift_when_policy_is_abort(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(report)
    plan.detection.python.version = "0.0.0"
    with pytest.raises(DriftError):
        apply_plan(plan, drift_policy="abort")

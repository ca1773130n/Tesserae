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
    assert payload["extraction"]["backend"] == plan.extractor


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

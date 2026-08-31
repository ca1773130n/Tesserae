"""Tests for tesserae.setup.plan."""

from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.setup import build_plan, detect
from tesserae.setup.plan import (
    InstallAction,
    PlanValidationError,
    SetupPlan,
)


def test_build_plan_uses_recommendations(tmp_path: Path, monkeypatch) -> None:
    import shutil

    from tesserae import llm_json

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/fake/bin/claude" if name == "claude" else None,
    )
    monkeypatch.setattr(llm_json, "_claude_cli_available", lambda: True)
    report = detect(tmp_path)
    plan = build_plan(report)
    assert plan.extractor == "claude-cli"
    assert plan.llm_provider == "claude"
    assert plan.project_root == tmp_path.resolve()


def test_build_plan_records_llm_fields(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(
        report,
        overrides={
            "llm_provider": "custom",
            "llm_base_url": "https://llm.example/v1",
            "llm_api_key": "sk-plan-key",
            "llm_model": "claude-opus-4-6",
            "codex_home": "~/.codex-alt",
        },
    )
    assert plan.llm_provider == "custom"
    assert plan.llm_base_url == "https://llm.example/v1"
    assert plan.llm_api_key == "sk-plan-key"
    assert plan.llm_model == "claude-opus-4-6"
    assert plan.codex_home == "~/.codex-alt"
    assert plan.intent["llm_provider"] == "custom"


def test_build_plan_rejects_unknown_llm_provider(tmp_path: Path) -> None:
    # "openai" used to stand in for an unknown provider here. It is a real one
    # now — the only way to reach an OpenAI-compatible endpoint — so this needs
    # a value that is still genuinely not a provider.
    report = detect(tmp_path)
    with pytest.raises(PlanValidationError):
        build_plan(report, overrides={"llm_provider": "openrouter"})


def test_build_plan_applies_overrides(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(
        report,
        overrides={"name": "my-wiki", "extractor": "deterministic"},
    )
    assert plan.name == "my-wiki"
    assert plan.extractor == "deterministic"


def test_build_plan_ignores_legacy_understand_anything_overrides(tmp_path: Path) -> None:
    """Removed backend: legacy UA override keys are swallowed (no unrecognized-key
    warning), requesting the integration only yields a removal warning, and no
    UA action or external tool is ever planned."""
    report = detect(tmp_path)
    plan = build_plan(
        report,
        overrides={
            "include_understand_anything": True,
            "install_understand_anything": True,
            "understand_anything_platform": "codex",
            "understand_anything_command": "echo hi",
            "run_understand_anything": True,
        },
    )
    ids = {a.id for a in plan.install_actions}
    tool_ids = {t.get("id") for t in plan.external_tools}
    assert "understand-anything" not in ids and "understand-anything" not in tool_ids
    assert not any("unrecognized override keys" in w for w in plan.warnings)
    assert any("understand-anything was removed" in w for w in plan.warnings)


def test_build_plan_skips_raganything_install_on_old_python(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("sys.version_info", (3, 9, 7, "final", 0))
    report = detect(tmp_path)
    plan = build_plan(report, overrides={"include_raganything": True})
    rag_installs = [a for a in plan.install_actions if a.id == "raganything"]
    assert rag_installs == []
    assert any("3.10" in w for w in plan.warnings)


def test_setup_plan_round_trip_serialization(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(report)
    payload = plan.model_dump_json()
    restored = SetupPlan.model_validate_json(payload)
    assert restored.name == plan.name
    assert restored.extractor == plan.extractor
    assert restored.detection.project.project_root == plan.detection.project.project_root


def test_build_plan_rejects_unknown_extractor(tmp_path: Path) -> None:
    report = detect(tmp_path)
    with pytest.raises(PlanValidationError):
        build_plan(report, overrides={"extractor": "not-a-real-backend"})


def test_build_plan_records_install_agent_pointer_override(tmp_path: Path) -> None:
    report = detect(tmp_path)
    assert build_plan(report).install_agent_pointer is True
    plan = build_plan(report, overrides={"install_agent_pointer": False})
    assert plan.install_agent_pointer is False
    assert plan.intent["install_agent_pointer"] is False


def test_the_global_config_beats_detection_for_the_llm_provider(
    tmp_path: Path, monkeypatch
) -> None:
    """Detection is a guess; the global config is a decision.

    `detection.py` checks the Claude CLI FIRST, so a machine with both CLIs had
    `llm_provider: "claude"` written into every new project however emphatically
    the global config said `codex` — and the project value then shadowed the
    global for good, because `resolve_llm_client_settings` reads the project
    layer first.

    Measured cost on 2026-08-24: a 1,552-document eval corpus compiled against a
    Claude Code subscription the operator had globally configured to use Codex.
    It hit the session limit at 5am and 1,116 documents (71.9%) fell back to
    deterministic extraction, and `init` had also pinned
    `llm_claude_config_dirs` to one specific account directory.
    """
    import shutil

    from tesserae import llm_json

    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/fake/bin/claude" if name == "claude" else None,
    )
    monkeypatch.setattr(llm_json, "_claude_cli_available", lambda: True)
    monkeypatch.setattr(
        llm_json, "_load_global_llm_config",
        lambda: {"llm_provider": "codex", "llm_model": "gpt-5.6-luna",
                 "llm_codex_home": "/tmp/custom-codex"},
    )
    report = detect(tmp_path)
    assert report.recommended.llm_provider == "claude", "detection still guesses claude"

    plan = build_plan(report)
    assert plan.llm_provider == "codex", (
        "detection recommended claude; the global config said codex and wins"
    )
    assert plan.llm_model == "gpt-5.6-luna"
    assert plan.codex_home == "/tmp/custom-codex", (
        "the codex home must be configurable, not hardcoded"
    )
    assert not plan.claude_config_dir, (
        "a claude account pin under a codex provider is what spent the wrong "
        "subscription"
    )


def test_an_explicit_override_still_beats_the_global_config(
    tmp_path: Path, monkeypatch
) -> None:
    """override > global > detection, in that order."""
    from tesserae import llm_json

    monkeypatch.setattr(
        llm_json, "_load_global_llm_config",
        lambda: {"llm_provider": "codex", "llm_model": "gpt-5.6-luna"},
    )
    report = detect(tmp_path)
    plan = build_plan(
        report, overrides={"llm_provider": "custom", "llm_model": "opus"}
    )
    assert plan.llm_provider == "custom"
    assert plan.llm_model == "opus"

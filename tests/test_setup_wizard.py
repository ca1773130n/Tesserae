"""Smoke tests for tesserae.setup.wizard."""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from tesserae.setup import build_plan, detect
from tesserae.setup import wizard as wizard_mod
from tesserae.setup.wizard import (
    WizardNotInteractive,
    _provider_choices,
    render_review,
    run_wizard,
)


def test_run_wizard_raises_when_no_tty(tmp_path: Path) -> None:
    report = detect(tmp_path)
    with pytest.raises(WizardNotInteractive):
        run_wizard(report)


def test_render_review_is_plain_text(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(report)
    text = render_review(plan)
    assert plan.name in text
    assert plan.extractor in text


def test_render_review_never_echoes_api_key(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(
        report,
        overrides={
            "llm_provider": "custom",
            "llm_base_url": "https://llm.example/v1",
            "llm_api_key": "sk-review-secret",
            "llm_model": "claude-opus-4-6",
        },
    )
    text = render_review(plan)
    assert "custom" in text
    assert "sk-review-secret" not in text


def test_provider_choices_honor_credential_probe(tmp_path: Path, monkeypatch) -> None:
    """A logged-out CLI on PATH must not be offered; anthropic/custom always are."""
    import shutil

    from tesserae import llm_json

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: f"/fake/bin/{name}" if name in {"claude", "codex"} else None,
    )
    monkeypatch.setattr(llm_json, "_claude_cli_available", lambda: False)
    monkeypatch.setattr(llm_json, "_codex_cli_available", lambda: True)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    report = detect(tmp_path)
    values = [value for value, _ in _provider_choices(report)]
    assert "claude" not in values
    assert values == ["codex", "anthropic", "custom"]
    assert report.recommended.llm_provider == "codex"


def _interactive(monkeypatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


def test_wizard_custom_provider_records_all_llm_fields(
    tmp_path: Path, monkeypatch
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    report = detect(tmp_path)
    _interactive(monkeypatch)

    def fake_prompt_ask(prompt="", **kwargs):
        text = str(prompt)
        if "Wiki name" in text:
            return "wiz-custom"
        if "Source kind" in text:
            return "Repository"
        if "Additional source" in text:
            return ""
        if "Pick a provider" in text:
            return "2"  # choices are [anthropic, custom]
        if "Base URL" in text:
            return "https://llm.internal.example/v1"
        if "API key" in text:
            return "sk-custom-secret"
        if "Model name" in text:
            return "claude-opus-4-6"
        if "Toggle by number" in text:
            return ""
        return kwargs.get("default") or ""

    monkeypatch.setattr(wizard_mod.Prompt, "ask", staticmethod(fake_prompt_ask))
    monkeypatch.setattr(
        wizard_mod.Confirm, "ask", staticmethod(lambda *a, **k: True)
    )
    console = Console(file=StringIO(), force_terminal=False, width=100)
    plan = run_wizard(report, console=console)
    assert plan.llm_provider == "custom"
    assert plan.llm_base_url == "https://llm.internal.example/v1"
    assert plan.llm_api_key == "sk-custom-secret"
    assert plan.llm_model == "claude-opus-4-6"
    assert "sk-custom-secret" not in render_review(plan)


def test_wizard_claude_provider_blank_config_dir_means_auto(
    tmp_path: Path, monkeypatch
) -> None:
    import shutil

    from tesserae import llm_json

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/fake/bin/claude" if name == "claude" else None,
    )
    monkeypatch.setattr(llm_json, "_claude_cli_available", lambda: True)
    report = detect(tmp_path)
    _interactive(monkeypatch)

    def fake_prompt_ask(prompt="", **kwargs):
        text = str(prompt)
        if "Pick a provider" in text:
            return "1"  # claude is offered first when credentialed
        if "CLAUDE_CONFIG_DIR" in text:
            return ""  # blank = auto-discovery, nothing persisted
        if "Toggle by number" in text or "Additional source" in text:
            return ""
        return kwargs.get("default") or ""

    monkeypatch.setattr(wizard_mod.Prompt, "ask", staticmethod(fake_prompt_ask))
    monkeypatch.setattr(
        wizard_mod.Confirm, "ask", staticmethod(lambda *a, **k: True)
    )
    console = Console(file=StringIO(), force_terminal=False, width=100)
    plan = run_wizard(report, console=console)
    assert plan.llm_provider == "claude"
    assert plan.claude_config_dir is None

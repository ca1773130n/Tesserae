"""Tests for tesserae.setup.detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.setup import detect
from tesserae.setup.detection import DetectionReport


def test_detect_returns_report_for_empty_project(tmp_path: Path) -> None:
    report = detect(tmp_path)
    assert isinstance(report, DetectionReport)
    assert report.project.project_root == tmp_path.resolve()
    assert report.project.has_tesserae is False
    assert report.project.has_git is False


def test_detect_finds_claude_cli(tmp_path: Path, monkeypatch) -> None:
    import shutil

    from tesserae import llm_json

    def fake_which(name: str) -> str | None:
        return "/fake/bin/claude" if name == "claude" else None

    monkeypatch.setattr(shutil, "which", fake_which)
    monkeypatch.setattr(llm_json, "_claude_cli_available", lambda: True)
    report = detect(tmp_path)
    assert report.llm_clis["claude"].available is True
    assert report.llm_clis["claude"].credentialed is True
    assert report.llm_clis["claude"].binary == "/fake/bin/claude"
    assert report.llm_clis["codex"].available is False


def test_detect_reports_logged_out_cli_unavailable(tmp_path: Path, monkeypatch) -> None:
    """A CLI on PATH without credentials must not be reported available."""
    import shutil

    from tesserae import llm_json

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: f"/fake/bin/{name}" if name in {"claude", "codex"} else None,
    )
    monkeypatch.setattr(llm_json, "_claude_cli_available", lambda: False)
    monkeypatch.setattr(llm_json, "_codex_cli_available", lambda: False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    report = detect(tmp_path)
    assert report.llm_clis["claude"].available is False
    assert report.llm_clis["claude"].credentialed is False
    assert report.llm_clis["claude"].binary == "/fake/bin/claude"
    assert report.llm_clis["codex"].available is False
    assert report.recommended.extractor == "deterministic"
    assert report.recommended.llm_provider is None


def test_detect_recommends_anthropic_provider_from_api_key(
    tmp_path: Path, monkeypatch
) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    report = detect(tmp_path)
    assert report.recommended.llm_provider == "anthropic"


def test_detect_reads_api_key_presence_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    report = detect(tmp_path)
    assert report.api_keys["ANTHROPIC_API_KEY"] is True
    assert report.api_keys["OPENAI_API_KEY"] is False
    serialized = report.model_dump_json()
    assert "sk-secret-value" not in serialized


def test_detect_warns_on_old_python(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sys.version_info", (3, 9, 7, "final", 0))
    report = detect(tmp_path)
    assert any("3.10" in w for w in report.recommended.warnings)
    assert report.recommended.raganything_available is False


def test_detect_recommends_claude_when_present(tmp_path: Path, monkeypatch) -> None:
    import shutil

    from tesserae import llm_json

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/fake/bin/claude" if name == "claude" else None,
    )
    monkeypatch.setattr(llm_json, "_claude_cli_available", lambda: True)
    report = detect(tmp_path)
    assert report.recommended.extractor == "claude-cli"
    assert report.recommended.llm_provider == "claude"


def test_detect_recommends_deterministic_with_no_llm(tmp_path: Path, monkeypatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    report = detect(tmp_path)
    assert report.recommended.extractor == "deterministic"


def test_detect_ignores_understand_anything_artifact(tmp_path: Path) -> None:
    """Removed backend: a leftover .understand-anything dir is not fingerprinted."""
    (tmp_path / ".understand-anything").mkdir()
    (tmp_path / ".understand-anything" / "knowledge-graph.json").write_text("{}")
    report = detect(tmp_path)
    assert not hasattr(report.project, "has_understand_anything")
    assert not hasattr(report.recommended, "include_understand_anything")

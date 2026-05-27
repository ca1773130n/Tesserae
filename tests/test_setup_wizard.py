"""Smoke tests for tesserae.setup.wizard."""

from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.setup import build_plan, detect
from tesserae.setup.wizard import WizardNotInteractive, render_review, run_wizard


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

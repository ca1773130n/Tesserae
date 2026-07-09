"""Apply a SetupPlan: write config, run gated install/run actions."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..project import ProjectWiki
from .detection import DetectionReport, detect
from .plan import SetupPlan


class DriftError(RuntimeError):
    """Raised when apply_plan(drift_policy='abort') detects environment drift."""


class SetupResult(BaseModel):
    config_path: Path
    actions_taken: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    drift: dict[str, Any] = Field(default_factory=dict)
    wiki_root: Path


def _expand_command(command: str, project_root: Path) -> str:
    try:
        return command.format(
            python=shlex.quote(sys.executable),
            project=shlex.quote(str(project_root)),
        )
    except Exception:
        # Custom commands may contain literal `{` (e.g. Python f-string fragments).
        # If formatting can't resolve placeholders, return the command unchanged.
        return command


def _run_action(
    *,
    action_id: str,
    description: str,
    command: str,
    project_root: Path,
    status_pass: str,
    status_fail: str,
) -> dict[str, Any]:
    expanded = _expand_command(command, project_root)
    completed = subprocess.run(
        expanded,
        shell=True,
        cwd=project_root,
        text=True,
        capture_output=True,
    )
    return {
        "id": action_id,
        "description": description,
        "command": expanded,
        "returncode": completed.returncode,
        "status": status_pass if completed.returncode == 0 else status_fail,
        "stdout": (completed.stdout or "")[-2000:],
        "stderr": (completed.stderr or "")[-2000:],
    }


def _detect_drift(
    plan_detection: DetectionReport, current: DetectionReport
) -> dict[str, Any]:
    drift: dict[str, Any] = {}
    if plan_detection.python.version != current.python.version:
        drift["python_version"] = (
            plan_detection.python.version,
            current.python.version,
        )
    for name, plan_cli in plan_detection.llm_clis.items():
        current_cli = current.llm_clis.get(name)
        if current_cli and plan_cli.available != current_cli.available:
            drift[f"llm_{name}.available"] = (
                plan_cli.available,
                current_cli.available,
            )
    return drift


def _build_config_payload(plan: SetupPlan) -> dict[str, Any]:
    return {
        "project": {"name": plan.name, "source_kind": plan.source_kind},
        "sources": list(plan.sources),
        "extraction": {
            "backend": plan.extractor,
            "claude_config_dir": plan.claude_config_dir,
            "claude_model": plan.claude_model,
            "codex_model": plan.codex_model,
        },
        "external_tools": list(plan.external_tools),
        "memory_backends": dict(plan.memory_backends),
        "setup": {
            "wizard": "tesserae init",
            "updated": date.today().isoformat(),
        },
    }


def apply_plan(
    plan: SetupPlan,
    *,
    confirm_install_actions: bool = False,
    confirm_run_actions: bool = False,
    drift_policy: Literal["warn", "abort", "ignore"] = "warn",
) -> SetupResult:
    """Apply a SetupPlan. Writes config unconditionally; gates install/run actions."""
    project_root = Path(plan.project_root).resolve()
    warnings = list(plan.warnings)

    current = detect(project_root)
    drift = _detect_drift(plan.detection, current)
    if drift:
        if drift_policy == "abort":
            raise DriftError(f"Environment drift detected: {drift}")
        if drift_policy == "warn":
            warnings.append(f"environment drift detected at apply time: {drift}")

    wiki = ProjectWiki.init(
        project_root,
        name=plan.name,
        source_kind=plan.source_kind,
        sources=plan.sources,
    )
    cfg = wiki.config()
    cfg.update(_build_config_payload(plan))
    wiki.paths.config.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    actions_taken: list[dict[str, Any]] = []

    # Not gated behind confirm_install_actions: this executes no commands, is
    # byte-idempotent, and is confirmed in the wizard / reviewable in the plan
    # JSON — same trust level as the unconditional config write above.
    if plan.install_agent_pointer:
        from ..agent_harness import install_instruction_pointer

        pointer = install_instruction_pointer(project_root, plan.name)
        actions_taken.append(
            {
                "id": "agent-pointer",
                "description": "Install Tesserae pointer block into AGENTS.md/CLAUDE.md",
                "status": "installed",
                "files": pointer,
            }
        )

    for action in plan.install_actions:
        if not confirm_install_actions:
            actions_taken.append(
                {
                    "id": action.id,
                    "description": action.description,
                    "command": action.command,
                    "status": "skipped",
                    "reason": "confirm_install_actions=False",
                }
            )
            continue
        actions_taken.append(
            _run_action(
                action_id=action.id,
                description=action.description,
                command=action.command,
                project_root=project_root,
                status_pass="installed",
                status_fail="install_failed",
            )
        )

    for action in plan.run_actions:
        if not confirm_run_actions:
            actions_taken.append(
                {
                    "id": action.id,
                    "description": action.description,
                    "command": action.command,
                    "status": "skipped",
                    "reason": "confirm_run_actions=False",
                }
            )
            continue
        actions_taken.append(
            _run_action(
                action_id=action.id,
                description=action.description,
                command=action.command,
                project_root=project_root,
                status_pass="passed",
                status_fail="failed",
            )
        )

    for tool in plan.external_tools:
        if tool.get("id") == "understand-anything":
            from ..project_setup import materialize_understand_anything_source

            materialize_understand_anything_source(project_root, tool)

    return SetupResult(
        config_path=wiki.paths.config,
        actions_taken=actions_taken,
        warnings=warnings,
        drift=drift,
        wiki_root=wiki.root,
    )

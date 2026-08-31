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
    payload: dict[str, Any] = {
        "name": plan.name,
        "source_kind": plan.source_kind,
        "project": {"name": plan.name, "source_kind": plan.source_kind},
        "sources": list(plan.sources),
        "external_tools": list(plan.external_tools),
        "memory_backends": dict(plan.memory_backends),
        "setup": {
            "wizard": "tesserae init",
            "updated": date.today().isoformat(),
        },
    }
    # Runtime keys read by llm_json.resolve_llm_client_settings (env vars —
    # ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY / TESSERAE_LLM_MODEL — win over
    # these). Persisted only when set so untouched configs stay byte-stable.
    if plan.llm_provider:
        payload["llm_provider"] = plan.llm_provider
    if plan.claude_config_dir:
        payload["llm_claude_config_dirs"] = [plan.claude_config_dir]
    if plan.codex_home:
        payload["llm_codex_home"] = plan.codex_home
    if plan.llm_model:
        payload["llm_model"] = plan.llm_model
    if plan.llm_base_url:
        payload["llm_base_url"] = plan.llm_base_url
    if plan.llm_api_key:
        payload["llm_api_key"] = plan.llm_api_key
    if getattr(plan, "llm_auth_token", None):
        payload["llm_auth_token"] = plan.llm_auth_token
    if getattr(plan, "llm_api_style", None):
        payload["llm_api_style"] = plan.llm_api_style
    return payload


def _merge_config_payload(
    payload: dict[str, Any], existing_cfg: dict[str, Any]
) -> dict[str, Any]:
    """Re-init merges instead of clobbering: union sources, preserve
    user-tuned memory_backends, keep external tools the plan doesn't manage."""
    existing_sources = [
        s for s in (existing_cfg.get("sources") or []) if isinstance(s, str)
    ]
    if existing_sources:
        payload["sources"] = existing_sources + [
            s for s in payload["sources"] if s not in existing_sources
        ]
    existing_backends = existing_cfg.get("memory_backends")
    if isinstance(existing_backends, dict) and existing_backends:
        payload["memory_backends"] = {
            **payload["memory_backends"],
            **existing_backends,
        }
    existing_tools = existing_cfg.get("external_tools")
    if isinstance(existing_tools, list) and existing_tools:
        plan_tool_ids = {
            t.get("id") for t in payload["external_tools"] if isinstance(t, dict)
        }
        payload["external_tools"] = list(payload["external_tools"]) + [
            t
            for t in existing_tools
            if isinstance(t, dict) and t.get("id") not in plan_tool_ids
        ]
    return payload


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

    # Capture any pre-existing config BEFORE ProjectWiki.init rewrites it:
    # re-init must merge with what the user already has, not clobber it.
    existing_cfg: dict[str, Any] = {}
    existing_config_path = project_root / ".tesserae" / "config.json"
    if existing_config_path.exists():
        try:
            loaded = json.loads(existing_config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing_cfg = loaded
        except Exception:
            warnings.append(
                f"existing config at {existing_config_path} is unreadable; "
                "rewriting it from the plan"
            )

    wiki = ProjectWiki.init(
        project_root,
        name=plan.name,
        source_kind=plan.source_kind,
        sources=plan.sources,
    )
    cfg = wiki.config()
    if existing_cfg:
        # Restore user keys over the freshly templated config; the merged
        # payload below re-asserts every key the plan actually owns.
        cfg.update(existing_cfg)
    cfg.update(_merge_config_payload(_build_config_payload(plan), existing_cfg))
    wiki.paths.config.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if plan.llm_api_key:
        key_warning = (
            f"llm_api_key is stored in plaintext in {wiki.paths.config}; "
            "prefer the ANTHROPIC_API_KEY environment variable"
        )
        warnings.append(key_warning)
        print(f"warning: {key_warning}", file=sys.stderr)

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

    return SetupResult(
        config_path=wiki.paths.config,
        actions_taken=actions_taken,
        warnings=warnings,
        drift=drift,
        wiki_root=wiki.root,
    )

"""SetupPlan model + build_plan(detection, overrides)."""

from __future__ import annotations

import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from ..project import (
    default_cognee_backend_config,
    default_raganything_backend_config,
    sanitize_server_name,
)
from .detection import DetectionReport


Extractor = Literal["deterministic", "claude-cli", "codex", "selective-claude"]


class PlanValidationError(ValueError):
    """Raised when build_plan overrides produce an invalid plan."""


class InstallAction(BaseModel):
    id: str
    description: str
    command: str
    required: bool = False


class RunAction(BaseModel):
    id: str
    description: str
    command: str


class SetupPlan(BaseModel):
    project_root: Path
    name: str
    source_kind: str = "Repository"
    sources: list[str] = Field(default_factory=list)

    extractor: Extractor = "deterministic"
    claude_config_dir: Optional[str] = None
    claude_model: Optional[str] = None
    codex_model: Optional[str] = None

    external_tools: list[dict[str, Any]] = Field(default_factory=list)
    memory_backends: dict[str, dict[str, Any]] = Field(default_factory=dict)

    install_actions: list[InstallAction] = Field(default_factory=list)
    run_actions: list[RunAction] = Field(default_factory=list)

    detection: DetectionReport
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


def _understand_anything_install_command(platform: str = "codex") -> str:
    return (
        "curl -fsSL https://raw.githubusercontent.com/Lum1104/Understand-Anything/main/install.sh "
        f"| bash -s {shlex.quote(platform)}"
    )


def _understand_anything_refresh_command(platform: str = "codex") -> str:
    return (
        "{python} -m tesserae.understand_anything_refresh "
        "--project {project} "
        f"--platform {shlex.quote(platform)}"
    )


def _raganything_refresh_command(parser: str = "mineru") -> str:
    return (
        "{python} -m tesserae.raganything_refresh "
        "--project {project} "
        f"--parser {shlex.quote(parser)}"
    )


def _raganything_install_command(extras: str = "all") -> str:
    if extras:
        return f"{{python}} -m pip install 'raganything[{extras}]>=1.3.0' docling"
    return "{python} -m pip install 'raganything>=1.3.0' docling"


def build_plan(
    detection: DetectionReport,
    *,
    overrides: Optional[dict[str, Any]] = None,
) -> SetupPlan:
    """Build a SetupPlan from a DetectionReport with optional field overrides."""

    overrides = dict(overrides or {})
    root = Path(overrides.pop("project_root", detection.project.project_root))

    include_understand_anything = bool(
        overrides.pop(
            "include_understand_anything",
            detection.recommended.include_understand_anything,
        )
    )
    understand_anything_platform = str(
        overrides.pop("understand_anything_platform", "codex")
    )
    install_understand_anything = bool(
        overrides.pop("install_understand_anything", include_understand_anything)
    )

    include_raganything = bool(overrides.pop("include_raganything", False))
    raganything_extras = str(overrides.pop("raganything_extras", "all"))
    raganything_parser = str(overrides.pop("raganything_parser", "mineru"))
    install_raganything = bool(
        overrides.pop(
            "install_raganything",
            include_raganything and not detection.python.raganything_importable,
        )
    )

    enable_cognee = bool(overrides.pop("enable_cognee", True))
    cognee_mode = str(overrides.pop("cognee_mode", "codex_cognify"))
    cognee_auto_cognify = bool(overrides.pop("cognee_auto_cognify", False))
    install_cognee = bool(overrides.pop("install_cognee", False))

    name = str(overrides.pop("name", None) or sanitize_server_name(root.name))
    source_kind = str(overrides.pop("source_kind", "Repository"))
    sources = list(overrides.pop("sources", detection.project.default_sources))

    extractor = overrides.pop("extractor", detection.recommended.extractor)
    claude_config_dir = overrides.pop(
        "claude_config_dir", detection.recommended.claude_config_dir
    )
    claude_model = overrides.pop("claude_model", None)
    codex_model = overrides.pop("codex_model", detection.recommended.codex_model)

    warnings = list(detection.recommended.warnings)
    external_tools: list[dict[str, Any]] = []
    memory_backends: dict[str, dict[str, Any]] = {}
    install_actions: list[InstallAction] = []
    run_actions: list[RunAction] = []

    if include_understand_anything:
        projection = ".tesserae/external/understand-anything.md"
        if projection not in sources:
            sources.append(projection)
        external_tools.append(
            {
                "id": "understand-anything",
                "name": "Understand Anything",
                "artifact": ".understand-anything/knowledge-graph.json",
                "source": projection,
                "refresh_command": _understand_anything_refresh_command(
                    understand_anything_platform
                ),
                "auto_refresh": True,
                "sync_mode": "native_graph",
                "preserve_markdown_projection": True,
                "managed_refresh": True,
                "enabled": True,
                "install": {
                    "enabled": True,
                    "auto_install": install_understand_anything,
                    "platform": understand_anything_platform,
                    "command": _understand_anything_install_command(
                        understand_anything_platform
                    ),
                },
            }
        )
        if install_understand_anything:
            install_actions.append(
                InstallAction(
                    id="understand-anything",
                    description=(
                        f"Install/update Understand Anything for platform "
                        f"{understand_anything_platform}"
                    ),
                    command=_understand_anything_install_command(
                        understand_anything_platform
                    ),
                )
            )

    if enable_cognee:
        cognee = default_cognee_backend_config(name)
        cognee["mode"] = cognee_mode
        cognee["auto_cognify"] = cognee_auto_cognify
        cognee.setdefault("install", {})
        cognee["install"]["auto_install"] = install_cognee
        memory_backends["cognee"] = cognee
        if install_cognee:
            install_command = str(cognee.get("install", {}).get("command") or "")
            if install_command:
                install_actions.append(
                    InstallAction(
                        id="cognee",
                        description="Install Cognee memory backend",
                        command=install_command,
                    )
                )

    if include_raganything:
        if not detection.recommended.raganything_available:
            warnings.append(
                "RAG-Anything requested but Python < 3.10; skipping install."
            )
        backend = default_raganything_backend_config(name)
        backend["enabled"] = detection.recommended.raganything_available
        backend["parser"] = raganything_parser
        memory_backends["raganything"] = backend
        if install_raganything and detection.recommended.raganything_available:
            install_actions.append(
                InstallAction(
                    id="raganything",
                    description="Install raganything + docling",
                    command=_raganything_install_command(raganything_extras),
                )
            )
        if detection.recommended.raganything_available:
            external_tools.append(
                {
                    "id": "raganything",
                    "name": "RAG-Anything",
                    "artifact": ".tesserae/external/raganything/manifest.json",
                    "source": ".tesserae/external/raganything/manifest.json",
                    "refresh_command": _raganything_refresh_command(raganything_parser),
                    "auto_refresh": False,
                    "sync_mode": "native_graph",
                    "parser": raganything_parser,
                    "extras": raganything_extras,
                    "managed_refresh": True,
                    "enabled": True,
                    "install": {
                        "enabled": True,
                        "auto_install": install_raganything,
                        "command": _raganything_install_command(raganything_extras),
                    },
                }
            )

    if overrides:
        warnings.append(f"unrecognized override keys: {sorted(overrides.keys())}")

    try:
        return SetupPlan(
            project_root=root.resolve(),
            name=name,
            source_kind=source_kind,
            sources=sources,
            extractor=extractor,
            claude_config_dir=claude_config_dir,
            claude_model=claude_model,
            codex_model=codex_model,
            external_tools=external_tools,
            memory_backends=memory_backends,
            install_actions=install_actions,
            run_actions=run_actions,
            detection=detection,
            warnings=warnings,
        )
    except ValidationError as exc:
        raise PlanValidationError(str(exc)) from exc

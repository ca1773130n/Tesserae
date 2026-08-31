"""SetupPlan model + build_plan(detection, overrides)."""

from __future__ import annotations

import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from ..project import (
    default_raganything_backend_config,
    sanitize_server_name,
)
from .detection import DetectionReport, LlmProvider


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
    codex_home: Optional[str] = None

    # Runtime LLM client settings, persisted as config.json llm_* keys and
    # resolved by llm_json.resolve_llm_client_settings (env wins over config).
    llm_provider: Optional[LlmProvider] = None
    llm_model: Optional[str] = None
    llm_base_url: Optional[str] = None
    llm_api_key: Optional[str] = None
    #: The bearer credential and the wire protocol. Without these the plan could
    #: carry a custom endpoint's URL and model but not how to authenticate to it
    #: or which protocol to speak — so `setup` accepted both flags and dropped them.
    llm_auth_token: Optional[str] = None
    llm_api_style: Optional[str] = None

    install_agent_pointer: bool = True

    external_tools: list[dict[str, Any]] = Field(default_factory=list)
    memory_backends: dict[str, dict[str, Any]] = Field(default_factory=dict)

    install_actions: list[InstallAction] = Field(default_factory=list)
    run_actions: list[RunAction] = Field(default_factory=list)

    # Captures the override dict that build_plan consumed. Trusted callers
    # (the CLI wizard) ignore this; the MCP apply path uses it to *regenerate*
    # action lists server-side instead of trusting caller-supplied command
    # strings (defense against MCP arbitrary-command-execution).
    intent: dict[str, Any] = Field(default_factory=dict)

    detection: DetectionReport
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


def _raganything_refresh_command(parser: str = "mineru") -> str:
    return (
        "{python} -m tesserae.raganything_refresh "
        "--project {project} "
        f"--parser {shlex.quote(parser)}"
    )


_RAGANYTHING_EXTRA_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_raganything_extras(extras: str) -> str:
    """Validate a comma-separated pip ``extras`` spec for safe shell interpolation.

    ``extras`` reaches us from caller-supplied overrides (including the MCP
    ``tesserae_setup_apply`` path) and is interpolated into a pip command that
    ``setup/apply.py`` runs with ``shell=True``. Each token must therefore be a
    bare extra name (PEP 508 extras are ``[A-Za-z0-9_-]`` after normalisation);
    anything else (quotes, spaces, ``;``, ``]``, ``$()`` …) could break out of
    the ``raganything[...]`` bracket and inject arbitrary shell. Reject rather
    than sanitise so a malformed/hostile value fails loudly instead of silently
    installing the wrong thing.
    """
    tokens = [t.strip() for t in extras.split(",") if t.strip()]
    bad = [t for t in tokens if not _RAGANYTHING_EXTRA_RE.match(t)]
    if bad:
        raise PlanValidationError(
            f"invalid raganything extras {bad!r}: each extra must match "
            f"[A-Za-z0-9_-]+ (got {extras!r})"
        )
    return ",".join(tokens)


def _raganything_install_command(extras: str = "all") -> str:
    extras = _validate_raganything_extras(extras)
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
    recorded_intent: dict[str, Any] = {
        k: v for k, v in overrides.items() if v is not None
    }
    root = Path(overrides.pop("project_root", detection.project.project_root))

    # Removed backend: swallow legacy understand-anything override keys so old
    # callers don't trip the unrecognized-key warning; requesting it warns.
    ua_requested = bool(overrides.pop("include_understand_anything", False))
    for _legacy_ua_key in (
        "understand_anything_platform",
        "install_understand_anything",
        "understand_anything_command",
        "run_understand_anything",
    ):
        overrides.pop(_legacy_ua_key, None)

    include_raganything = bool(overrides.pop("include_raganything", False))
    raganything_extras = str(overrides.pop("raganything_extras", "all"))
    raganything_parser = str(overrides.pop("raganything_parser", "mineru"))
    install_raganything = bool(
        overrides.pop(
            "install_raganything",
            include_raganything and not detection.python.raganything_importable,
        )
    )

    # Removed backend (0.19): swallow legacy cognee override keys so old
    # callers don't trip the unrecognized-key warning; requesting it warns.
    cognee_requested = bool(overrides.pop("enable_cognee", False)) or bool(
        overrides.pop("install_cognee", False)
    )
    for _legacy_cognee_key in ("cognee_mode", "cognee_auto_cognify"):
        overrides.pop(_legacy_cognee_key, None)

    name = str(overrides.pop("name", None) or sanitize_server_name(root.name))
    source_kind = str(overrides.pop("source_kind", "Repository"))
    sources = list(overrides.pop("sources", detection.project.default_sources))

    extractor = overrides.pop("extractor", detection.recommended.extractor)
    # PRECEDENCE: explicit override > the machine-wide config > detection.
    #
    # Detection is a GUESS about what is installed; the global config is the
    # user's standing DECISION. Before this, detection won, and
    # `detection.py` checks the Claude CLI first — so a machine with both CLIs
    # got `llm_provider: "claude"` written into every new project however
    # emphatically the global config said `codex`, and the project value then
    # shadowed the global for good, because `resolve_llm_client_settings` reads
    # the project layer first.
    #
    # Measured cost of that on 2026-08-24: a 1,552-document eval corpus was
    # compiled against a Claude Code subscription that the operator had
    # configured globally to use Codex. It exhausted the session limit at 5am
    # and 1,116 documents (71.9%) silently fell back to deterministic
    # extraction. `init` had also pinned `llm_claude_config_dirs` to one
    # specific account directory, which nothing had asked for.
    from tesserae.llm_json import _load_global_llm_config

    global_llm = _load_global_llm_config()

    def _pick(override_key: str, global_key: str, detected):
        """override > global > detected, with `None` never masking a later layer."""
        if override_key in overrides:
            return overrides.pop(override_key)
        chosen = global_llm.get(global_key)
        return chosen if chosen is not None else detected

    llm_provider = _pick("llm_provider", "llm_provider",
                         detection.recommended.llm_provider)
    llm_model = _pick("llm_model", "llm_model", None)
    codex_model = _pick("codex_model", "llm_model",
                        detection.recommended.codex_model)
    codex_home = _pick("codex_home", "llm_codex_home", None)
    # llm_claude_model is written by NOTHING — every writer uses llm_model
    # (cli.py, setup/apply.py, project.py). Reading only the dead name meant
    # setup silently saw no configured model. Fall back to the live key, and
    # read it directly rather than through _pick, which pops from overrides.
    claude_model = _pick("claude_model", "llm_claude_model",
                         global_llm.get("llm_model"))
    # A claude config dir is only meaningful when claude is the provider.
    # Writing one under a codex provider is the pin that surprised an operator
    # into spending the wrong subscription.
    # Same defect: every writer uses the PLURAL llm_claude_config_dirs, so the
    # singular key read here found nothing. Take the first of the real list.
    _dirs = global_llm.get("llm_claude_config_dirs")
    _dirs_first = _dirs[0] if isinstance(_dirs, list) and _dirs else None
    claude_config_dir = _dirs_first or _pick("claude_config_dir", "llm_claude_config_dir",
                              detection.recommended.claude_config_dir)
    if llm_provider and llm_provider != "claude":
        claude_config_dir = None
    llm_base_url = overrides.pop("llm_base_url", None)
    llm_api_key = overrides.pop("llm_api_key", None)
    llm_auth_token = overrides.pop("llm_auth_token", None)
    llm_api_style = overrides.pop("llm_api_style", None)
    install_agent_pointer = bool(overrides.pop("install_agent_pointer", True))

    warnings = list(detection.recommended.warnings)
    external_tools: list[dict[str, Any]] = []
    memory_backends: dict[str, dict[str, Any]] = {}
    install_actions: list[InstallAction] = []
    run_actions: list[RunAction] = []

    if ua_requested:
        warnings.append(
            "understand-anything was removed — code-structure nodes are "
            "extracted natively; see tesserae code ingest"
        )

    if cognee_requested:
        warnings.append(
            "cognee backend was removed in 0.19 — request ignored; "
            "use plain query or ask"
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
            codex_home=codex_home,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_auth_token=llm_auth_token,
            llm_api_style=llm_api_style,
            install_agent_pointer=install_agent_pointer,
            external_tools=external_tools,
            memory_backends=memory_backends,
            install_actions=install_actions,
            run_actions=run_actions,
            intent=recorded_intent,
            detection=detection,
            warnings=warnings,
        )
    except ValidationError as exc:
        raise PlanValidationError(str(exc)) from exc

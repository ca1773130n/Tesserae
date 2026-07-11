"""Environment detection: pure probes, never raises."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field


LLM_CLI_NAMES: tuple[str, ...] = (
    "claude",
    "codex",
    "gemini",
    "aider",
    "cursor",
    "gh",
)

LlmProvider = Literal["claude", "codex", "anthropic", "custom"]

API_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
)

STANDARD_CONFIG_DIRS: tuple[tuple[str, str], ...] = (
    ("claude", "~/.claude"),
    ("codex", "~/.codex"),
    ("gemini", "~/.gemini"),
)


class LlmCli(BaseModel):
    name: str
    binary: Optional[str] = None
    version: Optional[str] = None
    available: bool = False
    # True/False when a login probe exists for this CLI; None = unknown.
    credentialed: Optional[bool] = None


class ConfigDir(BaseModel):
    name: str
    path: str
    exists: bool = False


class ProjectFingerprint(BaseModel):
    project_root: Path
    has_git: bool = False
    has_tesserae: bool = False
    has_codegraph: bool = False
    has_pyproject: bool = False
    has_package_json: bool = False
    default_sources: list[str] = Field(default_factory=list)


class PythonEnv(BaseModel):
    executable: str
    version: str
    version_info: tuple[int, int, int]
    in_venv: bool
    venv_path: Optional[str] = None
    tesserae_importable: bool = False
    raganything_importable: bool = False


class Recommendations(BaseModel):
    extractor: Literal[
        "deterministic", "claude-cli", "codex", "selective-claude"
    ] = "deterministic"
    llm_provider: Optional[LlmProvider] = None
    claude_config_dir: Optional[str] = None
    claude_model: Optional[str] = None
    codex_model: Optional[str] = None
    raganything_available: bool = True
    warnings: list[str] = Field(default_factory=list)


class DetectionReport(BaseModel):
    llm_clis: dict[str, LlmCli]
    api_keys: dict[str, bool]
    config_dirs: list[ConfigDir]
    project: ProjectFingerprint
    python: PythonEnv
    recommended: Recommendations
    detected_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))


def _probe_credentials(name: str) -> Optional[bool]:
    """Login probe for CLIs we know how to check; ``None`` = no probe exists.

    Imported lazily so plain detection stays cheap and never pays the
    llm_json import unless a probe-able CLI binary is actually on PATH.
    """
    try:
        if name == "claude":
            from ..llm_json import _claude_cli_available

            return _claude_cli_available()
        if name == "codex":
            from ..llm_json import _codex_cli_available

            return _codex_cli_available()
    except Exception:
        return None
    return None


def _probe_cli(name: str) -> LlmCli:
    binary = shutil.which(name)
    if not binary:
        return LlmCli(name=name, available=False)
    version: Optional[str] = None
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        if completed.returncode == 0:
            text = (completed.stdout or completed.stderr or "").strip()
            version = text.splitlines()[0] if text else None
    except Exception:
        version = None
    credentialed = _probe_credentials(name)
    # A binary that is on PATH but logged out must not be reported available:
    # recommending it yields silent no-LLM fallbacks downstream.
    return LlmCli(
        name=name,
        binary=binary,
        version=version,
        available=credentialed is not False,
        credentialed=credentialed,
    )


def _probe_python(project_root: Path) -> PythonEnv:
    import importlib.util

    in_venv = bool(os.environ.get("VIRTUAL_ENV")) or (
        hasattr(sys, "real_prefix") or (sys.base_prefix != sys.prefix)
    )
    return PythonEnv(
        executable=sys.executable,
        version=sys.version.split()[0],
        version_info=(
            int(sys.version_info[0]),
            int(sys.version_info[1]),
            int(sys.version_info[2]),
        ),
        in_venv=in_venv,
        venv_path=os.environ.get("VIRTUAL_ENV"),
        tesserae_importable=importlib.util.find_spec("tesserae") is not None,
        raganything_importable=importlib.util.find_spec("raganything") is not None,
    )


def _discover_default_sources(root: Path) -> list[str]:
    candidates = ("README.md", "docs", "src", "lib", "app", "packages", "data")
    return [c for c in candidates if (root / c).exists()]


def _probe_project(root: Path) -> ProjectFingerprint:
    return ProjectFingerprint(
        project_root=root.resolve(),
        has_git=(root / ".git").exists(),
        has_tesserae=(root / ".tesserae" / "config.json").exists(),
        has_codegraph=(root / ".codegraph").exists(),
        has_pyproject=(root / "pyproject.toml").exists(),
        has_package_json=(root / "package.json").exists(),
        default_sources=_discover_default_sources(root),
    )


def _probe_config_dirs() -> list[ConfigDir]:
    out: list[ConfigDir] = []
    for name, path_str in STANDARD_CONFIG_DIRS:
        path = Path(path_str).expanduser()
        out.append(ConfigDir(name=name, path=str(path), exists=path.exists()))
    return out


def _recommend(
    *,
    llm_clis: dict[str, LlmCli],
    api_keys: dict[str, bool],
    config_dirs: list[ConfigDir],
    project: ProjectFingerprint,
    python: PythonEnv,
) -> Recommendations:
    warnings: list[str] = []
    raganything_available = True
    if python.version_info < (3, 10):
        warnings.append(
            f"RAG-Anything requires Python 3.10+; current interpreter is "
            f"{python.version_info[0]}.{python.version_info[1]}."
        )
        raganything_available = False

    if llm_clis.get("claude", LlmCli(name="claude")).available:
        extractor: str = "claude-cli"
    elif llm_clis.get("codex", LlmCli(name="codex")).available:
        extractor = "codex"
    elif api_keys.get("ANTHROPIC_API_KEY"):
        extractor = "selective-claude"
    else:
        extractor = "deterministic"

    # Runtime LLM provider (synthesis/insights JSON client). `available` is
    # credential-gated above, so a logged-out CLI is never recommended.
    llm_provider: Optional[str] = None
    if llm_clis.get("claude", LlmCli(name="claude")).available:
        llm_provider = "claude"
    elif llm_clis.get("codex", LlmCli(name="codex")).available:
        llm_provider = "codex"
    elif api_keys.get("ANTHROPIC_API_KEY"):
        llm_provider = "anthropic"

    claude_config_dir: Optional[str] = None
    env_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if env_dir:
        claude_config_dir = env_dir
    else:
        for cd in config_dirs:
            if cd.name == "claude" and cd.exists:
                claude_config_dir = cd.path
                break

    codex_model = "gpt-5.4" if extractor == "codex" else None

    return Recommendations(
        extractor=extractor,  # type: ignore[arg-type]
        llm_provider=llm_provider,  # type: ignore[arg-type]
        claude_config_dir=claude_config_dir,
        codex_model=codex_model,
        raganything_available=raganything_available,
        warnings=warnings,
    )


def detect(project_root: Path | str) -> DetectionReport:
    """Run a single best-effort detection pass. Never raises."""
    root = Path(project_root).resolve()
    llm_clis = {name: _probe_cli(name) for name in LLM_CLI_NAMES}
    api_keys = {key: bool(os.environ.get(key)) for key in API_KEYS}
    config_dirs = _probe_config_dirs()
    project = _probe_project(root)
    python = _probe_python(root)
    recommended = _recommend(
        llm_clis=llm_clis,
        api_keys=api_keys,
        config_dirs=config_dirs,
        project=project,
        python=python,
    )
    return DetectionReport(
        llm_clis=llm_clis,
        api_keys=api_keys,
        config_dirs=config_dirs,
        project=project,
        python=python,
        recommended=recommended,
    )

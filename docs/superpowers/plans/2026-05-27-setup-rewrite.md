# Setup Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace today's barebones `tesserae setup` with a four-layer system: environment detection → plan model → rich-based interactive wizard → apply executor, wired to both the CLI and two new MCP tools so `/tesserae:setup` actually works.

**Architecture:** New `tesserae/setup/` package with `detection.py`, `plan.py`, `wizard.py`, `apply.py`. Old `tesserae/project_setup.py` becomes a thin re-export shim. Two new MCP tools (`tesserae_setup_plan`, `tesserae_setup_apply`) expose a stateless dry-run/apply contract. Slash command flips from terminal-only stub to model-invokable.

**Tech Stack:** Python 3.10+, pydantic v2 (already a dep), rich >=13 (NEW dep), stdlib only otherwise. Tests use pytest + monkeypatch.

**Spec:** `docs/superpowers/specs/2026-05-27-setup-rewrite-design.md`

---

## File structure

**Create:**
- `tesserae/setup/__init__.py` — re-exports public API
- `tesserae/setup/detection.py` — `detect()`, `DetectionReport`, helper models
- `tesserae/setup/plan.py` — `SetupPlan`, `InstallAction`, `RunAction`, `build_plan()`
- `tesserae/setup/wizard.py` — `run_wizard()`, `WizardNotInteractive`, multi-select helper
- `tesserae/setup/apply.py` — `apply_plan()`, `SetupResult`, drift detection
- `tests/test_setup_detection.py`
- `tests/test_setup_plan.py`
- `tests/test_setup_apply.py`
- `tests/test_setup_mcp.py`

**Modify:**
- `pyproject.toml` — add `rich>=13` to base deps
- `tesserae/project_setup.py` — shrink to re-export shim (~30 lines)
- `tesserae/mcp_server.py` — add two tool definitions + dispatch branches
- `tesserae/cli.py` — replace `interactive_setup_plan` call with `run_wizard`, add non-TTY guard
- `commands/setup.md` — flip to model-invokable

---

## Task 1: Add rich dependency

**Files:**
- Modify: `pyproject.toml` (line 23-31, `dependencies` block)

- [ ] **Step 1: Edit pyproject.toml**

Add `"rich>=13"` to the `dependencies` list:

```toml
dependencies = [
  "pydantic>=2",
  "networkx>=3.0",
  "rich>=13",
]
```

- [ ] **Step 2: Sync the venv**

Run: `uv sync --active`
Expected: rich + dependencies (markdown-it-py, mdurl, pygments) installed; no resolver errors.

- [ ] **Step 3: Verify import**

Run: `/Users/neo/Developer/Projects/Tesserae/.venv/bin/python -c "import rich; print(rich.__version__)"`
Expected: prints a version >= 13.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "feat(setup): add rich>=13 base dependency for wizard UX

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

**Done when:** `import rich` works in the project venv and the dep is committed.

---

## Task 2: Implement detection layer

**Files:**
- Create: `tesserae/setup/__init__.py`
- Create: `tesserae/setup/detection.py`
- Create: `tests/test_setup_detection.py`

- [ ] **Step 1: Create empty package**

Write `tesserae/setup/__init__.py`:

```python
"""Setup pipeline: detection → plan → wizard → apply."""

from .detection import DetectionReport, detect
from .plan import (
    InstallAction,
    PlanValidationError,
    RunAction,
    SetupPlan,
    build_plan,
)

__all__ = [
    "DetectionReport",
    "InstallAction",
    "PlanValidationError",
    "RunAction",
    "SetupPlan",
    "build_plan",
    "detect",
]
```

(The plan-layer imports will be valid once Task 3 is in. Leave them for now; the failing import surfaces only when something tries to import `tesserae.setup` between Task 2 and Task 3 — we control all such callers and they come in Task 6+.)

- [ ] **Step 2: Write failing test for `detect()`**

Write `tests/test_setup_detection.py`:

```python
"""Tests for tesserae.setup.detection."""

from __future__ import annotations

import os
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

    def fake_which(name: str) -> str | None:
        return "/fake/bin/claude" if name == "claude" else None

    monkeypatch.setattr(shutil, "which", fake_which)
    report = detect(tmp_path)
    assert report.llm_clis["claude"].available is True
    assert report.llm_clis["claude"].binary == "/fake/bin/claude"
    assert report.llm_clis["codex"].available is False


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

    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/fake/bin/claude" if name == "claude" else None,
    )
    report = detect(tmp_path)
    assert report.recommended.extractor == "claude-cli"


def test_detect_recommends_deterministic_with_no_llm(tmp_path: Path, monkeypatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    report = detect(tmp_path)
    assert report.recommended.extractor == "deterministic"


def test_detect_picks_up_understand_anything_artifact(tmp_path: Path) -> None:
    (tmp_path / ".understand-anything").mkdir()
    (tmp_path / ".understand-anything" / "knowledge-graph.json").write_text("{}")
    report = detect(tmp_path)
    assert report.project.has_understand_anything is True
    assert report.recommended.include_understand_anything is True
```

- [ ] **Step 3: Run failing test**

Run: `cd /Users/neo/Developer/Projects/Tesserae && .venv/bin/pytest tests/test_setup_detection.py -x 2>&1 | tail -20`
Expected: `ModuleNotFoundError: No module named 'tesserae.setup.detection'` (or import error for `detect`).

- [ ] **Step 4: Implement `detection.py`**

Write `tesserae/setup/detection.py`:

```python
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
    "gh",   # we'll check `gh copilot` indirectly
)

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


class ConfigDir(BaseModel):
    name: str
    path: str
    exists: bool = False


class ProjectFingerprint(BaseModel):
    project_root: Path
    has_git: bool = False
    has_tesserae: bool = False
    has_understand_anything: bool = False
    has_codegraph: bool = False
    has_cognee: bool = False
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
    claude_config_dir: Optional[str] = None
    claude_model: Optional[str] = None
    codex_model: Optional[str] = None
    include_understand_anything: bool = False
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
            version = (completed.stdout or completed.stderr or "").strip().splitlines()[0] or None
    except Exception:
        version = None
    return LlmCli(name=name, binary=binary, version=version, available=True)


def _probe_python(project_root: Path) -> PythonEnv:
    import importlib.util

    in_venv = bool(os.environ.get("VIRTUAL_ENV")) or (
        hasattr(sys, "real_prefix") or (sys.base_prefix != sys.prefix)
    )
    return PythonEnv(
        executable=sys.executable,
        version=sys.version.split()[0],
        version_info=(sys.version_info.major, sys.version_info.minor, sys.version_info.micro),
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
        has_understand_anything=(root / ".understand-anything" / "knowledge-graph.json").exists(),
        has_codegraph=(root / ".codegraph").exists(),
        has_cognee=(root / ".cognee").exists(),
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
        claude_config_dir=claude_config_dir,
        codex_model=codex_model,
        include_understand_anything=project.has_understand_anything,
        raganything_available=raganything_available,
        warnings=warnings,
    )


def detect(project_root: Path | str) -> DetectionReport:
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
```

Note: the `__init__.py` imports `plan` symbols which don't exist yet — comment them out for now and restore in Task 3, OR move the plan import block to Task 3. Use this minimal `__init__.py` instead for now:

```python
"""Setup pipeline: detection → plan → wizard → apply."""

from .detection import DetectionReport, detect

__all__ = ["DetectionReport", "detect"]
```

(Will be expanded in Task 3.)

- [ ] **Step 5: Run tests**

Run: `cd /Users/neo/Developer/Projects/Tesserae && .venv/bin/pytest tests/test_setup_detection.py -x 2>&1 | tail -20`
Expected: all 7 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tesserae/setup/__init__.py tesserae/setup/detection.py tests/test_setup_detection.py
git commit -m "feat(setup): detection layer for LLM CLIs, API keys, project, python env

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

**Done when:** `tests/test_setup_detection.py` passes and `from tesserae.setup import detect` works.

---

## Task 3: Implement plan layer

**Files:**
- Create: `tesserae/setup/plan.py`
- Modify: `tesserae/setup/__init__.py` (expand exports)
- Create: `tests/test_setup_plan.py`

- [ ] **Step 1: Write failing tests**

Write `tests/test_setup_plan.py`:

```python
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

    monkeypatch.setattr(
        shutil, "which",
        lambda name: "/fake/bin/claude" if name == "claude" else None,
    )
    report = detect(tmp_path)
    plan = build_plan(report)
    assert plan.extractor == "claude-cli"
    assert plan.project_root == tmp_path.resolve()


def test_build_plan_applies_overrides(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(
        report,
        overrides={"name": "my-wiki", "extractor": "deterministic"},
    )
    assert plan.name == "my-wiki"
    assert plan.extractor == "deterministic"


def test_build_plan_emits_install_action_for_understand_anything(tmp_path: Path) -> None:
    (tmp_path / ".understand-anything").mkdir()
    (tmp_path / ".understand-anything" / "knowledge-graph.json").write_text("{}")
    report = detect(tmp_path)
    plan = build_plan(report, overrides={"include_understand_anything": True})
    ids = {a.id for a in plan.install_actions}
    assert "understand-anything" in ids or any(
        t.get("id") == "understand-anything" for t in plan.external_tools
    )


def test_build_plan_skips_raganything_install_on_old_python(tmp_path: Path, monkeypatch) -> None:
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
```

- [ ] **Step 2: Run failing test**

Run: `cd /Users/neo/Developer/Projects/Tesserae && .venv/bin/pytest tests/test_setup_plan.py -x 2>&1 | tail -20`
Expected: `ImportError` for `build_plan` / `SetupPlan` / `PlanValidationError`.

- [ ] **Step 3: Implement `plan.py`**

Write `tesserae/setup/plan.py`:

```python
"""SetupPlan model + build_plan(detection, overrides)."""

from __future__ import annotations

import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

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
        return (
            "{python} -m pip install "
            f"'raganything[{extras}]>=1.3.0' docling"
        )
    return "{python} -m pip install 'raganything>=1.3.0' docling"


def build_plan(
    detection: DetectionReport,
    *,
    overrides: Optional[dict[str, Any]] = None,
) -> SetupPlan:
    """Build a SetupPlan from a DetectionReport with optional field overrides."""

    overrides = dict(overrides or {})
    root = Path(overrides.pop("project_root", detection.project.project_root))

    # Companion-tool flags carried separately (not direct SetupPlan fields):
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
    codex_model = overrides.pop(
        "codex_model", detection.recommended.codex_model
    )

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
        cognee["install"]["auto_install"] = install_cognee
        memory_backends["cognee"] = cognee
        if install_cognee:
            install_actions.append(
                InstallAction(
                    id="cognee",
                    description="Install Cognee memory backend",
                    command=str(cognee["install"]["command"]),
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
```

- [ ] **Step 4: Expand `__init__.py`**

Overwrite `tesserae/setup/__init__.py`:

```python
"""Setup pipeline: detection → plan → wizard → apply."""

from .detection import DetectionReport, detect
from .plan import (
    InstallAction,
    PlanValidationError,
    RunAction,
    SetupPlan,
    build_plan,
)

__all__ = [
    "DetectionReport",
    "InstallAction",
    "PlanValidationError",
    "RunAction",
    "SetupPlan",
    "build_plan",
    "detect",
]
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/neo/Developer/Projects/Tesserae && .venv/bin/pytest tests/test_setup_plan.py -x 2>&1 | tail -20`
Expected: all 6 tests PASS. Detection tests still pass.

- [ ] **Step 6: Commit**

```bash
git add tesserae/setup/plan.py tesserae/setup/__init__.py tests/test_setup_plan.py
git commit -m "feat(setup): SetupPlan model + build_plan from detection

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

**Done when:** plan tests pass and `from tesserae.setup import build_plan, SetupPlan` works.

---

## Task 4: Implement apply layer

**Files:**
- Create: `tesserae/setup/apply.py`
- Modify: `tesserae/setup/__init__.py` (add apply exports)
- Create: `tests/test_setup_apply.py`

- [ ] **Step 1: Write failing tests**

Write `tests/test_setup_apply.py`:

```python
"""Tests for tesserae.setup.apply."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tesserae.setup import SetupPlan, build_plan, detect
from tesserae.setup.apply import SetupResult, apply_plan


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
    from tesserae.setup.plan import InstallAction

    plan.install_actions = [
        InstallAction(id="fake", description="install fake", command="echo fake")
    ]

    called = {"count": 0}
    original_run = subprocess.run

    def fake_run(*args, **kwargs):
        called["count"] += 1
        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = apply_plan(plan, confirm_install_actions=False)
    assert called["count"] == 0
    assert any(a.get("status") == "skipped" for a in result.actions_taken)


def test_apply_runs_install_actions_when_confirmed(tmp_path: Path) -> None:
    report = detect(tmp_path)
    plan = build_plan(report)
    from tesserae.setup.plan import InstallAction

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
    from tesserae.setup.apply import DriftError

    report = detect(tmp_path)
    plan = build_plan(report)
    plan.detection.python.version = "0.0.0"
    with pytest.raises(DriftError):
        apply_plan(plan, drift_policy="abort")
```

- [ ] **Step 2: Run failing test**

Run: `cd /Users/neo/Developer/Projects/Tesserae && .venv/bin/pytest tests/test_setup_apply.py -x 2>&1 | tail -20`
Expected: ImportError for `apply_plan`, `SetupResult`, `DriftError`.

- [ ] **Step 3: Implement `apply.py`**

Write `tesserae/setup/apply.py`:

```python
"""Apply a SetupPlan: write config, run gated install/run actions."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from ..project import ProjectWiki
from .detection import DetectionReport, detect
from .plan import InstallAction, RunAction, SetupPlan


class DriftError(RuntimeError):
    """Raised when apply_plan(drift_policy='abort') detects environment drift."""


class SetupResult(BaseModel):
    config_path: Path
    actions_taken: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    drift: dict[str, Any] = Field(default_factory=dict)
    wiki_root: Path


def _expand_command(command: str, project_root: Path) -> str:
    return command.format(
        python=shlex.quote(sys.executable),
        project=shlex.quote(str(project_root)),
    )


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


def _detect_drift(plan_detection: DetectionReport, current: DetectionReport) -> dict[str, Any]:
    drift: dict[str, Any] = {}
    if plan_detection.python.version != current.python.version:
        drift["python_version"] = (plan_detection.python.version, current.python.version)
    for name, plan_cli in plan_detection.llm_clis.items():
        current_cli = current.llm_clis.get(name)
        if current_cli and plan_cli.available != current_cli.available:
            drift[f"llm_{name}.available"] = (plan_cli.available, current_cli.available)
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
            "wizard": "tesserae project setup",
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

    # Materialize understand-anything projection if configured.
    from ..project_setup import materialize_understand_anything_source

    for tool in plan.external_tools:
        if tool.get("id") == "understand-anything":
            materialize_understand_anything_source(project_root, tool)

    return SetupResult(
        config_path=wiki.paths.config,
        actions_taken=actions_taken,
        warnings=warnings,
        drift=drift,
        wiki_root=wiki.root,
    )
```

- [ ] **Step 4: Update `__init__.py`**

Append to `tesserae/setup/__init__.py`:

```python
from .apply import DriftError, SetupResult, apply_plan

__all__ += ["DriftError", "SetupResult", "apply_plan"]
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/neo/Developer/Projects/Tesserae && .venv/bin/pytest tests/test_setup_apply.py tests/test_setup_plan.py tests/test_setup_detection.py -x 2>&1 | tail -20`
Expected: all tests PASS (5 + 6 + 7 = 18).

- [ ] **Step 6: Commit**

```bash
git add tesserae/setup/apply.py tesserae/setup/__init__.py tests/test_setup_apply.py
git commit -m "feat(setup): apply_plan with drift detection and gated actions

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

**Done when:** all setup tests pass; `apply_plan` writes a valid `.tesserae/config.json` in a tmp project.

---

## Task 5: Implement wizard layer

**Files:**
- Create: `tesserae/setup/wizard.py`
- Modify: `tesserae/setup/__init__.py` (add wizard exports)
- Create: `tests/test_setup_wizard.py`

- [ ] **Step 1: Write failing smoke test**

Write `tests/test_setup_wizard.py`:

```python
"""Smoke tests for tesserae.setup.wizard."""

from __future__ import annotations

from pathlib import Path

import pytest

from tesserae.setup import detect
from tesserae.setup.wizard import WizardNotInteractive, render_review, run_wizard


def test_run_wizard_raises_when_no_tty(tmp_path: Path) -> None:
    report = detect(tmp_path)
    with pytest.raises(WizardNotInteractive):
        run_wizard(report)


def test_render_review_is_plain_text(tmp_path: Path) -> None:
    from tesserae.setup import build_plan

    report = detect(tmp_path)
    plan = build_plan(report)
    text = render_review(plan)
    assert plan.name in text
    assert plan.extractor in text
```

- [ ] **Step 2: Run failing test**

Run: `cd /Users/neo/Developer/Projects/Tesserae && .venv/bin/pytest tests/test_setup_wizard.py -x 2>&1 | tail -10`
Expected: ImportError.

- [ ] **Step 3: Implement `wizard.py`**

Write `tesserae/setup/wizard.py`:

```python
"""Rich-based interactive setup wizard."""

from __future__ import annotations

import sys
from io import StringIO
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from .detection import DetectionReport
from .plan import SetupPlan, build_plan


EXTRACTOR_CHOICES = ["claude-cli", "codex", "selective-claude", "deterministic"]


class WizardNotInteractive(RuntimeError):
    """Raised when run_wizard is invoked without a TTY."""


def _detection_panel(report: DetectionReport) -> Panel:
    lines: list[str] = []
    lines.append(
        f"[bold]Project[/bold]  {report.project.project_root}"
    )
    py = report.python
    venv_marker = "[green]✓[/green]" if py.in_venv else "[dim]·[/dim]"
    tess_marker = "[green]✓[/green]" if py.tesserae_importable else "[dim]·[/dim]"
    lines.append(
        f"[bold]Python[/bold]   {py.version}   .venv {venv_marker}   tesserae {tess_marker}"
    )
    lines.append("")
    lines.append("[bold]LLM CLIs detected[/bold]")
    for name, cli in report.llm_clis.items():
        if cli.available:
            version = cli.version or "(no version)"
            lines.append(
                f"  [green]✓[/green] {name:<8} {version:<20} {cli.binary or ''}"
            )
        else:
            lines.append(f"  [dim]·  {name:<8} not found[/dim]")
    lines.append("")
    lines.append("[bold]API keys[/bold]")
    for key, present in report.api_keys.items():
        mark = "[green]✓[/green]" if present else "[dim]·[/dim]"
        lines.append(f"  {mark} {key}")
    lines.append("")
    lines.append("[bold]Companions[/bold]")
    proj = report.project
    for label, present in (
        ("understand-anything", proj.has_understand_anything),
        ("codegraph", proj.has_codegraph),
        ("cognee", proj.has_cognee),
    ):
        mark = "[green]✓[/green]" if present else "[dim]·[/dim]"
        lines.append(f"  {mark} {label}")
    return Panel("\n".join(lines), title="Tesserae setup", border_style="cyan")


def _multi_select(
    console: Console,
    title: str,
    items: list[tuple[str, bool]],
) -> list[str]:
    """Numbered toggle multi-select (Windows-safe; no raw termios)."""
    selections: dict[int, bool] = {i: pre for i, (_, pre) in enumerate(items)}
    while True:
        console.print(f"\n[bold]{title}[/bold]")
        for i, (label, _) in enumerate(items):
            marker = "[x]" if selections[i] else "[ ]"
            console.print(f"  {marker} {i + 1}) {label}")
        raw = Prompt.ask(
            "Toggle by number (comma-separated), or press Enter to accept",
            default="",
        )
        if not raw.strip():
            return [items[i][0] for i, on in selections.items() if on]
        for tok in raw.split(","):
            tok = tok.strip()
            if not tok.isdigit():
                continue
            idx = int(tok) - 1
            if 0 <= idx < len(items):
                selections[idx] = not selections[idx]


def render_review(plan: SetupPlan) -> str:
    """Render a plain-text review of a plan for MCP / non-TTY consumers."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=88)
    table = Table(title="Tesserae setup review", show_lines=False)
    table.add_column("Action")
    table.add_column("Detail")
    table.add_row("write config", str(plan.project_root / ".tesserae" / "config.json"))
    table.add_row("name", plan.name)
    table.add_row("extractor", plan.extractor)
    if plan.claude_config_dir:
        table.add_row("claude_config_dir", plan.claude_config_dir)
    if plan.codex_model:
        table.add_row("codex_model", plan.codex_model)
    table.add_row("sources", ", ".join(plan.sources) or "(none)")
    for action in plan.install_actions:
        table.add_row(f"install: {action.id}", action.command)
    for action in plan.run_actions:
        table.add_row(f"run: {action.id}", action.command)
    for warning in plan.warnings:
        table.add_row("[yellow]warning[/yellow]", warning)
    console.print(table)
    return buf.getvalue()


def run_wizard(
    detection: DetectionReport,
    defaults: Optional[SetupPlan] = None,
    *,
    console: Optional[Console] = None,
) -> SetupPlan:
    if not sys.stdin.isatty():
        raise WizardNotInteractive(
            "run_wizard requires an interactive TTY. Use --yes or call build_plan directly."
        )

    console = console or Console()
    console.print(_detection_panel(detection))

    # Step 1: project basics
    base_plan = defaults or build_plan(detection)
    name = Prompt.ask("Wiki name", default=base_plan.name)
    source_kind = Prompt.ask("Source kind", default=base_plan.source_kind)
    candidates = list(detection.project.default_sources)
    items = [(src, src in base_plan.sources) for src in candidates]
    sources = _multi_select(console, "Sources", items) if items else []
    extra = Prompt.ask(
        "Additional source paths (comma-separated, blank to skip)", default=""
    )
    sources.extend(p.strip() for p in extra.split(",") if p.strip())

    # Step 2: extractor
    console.print("\n[bold]Extractor backend[/bold]")
    for i, choice in enumerate(EXTRACTOR_CHOICES):
        marker = "[cyan]*[/cyan]" if choice == base_plan.extractor else " "
        console.print(f"  {marker} {i + 1}) {choice}")
    extractor_raw = Prompt.ask(
        "Pick a backend (number, or press Enter to keep recommended)",
        default=str(EXTRACTOR_CHOICES.index(base_plan.extractor) + 1),
    )
    try:
        extractor = EXTRACTOR_CHOICES[int(extractor_raw) - 1]
    except (ValueError, IndexError):
        extractor = base_plan.extractor

    claude_config_dir = base_plan.claude_config_dir
    codex_model = base_plan.codex_model
    if extractor in {"claude-cli", "selective-claude"}:
        claude_config_dir = Prompt.ask(
            "CLAUDE_CONFIG_DIR (leave default unless multi-account)",
            default=claude_config_dir or "~/.claude",
        )
    if extractor == "codex":
        codex_model = Prompt.ask("Codex model", default=codex_model or "gpt-5.4")

    # Step 3: companion tools
    companion_items = [
        ("understand-anything", detection.recommended.include_understand_anything),
        ("raganything", False),
        ("cognee", True),
    ]
    chosen = _multi_select(console, "Companion tools", companion_items)
    include_ua = "understand-anything" in chosen
    include_raganything = "raganything" in chosen
    enable_cognee = "cognee" in chosen

    install_ua = False
    if include_ua and not detection.project.has_understand_anything:
        install_ua = Confirm.ask("Install Understand Anything now?", default=True)
    install_raganything = False
    if include_raganything and not detection.python.raganything_importable:
        if detection.recommended.raganything_available:
            install_raganything = Confirm.ask("Install raganything now?", default=False)
        else:
            console.print(
                "[yellow]raganything requires Python 3.10+; skipping install option.[/yellow]"
            )

    plan = build_plan(
        detection,
        overrides={
            "name": name,
            "source_kind": source_kind,
            "sources": sources,
            "extractor": extractor,
            "claude_config_dir": claude_config_dir,
            "codex_model": codex_model,
            "include_understand_anything": include_ua,
            "install_understand_anything": install_ua,
            "include_raganything": include_raganything,
            "install_raganything": install_raganything,
            "enable_cognee": enable_cognee,
        },
    )

    # Step 4: review + confirm
    console.print()
    console.print(render_review(plan), end="")
    if not Confirm.ask("Apply this plan?", default=True):
        raise KeyboardInterrupt("setup cancelled by user at review step")
    return plan
```

- [ ] **Step 4: Update `__init__.py`**

Append to `tesserae/setup/__init__.py`:

```python
from .wizard import WizardNotInteractive, render_review, run_wizard

__all__ += ["WizardNotInteractive", "render_review", "run_wizard"]
```

- [ ] **Step 5: Run tests**

Run: `cd /Users/neo/Developer/Projects/Tesserae && .venv/bin/pytest tests/test_setup_wizard.py tests/test_setup_apply.py tests/test_setup_plan.py tests/test_setup_detection.py -x 2>&1 | tail -10`
Expected: all 20 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tesserae/setup/wizard.py tesserae/setup/__init__.py tests/test_setup_wizard.py
git commit -m "feat(setup): rich-based interactive wizard with non-TTY guard

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

**Done when:** wizard test passes; `render_review` returns plain text including the plan's name and extractor.

---

## Task 6: Wire MCP tools

**Files:**
- Modify: `tesserae/mcp_server.py` (tool list around line 390+, dispatcher around line 1186+)
- Create: `tests/test_setup_mcp.py`

- [ ] **Step 1: Write failing integration test**

Write `tests/test_setup_mcp.py`:

```python
"""Integration test: MCP plan → apply roundtrip writes valid config."""

from __future__ import annotations

import json
from pathlib import Path


def test_mcp_setup_plan_then_apply_roundtrip(tmp_path: Path) -> None:
    from tesserae.mcp_server import ResearchGraphMCP

    server = ResearchGraphMCP(graph_path=None, project_root=tmp_path)

    plan_response = server.invoke_tool(
        "tesserae_setup_plan",
        {"project_root": str(tmp_path)},
    )
    assert "plan" in plan_response
    assert "rendered_summary" in plan_response
    plan_payload = json.loads(plan_response["plan"])
    plan_payload["name"] = "smoke-wiki"

    apply_response = server.invoke_tool(
        "tesserae_setup_apply",
        {"plan": plan_payload, "drift_policy": "ignore"},
    )
    config_path = Path(apply_response["config_path"])
    assert config_path.exists()
    cfg = json.loads(config_path.read_text())
    assert cfg["project"]["name"] == "smoke-wiki"
```

(Note: `ResearchGraphMCP` may use a different class name and constructor — verify by `grep -n "class.*MCP\|invoke_tool\|def _invoke" tesserae/mcp_server.py` before implementing. Adjust the test to match the actual server class and dispatch method. If the dispatch is internal — e.g. `server._handle_tool_call(name, args)` — use that.)

- [ ] **Step 2: Find the MCP server class + dispatch**

Run: `grep -n "class .*\(MCP\|Server\)\b\|def _handle\|def call_tool\|def invoke" /Users/neo/Developer/Projects/Tesserae/tesserae/mcp_server.py | head -10`

Use the actual class/method name in the test from Step 1.

- [ ] **Step 3: Run failing test**

Run: `cd /Users/neo/Developer/Projects/Tesserae && .venv/bin/pytest tests/test_setup_mcp.py -x 2>&1 | tail -15`
Expected: error referencing `tesserae_setup_plan` not found.

- [ ] **Step 4: Add tool definitions**

Locate the tool list block in `tesserae/mcp_server.py` (around line 390). Append two new tool definitions before the closing `]`:

```python
{
    "name": "tesserae_setup_plan",
    "description": (
        "Detect the environment and propose a Tesserae setup plan as JSON. "
        "Read-only: does not touch .tesserae/. Returns {plan, rendered_summary}. "
        "Pass `overrides` to pre-set any SetupPlan field."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "project_root": {
                "type": "string",
                "description": "Project root path; defaults to '.'.",
            },
            "overrides": {
                "type": "object",
                "description": "Optional field overrides applied to build_plan().",
            },
        },
    },
},
{
    "name": "tesserae_setup_apply",
    "description": (
        "Apply a SetupPlan (from tesserae_setup_plan, possibly mutated): "
        "writes .tesserae/config.json and runs gated install/run actions. "
        "Pass confirm_install_actions=True to install dependencies, "
        "confirm_run_actions=True to execute refresh commands."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["plan"],
        "properties": {
            "plan": {
                "type": "object",
                "description": "SetupPlan JSON returned by tesserae_setup_plan.",
            },
            "confirm_install_actions": {"type": "boolean", "default": False},
            "confirm_run_actions": {"type": "boolean", "default": False},
            "drift_policy": {
                "type": "string",
                "enum": ["warn", "abort", "ignore"],
                "default": "warn",
            },
        },
    },
},
```

- [ ] **Step 5: Add dispatcher branches**

Locate the `if name == ...` ladder in the tool-dispatch method (around line 1186+). Add two new branches:

```python
if name == "tesserae_setup_plan":
    from .setup import build_plan, detect
    from .setup.wizard import render_review

    project_root = args.get("project_root") or "."
    overrides = args.get("overrides") or None
    report = detect(project_root)
    plan = build_plan(report, overrides=overrides)
    return {
        "plan": plan.model_dump_json(),
        "rendered_summary": render_review(plan),
    }

if name == "tesserae_setup_apply":
    from .setup import SetupPlan, apply_plan

    plan_payload = args.get("plan")
    if not isinstance(plan_payload, dict):
        raise ValueError("tesserae_setup_apply requires 'plan' as an object")
    plan = SetupPlan.model_validate(plan_payload)
    result = apply_plan(
        plan,
        confirm_install_actions=bool(args.get("confirm_install_actions", False)),
        confirm_run_actions=bool(args.get("confirm_run_actions", False)),
        drift_policy=args.get("drift_policy", "warn"),
    )
    return {
        "config_path": str(result.config_path),
        "actions_taken": result.actions_taken,
        "warnings": result.warnings,
        "drift": result.drift,
        "wiki_root": str(result.wiki_root),
    }
```

- [ ] **Step 6: Run integration test**

Run: `cd /Users/neo/Developer/Projects/Tesserae && .venv/bin/pytest tests/test_setup_mcp.py -x 2>&1 | tail -15`
Expected: PASS. If the harness doesn't expose `invoke_tool` directly, call the actual dispatch method discovered in Step 2.

- [ ] **Step 7: Commit**

```bash
git add tesserae/mcp_server.py tests/test_setup_mcp.py
git commit -m "feat(setup): MCP tools tesserae_setup_plan + tesserae_setup_apply

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

**Done when:** the integration test passes; running the MCP server lists both tools.

---

## Task 7: Update slash command + CLI

**Files:**
- Modify: `commands/setup.md`
- Modify: `tesserae/cli.py` (handler around line 1219+)

- [ ] **Step 1: Rewrite the slash command**

Overwrite `commands/setup.md`:

```markdown
---
description: Run Tesserae setup — detect environment, propose a plan, apply with confirmation.
argument-hint: ""
allowed-tools:
  - "mcp__tesserae__tesserae_setup_plan"
  - "mcp__tesserae__tesserae_setup_apply"
---

Run `tesserae_setup_plan` for the current working directory. Show the
`rendered_summary` to the user verbatim. Then ask:

1. Should I install the flagged dependencies? (sets `confirm_install_actions`)
2. Should I run the post-setup refresh commands? (sets `confirm_run_actions`)

Call `tesserae_setup_apply` with the appropriate flags. Report
`actions_taken` and `warnings`. If `drift` is non-empty, surface it before
applying.
```

- [ ] **Step 2: Update the CLI setup handler**

In `tesserae/cli.py`, find the `if args.command == "setup":` block (around line 1219). Replace its body with:

```python
if args.command == "setup":
    from .setup import (
        WizardNotInteractive,
        apply_plan,
        build_plan,
        detect,
        render_review,
        run_wizard,
    )

    try:
        report = detect(args.project)
        if args.yes:
            overrides = {
                "name": args.name,
                "source_kind": args.source_kind,
                "sources": args.source or None,
                "include_understand_anything": args.with_understand_anything,
                "understand_anything_platform": args.understand_anything_platform,
                "install_understand_anything": (
                    False if args.skip_install_understand_anything
                    else True if args.install_understand_anything
                    else None
                ),
                "include_raganything": (
                    False if args.skip_raganything else args.with_raganything
                ),
                "install_raganything": (
                    False if args.skip_install_raganything
                    else True if args.install_raganything
                    else None
                ),
                "raganything_extras": args.raganything_extras,
                "raganything_parser": args.raganything_parser,
                "enable_cognee": not args.no_cognee,
                "cognee_mode": args.cognee_mode,
                "cognee_auto_cognify": args.run_cognee,
                "install_cognee": (
                    False if args.skip_install_cognee
                    else True if args.install_cognee
                    else None
                ),
            }
            overrides = {k: v for k, v in overrides.items() if v is not None}
            plan = build_plan(report, overrides=overrides)
            print(render_review(plan), end="")
        else:
            try:
                plan = run_wizard(report)
            except WizardNotInteractive:
                print(
                    "tesserae setup: stdin is not a TTY. "
                    "Re-run from a real terminal, or pass --yes to use detected defaults.",
                    file=sys.stderr,
                )
                return 2
        result = apply_plan(
            plan,
            confirm_install_actions=True,
            confirm_run_actions=True,
        )
    except KeyboardInterrupt:
        print("Setup cancelled.")
        return 130
    except Exception as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 2
    print(f"Initialized project wiki: {result.wiki_root}")
    print(f"Config: {result.config_path}")
    if result.actions_taken:
        for row in result.actions_taken:
            status = row.get("status") or "?"
            print(f"  [{status}] {row.get('id')}: {row.get('command') or row.get('description')}")
    if result.warnings:
        for w in result.warnings:
            print(f"warning: {w}", file=sys.stderr)
    print("Next: tesserae project compile && tesserae project build-site")
    return 0
```

- [ ] **Step 3: Smoke test the CLI in --yes mode**

Run from any test project root:

```bash
cd /tmp && mkdir tesserae-smoke && cd tesserae-smoke
/Users/neo/Developer/Projects/Tesserae/.venv/bin/python -m tesserae.cli setup --yes --no-cognee --skip-raganything --project .
```

Expected: a review table printed, then `Initialized project wiki: ...`. `.tesserae/config.json` exists in `/tmp/tesserae-smoke`.

Clean up: `rm -rf /tmp/tesserae-smoke`.

- [ ] **Step 4: Commit**

```bash
git add commands/setup.md tesserae/cli.py
git commit -m "feat(setup): wire new wizard into CLI, flip slash command to model-invokable

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

**Done when:** `tesserae setup --yes` works end-to-end; slash command file references the two new MCP tools.

---

## Task 8: Migrate project_setup.py to shim

**Files:**
- Modify: `tesserae/project_setup.py` (currently 541 lines → ~40 lines of re-exports)

- [ ] **Step 1: Identify external callers**

Run: `cd /Users/neo/Developer/Projects/Tesserae && grep -rn "from tesserae.project_setup\|from .project_setup" --include='*.py' | head -20`

Expected callers: `tesserae/cli.py`, `tesserae/mcp_server.py`, `tests/test_*.py`. Note which symbols each caller imports.

- [ ] **Step 2: Rewrite `project_setup.py` as a shim**

Replace the entire contents of `tesserae/project_setup.py` with:

```python
"""Backward-compat shim. New code should import from `tesserae.setup`."""

from __future__ import annotations

# Original module split into the tesserae.setup/ package on 2026-05-27.
# These re-exports keep older imports working. Prefer tesserae.setup in new code.

from .setup import (  # noqa: F401
    DetectionReport,
    InstallAction,
    PlanValidationError,
    RunAction,
    SetupPlan,
    SetupResult,
    apply_plan,
    build_plan,
    detect,
    render_review,
    run_wizard,
)

# Compatibility aliases for the old function names.
build_setup_plan = build_plan
apply_setup_plan = apply_plan
interactive_setup_plan = run_wizard

# `render_setup_summary` had a different signature in the old API; the new
# `render_review` is a drop-in for the common case of "show me what would
# happen". Keep the old name pointing at it.
render_setup_summary = render_review


# Legacy helpers retained because external code (refresh, compile) still
# reaches into them. They're not part of the new public API but they keep
# behavior bit-for-bit identical.
def materialize_understand_anything_source(project_root, tool):
    """Materialize the understand-anything markdown projection from JSON artifact."""
    # Re-implement here so the shim doesn't import a soon-to-be-removed module.
    import json
    from pathlib import Path

    root = Path(project_root).resolve()
    artifact = root / str(tool.get("artifact") or ".understand-anything/knowledge-graph.json")
    source = root / str(
        tool.get("source") or ".tesserae/external/understand-anything.md"
    )
    source.parent.mkdir(parents=True, exist_ok=True)
    if not artifact.exists():
        source.write_text(
            "# Understand Anything Knowledge Graph\n\n"
            f"Expected artifact: `{artifact.relative_to(root) if artifact.is_relative_to(root) else artifact}`\n\n"
            "The artifact does not exist yet. Run the configured refresh command, then compile again.\n",
            encoding="utf-8",
        )
        return str(source.relative_to(root) if source.is_relative_to(root) else source)
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except Exception as exc:
        source.write_text(
            f"# Understand Anything Knowledge Graph\n\nCould not parse JSON: `{exc}`\n",
            encoding="utf-8",
        )
        return str(source.relative_to(root) if source.is_relative_to(root) else source)
    nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
    edges = payload.get("edges", []) if isinstance(payload, dict) else []
    project = payload.get("project", {}) if isinstance(payload, dict) else {}
    lines = [
        "# Understand Anything Knowledge Graph",
        "",
        f"- Project: {project.get('name') or root.name}",
        f"- Nodes: {len(nodes) if isinstance(nodes, list) else 0}",
        f"- Edges: {len(edges) if isinstance(edges, list) else 0}",
        "",
    ]
    source.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(source.relative_to(root) if source.is_relative_to(root) else source)


def refresh_configured_external_tools(project_root, *, only_auto=True, fail_fast=False):
    """Refresh external tools from a project's .tesserae config."""
    from .project import ProjectWiki

    wiki = ProjectWiki.load(project_root)
    cfg = wiki.config()
    tools = cfg.get("external_tools", [])
    results = []
    import subprocess
    import shlex
    import sys as _sys
    from pathlib import Path

    root = Path(wiki.project_root).resolve()
    for tool in tools:
        if not tool.get("enabled", True):
            continue
        if only_auto and not tool.get("auto_refresh"):
            continue
        command = str(tool.get("refresh_command") or "").strip()
        if not command:
            results.append({"id": tool.get("id"), "status": "skipped"})
            continue
        command = command.format(
            python=shlex.quote(_sys.executable),
            project=shlex.quote(str(root)),
        )
        completed = subprocess.run(
            command, shell=True, cwd=root, text=True, capture_output=True
        )
        results.append(
            {
                "id": tool.get("id"),
                "status": "passed" if completed.returncode == 0 else "failed",
                "command": command,
                "returncode": completed.returncode,
                "stdout": (completed.stdout or "")[-2000:],
                "stderr": (completed.stderr or "")[-2000:],
            }
        )
        if completed.returncode != 0 and fail_fast:
            raise RuntimeError(
                f"External tool failed: {tool.get('name')} ({completed.returncode})"
            )
    return results
```

- [ ] **Step 3: Verify imports**

Run: `cd /Users/neo/Developer/Projects/Tesserae && .venv/bin/python -c "
from tesserae.project_setup import (
    apply_setup_plan, build_setup_plan, interactive_setup_plan,
    render_setup_summary, materialize_understand_anything_source,
    refresh_configured_external_tools,
)
print('shim ok')
"`

Expected: prints `shim ok`.

- [ ] **Step 4: Run the full test suite**

Run: `cd /Users/neo/Developer/Projects/Tesserae && .venv/bin/pytest tests/ -x -q 2>&1 | tail -20`
Expected: all tests pass (the existing tests that imported from `project_setup` should now go through the shim transparently).

If any test fails because a removed legacy symbol was used, add a targeted re-export for that symbol. Do NOT add behavior — only re-exports.

- [ ] **Step 5: Commit**

```bash
git add tesserae/project_setup.py
git commit -m "refactor(setup): shrink project_setup.py to backward-compat shim

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

**Done when:** all tests pass; `project_setup.py` is < 200 lines; existing callers untouched.

---

## Task 9: Manual smoke test + PR

**Files:** none (verification only)

- [ ] **Step 1: Smoke test the CLI wizard interactively**

Open a real terminal in a fresh project:

```bash
mkdir -p /tmp/tesserae-smoke-wizard && cd /tmp/tesserae-smoke-wizard
git init -q
echo '# test' > README.md
/Users/neo/Developer/Projects/Tesserae/.venv/bin/python -m tesserae.cli setup --project .
```

Walk through all 5 steps. Confirm:
- Detection banner shows correct project / Python / LLM CLIs / API keys
- Pre-filled defaults reflect what's actually on the system
- Multi-select toggles work
- Extractor picker respects the recommended option
- Review table accurately reflects choices
- `Apply` writes `.tesserae/config.json`

Clean up: `rm -rf /tmp/tesserae-smoke-wizard`.

- [ ] **Step 2: Smoke test the MCP path**

From the Tesserae project root, in a new terminal:

```bash
cd /Users/neo/Developer/Projects/Tesserae
.venv/bin/python -c "
from tesserae.mcp_server import ResearchGraphMCP  # adjust if class name differs
import tempfile, json
from pathlib import Path

with tempfile.TemporaryDirectory() as td:
    server = ResearchGraphMCP(graph_path=None, project_root=Path(td))
    resp = server.invoke_tool('tesserae_setup_plan', {'project_root': td})
    print(resp['rendered_summary'])
    plan = json.loads(resp['plan'])
    plan['name'] = 'mcp-smoke'
    apply_resp = server.invoke_tool(
        'tesserae_setup_apply',
        {'plan': plan, 'drift_policy': 'ignore'},
    )
    cfg = json.loads(Path(apply_resp['config_path']).read_text())
    assert cfg['project']['name'] == 'mcp-smoke', cfg
    print('mcp roundtrip ok')
"
```

(Adjust `ResearchGraphMCP` / `invoke_tool` to the actual class/method names from Task 6 step 2.)

Expected: prints the rendered summary, then `mcp roundtrip ok`.

- [ ] **Step 3: Smoke test --yes mode**

```bash
mkdir -p /tmp/tesserae-smoke-yes && cd /tmp/tesserae-smoke-yes
/Users/neo/Developer/Projects/Tesserae/.venv/bin/python -m tesserae.cli setup --yes --no-cognee --skip-raganything --project .
cat .tesserae/config.json | head -20
```

Expected: review table printed, config written, file contains the new `extraction` block.

Clean up: `rm -rf /tmp/tesserae-smoke-yes`.

- [ ] **Step 4: Push + open PR**

```bash
cd /Users/neo/Developer/Projects/Tesserae
git push -u origin main 2>&1 | tail -3
```

(Or open a feature branch + PR if the workflow demands it. The user's preferred flow is committing to `main` for solo work — confirm before pushing.)

**Done when:** all three smoke tests pass; commits pushed (or PR opened, per user preference).

---

## Self-review checklist

After implementing all tasks:

- [ ] `tesserae setup` from a real terminal launches the rich wizard
- [ ] `tesserae setup --yes` works in CI / non-TTY
- [ ] `/tesserae:setup` slash command calls `tesserae_setup_plan` → user confirms → `tesserae_setup_apply`
- [ ] `.tesserae/config.json` written by all three paths contains the new `extraction` block with the chosen backend
- [ ] All existing tests pass
- [ ] No new dep beyond `rich`
- [ ] `tesserae/project_setup.py` is a thin shim; old imports still work

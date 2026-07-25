"""Interactive setup helpers for project-local Tesserae workspaces."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .project import ProjectWiki, default_raganything_backend_config, sanitize_server_name


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
RED = "\033[31m"


@dataclass
class SetupPlan:
    project_root: Path
    name: str
    source_kind: str = "Repository"
    sources: List[str] = field(default_factory=list)
    external_tools: List[dict] = field(default_factory=list)
    run_external_tools: bool = False
    install_external_tools: bool = False
    memory_backends: dict = field(default_factory=dict)


@dataclass
class SetupResult:
    wiki: ProjectWiki
    config_path: Path
    ran_tools: List[dict] = field(default_factory=list)


def _rel(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def discover_default_sources(project_root: str | Path) -> List[str]:
    root = Path(project_root).resolve()
    candidates = ["README.md", "docs", "src", "lib", "app", "packages", "data"]
    return [item for item in candidates if (root / item).exists()]


def expand_tool_command(command: str, project_root: str | Path, tool: Optional[dict] = None) -> str:
    root = Path(project_root).resolve()
    tool = tool or {}
    install = tool.get("install") or {}
    values = {
        "python": shlex.quote(sys.executable),
        "project": shlex.quote(str(root)),
        "platform": shlex.quote(str(install.get("platform") or tool.get("platform") or "codex")),
    }
    try:
        return command.format(**values)
    except Exception:
        return command


def build_setup_plan(
    project_root: str | Path,
    *,
    name: Optional[str] = None,
    source_kind: str = "Repository",
    sources: Optional[Iterable[str | Path]] = None,
    # Removed backend (0.19): legacy cognee kwargs are swallowed with a
    # removal warning (never an error) so old callers keep working.
    enable_cognee: bool = False,
    cognee_mode: str = "cognify",
    cognee_auto_cognify: bool = False,
    install_cognee: Optional[bool] = None,
    include_raganything: bool = False,
    install_raganything: bool | None = None,
    raganything_parser: str = "mineru",
    raganything_extras: str = "all",
    run_raganything: bool = False,
    raganything_llm_provider: str = "codex",
    raganything_llm_model: Optional[str] = None,
    raganything_claude_config_dir: Optional[str] = None,
    raganything_embedding_provider: str = "deterministic",
    raganything_embedding_dim: int = 768,
) -> SetupPlan:
    root = Path(project_root).resolve()
    source_list = [str(source) for source in sources] if sources is not None else discover_default_sources(root)
    external_tools: List[dict] = []

    memory_backends: dict = {}
    if enable_cognee or cognee_auto_cognify or install_cognee:
        print(
            "note: cognee backend was removed in 0.19 — request ignored",
            file=sys.stderr,
        )

    if include_raganything:
        if install_raganything is None:
            try:
                import raganything as _probe  # noqa: F401
                _raganything_installed = True
            except Exception:
                _raganything_installed = False
            should_install_raganything = not _raganything_installed
        else:
            should_install_raganything = bool(install_raganything)

        import sys as _sys
        _python_too_old = _sys.version_info < (3, 10)
        if _python_too_old and should_install_raganything:
            should_install_raganything = False
            _python_warning = (
                f"RAG-Anything requires Python 3.10+; current interpreter is "
                f"{_sys.version_info.major}.{_sys.version_info.minor}. Skipping install. "
                f"Use a Python 3.10+ environment to enable RAG-Anything."
            )
        elif _python_too_old:
            _python_warning = (
                f"RAG-Anything requires Python 3.10+; current interpreter is "
                f"{_sys.version_info.major}.{_sys.version_info.minor}. "
                f"Use a Python 3.10+ environment to enable RAG-Anything."
            )
        else:
            _python_warning = None

        backend = default_raganything_backend_config(name or sanitize_server_name(root.name))
        backend["enabled"] = True
        backend["parser"] = raganything_parser
        backend["llm"] = {
            "provider": raganything_llm_provider,
            "model": raganything_llm_model
            or ("gpt-5.6-luna" if raganything_llm_provider == "codex" else None),
            "timeout": 300,
            "claude_config_dir": raganything_claude_config_dir,
        }
        backend["embedding"] = {
            "provider": raganything_embedding_provider,
            "dim": int(raganything_embedding_dim),
        }
        install_command = (
            "{python} -m pip install 'raganything[" + raganything_extras + "]>=1.3.0' docling"
            if raganything_extras
            else "{python} -m pip install 'raganything>=1.3.0' docling"
        )
        if should_install_raganything:
            backend["install"]["auto_install"] = True
            backend["install"]["command"] = install_command
        if _python_too_old:
            backend["enabled"] = False
            backend["python_warning"] = _python_warning
        memory_backends["raganything"] = backend

        refresh_command = (
            "{python} -m tesserae.raganything_refresh "
            "--project {project} "
            f"--parser {shlex.quote(raganything_parser)}"
        )
        external_tools.append(
            {
                "id": "raganything",
                "name": "RAG-Anything",
                "artifact": ".tesserae/external/raganything/manifest.json",
                "source": ".tesserae/external/raganything/manifest.json",
                "refresh_command": refresh_command,
                "auto_refresh": bool(run_raganything),
                "sync_mode": "native_graph",
                "parser": raganything_parser,
                "extras": raganything_extras,
                "managed_refresh": True,
                "llm": dict(backend["llm"]),
                "embedding": dict(backend["embedding"]),
                "install": {
                    "enabled": True,
                    "auto_install": bool(should_install_raganything),
                    "command": install_command,
                },
                "python_warning": _python_warning,
                "enabled": not _python_too_old,
            }
        )

    return SetupPlan(
        project_root=root,
        name=name or sanitize_server_name(root.name),
        source_kind=source_kind,
        sources=source_list,
        external_tools=external_tools,
        run_external_tools=False,
        install_external_tools=any((tool.get("install") or {}).get("auto_install") for tool in external_tools),
        memory_backends=memory_backends,
    )


def _paint(text: str, color: str, enabled: bool) -> str:
    return f"{color}{text}{RESET}" if enabled else text


def render_setup_summary(plan: SetupPlan, *, color: bool = True) -> str:
    lines = [
        _paint("Tesserae setup", BOLD + CYAN, color),
        f"Project: {_paint(str(plan.project_root), DIM, color)}",
        f"Name:    {_paint(plan.name, GREEN, color)}",
        f"Kind:    {_paint(plan.source_kind, MAGENTA, color)}",
        "",
        _paint("Sources", BOLD, color),
    ]
    if plan.sources:
        lines.extend(f"  {_paint('✓', GREEN, color)} {source}" for source in plan.sources)
    else:
        lines.append(f"  {_paint('!', YELLOW, color)} no sources selected yet")
    lines.append("")
    lines.append(_paint("External tools", BOLD, color))
    if plan.external_tools:
        for tool in plan.external_tools:
            command = tool.get("refresh_command") or "configure later"
            source = tool.get("source") or tool.get("artifact")
            install = tool.get("install") or {}
            install_note = ", installs now" if install.get("auto_install") else ""
            lines.append(f"  {_paint('◆', CYAN, color)} {tool['name']} → {source} ({command}{install_note})")
            warning = tool.get("python_warning")
            if warning:
                lines.append(f"  {_paint('⚠ ' + tool['name'] + ': ' + warning, YELLOW + BOLD, color)}")
    else:
        lines.append(f"  {_paint('·', DIM, color)} none selected")
    lines.append("")
    lines.append(_paint("Memory backends", BOLD, color))
    backends = plan.memory_backends or {}
    if not backends:
        lines.append(f"  {_paint('·', DIM, color)} none")
    for backend_id, backend in backends.items():
        if backend_id == "cognee":
            # Removed backend (0.19): a stale plan entry is ignored.
            continue
        if not isinstance(backend, dict):
            continue
        if backend_id == "raganything":
            display_name = "RAG-Anything"
            parser = backend.get("parser") or "mineru"
            query_mode = backend.get("query_mode") or "hybrid"
            enabled = "enabled" if backend.get("enabled", True) else "disabled"
            lines.append(
                f"  {_paint('◆', CYAN, color)} {display_name} → {parser} ({query_mode}, runtime backend {enabled})"
            )
        else:
            display_name = backend.get("name") or backend_id
            enabled = "enabled" if backend.get("enabled", True) else "disabled"
            lines.append(f"  {_paint('◆', CYAN, color)} {display_name} ({enabled})")
    return "\n".join(lines) + "\n"


# The legacy in-module interactive wizard (interactive_setup_plan and its
# _ask_yes_no/_ask_list prompt helpers) was deleted: `tesserae init` runs the
# rich wizard in tesserae/setup/wizard.py instead. refresh_configured_external_tools
# below is the surviving runtime surface of this module.


def run_external_tools(plan: SetupPlan, *, fail_fast: bool = True) -> List[dict]:
    results: List[dict] = []
    if plan.install_external_tools:
        results.extend(run_tool_configs(plan.project_root, plan.external_tools, only_auto=False, fail_fast=fail_fast, run_installers=True))
    if not plan.run_external_tools:
        return results
    results.extend(run_tool_configs(plan.project_root, plan.external_tools, only_auto=False, fail_fast=fail_fast))
    return results


def run_tool_configs(project_root: str | Path, tools: Sequence[dict], *, only_auto: bool = True, fail_fast: bool = True, run_installers: bool = False) -> List[dict]:
    root = Path(project_root).resolve()
    results: List[dict] = []
    ua_noted = False
    for tool in tools:
        if tool.get("id") == "understand-anything":
            # Removed backend: OLD configs may still carry the entry. Ignore it
            # with ONE stderr note (not an error) so those configs keep loading.
            if not ua_noted:
                print(
                    "note: understand-anything external tool was removed — entry ignored"
                    " (code-structure nodes are extracted natively; see tesserae code ingest)",
                    file=sys.stderr,
                )
                ua_noted = True
            results.append({"id": "understand-anything", "status": "skipped", "reason": "backend removed"})
            continue
        if not tool.get("enabled", True):
            continue
        if only_auto and not tool.get("auto_refresh"):
            continue
        if run_installers:
            install = tool.get("install") or {}
            command = str(install.get("command") or "").strip()
            if not install.get("enabled", False) or not install.get("auto_install", False) or not command:
                continue
            command = expand_tool_command(command, root, tool)
            completed = subprocess.run(
                command,
                shell=True,
                cwd=root,
                text=True,
                capture_output=True,
            )
            result = {
                "id": tool.get("id"),
                "status": "installed" if completed.returncode == 0 else "install_failed",
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-2000:],
                "stderr": completed.stderr[-2000:],
            }
            results.append(result)
            if completed.returncode != 0 and fail_fast:
                raise RuntimeError(f"External tool install failed: {tool.get('name')} ({completed.returncode})")
            continue
        command = str(tool.get("refresh_command") or "").strip()
        if command:
            command = expand_tool_command(command, root, tool)
            completed = subprocess.run(
                command,
                shell=True,
                cwd=root,
                text=True,
                capture_output=True,
            )
            result = {
                "id": tool.get("id"),
                "status": "passed" if completed.returncode == 0 else "failed",
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-2000:],
                "stderr": completed.stderr[-2000:],
            }
            results.append(result)
            if completed.returncode != 0 and fail_fast:
                raise RuntimeError(f"External tool failed: {tool.get('name')} ({completed.returncode})")
        else:
            results.append({"id": tool.get("id"), "status": "skipped", "reason": "no refresh_command"})
    return results


def refresh_configured_external_tools(project_root: str | Path, *, only_auto: bool = True, fail_fast: bool = False) -> List[dict]:
    wiki = ProjectWiki.load(project_root)
    cfg = wiki.config()
    return run_tool_configs(wiki.project_root, cfg.get("external_tools", []), only_auto=only_auto, fail_fast=fail_fast)


def apply_setup_plan(plan: SetupPlan) -> SetupResult:
    ran_tools = run_external_tools(plan, fail_fast=False)
    wiki = ProjectWiki.init(plan.project_root, name=plan.name, source_kind=plan.source_kind, sources=plan.sources)
    cfg = wiki.config()
    cfg["setup"] = {
        "wizard": "tesserae init",
        "updated": date.today().isoformat(),
    }
    cfg["external_tools"] = plan.external_tools
    # No resurrection: a plan without memory backends writes an empty section.
    memory_backends = dict(plan.memory_backends or {})
    if memory_backends.pop("cognee", None) is not None:
        # Removed backend (0.19): a stale plan entry is dropped with a note.
        print(
            "note: cognee backend was removed in 0.19 — plan entry ignored",
            file=sys.stderr,
        )
        ran_tools.append({"id": "cognee", "status": "skipped", "reason": "backend removed"})
    cfg["memory_backends"] = memory_backends
    wiki.paths.config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return SetupResult(wiki=wiki, config_path=wiki.paths.config, ran_tools=ran_tools)

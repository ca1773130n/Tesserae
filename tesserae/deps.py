"""Optional external dependencies Tesserae can use, with detect + install.

One registry so ``tesserae setup --global``, ``tesserae deps``, and ``tesserae
init`` all offer the SAME installs instead of every project wiring them by hand.
These are machine-global installs (pip into the active env, cargo into ~/.cargo);
enabling a backend for a given project still lives in that project's config.

ponytail: a small registry of (detect, install-argv) — no plugin framework.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

# Install can be slow (a Rust build, a large pip resolve) — generous ceiling.
_INSTALL_TIMEOUT_S = 1800.0


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _binary_present(name: str) -> bool:
    return shutil.which(name) is not None


def _ua_installed() -> bool:
    """Understand-Anything's installer drops a plugin/skills tree (no PATH binary),
    so detect a real completion marker — its cloned ``repo/install.sh`` — rather
    than a `ua` executable or a bare leftover dir a failed install could leave."""
    if _binary_present("understand-anything") or _binary_present("ua"):
        return True
    return (Path.home() / ".understand-anything" / "repo" / "install.sh").is_file()


def _pip_install_argv(specs: List[str]) -> List[str]:
    """argv to install ``specs`` into the interpreter Tesserae is running under.

    uv tool installs (the common way to get the `tesserae` CLI) ship a venv
    WITHOUT pip, so ``{python} -m pip install`` dies with 'No module named pip'.
    Fall back to ``uv pip install --python <interp>`` — uv created the env so it's
    on PATH, and ``--python`` targets that same environment.
    """
    if _module_present("pip"):
        return [sys.executable, "-m", "pip", "install", *specs]
    if _binary_present("uv"):
        return ["uv", "pip", "install", "--python", sys.executable, *specs]
    return [sys.executable, "-m", "pip", "install", *specs]  # no pip, no uv -> fail with a clear error


@dataclass(frozen=True)
class Dep:
    name: str
    summary: str
    detect: Callable[[], bool]
    install_cmd: List[str]
    needs_shell: bool = False  # install_cmd[0] is "sh -c <string>"-style
    note: str = ""
    pip_specs: Optional[List[str]] = None  # set for pip deps -> argv resolved at install time


# The argv install commands mirror tesserae/setup/plan.py where they overlap.
DEPS: List[Dep] = [
    Dep(
        "memex",
        "Fast local transcript search (BM25/embeddings) for the sessions dashboard",
        lambda: _binary_present("memex"),
        ["cargo", "install", "--git", "https://github.com/nicosuave/memex", "--locked"],
        note="requires the Rust toolchain (cargo)",
    ),
    Dep(
        "cognee",
        "Cognee knowledge-graph backend",
        lambda: _module_present("cognee"),
        [sys.executable, "-m", "pip", "install", "cognee"],
        pip_specs=["cognee"],
    ),
    Dep(
        "raganything",
        "RAG-Anything multimodal retrieval backend",
        lambda: _module_present("raganything"),
        [sys.executable, "-m", "pip", "install", "raganything[all]>=1.3.0", "docling"],
        pip_specs=["raganything[all]>=1.3.0", "docling"],
    ),
    Dep(
        "understand-anything",
        "Understand-Anything code knowledge-graph skill",
        _ua_installed,
        ["bash", "-c",
         "curl -fsSL https://raw.githubusercontent.com/Lum1104/Understand-Anything/main/install.sh | bash -s codex"],
        needs_shell=True,
        note="runs the upstream install script (needs network)",
    ),
]

DEPS_BY_NAME = {d.name: d for d in DEPS}
DEP_NAMES = [d.name for d in DEPS]


def _safe_detect(dep: Dep) -> bool:
    """``dep.detect()`` that can never raise — a probe failure means 'absent'."""
    try:
        return bool(dep.detect())
    except Exception:  # noqa: BLE001 — detection is best-effort; never propagate
        return False


def status() -> List[dict]:
    """``[{name, summary, installed, note}]`` for every known dependency."""
    return [
        {"name": d.name, "summary": d.summary, "installed": _safe_detect(d), "note": d.note}
        for d in DEPS
    ]


def install(name: str, *, timeout: float = _INSTALL_TIMEOUT_S) -> dict:
    """Install one dependency. Always returns a dict; never raises.

    ``{name, ok, already?, error?, cmd}``. A no-op (already present) is ``ok``.
    """
    dep = DEPS_BY_NAME.get(name)
    if dep is None:
        return {"name": name, "ok": False, "error": f"unknown dependency (known: {', '.join(DEP_NAMES)})"}
    if _safe_detect(dep):
        return {"name": name, "ok": True, "already": True, "cmd": ""}
    # pip deps resolve their argv at install time (pip vs. uv-pip fallback).
    argv = _pip_install_argv(dep.pip_specs) if dep.pip_specs else dep.install_cmd
    cmd_display = " ".join(argv)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"name": name, "ok": False, "error": "install timed out", "cmd": cmd_display}
    except OSError as exc:
        # e.g. cargo / pip not on PATH.
        return {"name": name, "ok": False, "error": f"could not run installer: {exc}", "cmd": cmd_display}
    ok = proc.returncode == 0 and _safe_detect(dep)
    err = None if ok else (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[-500:]
    return {"name": name, "ok": ok, "error": err, "cmd": cmd_display}

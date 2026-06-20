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


@dataclass(frozen=True)
class Dep:
    name: str
    summary: str
    detect: Callable[[], bool]
    install_cmd: List[str]
    needs_shell: bool = False  # install_cmd[0] is "sh -c <string>"-style
    note: str = ""


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
    ),
    Dep(
        "raganything",
        "RAG-Anything multimodal retrieval backend",
        lambda: _module_present("raganything"),
        [sys.executable, "-m", "pip", "install", "raganything[all]>=1.3.0", "docling"],
    ),
    Dep(
        "understand-anything",
        "Understand-Anything code knowledge-graph skill",
        lambda: _binary_present("understand-anything") or _binary_present("ua"),
        ["bash", "-c",
         "curl -fsSL https://raw.githubusercontent.com/Lum1104/Understand-Anything/main/install.sh | bash -s codex"],
        needs_shell=True,
        note="runs the upstream install script (needs network)",
    ),
]

DEPS_BY_NAME = {d.name: d for d in DEPS}
DEP_NAMES = [d.name for d in DEPS]


def status() -> List[dict]:
    """``[{name, summary, installed, note}]`` for every known dependency."""
    return [
        {"name": d.name, "summary": d.summary, "installed": d.detect(), "note": d.note}
        for d in DEPS
    ]


def install(name: str, *, timeout: float = _INSTALL_TIMEOUT_S) -> dict:
    """Install one dependency. Always returns a dict; never raises.

    ``{name, ok, already?, error?, cmd}``. A no-op (already present) is ``ok``.
    """
    dep = DEPS_BY_NAME.get(name)
    if dep is None:
        return {"name": name, "ok": False, "error": f"unknown dependency (known: {', '.join(DEP_NAMES)})"}
    if dep.detect():
        return {"name": name, "ok": True, "already": True, "cmd": ""}
    cmd_display = " ".join(dep.install_cmd)
    try:
        proc = subprocess.run(dep.install_cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"name": name, "ok": False, "error": "install timed out", "cmd": cmd_display}
    except OSError as exc:
        # e.g. cargo / pip not on PATH.
        return {"name": name, "ok": False, "error": f"could not run installer: {exc}", "cmd": cmd_display}
    ok = proc.returncode == 0 and dep.detect()
    err = None if ok else (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()[-500:]
    return {"name": name, "ok": ok, "error": err, "cmd": cmd_display}

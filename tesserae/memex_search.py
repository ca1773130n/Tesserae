"""Fast local transcript search via nicosuave/memex (optional external tool).

memex (https://github.com/nicosuave/memex, MIT) is a Rust CLI that BM25/embedding
indexes Claude/Codex conversation transcripts. The sessions dashboard's search box
shells out to it. It is OPTIONAL: if the binary (or its index) is absent we degrade
with a clear, actionable message instead of erroring.

ponytail: shell out to the installed tool — don't vendor a Rust index or a second
embedding stack into a Python project.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Optional

_TIMEOUT_S = 20.0
_INSTALL_HINT = "cargo install --git https://github.com/nicosuave/memex"


def memex_path() -> Optional[str]:
    """Absolute path to the ``memex`` binary, or ``None`` if not installed."""
    return shutil.which("memex")


def search_transcripts(
    query: str,
    *,
    limit: int = 20,
    project: Optional[str] = None,
    source: Optional[str] = None,
    hybrid: bool = False,
) -> dict:
    """Search indexed transcripts. Always returns a dict; never raises.

    Shape: ``{"available": bool, "results": [...], "total": int, "error"?: str}``.
    """
    binary = memex_path()
    if binary is None:
        return {"available": False, "results": [], "total": 0,
                "error": f"memex is not installed. Install it with: {_INSTALL_HINT}"}

    query = (query or "").strip()
    if not query:
        return {"available": True, "results": [], "total": 0}

    cmd = [binary, "search", query, "--json-array", "--limit", str(max(1, min(int(limit), 100)))]
    if project:
        cmd += ["--project", project]
    if source in ("claude", "codex", "opencode"):
        cmd += ["--source", source]
    if hybrid:
        cmd.append("--hybrid")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"available": True, "results": [], "total": 0, "error": "memex search timed out"}
    except OSError as exc:
        return {"available": True, "results": [], "total": 0, "error": f"memex could not run: {exc}"}

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        hint = " — run `memex index` to build the search index" if "index" in err.lower() else ""
        return {"available": True, "results": [], "total": 0, "error": f"memex: {err[:300]}{hint}"}

    try:
        data = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return {"available": True, "results": [], "total": 0, "error": "memex returned unparseable output"}

    results = data if isinstance(data, list) else []
    return {"available": True, "results": results, "total": len(results)}

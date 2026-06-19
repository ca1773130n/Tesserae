"""Robust pidfile identity — defeats the stale-pidfile + PID-reuse false positive.

A bare PID is NOT proof a daemon is alive. When an engine dies without cleaning
its pidfile (SIGKILL, crash, power loss), the file lingers with a dead PID. The
OS recycles PIDs, so days later that same number may belong to an unrelated
process — and a plain ``os.kill(pid, 0)`` liveness probe then *succeeds*, making
the engine refuse to start with "already running (pid N)" when in fact no engine
is running. (Observed: ``~/.tesserae/engine.pid`` held a 6-day-old dead PID that
had been reassigned.)

The fix: pair the PID with a **stable per-incarnation identity** — the process
start time — and treat the lock as held only when BOTH the PID is alive AND its
current start time matches the one recorded in the pidfile. A recycled PID has a
different start time, so it is correctly judged stale and reclaimed.

Identity sources, in order (no third-party deps):
- Linux: ``/proc/<pid>/stat`` field 22 (``starttime``, clock ticks since boot).
- macOS / BSD: ``ps -o lstart=`` (absolute start timestamp).

Conservatism rule: if the start time cannot be read (permission, unsupported
platform, transient error) we fall back to treating a live PID as live — better
to occasionally refuse a start than to ever run two engines against one project.
The pidfile also keeps the legacy bare-integer format readable for back-compat.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

# How long to wait on the `ps` fallback before giving up (identity = unknown).
_PS_TIMEOUT_S = 5.0


def process_start_time(pid: int) -> Optional[str]:
    """Return a stable per-incarnation start-time token for ``pid``.

    ``None`` when it cannot be determined (dead pid, no permission, unsupported
    platform). The exact string is opaque — only equality across reads matters,
    and a recycled PID yields a different value.
    """
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            data = proc_stat.read_text()
        except OSError:
            return None
        # ``comm`` (field 2) is wrapped in parens and may itself contain spaces
        # or ')', so split on the LAST ')': everything after is space-delimited
        # with ``state`` at index 0, making ``starttime`` (field 22) index 19.
        try:
            tail = data.rsplit(")", 1)[1].split()
            return tail[19]
        except (IndexError, ValueError):
            return None
    # macOS / BSD: no /proc — ask ps for the absolute start timestamp.
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = result.stdout.strip()
    return line or None


def process_cmdline(pid: int) -> Optional[str]:
    """Best-effort command line for ``pid`` (diagnostic only; never decisive)."""
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if proc_cmdline.exists():
        try:
            raw = proc_cmdline.read_bytes()
        except OSError:
            return None
        text = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
        return text or None
    try:
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = result.stdout.strip()
    return line or None


def current_identity() -> dict:
    """Identity payload for the CURRENT process, to be written into a pidfile."""
    pid = os.getpid()
    return {
        "pid": pid,
        "start_time": process_start_time(pid),
        "cmdline": process_cmdline(pid),
    }


def serialize(identity: Optional[dict] = None) -> str:
    """Render an identity payload as the pidfile's on-disk text (JSON)."""
    return json.dumps(identity if identity is not None else current_identity())


def parse(text: str) -> Optional[dict]:
    """Parse pidfile text into an identity dict, or ``None`` if unusable.

    Accepts the current JSON form AND the legacy bare-integer form (older
    engines wrote just ``str(os.getpid())``); the legacy form carries no
    ``start_time``, so it degrades to a plain liveness check.
    """
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        data = None
    if isinstance(data, dict) and isinstance(data.get("pid"), int):
        return data
    if isinstance(data, int):
        return {"pid": data, "start_time": None, "cmdline": None}
    # Legacy bare-int pidfile.
    try:
        return {"pid": int(raw), "start_time": None, "cmdline": None}
    except ValueError:
        return None


def owner_is_alive(payload: Optional[dict]) -> bool:
    """True only when the process recorded in ``payload`` is genuinely running.

    Returns False (i.e. "stale, safe to reclaim") when the PID is dead OR is
    alive but its start time no longer matches the recorded one (PID reuse).
    Falls back to treating a live PID as alive when the start time is unknown
    (legacy pidfile or unreadable identity) — see the module conservatism rule.
    """
    if not payload or not isinstance(payload.get("pid"), int):
        return False
    pid = payload["pid"]
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False  # no such process — stale
    except PermissionError:
        pass  # alive but owned by another user — fall through to identity check
    except OSError:
        return True  # unexpected; be conservative and assume alive

    recorded = payload.get("start_time")
    if not recorded:
        return True  # no recorded identity (legacy) — can't detect reuse
    live = process_start_time(pid)
    if live is None:
        return True  # can't read identity now — don't risk a double start
    return live == recorded


def read_owner(path: Path) -> Optional[dict]:
    """Read+parse a pidfile at ``path``; ``None`` if missing/unreadable/garbage."""
    try:
        return parse(Path(path).read_text())
    except (OSError, ValueError):
        return None

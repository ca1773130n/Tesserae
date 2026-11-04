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

**The pidfile is a lease.** Several servers share one disk and therefore one
``.tesserae/``, and a pid only means something in the process table of the
machine that wrote it. So the record also carries ``host`` (the persisted
``local_host_id``), ``hostname``, ``boot_id``, the package ``version``, the
record ``schema`` and ``lease_expires`` (epoch seconds), which a long-running
owner renews through :class:`LeaseHeartbeat`. :func:`owner_is_alive` never
probes a pid recorded by another host: it reads that host's lease, and an
absent lease field answers "alive" (conservative, as above). On the writer's
own host a ``boot_id`` that changed means the machine rebooted since, which
kills every pid it recorded. :func:`version_conflicts` is what lets an engine
notice that another host runs a different Tesserae version against the same
project — the stale-code failure mode, one machine removed.

Lease timing is wall-clock across hosts, so it assumes clocks agree to well
inside :data:`DEFAULT_LEASE_TTL_S`; NTP-level skew is fine, a host minutes off
is not.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

# How long to wait on the `ps` fallback before giving up (identity = unknown).
_PS_TIMEOUT_S = 5.0

#: Version of the lease record's own shape; bump when a field changes meaning.
LEASE_SCHEMA = 1
#: How long a lease stays valid without a renewal. The heartbeat renews at a
#: third of it, so two missed beats still leave the lease live.
DEFAULT_LEASE_TTL_S = 120.0
#: Below this, clock skew between hosts is a large fraction of the lease.
_MIN_LEASE_TTL_S = 10.0

_BOOT_ID_UNSET = object()
_boot_id_cache: object = _BOOT_ID_UNSET
_own_start_time_cache: Dict[int, Optional[str]] = {}


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
    # LC_ALL=C pins the rendering: ps localizes lstart, so a daemon started
    # under a Korean-locale shell and a checker under an English one would
    # otherwise disagree about the SAME instant and misread a live owner as
    # PID reuse (doctor would then offer to delete a live daemon's pidfile).
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_S,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = result.stdout.strip()
    return line or None


def process_cmdline(pid: int) -> Optional[str]:
    """Best-effort command line for ``pid``.

    Diagnostic in pidfiles, plus one narrow decisive use: when start-time
    renders disagree (locale-mismatched writer/reader), an exact cmdline match
    rescues ``owner_is_alive`` — see the fallback there."""
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


def own_start_time() -> Optional[str]:
    """:func:`process_start_time` for THIS process, computed once per pid.

    A process's start time never changes, and on macOS each lookup forks
    ``ps`` — too slow for a per-write journal record. Keyed by pid so a forked
    child does not inherit its parent's answer.
    """
    pid = os.getpid()
    if pid not in _own_start_time_cache:
        _own_start_time_cache[pid] = process_start_time(pid)
    return _own_start_time_cache[pid]


def boot_id() -> Optional[str]:
    """An opaque token that changes on every reboot of this machine, or None.

    Linux: ``/proc/sys/kernel/random/boot_id``. macOS / BSD: ``sysctl -n
    kern.boottime``. A pid recorded before a reboot names nothing, whatever
    process holds that number now.
    """
    global _boot_id_cache
    if _boot_id_cache is not _BOOT_ID_UNSET:
        return _boot_id_cache  # type: ignore[return-value]
    value: Optional[str] = None
    linux = Path("/proc/sys/kernel/random/boot_id")
    try:
        if linux.exists():
            value = linux.read_text().strip() or None
        else:
            result = subprocess.run(
                ["sysctl", "-n", "kern.boottime"], capture_output=True, text=True,
                timeout=_PS_TIMEOUT_S, env={**os.environ, "LC_ALL": "C"},
            )
            value = result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("boot id unavailable: %s", exc)
    _boot_id_cache = value
    return value


def local_host() -> str:
    """This machine's persisted id (``local_host_id``), or ``""``."""
    try:
        from ..harness_sessions import local_host_id

        return local_host_id()
    except Exception as exc:  # noqa: BLE001 — identity must not block a start
        logger.warning("no host id available (%s); lease records name no host", exc)
        return ""


def package_version() -> str:
    from ..cli_tree import package_version as _version

    return _version()


def lease_ttl() -> float:
    """``TESSERAE_LEASE_TTL`` seconds (default :data:`DEFAULT_LEASE_TTL_S`)."""
    raw = os.environ.get("TESSERAE_LEASE_TTL", "")
    try:
        ttl = float(raw) if raw else DEFAULT_LEASE_TTL_S
    except ValueError:
        logger.warning("TESSERAE_LEASE_TTL=%r is not a number; using %s", raw, DEFAULT_LEASE_TTL_S)
        ttl = DEFAULT_LEASE_TTL_S
    return max(_MIN_LEASE_TTL_S, ttl)


def current_identity() -> dict:
    """Lease payload for the CURRENT process, to be written into a pidfile."""
    pid = os.getpid()
    now = time.time()
    return {
        "pid": pid,
        "start_time": process_start_time(pid),
        "cmdline": process_cmdline(pid),
        "host": local_host(),
        "hostname": socket.gethostname(),
        "boot_id": boot_id(),
        "version": package_version(),
        "schema": LEASE_SCHEMA,
        "renewed_at": now,
        "lease_expires": now + lease_ttl(),
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
    host = str(payload.get("host") or "")
    if host and host != local_host():
        # Another machine's pid: probing it here would interrogate an
        # unrelated local process. Only its lease can say whether it is live.
        expires = payload.get("lease_expires")
        if isinstance(expires, (int, float)):
            return time.time() < expires
        return True  # no lease recorded: cannot judge, so never call it stale
    recorded_boot = payload.get("boot_id")
    if recorded_boot:
        live_boot = boot_id()
        if live_boot and live_boot != recorded_boot:
            return False  # this machine rebooted since; every recorded pid died
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
    if live == recorded:
        return True
    # Start-time render can differ for the SAME instant when writer and
    # reader ran under different locales (pre-fix pidfiles; ps localizes
    # lstart on macOS). A matching full cmdline rescues the alive verdict —
    # it can only ever widen "alive", never declare a live owner stale.
    recorded_cmd = payload.get("cmdline")
    if recorded_cmd:
        return process_cmdline(pid) == recorded_cmd
    return False


def read_owner(path: Path) -> Optional[dict]:
    """Read+parse a pidfile at ``path``; ``None`` if missing/unreadable/garbage."""
    try:
        return parse(Path(path).read_text())
    except (OSError, ValueError):
        return None


def renew(path: Path) -> bool:
    """Push this process's lease at ``path`` forward by :func:`lease_ttl`.

    Renews only a record that is still ours (same pid, same host): a lease
    another process reclaimed must not be overwritten with ours. The static
    fields are carried over from disk rather than recomputed, so a heartbeat
    never forks ``ps``. Returns False, with a warning, when it did not renew.
    """
    from ..locking import journaled_write

    owner = read_owner(path)
    if not owner or owner.get("pid") != os.getpid() or str(owner.get("host") or "") != local_host():
        logger.warning("lease %s is no longer held by this process; not renewing", path)
        return False
    now = time.time()
    owner.update(renewed_at=now, lease_expires=now + lease_ttl())
    journaled_write(Path(path), json.dumps(owner), durable=False)
    return True


class LeaseHeartbeat:
    """Background renewal of one lease file until :meth:`stop`.

    A daemon thread: it must never be what keeps an exiting engine alive. A
    failed renewal is logged and retried on the next beat — an expired lease
    makes other hosts treat this owner as gone, so it is worth a warning.
    """

    def __init__(self, path: Path, *, interval: Optional[float] = None) -> None:
        self._path = Path(path)
        self._interval = interval if interval is not None else lease_ttl() / 3.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "LeaseHeartbeat":
        self._thread = threading.Thread(
            target=self._run, name=f"tesserae-lease-{self._path.name}", daemon=True
        )
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                if not renew(self._path):
                    return  # not ours any more; renewing again cannot fix that
            except OSError as exc:
                logger.warning("lease %s renewal failed: %s", self._path, exc)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None


def strict_version_guard() -> bool:
    """True when ``TESSERAE_LEASE_STRICT_VERSION`` asks to refuse, not warn."""
    return os.environ.get("TESSERAE_LEASE_STRICT_VERSION", "").strip().lower() in ("1", "true", "yes", "on")


def version_conflicts(paths: Iterable[Path]) -> List[dict]:
    """Live leases among ``paths`` that name a different Tesserae version.

    A record without a ``version`` (written before leases) is not a conflict:
    nothing is known about it.
    """
    mine = package_version()
    out: List[dict] = []
    for path in paths:
        owner = read_owner(path)
        if not owner:
            continue
        theirs = owner.get("version")
        if theirs and theirs != mine and owner_is_alive(owner):
            out.append({**owner, "path": str(path)})
    return out


def describe_conflicts(conflicts: List[dict]) -> str:
    """``"0.41.8 (pid 4711 on srv-b)"``-style list for a log line or error."""
    return ", ".join(
        f"{c.get('version')} (pid {c.get('pid')} on {c.get('hostname') or c.get('host') or '?'})"
        for c in conflicts
    )

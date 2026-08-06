"""Per-project compile lock.

A session-end hook can fire ``tesserae refresh`` while a long compile is
still running for the same project; without a lock the two stack onto the
same ``.tesserae`` state and pile up behind each other (observed: a wedged
compile holding the queue for ~2 days). ``flock(2)`` releases automatically
when the holder dies, so a crashed compile never leaves a stale lock.

The holder record inside the lock file is JSON — ``{"pid": …, "host": …}`` —
because a bare pid is meaningless when several machines share a disk and
therefore share ``.tesserae``: pid 4711 on one server says nothing about pid
4711 on another. Records written by older versions are a bare integer and are
still read; see :func:`read_holder`.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows: locking degrades to no-op
    fcntl = None  # type: ignore[assignment]


class CompileLockHeldError(RuntimeError):
    """Another compile/refresh already holds this project's lock."""


def _host_tag() -> str:
    """This machine's id, or ``""`` if it cannot be determined.

    Imported lazily and defensively: the lock is the lowest layer here and
    must not acquire a hard dependency on the session store, nor fail to lock
    because an identity file could not be written.
    """
    try:
        from .harness_sessions import local_host_id

        return local_host_id()
    except Exception:  # pragma: no cover — identity is a nicety, locking is not
        return ""


def read_holder(path: str | Path) -> Optional[Dict[str, object]]:
    """Parse a lock file's holder record, tolerating the legacy bare-pid form.

    Returns ``None`` when the file is absent, empty (i.e. released), or
    unreadable — every uncertain case answers "nobody claims this", because
    the callers use it to describe a lock, never to decide whether to break
    one. A legacy record answers ``{"pid": N}`` with no ``host``: the machine
    is genuinely unknown, and saying so is more useful than guessing "here".
    """
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if isinstance(payload, dict):
        return payload
    # A legacy record is a bare pid — and json.loads parses that happily as an
    # int, so this branch (not a JSONDecodeError) is where back-compat lives.
    if isinstance(payload, int):
        return {"pid": payload}
    return None


def describe_holder(holder: Optional[Dict[str, object]]) -> str:
    """A human-readable ``" (pid 4711 on srv-b)"`` suffix, or ``""``.

    Naming the machine matters: "another compile is running" means something
    different when it is on a server you are not looking at.
    """
    if not holder:
        return ""
    pid = holder.get("pid")
    host = str(holder.get("host") or "")
    if pid is None and not host:
        return ""
    if host:
        return f" (pid {pid} on {host})" if pid is not None else f" (on {host})"
    return f" (pid {pid})"


@contextmanager
def compile_lock(
    tesserae_dir: str | Path,
    wait_seconds: Optional[float] = None,
    name: str = "compile.lock",
    on_wait: Optional[Callable[[float, Optional[Dict[str, object]]], None]] = None,
) -> Iterator[None]:
    """Hold ``<tesserae_dir>/<name>`` exclusively for the block.

    Fails fast with :class:`CompileLockHeldError` when another process holds
    the lock; set ``wait_seconds`` (or the ``TESSERAE_COMPILE_LOCK_WAIT`` env
    var) to poll for the lock instead of failing. ``wait_seconds`` is an
    explicit argument rather than something inferred from, say, whether stderr
    is a tty: the same command must not silently change behaviour under
    ``tee``, in tmux capture, or in CI.

    ``on_wait(elapsed_seconds, holder)`` is called about every five seconds
    while waiting, so a caller can tell the user what it is waiting for
    instead of appearing hung. It is never called when the lock is free.

    ``name`` exists so a SHORT critical section can get its own lock file
    instead of queueing behind a multi-minute compile: ``agent_write`` takes
    ``agent-writes.lock`` for a read-dedupe-append that costs ~1 ms. No cycle
    is possible — the write path never takes ``compile.lock``.
    """
    directory = Path(tesserae_dir)
    if fcntl is None:  # pragma: no cover — Windows
        yield
        return
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / name
    handle = lock_path.open("a+", encoding="utf-8")
    if wait_seconds is None:
        try:
            wait_seconds = float(os.environ.get("TESSERAE_COMPILE_LOCK_WAIT", "0") or 0)
        except ValueError:
            wait_seconds = 0.0
    started = time.monotonic()
    deadline = started + max(0.0, wait_seconds)
    next_notice = started + 5.0
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                now = time.monotonic()
                if now >= deadline:
                    raise CompileLockHeldError(
                        "another tesserae compile/refresh is already running for "
                        f"this project{describe_holder(read_holder(lock_path))}; "
                        "retry when it finishes, pass --wait to queue behind it, "
                        "or set TESSERAE_COMPILE_LOCK_WAIT=<seconds>"
                    ) from None
                if on_wait is not None and now >= next_notice:
                    next_notice = now + 5.0
                    try:
                        on_wait(now - started, read_holder(lock_path))
                    except Exception:
                        on_wait = None  # a broken reporter must not break the wait
                time.sleep(0.2)
        handle.seek(0)
        handle.truncate()
        # Identify the MACHINE as well as the process. Without it, a lock held
        # by another server reads as a local pid that almost certainly belongs
        # to some unrelated local process, and every diagnostic built on it —
        # doctor, the error message above — describes the wrong thing.
        handle.write(json.dumps({"pid": os.getpid(), "host": _host_tag()}))
        handle.flush()
        try:
            yield
        finally:
            try:
                handle.seek(0)
                handle.truncate()
                handle.flush()
            except OSError:
                pass
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()

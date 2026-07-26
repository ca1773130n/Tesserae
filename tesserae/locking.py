"""Per-project compile lock.

A session-end hook can fire ``tesserae refresh`` while a long compile is
still running for the same project; without a lock the two stack onto the
same ``.tesserae`` state and pile up behind each other (observed: a wedged
compile holding the queue for ~2 days). ``flock(2)`` releases automatically
when the holder dies, so a crashed compile never leaves a stale lock.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows: locking degrades to no-op
    fcntl = None  # type: ignore[assignment]


class CompileLockHeldError(RuntimeError):
    """Another compile/refresh already holds this project's lock."""


@contextmanager
def compile_lock(
    tesserae_dir: str | Path,
    wait_seconds: Optional[float] = None,
    name: str = "compile.lock",
) -> Iterator[None]:
    """Hold ``<tesserae_dir>/<name>`` exclusively for the block.

    Fails fast with :class:`CompileLockHeldError` when another process holds
    the lock; set ``wait_seconds`` (or the ``TESSERAE_COMPILE_LOCK_WAIT`` env
    var) to poll for the lock instead of failing.

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
    handle = (directory / name).open("a+", encoding="utf-8")
    if wait_seconds is None:
        try:
            wait_seconds = float(os.environ.get("TESSERAE_COMPILE_LOCK_WAIT", "0") or 0)
        except ValueError:
            wait_seconds = 0.0
    deadline = time.monotonic() + max(0.0, wait_seconds)
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    holder = ""
                    try:
                        handle.seek(0)
                        pid = handle.read().strip()
                        if pid:
                            holder = f" (pid {pid})"
                    except OSError:
                        pass
                    raise CompileLockHeldError(
                        "another tesserae compile/refresh is already running for "
                        f"this project{holder}; retry when it finishes, or set "
                        "TESSERAE_COMPILE_LOCK_WAIT=<seconds> to wait for the lock"
                    ) from None
                time.sleep(0.2)
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
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

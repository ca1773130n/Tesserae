"""Per-project compile lock.

A session-end hook can fire ``tesserae refresh`` while a long compile is
still running for the same project; without a lock the two stack onto the
same ``.tesserae`` state and pile up behind each other (observed: a wedged
compile holding the queue for ~2 days). ``flock(2)`` releases automatically
when the holder dies, so a crashed compile never leaves a stale lock.

The lock is portable, not POSIX-only: ``flock`` where it exists and
``msvcrt.locking`` on Windows. It used to be ``if fcntl is None: yield`` — a
no-op — and the one write path with more than one producer is the agent-write
overlay append, which takes ``agent-writes.lock`` for a ~1 ms
read-dedupe-append. Unsynchronized appends there interleave into a torn JSONL
line, and ``replay_agent_writes`` skips an unusable record rather than failing,
so on Windows a lost agent write degraded into a warning nobody reads.

The holder record inside the lock file is JSON — ``{"pid": …, "host": …}`` —
because a bare pid is meaningless when several machines share a disk and
therefore share ``.tesserae``: pid 4711 on one server says nothing about pid
4711 on another. Records written by older versions are a bare integer and are
still read; see :func:`read_holder`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows, where msvcrt takes over
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # pragma: no cover — every POSIX platform
    msvcrt = None  # type: ignore[assignment]

# ``msvcrt.locking`` locks a byte RANGE from the current file position rather
# than the whole open file description, so the range is pinned to byte 0 and
# one byte long. The holder record is rewritten (and truncated) under the lock,
# so a range that tracked the file's length would let two writers lock disjoint
# parts of the same file and both believe they held it. Locking past EOF is
# legal on Windows, which is what makes a fixed range safe on an empty file.
_LOCK_OFFSET = 0
_LOCK_BYTES = 1

_warned_unlockable = False

logger = logging.getLogger(__name__)


def locking_unavailable() -> bool:
    """True when this interpreter has neither primitive.

    Read live rather than frozen at import so a test can substitute either
    module and have every caller — including doctor's probe — agree about
    which mechanism is in play.
    """
    return fcntl is None and msvcrt is None


class CompileLockHeldError(RuntimeError):
    """Another compile/refresh already holds this project's lock."""


def try_lock_exclusive(handle) -> bool:
    """Take an exclusive non-blocking lock on ``handle``; False if held.

    One contract over two primitives, because the callers must not care which
    platform they are on: ``flock`` locks the open file description, while
    ``msvcrt.locking`` locks ``_LOCK_BYTES`` from the current position — hence
    the seek. Both conflict between two handles on the same file even inside a
    single process, which is what makes the exclusion real for threads as well
    as processes.
    """
    if fcntl is not None:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True
    handle.seek(_LOCK_OFFSET)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, _LOCK_BYTES)
    except OSError:
        return False
    return True


def unlock_exclusive(handle) -> None:
    """Release what :func:`try_lock_exclusive` took.

    ``msvcrt`` unlocks by region, so the position must be put back where the
    lock was taken — writing the holder record left it at end-of-file. Errors
    are swallowed on purpose: this runs in a ``finally``, closing the handle
    releases the lock under both primitives anyway, and a failed release must
    never mask the exception the locked block was already raising.
    """
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return
        handle.seek(_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, _LOCK_BYTES)
    except OSError:
        pass


def _warn_unlockable_once() -> None:
    """Say out loud, once per process, that writes are unserialized.

    A documented no-op that says nothing at runtime is the silent-degradation
    pattern this repo keeps having to fix: the overlay append is the one write
    path with more than one producer, and unsynchronized appends interleave
    into a torn JSONL line that replay skips. Losing an agent write is worth a
    line on stderr even on a platform nobody expects to reach.
    """
    global _warned_unlockable
    if _warned_unlockable:
        return
    _warned_unlockable = True
    print(
        "warning: no file-locking primitive on this platform (neither fcntl "
        "nor msvcrt); concurrent tesserae compiles and agent writes are NOT "
        "serialized and can interleave",
        file=sys.stderr,
    )


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
    what: str = "compile/refresh",
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

    ``what`` names the holder in the "already running" error, so a lock that
    is not the compile's (``sessions-import.lock``) does not claim to be.
    """
    directory = Path(tesserae_dir)
    if locking_unavailable():
        _warn_unlockable_once()
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
            if try_lock_exclusive(handle):
                break
            now = time.monotonic()
            if now >= deadline:
                raise CompileLockHeldError(
                    f"another tesserae {what} is already running for "
                    f"this project{describe_holder(read_holder(lock_path))}; "
                    "retry when it finishes, pass --wait to queue behind it, "
                    "or set TESSERAE_COMPILE_LOCK_WAIT=<seconds>"
                )
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
            unlock_exclusive(handle)
    finally:
        handle.close()


# --------------------------------------------------------------------------- #
# Journaled atomic writes                                                      #
# --------------------------------------------------------------------------- #

#: Directory, beside the files it protects, that holds one intent record per
#: write in flight. Each record is its own file rather than a line in a shared
#: log on purpose: several hosts mount one ``.tesserae``, and an append from
#: two of them over a network filesystem is exactly the torn-write hazard the
#: journal exists to remove.
JOURNAL_DIRNAME = "journal"
#: A record whose writer runs on ANOTHER host is only judged abandoned after
#: this long. Its pid means nothing here (see :mod:`tesserae.engine.pidlock`),
#: so age is the only machine-independent signal; a publish takes seconds.
JOURNAL_FOREIGN_STALE_S = 600.0


class WriteConflictError(RuntimeError):
    """The target changed since the caller read it; the write was not made."""


def sha256_of(path: str | Path) -> Optional[str]:
    """Hex sha256 of ``path``'s bytes, or ``None`` when it does not exist."""

    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return None
    return hashlib.sha256(data).hexdigest()


def journal_dir_for(target: str | Path) -> Path:
    """``<nearest .tesserae ancestor>/journal``, else ``<target dir>/journal``.

    One journal per ``.tesserae`` keeps ``doctor --recover`` to a single
    directory however deep the target sits (``harness_sessions/<h>/x.json``).
    """
    parent = Path(target).parent
    for candidate in (parent, *parent.parents):
        if candidate.name == ".tesserae":
            return candidate / JOURNAL_DIRNAME
    return parent / JOURNAL_DIRNAME


def _fsync_dir(directory: Path) -> None:
    """Persist a rename. POSIX only: Windows cannot open a directory, and its
    ``MoveFileEx`` replace is not made durable this way anyway."""
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def journaled_write(
    target: str | Path,
    content: str,
    *,
    expected_sha256: Optional[str] = None,
    durable: bool = True,
) -> str:
    """Replace ``target`` with ``content`` atomically; return the new sha256.

    The sequence is: check ``expected_sha256`` against the bytes on disk →
    record intent (target, temp path, new sha256, the target's current
    sha256) in the journal → write a private temp file → fsync it → rename
    over the target → fsync the directory → drop the intent record. Intent
    goes first so that every temp file a killed writer can leave behind is
    named by a record that says whose it was and what it should hash to.

    * ``expected_sha256`` — the sha256 of the bytes the caller read; omit it
      to skip the check. A mismatch raises :class:`WriteConflictError` before anything is
      written: a read-modify-write that does not hold the compile lock would
      otherwise silently erase whatever landed in between. The check and the
      rename are not one atomic step, so it narrows that window rather than
      closing it — the lock is still what excludes writers.
    * ``durable=False`` skips the fsyncs and the journal, keeping only the
      atomic rename. For records a later run re-derives anyway, where an fsync
      per file would multiply the cost of a bulk import.

    The temp name is ``<stem>.tmp.<pid>.<hex>``, the shape the sidecar
    registry and ``doctor``'s orphan sweep already know. A writer killed before
    the rename leaves a record plus a temp file that is either complete (its
    sha256 matches) or not; :func:`recover_journal` rolls a complete one
    forward when the target has not moved on since, and discards the rest.
    """

    path = Path(target)
    if expected_sha256 is not None:
        current = sha256_of(path)
        if current != expected_sha256:
            raise WriteConflictError(
                f"{path} changed since it was read (expected sha256 "
                f"{expected_sha256[:12]}…, found {(current or 'nothing')[:12]}…); "
                "re-read it and retry"
            )
    data = content.encode("utf-8")
    new_sha = hashlib.sha256(data).hexdigest()
    tmp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    entry: Optional[Path] = None
    try:
        if durable:
            entry = _journal_begin(path, tmp, new_sha)
        with open(tmp, "wb") as handle:
            handle.write(data)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(tmp, path)
        if durable:
            _fsync_dir(path.parent)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError as exc:
                logger.warning("could not remove temp file %s: %s", tmp, exc)
        if entry is not None:
            try:
                entry.unlink()
            except OSError as exc:
                # A leftover record is harmless — recovery finds the temp gone
                # and drops it — but say so rather than hide it.
                logger.warning("journal record %s left behind: %s", entry, exc)
    return new_sha


def _journal_begin(target: Path, tmp: Path, new_sha: str) -> Path:
    """Write the intent record for one in-flight replace; return its path."""

    from .engine import pidlock

    directory = journal_dir_for(target)
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "target": str(target),
        "tmp": str(tmp),
        "sha256": new_sha,
        "prior_sha256": sha256_of(target),
        "host": _host_tag(),
        "pid": os.getpid(),
        "start_time": pidlock.own_start_time(),
        "created_at": time.time(),
    }
    entry = directory / f"{os.getpid()}.{secrets.token_hex(6)}.wal"
    with open(entry, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    return entry


def _journal_writer_alive(record: Dict[str, object], entry: Path, now: float) -> bool:
    """Could the writer of ``record`` still be between journal and rename?"""
    from .engine import pidlock

    host = str(record.get("host") or "")
    if host and host != _host_tag():
        try:
            age = now - float(record.get("created_at") or entry.stat().st_mtime)
        except (OSError, TypeError, ValueError):
            return True
        return age < JOURNAL_FOREIGN_STALE_S
    return pidlock.owner_is_alive(
        {"pid": record.get("pid"), "start_time": record.get("start_time")}
    )


def pending_journal(tesserae_dir: str | Path) -> List[Path]:
    """Intent records under ``<tesserae_dir>/journal``, oldest name first."""
    directory = Path(tesserae_dir) / JOURNAL_DIRNAME
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.wal"))


def recover_journal(
    tesserae_dir: str | Path, *, dry_run: bool = False, now: Optional[float] = None
) -> List[str]:
    """Finish or discard every write whose writer died mid-publish.

    For each intent record whose writer is gone (dead pid on this host, or
    older than :data:`JOURNAL_FOREIGN_STALE_S` on another one):

    * temp file complete (its sha256 matches the record) and the target still
      holds the bytes it held when the write began → **roll forward**: only
      the rename was missing;
    * temp file incomplete, or the target has moved on (another writer
      published since) → **discard** the temp: a partial file is not a
      graph, and rolling a stale one forward would erase the newer publish;
    * temp file gone → the rename happened, or the writer died before
      creating it; drop the record.

    Records whose writer may still be running are left alone and reported.
    Returns one line per record describing what was (or, with ``dry_run``,
    would be) done.
    """
    clock = time.time() if now is None else now
    actions: List[str] = []
    for entry in pending_journal(tesserae_dir):
        try:
            record = json.loads(entry.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as exc:
            # Torn record: the writer died while writing it, i.e. before it
            # created its temp file, so neither the target nor any temp file
            # was touched.
            actions.append(f"{entry.name}: unreadable record ({exc}); dropped")
            if not dry_run:
                entry.unlink(missing_ok=True)
            continue
        if _journal_writer_alive(record, entry, clock):
            actions.append(f"{entry.name}: writer may still be running; left alone")
            continue
        target = Path(str(record.get("target") or ""))
        tmp = Path(str(record.get("tmp") or ""))
        if tmp.name and tmp.is_file():
            complete = sha256_of(tmp) == record.get("sha256")
            if complete and sha256_of(target) == record.get("prior_sha256"):
                actions.append(f"{entry.name}: rolled {target.name} forward")
                if not dry_run:
                    os.replace(tmp, target)
                    _fsync_dir(target.parent)
            else:
                why = f"{target.name} moved on since" if complete else "temp file incomplete"
                actions.append(f"{entry.name}: {why}; discarded {tmp.name}")
                if not dry_run:
                    tmp.unlink(missing_ok=True)
        else:
            actions.append(f"{entry.name}: no temp file for {target.name}; record dropped")
        if not dry_run:
            entry.unlink(missing_ok=True)
    return actions

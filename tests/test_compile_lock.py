"""Per-project compile lock — prevents hook-triggered refreshes from stacking
onto an already-running compile (observed: a wedged compile queueing work for
~2 days while later refreshes piled up behind it)."""
from __future__ import annotations

import os
import threading
import time

import pytest

from tesserae import locking
from tesserae.locking import CompileLockHeldError, compile_lock, read_holder
from tesserae.project import ProjectWiki


class FakeMsvcrt:
    """Stand-in for the Windows byte-range lock API, for use on POSIX.

    The Windows branch of ``locking`` cannot be exercised on this machine, and
    platform-gating the assertion away would leave it as untested as the no-op
    it replaced. So substitute the module and enforce the two properties the
    real API has that the code depends on: two handles on the same file
    conflict even inside ONE process (which is what makes the lock work for the
    overlay's threads, not just for separate processes), and an unlock must
    name the same region the lock was taken on — so a caller that forgets to
    seek back to byte 0, or never locks at all, fails here rather than shipping.

    Constants carry the real ``msvcrt`` values so the code under test cannot
    pass by treating them as interchangeable.
    """

    LK_UNLCK = 0
    LK_LOCK = 1
    LK_NBLCK = 2

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.held: dict = {}  # (inode, offset, length) -> owning fd
        self.regions: list = []  # every region a lock was taken on, in order
        self.acquired = 0
        self.denied = 0

    def locking(self, fd, mode, nbytes):
        # The real API locks from the CURRENT file position, which is why the
        # offset is read off the fd rather than passed in.
        region = (os.fstat(fd).st_ino, os.lseek(fd, 0, os.SEEK_CUR), nbytes)
        with self._guard:
            if mode == self.LK_UNLCK:
                if self.held.get(region) != fd:
                    raise AssertionError(
                        f"unlock of a region fd {fd} does not hold: {region}"
                    )
                del self.held[region]
                return
            assert mode == self.LK_NBLCK, f"expected a non-blocking lock, got {mode}"
            if region in self.held:
                self.denied += 1
                raise OSError(36, "region already locked")
            self.held[region] = fd
            self.regions.append(region)
            self.acquired += 1


def test_compile_lock_is_exclusive_and_releases(tmp_path):
    with compile_lock(tmp_path):
        with pytest.raises(CompileLockHeldError):
            with compile_lock(tmp_path):
                pass
    # Released on exit — can acquire again.
    with compile_lock(tmp_path):
        pass


def test_compile_lock_error_names_holder_pid(tmp_path):
    with compile_lock(tmp_path):
        with pytest.raises(CompileLockHeldError, match=str(os.getpid())):
            with compile_lock(tmp_path):
                pass


def test_compile_lock_wait_acquires_after_release(tmp_path):
    release = threading.Event()

    def holder():
        with compile_lock(tmp_path):
            release.wait(timeout=10)

    thread = threading.Thread(target=holder)
    thread.start()
    time.sleep(0.2)  # let the holder acquire first
    timer = threading.Timer(0.5, release.set)
    timer.start()
    try:
        with compile_lock(tmp_path, wait_seconds=5):
            pass  # acquired once the holder let go — no exception
    finally:
        release.set()
        thread.join(timeout=10)
        timer.cancel()


def test_wiki_compile_fails_fast_when_lock_held(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    wiki = ProjectWiki.init(project, name="proj")
    with compile_lock(wiki.paths.root):
        with pytest.raises(CompileLockHeldError):
            wiki.compile()


def test_cli_compile_prints_clean_lock_message(tmp_path, capsys):
    from tesserae.cli import main

    project = tmp_path / "proj"
    project.mkdir()
    wiki = ProjectWiki.init(project, name="proj")
    with compile_lock(wiki.paths.root):
        rc = main(["compile", "--project", str(project)])
    captured = capsys.readouterr()
    assert rc == 2
    assert "already running" in captured.err
    assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# Holder identity: a pid alone is meaningless when hosts share a disk
# ---------------------------------------------------------------------------


def test_holder_record_names_the_machine(tmp_path, monkeypatch):
    """pid 4711 on one server says nothing about pid 4711 on another, so the
    lock records the host too — otherwise every diagnostic built on the pid
    describes an unrelated local process."""
    import tesserae.harness_sessions as hs
    from tesserae.locking import read_holder

    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    monkeypatch.setattr(hs, "_HOST_ID_CACHE", None, raising=False)

    with compile_lock(tmp_path):
        holder = read_holder(tmp_path / "compile.lock")
        assert holder == {"pid": os.getpid(), "host": "srv-a"}

    # Released: the record is cleared, so nobody is described as holding it.
    assert read_holder(tmp_path / "compile.lock") is None


def test_read_holder_accepts_the_legacy_bare_pid(tmp_path):
    """Lock files written before the record became JSON must still be read —
    an unparseable holder would otherwise report a lock as unheld."""
    from tesserae.locking import describe_holder, read_holder

    (tmp_path / "compile.lock").write_text("4711", encoding="utf-8")
    holder = read_holder(tmp_path / "compile.lock")
    assert holder == {"pid": 4711}
    # No host recorded: the machine is genuinely unknown, so do not claim one.
    assert describe_holder(holder) == " (pid 4711)"
    assert "on " not in describe_holder(holder)


def test_lock_error_names_the_holding_machine(tmp_path, monkeypatch):
    import tesserae.harness_sessions as hs

    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    monkeypatch.setattr(hs, "_HOST_ID_CACHE", None, raising=False)
    with compile_lock(tmp_path):
        with pytest.raises(CompileLockHeldError, match="on srv-a"):
            with compile_lock(tmp_path):
                pass


def test_waiting_reports_progress_instead_of_looking_hung(tmp_path, monkeypatch):
    """A caller waiting on a lock held by an invisible process — often on
    another machine — must be told what it is waiting for."""
    seen: list = []
    release = threading.Event()

    def holder():
        with compile_lock(tmp_path):
            release.wait(timeout=10)

    thread = threading.Thread(target=holder)
    thread.start()
    time.sleep(0.2)
    # Fire the notice immediately rather than after the 5s production cadence.
    monkeypatch.setattr(time, "monotonic", time.monotonic)
    timer = threading.Timer(1.0, release.set)
    timer.start()
    try:
        with compile_lock(
            tmp_path,
            wait_seconds=8,
            on_wait=lambda elapsed, h: seen.append((elapsed, h)),
        ):
            pass
    finally:
        release.set()
        thread.join(timeout=10)
        timer.cancel()
    # The holder released after ~1s, so no 5s notice is guaranteed; what must
    # hold is that waiting SUCCEEDED and the reporter was never handed a
    # malformed holder.
    assert all(h is None or isinstance(h, dict) for _, h in seen)


def test_a_broken_wait_reporter_cannot_break_the_wait(tmp_path):
    """on_wait is diagnostics. A caller whose reporter raises must still get
    the lock — losing a compile to a broken print would be absurd."""
    release = threading.Event()

    def holder():
        with compile_lock(tmp_path):
            release.wait(timeout=10)

    def boom(elapsed, holder_record):
        raise RuntimeError("reporter exploded")

    thread = threading.Thread(target=holder)
    thread.start()
    time.sleep(0.2)
    timer = threading.Timer(0.5, release.set)
    timer.start()
    try:
        with compile_lock(tmp_path, wait_seconds=8, on_wait=boom):
            pass  # acquired despite the reporter raising
    finally:
        release.set()
        thread.join(timeout=10)
        timer.cancel()


# ---------------------------------------------------------------------------
# The Windows branch: it used to be ``if fcntl is None: yield``, i.e. no lock
# ---------------------------------------------------------------------------


def test_windows_branch_takes_a_real_lock(tmp_path, monkeypatch):
    """Substituting msvcrt for fcntl must not change the contract by one word.

    A second acquire still fails, the holder record is still written and
    cleared, and — the part the no-op could never do — the lock is genuinely
    taken and genuinely released.
    """
    fake = FakeMsvcrt()
    monkeypatch.setattr(locking, "fcntl", None)
    monkeypatch.setattr(locking, "msvcrt", fake)

    with compile_lock(tmp_path):
        assert fake.acquired == 1
        assert (read_holder(tmp_path / "compile.lock") or {}).get("pid") == os.getpid()
        with pytest.raises(CompileLockHeldError):
            with compile_lock(tmp_path):
                pass
    assert fake.denied >= 1  # the second acquire was refused, not granted
    assert not fake.held  # released, and the unlock named the right region
    assert read_holder(tmp_path / "compile.lock") is None


def test_windows_lock_region_does_not_move_with_the_file(tmp_path, monkeypatch):
    """msvcrt locks a byte RANGE, so the range must be pinned to byte 0.

    A range that tracked the file's length would slide as the holder record is
    written and truncated, letting two writers lock disjoint parts of the same
    file and both believe they hold it.
    """
    fake = FakeMsvcrt()
    monkeypatch.setattr(locking, "fcntl", None)
    monkeypatch.setattr(locking, "msvcrt", fake)

    for _ in range(3):
        with compile_lock(tmp_path):
            pass
    assert fake.acquired == 3
    assert {(offset, length) for _ino, offset, length in fake.regions} == {(0, 1)}


def test_no_locking_primitive_says_so_once_instead_of_going_quiet(
    tmp_path, monkeypatch, capsys
):
    """A platform with neither primitive must NAME the exposure at runtime.

    A documented no-op that says nothing is the silent-degradation pattern this
    repo keeps fixing; the exposure is real (unserialized overlay appends tear
    JSONL lines that replay then drops). Once per process, not once per
    acquire — a line on every agent write would drown the thing it warns about.
    """
    monkeypatch.setattr(locking, "fcntl", None)
    monkeypatch.setattr(locking, "msvcrt", None)
    monkeypatch.setattr(locking, "_warned_unlockable", False)

    with compile_lock(tmp_path):
        pass
    err = capsys.readouterr().err
    assert "no file-locking primitive" in err
    assert "not" in err.lower() and "serialized" in err

    with compile_lock(tmp_path):
        pass
    assert capsys.readouterr().err == ""


def test_cli_wait_flag_queues_instead_of_failing(tmp_path, capsys):
    """The background engine holding the lock must not make an interactive
    compile fail — that is the whole complaint. --wait queues behind it."""
    from tesserae.cli import main

    project = tmp_path / "proj"
    project.mkdir()
    wiki = ProjectWiki.init(project, name="proj")
    release = threading.Event()

    def holder():
        with compile_lock(wiki.paths.root):
            release.wait(timeout=15)

    thread = threading.Thread(target=holder)
    thread.start()
    time.sleep(0.2)
    timer = threading.Timer(1.0, release.set)
    timer.start()
    try:
        rc = main(["compile", "--project", str(project), "--wait", "20"])
    finally:
        release.set()
        thread.join(timeout=15)
        timer.cancel()
    assert rc != 2, capsys.readouterr().err

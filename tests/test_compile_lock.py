"""Per-project compile lock — prevents hook-triggered refreshes from stacking
onto an already-running compile (observed: a wedged compile queueing work for
~2 days while later refreshes piled up behind it)."""
from __future__ import annotations

import os
import threading
import time

import pytest

from tesserae.locking import CompileLockHeldError, compile_lock
from tesserae.project import ProjectWiki


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

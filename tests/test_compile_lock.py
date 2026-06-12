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

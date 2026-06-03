"""Tests for daemon trigger sources (Plan 02-02, ENG-05).

Three deterministic guarantees, mirroring test_watch.py / test_vault_watch.py
conventions (tmp_path, caplog, monkeypatch; no real sleeps / hangs):

1. The WatchLoop ``on_change`` closure delivers a ``TriggerEvent(source="watch")``
   onto the daemon queue through the ``call_soon_threadsafe`` bridge.
2. A poller thread whose body raises is logged (ERROR, "tesserae.daemon") and
   is NON-FATAL — the call returns normally, the daemon survives.
3. After ``stop_event`` is set + join, zero orphaned non-daemon source threads
   remain in ``threading.enumerate()``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path

import pytest

from tesserae.engine.daemon import Daemon, TriggerEvent


# --------------------------------------------------------------- helpers


def _make_daemon_with_loop(tmp_path: Path, **kwargs) -> tuple[Daemon, asyncio.AbstractEventLoop]:
    """Build a Daemon with a real (un-run) loop + queue installed on it."""
    d = Daemon(tmp_path, **kwargs)
    loop = asyncio.new_event_loop()
    d._loop = loop
    d._queue = asyncio.Queue()
    return d, loop


def _drain_queue(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue) -> list:
    """Pump scheduled call_soon_threadsafe callbacks, then drain the queue."""
    # Run one iteration so the queued call_soon_threadsafe callbacks execute.
    loop.run_until_complete(asyncio.sleep(0))
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


# ------------------------------------------------- 1. on_change -> queue


def test_watch_source_on_change_enqueues_trigger_event(tmp_path: Path) -> None:
    """The on_change closure pushes a watch TriggerEvent onto the queue.

    Proves the thread->loop bridge wiring with no thread and no wall clock:
    we intercept the ``WatchLoop`` constructed inside ``_start_watch_source``,
    capture its ``on_change``, and call it directly with a fake path list.
    """
    d, loop = _make_daemon_with_loop(tmp_path)
    captured = {}

    class _FakeWatchLoop:
        def __init__(self, root, *, interval, on_change, quiet):  # noqa: D401
            captured["on_change"] = on_change

    # Patch the lazily-imported WatchLoop symbol used by _start_watch_source.
    import tesserae.watch as watch_mod

    original = watch_mod.WatchLoop
    watch_mod.WatchLoop = _FakeWatchLoop
    try:
        # Stop the thread body immediately so the spawned thread does nothing.
        d._stop_event.set()
        d._start_watch_source(loop)
        d._stop_event.clear()  # re-enable enqueue for the closure call below

        on_change = captured["on_change"]
        changed = [tmp_path / "docs" / "a.md", tmp_path / "docs" / "b.md"]
        on_change(changed)

        events = _drain_queue(loop, d._queue)
    finally:
        watch_mod.WatchLoop = original
        for t in d._threads:
            t.join(timeout=2.0)
        loop.close()

    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, TriggerEvent)
    assert ev.source == "watch"
    assert set(ev.changed_paths) == set(changed)
    assert ev.changed_only is True


def test_on_change_dropped_when_stop_event_set(tmp_path: Path) -> None:
    """Late on_change events during shutdown are silently dropped."""
    d, loop = _make_daemon_with_loop(tmp_path)
    captured = {}

    class _FakeWatchLoop:
        def __init__(self, root, *, interval, on_change, quiet):
            captured["on_change"] = on_change

    import tesserae.watch as watch_mod

    original = watch_mod.WatchLoop
    watch_mod.WatchLoop = _FakeWatchLoop
    try:
        d._stop_event.set()
        d._start_watch_source(loop)
        # stop_event still set: closure should no-op.
        captured["on_change"]([tmp_path / "x.md"])
        events = _drain_queue(loop, d._queue)
    finally:
        watch_mod.WatchLoop = original
        for t in d._threads:
            t.join(timeout=2.0)
        loop.close()

    assert events == []


# ------------------------------------- 2. poller exception logged, not fatal


def test_watch_source_exception_is_logged_not_fatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    d, loop = _make_daemon_with_loop(tmp_path)

    class _BoomWatchLoop:
        interval = 0.01

        def snapshot(self):
            raise RuntimeError("snapshot boom")

    try:
        with caplog.at_level(logging.ERROR, logger="tesserae.daemon"):
            # Must return normally — no exception escapes.
            d._run_watch_source(_BoomWatchLoop())
    finally:
        loop.close()

    assert any(
        "watch-source thread died" in r.getMessage() and r.name == "tesserae.daemon"
        for r in caplog.records
    )
    assert any(r.exc_info is not None for r in caplog.records)


def test_vault_source_exception_is_logged_not_fatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    d, loop = _make_daemon_with_loop(tmp_path)

    # No-op sleep so the loop reaches _tick() instantly.
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    class _BoomWatcher:
        poll_interval = 0.01

        def _tick(self):
            raise RuntimeError("tick boom")

    try:
        with caplog.at_level(logging.ERROR, logger="tesserae.daemon"):
            d._run_vault_source(_BoomWatcher())
    finally:
        loop.close()

    assert any(
        "vault-source thread died" in r.getMessage() and r.name == "tesserae.daemon"
        for r in caplog.records
    )
    assert any(r.exc_info is not None for r in caplog.records)


# ------------------------------------- 3. no orphaned threads after stop


def test_no_orphaned_threads_after_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After stop_event + join, no live non-daemon source threads remain."""
    # No-op sleep so the watch poll loop spins fast and re-checks stop_event.
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)

    d, loop = _make_daemon_with_loop(tmp_path, enable_vault=False)

    class _QuietWatchLoop:
        interval = 0.0

        def __init__(self, root, *, interval, on_change, quiet):
            pass

        def snapshot(self):
            return {}

        @staticmethod
        def diff(a, b):
            return [], [], []

    import tesserae.watch as watch_mod

    original = watch_mod.WatchLoop
    watch_mod.WatchLoop = _QuietWatchLoop
    try:
        d._start_sources(loop)
        # The watch-source thread is alive and a daemon thread.
        watch_threads = [t for t in d._threads if t.name == "watch-source"]
        assert len(watch_threads) == 1
        wt = watch_threads[0]
        assert wt.daemon is True
        # No vault-source thread (disabled).
        assert all(t.name != "vault-source" for t in d._threads)

        # Request stop and join within a short timeout.
        d._stop_event.set()
        for t in d._threads:
            t.join(timeout=2.0)
        assert all(not t.is_alive() for t in d._threads)
    finally:
        watch_mod.WatchLoop = original
        d._stop_event.set()
        for t in d._threads:
            t.join(timeout=2.0)
        loop.close()

    live = [
        t
        for t in threading.enumerate()
        if t.name in ("watch-source", "vault-source") and t.is_alive() and not t.daemon
    ]
    assert live == []

"""Deterministic unit tests for the daemon core (no real sleeps).

These drive the asyncio drain loop directly via ``loop.run_until_complete``
(pytest-asyncio is NOT a dependency). Debounce is pinned to ``0.0`` and
determinism comes from event-queue ordering, not wall-clock timing: all events
in a burst are enqueued BEFORE the drain runs, so they coalesce.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

import pytest

from tesserae.engine.daemon import Daemon, TriggerEvent


def _new_loop() -> asyncio.AbstractEventLoop:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def _drain_one_burst(d: Daemon, loop: asyncio.AbstractEventLoop, paths):
    """Pre-load a burst, run the drain, and block until the debounce task fires.

    Determinism: all events are enqueued BEFORE the drain runs, so they coalesce
    into one debounce task. We trip ``stop_event`` only AFTER that task completes
    (signalled by the injected run_pipeline), so there is no wall-clock race.
    """
    ran = threading.Event()
    inner = d._run_pipeline_override

    def wrapped(merged):
        try:
            if inner is not None:
                inner(merged)
        finally:
            d._loop.call_soon_threadsafe(ran.set)

    d._run_pipeline_override = wrapped

    async def scenario():
        d._stop_event.clear()
        d._queue = asyncio.Queue()
        for p in paths:
            d._queue.put_nowait(TriggerEvent(source="t", changed_paths=[p]))

        async def stopper():
            # Wait for the debounce task to actually invoke the pipeline, then stop.
            while not ran.is_set():
                await asyncio.sleep(0)
            d._stop_event.set()

        st = asyncio.create_task(stopper())
        await d._drain_loop()
        await st

    loop.run_until_complete(scenario())
    d._run_pipeline_override = inner


def test_burst_coalesces_to_one_pipeline_run(tmp_path):
    """Five queued TriggerEvents -> exactly ONE pipeline run with merged paths."""
    calls = []
    d = Daemon(tmp_path, debounce=0.0, queue_timeout=0.01, run_pipeline=calls.append)
    loop = _new_loop()
    d._loop = loop
    try:
        paths = [Path(f"f{i}.md") for i in range(5)]
        _drain_one_burst(d, loop, paths)
    finally:
        loop.close()

    assert len(calls) == 1, f"expected 1 coalesced run, got {len(calls)}"
    assert set(calls[0]) == set(paths), "merged paths must cover all 5 events"


def test_pipeline_exception_does_not_kill_daemon(tmp_path):
    """A run that raises is contained; the next burst still drives a run."""
    record = {"n": 0, "ok": []}

    def flaky(paths):
        record["n"] += 1
        if record["n"] == 1:
            raise RuntimeError("boom on first burst")
        record["ok"].append(paths)

    d = Daemon(tmp_path, debounce=0.0, queue_timeout=0.01, run_pipeline=flaky)
    loop = _new_loop()
    d._loop = loop
    try:
        # Burst 1: the injected pipeline raises. The drain loop's teardown must
        # contain that exception (it never escapes into run_until_complete).
        _drain_one_burst(d, loop, [Path("a.md")])
        # Burst 2: must run cleanly, proving the daemon survived burst 1.
        _drain_one_burst(d, loop, [Path("b.md")])
    finally:
        loop.close()

    assert record["n"] == 2, "both bursts must have attempted a run"
    assert record["ok"] == [[Path("b.md")]], "second burst ran after first raised"


def test_shutdown_sets_stop_and_removes_pidfile(tmp_path):
    """Shutdown sets stop_event, removes pidfile, leaves no orphaned threads."""
    d = Daemon(tmp_path)
    d._write_pidfile()
    assert d._pidfile.exists()

    d._handle_signal()
    assert d._stop_event.is_set()

    d._remove_pidfile()
    assert not d._pidfile.exists()

    orphan_prefixes = ("watch-source", "vault-source")
    alive = [
        t.name
        for t in threading.enumerate()
        if t.name.startswith(orphan_prefixes)
    ]
    assert alive == [], f"orphaned poller threads: {alive}"


def test_final_trigger_before_shutdown_runs_exactly_once(tmp_path):
    """A trigger enqueued just before shutdown gets ONE final coalesced run.

    codex #1: on SIGTERM during the debounce window the daemon must drain the
    queued trigger and run exactly one final pipeline (paths included) instead
    of cancelling the work. We use a NON-zero debounce so the debounce task is
    still sleeping when stop_event trips — the final-drain path (not the normal
    debounce completion) is what must fire the run.
    """
    calls = []
    # Debounce long enough that it never elapses during the test; the final
    # drain must short-circuit it and run immediately.
    d = Daemon(tmp_path, debounce=30.0, queue_timeout=0.01, run_pipeline=calls.append)
    loop = _new_loop()
    d._loop = loop
    try:
        async def scenario():
            d._stop_event.clear()
            d._queue = asyncio.Queue()
            # Enqueue the trigger, then trip shutdown while the debounce sleeps.
            d._queue.put_nowait(
                TriggerEvent(source="t", changed_paths=[Path("late.md")])
            )

            async def stopper():
                # Let the drain loop pick up the event and start its debounce,
                # then request shutdown mid-debounce.
                for _ in range(5):
                    await asyncio.sleep(0)
                d._stop_event.set()

            st = asyncio.create_task(stopper())
            await d._drain_loop()
            await st

        loop.run_until_complete(scenario())
    finally:
        loop.close()

    assert len(calls) == 1, f"expected exactly 1 final run, got {len(calls)}"
    assert calls[0] == [Path("late.md")], "final run must include the queued path"


def test_final_drain_pipeline_exception_still_exits_clean(tmp_path):
    """A failing FINAL-drain run must not propagate — the daemon exits cleanly."""
    def boom(_paths):
        raise RuntimeError("final-drain boom")

    d = Daemon(tmp_path, debounce=30.0, queue_timeout=0.01, run_pipeline=boom)
    loop = _new_loop()
    d._loop = loop
    try:
        async def scenario():
            d._stop_event.clear()
            d._queue = asyncio.Queue()
            d._queue.put_nowait(TriggerEvent(source="t", changed_paths=[Path("z.md")]))

            async def stopper():
                for _ in range(5):
                    await asyncio.sleep(0)
                d._stop_event.set()

            st = asyncio.create_task(stopper())
            # Must NOT raise out of the drain loop.
            await d._drain_loop()
            await st

        loop.run_until_complete(scenario())
    finally:
        loop.close()


def test_stale_pidfile_is_overwritten(tmp_path, monkeypatch):
    """A pidfile whose PID is dead (ProcessLookupError) is overwritten."""
    d = Daemon(tmp_path)
    d._pidfile.parent.mkdir(parents=True, exist_ok=True)
    d._pidfile.write_text("9999999")

    def fake_kill(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", fake_kill)
    d._write_pidfile()  # must not raise
    assert d._pidfile.read_text().strip() == str(os.getpid())


def test_live_pidfile_refuses_start(tmp_path, monkeypatch):
    """A pidfile whose PID is alive (os.kill returns) causes a refusal."""
    d = Daemon(tmp_path)
    d._pidfile.parent.mkdir(parents=True, exist_ok=True)
    d._pidfile.write_text("4242")

    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    with pytest.raises(RuntimeError):
        d._write_pidfile()


def test_request_stop_ends_run_loop_from_another_thread(tmp_path):
    """The fleet supervisor stops units via request_stop(); the drain loop
    must notice within ~queue_timeout and run() must return 0."""
    (tmp_path / ".tesserae").mkdir(parents=True)
    daemon = Daemon(
        tmp_path,
        queue_timeout=0.05,
        enable_watch=False,
        enable_vault=False,
        enable_session_tail=False,
        install_signal_handlers=False,
        run_pipeline=lambda paths: None,
    )
    rc_box = {}
    thread = threading.Thread(target=lambda: rc_box.setdefault("rc", daemon.run()))
    thread.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and daemon._loop is None:
        time.sleep(0.01)
    assert daemon._loop is not None, "daemon loop did not start"
    daemon.request_stop()
    thread.join(timeout=5)
    assert not thread.is_alive(), "drain loop did not stop after request_stop()"
    assert rc_box["rc"] == 0
    assert not (tmp_path / ".tesserae" / "daemon.pid").exists()


def test_compile_gate_serializes_pipeline_runs(tmp_path):
    """Two daemons sharing one Semaphore(1) must never run pipelines
    concurrently — the fleet uses this to respect shared LLM rate limits."""
    gate = threading.Semaphore(1)
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()

    def slow_pipeline(paths):
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.2)
        with lock:
            state["active"] -= 1

    daemons = []
    for name in ("a", "b"):
        root = tmp_path / name
        (root / ".tesserae").mkdir(parents=True)
        daemons.append(
            Daemon(
                root,
                debounce=0.0,
                enable_watch=False,
                enable_vault=False,
                enable_session_tail=False,
                install_signal_handlers=False,
                compile_gate=gate,
                run_pipeline=slow_pipeline,
            )
        )
    threads = [threading.Thread(target=lambda d=d: d.run(once=True)) for d in daemons]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert all(not t.is_alive() for t in threads)
    assert state["max_active"] == 1, f"pipelines overlapped: {state['max_active']}"

"""Deterministic tests for the daemon's sleep-cycle consolidation thread.

The "sleep cycle" is idle-triggered + periodic agent-memory consolidation on
the always-on daemon (the brain-sleep analogy: knowledge is consolidated during
rest, not while work is in flight). Determinism comes from two constructor
seams — an injected monotonic clock (``monotonic=``) and an injected distill
callable (``distill=``) — so no test sleeps out a real idle window or drives a
real LLM. The trigger logic is exercised by calling ``_consolidation_tick()``
directly against a hand-advanced clock; thread lifecycle is exercised via
``_start_consolidation`` / ``run``.

Run with the project venv (NOT the shim)::

    .venv/bin/python -m pytest tests/test_engine_consolidation.py -q
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from tesserae.engine.daemon import Daemon, TriggerEvent


class FakeClock:
    """A hand-advanced stand-in for ``time.monotonic`` (seconds, float)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class RecordingDistill:
    """Stub for ``maybe_distill_on_refresh``: records calls, returns/raises."""

    def __init__(self, result=None, raises: BaseException | None = None) -> None:
        self.calls = []
        self.result = result if result is not None else {
            "distilled": [],
            "skipped": [],
            "failed": [],
        }
        self.raises = raises

    def __call__(self, project_root, graph, *, cfg=None, env=None):
        self.calls.append(
            {"project_root": project_root, "graph": graph, "cfg": cfg, "env": env}
        )
        if self.raises is not None:
            raise self.raises
        return self.result


def _make_project(tmp_path: Path) -> Path:
    """A minimal project with an empty compiled graph the daemon can load."""
    tdir = tmp_path / ".tesserae"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "graph.json").write_text(json.dumps({"nodes": [], "edges": []}))
    return tmp_path


# --------------------------------------------------------------------------- trigger logic


def test_idle_elapsed_triggers_consolidation_exactly_once(tmp_path):
    """After one idle window elapses, one tick fires ONE consolidation; a
    second tick (no further activity) must not re-fire (anti-thrash floor)."""
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    distill = RecordingDistill()
    d = Daemon(
        root,
        consolidate_idle_seconds=300.0,
        consolidate_max_interval_seconds=21600.0,
        monotonic=clock,
        distill=distill,
    )

    # Fresh construction stamps _last_activity == _last_consolidation == now:
    # nothing is idle yet, so the first tick is a no-op.
    d._consolidation_tick()
    assert distill.calls == [], "must not consolidate before the idle window"

    # Advance just past the idle window -> exactly one consolidation.
    clock.advance(301)
    d._consolidation_tick()
    assert len(distill.calls) == 1, "idle window elapsed -> one consolidation"

    # No further activity and no clock advance: the floor (measured from
    # _last_consolidation, just stamped) blocks an immediate second pass.
    d._consolidation_tick()
    assert len(distill.calls) == 1, "must not re-fire within one idle window"


def test_trigger_event_resets_idle_clock(tmp_path):
    """An enqueue() (a real trigger event) is activity: it resets _last_activity
    so the idle trigger does not fire until a fresh idle window elapses."""
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    distill = RecordingDistill()
    d = Daemon(
        root, consolidate_idle_seconds=300.0, monotonic=clock, distill=distill
    )

    # enqueue() needs a loop+queue to bridge onto; it updates _last_activity
    # synchronously (before the call_soon), which is what we assert.
    loop = asyncio.new_event_loop()
    d._loop = loop
    d._queue = asyncio.Queue()
    try:
        clock.advance(250)  # 250s of quiet, still inside the 300s window
        d.enqueue(TriggerEvent(source="watch", changed_paths=[Path("x.md")]))
        assert d._last_activity == 1250.0, "enqueue must stamp activity"

        clock.advance(299)  # only 299s since the reset -> still not idle
        d._consolidation_tick()
        assert distill.calls == [], "activity reset kept it from firing"

        clock.advance(2)  # now 301s since the last activity -> idle fires
        d._consolidation_tick()
        assert len(distill.calls) == 1, "a fresh idle window fires consolidation"
    finally:
        loop.close()


def test_max_interval_fires_under_continuous_activity(tmp_path):
    """The ceiling (consolidate_max_interval_seconds) fires even when the
    project never goes idle — periodic consolidation regardless of activity."""
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    distill = RecordingDistill()
    d = Daemon(
        root,
        consolidate_idle_seconds=300.0,
        consolidate_max_interval_seconds=3600.0,
        monotonic=clock,
        distill=distill,
    )

    # Simulate continuous activity: keep _last_activity glued to "now" so the
    # IDLE trigger can never fire. The CEILING must still fire once the max
    # interval since the last consolidation elapses.
    for _ in range(5):
        clock.advance(1000.0)          # < 3600 per step
        d._last_activity = clock()     # never idle
        d._consolidation_tick()

    assert len(distill.calls) == 1, "ceiling fired despite constant activity"


def test_ceiling_disabled_by_zero(tmp_path):
    """consolidate_max_interval_seconds == 0 disables the ceiling entirely."""
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    distill = RecordingDistill()
    d = Daemon(
        root,
        consolidate_idle_seconds=300.0,
        consolidate_max_interval_seconds=0.0,  # ceiling off
        monotonic=clock,
        distill=distill,
    )
    # Huge elapsed time but activity always fresh -> neither idle nor ceiling.
    for _ in range(5):
        clock.advance(100_000.0)
        d._last_activity = clock()
        d._consolidation_tick()
    assert distill.calls == [], "ceiling==0 must never fire on a busy project"


# --------------------------------------------------------------------------- gate + safety


def test_gate_off_consolidation_is_noop(tmp_path, monkeypatch):
    """With the distill gate off (default), the REAL maybe_distill_on_refresh
    returns skipped and writes NO artifacts — a safe no-op."""
    monkeypatch.delenv("TESSERAE_AGENT_DISTILL", raising=False)
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    # No distill stub -> exercises the real, triple-gated distill entry point.
    d = Daemon(root, consolidate_idle_seconds=300.0, monotonic=clock)

    clock.advance(301)
    d._consolidation_tick()  # fires the trigger, but distill is gated off

    tdir = root / ".tesserae"
    leftovers = sorted(p.name for p in tdir.iterdir() if p.name != "graph.json")
    assert leftovers == [], f"gate-off consolidation wrote artifacts: {leftovers}"


def test_consolidation_never_overlaps_a_pipeline_under_the_gate(tmp_path):
    """Consolidation runs UNDER the compile gate, so it can never overlap a
    pipeline run. Drive both concurrently through a shared Semaphore(1) and
    assert max concurrency across the two is exactly 1."""
    root = _make_project(tmp_path)
    gate = threading.Semaphore(1)
    state = {"active": 0, "max_active": 0}
    lock = threading.Lock()

    def _enter():
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])

    def _leave():
        with lock:
            state["active"] -= 1

    def slow_pipeline(paths):
        _enter()
        time.sleep(0.2)
        _leave()

    def slow_distill(project_root, graph, *, cfg=None, env=None):
        _enter()
        time.sleep(0.2)
        _leave()
        return {"skipped": []}

    clock = FakeClock(1000.0)
    d = Daemon(
        root,
        debounce=0.0,
        consolidate_idle_seconds=1000.0,       # idle path irrelevant here
        consolidate_max_interval_seconds=1.0,  # ceiling fires regardless of activity
        compile_gate=gate,
        run_pipeline=slow_pipeline,
        monotonic=clock,
        distill=slow_distill,
    )
    clock.advance(2)  # since_consolidation == 2 >= 1 -> ceiling due

    threads = [
        threading.Thread(target=lambda: d._run_pipeline([])),
        threading.Thread(target=d._consolidation_tick),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert all(not t.is_alive() for t in threads), "threads did not finish"
    assert state["max_active"] == 1, (
        f"pipeline and consolidation overlapped: {state['max_active']}"
    )


def test_consolidation_never_raises_when_distill_throws(tmp_path):
    """A distill that raises must be contained; the tick stamps
    _last_consolidation in its finally so a failing pass cannot hot-loop."""
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    distill = RecordingDistill(raises=RuntimeError("distill boom"))
    d = Daemon(
        root, consolidate_idle_seconds=300.0, monotonic=clock, distill=distill
    )

    clock.advance(301)
    before = d._last_consolidation
    d._consolidation_tick()  # must NOT raise

    assert len(distill.calls) == 1, "distill was attempted"
    assert d._last_consolidation != before, "_last_consolidation stamped in finally"


# --------------------------------------------------------------------------- thread lifecycle


def test_stop_event_exits_consolidation_thread(tmp_path):
    """The consolidation thread observes stop_event and exits promptly."""
    root = _make_project(tmp_path)
    distill = RecordingDistill()
    d = Daemon(
        root,
        consolidate_check_interval=0.05,
        consolidate_idle_seconds=10_000.0,     # never idle-fires in-test
        consolidate_max_interval_seconds=0.0,  # ceiling disabled
        distill=distill,
    )
    d._start_consolidation(None)  # loop arg is unused (parity with other sources)
    assert any(t.name == "consolidation" for t in d._threads)

    time.sleep(0.15)  # let it spin a few check intervals
    d._stop_event.set()
    for t in d._threads:
        t.join(timeout=2)

    assert all(not t.is_alive() for t in d._threads), "thread did not exit on stop"
    assert distill.calls == [], "must not consolidate when never due"


def test_run_starts_consolidation_thread_when_enabled(tmp_path):
    """run() (long-running) starts the consolidation thread; request_stop()
    drains it via run()'s existing finally join."""
    root = _make_project(tmp_path)
    distill = RecordingDistill()
    d = Daemon(
        root,
        queue_timeout=0.05,
        consolidate=True,
        consolidate_check_interval=0.05,
        consolidate_idle_seconds=10_000.0,
        consolidate_max_interval_seconds=0.0,
        enable_watch=False,
        enable_vault=False,
        enable_session_tail=False,
        install_signal_handlers=False,
        run_pipeline=lambda paths: None,
        distill=distill,
    )
    thread = threading.Thread(target=d.run)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not any(
            t.name == "consolidation" for t in d._threads
        ):
            time.sleep(0.01)
        assert any(
            t.name == "consolidation" for t in d._threads
        ), "run() did not start the consolidation thread"
    finally:
        d.request_stop()
        thread.join(timeout=5)

    assert not thread.is_alive(), "run() did not stop after request_stop()"
    assert not (root / ".tesserae" / "daemon.pid").exists()


def test_once_mode_never_consolidates(tmp_path):
    """run(once=True) is the poller-thread-free mode: no consolidation thread,
    no consolidation, even with consolidate=True."""
    root = _make_project(tmp_path)
    distill = RecordingDistill()
    d = Daemon(
        root,
        debounce=0.0,
        consolidate=True,
        enable_watch=False,
        enable_vault=False,
        enable_session_tail=False,
        install_signal_handlers=False,
        run_pipeline=lambda paths: None,
        distill=distill,
    )
    rc = d.run(once=True)

    assert rc == 0
    assert distill.calls == [], "once mode must not consolidate"
    assert not any(
        t.name == "consolidation" for t in d._threads
    ), "once mode must not start the consolidation thread"

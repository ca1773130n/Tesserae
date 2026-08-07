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

from tesserae.engine import pidlock
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
    assert pidlock.parse(d._pidfile.read_text())["pid"] == os.getpid()


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
    # Ask the daemon which file it owns: the name is host-scoped now, so a
    # literal "daemon.pid" here would pass without proving anything.
    assert not daemon._pidfile.exists()


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


def test_run_pipeline_logs_start(tmp_path, caplog):
    """A compile can run for hours; the daemon must announce it started."""
    import logging

    (tmp_path / ".tesserae").mkdir(parents=True)
    daemon = Daemon(
        tmp_path,
        debounce=0.0,
        enable_watch=False,
        enable_vault=False,
        enable_session_tail=False,
        install_signal_handlers=False,
        run_pipeline=lambda paths: None,
    )
    with caplog.at_level(logging.INFO, logger="tesserae.daemon"):
        daemon.run(once=True)
    assert any("pipeline starting" in r.getMessage() for r in caplog.records)


# ------------------------------------------------------- shared-disk fleet
#
# Several servers can run their own agent sessions against ONE shared project
# directory, so anything per-machine that lives in `.tesserae/` is written and
# read by all of them. These cover the daemon's share of that.


def test_pidfile_name_is_scoped_to_the_host(tmp_path, monkeypatch):
    """Two hosts sharing a project directory must not share one pidfile.

    Liveness is decided by `os.kill(pid, 0)` against the LOCAL process table,
    so a pid written by another machine is judged by whatever unrelated process
    holds that number here — the daemon then either refuses to start behind a
    stranger's pid or clobbers a live daemon's file.
    """
    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    a = Daemon(tmp_path)
    assert a._pidfile.name == "daemon.srv-a.pid"
    assert a._pidfile.parent == tmp_path / ".tesserae", "must stay in the project dir"

    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-b")
    b = Daemon(tmp_path)
    assert b._pidfile != a._pidfile

    # srv-a holding a LIVE pidfile must not block srv-b in the same directory.
    a._write_pidfile()
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    b._write_pidfile()  # must not raise
    assert a._pidfile.exists() and b._pidfile.exists()


def test_pidfile_falls_back_to_legacy_name_without_a_host_id(tmp_path, monkeypatch):
    """An unusable host id degrades to `daemon.pid` — today's single-host name."""
    import tesserae.harness_sessions as harness_sessions

    def _no_id():
        raise OSError("home is read-only")

    monkeypatch.setattr(harness_sessions, "local_host_id", _no_id)
    assert Daemon(tmp_path)._pidfile.name == Daemon.PIDFILE_NAME


def _tailer(root: Path, db):
    """A SessionTailer with no watch roots — construction only, no scanning."""
    from tesserae.engine.session_tail import SessionTailer

    return SessionTailer(
        project_root=root,
        sessions_db=db,
        on_new_turns=lambda path, turns: None,
        watch_roots=[],
    )


def test_session_scan_floor_is_scoped_to_the_host(tmp_path, monkeypatch):
    """Two hosts scanning their own transcript trees must not share one floor.

    ``harness_sessions.db`` sits in the shared `.tesserae/`, but each host
    enumerates only its own local transcripts. Under one floor the host that
    scanned last pushes it past date dirs the other has never read, and since
    the floor only moves forward those transcripts are never imported.

    (This belongs beside the tailer's other floor tests in
    tests/test_session_tailer.py; it lives here because that file is outside
    this change's ownership. Fold it in when they merge.)
    """
    from tesserae.engine.session_tail import SessionTailer
    from tesserae.harness_sessions_db import HarnessSessionsDB

    db = HarnessSessionsDB(tmp_path / ".tesserae" / "harness_sessions.db")

    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    _tailer(tmp_path, db)
    assert db.get_meta("codex_dir_floor:srv-a"), "srv-a wrote no floor of its own"
    assert db.get_meta("codex_dir_floor") is None, "the shared key must not be written"

    # srv-b has scanned nothing yet, so it must start cold rather than inherit
    # srv-a's floor and skip everything srv-a already walked past. The
    # constructor's enumerate() advances the floor to "now" immediately, so it
    # is stubbed out here to observe the value the store actually seeded.
    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-b")
    with monkeypatch.context() as m:
        m.setattr(SessionTailer, "_enumerate", lambda self: None)
        assert _tailer(tmp_path, db)._codex_dir_floor == 0.0, "srv-b inherited a floor"
    _tailer(tmp_path, db)
    assert db.get_meta("codex_dir_floor:srv-b")


def test_legacy_shared_scan_floor_seeds_this_host_and_is_left_alone(tmp_path, monkeypatch):
    """An existing single-host store must not redo its full cold sweep.

    The pre-fix, host-agnostic key is read once as a seed and then left in
    place — never rewritten, never deleted — because a second host sharing the
    store needs the same seed when it upgrades.
    """
    from tesserae.engine.session_tail import SessionTailer
    from tesserae.harness_sessions_db import HarnessSessionsDB

    db = HarnessSessionsDB(tmp_path / ".tesserae" / "harness_sessions.db")
    legacy = time.time()
    db.set_meta("codex_dir_floor", str(legacy))

    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    # enumerate() is stubbed for the seed observation only: it would advance
    # _codex_dir_floor to "now" before the assertion could see the seed.
    with monkeypatch.context() as m:
        m.setattr(SessionTailer, "_enumerate", lambda self: None)
        seeded = _tailer(tmp_path, db)._codex_dir_floor
    assert seeded == legacy - SessionTailer._FLOOR_LOOKBACK_S, "no warm start"

    _tailer(tmp_path, db)
    assert db.get_meta("codex_dir_floor") == str(legacy), "legacy seed must survive"
    assert db.get_meta("codex_dir_floor:srv-a"), "this host now owns its own floor"


# ------------------------------------------------------------ harvest-only


def test_harvest_only_daemon_tails_but_never_compiles(tmp_path):
    """enable_watch/vault off + session tail on = a harvester, not a compiler.

    This is the shape that takes N-1 servers off the shared compile lock: they
    tail their own transcripts into the sessions store and leave compiling to
    the one host that owns it. Reaching `ProjectWiki.load` at all would mean
    reaching the lock.
    """
    import tesserae.project as project_mod

    d = Daemon(
        tmp_path,
        enable_watch=False,
        enable_vault=False,
        enable_session_tail=True,
        install_signal_handlers=False,
    )
    assert d._enable_compile is False

    started = []
    d._start_watch_source = lambda loop: started.append("watch")
    d._start_vault_source = lambda loop: started.append("vault")
    d._start_session_source = lambda loop: started.append("session")
    d._start_sources(None)
    assert started == ["session"], "the harvester must still tail transcripts"

    loaded = []
    original = project_mod.ProjectWiki
    # The daemon imports ProjectWiki inside _run_pipeline, so patch the module
    # attr; any load at all means the compile lock was about to be taken.
    project_mod.ProjectWiki = type(
        "_TripwireWiki",
        (),
        {"load": classmethod(lambda cls, root: loaded.append(root))},
    )
    try:
        assert d._run_pipeline([Path("a.md")]) is True
    finally:
        project_mod.ProjectWiki = original
    assert loaded == [], "harvest-only daemon reached the compile path"


def test_watch_enabled_daemon_still_compiles(tmp_path):
    """The default (single-machine, no config) daemon is unchanged: it compiles."""
    assert Daemon(tmp_path)._enable_compile is True
    assert Daemon(tmp_path, enable_watch=False)._enable_compile is True  # vault on
    # An explicit flag overrides the inference in both directions.
    assert Daemon(tmp_path, enable_compile=True, enable_watch=False,
                  enable_vault=False)._enable_compile is True
    assert Daemon(tmp_path, enable_compile=False)._enable_compile is False


# -------------------------------------------------- compile-lock deferral


class _Clock:
    """Hand-advanced stand-in for ``time.monotonic`` (seconds, float)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t


def _patch_wiki(compile_fn):
    """Install a ProjectWiki whose compile() runs ``compile_fn``; return undo."""
    import tesserae.project as project_mod

    original = project_mod.ProjectWiki

    class _Wiki:
        @classmethod
        def load(cls, root):  # noqa: ANN001
            return cls()

        def compile(self, **kwargs):  # noqa: ANN003
            return compile_fn(**kwargs)

    project_mod.ProjectWiki = _Wiki  # type: ignore[assignment]

    def undo():
        project_mod.ProjectWiki = original  # type: ignore[assignment]

    return undo


def test_compile_lock_held_defers_with_capped_exponential_backoff(tmp_path, caplog):
    """A held compile lock defers the batch; the retry must not become a spin.

    _drain_loop reschedules through _debounce_and_run, which sleeps only
    `debounce` (1.0s by default), so without a backoff every host would redo
    ProjectWiki.load + a config read + a flock syscall once a second for the
    whole duration of a human's multi-minute interactive compile.
    """
    import logging

    from tesserae.locking import CompileLockHeldError

    def _held(**_kwargs):
        raise CompileLockHeldError("held by pid 4711 on srv-b")

    clock = _Clock()
    d = Daemon(tmp_path, monotonic=clock)
    undo = _patch_wiki(_held)
    try:
        with caplog.at_level(logging.INFO, logger="tesserae.daemon"):
            assert d._run_pipeline([Path("a.md")]) is False, "must report DEFERRED"
            assert d._defer_delay == Daemon.DEFER_BACKOFF_START
            assert d._defer_until == clock.t + Daemon.DEFER_BACKOFF_START

            assert d._run_pipeline([Path("a.md")]) is False
            assert d._defer_delay == 2 * Daemon.DEFER_BACKOFF_START

            for _ in range(10):
                d._run_pipeline([Path("a.md")])
            assert d._defer_delay == Daemon.DEFER_BACKOFF_MAX, "backoff must be capped"
    finally:
        undo()

    # Another process holding the lock is normal traffic, not a fault.
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert any("deferring" in r.getMessage() for r in caplog.records)

    # A run that gets the lock clears the ladder, so the next contended run
    # does not inherit a minute-long wait earned hours ago.
    undo = _patch_wiki(lambda **_kwargs: {})
    try:
        assert d._run_pipeline([Path("a.md")]) is True
    finally:
        undo()
    assert d._defer_delay == 0.0
    assert d._defer_until == 0.0


def test_deferred_batch_is_retried_not_dropped(tmp_path):
    """A deferral re-queues the paths; only a run that happened consumes them."""
    attempts = []
    d = Daemon(tmp_path, debounce=0.0)

    def fake_run(paths):
        attempts.append(list(paths))
        return len(attempts) > 1  # first attempt defers, second runs

    d._run_pipeline = fake_run
    consumed = []
    loop = _new_loop()
    try:
        loop.run_until_complete(
            d._debounce_and_run([Path("a.md")], on_consumed=lambda: consumed.append(1))
        )
    finally:
        loop.close()

    assert attempts == [[Path("a.md")], [Path("a.md")]], "the batch must be retried"
    assert consumed == [1], "paths must stay pending until a run actually happened"


def test_once_mode_does_not_wait_out_a_deferral(tmp_path):
    """`--once` asked for one bounded pass: report the contention and exit.

    Blocking a one-shot invocation behind a human's multi-minute compile is
    worse than returning — the batch is only delayed, since the next compile's
    manifest differ still sees the same files.
    """
    d = Daemon(tmp_path, debounce=0.0, install_signal_handlers=False)
    d._defer_until = time.monotonic() + 3600.0  # a deferral already in flight
    d._run_pipeline = lambda paths: False  # and this attempt defers too

    # Run it on a daemon thread and join with a deadline rather than calling
    # run() inline: if the guard regresses, once-mode sleeps out the hour-long
    # deferral, and an inline call would hang the whole suite forever instead
    # of failing (there is no pytest-timeout plugin here to cut it short).
    rc_box = {}
    thread = threading.Thread(
        target=lambda: rc_box.setdefault("rc", d.run(once=True)), daemon=True
    )
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "once mode waited out the backoff"
    assert rc_box["rc"] == 0


def test_raise_fd_limit_returns_nondecreasing_soft_limit():
    import resource

    from tesserae.engine.daemon import raise_fd_limit

    before_soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    result = raise_fd_limit(target=before_soft)  # never lowers; idempotent at current
    assert result >= before_soft

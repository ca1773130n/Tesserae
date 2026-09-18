"""FleetDaemon: one process supervising a per-project Daemon per registry entry."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from tesserae.engine.fleet import FleetDaemon


def _write_registry(path: Path, projects: dict[str, Path]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "active": None,
                "projects": {
                    name: {"root": str(root), "graph_path": str(root / ".tesserae" / "graph.json")}
                    for name, root in projects.items()
                },
            }
        ),
        encoding="utf-8",
    )


def _make_project(base: Path, name: str) -> Path:
    root = base / name
    (root / ".tesserae").mkdir(parents=True)
    return root


def test_fleet_once_runs_one_pipeline_per_registered_project(tmp_path):
    registry = tmp_path / "registry.json"
    roots = {name: _make_project(tmp_path, name) for name in ("alpha", "beta")}
    _write_registry(registry, roots)

    ran: list[str] = []

    def factory(name, root, fleet):
        from tesserae.engine.daemon import Daemon

        return Daemon(
            root,
            enable_watch=False,
            enable_vault=False,
            enable_session_tail=False,
            install_signal_handlers=False,
            compile_gate=fleet.compile_gate,
            run_pipeline=lambda paths, name=name: ran.append(name),
        )

    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=factory,
    )
    rc = fleet.run(once=True)

    assert rc == 0
    assert sorted(ran) == ["alpha", "beta"]
    assert not (tmp_path / "engine.pid").exists()  # released on exit


def test_fleet_skips_registered_projects_missing_on_disk(tmp_path):
    registry = tmp_path / "registry.json"
    alive = _make_project(tmp_path, "alive")
    ghost = tmp_path / "ghost"  # registered but no .tesserae on disk
    _write_registry(registry, {"alive": alive, "ghost": ghost})

    ran: list[str] = []

    def factory(name, root, fleet):
        from tesserae.engine.daemon import Daemon

        return Daemon(
            root,
            enable_watch=False,
            enable_vault=False,
            enable_session_tail=False,
            install_signal_handlers=False,
            compile_gate=fleet.compile_gate,
            run_pipeline=lambda paths, name=name: ran.append(name),
        )

    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=factory,
    )
    assert fleet.run(once=True) == 0
    assert ran == ["alive"]


def _recording_factory(ran_units: dict):
    """Factory building real Daemons with stub pipelines that record liveness."""

    def factory(name, root, fleet):
        from tesserae.engine.daemon import Daemon

        ran_units[name] = ran_units.get(name, 0) + 1
        return Daemon(
            root,
            queue_timeout=0.05,
            enable_watch=False,
            enable_vault=False,
            enable_session_tail=False,
            install_signal_handlers=False,
            compile_gate=fleet.compile_gate,
            run_pipeline=lambda paths: None,
        )

    return factory


def test_reconcile_starts_new_and_stops_removed_units(tmp_path):
    registry = tmp_path / "registry.json"
    alpha = _make_project(tmp_path, "alpha")
    _write_registry(registry, {"alpha": alpha})

    built: dict = {}
    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=_recording_factory(built),
    )
    try:
        fleet.reconcile()
        assert set(fleet._units) == {"alpha"}
        # Fix 1: bounded poll so a freshly-started thread has time to be scheduled.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not fleet._units["alpha"].thread.is_alive():
            time.sleep(0.005)
        assert fleet._units["alpha"].thread.is_alive()
        # Fix 3: assert factory was called exactly once for "alpha".
        assert built == {"alpha": 1}

        # Register beta, drop alpha → reconcile converges.
        beta = _make_project(tmp_path, "beta")
        _write_registry(registry, {"beta": beta})
        fleet.reconcile()
        assert set(fleet._units) == {"beta"}
        # Fix 3: assert factory was called once for "beta" (alpha count unchanged).
        assert built == {"alpha": 1, "beta": 1}
    finally:
        # Fix 2: always stop unit threads so they never leak on assertion failure.
        for name in list(fleet._units):
            fleet._stop_unit(name)
    assert fleet._units == {}


def test_reconcile_restarts_unit_when_registry_root_changes(tmp_path):
    """Same registry name pointing at a new root must restart the unit on the
    new root — a name-only diff would leave the old daemon running forever."""
    registry = tmp_path / "registry.json"
    old_root = _make_project(tmp_path, "alpha-old")
    new_root = _make_project(tmp_path, "alpha-new")
    _write_registry(registry, {"alpha": old_root})

    built: dict = {}
    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=_recording_factory(built),
    )
    try:
        fleet.reconcile()
        assert fleet._units["alpha"].root == old_root.resolve()
        assert built == {"alpha": 1}

        _write_registry(registry, {"alpha": new_root})
        fleet.reconcile()
        assert fleet._units["alpha"].root == new_root.resolve()
        # A fresh daemon was built for the new root.
        assert built == {"alpha": 2}
    finally:
        for name in list(fleet._units):
            fleet._stop_unit(name)
    assert fleet._units == {}


def test_run_loop_stops_all_units_on_request_stop(tmp_path):
    registry = tmp_path / "registry.json"
    alpha = _make_project(tmp_path, "alpha")
    _write_registry(registry, {"alpha": alpha})

    built: dict = {}
    fleet = FleetDaemon(
        registry_path=registry,
        registry_poll=0.1,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=_recording_factory(built),
    )
    rc_box = {}
    runner = threading.Thread(target=lambda: rc_box.setdefault("rc", fleet.run()))
    runner.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "alpha" not in fleet._units:
            time.sleep(0.05)
        assert "alpha" in fleet._units
        # Fix 3: assert factory was called exactly once for "alpha".
        assert built == {"alpha": 1}
    finally:
        # Unit threads are non-daemon: stop the fleet even on assertion
        # failure or pytest would hang at interpreter exit.
        fleet.request_stop()
        runner.join(timeout=10)
    assert not runner.is_alive()
    assert rc_box["rc"] == 0
    assert fleet._units == {}
    assert not (tmp_path / "engine.pid").exists()


def test_run_cleans_up_units_when_reconcile_raises(tmp_path, monkeypatch):
    """A reconcile() crash mid-loop must still stop unit threads and release
    the pidfile — non-daemon units would otherwise outlive the fleet."""
    registry = tmp_path / "registry.json"
    alpha = _make_project(tmp_path, "alpha")
    _write_registry(registry, {"alpha": alpha})

    built: dict = {}
    fleet = FleetDaemon(
        registry_path=registry,
        registry_poll=0.05,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=_recording_factory(built),
    )

    calls = {"n": 0}
    real_reconcile = fleet.reconcile

    def flaky_reconcile():
        calls["n"] += 1
        real_reconcile()
        if calls["n"] >= 2:
            raise RuntimeError("registry exploded")

    monkeypatch.setattr(fleet, "reconcile", flaky_reconcile)

    result: dict = {}

    def _run():
        try:
            fleet.run()
        except RuntimeError as exc:
            result["exc"] = exc

    runner = threading.Thread(target=_run)
    runner.start()
    runner.join(timeout=10)
    assert not runner.is_alive()
    assert isinstance(result.get("exc"), RuntimeError)
    assert fleet._units == {}, "unit threads leaked past a crashing run loop"
    assert not (tmp_path / "engine.pid").exists()


def test_malformed_registry_entries_and_schema_survive(tmp_path):
    """One bad entry is skipped (others still run); a whole-registry shape
    error keeps the current unit set instead of crashing the fleet."""
    registry = tmp_path / "registry.json"
    alpha = _make_project(tmp_path, "alpha")
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "active": None,
                "projects": {
                    "bad": "not-a-dict",
                    "alpha": {"root": str(alpha), "graph_path": str(alpha / ".tesserae" / "graph.json")},
                },
            }
        ),
        encoding="utf-8",
    )

    built: dict = {}
    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=_recording_factory(built),
    )
    try:
        fleet.reconcile()
        assert set(fleet._units) == {"alpha"}

        # "projects" with the wrong shape entirely → keep running units.
        registry.write_text(
            json.dumps({"version": 1, "active": None, "projects": ["alpha"]}),
            encoding="utf-8",
        )
        fleet.reconcile()
        assert set(fleet._units) == {"alpha"}

        # Falsey non-dict values must be malformed too, not "no projects" —
        # `or {}` coercion would silently stop every running unit.
        for bad in (None, [], "", False, 0):
            registry.write_text(
                json.dumps({"version": 1, "active": None, "projects": bad}),
                encoding="utf-8",
            )
            fleet.reconcile()
            assert set(fleet._units) == {"alpha"}, f"units lost on projects={bad!r}"
    finally:
        for name in list(fleet._units):
            fleet._stop_unit(name)
    assert fleet._units == {}


def test_stale_pidfile_is_reclaimed(tmp_path):
    import subprocess
    import sys

    # A pid that is guaranteed dead: a child that already exited.
    proc = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    dead_pid = int(proc.stdout.strip())

    registry = tmp_path / "registry.json"
    _write_registry(registry, {})
    pidfile = tmp_path / "engine.pid"
    pidfile.write_text(str(dead_pid))

    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=pidfile,
        daemon_factory=_recording_factory({}),
    )
    assert fleet.run(once=True) == 0  # stale pidfile reclaimed, run, released
    assert not pidfile.exists()


def test_stale_reclaim_refuses_without_flock_backend(tmp_path, monkeypatch):
    """On platforms without fcntl the unlink+retry reclaim is unserialized —
    the fleet must refuse with a clear manual-removal error instead."""
    import subprocess
    import sys

    import pytest

    from tesserae.engine import fleet as fleet_mod

    proc = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True,
        text=True,
        check=True,
    )
    dead_pid = int(proc.stdout.strip())

    registry = tmp_path / "registry.json"
    _write_registry(registry, {})
    pidfile = tmp_path / "engine.pid"
    pidfile.write_text(str(dead_pid))

    monkeypatch.setattr(fleet_mod, "fcntl", None)
    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=pidfile,
        daemon_factory=_recording_factory({}),
    )
    with pytest.raises(RuntimeError, match="remove the file manually"):
        fleet.run(once=True)
    assert pidfile.exists(), "must not touch the stale pidfile without a lock backend"


def test_remove_pidfile_only_when_owned(tmp_path):
    """A fleet must not unlink a pidfile that a newer process re-created."""
    registry = tmp_path / "registry.json"
    _write_registry(registry, {})
    pidfile = tmp_path / "engine.pid"
    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=pidfile,
        daemon_factory=_recording_factory({}),
    )
    pidfile.write_text("999999999")  # someone else's pidfile
    fleet._remove_pidfile()
    assert pidfile.exists(), "removed a pidfile owned by another process"
    pidfile.write_text(str(os.getpid()))
    fleet._remove_pidfile()
    assert not pidfile.exists()


def test_default_units_never_bind_a_port(tmp_path):
    """N units racing one port is the failure this pins shut.

    Fleet mode has no serve knob on purpose: `_default_daemon_factory` must
    build units with the serve source OFF, whatever the Daemon default becomes.
    """
    registry = tmp_path / "registry.json"
    root = _make_project(tmp_path, "alpha")
    _write_registry(registry, {"alpha": root})
    fleet = FleetDaemon(registry_path=registry, pidfile=tmp_path / "engine.pid")

    unit = fleet._default_daemon_factory("alpha", root, fleet)

    assert unit._enable_serve is False


def test_a_fleet_unit_whose_pipeline_failed_is_not_counted_ok(tmp_path, caplog):
    """The fleet reads each unit's rc, so "2/2 units ok" was a lie downstream.

    Nothing was wrong with the counting — `Daemon.run(once=True)` answered 0
    for a failed compile, so every unit looked fine.
    """
    import logging

    registry = tmp_path / "registry.json"
    roots = {name: _make_project(tmp_path, name) for name in ("alpha", "beta")}
    _write_registry(registry, roots)

    def factory(name, root, fleet):
        from tesserae.engine.daemon import Daemon

        d = Daemon(
            root, debounce=0.0, enable_watch=False, enable_vault=False,
            enable_session_tail=False, install_signal_handlers=False,
            compile_gate=fleet.compile_gate,
        )

        def pipeline(paths, _name=name, _d=d):
            if _name == "beta":
                _d._pipeline_failed = True  # what a failed step does
            return True

        d._run_pipeline = pipeline
        return d

    fleet = FleetDaemon(
        registry_path=registry, pidfile=tmp_path / "engine.pid", daemon_factory=factory
    )
    with caplog.at_level(logging.ERROR):
        rc = fleet.run(once=True)

    assert rc == 1, "a fleet with a failed unit must not exit 0"
    assert any("beta" in r.getMessage() for r in caplog.records)


def test_harvest_only_reaches_every_fleet_unit(tmp_path):
    """`engine --all --harvest-only` compiled every project — the one thing the
    flag promises never to do. The fleet dropped the flag on the floor.

    A fleet is the shape this flag was written for: N hosts sharing a project
    directory, each tailing what only it can see, one host compiling.
    """
    registry = tmp_path / "registry.json"
    root = _make_project(tmp_path, "alpha")
    _write_registry(registry, {"alpha": root})

    fleet = FleetDaemon(
        registry_path=registry, pidfile=tmp_path / "engine.pid", harvest_only=True
    )
    unit = fleet._default_daemon_factory("alpha", root, fleet)
    assert unit._enable_compile is False
    assert unit._enable_watch is False
    assert unit._enable_vault is False
    assert unit._enable_session_tail is True, "a harvester must still tail sessions"
    assert unit._consolidate is False, "a harvester did not ask for LLM spend"

    plain = FleetDaemon(registry_path=registry, pidfile=tmp_path / "e2.pid")
    assert plain._default_daemon_factory("alpha", root, plain)._enable_compile is True


def test_stop_asks_every_unit_before_waiting_on_any(tmp_path):
    """A unit still finishing its compile must not keep the other units running.

    Units were stopped one at a time: while the first finished its compile,
    every other unit kept watching, and could start a compile after the fleet
    had been told to stop.
    """
    registry = tmp_path / "registry.json"
    _write_registry(registry, {name: _make_project(tmp_path, name) for name in ("alpha", "beta")})
    units: dict = {}
    seen: dict = {}

    class _Unit:
        def __init__(self, name):
            self.name = name
            self.stopped = threading.Event()

        def request_stop(self):
            self.stopped.set()

        def run(self):
            self.stopped.wait()
            if self.name == "alpha":
                # Still finishing work: waits on beta to be told as well.
                seen["beta_asked"] = units["beta"].stopped.wait(timeout=5)

    def factory(name, root, fleet):
        units[name] = _Unit(name)
        return units[name]

    fleet = FleetDaemon(
        registry_path=registry,
        registry_poll=0.05,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=factory,
    )
    runner = threading.Thread(target=fleet.run)
    runner.start()
    try:
        deadline = time.monotonic() + 5
        while len(fleet._units) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert sorted(fleet._units) == ["alpha", "beta"]
    finally:
        fleet.request_stop()
        runner.join(timeout=15)
    assert not runner.is_alive()
    assert seen["beta_asked"], "beta kept running until alpha had finished"


def test_stop_now_exits_even_when_a_unit_pidfile_cannot_be_removed(tmp_path, monkeypatch):
    """A failed cleanup step must not cancel the exit.

    The pidfile removal ran inside the signal handler with nothing around it,
    so an unlink error (a read-only or stale shared disk) escaped before the
    agents were killed and before the exit: the fleet went back to waiting
    out the unit's compile.
    """
    import signal

    import pytest

    import tesserae.engine.fleet as fleet_mod
    from tesserae.engine import pidlock

    class _Exited(BaseException):
        pass

    exits: list = []
    stopped: list = []

    def fake_exit(code):
        exits.append(code)
        raise _Exited

    monkeypatch.setattr(fleet_mod.os, "_exit", fake_exit)
    monkeypatch.setattr(fleet_mod, "stop_cli_agents", lambda: stopped.append(True))

    class _Daemon:
        _pidfile = tmp_path / "unit.pid"

        def _remove_pidfile(self):
            raise PermissionError("read-only share")

    _Daemon._pidfile.write_text(pidlock.serialize())
    fleet = FleetDaemon(registry_path=tmp_path / "registry.json", pidfile=tmp_path / "engine.pid")
    fleet._write_pidfile()
    fleet._units["alpha"] = fleet_mod._Unit(name="alpha", root=tmp_path, daemon=_Daemon())

    with pytest.raises(_Exited):
        fleet._stop_now(signal.SIGTERM)
    assert exits == [128 + signal.SIGTERM]
    assert stopped == [True]
    assert not (tmp_path / "engine.pid").exists()

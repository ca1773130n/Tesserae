"""FleetDaemon: one process supervising a per-project Daemon per registry entry."""
from __future__ import annotations

import json
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
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "alpha" not in fleet._units:
        time.sleep(0.05)
    assert "alpha" in fleet._units
    # Fix 3: assert factory was called exactly once for "alpha".
    assert built == {"alpha": 1}

    fleet.request_stop()
    runner.join(timeout=10)
    assert not runner.is_alive()
    assert rc_box["rc"] == 0
    assert fleet._units == {}
    assert not (tmp_path / "engine.pid").exists()

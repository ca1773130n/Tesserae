"""FleetDaemon threads the sleep-cycle knobs (consolidate_* + the SUMMARIZE
``summarize_budget``) down to each unit Daemon.

'tesserae engine --all' must consolidate every registered project on idle, so
the fleet's constructor knobs have to reach the per-unit Daemon built by the
default factory. These tests intercept the real Daemon construction the default
factory performs and assert the consolidation config arrives intact.
"""
from __future__ import annotations

import json
from pathlib import Path

from tesserae.engine import fleet as fleet_mod
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


def _capture_daemon_kwargs(monkeypatch) -> list[dict]:
    """Patch fleet's Daemon so the default factory records its kwargs.

    Returns a StubDaemon (never run) that only records how it was constructed;
    the default factory is what wires the fleet knobs into these kwargs.
    """
    captured: list[dict] = []

    class StubDaemon:
        def __init__(self, root, **kwargs):
            captured.append({"root": root, **kwargs})

    monkeypatch.setattr(fleet_mod, "Daemon", StubDaemon)
    return captured


def test_default_factory_threads_default_consolidate_knobs(tmp_path, monkeypatch):
    captured = _capture_daemon_kwargs(monkeypatch)
    root = _make_project(tmp_path, "alpha")

    fleet = FleetDaemon(
        registry_path=tmp_path / "registry.json",
        pidfile=tmp_path / "engine.pid",
    )
    fleet._default_daemon_factory("alpha", root, fleet)

    assert len(captured) == 1
    kw = captured[0]
    assert kw["consolidate"] is True
    assert kw["consolidate_idle_seconds"] == 300.0
    assert kw["consolidate_max_interval_seconds"] == 21600.0
    assert kw["consolidate_check_interval"] == 30.0
    assert kw["summarize_budget"] == 25


def test_default_factory_threads_custom_consolidate_knobs(tmp_path, monkeypatch):
    captured = _capture_daemon_kwargs(monkeypatch)
    root = _make_project(tmp_path, "alpha")

    fleet = FleetDaemon(
        registry_path=tmp_path / "registry.json",
        pidfile=tmp_path / "engine.pid",
        consolidate=False,
        consolidate_idle_seconds=17.0,
        consolidate_max_interval_seconds=0.0,
        consolidate_check_interval=5.0,
        summarize_budget=4,
    )
    fleet._default_daemon_factory("alpha", root, fleet)

    assert len(captured) == 1
    kw = captured[0]
    assert kw["consolidate"] is False
    assert kw["consolidate_idle_seconds"] == 17.0
    assert kw["consolidate_max_interval_seconds"] == 0.0
    assert kw["consolidate_check_interval"] == 5.0
    assert kw["summarize_budget"] == 4


def test_reconcile_builds_units_carrying_custom_consolidate_config(tmp_path, monkeypatch):
    """End-to-end through reconcile(): every registered project's unit Daemon is
    constructed with the fleet's consolidation config."""
    captured = _capture_daemon_kwargs(monkeypatch)
    registry = tmp_path / "registry.json"
    roots = {name: _make_project(tmp_path, name) for name in ("alpha", "beta")}
    _write_registry(registry, roots)

    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        consolidate=True,
        consolidate_idle_seconds=42.0,
        consolidate_max_interval_seconds=99.0,
        consolidate_check_interval=7.0,
    )
    try:
        fleet.reconcile()
        assert set(fleet._units) == {"alpha", "beta"}
        assert len(captured) == 2
        for kw in captured:
            assert kw["consolidate"] is True
            assert kw["consolidate_idle_seconds"] == 42.0
            assert kw["consolidate_max_interval_seconds"] == 99.0
            assert kw["consolidate_check_interval"] == 7.0
    finally:
        # StubDaemon has no thread; drop units directly without join.
        fleet._units.clear()


def test_fleet_defaults_match_daemon_defaults():
    """The fleet's consolidate defaults must equal the Daemon's own defaults so
    'engine --all' behaves identically to a single 'engine' per project."""
    import inspect

    from tesserae.engine.daemon import Daemon

    daemon_params = inspect.signature(Daemon.__init__).parameters
    fleet_params = inspect.signature(FleetDaemon.__init__).parameters
    for knob in (
        "consolidate",
        "consolidate_idle_seconds",
        "consolidate_max_interval_seconds",
        "consolidate_check_interval",
        "summarize_budget",
    ):
        assert fleet_params[knob].default == daemon_params[knob].default

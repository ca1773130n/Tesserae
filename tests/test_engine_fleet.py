"""FleetDaemon: one process supervising a per-project Daemon per registry entry."""
from __future__ import annotations

import json
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
            run_pipeline=lambda paths, name=name: ran.append(name),
        )

    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=factory,
    )
    assert fleet.run(once=True) == 0
    assert ran == ["alive"]

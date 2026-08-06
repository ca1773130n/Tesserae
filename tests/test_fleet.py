"""Per-project failure isolation for ``FleetDaemon.run(once=True)``.

``tests/test_engine_fleet.py`` owns the fleet's reconciliation, threading and
pidfile behaviour. This file covers only the once-mode batch contract: every
registered project gets its run even when an earlier one blows up, and the
exit code tells the truth about whether they all succeeded.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from tesserae.engine.fleet import FleetDaemon


def _write_registry(path: Path, projects: "dict[str, Path]") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "active": None,
                "projects": {
                    name: {
                        "root": str(root),
                        "graph_path": str(root / ".tesserae" / "graph.json"),
                    }
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


class _RecordingDaemon:
    """Stand-in unit: records that it ran, or raises on demand."""

    def __init__(self, name: str, ran: "list[str]", boom: bool) -> None:
        self.name = name
        self._ran = ran
        self._boom = boom

    def run(self, *, once: bool = False) -> int:
        self._ran.append(self.name)
        if self._boom:
            raise RuntimeError(f"{self.name} is wedged")
        return 0


def test_once_mode_runs_every_unit_even_when_one_raises(tmp_path, caplog):
    """A project that blows up must not cost every project after it its run.

    Registry order is alphabetical, so "beta" failing used to abort the loop
    before "gamma" ever started — and the fleet still returned 0, which is the
    part that makes a batch run untrustworthy.
    """
    registry = tmp_path / "registry.json"
    roots = {name: _make_project(tmp_path, name) for name in ("alpha", "beta", "gamma")}
    _write_registry(registry, roots)

    ran: "list[str]" = []

    def factory(name, root, fleet):
        return _RecordingDaemon(name, ran, boom=(name == "beta"))

    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=factory,
    )
    with caplog.at_level(logging.INFO, logger="tesserae.fleet"):
        rc = fleet.run(once=True)

    assert ran == ["alpha", "beta", "gamma"], "every unit must get its run"
    assert rc == 1, "a failed unit must be visible in the exit code"
    summary = [r.getMessage() for r in caplog.records if "fleet once-run" in r.getMessage()]
    assert summary and "beta" in summary[0], f"no summary naming the casualty: {summary}"
    assert not (tmp_path / "engine.pid").exists()  # released on exit


def test_once_mode_returns_zero_when_every_unit_succeeds(tmp_path):
    """The all-green path keeps returning 0 — the isolation adds no false alarm."""
    registry = tmp_path / "registry.json"
    roots = {name: _make_project(tmp_path, name) for name in ("alpha", "beta")}
    _write_registry(registry, roots)

    ran: "list[str]" = []
    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=lambda name, root, f: _RecordingDaemon(name, ran, boom=False),
    )
    assert fleet.run(once=True) == 0
    assert ran == ["alpha", "beta"]


def test_once_mode_reports_a_nonzero_unit_return_code(tmp_path):
    """A unit that returns non-zero counts as failed, same as one that raises."""
    registry = tmp_path / "registry.json"
    _write_registry(registry, {"alpha": _make_project(tmp_path, "alpha")})

    class _Rc2:
        def run(self, *, once: bool = False) -> int:
            return 2

    fleet = FleetDaemon(
        registry_path=registry,
        pidfile=tmp_path / "engine.pid",
        daemon_factory=lambda name, root, f: _Rc2(),
    )
    assert fleet.run(once=True) == 1

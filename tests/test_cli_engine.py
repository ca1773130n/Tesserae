"""Deterministic CLI tests for the `engine` / `daemon` command (Plan 02-03).

These tests never start a long-running process: every path uses `--once`
(single drain) with an injected/spied pipeline, so there are no poller
threads, no signal handlers, and no wall-clock sleeps.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import tesserae.cli as cli
import tesserae.engine.daemon as daemon_mod
from tesserae.cli import _handle_engine
from tesserae.cli_tree import moved_replacement


def test_engine_routes_to_handler_and_daemon_redirects_to_engine():
    """`engine` dispatches to _handle_engine; the old `daemon` alias now
    redirects to `tesserae engine` (redesign task 7 removed the _COMMANDS alias).
    """
    # `engine` is a first-class verb in the new dispatch table, routed via
    # _route_engine, which calls _handle_engine.
    assert "engine" in cli._NEW_DISPATCH
    assert cli._NEW_DISPATCH["engine"] is cli._route_engine
    # The router resolves to the engine handler (resolved at call time).
    import inspect

    assert "_handle_engine" in inspect.getsource(cli._route_engine)
    assert cli._handle_engine is _handle_engine
    # The legacy `daemon` alias is gone as a top-level dispatch key.
    assert "daemon" not in cli._NEW_DISPATCH
    # The old `project daemon` subcommand is redirected to `tesserae engine`
    # by the moved-command table instead.
    moved = moved_replacement(["project", "daemon"])
    assert moved is not None
    assert moved[1] == "tesserae engine"


def test_engine_once_runs_single_drain_exit_zero(tmp_path, monkeypatch):
    """`engine --once` constructs a Daemon and drives exactly ONE drain, exit 0.

    The real pipeline is replaced by a recording spy via `Daemon`'s
    `run_pipeline=` seam, so no real compile/project work runs. debounce=0
    keeps the single drain instant.
    """
    (tmp_path / ".tesserae").mkdir(parents=True, exist_ok=True)

    calls: list = []
    real_daemon_cls = daemon_mod.Daemon

    def daemon_factory(project_root, **kwargs):
        # Force the recording seam regardless of handler kwargs.
        kwargs["run_pipeline"] = lambda paths: calls.append(list(paths))
        # debounce=0 -> instant drain.
        kwargs["debounce"] = 0
        return real_daemon_cls(project_root, **kwargs)

    monkeypatch.setattr("tesserae.engine.daemon.Daemon", daemon_factory)

    rc = cli.main(
        ["engine", "--once", "--project", str(tmp_path), "--debounce", "0"]
    )

    assert rc == 0
    assert len(calls) == 1  # exactly one drain/pipeline cycle


def test_daemon_alias_once_runs_single_drain_exit_zero(tmp_path, monkeypatch):
    """The old `daemon` alias is gone (redesign); `tesserae engine` is the
    single entry point and still runs exactly one drain cycle on --once."""
    (tmp_path / ".tesserae").mkdir(parents=True, exist_ok=True)

    calls: list = []
    real_daemon_cls = daemon_mod.Daemon

    def daemon_factory(project_root, **kwargs):
        kwargs["run_pipeline"] = lambda paths: calls.append(list(paths))
        kwargs["debounce"] = 0
        return real_daemon_cls(project_root, **kwargs)

    monkeypatch.setattr("tesserae.engine.daemon.Daemon", daemon_factory)

    rc = cli.main(
        ["engine", "--once", "--project", str(tmp_path), "--debounce", "0"]
    )

    assert rc == 0
    assert len(calls) == 1


def test_engine_handler_does_not_duplicate_refresh_chain(tmp_path, monkeypatch):
    """_handle_engine delegates to Daemon.run — it does NOT re-implement the chain.

    A spy Daemon records its construction and `.run(once=...)` call. The handler
    must construct exactly one Daemon and call `.run(once=True)` exactly once.
    """
    constructed: list = []
    run_calls: list = []

    class SpyDaemon:
        def __init__(self, project_root, **kwargs):
            constructed.append((project_root, kwargs))

        def run(self, *, once: bool = False) -> int:
            run_calls.append(once)
            return 0

    monkeypatch.setattr("tesserae.engine.daemon.Daemon", SpyDaemon)

    args = argparse.Namespace(
        project=str(tmp_path), interval=2.0, debounce=1.0, once=True
    )
    rc = _handle_engine(args)

    assert rc == 0
    assert len(constructed) == 1  # exactly one Daemon built (no duplicate chain)
    assert constructed[0][0] == Path(tmp_path).resolve()
    assert run_calls == [True]  # delegated to Daemon.run(once=True), once


def test_engine_all_once_runs_fleet_over_registry(tmp_path, monkeypatch, capsys):
    import json

    from tesserae.cli import main
    from tesserae.project import ProjectWiki

    # Two real (empty) projects + a registry pointing at them.
    roots = {}
    for name in ("alpha", "beta"):
        root = tmp_path / name
        root.mkdir()
        ProjectWiki.init(root, name=name)
        roots[name] = root
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "active": None,
                "projects": {
                    n: {"root": str(r), "graph_path": str(r / ".tesserae" / "graph.json")}
                    for n, r in roots.items()
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("TESSERAE_REGISTRY", str(registry))
    monkeypatch.setenv("TESSERAE_FLEET_PIDFILE", str(tmp_path / "engine.pid"))
    # ProjectWiki.init already writes graph.json, so existence alone would not
    # prove the fleet compiled anything — require a rewrite (mtime advance).
    mtimes_before = {
        name: (root / ".tesserae" / "graph.json").stat().st_mtime_ns
        for name, root in roots.items()
    }

    rc = main(["engine", "--all", "--once", "--debounce", "0"])

    assert rc == 0
    for name, root in roots.items():
        graph = root / ".tesserae" / "graph.json"
        assert graph.exists()
        assert graph.stat().st_mtime_ns > mtimes_before[name], (
            f"fleet once-run did not recompile {name}"
        )


def test_engine_all_and_project_are_mutually_exclusive(capsys):
    import pytest

    from tesserae.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["engine", "--all", "--project", "/tmp/x"])
    assert exc.value.code == 2


def test_engine_all_rejects_explicit_project_dot(capsys):
    import pytest

    from tesserae.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["engine", "--all", "--project", "."])
    assert exc.value.code == 2


def test_engine_all_reports_held_pidfile_cleanly(tmp_path, monkeypatch, capsys):
    import json
    import os

    from tesserae.cli import main

    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"version": 1, "active": None, "projects": {}}), encoding="utf-8")
    pidfile = tmp_path / "engine.pid"
    pidfile.write_text(str(os.getpid()))  # our own live pid → "already running"
    monkeypatch.setenv("TESSERAE_REGISTRY", str(registry))
    monkeypatch.setenv("TESSERAE_FLEET_PIDFILE", str(pidfile))

    rc = main(["engine", "--all", "--once"])

    captured = capsys.readouterr()
    assert rc == 2
    assert "already running" in captured.err
    assert "Traceback" not in captured.err


def test_harvest_only_daemon_never_reaches_for_the_compile_lock(tmp_path, monkeypatch):
    """`engine --harvest-only` is the shape a shared-disk fleet needs: N hosts
    tail the transcripts only they can see, one host compiles. The promise is
    load-bearing — if a harvester took the compile lock it would contend with
    the compiling host exactly as before — so assert the constructor arguments
    the flag produces rather than trusting a default elsewhere to imply them."""
    from tesserae.cli import main
    from tesserae.project import ProjectWiki

    project = tmp_path / "proj"
    project.mkdir()
    ProjectWiki.init(project, name="proj")

    seen = {}

    class FakeDaemon:
        def __init__(self, root, **kwargs):
            seen.update(kwargs)
            seen["root"] = root

        def run(self, once=False):
            return 0

    import tesserae.engine.daemon as daemon_mod

    monkeypatch.setattr(daemon_mod, "Daemon", FakeDaemon)

    rc = main(["engine", "--harvest-only", "--once", "--project", str(project)])

    assert rc == 0
    assert seen["enable_compile"] is False, "a harvester must never compile"
    assert seen["enable_watch"] is False
    assert seen["enable_vault"] is False
    assert seen["enable_session_tail"] is True, "harvesting is the whole point"
    assert seen["consolidate"] is False, "a harvester was not asked to spend LLM calls"


def test_engine_without_harvest_only_still_compiles(tmp_path, monkeypatch):
    """The default engine must be unchanged — this flag is opt-in."""
    from tesserae.cli import main
    from tesserae.project import ProjectWiki

    project = tmp_path / "proj"
    project.mkdir()
    ProjectWiki.init(project, name="proj")

    seen = {}

    class FakeDaemon:
        def __init__(self, root, **kwargs):
            seen.update(kwargs)

        def run(self, once=False):
            return 0

    import tesserae.engine.daemon as daemon_mod

    monkeypatch.setattr(daemon_mod, "Daemon", FakeDaemon)

    assert main(["engine", "--once", "--project", str(project)]) == 0
    assert seen["enable_compile"] is True
    assert seen["enable_watch"] is True

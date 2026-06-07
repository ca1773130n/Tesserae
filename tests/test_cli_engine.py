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
from tesserae.cli import _COMMANDS, _handle_engine


def test_engine_and_daemon_aliases_dispatch_same_handler():
    """Both 'engine' and its alias 'daemon' resolve to the same handler."""
    assert "engine" in _COMMANDS
    assert "daemon" in _COMMANDS
    assert _COMMANDS["engine"] is _COMMANDS["daemon"]
    assert _COMMANDS["engine"] is _handle_engine


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

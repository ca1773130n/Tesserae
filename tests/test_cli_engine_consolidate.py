"""CLI wiring tests for the engine 'sleep cycle' consolidation flags.

Covers the `--consolidate/--no-consolidate`, `--consolidate-idle`,
`--consolidate-every` (max-interval ceiling) and `--consolidate-check`
flags on `tesserae engine`: that they parse with the documented defaults
and that they reach the `Daemon` / `FleetDaemon` constructors.

These tests never start a long-running process. The single-project path
uses `--once`; the constructor is patched to a recording fake so the test
is decoupled from the daemon's own signature evolution.
"""

from __future__ import annotations

from pathlib import Path

import tesserae.cli as cli
from tesserae.cli import _build_engine_parser


# ---------------------------------------------------------------------------
# Parsing + defaults
# ---------------------------------------------------------------------------

def test_consolidate_defaults_present():
    """With no consolidate flags, the parser yields the documented defaults."""
    args = _build_engine_parser().parse_args([])
    assert args.consolidate is True
    assert args.consolidate_idle == 300.0
    assert args.consolidate_every == 21600.0
    assert args.consolidate_check == 30.0
    assert args.summarize_budget == 25


def test_summarize_budget_parses_including_zero():
    """`--summarize-budget` parses as int; 0 disables the SUMMARIZE op."""
    args = _build_engine_parser().parse_args(["--summarize-budget", "0"])
    assert args.summarize_budget == 0
    args = _build_engine_parser().parse_args(["--summarize-budget", "7"])
    assert args.summarize_budget == 7


def test_no_consolidate_disables():
    """`--no-consolidate` flips the boolean off; siblings keep their defaults."""
    args = _build_engine_parser().parse_args(["--no-consolidate"])
    assert args.consolidate is False
    assert args.consolidate_idle == 300.0


def test_explicit_consolidate_on():
    args = _build_engine_parser().parse_args(["--consolidate"])
    assert args.consolidate is True


def test_consolidate_seconds_flags_parse():
    """The three numeric knobs parse as floats, including 0 to disable the ceiling."""
    args = _build_engine_parser().parse_args(
        [
            "--consolidate-idle", "45",
            "--consolidate-every", "0",
            "--consolidate-check", "7.5",
        ]
    )
    assert args.consolidate_idle == 45.0
    assert args.consolidate_every == 0.0  # 0 disables the ceiling
    assert args.consolidate_check == 7.5


def test_consolidate_flags_appear_in_help():
    """Help text frames the flags as the idle/periodic 'sleep' cycle."""
    help_text = _build_engine_parser().format_help()
    assert "--consolidate" in help_text
    assert "--no-consolidate" in help_text
    assert "--consolidate-idle" in help_text
    assert "--consolidate-every" in help_text
    assert "--consolidate-check" in help_text
    assert "--summarize-budget" in help_text
    assert "sleep" in help_text.lower()


# ---------------------------------------------------------------------------
# Wiring: flags reach the Daemon constructor (single-project path)
# ---------------------------------------------------------------------------

class _FakeDaemon:
    """Records constructor kwargs and no-ops run()."""

    last_kwargs: dict = {}
    last_root: Path | None = None

    def __init__(self, project_root, **kwargs):
        type(self).last_root = project_root
        type(self).last_kwargs = kwargs

    def run(self, once=False):  # noqa: D401 - matches Daemon.run signature
        return 0


def _run_engine(monkeypatch, argv):
    monkeypatch.setattr("tesserae.engine.daemon.Daemon", _FakeDaemon)
    # raise_fd_limit is imported from the same module inside the handler; keep
    # it harmless (it is a real function, but calling it is fine — no patch).
    return cli.main(argv)


def test_default_consolidate_knobs_reach_daemon(tmp_path, monkeypatch):
    (tmp_path / ".tesserae").mkdir(parents=True, exist_ok=True)
    rc = _run_engine(monkeypatch, ["engine", "--once", "--project", str(tmp_path)])
    assert rc == 0
    kw = _FakeDaemon.last_kwargs
    assert kw["consolidate"] is True
    assert kw["consolidate_idle_seconds"] == 300.0
    assert kw["consolidate_max_interval_seconds"] == 21600.0
    assert kw["consolidate_check_interval"] == 30.0
    assert kw["summarize_budget"] == 25


def test_summarize_budget_reaches_daemon(tmp_path, monkeypatch):
    (tmp_path / ".tesserae").mkdir(parents=True, exist_ok=True)
    rc = _run_engine(
        monkeypatch,
        ["engine", "--once", "--project", str(tmp_path), "--summarize-budget", "3"],
    )
    assert rc == 0
    assert _FakeDaemon.last_kwargs["summarize_budget"] == 3


def test_custom_consolidate_knobs_reach_daemon(tmp_path, monkeypatch):
    (tmp_path / ".tesserae").mkdir(parents=True, exist_ok=True)
    rc = _run_engine(
        monkeypatch,
        [
            "engine", "--once", "--project", str(tmp_path),
            "--no-consolidate",
            "--consolidate-idle", "60",
            "--consolidate-every", "0",
            "--consolidate-check", "5",
        ],
    )
    assert rc == 0
    kw = _FakeDaemon.last_kwargs
    assert kw["consolidate"] is False
    assert kw["consolidate_idle_seconds"] == 60.0
    assert kw["consolidate_max_interval_seconds"] == 0.0
    assert kw["consolidate_check_interval"] == 5.0


# ---------------------------------------------------------------------------
# Wiring: flags reach the FleetDaemon constructor (--all path)
# ---------------------------------------------------------------------------

class _FakeFleet:
    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        type(self).last_kwargs = kwargs

    def run(self, once=False):
        return 0


def test_consolidate_knobs_reach_fleet(monkeypatch):
    monkeypatch.setattr("tesserae.engine.fleet.FleetDaemon", _FakeFleet)
    rc = cli.main(
        [
            "engine", "--all", "--once",
            "--consolidate-idle", "120",
            "--consolidate-every", "3600",
            "--consolidate-check", "15",
        ]
    )
    assert rc == 0
    kw = _FakeFleet.last_kwargs
    assert kw["consolidate"] is True
    assert kw["consolidate_idle_seconds"] == 120.0
    assert kw["consolidate_max_interval_seconds"] == 3600.0
    assert kw["consolidate_check_interval"] == 15.0
    assert kw["summarize_budget"] == 25


def test_summarize_budget_reaches_fleet(monkeypatch):
    monkeypatch.setattr("tesserae.engine.fleet.FleetDaemon", _FakeFleet)
    rc = cli.main(["engine", "--all", "--once", "--summarize-budget", "0"])
    assert rc == 0
    assert _FakeFleet.last_kwargs["summarize_budget"] == 0

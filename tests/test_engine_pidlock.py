"""Pidfile identity must defeat the stale-pidfile + PID-reuse false positive.

A dead engine that left its pidfile behind, plus an OS that recycled its PID to
an unrelated process, made a bare ``os.kill(pid, 0)`` liveness probe succeed —
so the engine refused to start with "already running" when none was. These tests
pin the fix: a live PID counts as the owner ONLY when its recorded start-time
identity still matches.
"""

from __future__ import annotations

import json
import os

import pytest

from tesserae.engine import pidlock
from tesserae.engine.daemon import Daemon

# A PID that is essentially never alive — above the typical pid_max.
_DEAD_PID = 2_147_483_646


def test_parse_accepts_json_and_legacy_bare_int():
    assert pidlock.parse('{"pid": 123, "start_time": "x"}')["pid"] == 123
    legacy = pidlock.parse("4242")  # older engines wrote just the integer
    assert legacy == {"pid": 4242, "start_time": None, "cmdline": None}
    assert pidlock.parse("") is None
    assert pidlock.parse("not-a-pid") is None


def test_serialize_roundtrips_current_process():
    payload = pidlock.parse(pidlock.serialize())
    assert payload["pid"] == os.getpid()
    # cmdline/start_time are best-effort; the key invariant is the pid survives.
    assert "start_time" in payload and "cmdline" in payload


def test_dead_pid_is_not_alive():
    assert pidlock.owner_is_alive({"pid": _DEAD_PID, "start_time": "whatever"}) is False
    assert pidlock.owner_is_alive(None) is False
    assert pidlock.owner_is_alive({"start_time": "x"}) is False  # no pid


def test_live_pid_with_matching_identity_is_alive(monkeypatch):
    # Our own (live) process AND the recorded start time matches the live one.
    # Pin process_start_time so the assertion holds on every platform — including
    # CI/sandboxes where `ps`/`/proc` are unavailable and it would return None.
    monkeypatch.setattr(pidlock, "process_start_time", lambda pid: "INCARNATION-A")
    payload = {"pid": os.getpid(), "start_time": "INCARNATION-A"}
    assert pidlock.owner_is_alive(payload) is True


def test_live_pid_with_mismatched_identity_is_stale(monkeypatch):
    """THE bug: a recycled PID is alive but its start time differs -> stale.

    Deterministic on every platform — the live identity is pinned to a value
    that differs from what the pidfile recorded, simulating PID reuse without
    depending on a real second process or on `ps`/`/proc` being available.
    """
    monkeypatch.setattr(pidlock, "process_start_time", lambda pid: "INCARNATION-NEW")
    payload = {"pid": os.getpid(), "start_time": "INCARNATION-OLD"}
    assert pidlock.owner_is_alive(payload) is False


def test_legacy_bare_int_live_pid_stays_conservative():
    # No recorded identity (legacy pidfile) -> a live PID is treated as alive,
    # so we never risk starting a second engine on ambiguous evidence.
    assert pidlock.owner_is_alive({"pid": os.getpid(), "start_time": None}) is True


def test_unreadable_identity_stays_conservative(monkeypatch):
    # Live PID but we CANNOT read its current start time (None) -> treat as alive
    # rather than risk a double start. The recorded value is irrelevant here.
    monkeypatch.setattr(pidlock, "process_start_time", lambda pid: None)
    assert pidlock.owner_is_alive({"pid": os.getpid(), "start_time": "anything"}) is True


def test_daemon_reclaims_pidfile_with_reused_pid(tmp_path, monkeypatch):
    """End-to-end: a pidfile naming our LIVE pid but a STALE identity must be
    reclaimed (pre-fix this raised "already running"). Platform-independent."""
    monkeypatch.setattr(pidlock, "process_start_time", lambda pid: "INCARNATION-NEW")
    d = Daemon(tmp_path)
    d._pidfile.parent.mkdir(parents=True, exist_ok=True)
    d._pidfile.write_text(
        json.dumps({"pid": os.getpid(), "start_time": "INCARNATION-OLD", "cmdline": "old"})
    )
    d._write_pidfile()  # must NOT raise — the recorded incarnation is gone
    refreshed = pidlock.parse(d._pidfile.read_text())
    assert refreshed["pid"] == os.getpid()
    assert refreshed["start_time"] == "INCARNATION-NEW"  # rewritten to current


@pytest.mark.skipif(
    pidlock.process_start_time(os.getpid()) is None,
    reason="process start time unavailable on this platform (ps//proc absent)",
)
def test_real_platform_start_time_roundtrips():
    """Smoke: where the OS exposes start time, serialize() captures a real,
    non-None token and owner_is_alive confirms our own live process."""
    payload = pidlock.parse(pidlock.serialize())
    assert payload["pid"] == os.getpid()
    assert payload["start_time"] is not None
    assert pidlock.owner_is_alive(payload) is True


def test_locale_mismatched_start_time_rescued_by_cmdline(monkeypatch):
    """Same instant, different locale renderings (Korean writer, C reader):
    a matching full cmdline rescues the alive verdict instead of misreading
    a live daemon as PID reuse."""
    monkeypatch.setattr(pidlock, "process_start_time", lambda pid: "Sat Jul 11 18:51:15 2026")
    monkeypatch.setattr(pidlock, "process_cmdline", lambda pid: "python -m tesserae engine")
    payload = {
        "pid": os.getpid(),
        "start_time": "2026년  7월 11일 토요일 18시 51분 15초",
        "cmdline": "python -m tesserae engine",
    }
    assert pidlock.owner_is_alive(payload) is True


def test_mismatched_start_time_and_cmdline_is_stale(monkeypatch):
    """Genuine PID reuse: both identity signals disagree — stale."""
    monkeypatch.setattr(pidlock, "process_start_time", lambda pid: "Sat Jul 11 18:51:15 2026")
    monkeypatch.setattr(pidlock, "process_cmdline", lambda pid: "some other process")
    payload = {
        "pid": os.getpid(),
        "start_time": "Sat Jan  1 00:00:00 2022",
        "cmdline": "python -m tesserae engine",
    }
    assert pidlock.owner_is_alive(payload) is False


def test_ps_start_time_is_locale_pinned():
    """The macOS ps branch must pin LC_ALL=C so writer/reader agree."""
    import inspect

    src = inspect.getsource(pidlock.process_start_time)
    assert 'LC_ALL' in src  # regression guard: locale-pinned ps invocation

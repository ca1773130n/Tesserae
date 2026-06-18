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


def test_live_pid_with_matching_identity_is_alive():
    # Our own process: alive, and its recorded identity matches the live one.
    payload = {"pid": os.getpid(), "start_time": pidlock.process_start_time(os.getpid())}
    assert pidlock.owner_is_alive(payload) is True


def test_live_pid_with_mismatched_identity_is_stale():
    """THE bug: a recycled PID is alive but its start time differs -> stale."""
    if pidlock.process_start_time(os.getpid()) is None:
        pytest.skip("process start time unavailable on this platform")
    payload = {"pid": os.getpid(), "start_time": "STALE-FROM-A-PRIOR-INCARNATION"}
    assert pidlock.owner_is_alive(payload) is False


def test_legacy_bare_int_live_pid_stays_conservative():
    # No recorded identity (legacy pidfile) -> a live PID is treated as alive,
    # so we never risk starting a second engine on ambiguous evidence.
    assert pidlock.owner_is_alive({"pid": os.getpid(), "start_time": None}) is True


def test_daemon_reclaims_pidfile_with_reused_pid(tmp_path):
    """End-to-end: a pidfile naming our LIVE pid but a STALE identity must be
    reclaimed (pre-fix this raised "already running")."""
    if pidlock.process_start_time(os.getpid()) is None:
        pytest.skip("process start time unavailable on this platform")
    d = Daemon(tmp_path)
    d._pidfile.parent.mkdir(parents=True, exist_ok=True)
    d._pidfile.write_text(
        json.dumps({"pid": os.getpid(), "start_time": "PRIOR-INCARNATION", "cmdline": "old"})
    )
    d._write_pidfile()  # must NOT raise — the recorded incarnation is gone
    refreshed = pidlock.parse(d._pidfile.read_text())
    assert refreshed["pid"] == os.getpid()
    assert refreshed["start_time"] != "PRIOR-INCARNATION"  # rewritten to current

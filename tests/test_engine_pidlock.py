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


# --------------------------------------------------------------------------- #
# Lease records: host- and version-aware (roadmap R5)                          #
# --------------------------------------------------------------------------- #


def test_serialize_writes_a_lease(monkeypatch):
    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    monkeypatch.setenv("TESSERAE_LEASE_TTL", "60")
    payload = pidlock.parse(pidlock.serialize())
    assert payload["host"] == "srv-a"
    assert payload["schema"] == pidlock.LEASE_SCHEMA
    assert payload["version"] == pidlock.package_version()
    assert payload["lease_expires"] - payload["renewed_at"] == pytest.approx(60.0)
    assert {"hostname", "boot_id"} <= set(payload)


def test_foreign_pid_is_never_probed(monkeypatch):
    """A pid recorded by another host names nothing in THIS process table.

    ``_DEAD_PID`` is dead here; judged locally it would read as stale. With a
    live lease from another host it must read as alive, and with an expired
    one as gone, whatever the local table says.
    """
    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    import time as _time

    live = {"pid": _DEAD_PID, "host": "srv-b", "lease_expires": _time.time() + 60}
    expired = {"pid": os.getpid(), "host": "srv-b", "lease_expires": _time.time() - 1}
    assert pidlock.owner_is_alive(live) is True
    assert pidlock.owner_is_alive(expired) is False  # our own pid is alive here; irrelevant
    # No lease at all (a pre-lease record carrying a host): cannot judge, so alive.
    assert pidlock.owner_is_alive({"pid": _DEAD_PID, "host": "srv-b"}) is True


def test_reboot_on_this_host_makes_the_record_stale(monkeypatch):
    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    monkeypatch.setattr(pidlock, "boot_id", lambda: "BOOT-NEW")
    monkeypatch.setattr(pidlock, "process_start_time", lambda pid: "SAME")
    payload = {"pid": os.getpid(), "host": "srv-a", "boot_id": "BOOT-OLD", "start_time": "SAME"}
    assert pidlock.owner_is_alive(payload) is False
    assert pidlock.owner_is_alive({**payload, "boot_id": "BOOT-NEW"}) is True


def test_renew_extends_only_our_own_lease(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    monkeypatch.setenv("TESSERAE_LEASE_TTL", "60")
    path = tmp_path / "daemon.srv-a.pid"
    path.write_text(pidlock.serialize())
    before = pidlock.read_owner(path)["lease_expires"]
    monkeypatch.setenv("TESSERAE_LEASE_TTL", "600")
    assert pidlock.renew(path) is True
    assert pidlock.read_owner(path)["lease_expires"] > before
    # Someone else's record (reclaimed after we stalled) is never overwritten.
    path.write_text(json.dumps({"pid": os.getpid() + 1, "host": "srv-a"}))
    assert pidlock.renew(path) is False
    assert pidlock.read_owner(path)["pid"] == os.getpid() + 1


def test_heartbeat_renews_until_stopped(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    path = tmp_path / "engine.pid"
    path.write_text(pidlock.serialize())
    first = pidlock.read_owner(path)["renewed_at"]
    beat = pidlock.LeaseHeartbeat(path, interval=0.02).start()
    import time as _time

    deadline = _time.monotonic() + 5
    while pidlock.read_owner(path)["renewed_at"] == first and _time.monotonic() < deadline:
        _time.sleep(0.01)
    beat.stop()
    assert pidlock.read_owner(path)["renewed_at"] > first


def test_version_conflicts_reports_only_live_leases_of_other_versions(tmp_path, monkeypatch):
    import time as _time

    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    monkeypatch.setattr(pidlock, "package_version", lambda: "0.42.0")
    live_old = tmp_path / "daemon.srv-b.pid"
    live_old.write_text(json.dumps({"pid": 11, "host": "srv-b", "version": "0.41.8",
                                    "lease_expires": _time.time() + 60}))
    dead_old = tmp_path / "daemon.srv-c.pid"
    dead_old.write_text(json.dumps({"pid": 12, "host": "srv-c", "version": "0.40.0",
                                    "lease_expires": _time.time() - 60}))
    same = tmp_path / "daemon.srv-d.pid"
    same.write_text(json.dumps({"pid": 13, "host": "srv-d", "version": "0.42.0",
                                "lease_expires": _time.time() + 60}))
    conflicts = pidlock.version_conflicts([live_old, dead_old, same])
    assert [c["version"] for c in conflicts] == ["0.41.8"]


def _foreign_old_version_lease(daemon):
    import time as _time

    daemon._pidfile.parent.mkdir(parents=True, exist_ok=True)
    (daemon._pidfile.parent / "daemon.srv-b.pid").write_text(json.dumps(
        {"pid": 11, "host": "srv-b", "hostname": "srv-b", "version": "0.0.1",
         "lease_expires": _time.time() + 60}))


def test_daemon_warns_by_default_on_a_live_other_version_lease(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    monkeypatch.delenv("TESSERAE_LEASE_STRICT_VERSION", raising=False)
    d = Daemon(tmp_path)
    _foreign_old_version_lease(d)
    with caplog.at_level("WARNING"):
        d._write_pidfile()
    assert d._pidfile.exists()
    assert "0.0.1 (pid 11 on srv-b)" in caplog.text


def test_daemon_refuses_under_strict_version_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("TESSERAE_HOST_ID", "srv-a")
    monkeypatch.setenv("TESSERAE_LEASE_STRICT_VERSION", "1")
    d = Daemon(tmp_path)
    _foreign_old_version_lease(d)
    with pytest.raises(RuntimeError, match="refusing to start"):
        d._write_pidfile()
    assert not d._pidfile.exists()

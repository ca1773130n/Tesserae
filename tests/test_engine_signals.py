"""Real-process signal tests for ``tesserae engine``.

The daemon core tests drive the drain loop in-process with no real sleeps. Signal
delivery cannot be tested that way: what matters is which thread a handler runs
on while a compile blocks the event loop, so these start a real process and send
it real signals.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")

_SLOW_FINAL_COMPILE = """
import pathlib, sys, threading, time
from tesserae.engine.daemon import Daemon, TriggerEvent

root = pathlib.Path(sys.argv[1])

class _Daemon(Daemon):
    def _start_sources(self, loop):
        def arm():
            time.sleep(0.5)
            self.enqueue(TriggerEvent(source="t", changed_paths=[root / "a.md"]))
            (root / "armed").write_text("x")
        threading.Thread(target=arm, daemon=True).start()

def slow_compile(paths):
    (root / "compiling").write_text("x")
    time.sleep(120)

_Daemon(root, debounce=60.0, consolidate=False, run_pipeline=slow_compile).run()
"""


def test_second_sigterm_stops_a_running_final_compile(tmp_path):
    """Real process, real signals: SIGTERM during the final compile exits at once.

    The compile runs on the loop thread, and signals were delivered through the
    loop, so a second SIGTERM sent while the final compile ran was not seen until
    that compile ended.
    """
    (tmp_path / ".tesserae").mkdir()
    proc = subprocess.Popen([sys.executable, "-c", _SLOW_FINAL_COMPILE, str(tmp_path)])
    try:
        deadline = time.monotonic() + 20
        while not (tmp_path / "armed").exists():
            assert proc.poll() is None and time.monotonic() < deadline, "daemon never armed"
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)  # graceful: starts the final compile
        while not (tmp_path / "compiling").exists():
            assert proc.poll() is None and time.monotonic() < deadline, "final compile never ran"
            time.sleep(0.05)
        time.sleep(0.2)
        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        try:
            rc = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            rc = None
        assert rc is not None, "second SIGTERM did not stop the running final compile"
        assert time.monotonic() - started < 10
        assert rc == 128 + signal.SIGTERM
        assert not list((tmp_path / ".tesserae").glob("daemon*.pid")), "pidfile left behind"
    finally:
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait()


_FLEET_UNIT_MID_COMPILE = """
import json, os, pathlib, sys, threading, time
from tesserae.engine.daemon import Daemon, TriggerEvent
from tesserae.engine.fleet import FleetDaemon
from tesserae.llm_json import _run_cli

base = pathlib.Path(sys.argv[1])
root = base / "alpha"
(root / ".tesserae").mkdir(parents=True)
registry = base / "registry.json"
registry.write_text(json.dumps({"version": 1, "projects": {"alpha": {"root": str(root)}}}))
# A stand-in CLI agent: records its pid, then outlives the test by far.
agent = "import os, pathlib, sys, time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(120)"

class _Unit(Daemon):
    def _start_sources(self, loop):
        def arm():
            time.sleep(0.5)
            self.enqueue(TriggerEvent(source="t", changed_paths=[root / "a.md"]))
        threading.Thread(target=arm, daemon=True).start()

def compile_with_agent(paths):
    _run_cli([sys.executable, "-c", agent, str(base / "agent.pid")], "", os.environ, 300)

def factory(name, project_root, fleet):
    return _Unit(project_root, debounce=0.0, consolidate=False, install_signal_handlers=False,
                 compile_gate=fleet.compile_gate, run_pipeline=compile_with_agent)

sys.exit(FleetDaemon(registry, pidfile=base / "engine.pid", daemon_factory=factory).run())
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_second_sigterm_stops_a_fleet_whose_unit_is_mid_compile(tmp_path):
    """`engine --all`: a second SIGTERM exits at once and takes the unit's agent with it.

    A fleet unit compiles on its own thread, which no exception raised in the
    main thread can reach, so the fleet ignored every signal after the first
    and waited out the compile. Its `claude -p` child runs in its own session
    and would outlive a fleet that just exited.
    """
    proc = subprocess.Popen([sys.executable, "-c", _FLEET_UNIT_MID_COMPILE, str(tmp_path)])
    agent_pid = None
    try:
        deadline = time.monotonic() + 20
        while not (tmp_path / "agent.pid").exists() or not (tmp_path / "agent.pid").read_text():
            assert proc.poll() is None and time.monotonic() < deadline, "unit never started its agent"
            time.sleep(0.05)
        agent_pid = int((tmp_path / "agent.pid").read_text())
        proc.send_signal(signal.SIGTERM)  # graceful: the unit finishes its compile
        time.sleep(0.5)
        assert proc.poll() is None, "the first SIGTERM must let the running compile finish"
        started = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        try:
            rc = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            rc = None
        assert rc is not None, "second SIGTERM did not stop a fleet whose unit is mid-compile"
        assert time.monotonic() - started < 10
        assert rc == 128 + signal.SIGTERM
        deadline = time.monotonic() + 5
        while _alive(agent_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(agent_pid), "the unit's CLI agent outlived the fleet"
        assert not (tmp_path / "engine.pid").exists(), "fleet pidfile left behind"
        assert not list((tmp_path / "alpha" / ".tesserae").glob("daemon*.pid")), "unit pidfile left behind"
    finally:
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait()
        if agent_pid is not None and _alive(agent_pid):
            os.killpg(agent_pid, signal.SIGKILL)

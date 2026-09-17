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

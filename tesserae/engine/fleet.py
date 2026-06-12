"""Fleet supervisor: one process keeping every registered project fresh.

Supervises one per-project :class:`~tesserae.engine.daemon.Daemon` per entry in
``~/.tesserae/registry.json``, each in its own thread. The fleet owns the
process-level concerns — global pidfile, SIGTERM/SIGINT, registry
reconciliation, and a shared compile semaphore so concurrent units don't fight
over the same LLM accounts. Design:
docs/superpowers/specs/2026-06-12-global-engine-design.md

The ``TESSERAE_REGISTRY`` and ``TESSERAE_FLEET_PIDFILE`` environment overrides
are mapped by the CLI layer (``tesserae engine --all``); programmatic callers
pass ``registry_path=`` and ``pidfile=`` explicitly to :class:`FleetDaemon`.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from .daemon import Daemon

logger = logging.getLogger("tesserae.fleet")

DEFAULT_FLEET_PIDFILE = Path.home() / ".tesserae" / "engine.pid"

# (name, project_root, fleet) -> Daemon. The fleet passes itself so factories
# can reach the shared compile_gate; tests substitute recording daemons.
DaemonFactory = Callable[[str, Path, "FleetDaemon"], Daemon]


@dataclass
class _Unit:
    name: str
    root: Path
    daemon: Daemon
    thread: Optional[threading.Thread] = None


class FleetDaemon:
    """One process, every registered project. See module docstring."""

    def __init__(
        self,
        registry_path: Optional[Path] = None,
        *,
        compile_slots: int = 1,
        registry_poll: float = 10.0,
        debounce: float = 1.0,
        watch_interval: float = 2.0,
        pidfile: Optional[Path] = None,
        daemon_factory: Optional[DaemonFactory] = None,
    ) -> None:
        from ..mcp_server import ProjectRegistry

        self._registry = ProjectRegistry(registry_path)
        self.compile_gate = threading.Semaphore(max(1, int(compile_slots)))
        self._registry_poll = registry_poll
        self._unit_debounce = debounce
        self._unit_watch_interval = watch_interval
        self._pidfile = Path(pidfile) if pidfile is not None else DEFAULT_FLEET_PIDFILE
        self._daemon_factory = daemon_factory or self._default_daemon_factory
        self._units: Dict[str, _Unit] = {}
        self._stop = threading.Event()

    # ----- unit construction ------------------------------------------------

    def _default_daemon_factory(self, name: str, root: Path, fleet: "FleetDaemon") -> Daemon:
        return Daemon(
            root,
            debounce=fleet._unit_debounce,
            watch_interval=fleet._unit_watch_interval,
            install_signal_handlers=False,
            compile_gate=fleet.compile_gate,
        )

    def _desired_projects(self) -> Dict[str, Path]:
        """Registry entries that exist on disk, name -> resolved root."""
        try:
            data = self._registry.load()
        except (ValueError, OSError) as exc:
            # Corrupt (ValueError) or unreadable/racing-deleted (OSError)
            # registry: keep the current unit set running rather than tearing
            # the fleet down over a transient bad state.
            # At startup that set is empty: the fleet idles until the registry becomes readable.
            logger.error("registry unreadable (%s); keeping current units, will retry at next poll tick", exc)
            return {u.name: u.root for u in self._units.values()}
        desired: Dict[str, Path] = {}
        for name, entry in (data.get("projects") or {}).items():
            root = Path(str(entry.get("root") or "")).expanduser()
            if (root / ".tesserae").is_dir():
                desired[name] = root.resolve()
            else:
                logger.warning("registered project %s has no .tesserae at %s; skipping", name, root)
        return desired

    # ----- reconciliation ---------------------------------------------------

    def reconcile(self) -> None:
        """Diff registry against running units; start/stop to converge.

        Public + synchronous so tests drive it directly without poll sleeps.
        """
        desired = self._desired_projects()
        for name in list(self._units):
            unit = self._units[name]
            dead = unit.thread is not None and not unit.thread.is_alive()
            moved = name in desired and desired[name] != unit.root
            if name not in desired or dead or moved:
                if dead and name in desired:
                    logger.warning("unit %s thread died; will restart", name)
                if moved:
                    logger.info("unit %s root changed %s -> %s; restarting", name, unit.root, desired[name])
                self._stop_unit(name)
        for name, root in sorted(desired.items()):
            if name not in self._units:
                self._start_unit(name, root)

    def _start_unit(self, name: str, root: Path) -> None:
        daemon = self._daemon_factory(name, root, self)

        def _run() -> None:
            try:
                daemon.run()
            except Exception as exc:  # noqa: BLE001 — unit dies loudly, fleet survives
                logger.error("unit %s exited with error: %s", name, exc)

        # Non-daemon: a unit mid-compile must finish (or be cleanly stopped)
        # before the interpreter exits — abandoning it could tear down the
        # thread while it is writing .tesserae artifacts.
        thread = threading.Thread(target=_run, name=f"tesserae-unit-{name}", daemon=False)
        self._units[name] = _Unit(name=name, root=root, daemon=daemon, thread=thread)
        thread.start()
        logger.info("unit %s started (%s)", name, root)

    def _stop_unit(self, name: str) -> None:
        unit = self._units.pop(name, None)
        if unit is None:
            return
        unit.daemon.request_stop()
        if unit.thread is not None:
            # Wait as long as it takes: a unit mid-compile finishes its current
            # pipeline before exiting, and abandoning the thread would let the
            # process tear it down mid-write. Periodic warnings keep the wait
            # observable instead of silent.
            while True:
                unit.thread.join(timeout=10)
                if not unit.thread.is_alive():
                    break
                logger.warning("unit %s still stopping (compile in progress?); waiting", name)
        logger.info("unit %s stopped", name)

    # ----- lifecycle ----------------------------------------------------------

    def request_stop(self) -> None:
        """Thread-safe external stop (signals route here too)."""
        self._stop.set()

    def run(self, *, once: bool = False) -> int:
        self._write_pidfile()
        try:
            if once:
                # Deterministic CI mode: one bounded once-run per project,
                # sequential, no threads, no signals.
                for name, root in sorted(self._desired_projects().items()):
                    daemon = self._daemon_factory(name, root, self)
                    rc = daemon.run(once=True)
                    logger.info("unit %s once-run rc=%d", name, rc)
                return 0
            # Fleet uses signal.signal (main-thread only); unit Daemons use loop.add_signal_handler — see daemon.py.
            try:
                for sig in (signal.SIGTERM, signal.SIGINT):
                    signal.signal(sig, lambda *_: self.request_stop())
            except ValueError:
                # signal.signal only works from the main thread; both signals
                # fail or succeed together, so one warning covers it. Callers
                # off the main thread (tests) use request_stop() directly.
                logger.warning("signal handlers unavailable off the main thread")
            self.reconcile()
            while not self._stop.is_set():
                self._stop.wait(self._registry_poll)
                if not self._stop.is_set():
                    self.reconcile()
            for name in list(self._units):
                self._stop_unit(name)
            return 0
        finally:
            self._remove_pidfile()

    # ----- pidfile (atomic acquire; only the owner removes it) ---------------

    def _write_pidfile(self) -> None:
        """Acquire the fleet pidfile atomically.

        ``O_CREAT | O_EXCL`` closes the check-then-write race two concurrent
        ``engine --all`` starts would otherwise hit: exactly one create wins.
        A stale file (dead pid) is unlinked and the create retried once; if a
        live process re-creates it in that window, the second attempt sees it
        and raises.
        """
        self._pidfile.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):
            try:
                fd = os.open(self._pidfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    old_pid = int(self._pidfile.read_text().strip())
                except (ValueError, OSError):
                    old_pid = None
                if old_pid is not None:
                    try:
                        os.kill(old_pid, 0)
                    except ProcessLookupError:
                        pass  # stale — fall through to unlink + retry
                    except PermissionError:
                        raise RuntimeError(f"fleet engine already running (pid {old_pid})")
                    else:
                        raise RuntimeError(f"fleet engine already running (pid {old_pid})")
                if attempt == 0:
                    logger.warning("stale fleet pidfile (pid %s gone); reclaiming", old_pid)
                    try:
                        self._pidfile.unlink()
                    except FileNotFoundError:
                        pass
                continue
            with os.fdopen(fd, "w") as handle:
                handle.write(str(os.getpid()))
            return
        raise RuntimeError(f"could not acquire fleet pidfile at {self._pidfile}")

    def _remove_pidfile(self) -> None:
        """Remove the pidfile only if this process still owns it.

        An unconditional unlink could delete a replacement pidfile written by
        a newer fleet that legitimately reclaimed a stale entry.
        """
        try:
            if self._pidfile.read_text().strip() == str(os.getpid()):
                self._pidfile.unlink()
        except (OSError, ValueError):
            pass

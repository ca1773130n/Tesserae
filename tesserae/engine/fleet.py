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
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # pragma: no cover — Windows: pidfile acquire degrades to O_EXCL only
    fcntl = None  # type: ignore[assignment]

from . import pidlock
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
        consolidate: bool = True,
        consolidate_idle_seconds: float = 300.0,
        consolidate_max_interval_seconds: float = 21600.0,
        consolidate_check_interval: float = 30.0,
        summarize_budget: int = 25,
        brief_budget: int = 8,
        pidfile: Optional[Path] = None,
        daemon_factory: Optional[DaemonFactory] = None,
    ) -> None:
        from ..mcp_server import ProjectRegistry

        self._registry = ProjectRegistry(registry_path)
        self.compile_gate = threading.Semaphore(max(1, int(compile_slots)))
        self._registry_poll = registry_poll
        self._unit_debounce = debounce
        self._unit_watch_interval = watch_interval
        self._unit_consolidate = consolidate
        self._unit_consolidate_idle_seconds = consolidate_idle_seconds
        self._unit_consolidate_max_interval_seconds = consolidate_max_interval_seconds
        self._unit_consolidate_check_interval = consolidate_check_interval
        self._unit_summarize_budget = summarize_budget
        self._unit_brief_budget = brief_budget
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
            consolidate=fleet._unit_consolidate,
            consolidate_idle_seconds=fleet._unit_consolidate_idle_seconds,
            consolidate_max_interval_seconds=fleet._unit_consolidate_max_interval_seconds,
            consolidate_check_interval=fleet._unit_consolidate_check_interval,
            summarize_budget=fleet._unit_summarize_budget,
            brief_budget=fleet._unit_brief_budget,
            install_signal_handlers=False,
            compile_gate=fleet.compile_gate,
        )

    def _desired_projects(self) -> Dict[str, Path]:
        """Registry entries that exist on disk, name -> resolved root."""
        try:
            data = self._registry.load()
            projects = data.get("projects", {})
            if not isinstance(projects, dict):
                # `or {}` would silently coerce null/[]/""/false to "no
                # projects" and stop every running unit; a non-dict value is
                # malformed, not empty.
                raise TypeError(f"registry 'projects' is {type(projects).__name__}, expected object")
            entries = list(projects.items())
        except (ValueError, OSError, AttributeError, TypeError) as exc:
            # Corrupt JSON (ValueError), unreadable/racing-deleted (OSError),
            # or well-formed JSON of the wrong shape (AttributeError/TypeError,
            # e.g. "projects" being a list): keep the current unit set running
            # rather than tearing the fleet down over a transient bad state.
            # At startup that set is empty: the fleet idles until the registry becomes readable.
            logger.error("registry unreadable (%s); keeping current units, will retry at next poll tick", exc)
            return {u.name: u.root for u in self._units.values()}
        desired: Dict[str, Path] = {}
        for name, entry in entries:
            if not isinstance(entry, dict):
                logger.warning("registered project %r has a malformed registry entry; skipping", name)
                continue
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

    def _run_once_over_registry(self) -> int:
        """One bounded run per registered project; per-project failure isolation.

        Every unit runs even when an earlier one blows up. Without this, a
        single project whose ``.tesserae`` is corrupt (or whose pidfile is held
        by a live daemon — ``Daemon.run`` raises ``RuntimeError`` for that)
        aborts the loop and every project after it in name order is silently
        never refreshed, while the fleet still returns 0. A batch run over a
        registry is only trustworthy if "it returned 0" means every unit ran.

        Returns 1 when any unit failed, 0 when they all succeeded.
        """
        results: List[Tuple[str, bool, str]] = []
        for name, root in sorted(self._desired_projects().items()):
            try:
                daemon = self._daemon_factory(name, root, self)
                rc = daemon.run(once=True)
            except Exception as exc:  # noqa: BLE001 — one bad project must not end the batch
                logger.error("unit %s once-run failed: %s", name, exc)
                results.append((name, False, str(exc)))
                continue
            if rc == 0:
                logger.info("unit %s once-run rc=%d", name, rc)
                results.append((name, True, ""))
            else:
                logger.error("unit %s once-run rc=%d", name, rc)
                results.append((name, False, f"rc={rc}"))
        failed = [(name, error) for name, ok, error in results if not ok]
        if failed:
            # One line naming every casualty: a batch over 20 projects buries
            # per-unit errors far above the exit code the operator sees.
            logger.error(
                "fleet once-run: %d/%d units ok; failed: %s",
                len(results) - len(failed),
                len(results),
                ", ".join(f"{name} ({error})" for name, error in failed),
            )
            return 1
        logger.info("fleet once-run: %d/%d units ok", len(results), len(results))
        return 0

    def run(self, *, once: bool = False) -> int:
        self._write_pidfile()
        try:
            if once:
                # Deterministic CI mode: one bounded once-run per project,
                # sequential, no threads, no signals.
                return self._run_once_over_registry()
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
            return 0
        finally:
            # Stop/join all units on EVERY exit path — including reconcile()
            # raising mid-loop — before releasing the pidfile. Otherwise
            # non-daemon unit threads would outlive a "stopped" fleet whose
            # pidfile is already gone.
            for name in list(self._units):
                self._stop_unit(name)
            self._remove_pidfile()

    # ----- pidfile (atomic acquire; only the owner removes it) ---------------

    @contextmanager
    def _pidfile_mutex(self) -> Iterator[None]:
        """Exclusive flock on a sidecar lock file serializing pidfile access.

        Without it, two concurrent starts can both judge the same pidfile
        stale; the slower one then unlinks the winner's freshly written file
        and two fleets end up running. The sidecar file is never removed —
        only its lock matters.
        """
        lock_path = self._pidfile.with_name(self._pidfile.name + ".lock")
        with lock_path.open("a") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _write_pidfile(self) -> None:
        """Acquire the fleet pidfile atomically.

        The whole read/liveness-check/unlink/create sequence runs under the
        sidecar flock (see :meth:`_pidfile_mutex`), and the create itself uses
        ``O_CREAT | O_EXCL`` so it stays atomic even where flock is
        unavailable. A stale file (dead pid) is unlinked and the create
        retried once; a live owner raises.
        """
        self._pidfile.parent.mkdir(parents=True, exist_ok=True)
        with self._pidfile_mutex():
            for attempt in range(2):
                try:
                    fd = os.open(self._pidfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    owner = pidlock.read_owner(self._pidfile)
                    old_pid = owner.get("pid") if owner else None
                    # A live PID is only the owner if its start time still
                    # matches what was recorded — a recycled PID is stale (the
                    # bug this guards against). Unknown identity degrades to a
                    # plain liveness check (conservative; see pidlock).
                    if pidlock.owner_is_alive(owner):
                        raise RuntimeError(f"fleet engine already running (pid {old_pid})")
                    if fcntl is None:
                        # Without a lock backend the unlink+retry reclaim is
                        # the very race the flock exists to close: two starts
                        # could both judge the pid stale and the slower one
                        # would unlink the winner's fresh pidfile.
                        raise RuntimeError(
                            f"stale fleet pidfile at {self._pidfile} (pid {old_pid} gone); "
                            "automatic reclaim needs flock, which is unavailable on this "
                            "platform — remove the file manually and retry"
                        )
                    if attempt == 0:
                        logger.warning("stale fleet pidfile (pid %s); reclaiming", old_pid)
                        try:
                            self._pidfile.unlink()
                        except FileNotFoundError:
                            pass
                    continue
                with os.fdopen(fd, "w") as handle:
                    handle.write(pidlock.serialize())
                return
            raise RuntimeError(f"could not acquire fleet pidfile at {self._pidfile}")

    def _remove_pidfile(self) -> None:
        """Remove the pidfile only if this process still owns it.

        An unconditional unlink could delete a replacement pidfile written by
        a newer fleet that legitimately reclaimed a stale entry. Runs under
        the same sidecar flock as acquisition.
        """
        try:
            with self._pidfile_mutex():
                owner = pidlock.read_owner(self._pidfile)
                if owner and owner.get("pid") == os.getpid():
                    self._pidfile.unlink()
        except (OSError, ValueError):
            pass

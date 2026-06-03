"""Single-owner asyncio supervisor loop for the Tesserae refresh chain.

This is the engine spine (ENG-03 + ENG-04). It owns ONE asyncio event loop that
drains an ``asyncio.Queue`` of :class:`TriggerEvent`s, coalesces a burst of N
events into exactly ONE ``Pipeline.run()`` via a cancel-and-reschedule debounce,
handles SIGTERM/SIGINT for a graceful drain + stop, and owns a pidfile
(create / stale-detect / remove).

Design constraints (02-RESEARCH.md, std-lib only):
- No ``asyncio.run()`` anywhere -- the daemon owns one loop created at startup.
- Signals via ``loop.add_signal_handler`` (asyncio-safe), NOT ``signal.signal``.
- Pidfile stale detection via ``os.kill(pid, 0)`` + ``ProcessLookupError``.
- Each pipeline drain is wrapped in ``try/except Exception`` so the loop survives
  a step that raises (``Pipeline.run`` already catches ``Exception``; this outer
  wrap is the belt-and-suspenders guard for unexpected ``BaseException`` edges).

Trigger sources (poller threads) and CLI wiring are deliberately NOT here: the
``_start_sources`` hook is a no-op that Plan 02 overrides surgically, and Plan 03
adds the CLI command. A ``run_pipeline=`` injection seam keeps tests deterministic
(no real project/compile, no real sleeps).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger("tesserae.daemon")


@dataclass
class TriggerEvent:
    """A single trigger to (eventually) drive one pipeline run.

    Pollers enqueue these; the drain loop coalesces a burst by merging
    ``changed_paths`` across all events collapsed within the debounce window.
    """

    source: str
    changed_paths: List[Path] = field(default_factory=list)
    changed_only: bool = True


class Daemon:
    """Supervisor: one asyncio loop, debounced/coalesced pipeline runs, pidfile.

    The pipeline call is injectable via ``run_pipeline`` (a callable taking the
    merged ``List[Path]``) so tests assert coalescing/survival WITHOUT touching a
    real project. When no override is given, the real step is
    ``ProjectWiki.load(root).compile(changed_only=...)`` driven through
    ``Pipeline.run()``.
    """

    PIDFILE_NAME = "daemon.pid"

    def __init__(
        self,
        project_root: Path,
        *,
        debounce: float = 1.0,
        queue_timeout: float = 1.0,
        join_timeout: float = 5.0,
        run_pipeline: Optional[Callable[[List[Path]], None]] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.debounce = debounce
        self._queue_timeout = queue_timeout
        self._join_timeout = join_timeout
        self._pidfile = self.project_root / ".tesserae" / self.PIDFILE_NAME
        self._stop_event = threading.Event()
        self._threads: List[threading.Thread] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue] = None
        self._run_pipeline_override = run_pipeline

    # ----- thread -> loop bridge -------------------------------------------

    def enqueue(self, event: TriggerEvent) -> None:
        """Thread-safe bridge for poller threads (Plan 02) to feed the loop."""
        if self._loop is not None and self._queue is not None and not self._stop_event.is_set():
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)

    # ----- lifecycle -------------------------------------------------------

    def run(self) -> int:
        """Write pidfile, own the loop until shutdown, clean up unconditionally."""
        self._write_pidfile()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.add_signal_handler(sig, self._handle_signal)
                except NotImplementedError:
                    # Windows / non-main-thread: signals unavailable here.
                    logger.warning("add_signal_handler unavailable for %s; skipping", sig)
            self._start_sources(loop)
            loop.run_until_complete(self._drain_loop())
        finally:
            self._stop_event.set()
            for t in self._threads:
                t.join(timeout=self._join_timeout)
            loop.close()
            self._loop = None
            self._remove_pidfile()
        return 0

    def _handle_signal(self) -> None:
        """Signal callback: request graceful drain+exit (no abrupt loop.stop)."""
        logger.info("shutdown signal received")
        self._stop_event.set()

    # ----- drain loop ------------------------------------------------------

    async def _drain_loop(self) -> None:
        # Create the queue inside the running loop unless one was pre-installed
        # (the test seam pre-loads a burst before driving the drain directly).
        if self._queue is None:
            self._queue = asyncio.Queue()
        pending: Optional[asyncio.Task] = None
        try:
            while not self._stop_event.is_set():
                try:
                    event = await asyncio.wait_for(
                        self._queue.get(), timeout=self._queue_timeout
                    )
                except asyncio.TimeoutError:
                    continue
                # Drain the whole burst that is already queued.
                events = [event]
                while not self._queue.empty():
                    events.append(self._queue.get_nowait())
                # Cancel any in-flight debounce so the burst coalesces to one run.
                if pending is not None and not pending.done():
                    pending.cancel()
                merged = [p for e in events for p in e.changed_paths]
                pending = asyncio.create_task(self._debounce_and_run(merged))
        finally:
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                # Retrieve the task result/exception so neither a CancelledError
                # nor a swallowed pipeline exception leaks an "never retrieved"
                # warning. The pipeline wrapper already logs-and-returns, so a
                # completed task here is benign.
                try:
                    await pending
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001 - daemon survives
                    logger.error(
                        "pipeline raised outside StepResult (daemon survives): %s", exc
                    )

    async def _debounce_and_run(self, paths: List[Path]) -> None:
        await asyncio.sleep(self.debounce)
        self._run_pipeline(paths)

    def _run_pipeline(self, paths: List[Path]) -> None:
        if self._run_pipeline_override is not None:
            self._run_pipeline_override(paths)
            return
        from .pipeline import Pipeline
        from ..project import ProjectWiki

        wiki = ProjectWiki.load(self.project_root)
        steps = [("compile", lambda: wiki.compile(changed_only=bool(paths)))]
        try:
            results = Pipeline(steps).run()
        except Exception as exc:  # noqa: BLE001 - daemon must survive
            logger.error("pipeline raised outside StepResult (daemon survives): %s", exc)
            return
        for r in results:
            if r.ok:
                logger.info("step %s: ok", r.name)
            else:
                logger.error("step %s: FAILED: %s", r.name, r.error)

    # ----- trigger-source hook (Plan 02 overrides body) --------------------

    def _start_sources(self, loop: asyncio.AbstractEventLoop) -> None:
        """No-op hook. Plan 02 fills this with poller threads via ``enqueue``."""
        pass

    # ----- pidfile ---------------------------------------------------------

    def _write_pidfile(self) -> None:
        if self._pidfile.exists():
            try:
                old_pid = int(self._pidfile.read_text().strip())
            except (ValueError, OSError):
                # Unreadable/garbage pidfile -- overwrite-safe.
                old_pid = None
            if old_pid is not None:
                try:
                    os.kill(old_pid, 0)
                except ProcessLookupError:
                    logger.warning("Stale pidfile (pid %d gone); overwriting.", old_pid)
                except PermissionError:
                    # Process exists but is owned by someone else -- treat as live.
                    raise RuntimeError(f"Daemon already running (pid {old_pid})")
                else:
                    raise RuntimeError(f"Daemon already running (pid {old_pid})")
        self._pidfile.parent.mkdir(parents=True, exist_ok=True)
        self._pidfile.write_text(str(os.getpid()))

    def _remove_pidfile(self) -> None:
        try:
            self._pidfile.unlink()
        except FileNotFoundError:
            pass

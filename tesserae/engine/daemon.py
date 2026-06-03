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
import time
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
        watch_interval: float = 2.0,
        vault_poll_interval: float = 1.5,
        enable_watch: bool = True,
        enable_vault: bool = True,
        run_pipeline: Optional[Callable[[List[Path]], None]] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.debounce = debounce
        self._queue_timeout = queue_timeout
        self._join_timeout = join_timeout
        self._watch_interval = watch_interval
        self._vault_poll_interval = vault_poll_interval
        self._enable_watch = enable_watch
        self._enable_vault = enable_vault
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

    def run(self, *, once: bool = False) -> int:
        """Write pidfile, own the loop until shutdown, clean up unconditionally.

        ``once=True`` is the deterministic, CI-friendly mode: NO poller threads,
        NO signal handlers, NO long-running loop. It enqueues a single manual
        ``TriggerEvent`` and runs exactly ONE bounded drain (``_drain_once``)
        that drives exactly one ``_run_pipeline`` call, then returns 0. This is
        the proxy for the SIGTERM-exit-0 success criterion and lets the CLI be
        tested without a real long-running process.
        """
        self._write_pidfile()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            if once:
                loop.run_until_complete(self._drain_once())
            else:
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

    async def _drain_once(self) -> None:
        """Single bounded drain for ``run(once=True)`` — exactly one pipeline run.

        Enqueue ONE manual ``TriggerEvent``, drain the (singleton) burst, merge
        its paths, and run ``_debounce_and_run`` to completion (awaiting the
        configured debounce sleep). Exactly one ``_run_pipeline`` call happens;
        no poller threads, no signals, no unbounded loop. ``run``'s finally
        block then removes the pidfile and returns 0.
        """
        if self._queue is None:
            self._queue = asyncio.Queue()
        self._queue.put_nowait(
            TriggerEvent(source="manual", changed_paths=[], changed_only=False)
        )
        events = []
        while not self._queue.empty():
            events.append(self._queue.get_nowait())
        merged = [p for e in events for p in e.changed_paths]
        await self._debounce_and_run(merged)

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
        """Spawn trigger-source daemon threads (WatchLoop + VaultWatcher).

        Each source runs its poll body in a ``daemon=True`` thread gated by the
        shared ``stop_event`` (NO bare ``while True`` / ``KeyboardInterrupt``
        death) and pushes ``TriggerEvent``s onto the queue via the
        ``call_soon_threadsafe`` bridge. The watcher modules (``watch.py`` /
        ``vault_watch.py``) are reused, NOT rewritten — only their owning loop
        is replaced here. Every thread target wraps its body in a logged
        ``try/except`` so a poller dies LOUDLY without killing the daemon.
        """
        if self._enable_watch:
            self._start_watch_source(loop)
        if self._enable_vault:
            self._start_vault_source(loop)

    # ----- watch source ----------------------------------------------------

    def _start_watch_source(self, loop: asyncio.AbstractEventLoop) -> None:
        from ..watch import WatchLoop

        def on_change(paths) -> None:
            # Guard: drop late events fired during shutdown.
            if self._stop_event.is_set():
                return
            self._loop.call_soon_threadsafe(
                self._queue.put_nowait,
                TriggerEvent(
                    source="watch",
                    changed_paths=list(paths),
                    changed_only=True,
                ),
            )

        wl = WatchLoop(
            self.project_root,
            interval=self._watch_interval,
            on_change=on_change,
            quiet=True,
        )
        t = threading.Thread(
            target=self._run_watch_source,
            args=(wl,),
            daemon=True,
            name="watch-source",
        )
        t.start()
        self._threads.append(t)

    def _run_watch_source(self, wl) -> None:
        """Poll body for the WatchLoop source (replaces ``WatchLoop.run``).

        Reuses ``snapshot`` / ``diff`` / ``_combine`` / ``_trigger`` (which
        fires ``on_change`` -> enqueue). The owning loop is gated by
        ``stop_event`` instead of ``while True`` + ``KeyboardInterrupt``.
        """
        from ..watch import _combine

        try:
            previous = wl.snapshot()
            while not self._stop_event.is_set():
                time.sleep(wl.interval)
                if self._stop_event.is_set():
                    break
                current = wl.snapshot()
                added, modified, removed = wl.diff(previous, current)
                changed = list(_combine(added, modified, removed))
                if changed:
                    wl._trigger(changed)  # noqa: SLF001 - fires on_change -> enqueue
                previous = current
        except Exception:  # noqa: BLE001 - daemon survives a dead source
            logger.exception("watch-source thread died")

    # ----- vault source ----------------------------------------------------

    def _start_vault_source(self, loop: asyncio.AbstractEventLoop) -> None:
        from ..vault_watch import VaultWatcher

        try:
            from ..project import ProjectWiki

            wiki = ProjectWiki.load(self.project_root)
        except Exception:  # noqa: BLE001 - vault not ready: skip, don't crash
            logger.warning(
                "vault source unavailable (project/vault not ready); skipping",
                exc_info=True,
            )
            return

        watcher = VaultWatcher(wiki, poll_interval=self._vault_poll_interval)
        t = threading.Thread(
            target=self._run_vault_source,
            args=(watcher,),
            daemon=True,
            name="vault-source",
        )
        t.start()
        self._threads.append(t)

    def _run_vault_source(self, watcher) -> None:
        """Poll body for the VaultWatcher source (mirrors cli.py:629-657).

        Drives ``VaultWatcher._tick`` in a ``stop_event``-gated loop. ``_tick``
        already debounce-sleeps internally, so the outer loop's single
        ``time.sleep`` is sufficient (02-RESEARCH Pitfall 4 — no extra sleep).
        """
        try:
            while not self._stop_event.is_set():
                time.sleep(watcher.poll_interval)
                if self._stop_event.is_set():
                    break
                changed = watcher._tick()  # noqa: SLF001 - graceful-stop reuse
                if changed:
                    self._loop.call_soon_threadsafe(
                        self._queue.put_nowait,
                        TriggerEvent(source="vault_watch", changed_only=True),
                    )
        except Exception:  # noqa: BLE001 - daemon survives a dead source
            logger.exception("vault-source thread died")

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

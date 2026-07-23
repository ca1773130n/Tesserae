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
from contextlib import nullcontext
import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import pidlock

logger = logging.getLogger("tesserae.daemon")


def raise_fd_limit(target: int = 8192) -> int:
    """Raise RLIMIT_NOFILE's soft limit toward ``target``; return the new soft.

    macOS terminals default to a 256-fd soft limit — far too low for an engine
    that runs full compiles in-process while tailers tick over thousands of
    transcripts. Hitting the cap surfaces as ``sqlite3.OperationalError:
    unable to open database file`` storms. Best-effort: never raises.
    """
    try:
        import resource
    except ImportError:  # pragma: no cover — non-POSIX
        return -1
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        wanted = target if hard == resource.RLIM_INFINITY else min(hard, target)
        if soft < wanted:
            resource.setrlimit(resource.RLIMIT_NOFILE, (wanted, hard))
            logger.info("raised open-file soft limit %d -> %d", soft, wanted)
            return wanted
        return soft
    except (ValueError, OSError) as exc:  # pragma: no cover — platform quirk
        logger.warning("could not raise open-file limit: %s", exc)
        return -1


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
        session_poll_interval: float = 1.0,
        enable_watch: bool = True,
        enable_vault: bool = True,
        enable_session_tail: bool = True,
        consolidate: bool = True,
        consolidate_idle_seconds: float = 300.0,
        consolidate_max_interval_seconds: float = 21600.0,
        consolidate_check_interval: float = 30.0,
        summarize_budget: int = 25,
        install_signal_handlers: bool = True,
        compile_gate: Optional[threading.Semaphore] = None,
        run_pipeline: Optional[Callable[[List[Path]], None]] = None,
        monotonic: Optional[Callable[[], float]] = None,
        distill: Optional[Callable[..., dict]] = None,
        associate: Optional[Callable[..., dict]] = None,
        summary_client: Optional[object] = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.debounce = debounce
        self._queue_timeout = queue_timeout
        self._join_timeout = join_timeout
        self._watch_interval = watch_interval
        self._vault_poll_interval = vault_poll_interval
        self._session_poll_interval = session_poll_interval
        self._enable_watch = enable_watch
        self._enable_vault = enable_vault
        self._enable_session_tail = enable_session_tail
        self._consolidate = consolidate
        self._consolidate_idle_seconds = consolidate_idle_seconds
        self._consolidate_max_interval_seconds = consolidate_max_interval_seconds
        self._consolidate_check_interval = consolidate_check_interval
        self._summarize_budget = summarize_budget
        self._install_signal_handlers = install_signal_handlers
        # Default the compile gate to a private mutex so the consolidation
        # thread NEVER overlaps a compile even in single-daemon mode. Without
        # this, single mode leaves the gate as a nullcontext and the sleep-cycle
        # distill (which runs on its own thread) could race a pipeline run — the
        # fleet already shares a real Semaphore, so it was covered there.
        self._compile_gate = (
            compile_gate if compile_gate is not None else threading.Semaphore(1)
        )
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        self._distill_override = distill
        self._associate_override = associate
        self._summary_client_override = summary_client
        self._pidfile = self.project_root / ".tesserae" / self.PIDFILE_NAME
        self._stop_event = threading.Event()
        self._threads: List[threading.Thread] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queue: Optional[asyncio.Queue] = None
        self._run_pipeline_override = run_pipeline
        # Monotonic activity clock for the sleep-cycle trigger: updated on every
        # enqueue() and _run_pipeline(), read by the consolidation thread. A
        # float read/write is atomic under the GIL so no lock is needed (and a
        # lock here could deadlock against the compile gate). NEVER persisted —
        # wall-clock/mutable state in artifacts is the byte-idempotence blind spot.
        now = self._monotonic()
        self._last_activity = now
        self._last_consolidation = now

    # ----- thread -> loop bridge -------------------------------------------

    def enqueue(self, event: TriggerEvent) -> None:
        """Thread-safe bridge for poller threads (Plan 02) to feed the loop.

        ALL source-thread enqueues route through here. If shutdown has already
        closed/stopped the loop, ``call_soon_threadsafe`` raises ``RuntimeError``
        ("Event loop is closed" / loop not running); we catch it and log quietly
        so a late source stops cleanly instead of dying with an unhandled
        traceback (codex #6).
        """
        if self._loop is None or self._queue is None or self._stop_event.is_set():
            return
        # A real trigger event is activity: reset the idle clock so the
        # sleep-cycle consolidation only fires during genuine rest. Placed after
        # the guard so late events dropped during shutdown don't reset it.
        self._last_activity = self._monotonic()
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
        except RuntimeError:
            # Loop closed/stopped mid-shutdown: drop the late event quietly.
            logger.debug("enqueue after loop closed; dropping %s event", event.source)

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
                if self._install_signal_handlers:
                    for sig in (signal.SIGTERM, signal.SIGINT):
                        try:
                            loop.add_signal_handler(sig, self._handle_signal)
                        except NotImplementedError:
                            # Windows / non-main-thread: signals unavailable here.
                            logger.warning("add_signal_handler unavailable for %s; skipping", sig)
                self._start_sources(loop)
                # Sleep-cycle consolidation: idle + periodic agent-memory
                # distill on its own poller thread. Long-running mode only —
                # once=True (above) must never spawn poller threads.
                if self._consolidate:
                    self._start_consolidation(loop)
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

    def request_stop(self) -> None:
        """Thread-safe external stop (fleet supervisor / tests).

        Same effect as a SIGTERM: the drain loop notices the stop event within
        ``queue_timeout`` and exits via the graceful-drain path.

        No effect on ``run(once=True)``, which performs a single bounded drain
        regardless.
        """
        self._stop_event.set()

    # ----- drain loop ------------------------------------------------------

    async def _drain_loop(self) -> None:
        # Create the queue inside the running loop unless one was pre-installed
        # (the test seam pre-loads a burst before driving the drain directly).
        if self._queue is None:
            self._queue = asyncio.Queue()
        # Coalescing state lives OUTSIDE the cancellable debounce task so a
        # shutdown that cancels the in-flight debounce never drops the work
        # (codex #1). ``pending_paths`` is the union of every changed path that
        # has not yet been consumed by a *completed* run; ``run_pending`` marks
        # that at least one trigger is owed a pipeline run.
        pending: Optional[asyncio.Task] = None
        pending_paths: List[Path] = []
        run_pending = False
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
                # Cancelling does NOT drop the paths: they stay in pending_paths
                # and the freshly-scheduled debounce carries the full union.
                if pending is not None and not pending.done():
                    pending.cancel()
                pending_paths.extend(p for e in events for p in e.changed_paths)
                run_pending = True

                def _consume() -> None:
                    # Called by the debounce task SYNCHRONOUSLY after the pipeline
                    # actually ran (never reached if the debounce was cancelled
                    # during its sleep). Clearing here — not in a done-callback —
                    # avoids a shutdown race where the task is done() but its
                    # callback hasn't fired yet, which would trigger a spurious
                    # second final run.
                    nonlocal run_pending
                    pending_paths.clear()
                    run_pending = False

                pending = asyncio.create_task(
                    self._debounce_and_run(list(pending_paths), on_consumed=_consume)
                )
        finally:
            # Graceful drain: pull in any events that landed after the last
            # loop iteration so the FINAL run sees the full union of paths.
            while self._queue is not None and not self._queue.empty():
                ev = self._queue.get_nowait()
                pending_paths.extend(ev.changed_paths)
                run_pending = True
            # If a debounce was in flight, cancel it (we run one final coalesced
            # pass immediately rather than waiting out its debounce sleep) and
            # absorb its result so no "never retrieved" warning leaks.
            if pending is not None:
                if not pending.done():
                    pending.cancel()
                try:
                    await pending
                except asyncio.CancelledError:
                    pass
                except Exception as exc:  # noqa: BLE001 - daemon survives
                    logger.error(
                        "pipeline raised outside StepResult (daemon survives): %s", exc
                    )
            # Run exactly ONE final coalesced pipeline for the queued+pending
            # triggers that never got their run. A failure here must still let
            # the daemon exit cleanly (exception survival).
            if run_pending:
                try:
                    self._run_pipeline(list(pending_paths))
                except Exception as exc:  # noqa: BLE001 - daemon survives
                    logger.error(
                        "final-drain pipeline raised (daemon exits cleanly): %s", exc
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

    async def _debounce_and_run(
        self, paths: List[Path], on_consumed: Optional[Callable[[], None]] = None
    ) -> None:
        await asyncio.sleep(self.debounce)
        try:
            self._run_pipeline(paths)
        finally:
            # Mark these paths consumed once the pipeline was ATTEMPTED — even if
            # it raised, the run happened, so it must not be retried in the final
            # drain (preserves exception-survival). If the debounce was cancelled
            # during the sleep above we never reach here, so the paths survive
            # into the next / final coalesced run (codex #1).
            if on_consumed is not None:
                on_consumed()

    def _run_pipeline(self, paths: List[Path]) -> None:
        # A pipeline run is activity: reset the idle clock so idle-triggered
        # consolidation does not fire immediately after a compile.
        self._last_activity = self._monotonic()
        gate = self._compile_gate if self._compile_gate is not None else nullcontext()
        with gate:
            # Visibility: without this line a long compile looks like a hang —
            # the only other logs are step results AFTER it finishes.
            logger.info(
                "pipeline starting for %s (%d changed paths)",
                self.project_root.name,
                len(paths),
            )
            if self._run_pipeline_override is not None:
                self._run_pipeline_override(paths)
                return
            from .pipeline import Pipeline
            from ..project import ProjectWiki

            wiki = ProjectWiki.load(self.project_root)
            # Forward the coalesced changed-path set into compile (CMP-04) instead
            # of dropping it — the provenance-driven differ trusts this explicit set
            # over the manifest re-scan.
            steps = [
                (
                    "compile",
                    lambda: wiki.compile(
                        changed_only=bool(paths), changed_paths=paths or None
                    ),
                )
            ]
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
        if self._enable_session_tail:
            self._start_session_source(loop)

    # ----- watch source ----------------------------------------------------

    def _start_watch_source(self, loop: asyncio.AbstractEventLoop) -> None:
        from ..watch import WatchLoop

        def on_change(paths) -> None:
            # Guard: drop late events fired during shutdown. Route through the
            # enqueue() bridge so a closing loop never crashes the thread.
            if self._stop_event.is_set():
                return
            self.enqueue(
                TriggerEvent(
                    source="watch",
                    changed_paths=list(paths),
                    changed_only=True,
                )
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
                # stop_event.wait() so a stopping daemon wakes promptly instead
                # of sleeping out the full interval (codex #6).
                if self._stop_event.wait(wl.interval):
                    break
                current = wl.snapshot()
                added, modified, removed = wl.diff(previous, current)
                changed = list(_combine(added, modified, removed))
                # Re-check stop_event before triggering so a stop between the
                # wait and the enqueue does not push into a closing loop.
                if changed and not self._stop_event.is_set():
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
                # stop_event.wait() wakes promptly on shutdown (codex #6).
                if self._stop_event.wait(watcher.poll_interval):
                    break
                changed = watcher._tick()  # noqa: SLF001 - graceful-stop reuse
                # Re-check stop_event immediately after _tick(): if shutdown
                # raced in, do not enqueue into a closing loop.
                if changed and not self._stop_event.is_set():
                    self.enqueue(
                        TriggerEvent(source="vault_watch", changed_only=True)
                    )
        except Exception:  # noqa: BLE001 - daemon survives a dead source
            logger.exception("vault-source thread died")

    # ----- session-tail source --------------------------------------------

    def _start_session_source(self, loop: asyncio.AbstractEventLoop) -> None:
        """Third trigger source: live transcript tailer (mirrors watch/vault).

        Mirrors :meth:`_start_vault_source`'s "not ready -> skip, don't crash"
        contract — if the tailer/store cannot be built the daemon keeps running
        without a session source. The tailer writes the live
        ``HarnessSessionsDB`` BEFORE the callback enqueues, so the debounced
        compile reads correct state (store-before-enqueue, 03-RESEARCH).
        """
        from ..harness_sessions_db import HarnessSessionsDB
        from .session_tail import SessionTailer

        def on_new_turns(path, turns) -> None:
            # Guard: drop late callbacks fired during shutdown. Route through
            # the enqueue() bridge so a closing loop never crashes the thread.
            if self._stop_event.is_set():
                return
            self.enqueue(
                TriggerEvent(
                    source="session_tail",
                    changed_paths=[path],
                    changed_only=True,
                )
            )

        # Daily chunk writer (session_chunks.db): best-effort optimization —
        # if the store cannot be built the tailer simply runs without it and
        # the activity summary keeps its raw-scan path. Never blocks startup.
        chunk_writer = None
        try:
            from ..session_chunks import (
                SessionChunksDB,
                chunks_db_path,
                record_live_turns,
            )

            chunks_db = SessionChunksDB(chunks_db_path(self.project_root))

            def chunk_writer(harness, path, session_key, turns, _db=chunks_db):
                # record_live_turns is itself never-raise; this wrapper only
                # binds the db handle.
                record_live_turns(_db, harness, path, session_key, turns)

        except Exception:  # noqa: BLE001 - chunk store not ready: run without it
            logger.warning(
                "session-chunks writer unavailable; live day-chunking disabled",
                exc_info=True,
            )
            chunk_writer = None

        try:
            db = HarnessSessionsDB(
                self.project_root / ".tesserae" / "harness_sessions.db"
            )
            tailer = SessionTailer(
                project_root=self.project_root,
                sessions_db=db,
                poll_interval=self._session_poll_interval,
                on_new_turns=on_new_turns,
                on_chunk_turns=chunk_writer,
            )
        except Exception:  # noqa: BLE001 - tailer not ready: skip, don't crash
            logger.warning(
                "session-tail source unavailable (store/tailer not ready); skipping",
                exc_info=True,
            )
            return

        t = threading.Thread(
            target=self._run_session_source,
            args=(tailer,),
            daemon=True,
            name="session-tail",
        )
        t.start()
        self._threads.append(t)

    def _run_session_source(self, tailer) -> None:
        """Poll body for the SessionTailer source (mirrors ``_run_vault_source``).

        Drives ``tailer.tick()`` in a ``stop_event``-gated loop. The whole body
        is wrapped in a logged ``try/except`` so a dead source dies LOUDLY
        without killing the daemon (KNOWHOW anti-pattern: no silent thread death).
        """
        try:
            while not self._stop_event.is_set():
                # stop_event.wait() wakes promptly on shutdown (codex #6). The
                # tailer enqueues via on_new_turns -> enqueue(), which already
                # guards a closing loop; re-check stop_event before ticking so a
                # stop between wait and tick skips the (enqueuing) tick body.
                if self._stop_event.wait(tailer.poll_interval):
                    break
                if self._stop_event.is_set():
                    break
                tailer.tick()
        except Exception:  # noqa: BLE001 - daemon survives a dead source
            logger.exception("session-tail thread died")

    # ----- sleep-cycle consolidation ---------------------------------------

    def _start_consolidation(self, loop: asyncio.AbstractEventLoop) -> None:
        """Spawn the sleep-cycle consolidation thread (idle + periodic distill).

        The brain-sleep analogy: agent memory is consolidated during *rest*,
        not while work is in flight. Mirrors the poller-thread idiom
        (:meth:`_start_watch_source`) — one ``daemon=True`` thread gated by the
        shared ``stop_event`` that wakes every ``consolidate_check_interval``
        and, when the project has been idle long enough (or the max interval has
        elapsed), runs one consolidation pass UNDER the compile gate. ``loop`` is
        unused (kept for signature parity with the other ``_start_*`` sources).
        """
        t = threading.Thread(
            target=self._run_consolidation,
            daemon=True,
            name="consolidation",
        )
        t.start()
        self._threads.append(t)

    def _run_consolidation(self) -> None:
        """Poll body for the consolidation source (mirrors ``_run_watch_source``).

        Wakes every ``consolidate_check_interval`` and evaluates the idle /
        periodic trigger against the monotonic activity clock. ``stop_event``
        wakes it promptly on shutdown. A dead thread dies LOUDLY (logged)
        without killing the daemon; a single failed consolidation is contained
        by :meth:`_consolidation_tick` and never breaks the loop.
        """
        try:
            while not self._stop_event.is_set():
                # stop_event.wait() so a stopping daemon wakes promptly instead
                # of sleeping out the full check interval (codex #6).
                if self._stop_event.wait(self._consolidate_check_interval):
                    break
                self._consolidation_tick()
        except Exception:  # noqa: BLE001 - daemon survives a dead source
            logger.exception("consolidation thread died")

    def _consolidation_tick(self) -> None:
        """Evaluate the sleep-cycle trigger once; consolidate if due.

        Two independent triggers, both measured on the monotonic clock:

        * IDLE — no trigger event and no pipeline run for
          ``consolidate_idle_seconds`` AND at least that long since the last
          consolidation (the anti-thrash floor uses ``_last_consolidation``, not
          ``_last_activity``, so a busy project that just went quiet cannot
          re-fire immediately). This is the "consolidate during rest" path.
        * CEILING — ``consolidate_max_interval_seconds`` elapsed since the last
          consolidation regardless of activity (``0`` disables it). Guarantees a
          periodic pass on a project that never goes quiet.

        Runs UNDER the compile gate so it serializes with — and never overlaps —
        a compile. Never raises; ``_last_consolidation`` is stamped after every
        attempt (in a ``finally``) so a due-but-failed pass cannot hot-loop.
        """
        now = self._monotonic()
        since_activity = now - self._last_activity
        since_consolidation = now - self._last_consolidation
        idle = (
            since_activity >= self._consolidate_idle_seconds
            and since_consolidation >= self._consolidate_idle_seconds
        )
        ceiling = (
            self._consolidate_max_interval_seconds > 0
            and since_consolidation >= self._consolidate_max_interval_seconds
        )
        if not (idle or ceiling):
            return
        if self._stop_event.is_set():
            return
        gate = self._compile_gate if self._compile_gate is not None else nullcontext()
        try:
            with gate:
                self._consolidate_once()
        except Exception as exc:  # noqa: BLE001 - consolidation never kills the loop
            logger.error("consolidation raised (daemon survives): %s", exc)
        finally:
            self._last_consolidation = self._monotonic()

    def _consolidate_once(self) -> None:
        """Load the compiled graph and run one agent-memory consolidation pass.

        Three operations, in order, on the SAME loaded graph:

        1. DISTILL (compress/forget) — mirrors ``cli.py``'s ``step_agent_distill``
           refresh path: load ``.tesserae/graph.json`` (skip if absent) and call
           :func:`maybe_distill_on_refresh`, which is triple-gated internally
           (``TESSERAE_AGENT_DISTILL`` opt-in, per-agent watermark, per-agent
           memory pressure) and never raises for per-agent failures — a safe
           no-op whenever the distill gate is off.
        2. ASSOCIATE (discover connections) — the third sleep-cycle operation:
           :func:`tesserae.memory.associate.consolidate_associations` finds new
           embedding-similar links and accumulates them into a ``.tesserae``
           sidecar overlay (NEVER ``graph.json``). It resolves the app's semantic
           backend (:meth:`_resolve_embedding_backend`); with no real backend it
           skips honestly. It never raises, but the call is wrapped anyway so an
           unexpected failure — or a test stub that throws — cannot break the tick.
        3. SUMMARIZE (pre-warm) — the Descent sleep-cycle operation (§6.4, PR7):
           :meth:`_summarize_once` spends a per-tick LLM-call budget warming
           community-summary caches for the scopes agents demand most (ranked by
           ``node_memory`` access bumps from ``graph_map``), so a later descent
           finds a warm cache instead of paying a synchronous LLM call. Honest
           no-op without a hierarchy sidecar or an LLM client.

        Runs under the compile gate (held by the caller).
        """
        graph_path = self.project_root / ".tesserae" / "graph.json"
        if not graph_path.is_file():
            logger.debug(
                "consolidation: no compiled graph at %s; skipping", graph_path
            )
            return
        from ..project import load_graph_file

        graph = load_graph_file(graph_path)
        cfg = self._load_config()
        if self._distill_override is not None:
            distill = self._distill_override
        else:
            from ..agent_distill import maybe_distill_on_refresh

            distill = maybe_distill_on_refresh
        summary = distill(self.project_root, graph, cfg=cfg, env=os.environ)
        logger.info("consolidation for %s: %s", self.project_root.name, summary)

        # Associate runs AFTER distill, on the same graph, under the same gate.
        # It must never raise into the tick — consolidate_associations is already
        # non-raising, but the wrap covers backend resolution and injected stubs.
        if self._associate_override is not None:
            associate = self._associate_override
        else:
            from ..memory.associate import consolidate_associations

            associate = consolidate_associations
        try:
            backend = self._resolve_embedding_backend()
            assoc = associate(self.project_root, graph, backend=backend)
            logger.info("association for %s: %s", self.project_root.name, assoc)
        except Exception as exc:  # noqa: BLE001 - associate never kills the tick
            logger.error("association raised (daemon survives): %s", exc)

        # Summarize runs LAST, on the same graph, under the same gate.
        # _summarize_once already degrades every failure mode to a summary
        # dict, but the wrap covers injected stubs and import edges anyway.
        try:
            warm = self._summarize_once(graph)
            logger.info("summarize for %s: %s", self.project_root.name, warm)
        except Exception as exc:  # noqa: BLE001 - summarize never kills the tick
            logger.error("summarize raised (daemon survives): %s", exc)

    def _summarize_once(self, graph) -> dict:
        """Pre-warm community-summary caches by demand (SUMMARIZE, Descent §6.4).

        Within a per-tick LLM-call budget (``summarize_budget``, default 25;
        ``0`` disables the op) lazily materialize summaries for the communities
        agents are most likely to descend into next, so their first ``graph_map``
        visit finds a warm cache instead of paying a synchronous LLM call.
        Candidates are every community in the hierarchy sidecar at its
        canonical (coarsest) occurrence, ranked by demand — Σ
        ``node_memory.access_count`` over members (populated by ``graph_map``
        bumps) — tie-broken by member count, then summed member degree, then
        coarsest level first, then cid. Warm (digest-valid) caches, in-graph
        COMMUNITY_SUMMARY scopes (compile-owned) and singletons cost no budget;
        only cold materializations do — each via
        :func:`~tesserae.community_summaries.materialize_community_summary`,
        the exact single-call path ``graph_map`` uses (same level-scoped cache
        layout, citation discipline, atomic writes). Summaries are caches, not
        knowledge: nothing here touches ``graph.json``. Honest no-op without a
        hierarchy sidecar or an LLM client; never raises — every outcome is a
        summary dict for the tick log.
        """
        budget = max(0, int(self._summarize_budget))
        if budget == 0:
            return {"summarized": [], "skipped": "budget=0"}
        try:
            from ..hierarchy import load_hierarchy, undirected_degrees

            hierarchy = load_hierarchy(self.project_root)
        except ValueError:
            return {"summarized": [], "skipped": "no hierarchy sidecar"}
        if not hierarchy.levels:
            return {"summarized": [], "skipped": "empty hierarchy"}
        client = self._resolve_summary_client()
        if client is None:
            return {"summarized": [], "skipped": "no LLM client"}

        from ..community_summaries import (
            materialize_community_summary,
            read_warm_summary,
        )
        from ..research_graph import ResearchNodeType

        access = self._read_access_counts()
        by_id = {n.id: n for n in graph.nodes}
        degrees = undirected_degrees(graph)
        # One canonical (level, members) per cid — the coarsest occurrence,
        # matching Hierarchy.find_scope and therefore the level-scoped cache
        # path graph_map reads (a community unchanged between adjacent levels
        # repeats its membership hash; warming it once is enough).
        scopes: Dict[str, Tuple[int, List[str]]] = {}
        for level in range(len(hierarchy.levels) - 1, -1, -1):
            for cid, members in hierarchy.levels[level].items():
                scopes.setdefault(cid, (level, members))
        ranked = sorted(
            scopes.items(),
            key=lambda kv: (
                -sum(access.get(m, 0) for m in kv[1][1]),
                -len(kv[1][1]),
                -sum(degrees.get(m, 0) for m in kv[1][1]),
                -kv[1][0],
                kv[0],
            ),
        )
        cache_dir = self.project_root / ".tesserae" / "community_summaries"
        summarized: List[str] = []
        failed: List[str] = []
        warm = 0
        attempted = 0
        for cid, (level, members) in ranked:
            if attempted >= budget:
                break
            if len(members) < 2:
                continue  # a singleton "community" card is just the node
            node = by_id.get(cid)
            if node is not None and node.type is ResearchNodeType.COMMUNITY_SUMMARY:
                warm += 1  # compile-owned coarsest summary, already in-graph
                continue
            present = [by_id[m] for m in members if m in by_id]
            if not present:
                continue
            if read_warm_summary(cache_dir, level, cid, present) is not None:
                warm += 1  # digest-valid cache — free, costs no budget
                continue
            children = hierarchy.children(cid)
            child_cids = [child_cid for child_cid, _ in children[0]] if children else []
            attempted += 1
            result = materialize_community_summary(
                present,
                cid=cid,
                member_ids=members,
                level=level,
                cache_dir=cache_dir,
                json_client=client,
                child_cids=child_cids,
            )
            if result is not None:
                summarized.append(cid)
            else:
                failed.append(cid)
        return {
            "summarized": summarized,
            "failed": failed,
            "warm": warm,
            "attempted": attempted,
            "budget": budget,
        }

    def _resolve_summary_client(self) -> Optional[object]:
        """LLM JSON client for the SUMMARIZE op, or ``None`` for an honest no-op.

        The constructor seam (``summary_client=``) wins so tests inject a fake
        client; otherwise resolution mirrors
        ``mcp_server._community_summary_json_client``: the community-summaries
        test client, then the ``TESSERAE_COMMUNITY_SUMMARIES`` opt-out, then
        the default client — memoized including a ``None``/failed build so a
        clientless environment costs nothing per tick. Never raises.
        """
        if self._summary_client_override is not None:
            return self._summary_client_override
        try:
            from ..community_summaries import is_enabled_via_env
            from ..project import _get_community_summaries_test_client

            injected = _get_community_summaries_test_client()
            if injected is not None:
                return injected
            if not is_enabled_via_env():
                return None
            if not hasattr(self, "_default_summary_client"):
                from ..llm_json import build_default_json_client

                self._default_summary_client = build_default_json_client()
            return self._default_summary_client
        except Exception as exc:  # noqa: BLE001 - no client -> honest no-op
            logger.debug("no LLM client for summarize: %s", exc)
            return None

    def _read_access_counts(self) -> Dict[str, int]:
        """``access_count`` per node id from the node_memory sidecar, or empty.

        Reads only when ``.tesserae/sqlite.db`` already exists — the demand
        signal comes from ``graph_map`` bumps, so a missing sidecar means no
        demand yet, and creating the db from the consolidation thread would be
        a side-effect write the tick has no business making. Never raises;
        an unreadable sidecar degrades to size/degree ranking.
        """
        db_path = self.project_root / ".tesserae" / "sqlite.db"
        if not db_path.is_file():
            return {}
        try:
            from ..memory.store import read_memory

            return {
                node_id: int(row.access_count)
                for node_id, row in read_memory(db_path).items()
            }
        except Exception as exc:  # noqa: BLE001 - demand signal is best-effort
            logger.debug("summarize: node_memory unreadable: %s", exc)
            return {}

    def _resolve_embedding_backend(self):
        """Resolve the app's semantic embedding backend, or ``None`` when absent.

        Resolves exactly the way semantic features do
        (:func:`tesserae.retrieval.hybrid.active_embedding_backend`) and collapses
        the non-semantic hash-bucket stub to ``None`` so the associate pass skips
        honestly instead of discovering noise links off a stub model. Any
        resolution failure degrades to ``None`` (never raises).
        """
        try:
            from ..retrieval.hybrid import (
                HashEmbeddingBackend,
                active_embedding_backend,
            )

            backend = active_embedding_backend()
            if isinstance(backend, HashEmbeddingBackend):
                return None
            return backend
        except Exception as exc:  # noqa: BLE001 - unavailable backend -> honest skip
            logger.debug("no semantic backend for association: %s", exc)
            return None

    def _load_config(self) -> Optional[dict]:
        """Best-effort read of ``.tesserae/config.json`` for the distill opt-in.

        The agent-distill gate reads ``TESSERAE_AGENT_DISTILL`` from the env
        first and only falls back to ``cfg['agent_distill']['enabled']``, so a
        missing or unparseable config degrades safely to env-only gating. Never
        raises.
        """
        config_path = self.project_root / ".tesserae" / "config.json"
        if not config_path.is_file():
            return None
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    # ----- pidfile ---------------------------------------------------------

    def _write_pidfile(self) -> None:
        if self._pidfile.exists():
            owner = pidlock.read_owner(self._pidfile)
            old_pid = owner.get("pid") if owner else None
            # Only refuse to start when the recorded process is genuinely
            # alive: a live PID whose start time no longer matches (PID reuse)
            # or a dead PID is stale and overwrite-safe. Unknown identity
            # degrades to a plain liveness check (conservative; see pidlock).
            if pidlock.owner_is_alive(owner):
                raise RuntimeError(f"Daemon already running (pid {old_pid})")
            if old_pid is not None:
                logger.warning("Stale pidfile (pid %s); overwriting.", old_pid)
        self._pidfile.parent.mkdir(parents=True, exist_ok=True)
        self._pidfile.write_text(pidlock.serialize())

    def _remove_pidfile(self) -> None:
        try:
            self._pidfile.unlink()
        except FileNotFoundError:
            pass

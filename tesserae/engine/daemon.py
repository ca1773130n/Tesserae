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
from contextlib import contextmanager, nullcontext
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


#: Ceiling on the BRIEF op's per-domain retry backoff, in consolidation ticks.
#: A domain that keeps failing doubles its wait (2, 4, 8 … ticks) up to this
#: cap. At the default ``consolidate_idle_seconds=300`` a tick is ~5 minutes,
#: so 64 ticks is a retry roughly every 5 hours: often enough that a domain
#: cold only because the provider was down recovers on its own, rare enough
#: that a permanently un-warmable one costs a handful of calls a day instead
#: of the 96/hour an un-backed-off retry at rank 1 would cost.
_BRIEF_MAX_BACKOFF_TICKS = 64


def _is_census_domain(entry: dict) -> bool:
    """Is this charter record the intake census rather than a subject?

    ``build_charter`` labels every tier-1 domain ``own_altitude: "division"``
    via ``_altitude_for``, with exactly ONE deliberate exception: the intake
    domain, which is written ``tier: 1`` / ``own_altitude: "team"`` and an
    empty anchor because it is "a census of everything structure could not
    route" (charter.py:1015-1041). So this pair is not a heuristic — it is the
    writer's own recorded statement that the domain has no subject.

    Such a domain must never be briefed. Measured at 7,581 members, a brief
    would be written from the 25 the prompt can hold — 0.33% of it — and then
    served at ``quality: "llm"``, which reads as an authoritative description
    of the whole bucket. The re-scope roadmap ruled on this directly: "Do not
    promise a census brief that cannot be rendered"
    (docs/superpowers/specs/2026-08-14-charter-rescope-roadmap.md:155). The
    structural card it keeps — a count plus top members by degree, with the
    count visible and rising — is the honest artifact, and the count is the
    standing extraction-quality lint intake exists to be.
    """
    return (
        int(entry.get("tier") or 1) == 1
        and str(entry.get("own_altitude") or "") == "team"
        and not str(entry.get("anchor_id") or "")
    )


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

    #: Host-agnostic pidfile name. Still used verbatim when no host id can be
    #: determined — see :meth:`_pidfile_name`.
    PIDFILE_NAME = "daemon.pid"

    #: First and largest wait between re-attempts of a compile that found the
    #: per-project compile lock held by someone else (see :meth:`_run_pipeline`).
    DEFER_BACKOFF_START = 2.0
    DEFER_BACKOFF_MAX = 60.0

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
        enable_compile: Optional[bool] = None,
        consolidate: bool = True,
        consolidate_idle_seconds: float = 300.0,
        consolidate_max_interval_seconds: float = 21600.0,
        consolidate_check_interval: float = 30.0,
        summarize_budget: int = 25,
        brief_budget: int = 8,
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
        # Harvest-only mode. With both content watchers off there is nothing a
        # compile here could pick up that the compiling host will not see
        # anyway, and on a fleet of servers sharing one disk the per-project
        # compile lock is the contended resource: N-1 hosts should tail their
        # own local transcripts into the shared sessions store and never reach
        # for it. Inferred rather than required so no existing caller changes;
        # pass ``enable_compile=`` explicitly to override the inference.
        self._enable_compile = (
            bool(enable_watch or enable_vault)
            if enable_compile is None
            else bool(enable_compile)
        )
        self._consolidate = consolidate
        self._consolidate_idle_seconds = consolidate_idle_seconds
        self._consolidate_max_interval_seconds = consolidate_max_interval_seconds
        self._consolidate_check_interval = consolidate_check_interval
        self._summarize_budget = summarize_budget
        self._brief_budget = brief_budget
        # BRIEF back-off state. Consecutive failures per slug and the tick each
        # one becomes eligible again, so a domain that cannot be warmed stops
        # holding the budget slot a warmable one could use. In memory ONLY and
        # never persisted, for the same reason ``_last_activity`` is not:
        # mutable/wall-clock state inside an artifact is this repo's
        # byte-idempotence blind spot. A restart simply retries everything,
        # which is the right default for state whose worst case is one extra
        # LLM call per domain.
        self._brief_tick = 0
        self._brief_failures: Dict[str, int] = {}
        self._brief_retry_at: Dict[str, int] = {}
        self._install_signal_handlers = install_signal_handlers
        # Default the compile gate to a private mutex so the consolidation
        # thread NEVER overlaps a compile even in single-daemon mode. Without
        # this, single mode leaves the gate as a nullcontext and the sleep-cycle
        # distill (which runs on its own thread) could race a pipeline run — the
        # fleet already shares a real Semaphore, so it was covered there.
        self._compile_gate = (
            compile_gate if compile_gate is not None else threading.Semaphore(1)
        )
        # "A pipeline run is waiting for the compile gate." Set by
        # :meth:`_gate_for_pipeline` immediately before it blocks on the gate and
        # cleared by that SAME context manager the moment the gate is in hand —
        # so the flag's whole lifetime is one gate acquisition, and every set is
        # paired with a clear in a ``finally``. It CANNOT latch: nothing else
        # writes it, and a set that is never followed by an acquisition is not
        # reachable (the acquisition is the next statement).
        #
        # The pre-warm loops (:meth:`_summarize_once`, :meth:`_brief_once`) read
        # it at the top of each iteration and abandon their remaining budget when
        # it is set. That is the ONLY thing it does. It never releases the gate
        # mid-pass — a tick must read one consistent ``graph.json``, so yielding
        # the gate between calls would let a compile rewrite the graph underneath
        # and leave early briefs describing a different graph than late ones.
        # Abandoning finishes the pass early instead, which costs nothing:
        # warming is idempotent, and a domain that was never tried is simply
        # still cold on the next tick.
        self._pipeline_pending = threading.Event()
        self._monotonic = monotonic if monotonic is not None else time.monotonic
        self._distill_override = distill
        self._associate_override = associate
        self._summary_client_override = summary_client
        self._pidfile = self.project_root / ".tesserae" / self._pidfile_name()
        self._stop_event = threading.Event()
        # Backoff state for a compile deferred because another process holds
        # the per-project compile lock. ``_defer_until`` is a monotonic
        # deadline that EVERY attempt waits out, including one scheduled by a
        # fresh burst of triggers — without that, the session tailer (which
        # fires while an agent is writing, i.e. exactly while a human compile
        # is running) would re-attempt ProjectWiki.load + a config read + a
        # flock syscall once per debounce for the whole compile, on every host.
        self._defer_until = 0.0
        self._defer_delay = 0.0
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

    @classmethod
    def _pidfile_name(cls) -> str:
        """``daemon.<host>.pid`` — the pidfile name scoped to this machine.

        Several servers each running their own agent sessions can share a
        disk, and therefore share one ``.tesserae/`` directory. A single
        ``daemon.pid`` there is written by all of them, while liveness is
        decided by ``os.kill(pid, 0)`` against the LOCAL process table
        (:mod:`tesserae.engine.pidlock`) — so a PID recorded by another
        machine is judged alive or dead by whatever unrelated process happens
        to hold that number here, and a host either refuses to start behind a
        stranger's pid or clobbers a live daemon's file. Scoping the NAME
        keeps the pidfile in the shared, greppable project directory (that
        part was never the bug) while giving each machine its own.

        Degrades to the legacy :attr:`PIDFILE_NAME` if no host id can be
        determined, so a machine whose home is unwritable keeps exactly
        today's single-host behaviour rather than failing to start.
        """
        try:
            from ..harness_sessions import local_host_id

            host = local_host_id().strip()
        except Exception as exc:  # noqa: BLE001 — naming a file must not block startup
            logger.debug("no host id available (%s); using %s", exc, cls.PIDFILE_NAME)
            host = ""
        return f"daemon.{host}.pid" if host else cls.PIDFILE_NAME

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
            # the daemon exit cleanly (exception survival). A DEFERRED return is
            # ignored on purpose: we are shutting down, so there is nothing to
            # retry into — the next start's compile picks these files up through
            # the manifest differ.
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
        # ``retry_deferred=False``: a one-shot run must terminate. If another
        # process holds the compile lock, say so once and exit rather than
        # waiting it out — the caller asked for one bounded pass, and blocking
        # a CI invocation behind a human's multi-minute compile is worse than
        # returning and letting the next run pick the work up via the manifest
        # differ.
        await self._debounce_and_run(merged, retry_deferred=False)

    async def _debounce_and_run(
        self,
        paths: List[Path],
        on_consumed: Optional[Callable[[], None]] = None,
        *,
        retry_deferred: bool = True,
    ) -> None:
        await asyncio.sleep(self.debounce)
        while True:
            # Wait out any deferral left by an earlier attempt that found the
            # compile lock held. Re-read the deadline each pass: a burst that
            # cancelled a deferred task and re-scheduled this one must serve
            # the SAME backoff, otherwise the retry degenerates into a
            # once-per-debounce spin against a multi-minute human compile.
            remaining = self._defer_until - self._monotonic()
            if remaining > 0 and retry_deferred:
                await asyncio.sleep(remaining)
                continue
            ran = True
            try:
                ran = self._run_pipeline(paths)
            finally:
                # Mark these paths consumed once the pipeline was ATTEMPTED —
                # even if it raised, the run happened, so it must not be retried
                # in the final drain (preserves exception-survival). A DEFERRED
                # attempt is not an attempt: nothing ran, so the paths stay
                # pending and this task loops round to retry them. If the
                # debounce was cancelled during a sleep above we never reach
                # here, so the paths survive into the next / final coalesced
                # run (codex #1).
                if ran and on_consumed is not None:
                    on_consumed()
            if ran or not retry_deferred:
                return

    def _run_pipeline(self, paths: List[Path]) -> bool:
        """Run one coalesced pipeline; ``False`` means DEFERRED, not done.

        A ``False`` return says only that another process holds the project's
        compile lock and this batch has been rescheduled behind
        :attr:`_defer_until` — the caller must keep ``paths`` pending. Every
        other outcome, including a step that failed, returns ``True``.
        """
        # A pipeline run is activity: reset the idle clock so idle-triggered
        # consolidation does not fire immediately after a compile.
        self._last_activity = self._monotonic()
        if not self._enable_compile and self._run_pipeline_override is None:
            # Harvest-only: this host tails transcripts into the shared sessions
            # store and leaves compiling to the host that owns it, so it must
            # not even reach for the compile lock. Nothing is lost — the
            # compiling host's next incremental compile falls back to the
            # manifest differ and still sees these files. The injected
            # ``run_pipeline`` seam is exempt: supplying one is an explicit
            # statement that THAT is this daemon's pipeline.
            logger.debug(
                "compile disabled (harvest-only); leaving %d changed paths to "
                "the compiling host",
                len(paths),
            )
            return True
        with self._gate_for_pipeline():
            # Visibility: without this line a long compile looks like a hang —
            # the only other logs are step results AFTER it finishes.
            logger.info(
                "pipeline starting for %s (%d changed paths)",
                self.project_root.name,
                len(paths),
            )
            if self._run_pipeline_override is not None:
                self._run_pipeline_override(paths)
                return True
            from .pipeline import Pipeline
            from ..locking import CompileLockHeldError
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
                return True
            for r in results:
                if r.ok:
                    logger.info("step %s: ok", r.name)
                elif isinstance(r.error, CompileLockHeldError):
                    # Someone else — usually a human's interactive `tesserae
                    # compile` — holds the per-project lock. That is normal
                    # traffic, not a fault, so it logs at info; and the batch is
                    # deferred rather than dropped, because dropping the trigger
                    # is how a session's turns sit unincorporated until the next
                    # unrelated change happens to fire one.
                    self._defer(r.error)
                    return False
                else:
                    logger.error("step %s: FAILED: %s", r.name, r.error)
            # A pipeline that got the lock clears the backoff, so the next
            # contended run starts from DEFER_BACKOFF_START rather than
            # inheriting a minute-long wait earned hours ago.
            self._defer_until = 0.0
            self._defer_delay = 0.0
            return True

    @contextmanager
    def _gate_for_pipeline(self):
        """Take the compile gate for a pipeline run, asking a tick to stand down.

        Identical to ``with self._compile_gate:`` except that it raises
        :attr:`_pipeline_pending` for exactly as long as this call is BLOCKED on
        the gate. A consolidation tick holding the gate can spend up to
        ``summarize_budget + brief_budget`` sequential LLM calls (33 at the
        defaults); with a CLI provider that is minutes, and a file save arriving
        mid-tick used to wait out every remaining call. The pre-warm loops read
        the flag at the top of each iteration and abandon their remaining budget,
        so the wait is now bounded by the ONE call already in flight.

        WHO CLEARS IT, and when — the whole anti-latch argument:

        * The flag is cleared as the first statement INSIDE the gate, before any
          pipeline work. From the instant this run owns the gate, the next tick
          must see a clear flag, otherwise it would abandon spuriously on every
          pass that follows a compile.
        * The ``finally`` clears it again, so the acquisition raising (or the
          ``nullcontext`` branch changing shape later) still cannot leave it set.

        A stuck flag would permanently disable both pre-warm ops, which is worse
        than the latency it fixes — hence set and clear in one place, one scope,
        with no other writer anywhere in the class.
        """
        gate = self._compile_gate if self._compile_gate is not None else nullcontext()
        self._pipeline_pending.set()
        try:
            with gate:
                self._pipeline_pending.clear()
                yield
        finally:
            self._pipeline_pending.clear()

    def _defer(self, exc: BaseException) -> None:
        """Push the next compile attempt out by an exponentially growing wait.

        Capped at :attr:`DEFER_BACKOFF_MAX` so a compile that outlives the
        backoff ladder is still retried about once a minute. The cost this
        bounds is not the compile — it is ``ProjectWiki.load`` + a config read
        + a flock syscall per attempt, paid by every host in the fleet, for as
        long as one human compile runs.
        """
        self._defer_delay = min(
            self.DEFER_BACKOFF_MAX,
            self._defer_delay * 2 if self._defer_delay else self.DEFER_BACKOFF_START,
        )
        self._defer_until = self._monotonic() + self._defer_delay
        logger.info(
            "compile lock held for %s (%s); deferring this batch %.0fs",
            self.project_root.name,
            exc,
            self._defer_delay,
        )

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

        Four operations, in order, on the SAME loaded graph:

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
        4. BRIEF (pre-warm the charter) — the same shape as SUMMARIZE, one axis
           over: :meth:`_brief_once` spends its OWN per-tick budget
           (``brief_budget``) warming domain briefs for the charter's live
           domains through :func:`~tesserae.charter.materialize_domain_brief`.
           It is the only caller of that writer, and it lives HERE rather than
           in compile or on a read for two reasons: compile would put an LLM
           call per domain on every compile, and a lazy read would break the
           pinned invariant that reading a domain card costs the map no
           ``complete_json`` call. Honest no-op without a charter on disk or
           an LLM client.

        Runs under the compile gate (held by the caller) — for the WHOLE pass,
        deliberately. Every op above reads the one ``graph.json`` loaded here, so
        releasing the gate between LLM calls would let a compile rewrite the
        graph mid-pass and leave early briefs describing a different graph than
        late ones. What the tick does instead, when a file save arrives and a
        pipeline run starts waiting on the gate, is ABANDON: ops 3 and 4 check
        :attr:`_pipeline_pending` at the top of each iteration and stop spending,
        so the pipeline waits out at most the one LLM call already in flight
        rather than all ``summarize_budget + brief_budget`` of them. The
        abandoned work is not lost — it is simply still cold on the next tick.
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

        # Brief runs LAST, on the same graph, under the same gate, and spends
        # its OWN budget — sharing summarize's would let a busy dendrogram
        # starve the charter (or the reverse), and the two populations differ
        # by orders of magnitude. _brief_once already degrades every failure
        # mode to a summary dict; the wrap covers injected stubs anyway.
        try:
            briefed = self._brief_once(graph)
            logger.info("brief for %s: %s", self.project_root.name, briefed)
        except Exception as exc:  # noqa: BLE001 - brief never kills the tick
            logger.error("brief raised (daemon survives): %s", exc)

    def _summarize_once(self, graph) -> dict:
        """Pre-warm community-summary caches by demand (SUMMARIZE, Descent §6.4).

        Within a per-tick LLM-call budget (``summarize_budget``, default 25;
        ``0`` disables the op) lazily materialize summaries for the communities
        agents are most likely to descend into next, so their first ``graph_map``
        visit finds a warm cache instead of paying a synchronous LLM call.
        Candidates are every community in the hierarchy sidecar at its
        canonical (coarsest) occurrence, ranked by demand — the scope's own
        cid ``access_count`` row (``graph_map`` bumps every surfaced card's
        scope_id, and below the coarsest level those cids are pseudo-id rows,
        not graph nodes, so spine traversal is visible ONLY there) plus Σ
        ``node_memory.access_count`` over members (leaf reads from
        node_context/search surfaces) — tie-broken by member count, then
        summed member degree, then coarsest level first, then cid. Warm (digest-valid) caches, in-graph
        COMMUNITY_SUMMARY scopes (compile-owned) and singletons cost no budget;
        only cold materializations do — each via
        :func:`~tesserae.community_summaries.materialize_community_summary`,
        the exact single-call path ``graph_map`` uses (same level-scoped cache
        layout, citation discipline, atomic writes). Summaries are caches, not
        knowledge: nothing here touches ``graph.json``. Honest no-op without a
        hierarchy sidecar or an LLM client; never raises — every outcome is a
        summary dict for the tick log.

        ABANDONMENT. The budget is a ceiling, not a quota. At the top of each
        iteration the loop reads :attr:`_pipeline_pending` and stops if a
        pipeline run is blocked on the compile gate this pass holds, reporting
        ``abandoned`` and ``unspent`` in the summary dict. Stopping is lossless:
        warming is idempotent, a scope not reached is still cold next tick at
        the same rank, and this loop writes no state that the ranking reads. The
        gate is NOT released mid-pass — every scope this tick warms is described
        against the one ``graph.json`` it loaded.
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
        # Demand = the cid row's own bumps (graph_map spine traversal — the
        # only place browsing lands when an agent never reaches leaf node
        # cards) + member bumps (leaf reads). Both live in node_memory.
        ranked = sorted(
            scopes.items(),
            key=lambda kv: (
                -(access.get(kv[0], 0) + sum(access.get(m, 0) for m in kv[1][1])),
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
        abandoned = False
        for cid, (level, members) in ranked:
            if attempted >= budget:
                break
            if self._pipeline_pending.is_set():
                # A pipeline run is blocked on the compile gate this pass holds.
                # Abandon the rest of the budget: the user's edit outranks
                # speculative warming, and nothing is lost by stopping — every
                # scope warmed so far is on disk, and every scope not reached is
                # simply still cold next tick, at the same rank (the ranking
                # reads access counts and the hierarchy, never anything this
                # loop writes). Checked HERE, at the top of an iteration, so a
                # call already in flight always finishes.
                abandoned = True
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
        result = {
            "summarized": summarized,
            "failed": failed,
            "warm": warm,
            "attempted": attempted,
            "budget": budget,
        }
        if abandoned:
            # Say so in the tick log. A silent early return is indistinguishable
            # from "there was nothing to warm", which is the one reading that
            # would stop anyone noticing this op had been throttled all week.
            result["abandoned"] = "pipeline pending"
            result["unspent"] = budget - attempted
        return result

    def _brief_once(self, graph) -> dict:
        """Pre-warm charter domain briefs by demand (BRIEF, the 4th op).

        ``_summarize_once`` one axis over: the candidates are the charter's
        LIVE domains rather than the dendrogram's communities, and each cold
        one is warmed through :func:`~tesserae.charter.materialize_domain_brief`
        — the writer paired with the three readers that consume a brief
        (``mcp_server._domain_card``, ``charter_route``, lint's
        ``CHARTER_FALLBACK``). Until this op existed that writer had no caller
        outside tests, so all three read an empty set forever: every domain
        card rendered ``quality: "structural"``, ``warm_rows`` was always 0,
        and the lint reported every live domain cold.

        WHY THE DAEMON. A brief costs one ``complete_json`` call. Minting them
        in compile would put one LLM call per live domain on EVERY compile —
        the compile path is the one place this project keeps deterministic and
        cheap. Minting them lazily on read would break the invariant
        ``test_reading_a_brief_costs_the_map_no_llm_call`` pins: a
        ``graph_map`` visit never calls an LLM for a domain card. The idle
        sleep cycle is the only place left that can spend a call nobody is
        waiting on.

        BUDGET. ``brief_budget`` (default 8; ``0`` disables the op) is
        deliberately separate from ``summarize_budget`` so neither op can
        starve the other, and deliberately smaller. One brief is one
        ``complete_json`` call over at most 25 prompt members — the same unit
        cost as one community summary — but the populations are not
        comparable, and neither is the shape of the demand. The figures this
        project's own code records for itself are a dendrogram root of 1,852
        communities (``_mcp_graph_map``) against a charter of 780 live domains
        (``lint._check_charter_fallback``) of which SEVEN are the live
        DIVISIONS ``graph_map()`` serves as its root card set. Those seven are
        the cards every agent meets first, so 8 warms the whole entry point in
        the first idle tick and then costs nothing forever, because a
        digest-valid brief is free; the remaining tiers warm at 8 per tick
        behind it. A larger default would front-load spend on domains no agent
        has reached yet, and a cold brief degrades to a card that still names
        the domain's size and top members — so warming one late is cheap,
        which is what makes a small budget the right trade.

        RATE, stated because this op is on by default and spends money: at
        most ``brief_budget`` calls per tick, and a tick fires at most once
        per ``consolidate_idle_seconds``. At the defaults that is 8 calls per
        5-minute tick, so a ceiling of 96/hour — and only while domains are
        cold. Once the charter is warm the steady state is ZERO, because a
        digest-valid brief costs no call and no slot.

        ORDER — and this is BREADTH-FIRST, not a demand rank; calling it one
        would misdescribe what it does. The key is ``(not a live division,
        -demand, -members present, -Σ degree, tier ascending, slug)``:

        * The first component is membership of ``charter.live_divisions`` —
          the exact set ``graph_map()`` serves as its root card set. NOT
          ``tier == 1``: a division is a domain with no LIVE parent, which is
          "on purpose" and differs for an orphan whose parent was retired
          without it (charter.py:637). Ranking on a different set than
          ``graph_map`` serves would make the default budget's own
          justification — "one tick warms the root" — untrue.
        * DEMAND is Σ ``node_memory.access_count`` over the domain's members,
          and unlike ``_summarize_once`` there is NO scope row to add to it.
          ``graph_map`` deliberately does not bump ``domain:<slug>``
          (``mcp_server._bump_card_access`` skips ``kind="domain"`` cards, on
          the stated ground that a domain row is "a key nothing on any read
          path looks up"); the node cards INSIDE a domain, which are real
          graph ids, are bumped instead. Reading a row nothing writes would
          make this ranking silently uniform.
        * But demand cannot order an ancestor against its own descendant.
          Members are ``domain_member_ids`` — the whole SUBTREE — so a
          parent's member set CONTAINS every child's, and therefore
          ``demand(parent) >= demand(child)``, ``present(parent) >=
          present(child)`` and ``degree(parent) >= degree(child)``, with the
          tier tiebreak resolving any remaining equality towards the parent.
          **No domain is ever warmed before its ancestors.** Demand orders
          only domains neither of which contains the other.

        That is intended rather than an artifact. Agents descend from the
        root, so the coarse card is the one read first and the one worth
        having prose for, and a hot subtree lifts its whole spine. The
        alternative — demand over DIRECT members only — would let a hot leaf
        be briefed while every division an agent passes through to reach it
        stayed structural, which is the wrong end to start from.

        Whichever way it runs, the order is TOTAL — slug is unique per
        charter — so two ticks over identical state pick the same domains in
        the same sequence.

        WHAT NEVER COSTS A BUDGET SLOT. A slot is meant to BE an LLM call, and
        the rank is stable, so a slot spent on something that cannot warm is
        spent again every tick forever:

        * Tombstones (``status != "live"``) — a retired domain holds no
          members and is offered nowhere.
        * The intake CENSUS domain — see :func:`_is_census_domain`.
        * A slug ``brief_cache_path`` refuses: the writer returns ``None``
          having made no call at all, so charging it spends the tick on
          nothing.
        * A domain with no members left in the graph — same reason.
        * A warm, digest-valid brief. That is what the cache is for.

        BACK-OFF, which is what stops head-of-line starvation. A failure that
        DOES cost a call — most importantly a citation rejection, which
        ``summarize_community`` neither caches nor retracts from the client's
        own prompt cache, unlike ``llm_extractor``'s ``forget_cached_answer``
        — would otherwise recur at the same rank on every tick; and because
        the loop stops at ``attempted >= budget``, every candidate below it
        would never be reached at all. That is permanent zero progress at 12
        ticks an hour. So a failed domain takes a strike and is held off for
        ``2**strikes`` ticks (capped at ``_BRIEF_MAX_BACKOFF_TICKS``), freeing
        its slot on the very NEXT tick; a success or a warm read clears the
        strikes. The pass therefore makes progress even when the first N
        candidates are permanently un-warmable and the budget is below N, and
        a domain that is cold only because a provider was down still recovers
        on its own.

        ABANDONMENT, and why it does not disturb any of the above. At the top of
        each iteration the loop reads :attr:`_pipeline_pending` and stops if a
        pipeline run is blocked on the compile gate this pass holds, reporting
        ``abandoned`` and ``unspent``. The check sits ABOVE both the warm read
        and the writer, so an abandoned domain takes no strike and gains no
        ``retry_at`` — it did not fail, it was never tried, and back-off is for
        domains that burned a call. The tick COUNTER still advances, so an
        outstanding back-off decays by one tick; that is deliberate and costs at
        most one extra call per domain, the same trade the restart-retries-all
        note above already accepts. Because an abandonment writes nothing the
        ranking reads, the next tick picks the same domains in the same
        sequence it would have picked anyway — the order never depends on when
        an abandon happened.

        Briefs are caches, not knowledge — nothing here touches
        ``graph.json``. Never raises: no charter, an unreadable one, no LLM
        client, a cyclic child tree, a domain with no members present, a
        client that raises and a writer that returned ``None`` are each an
        outcome in the returned summary dict.
        """
        budget = max(0, int(self._brief_budget))
        if budget == 0:
            return {"briefed": [], "skipped": "budget=0"}
        try:
            from ..charter import CharterUnreadable, read_charter

            charter = read_charter(self.project_root)
        except CharterUnreadable as exc:
            return {"briefed": [], "skipped": f"unreadable charter: {exc}"}
        except Exception as exc:  # noqa: BLE001 - a brief is never load-bearing
            return {"briefed": [], "skipped": f"charter read failed: {exc}"}
        if charter is None:
            return {"briefed": [], "skipped": "no charter"}
        domains = charter.get("domains")
        if not isinstance(domains, dict):
            return {"briefed": [], "skipped": "charter has no domains"}
        live = sorted(
            str(slug)
            for slug, entry in domains.items()
            if isinstance(entry, dict) and entry.get("status") == "live"
        )
        if not live:
            return {"briefed": [], "skipped": "no live domains"}
        client = self._resolve_summary_client()
        if client is None:
            return {"briefed": [], "skipped": "no LLM client"}

        from ..charter import (
            _brief_level,
            brief_cache_path,
            domain_member_ids,
            live_divisions,
            materialize_domain_brief,
            read_domain_brief,
        )
        from ..hierarchy import undirected_degrees

        tick = self._brief_tick = self._brief_tick + 1
        access = self._read_access_counts()
        by_id = {n.id: n for n in graph.nodes}
        degrees = undirected_degrees(graph)
        cache_dir = self.project_root / ".tesserae" / "community_summaries"
        try:
            divisions = set(live_divisions(charter))
        except Exception as exc:  # noqa: BLE001 - a mangled tree is data
            logger.debug("brief: live_divisions failed: %s", exc)
            divisions = set()

        candidates: List[Tuple[Tuple[int, int, int, int, int, str], str]] = []
        broken: List[str] = []
        census: List[str] = []
        unwritable: List[str] = []
        deferred: List[str] = []
        for slug in live:
            entry = domains.get(slug) or {}
            if _is_census_domain(entry):
                census.append(slug)
                continue
            if self._brief_retry_at.get(slug, 0) > tick:
                deferred.append(slug)
                continue
            try:
                member_ids = domain_member_ids(charter, slug)
                tier = _brief_level(charter, slug)
                writable = brief_cache_path(charter, slug, cache_dir=cache_dir)
            except Exception as exc:  # noqa: BLE001 - a mangled tree is data
                logger.debug("brief: %s is not walkable: %s", slug, exc)
                broken.append(slug)
                continue
            if writable is None:
                # ``_brief_slug_ok`` refuses this slug, so the writer returns
                # None having made NO LLM call. Charging it a budget slot would
                # spend the tick on nothing, every tick, forever.
                unwritable.append(slug)
                continue
            present = [mid for mid in member_ids if mid in by_id]
            if not present:
                continue  # the writer would return None without an LLM call
            demand = sum(access.get(mid, 0) for mid in member_ids)
            weight = sum(degrees.get(mid, 0) for mid in present)
            candidates.append(
                (
                    (
                        0 if slug in divisions else 1,
                        -demand,
                        -len(present),
                        -weight,
                        tier,
                        slug,
                    ),
                    slug,
                )
            )
        candidates.sort(key=lambda item: item[0])

        briefed: List[str] = []
        failed: List[str] = []
        warm = 0
        attempted = 0
        abandoned = False
        for _key, slug in candidates:
            if attempted >= budget:
                break
            if self._pipeline_pending.is_set():
                # Same contract as _summarize_once. Note WHERE this sits: above
                # both the warm read and the writer, so an abandoned domain
                # takes NO strike and gets NO retry_at entry. It did not fail,
                # it was never tried — charging it back-off would push a
                # perfectly warmable domain down the queue for 2**strikes ticks
                # because an unrelated file was saved. Nothing else in this loop
                # has run for this slug either, so the abandonment writes no
                # state at all and the next tick's ranking is byte-identical to
                # the one this tick would have produced.
                abandoned = True
                break
            warm_brief = read_domain_brief(
                charter, slug, by_id, degrees, cache_dir=cache_dir
            )
            if warm_brief is not None:
                warm += 1  # digest-valid cache — free, costs no budget
                self._brief_failures.pop(slug, None)
                self._brief_retry_at.pop(slug, None)
                continue
            # Past the pre-checks every remaining path through the writer
            # reaches the LLM, so from here a budget slot IS a call.
            attempted += 1
            if materialize_domain_brief(
                charter,
                slug,
                by_id,
                degrees,
                cache_dir=cache_dir,
                json_client=client,
            ) is not None:
                briefed.append(slug)
                self._brief_failures.pop(slug, None)
                self._brief_retry_at.pop(slug, None)
            else:
                failed.append(slug)
                strikes = self._brief_failures.get(slug, 0) + 1
                self._brief_failures[slug] = strikes
                self._brief_retry_at[slug] = tick + min(
                    2**strikes, _BRIEF_MAX_BACKOFF_TICKS
                )
        result = {
            "briefed": briefed,
            "failed": failed,
            "unwalkable": broken,
            "unwritable": unwritable,
            "census": census,
            "deferred": deferred,
            "warm": warm,
            "attempted": attempted,
            "budget": budget,
            "live": len(live),
            "tick": tick,
        }
        if abandoned:
            result["abandoned"] = "pipeline pending"
            result["unspent"] = budget - attempted
        return result

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
                from ..llm_json import build_default_json_client, project_llm_settings

                self._default_summary_client = build_default_json_client(
                    settings=project_llm_settings(self.project_root))
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

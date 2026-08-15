"""Live, codegraph-style progress for ``tesserae compile``.

A small reporter the compile pipeline drives through its phases:

    scan(total)        → "◆ Scanning sources — N found"
    extract_start(n)   → opens the live file bar
    advance()          → ticks the bar one file
    extract_done(n)    → "◆ Extracted N files"
    finalize(label)    → "⠙ Finalizing — <label>…" (spinner held open)
    done(nodes, edges) → "◆ Compiled · N nodes · M edges · 41s"

Three implementations: :class:`RichCompileProgress` for an interactive TTY,
:class:`LoggingCompileProgress` for everything else that wants to be watched,
and :class:`NullCompileProgress` (all no-ops) for callers that asked for
silence. :func:`make_compile_progress` picks between them.

Why a third one exists
----------------------

The rich bar draws with cursor control, so it is worthless in a file and was
correctly limited to a TTY. What that left behind, though, was
:class:`NullCompileProgress` for every NON-terminal run — and a long compile is
almost never run on a terminal. Detached by the session-close hook, backgrounded
with ``&``, redirected to a log, run under CI or an agent harness: every one of
those took the no-op branch and printed **nothing at all** until the final
summary line. Measured: a full 2,524-document compile emitted 0 bytes over
3h35m, which is indistinguishable from a hang. It was killed on that suspicion.

So the non-TTY default is now a reporter that LOGS rather than one that does
nothing. It carries the two facts a bar cannot: which document is in flight
(a stall names its file) and whether that document was replayed from
``~/.tesserae/llm_cache`` or paid for a real model call. Those differ by four
orders of magnitude in wall-clock, so a run that is mostly cache and a run
paying full price are the same picture without the label and obvious with it.

Nothing here reaches an artifact. Records go to ``logging``, which the CLI
points at stderr; ``graph.json`` is byte-identical with the reporter on or off,
and that is asserted by ``tests/test_compile_progress.py``.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Optional, Protocol, TextIO, runtime_checkable

#: The compile's own channel, named like ``tesserae.project`` /
#: ``tesserae.code_graph`` so an operator can raise or silence progress alone
#: without touching the rest of the package's logging.
logger = logging.getLogger("tesserae.compile")

#: Documents between heartbeat summaries. Per-document lines tell you what is in
#: flight; the heartbeat tells you the shape of the run so far (how much was
#: cache) without having to read every line.
_HEARTBEAT_EVERY = 50

#: Don't estimate from a handful of documents. Per-document cost is bimodal — a
#: cache replay is milliseconds, a real call is tens of seconds — so an average
#: over the first few files is noise dressed up as a number.
_MIN_SAMPLE_FOR_ETA = 20


@runtime_checkable
class CompileProgress(Protocol):
    """The phase interface the compile pipeline drives."""

    def scan(self, total: int) -> None: ...

    #: ``path`` names the document just finished and ``outcome`` says what it
    #: cost: ``"cache"`` (replayed from the LLM cache), ``"llm"`` (a real model
    #: call), ``"skip"`` (unchanged since the last compile, never extracted) or
    #: ``None`` when the caller has nothing to report. Both are keyword-only
    #: with defaults so a bare ``advance()`` — which is all the deterministic
    #: extractor path and the existing tests do — stays valid.
    def advance(self, *, path: Optional[str] = None, outcome: Optional[str] = None) -> None: ...
    def extract_start(self, total: int) -> None: ...
    def extract_done(self, processed: int) -> None: ...
    def finalize(self, label: str) -> None: ...
    def done(self, *, nodes: int, edges: int) -> None: ...
    def __enter__(self) -> "CompileProgress": ...
    def __exit__(self, *exc: object) -> None: ...


class NullCompileProgress:
    """No-op reporter — used whenever output isn't an interactive terminal."""

    def scan(self, total: int) -> None:
        return None

    def extract_start(self, total: int) -> None:
        return None

    def advance(self, *, path: Optional[str] = None, outcome: Optional[str] = None) -> None:
        return None

    def extract_done(self, processed: int) -> None:
        return None

    def finalize(self, label: str) -> None:
        return None

    def done(self, *, nodes: int, edges: int) -> None:
        return None

    def __enter__(self) -> "NullCompileProgress":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _format_elapsed(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m{seconds % 60:02d}s"


class RichCompileProgress:
    """Codegraph-style live progress backed by ``rich``.

    Renders to ``stream`` (stderr by default) so stdout stays clean for
    piping. A single ``rich.console.Console`` carries the phase lines, the
    live extraction bar, and the finalize spinner.
    """

    def __init__(self, stream: Optional[TextIO] = None) -> None:
        from rich.console import Console

        self._console = Console(file=stream or sys.stderr, force_terminal=True)
        self._progress = None  # rich.progress.Progress while the bar is live
        self._task = None
        self._status = None  # rich.status.Status during finalize
        self._start = time.monotonic()

    # -- diamond/bullet markers, matching the codegraph aesthetic ----------
    def _diamond(self, text: str) -> None:
        self._console.print(f"[green]◆[/green] {text}")

    def scan(self, total: int) -> None:
        self._start = time.monotonic()
        self._diamond(f"Scanning sources — [bold]{total}[/bold] found")

    def extract_start(self, total: int) -> None:
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            SpinnerColumn,
            TaskProgressColumn,
            TextColumn,
            TimeElapsedColumn,
        )

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]Extracting[/bold]"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self._console,
            transient=True,  # bar disappears on stop; the ◆ line is the record
        )
        self._progress.start()
        self._task = self._progress.add_task("extract", total=max(total, 0))

    def advance(self, *, path: Optional[str] = None, outcome: Optional[str] = None) -> None:
        # The bar already shows N/M and a running clock, and a per-file label
        # under a transient widget is unreadable at extraction speed, so the
        # detail is accepted and dropped here. It is the LOG reporter that
        # needs it, because a log has no cursor to redraw.
        if self._progress is not None and self._task is not None:
            self._progress.advance(self._task)

    def extract_done(self, processed: int) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._task = None
        self._diamond(f"Extracted [bold]{processed}[/bold] files")

    def finalize(self, label: str) -> None:
        # Hold a spinner open while the post-extraction passes run; it is
        # closed by done(). Guard against a second finalize().
        if self._status is None:
            self._status = self._console.status(f"Finalizing — {label}…")
            self._status.start()

    def done(self, *, nodes: int, edges: int) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None
        elapsed = _format_elapsed(time.monotonic() - self._start)
        self._diamond(
            f"Compiled  ·  [bold]{nodes}[/bold] nodes · "
            f"[bold]{edges}[/bold] edges  ·  {elapsed}"
        )

    def __enter__(self) -> "RichCompileProgress":
        return self

    def __exit__(self, *exc: object) -> None:
        # Always tear the live widgets down, even on an exception path.
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
        if self._status is not None:
            self._status.stop()
            self._status = None


#: Fixed-width so the column lines up and the eye can scan it. The whole point
#: is that "this run is replaying cache" and "this run is paying full price"
#: are distinguishable at a glance rather than by reading timestamps.
_OUTCOME_LABEL = {
    "cache": "cache",
    "llm": "LLM  ",
    "skip": "skip ",
}


class LoggingCompileProgress:
    """Line-per-document progress for runs with no terminal to draw on.

    Emits through :data:`logger` at INFO. Every line answers the three questions
    an operator has about a compile that has been running for hours: how far in
    is it, what is it working on, and is it paying for model calls or replaying
    the cache.
    """

    def __init__(self, heartbeat_every: int = _HEARTBEAT_EVERY) -> None:
        self._total = 0
        self._done = 0
        self._counts = {"cache": 0, "llm": 0, "skip": 0}
        self._heartbeat_every = max(1, heartbeat_every)
        self._start = time.monotonic()
        # ``advance`` is called from TESSERAE_EXTRACT_CONCURRENCY worker threads.
        # BatchIngestRunner happens to hold its own lock across the call today,
        # but this class must not depend on a caller's locking to keep its
        # counters from tearing.
        self._lock = threading.Lock()

    def scan(self, total: int) -> None:
        self._start = time.monotonic()
        logger.info("Scanning sources — %d found", total)

    def extract_start(self, total: int) -> None:
        self._total = max(total, 0)
        logger.info("Extracting %d documents", self._total)

    def advance(self, *, path: Optional[str] = None, outcome: Optional[str] = None) -> None:
        with self._lock:
            self._done += 1
            done = self._done
            if outcome in self._counts:
                self._counts[outcome] += 1
            heartbeat_due = done % self._heartbeat_every == 0
            snapshot = dict(self._counts)
        # Logged OUTSIDE the lock: a handler writing to a slow file must not
        # serialise the worker threads behind it.
        logger.info(
            "  %d/%d  %s  %s",
            done,
            self._total,
            _OUTCOME_LABEL.get(outcome or "", "     "),
            path or "",
        )
        if heartbeat_due:
            self._heartbeat(done, snapshot)

    def _heartbeat(self, done: int, counts: dict) -> None:
        elapsed = time.monotonic() - self._start
        rate = done / elapsed if elapsed > 0 else 0.0
        message = (
            f"  … {done}/{self._total} · cache {counts['cache']} · "
            f"LLM {counts['llm']} · skip {counts['skip']} · "
            f"{rate * 60:.1f} docs/min · {_format_elapsed(elapsed)} elapsed"
        )
        remaining = self._total - done
        if remaining > 0 and done >= _MIN_SAMPLE_FOR_ETA and rate > 0:
            # Deliberately hedged. A document costs milliseconds from cache and
            # tens of seconds from a model, so the mean so far only predicts the
            # rest if the REMAINING documents hit the cache at the same rate —
            # which nothing here knows. Naming that assumption in the line is
            # the difference between an estimate and a fabricated number.
            message += f" · eta ~{_format_elapsed(remaining / rate)} at this cache mix"
        logger.info("%s", message)

    def extract_done(self, processed: int) -> None:
        with self._lock:
            counts = dict(self._counts)
        logger.info(
            "Extracted %d documents — %d from cache, %d via model calls, %d unchanged",
            processed,
            counts["cache"],
            counts["llm"],
            counts["skip"],
        )

    def finalize(self, label: str) -> None:
        logger.info("Finalizing — %s…", label)

    def done(self, *, nodes: int, edges: int) -> None:
        logger.info(
            "Compiled — %d nodes, %d edges, %s",
            nodes,
            edges,
            _format_elapsed(time.monotonic() - self._start),
        )

    def __enter__(self) -> "LoggingCompileProgress":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def make_compile_progress(
    stream: Optional[TextIO] = None,
    quiet: bool = False,
) -> CompileProgress:
    """Pick the right reporter.

    ``quiet`` silences everything. Otherwise: rich when ``stream`` (default
    ``sys.stderr``) is an interactive terminal, and the LOGGING reporter when it
    is not — because a pipe, a log file and a detached background job are
    exactly the runs nobody can watch, and those were the ones printing nothing.

    Only ``quiet`` buys silence now. That is the deliberate change: silence used
    to be the default for every non-terminal run, which is how a 3h35m compile
    came to look like a hang.
    """
    target = stream if stream is not None else sys.stderr
    is_tty = bool(getattr(target, "isatty", lambda: False)())
    if quiet:
        return NullCompileProgress()
    if not is_tty:
        return LoggingCompileProgress()
    return RichCompileProgress(target)

"""Live, codegraph-style progress for ``tesserae compile``.

A small reporter the compile pipeline drives through its phases:

    scan(total)        → "◆ Scanning sources — N found"
    extract_start(n)   → opens the live file bar
    advance()          → ticks the bar one file
    extract_done(n)    → "◆ Extracted N files"
    finalize(label)    → "⠙ Finalizing — <label>…" (spinner held open)
    done(nodes, edges) → "◆ Compiled · N nodes · M edges · 41s"

Two implementations: :class:`RichCompileProgress` for an interactive TTY and
:class:`NullCompileProgress` (all no-ops) for pipes/CI/MCP/daemon/tests.
:func:`make_compile_progress` picks the right one — rich ONLY when the target
stream is a real terminal and the caller didn't ask to be quiet — so scripted
and captured output stays exactly as before.
"""

from __future__ import annotations

import sys
import time
from typing import Optional, Protocol, TextIO, runtime_checkable


@runtime_checkable
class CompileProgress(Protocol):
    """The phase interface the compile pipeline drives."""

    def scan(self, total: int) -> None: ...
    def extract_start(self, total: int) -> None: ...
    def advance(self) -> None: ...
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

    def advance(self) -> None:
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

    def advance(self) -> None:
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


def make_compile_progress(
    stream: Optional[TextIO] = None,
    quiet: bool = False,
) -> CompileProgress:
    """Pick the right reporter.

    Rich ONLY when ``stream`` (default ``sys.stderr``) is an interactive
    terminal and ``quiet`` is false; otherwise a :class:`NullCompileProgress`
    so piped/CI/MCP/daemon/test output is byte-identical to before.
    """
    target = stream if stream is not None else sys.stderr
    is_tty = bool(getattr(target, "isatty", lambda: False)())
    if quiet or not is_tty:
        return NullCompileProgress()
    return RichCompileProgress(target)

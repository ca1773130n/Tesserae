"""Tests for the compile progress reporter (codegraph-style live bar)."""

from __future__ import annotations

import io

from tesserae.compile_progress import (
    NullCompileProgress,
    RichCompileProgress,
    make_compile_progress,
)


class _FakeTTY(io.StringIO):
    def isatty(self) -> bool:  # pragma: no cover - trivial
        return True


class _FakePipe(io.StringIO):
    def isatty(self) -> bool:  # pragma: no cover - trivial
        return False


def test_factory_returns_null_when_not_a_tty():
    prog = make_compile_progress(stream=_FakePipe())
    assert isinstance(prog, NullCompileProgress)


def test_factory_returns_null_when_quiet_even_on_tty():
    prog = make_compile_progress(stream=_FakeTTY(), quiet=True)
    assert isinstance(prog, NullCompileProgress)


def test_factory_returns_rich_on_a_tty():
    prog = make_compile_progress(stream=_FakeTTY())
    assert isinstance(prog, RichCompileProgress)


def test_null_progress_is_a_no_op_and_never_raises():
    prog = NullCompileProgress()
    with prog:
        prog.scan(10)
        prog.extract_start(10)
        for _ in range(50):  # advancing past total must not blow up
            prog.advance()
        prog.extract_done(10)
        prog.finalize("community summaries, vault, site")
        prog.done(nodes=5, edges=3)


def test_rich_progress_renders_phases_to_its_stream():
    stream = _FakeTTY()
    prog = make_compile_progress(stream=stream)
    with prog:
        prog.scan(3)
        prog.extract_start(3)
        prog.advance()
        prog.advance()
        prog.advance()
        prog.extract_done(3)
        prog.finalize("community summaries, vault, site")
        prog.done(nodes=42, edges=7)
    out = stream.getvalue()
    # codegraph-style phase markers + the headline file count + the summary
    assert "Scanning" in out and "3" in out
    assert "Extract" in out
    assert "42" in out and "7" in out

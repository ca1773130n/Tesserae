"""Tests for the compile progress reporter (codegraph-style live bar)."""

from __future__ import annotations

import io
import logging

from tesserae.compile_progress import (
    LoggingCompileProgress,
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


def test_factory_logs_when_not_a_tty_rather_than_going_silent():
    """The regression this whole module exists for.

    A non-terminal run used to get :class:`NullCompileProgress` — a pure no-op.
    That covers a pipe, a redirect, CI, and a job detached by the session-close
    hook, which is to say every long compile nobody is sitting in front of. One
    measured 2,524-document run printed 0 bytes over 3h35m and was killed
    because it was indistinguishable from a hang. Silence is now opt-IN.
    """
    prog = make_compile_progress(stream=_FakePipe())
    assert isinstance(prog, LoggingCompileProgress)


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


def test_factory_returns_quiet_null_even_when_not_a_tty():
    prog = make_compile_progress(stream=_FakePipe(), quiet=True)
    assert isinstance(prog, NullCompileProgress)


def test_logging_progress_reports_position_path_and_what_each_doc_cost(caplog):
    """The three facts an operator needs from a compile that has run for hours.

    How far in (``N/M``), what is in flight (the path, so a stall names its
    file), and — the one nothing else can supply — whether the document was
    replayed from ``~/.tesserae/llm_cache`` or paid for a real model call. Those
    differ by four orders of magnitude in wall-clock, so without the label a
    90%-cached run and a full-price run are the same picture.
    """
    caplog.set_level(logging.INFO, logger="tesserae.compile")
    prog = LoggingCompileProgress()
    with prog:
        prog.scan(3)
        prog.extract_start(3)
        prog.advance(path="docs/a.md", outcome="cache")
        prog.advance(path="docs/b.md", outcome="llm")
        prog.advance(path="docs/c.md", outcome="skip")
        prog.extract_done(3)
        prog.done(nodes=9, edges=4)
    lines = [r.getMessage() for r in caplog.records]
    text = "\n".join(lines)

    assert "Scanning sources — 3 found" in text
    # Position and path, per document.
    assert "1/3" in text and "docs/a.md" in text
    assert "3/3" in text and "docs/c.md" in text
    # The cost label has to be legible at a glance, and the three kinds must
    # not render the same — that is the entire diagnostic value.
    cache_line = next(ln for ln in lines if "docs/a.md" in ln)
    llm_line = next(ln for ln in lines if "docs/b.md" in ln)
    skip_line = next(ln for ln in lines if "docs/c.md" in ln)
    assert "cache" in cache_line and "LLM" not in cache_line
    assert "LLM" in llm_line and "cache" not in llm_line
    assert "skip" in skip_line
    # The run-level tally repeats it, so a reader who scrolls to the end still
    # learns what the run cost without counting lines.
    assert "3 from cache" not in text  # only one was
    assert "1 from cache, 1 via model calls, 1 unchanged" in text
    assert "9 nodes, 4 edges" in text


def test_logging_progress_heartbeat_gives_a_rate_and_hedges_the_eta(caplog):
    """An ETA over a bimodal cost has to name its assumption or it is a lie.

    A cache replay costs milliseconds and a real model call tens of seconds, so
    the mean cost so far predicts the remaining work only if the rest of the
    corpus hits the cache at the same rate — which nothing here knows. Emitting
    a bare ``eta 40m`` would be fabricated precision; the rate is always
    honest, so the rate is unconditional and the estimate carries the caveat.
    """
    caplog.set_level(logging.INFO, logger="tesserae.compile")
    prog = LoggingCompileProgress(heartbeat_every=5)
    prog.scan(100)
    prog.extract_start(100)
    for i in range(5):
        prog.advance(path=f"docs/{i}.md", outcome="cache")
    beat = next(r.getMessage() for r in caplog.records if "docs/min" in r.getMessage())
    assert "5/100" in beat and "cache 5" in beat
    assert "elapsed" in beat
    # 5 documents is below the sampling floor, so no estimate is offered yet.
    assert "eta" not in beat

    caplog.clear()
    for i in range(5, 25):
        prog.advance(path=f"docs/{i}.md", outcome="llm")
    beat = [r.getMessage() for r in caplog.records if "docs/min" in r.getMessage()][-1]
    assert "eta ~" in beat and "at this cache mix" in beat


def test_logging_progress_survives_a_bare_advance(caplog):
    """The deterministic extractor reports no cost, and must still tick.

    ``outcome=None`` means the LLM cache was never consulted (a structural
    extraction, or ``TESSERAE_LLM_CACHE=0``). Inventing a label there would
    report a miss that was never observed, so the column is simply blank.
    """
    caplog.set_level(logging.INFO, logger="tesserae.compile")
    prog = LoggingCompileProgress()
    prog.extract_start(2)
    prog.advance()  # the pre-existing call shape, still valid
    prog.advance(path="docs/only-path.md")
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "1/2" in text and "2/2" in text
    assert "cache" not in text and "LLM" not in text


def test_logging_progress_counters_hold_under_concurrent_advances(caplog):
    """``advance`` runs on TESSERAE_EXTRACT_CONCURRENCY worker threads.

    A torn counter would misreport position on exactly the runs long enough to
    need reporting, so the reporter locks its own state rather than trusting a
    caller to hold one.
    """
    import threading

    caplog.set_level(logging.INFO, logger="tesserae.compile")
    prog = LoggingCompileProgress(heartbeat_every=10_000)
    prog.extract_start(200)
    threads = [
        threading.Thread(
            target=lambda n=n: [
                prog.advance(path=f"docs/{n}-{i}.md", outcome="cache") for i in range(50)
            ]
        )
        for n in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    prog.extract_done(200)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "200 from cache" in text
    positions = {
        int(msg.split("/")[0].strip())
        for msg in (r.getMessage() for r in caplog.records)
        if "/200" in msg and msg.startswith(" ")
    }
    assert positions == set(range(1, 201))

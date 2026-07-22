"""Deterministic tests for the associate (connection-discovery) leg of the
daemon's sleep-cycle consolidation.

The idle-consolidation tick already runs distill (compress/forget); this suite
covers the third sleep-cycle operation wired into the SAME tick, under the SAME
compile gate, AFTER distill:
``tesserae.memory.associate.consolidate_associations``. Determinism reuses the
constructor seams from ``tests/test_engine_consolidation.py`` — an injected
monotonic clock (``monotonic=``), an injected distill callable (``distill=``),
and an injected associate callable (``associate=``) — so no test drives a real
embedding model or sleeps out an idle window.

Run with the project venv (NOT the shim)::

    .venv/bin/python -m pytest tests/test_daemon_associate.py -q
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from tesserae.engine.daemon import Daemon


class FakeClock:
    """A hand-advanced stand-in for ``time.monotonic`` (seconds, float)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class RecordingDistill:
    """Stub for ``maybe_distill_on_refresh``: records calls into a shared log."""

    def __init__(self, order: list, graph_seen: list) -> None:
        self._order = order
        self._graph_seen = graph_seen

    def __call__(self, project_root, graph, *, cfg=None, env=None):
        self._order.append("distill")
        self._graph_seen.append(("distill", graph))
        return {"distilled": [], "skipped": [], "failed": []}


class RecordingAssociate:
    """Stub for ``consolidate_associations``: records call order + args.

    Optionally raises (to prove an associate failure never breaks the tick) and
    optionally checks that the compile gate is held while it runs (to prove it
    executes UNDER the gate, serialized with any compile).
    """

    def __init__(
        self,
        order: list,
        graph_seen: list,
        *,
        raises: BaseException | None = None,
        gate: threading.Semaphore | None = None,
    ) -> None:
        self._order = order
        self._graph_seen = graph_seen
        self._raises = raises
        self._gate = gate
        self.calls: list = []
        self.gate_was_held: bool | None = None

    def __call__(self, project_root, graph, *, backend=None, **kwargs):
        self._order.append("associate")
        self._graph_seen.append(("associate", graph))
        self.calls.append(
            {"project_root": project_root, "graph": graph, "backend": backend}
        )
        if self._gate is not None:
            # Non-blocking probe: if the caller holds the gate, acquire() fails.
            acquired = self._gate.acquire(blocking=False)
            self.gate_was_held = not acquired
            if acquired:
                self._gate.release()
        if self._raises is not None:
            raise self._raises
        return {"associate_added": 0}


def _make_project(tmp_path: Path) -> Path:
    """A minimal project with an empty compiled graph the daemon can load."""
    tdir = tmp_path / ".tesserae"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "graph.json").write_text(json.dumps({"nodes": [], "edges": []}))
    return tmp_path


# --------------------------------------------------------------------------- #
# associate runs after distill, on the same graph, under the gate             #
# --------------------------------------------------------------------------- #


def test_tick_calls_associate_after_distill_under_the_gate(tmp_path):
    """One due tick runs distill THEN associate, both on the SAME loaded graph,
    and associate observes the compile gate held (so it runs under it)."""
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    gate = threading.Semaphore(1)
    order: list = []
    graph_seen: list = []
    distill = RecordingDistill(order, graph_seen)
    associate = RecordingAssociate(order, graph_seen, gate=gate)
    d = Daemon(
        root,
        consolidate_idle_seconds=300.0,
        compile_gate=gate,
        monotonic=clock,
        distill=distill,
        associate=associate,
    )

    clock.advance(301)  # idle window elapsed -> tick is due
    d._consolidation_tick()

    assert order == ["distill", "associate"], "associate must run AFTER distill"
    assert len(associate.calls) == 1, "associate ran exactly once"
    # Same graph instance handed to both (tick loads once, reuses for both).
    assert graph_seen[0][1] is graph_seen[1][1], "distill+associate share the graph"
    assert associate.gate_was_held is True, "associate must run under the compile gate"


def test_associate_receives_resolved_backend(tmp_path):
    """The daemon resolves the embedding backend the app way and hands exactly
    that to associate — a real backend when semantic deps are installed, None
    when only the hash stub is available (associate then skips honestly)."""
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    order: list = []
    graph_seen: list = []
    distill = RecordingDistill(order, graph_seen)
    associate = RecordingAssociate(order, graph_seen)
    d = Daemon(
        root,
        consolidate_idle_seconds=300.0,
        monotonic=clock,
        distill=distill,
        associate=associate,
    )

    clock.advance(301)
    d._consolidation_tick()

    assert len(associate.calls) == 1
    # Whatever the app's resolver yields is exactly what associate receives; the
    # hash stub collapses to None so the pass is an honest no-op without a model.
    expected = d._resolve_embedding_backend()
    assert associate.calls[0]["backend"] is expected
    from tesserae.retrieval.hybrid import HashEmbeddingBackend

    assert not isinstance(associate.calls[0]["backend"], HashEmbeddingBackend)


# --------------------------------------------------------------------------- #
# safety: an associate failure never breaks the tick / daemon                 #
# --------------------------------------------------------------------------- #


def test_associate_exception_never_breaks_the_tick(tmp_path):
    """A throwing associate is contained: the tick does not raise, distill still
    ran, and ``_last_consolidation`` is stamped so a failing pass cannot hot-loop.
    """
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    order: list = []
    graph_seen: list = []
    distill = RecordingDistill(order, graph_seen)
    associate = RecordingAssociate(
        order, graph_seen, raises=RuntimeError("associate boom")
    )
    d = Daemon(
        root,
        consolidate_idle_seconds=300.0,
        monotonic=clock,
        distill=distill,
        associate=associate,
    )

    clock.advance(301)
    before = d._last_consolidation
    d._consolidation_tick()  # must NOT raise

    assert order == ["distill", "associate"], "distill ran, associate was attempted"
    assert d._last_consolidation != before, "_last_consolidation stamped in finally"


def test_associate_error_leaves_daemon_survivable(tmp_path):
    """After a throwing associate the tick is still healthy: a later due tick
    fires again (distill + associate) rather than the loop being poisoned."""
    root = _make_project(tmp_path)
    clock = FakeClock(1000.0)
    order: list = []
    graph_seen: list = []
    distill = RecordingDistill(order, graph_seen)
    associate = RecordingAssociate(
        order, graph_seen, raises=RuntimeError("associate boom")
    )
    d = Daemon(
        root,
        consolidate_idle_seconds=300.0,
        monotonic=clock,
        distill=distill,
        associate=associate,
    )

    clock.advance(301)
    d._consolidation_tick()
    clock.advance(301)  # a fresh idle window after the failing pass
    d._consolidation_tick()

    assert order == ["distill", "associate", "distill", "associate"], (
        "daemon survives a failing associate and consolidates again"
    )


# --------------------------------------------------------------------------- #
# once mode never consolidates (so never associates)                          #
# --------------------------------------------------------------------------- #


def test_once_mode_never_associates(tmp_path):
    """run(once=True) is the consolidation-free mode: associate never runs."""
    root = _make_project(tmp_path)
    order: list = []
    graph_seen: list = []
    distill = RecordingDistill(order, graph_seen)
    associate = RecordingAssociate(order, graph_seen)
    d = Daemon(
        root,
        debounce=0.0,
        consolidate=True,
        enable_watch=False,
        enable_vault=False,
        enable_session_tail=False,
        install_signal_handlers=False,
        run_pipeline=lambda paths: None,
        distill=distill,
        associate=associate,
    )
    rc = d.run(once=True)

    assert rc == 0
    assert order == [], "once mode must neither distill nor associate"
    assert associate.calls == [], "once mode must not associate"

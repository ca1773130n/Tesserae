"""A consolidation tick abandons its remaining budget for a waiting pipeline.

``_consolidate_once`` runs UNDER the compile gate, and ``_run_pipeline`` takes
that same gate. Inside one tick two pre-warming ops each spend a per-tick LLM
budget SEQUENTIALLY — ``_summarize_once`` (25 by default) then ``_brief_once``
(8) — so a single tick could hold the gate across up to 33 sequential LLM
calls. A file save arriving mid-tick waited out every remaining call before its
pipeline run could start; with a CLI provider each call is seconds, so the wait
ran to minutes.

The fix is NOT to release the gate between calls. The gate is what makes a tick
read ONE consistent ``graph.json``; handing it back mid-pass would let a compile
rewrite the graph underneath, so early briefs would describe a different graph
than late ones — trading a latency problem for a correctness one. The tick
ABANDONS its remaining budget instead and finishes early, which costs nothing:
warming is idempotent, and a scope or domain never tried is simply still cold on
the next tick.

What these tests pin, in order:

* the abandonment happens at all, measured in LLM CALLS rather than wall-clock;
* the interrupting pipeline actually gets the gate it was waiting for;
* the signal CANNOT latch — a stuck flag would disable both pre-warm ops
  permanently, which is worse than the latency it fixes;
* an abandoned domain takes NO strike and NO back-off, because it did not fail,
  it was never tried (that is #172's state, and abandoning must not perturb it);
* with nothing pending, both ops behave exactly as they did before.

Nothing here reaches a network or an LLM, and nothing compiles a graph. The
interrupting pipeline is a REAL ``Daemon._run_pipeline`` call on a second
thread, taking the REAL gate through the REAL ``_gate_for_pipeline`` — a test
that merely set ``_pipeline_pending`` by hand would stay green against a daemon
whose gate handling was wrong in every other respect.

Run with the project venv (NOT the shim)::

    .venv/bin/python -m pytest tests/test_daemon_abandons_budget.py -q
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import tesserae.project as project_mod
from tesserae.community_summaries import community_id
from tesserae.engine.daemon import Daemon
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)

from tests.test_daemon_brief import (
    FakeClock,
    RecordingAssociate,
    RecordingDistill,
    StubBriefClient,
)
from tests.test_daemon_brief import _make_project as _make_brief_project

# Enough cold communities that a budget of 25 never caps the loop: whatever
# stops it early has to be the abandonment, not the budget.
N_COMMUNITIES = 12
BIG_BUDGET = 25


# --------------------------------------------------------------------------- #
# Fixtures — a hand-built graph of many equal-sized communities. No compile.    #
# --------------------------------------------------------------------------- #


def _community_members(i: int) -> list:
    return [f"Concept:c{i:02d}m{j}" for j in range(3)]


def _many_community_project(tmp_path: Path) -> Path:
    """A project whose hierarchy holds :data:`N_COMMUNITIES` COLD communities.

    One level only, so no community has children and the §5.2 citation
    discipline asks the stub for nothing it cannot supply. Every community is
    the same shape, so the demand ranking falls through to its final, total
    tiebreak (cid) and the visit order is identical on every run.
    """
    nodes: list = []
    edges: list = []
    level: dict = {}
    for i in range(N_COMMUNITIES):
        members = _community_members(i)
        for mid in members:
            nodes.append(
                ResearchNode(
                    id=mid,
                    name=f"Node {mid.split(':')[1]}",
                    type=ResearchNodeType.CONCEPT,
                    description=f"description of {mid}",
                )
            )
        edges.append(
            ResearchEdge(
                source=members[0], target=members[1], type="shares_concept_with"
            )
        )
        level[community_id(members)] = members

    root = tmp_path / "proj"
    tess = root / ".tesserae"
    tess.mkdir(parents=True)
    (tess / "graph.json").write_text(
        ResearchGraph(nodes=nodes, edges=edges).to_json(indent=2), encoding="utf-8"
    )
    (tess / "hierarchy.json").write_text(
        json.dumps(
            {"schema_version": 1, "levels": [level], "hubs": []},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return root


class InterruptingSummaryClient:
    """Stub ``json_client`` that can queue a real pipeline run mid-pass.

    ``after_calls=N`` starts the pipeline once N calls have been made, then
    blocks until that run is genuinely parked on the compile gate. Without that
    handshake the test would race the scheduler and go green by luck.
    """

    def __init__(self, *, interrupt=None, after_calls: int = 0) -> None:
        self.calls: list = []
        self._interrupt = interrupt
        self._after_calls = after_calls

    def complete_json(self, *, system, user, schema_name, cache_key=None, **_):  # noqa: ANN001
        self.calls.append({"user": user, "cache_key": cache_key})
        if self._interrupt is not None and len(self.calls) == self._after_calls:
            self._interrupt.queue_a_pipeline_run()
        return {
            "title": "Warm Title",
            "description": "Pre-warmed community summary.",
            "tags": ["warm", "cache", "descent"],
        }


class PipelineInterrupt:
    """A REAL ``_run_pipeline`` on a second thread, contending for the gate."""

    def __init__(self) -> None:
        self.daemon: Daemon | None = None
        self.thread: threading.Thread | None = None
        self.ran = threading.Event()

    def pipeline(self, paths) -> None:
        """The daemon's ``run_pipeline=`` seam — runs INSIDE the gate."""
        self.ran.set()

    def queue_a_pipeline_run(self) -> None:
        assert self.daemon is not None
        assert self.thread is None, "the interrupt fires once per tick"
        self.thread = threading.Thread(
            target=self.daemon._run_pipeline, args=([],), daemon=True
        )
        self.thread.start()
        # Park until the run has raised the flag and blocked on the gate, so
        # the tick's next iteration is guaranteed to observe it.
        assert self.daemon._pipeline_pending.wait(timeout=10), "pipeline never queued"

    def join(self, timeout: float = 10.0) -> bool:
        if self.thread is not None:
            self.thread.join(timeout)
        return self.ran.is_set()


def _capture(daemon: Daemon, name: str) -> list:
    """Record what an op RETURNS without changing what it does."""
    real = getattr(daemon, name)
    seen: list = []

    def wrapper(graph):
        result = real(graph)
        seen.append(result)
        return result

    setattr(daemon, name, wrapper)
    return seen


def _make_daemon(root: Path, clock: FakeClock, **kwargs) -> Daemon:
    order: list = []
    kwargs.setdefault("summarize_budget", BIG_BUDGET)
    kwargs.setdefault("brief_budget", 0)
    return Daemon(
        root,
        consolidate_idle_seconds=300.0,
        monotonic=clock,
        distill=RecordingDistill(order),
        associate=RecordingAssociate(order),
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _reset_community_client():
    project_mod.set_community_summaries_test_client(None)
    yield
    project_mod.set_community_summaries_test_client(None)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    return _many_community_project(tmp_path)


# --------------------------------------------------------------------------- #
# The defect: a tick spends its whole budget while an edit waits               #
# --------------------------------------------------------------------------- #


def test_a_tick_abandons_its_remaining_budget_when_a_pipeline_is_waiting(project):
    """The load-bearing test. Fails before the fix: all 12 candidates warmed.

    Counted in LLM CALLS, not seconds, so it states the defect exactly and
    cannot flake on a slow machine.
    """
    interrupt = PipelineInterrupt()
    client = InterruptingSummaryClient(interrupt=interrupt, after_calls=3)
    clock = FakeClock(1000.0)
    daemon = _make_daemon(
        project, clock, summary_client=client, run_pipeline=interrupt.pipeline
    )
    interrupt.daemon = daemon
    seen = _capture(daemon, "_summarize_once")

    clock.advance(301)  # the idle window has elapsed -> the tick is due
    daemon._consolidation_tick()

    assert len(client.calls) == 3, (
        "the tick kept spending after a pipeline started waiting for the gate; "
        f"it made {len(client.calls)} of a possible {N_COMMUNITIES} calls"
    )
    (summary,) = seen
    assert summary["attempted"] == 3
    assert summary["summarized"] == [c for c in summary["summarized"]]
    assert len(summary["summarized"]) == 3

    # Visible, not silent: a bare early return is indistinguishable from
    # "there was nothing to warm".
    assert summary["abandoned"] == "pipeline pending"
    assert summary["unspent"] == BIG_BUDGET - 3

    # ...and the run it stood down for actually got the gate.
    assert interrupt.join(), "the queued pipeline never ran"


def test_the_work_abandoned_by_a_tick_is_still_there_for_the_next_one(project):
    """Lossless and resumable: warmed scopes stay warm, the rest stay cold.

    Nothing is double-charged either — the three already warmed cost no budget
    the second time round, so the second tick's calls are all NEW scopes.
    """
    interrupt = PipelineInterrupt()
    client = InterruptingSummaryClient(interrupt=interrupt, after_calls=3)
    clock = FakeClock(1000.0)
    daemon = _make_daemon(
        project, clock, summary_client=client, run_pipeline=interrupt.pipeline
    )
    interrupt.daemon = daemon
    seen = _capture(daemon, "_summarize_once")

    clock.advance(301)
    daemon._consolidation_tick()
    assert interrupt.join()
    first = seen[-1]
    assert first["attempted"] == 3

    # Second tick on the SAME daemon, with nothing pending this time.
    clock.advance(301)
    daemon._consolidation_tick()
    second = seen[-1]

    assert "abandoned" not in second, "nothing was pending; nothing should abandon"
    assert second["warm"] == 3, "the three warmed last tick must cost no budget now"
    assert second["attempted"] == N_COMMUNITIES - 3
    assert len(client.calls) == N_COMMUNITIES, "every community warmed exactly once"
    assert sorted(first["summarized"] + second["summarized"]) == sorted(
        {community_id(_community_members(i)) for i in range(N_COMMUNITIES)}
    )


def test_the_signal_cannot_latch_so_the_next_tick_warms_normally(project):
    """A stuck flag would disable both pre-warm ops forever — worse than the bug.

    ``_gate_for_pipeline`` is the ONLY writer: it sets the flag, then clears it
    as the first statement inside the gate and again in a ``finally``. So the
    flag is raised only while a run is BLOCKED, and a completed run always
    leaves it down.
    """
    interrupt = PipelineInterrupt()
    client = InterruptingSummaryClient(interrupt=interrupt, after_calls=1)
    clock = FakeClock(1000.0)
    daemon = _make_daemon(
        project, clock, summary_client=client, run_pipeline=interrupt.pipeline
    )
    interrupt.daemon = daemon

    clock.advance(301)
    daemon._consolidation_tick()
    assert interrupt.join(), "the queued pipeline never ran"

    # The pending work has completed. The flag must be down.
    assert not daemon._pipeline_pending.is_set()

    seen = _capture(daemon, "_summarize_once")
    clock.advance(301)
    daemon._consolidation_tick()

    assert "abandoned" not in seen[-1]
    assert seen[-1]["attempted"] == N_COMMUNITIES - 1, "the next tick must warm normally"


def test_a_pipeline_that_owns_the_gate_leaves_the_flag_down(project):
    """The clear happens INSIDE the gate, before any pipeline work.

    If it happened only after the gate was released, a tick that acquired the
    gate the instant a compile finished would read a stale set and abandon
    spuriously on every pass that followed a compile.
    """
    observed: list = []
    clock = FakeClock(1000.0)

    def _pipeline(paths) -> None:
        observed.append(daemon._pipeline_pending.is_set())

    daemon = _make_daemon(project, clock, run_pipeline=_pipeline)
    assert not daemon._pipeline_pending.is_set()
    daemon._run_pipeline([])

    assert observed == [False], "the flag must be down once the run owns the gate"
    assert not daemon._pipeline_pending.is_set()


def test_with_nothing_pending_a_tick_spends_its_whole_budget(project):
    """The control. Abandonment must not alter behaviour when nothing waits."""
    client = InterruptingSummaryClient()
    clock = FakeClock(1000.0)
    daemon = _make_daemon(project, clock, summary_client=client)
    seen = _capture(daemon, "_summarize_once")

    clock.advance(301)
    daemon._consolidation_tick()

    assert len(client.calls) == N_COMMUNITIES
    assert seen[-1]["attempted"] == N_COMMUNITIES
    assert "abandoned" not in seen[-1]
    assert "unspent" not in seen[-1]


def test_a_budget_that_runs_out_on_its_own_is_not_reported_as_abandoned(project):
    """Exhausting the budget and standing down are different events."""
    client = InterruptingSummaryClient()
    clock = FakeClock(1000.0)
    daemon = _make_daemon(project, clock, summary_client=client, summarize_budget=4)
    seen = _capture(daemon, "_summarize_once")

    clock.advance(301)
    daemon._consolidation_tick()

    assert seen[-1]["attempted"] == 4
    assert "abandoned" not in seen[-1], "a spent budget is not an abandonment"


# --------------------------------------------------------------------------- #
# BRIEF: the back-off state #172 added must survive an abandonment untouched    #
# --------------------------------------------------------------------------- #


class InterruptingBriefClient(StubBriefClient):
    """:class:`StubBriefClient` that queues a pipeline run after N calls."""

    def __init__(self, *, interrupt=None, after_calls: int = 0) -> None:
        super().__init__(None)
        self._interrupt = interrupt
        self._after_calls = after_calls

    def complete_json(self, **kwargs: object) -> dict:
        payload = super().complete_json(**kwargs)
        if self._interrupt is not None and len(self.calls) == self._after_calls:
            self._interrupt.queue_a_pipeline_run()
        return payload


def test_brief_also_honours_the_signal(tmp_path):
    """The summarize pass is 25 of the 33 calls, but brief must stand down too.

    Fixing only the newer op would leave the tick holding the gate across the
    older one's whole budget.
    """
    root = _make_brief_project(tmp_path)
    interrupt = PipelineInterrupt()
    client = InterruptingBriefClient(interrupt=interrupt, after_calls=1)
    clock = FakeClock(1000.0)
    daemon = _make_daemon(
        root,
        clock,
        summarize_budget=0,  # isolate the BRIEF op's spend
        brief_budget=8,
        summary_client=client,
        run_pipeline=interrupt.pipeline,
    )
    interrupt.daemon = daemon
    seen = _capture(daemon, "_brief_once")

    clock.advance(301)
    daemon._consolidation_tick()
    assert interrupt.join()

    brief = seen[-1]
    assert brief["attempted"] == 1
    assert brief["abandoned"] == "pipeline pending"
    assert brief["unspent"] == 8 - 1
    assert len(client.calls) == 1


def test_an_abandoned_domain_takes_no_strike_and_no_back_off(tmp_path):
    """The subtle one. An abandoned domain did not FAIL — it was never tried.

    #172's back-off holds a domain off for ``2**strikes`` ticks after a failure
    that burned a call. Charging that to a domain the tick simply never reached
    would push a perfectly warmable domain down the queue because an unrelated
    file was saved. The check therefore sits ABOVE both the warm read and the
    writer, so an abandonment writes no strike state at all.
    """
    root = _make_brief_project(tmp_path)
    interrupt = PipelineInterrupt()
    client = InterruptingBriefClient(interrupt=interrupt, after_calls=1)
    clock = FakeClock(1000.0)
    daemon = _make_daemon(
        root,
        clock,
        summarize_budget=0,
        brief_budget=8,
        summary_client=client,
        run_pipeline=interrupt.pipeline,
    )
    interrupt.daemon = daemon

    # Seed unrelated back-off state and prove an abandonment leaves it alone.
    daemon._brief_failures["someone-else"] = 2
    daemon._brief_retry_at["someone-else"] = 99

    seen = _capture(daemon, "_brief_once")
    clock.advance(301)
    daemon._consolidation_tick()
    assert interrupt.join()

    assert seen[-1]["failed"] == [], "an abandoned domain is not a failed one"
    assert daemon._brief_failures == {"someone-else": 2}
    assert daemon._brief_retry_at == {"someone-else": 99}

    # ...and the domain that was skipped is warmable on the very next tick,
    # not deferred behind a back-off it never earned.
    clock.advance(301)
    daemon._consolidation_tick()
    assert seen[-1]["deferred"] == []
    assert "abandoned" not in seen[-1]
    assert seen[-1]["attempted"] >= 1, "the abandoned domain must be reachable again"

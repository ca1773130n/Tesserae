"""The BRIEF leg of the daemon's sleep cycle — the charter's only warm path.

``charter.materialize_domain_brief`` shipped with no caller outside tests, so
its three readers all consumed an empty set forever: every ``graph_map``
domain card rendered ``quality: "structural"``, ``charter_route`` reported
``warm_rows: 0``, and lint's ``CHARTER_FALLBACK`` counted every live domain
cold. This suite covers the fourth consolidation op that fills it, wired into
the SAME tick, under the SAME compile gate, AFTER summarize, with its OWN
per-tick LLM budget.

The load-bearing test is :func:`test_one_daemon_tick_turns_all_three_readers_warm`.
Four PRs in this milestone each invented a different filename for this one
artifact and each was green in isolation, because nothing existed to
disagree with. Nothing here hand-writes a cache file: the REAL daemon pass
runs against a stub ``complete_json`` client and the three REAL readers are
then asked what they see. If the writer and any reader drift apart again,
that test is what fails.

No test here reaches a network, an LLM or a compile.

Run with the project venv (NOT the shim)::

    .venv/bin/python -m pytest tests/test_daemon_brief.py -q
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import tesserae.project as project_mod
from tesserae.charter import brief_cache_path
from tesserae.charter_route import charter_route
from tesserae.engine.daemon import Daemon
from tesserae.lint import WikiLinter
from tesserae.mcp_server import LLMWikiMCPServer
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.retrieval.hybrid import HashEmbeddingBackend

X_MEMBERS = ["Concept:x1", "Concept:x2", "Concept:x3"]
Y_MEMBERS = ["Concept:y1", "Concept:y2", "Concept:y3", "Concept:y4", "Concept:y5"]


# --------------------------------------------------------------------------- #
# Fixtures — a hand-built graph and charter. Nothing compiles.                  #
# --------------------------------------------------------------------------- #


class FakeClock:
    """A hand-advanced stand-in for ``time.monotonic`` (seconds, float)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class RecordingDistill:
    def __init__(self, order: list) -> None:
        self._order = order

    def __call__(self, project_root, graph, *, cfg=None, env=None):
        self._order.append("distill")
        return {"distilled": [], "skipped": [], "failed": []}


class RecordingAssociate:
    def __init__(self, order: list) -> None:
        self._order = order

    def __call__(self, project_root, graph, *, backend=None, **kwargs):
        self._order.append("associate")
        return {"associate_added": 0}


class StubBriefClient:
    """A ``json_client`` returning one valid envelope. No LLM, no network.

    Handed to the REAL writer through the REAL daemon pass rather than used to
    hand-build a cache file — a fixture that spells the cache convention out
    for itself is exactly how this milestone shipped four disagreeing
    filenames while every suite stayed green.

    The description cites the first child brief cid the prompt lists, because
    ``_cites_child_communities`` REJECTS and refuses to cache a summary of
    summaries that names none of its children. A stub returning fixed prose
    silently never warms any domain that has children — which is every
    division above a leaf.
    """

    TITLE = "The Zephyr Programme"
    DESCRIPTION = "Ingesting agent transcripts as they land, described rather than ranked."
    TAGS = ["zephyr", "transcripts"]

    def __init__(self, order: list | None = None) -> None:
        self._order = order
        self.calls: list = []

    def complete_json(self, **kwargs: object) -> dict:
        self.calls.append(kwargs)
        if self._order is not None:
            self._order.append("brief")
        cited = re.findall(r"CharterDomain:[a-z0-9][a-z0-9-]*", str(kwargs.get("user") or ""))
        description = self.DESCRIPTION
        if cited:
            description += f" It holds {cited[0]}."
        return {
            "title": self.TITLE,
            "description": description,
            "tags": list(self.TAGS),
        }


def _fixture_graph() -> ResearchGraph:
    nodes = [
        ResearchNode(
            id=nid,
            name=f"Node {nid.split(':')[1].upper()}",
            type=ResearchNodeType.CONCEPT,
            description=f"description of {nid}",
        )
        for nid in X_MEMBERS + Y_MEMBERS
    ]
    edges = [
        ResearchEdge(source="Concept:x1", target="Concept:x2", type="shares_concept_with"),
        ResearchEdge(source="Concept:y1", target="Concept:y2", type="shares_concept_with"),
        ResearchEdge(source="Concept:y1", target="Concept:y3", type="shares_concept_with"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def _domain(**overrides) -> dict:
    entry = {
        "tier": 1,
        "own_altitude": "division",
        "parent_slug": None,
        "child_slugs": [],
        "anchor_id": "",
        "direct_member_ids": [],
        "member_count": 0,
        "reorg_seq": 0,
        "status": "live",
        "transition": "founded",
        "unsplittable": False,
    }
    entry.update(overrides)
    return entry


def _charter_payload() -> dict:
    return {
        "version": 1,
        "reorg_seq": 0,
        "domains": {
            "atlas": _domain(
                anchor_id="Concept:x1",
                child_slugs=["atlas-core"],
                direct_member_ids=["Concept:x1"],
                member_count=3,
            ),
            "atlas-core": _domain(
                tier=2,
                own_altitude="department",
                parent_slug="atlas",
                anchor_id="Concept:x2",
                direct_member_ids=["Concept:x2", "Concept:x3"],
                member_count=2,
            ),
            "zephyr": _domain(
                anchor_id="Concept:y1",
                direct_member_ids=list(Y_MEMBERS),
                member_count=5,
            ),
            # A tombstone. It holds a member and is otherwise a perfectly good
            # candidate; it must never be briefed.
            "ghost": _domain(
                status="retired",
                transition="retired",
                anchor_id="Concept:x3",
                direct_member_ids=["Concept:x3"],
                member_count=1,
            ),
        },
        "member_index": {
            "Concept:x1": "atlas",
            "Concept:x2": "atlas-core",
            "Concept:x3": "atlas-core",
            **{mid: "zephyr" for mid in Y_MEMBERS},
        },
    }


def _hierarchy_payload() -> dict:
    from tesserae.community_summaries import community_id

    return {
        "schema_version": 1,
        "levels": [{community_id(X_MEMBERS): X_MEMBERS, community_id(Y_MEMBERS): Y_MEMBERS}],
        "hubs": ["Concept:y1"],
    }


def _make_project(tmp_path: Path, *, charter: object = "default") -> Path:
    root = tmp_path / "proj"
    tess = root / ".tesserae"
    tess.mkdir(parents=True)
    (tess / "config.json").write_text("{}\n", encoding="utf-8")
    (tess / "graph.json").write_text(_fixture_graph().to_json(indent=2), encoding="utf-8")
    (tess / "hierarchy.json").write_text(
        json.dumps(_hierarchy_payload(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    payload = _charter_payload() if charter == "default" else charter
    if payload is not None:
        (tess / "charter").mkdir(parents=True)
        text = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
        (tess / "charter" / "charter.json").write_text(text, encoding="utf-8")
    return root


def _make_daemon(root: Path, clock: FakeClock, order: list, **kwargs) -> Daemon:
    kwargs.setdefault("summarize_budget", 0)  # isolate the BRIEF op's spend
    return Daemon(
        root,
        consolidate_idle_seconds=300.0,
        monotonic=clock,
        distill=RecordingDistill(order),
        associate=RecordingAssociate(order),
        **kwargs,
    )


def _tick(root: Path, *, client=None, order=None, **kwargs):
    """Drive one DUE consolidation tick and return the client it spent."""
    order = [] if order is None else order
    client = StubBriefClient(order) if client is None else client
    clock = FakeClock(1000.0)
    daemon = _make_daemon(root, clock, order, summary_client=client, **kwargs)
    clock.advance(301)  # idle window elapsed -> the tick is due
    daemon._consolidation_tick()
    return client


def _ticker(root: Path, *, client=None, **kwargs):
    """One LONG-LIVED daemon whose ticks can be driven repeatedly.

    The back-off state that stops head-of-line starvation lives on the daemon
    instance, so a helper that builds a fresh daemon per tick cannot see it —
    and a starvation test written that way would pass against the starving
    code. This is the seam those tests must use.
    """
    order: list = []
    client = StubBriefClient(order) if client is None else client
    clock = FakeClock(1000.0)
    daemon = _make_daemon(root, clock, order, summary_client=client, **kwargs)

    def tick() -> dict:
        clock.advance(301)
        daemon._consolidation_tick()
        return {"client": client, "daemon": daemon}

    return tick, client


def _bump(root: Path, node_id: str, times: int) -> None:
    """Deterministic demand: repeated graph_map-style access bumps."""
    from tesserae.memory.store import bump_access

    for _ in range(times):
        bump_access(root / ".tesserae" / "sqlite.db", node_id, "2026-01-01T00:00:00Z")


@pytest.fixture(autouse=True)
def _reset_community_client():
    project_mod.set_community_summaries_test_client(None)
    yield
    project_mod.set_community_summaries_test_client(None)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    return _make_project(tmp_path)


# --------------------------------------------------------------------------- #
# The seam test: one pass, three readers                                       #
# --------------------------------------------------------------------------- #


def _map_cards(root: Path) -> dict:
    payload = LLMWikiMCPServer().call_tool(
        "graph_map", {"graph_path": str(root / ".tesserae" / "graph.json")}
    )
    return {card["scope_id"]: card for card in payload["cards"]}


def _route(root: Path, task: str) -> dict:
    return charter_route(
        _fixture_graph(),
        _charter_payload(),
        task,
        backend=HashEmbeddingBackend(),
        summary_cache_dir=root / ".tesserae" / "community_summaries",
    )


def _charter_fallback(root: Path):
    findings = [f for f in WikiLinter(root).run().findings if f.code == "CHARTER_FALLBACK"]
    return findings[0] if findings else None


def test_one_daemon_tick_turns_all_three_readers_warm(project) -> None:
    """THE test. One real daemon pass; all three shipped readers go warm.

    Every one of these three assertions was false before the daemon called the
    writer, and each was false for its own reason — a card that could never be
    ``llm``, a router whose corpus was always cold, a lint whose census could
    never find a warm file. They are asserted together, from ONE pass, because
    the failure this milestone actually had was the seam between them: four
    PRs, four filenames, four green suites.
    """
    _bump(project, "Concept:y1", 5)  # demand steers the single call to zephyr

    # ----- BEFORE: all three readers see nothing -------------------------- #
    assert _map_cards(project)["domain:zephyr"]["quality"] == "structural"
    cold_route = _route(project, "ingesting agent transcripts")
    assert cold_route["route_quality"]["warm_rows"] == 0
    assert _charter_fallback(project) is None  # 0-of-N warm is not a finding

    # ----- THE PASS: the real daemon, a stub client, no network ----------- #
    client = _tick(project, brief_budget=1)
    assert len(client.calls) == 1, "exactly one cold materialization was paid for"

    # ----- AFTER: reader 1 — mcp_server._domain_card via graph_map -------- #
    cards = _map_cards(project)
    zephyr = cards["domain:zephyr"]
    assert zephyr["quality"] == "llm"
    assert zephyr["title"] == StubBriefClient.TITLE
    assert zephyr["tags"] == StubBriefClient.TAGS
    assert cards["domain:atlas"]["quality"] == "structural", "budget was 1"

    # ----- AFTER: reader 2 — charter_route.warm_rows ---------------------- #
    warm_route = _route(project, "ingesting agent transcripts")
    assert warm_route["route_quality"]["warm_rows"] >= 1
    assert warm_route["routed"] is True
    assert [card["slug"] for card in warm_route["path"]] == ["zephyr"]
    assert warm_route["brief"]["quality"] == "llm"

    # ----- AFTER: reader 3 — lint's CHARTER_FALLBACK census --------------- #
    finding = _charter_fallback(project)
    assert finding is not None, "one warm domain makes the cold remainder reportable"
    assert "2 of 3 live charter domain(s)" in finding.message
    assert "atlas, atlas-core" in finding.message
    assert "zephyr" not in finding.message, "the warmed domain is no longer cold"

    # Printed so `pytest -s` shows the seam going warm in one place. Not
    # decoration: this is the evidence the three readers agree.
    written = brief_cache_path(
        _charter_payload(), "zephyr", cache_dir=project / ".tesserae" / "community_summaries"
    )
    print(
        "\n--- THREE READERS AFTER ONE DAEMON TICK -------------------------\n"
        f"  writer     : {written.relative_to(project)}  ({len(client.calls)} LLM call)\n"
        f"  reader 1   : graph_map domain:zephyr -> quality={zephyr['quality']!r} "
        f"title={zephyr['title']!r}\n"
        f"  reader 2   : charter_route -> warm_rows="
        f"{warm_route['route_quality']['warm_rows']} routed={warm_route['routed']} "
        f"brief.quality={warm_route['brief']['quality']!r}\n"
        f"  reader 3   : lint CHARTER_FALLBACK -> {finding.message.split(': ')[-1]}"
        " (zephyr no longer cold)\n"
        "-----------------------------------------------------------------"
    )


# --------------------------------------------------------------------------- #
# Budget                                                                       #
# --------------------------------------------------------------------------- #


def test_brief_budget_zero_is_an_honest_no_op(project) -> None:
    """``0`` disables the op: no LLM call, no cache file, no graph.json churn."""
    before = (project / ".tesserae" / "graph.json").read_bytes()

    client = _tick(project, brief_budget=0)

    assert client.calls == []
    assert not (project / ".tesserae" / "community_summaries").exists()
    assert (project / ".tesserae" / "graph.json").read_bytes() == before


def test_the_budget_caps_cold_materializations(project) -> None:
    """Three cold live domains, budget 2 -> exactly two LLM calls."""
    client = _tick(project, brief_budget=2)

    assert len(client.calls) == 2
    cache_dir = project / ".tesserae" / "community_summaries"
    charter = _charter_payload()
    warm = [
        slug
        for slug in ("atlas", "atlas-core", "zephyr")
        if brief_cache_path(charter, slug, cache_dir=cache_dir).is_file()
    ]
    assert len(warm) == 2
    # The tombstone is never a candidate, at any budget.
    assert not brief_cache_path(charter, "ghost", cache_dir=cache_dir).is_file()


def test_the_tombstone_is_skipped_even_when_the_budget_covers_everything(project) -> None:
    client = _tick(project, brief_budget=25)

    assert len(client.calls) == 3, "three live domains; ghost is retired"
    cache_dir = project / ".tesserae" / "community_summaries"
    assert not brief_cache_path(
        _charter_payload(), "ghost", cache_dir=cache_dir
    ).is_file()


def test_a_second_tick_over_unchanged_state_materializes_nothing(project) -> None:
    """A digest-valid warm brief is FREE — the budget is for cold work only."""
    first = _tick(project, brief_budget=25)
    assert len(first.calls) == 3

    second = _tick(project, brief_budget=25)

    assert second.calls == [], "warm briefs must cost neither a call nor budget"


def test_the_brief_budget_is_not_the_summarize_budget(project) -> None:
    """The two ops spend separately: a disabled BRIEF leaves SUMMARIZE intact
    and vice versa, so adding this op cannot change what summarize does."""
    only_summarize = _tick(project, brief_budget=0, summarize_budget=25)
    cache_dir = project / ".tesserae" / "community_summaries"
    charter = _charter_payload()
    assert only_summarize.calls, "summarize still spends its own budget"
    assert not any(
        brief_cache_path(charter, slug, cache_dir=cache_dir).is_file()
        for slug in ("atlas", "atlas-core", "zephyr")
    )


# --------------------------------------------------------------------------- #
# Determinism                                                                  #
# --------------------------------------------------------------------------- #


def test_ranking_is_a_total_order_across_two_independent_runs(tmp_path) -> None:
    """Two ticks over IDENTICAL state pick the same domains in the same order.

    The demand signal is deliberately flat here, so the run is decided
    entirely by the tiebreaks. Without an explicit total order the outcome
    would ride on dict iteration order and this would be a coin flip.
    """
    picked = []
    for run in range(2):
        root = _make_project(tmp_path / f"run{run}")
        client = _tick(root, brief_budget=2)
        charter = _charter_payload()
        cache_dir = root / ".tesserae" / "community_summaries"
        picked.append(
            [
                slug
                for slug in ("atlas", "atlas-core", "zephyr")
                if brief_cache_path(charter, slug, cache_dir=cache_dir).is_file()
            ]
        )
        assert len(client.calls) == 2
    assert picked[0] == picked[1]
    # No demand -> the size tiebreak decides: zephyr (5 present members) then
    # atlas (3, via its subtree) — atlas-core (2) loses.
    assert picked[0] == ["atlas", "zephyr"]


def test_demand_steers_the_budget_to_the_domain_agents_read(tmp_path) -> None:
    """``graph_map`` never bumps ``domain:<slug>`` — it bumps the node cards
    INSIDE a domain — so demand is Σ over members. Bumping one atlas-core
    member must lift atlas-core over the larger, unread zephyr."""
    root = _make_project(tmp_path)
    _bump(root, "Concept:x2", 9)

    _tick(root, brief_budget=1)

    charter = _charter_payload()
    cache_dir = root / ".tesserae" / "community_summaries"
    # atlas contains x2 in its subtree, so its demand ties atlas-core's;
    # the size tiebreak then puts atlas (3 members) first.
    assert brief_cache_path(charter, "atlas", cache_dir=cache_dir).is_file()
    assert not brief_cache_path(charter, "zephyr", cache_dir=cache_dir).is_file()


# --------------------------------------------------------------------------- #
# Honest degradation — every failure mode is an outcome, never an exception     #
# --------------------------------------------------------------------------- #


def test_no_charter_on_disk_is_a_no_op_not_a_failure(tmp_path) -> None:
    root = _make_project(tmp_path, charter=None)
    before = (root / ".tesserae" / "graph.json").read_bytes()

    client = _tick(root, brief_budget=25)

    assert client.calls == []
    assert (root / ".tesserae" / "graph.json").read_bytes() == before


def test_an_unreadable_charter_is_reported_not_raised(tmp_path) -> None:
    root = _make_project(tmp_path, charter="{ this is not json")
    daemon = _make_daemon(root, FakeClock(), [], summary_client=StubBriefClient())

    result = daemon._brief_once(_fixture_graph())

    assert result["briefed"] == []
    assert "unreadable" in result["skipped"]


def test_no_llm_client_is_an_honest_no_op(project) -> None:
    """No client means no work — and NOT a half-written cache."""
    daemon = _make_daemon(project, FakeClock(), [], summary_client=None)

    result = daemon._brief_once(_fixture_graph())

    assert result == {"briefed": [], "skipped": "no LLM client"}
    assert not (project / ".tesserae" / "community_summaries").exists()


def test_a_domain_whose_members_left_the_graph_costs_no_budget(tmp_path) -> None:
    """The writer returns None for an empty member set without calling an LLM,
    so charging budget for it would burn the tick on nothing."""
    payload = _charter_payload()
    payload["domains"]["orphan"] = _domain(
        anchor_id="Concept:gone", direct_member_ids=["Concept:gone"], member_count=1
    )
    root = _make_project(tmp_path, charter=payload)

    client = _tick(root, brief_budget=25)

    assert len(client.calls) == 3, "orphan is skipped, not attempted"
    assert brief_cache_path(
        payload, "orphan", cache_dir=root / ".tesserae" / "community_summaries"
    ).is_file() is False


def test_a_cyclic_child_tree_is_contained_to_the_domain_that_owns_it(tmp_path) -> None:
    """``domain_member_ids`` raises on a cycle. That must cost that domain its
    brief, not the whole pass."""
    payload = _charter_payload()
    payload["domains"]["atlas-core"]["child_slugs"] = ["atlas"]  # atlas <-> atlas-core
    root = _make_project(tmp_path, charter=payload)
    daemon = _make_daemon(root, FakeClock(), [], summary_client=StubBriefClient())

    result = daemon._brief_once(_fixture_graph())

    assert result["unwalkable"] == ["atlas", "atlas-core"]
    assert result["briefed"] == ["zephyr"], "the healthy domain still got briefed"


def test_a_writer_that_returns_none_is_recorded_as_failed(project) -> None:
    """``materialize_domain_brief`` absorbs its own failures and returns None.
    That is an outcome in the tick log, never an exception."""

    class RefusingClient:
        def complete_json(self, **_kwargs):
            raise RuntimeError("provider down")

    daemon = _make_daemon(project, FakeClock(), [], summary_client=RefusingClient())

    result = daemon._brief_once(_fixture_graph())

    assert result["briefed"] == []
    assert sorted(result["failed"]) == ["atlas", "atlas-core", "zephyr"]
    assert result["attempted"] == 3


# --------------------------------------------------------------------------- #
# Head-of-line starvation — the blocker                                        #
# --------------------------------------------------------------------------- #


class NonCitingClient:
    """Valid prose that cites no child. The REAL permanent-failure mode.

    ``_cites_child_communities`` rejects a summary of summaries naming none of
    its children, and ``summarize_community`` neither caches the rejection nor
    drops it from the client's own prompt cache. So every domain WITH children
    fails, deterministically, on every attempt, forever — while leaves succeed.
    That is not a contrived stub: on the live charter every tier-1 division is
    a router, which is exactly the population the default budget targets.
    """

    def __init__(self) -> None:
        self.calls: list = []

    def complete_json(self, **kwargs: object) -> dict:
        self.calls.append(kwargs)
        return {
            "title": "A summary that cites nobody",
            "description": "Prose describing the domain without naming a child.",
            "tags": ["orphaned", "citation"],
        }


def _starvation_charter() -> dict:
    """Three un-warmable routers ranked above three warmable leaves.

    Each division outranks every leaf by construction (a parent's member set
    contains its child's), so with a budget below the number of divisions the
    leaves sit permanently below the cut.
    """
    domains = {}
    index = {}
    for i, (parent_member, child_members) in enumerate(
        [
            ("Concept:x1", ["Concept:x2", "Concept:x3"]),
            ("Concept:y1", ["Concept:y2", "Concept:y3"]),
            ("Concept:y4", ["Concept:y5"]),
        ]
    ):
        parent, child = f"div{i}", f"div{i}-leaf"
        domains[parent] = _domain(
            anchor_id=parent_member,
            child_slugs=[child],
            direct_member_ids=[parent_member],
            member_count=1 + len(child_members),
        )
        domains[child] = _domain(
            tier=2,
            own_altitude="department",
            parent_slug=parent,
            anchor_id=child_members[0],
            direct_member_ids=list(child_members),
            member_count=len(child_members),
        )
        index[parent_member] = parent
        index.update({m: child for m in child_members})
    return {"version": 1, "reorg_seq": 0, "domains": domains, "member_index": index}


def test_unwarmable_domains_do_not_starve_the_warmable_ones(tmp_path) -> None:
    """THE blocker. Three permanently un-warmable routers rank above three
    warmable leaves and the budget is 2, so the loop's ``attempted >= budget``
    break can never reach a leaf on its own.

    Without back-off this spins forever: the rank is deterministic, a failed
    domain's rank never changes, and the same two routers are retried every
    tick at 12 ticks/hour, warming nothing, indefinitely. The assertion is
    bounded progress — this test FAILS (times out on the bound) against code
    that can spin.
    """
    root = _make_project(tmp_path, charter=_starvation_charter())
    tick, client = _ticker(root, client=NonCitingClient(), brief_budget=2)
    cache_dir = root / ".tesserae" / "community_summaries"
    charter = _starvation_charter()
    leaves = [f"div{i}-leaf" for i in range(3)]

    def warm_leaves() -> list:
        return [
            slug
            for slug in leaves
            if brief_cache_path(charter, slug, cache_dir=cache_dir).is_file()
        ]

    tick()
    assert warm_leaves() == [], "tick 1 is entirely consumed by the routers"

    # The bound: every warmable domain must be warm within a handful of ticks.
    for _ in range(6):
        tick()
        if len(warm_leaves()) == len(leaves):
            break
    assert warm_leaves() == leaves, (
        "the routers held the budget and the leaves never warmed — "
        "head-of-line starvation"
    )

    # And the failing routers must stop burning calls at the old rate. The
    # retry interval doubles per strike, so over a long window the spend on
    # domains that can never warm collapses.
    daemon = tick()["daemon"]
    strikes_before = dict(daemon._brief_failures)
    calls_before = len(client.calls)
    for _ in range(32):
        tick()
    spent = len(client.calls) - calls_before

    # 3 routers on doubling intervals (8, 16, 32 …) retry a handful of times
    # across 32 ticks. A flat retry would have spent 32 x 2 = 64.
    assert spent <= 12, (
        f"32 ticks cost {spent} calls on permanently un-warmable domains; "
        "an un-backed-off pass would have spent the whole budget every tick "
        "(32 x 2 = 64)"
    )
    assert all(
        daemon._brief_failures[slug] > strikes_before[slug] for slug in strikes_before
    ), "each retry must add a strike, which is what doubles the interval"


def test_a_zero_call_rejection_never_consumes_a_budget_slot(tmp_path) -> None:
    """S1(b): ``_brief_slug_ok`` refuses a mangled slug and the writer returns
    None having made NO LLM call. Charging that a slot spends the tick on
    nothing — every tick, because the rank never moves."""
    payload = _charter_payload()
    # Ranked FIRST: a division holding every member there is, so nothing can
    # outrank it. Its slug is one the writer will not key a file on.
    payload["domains"]["Bad Slug!"] = _domain(
        anchor_id="Concept:x1",
        direct_member_ids=list(X_MEMBERS) + list(Y_MEMBERS),
        member_count=8,
    )
    root = _make_project(tmp_path, charter=payload)

    client = _tick(root, brief_budget=1)

    assert len(client.calls) == 1, "the one slot went to a domain that can warm"
    cache_dir = root / ".tesserae" / "community_summaries"
    assert brief_cache_path(payload, "Bad Slug!", cache_dir=cache_dir) is None
    assert brief_cache_path(payload, "zephyr", cache_dir=cache_dir).is_file()


def test_the_intake_census_is_never_briefed(tmp_path) -> None:
    """S5: ``build_charter`` writes intake as tier-1 / altitude ``team`` /
    no anchor precisely because it has no subject. A brief would describe 25
    of its members and be served as ``quality: "llm"`` over the whole census;
    the roadmap ruled against exactly that."""
    payload = _charter_payload()
    payload["domains"]["intake"] = _domain(
        own_altitude="team",  # the writer's own "this is a census" marker
        anchor_id="",
        direct_member_ids=list(X_MEMBERS) + list(Y_MEMBERS),
        member_count=8,
    )
    root = _make_project(tmp_path, charter=payload)
    daemon = _make_daemon(root, FakeClock(), [], summary_client=StubBriefClient())

    result = daemon._brief_once(_fixture_graph())

    assert result["census"] == ["intake"]
    assert "intake" not in result["briefed"] and "intake" not in result["failed"]
    assert (
        brief_cache_path(
            payload, "intake", cache_dir=root / ".tesserae" / "community_summaries"
        ).is_file()
        is False
    )


def test_an_orphan_division_is_ranked_as_a_division_not_by_its_tier(tmp_path) -> None:
    """S4: ``graph_map``'s root set is ``live_divisions`` — no LIVE parent —
    not ``tier == 1``. A domain whose parent was retired without it is served
    at the root, so it must be warmed with the root, however deep its tier
    label says it is."""
    payload = _charter_payload()
    payload["domains"]["retired-parent"] = _domain(
        status="retired", transition="retired", anchor_id="Concept:x1"
    )
    # Deep tier, small, unread: it loses every tiebreak except the one that
    # matters — graph_map serves it at the root.
    payload["domains"]["orphan"] = _domain(
        tier=9,
        own_altitude="team",
        parent_slug="retired-parent",
        anchor_id="Concept:y5",
        direct_member_ids=["Concept:y5"],
        member_count=1,
    )
    payload["member_index"]["Concept:y5"] = "orphan"
    payload["domains"]["zephyr"]["direct_member_ids"] = Y_MEMBERS[:-1]
    root = _make_project(tmp_path, charter=payload)

    from tesserae.charter import live_divisions

    assert "orphan" in live_divisions(payload), "the fixture's premise"

    # Budget = exactly the number of live divisions: the root must be covered.
    client = _tick(root, brief_budget=len(live_divisions(payload)))

    cache_dir = root / ".tesserae" / "community_summaries"
    assert brief_cache_path(payload, "orphan", cache_dir=cache_dir).is_file(), (
        "an orphan division missed the tick that was budgeted to warm the root"
    )
    assert client.calls


def test_the_pass_runs_last_and_never_touches_graph_json(project) -> None:
    order: list = []
    before = (project / ".tesserae" / "graph.json").read_bytes()

    _tick(project, order=order, brief_budget=25)

    assert order[:2] == ["distill", "associate"]
    assert set(order[2:]) == {"brief"}, "BRIEF runs after the other ops"
    assert (project / ".tesserae" / "graph.json").read_bytes() == before

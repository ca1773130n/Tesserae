"""Validity-interval tests for the temporal fact projection.

Covers the `valid_from` timestamp ladder, `valid_to` population from
supersede/contradiction chains, the subject-side invalidation fix, and the
`facts_as_of` time-travel filter.
"""

import hashlib
import json

import pytest

from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
from tesserae.temporal import TemporalFactProjector, facts_as_of


def _finding(node_id, name, **metadata):
    return ResearchNode(
        id=node_id,
        name=name,
        type=ResearchNodeType.SESSION_INSIGHT,
        description=f"description of {name}",
        metadata=dict(metadata),
    )


def _doc(node_id="Paper:doc", name="Doc"):
    return ResearchNode(id=node_id, name=name, type=ResearchNodeType.PAPER)


def _fact(facts, predicate, subject_id=None):
    for fact in facts:
        if fact.predicate != predicate:
            continue
        if subject_id is not None and fact.subject_id != subject_id:
            continue
        return fact
    raise AssertionError(f"no {predicate} fact (subject={subject_id})")


# --------------------------------------------------------------- valid_from


def test_valid_from_uses_session_first_seen_at():
    """A finding carrying only ``first_seen_at`` must not read "undated"."""
    finding = _finding("SessionInsight:a", "A", first_seen_at="2026-03-01T10:00:00Z")
    doc = _doc()
    graph = ResearchGraph(
        nodes=[finding, doc],
        edges=[ResearchEdge(source=finding.id, target=doc.id, type="discussed_in")],
    )

    facts = TemporalFactProjector().project(graph)

    assert _fact(facts, "discussed_in").valid_from == "2026-03-01T10:00:00Z"


def test_last_accessed_at_never_becomes_valid_from():
    """last_accessed_at is mutable sidecar state — it must stay out of the artifact."""
    finding = _finding("SessionInsight:a", "A", last_accessed_at="2026-03-01T10:00:00Z")
    doc = _doc()
    graph = ResearchGraph(
        nodes=[finding, doc],
        edges=[ResearchEdge(source=finding.id, target=doc.id, type="discussed_in")],
    )

    facts = TemporalFactProjector().project(graph)

    assert _fact(facts, "discussed_in").valid_from == "undated"


def test_valid_from_is_max_over_both_endpoints():
    """A fact cannot predate either endpoint, so the LATER one wins."""
    early = _finding("SessionInsight:a", "A", first_seen_at="2026-03-01")
    late = ResearchNode(
        id="Paper:doc",
        name="Doc",
        type=ResearchNodeType.PAPER,
        metadata={"analysis_date": "2026-05-09"},
    )
    graph = ResearchGraph(
        nodes=[early, late],
        edges=[ResearchEdge(source=early.id, target=late.id, type="discussed_in")],
    )

    facts = TemporalFactProjector().project(graph)

    assert _fact(facts, "discussed_in").valid_from == "2026-05-09"


# ------------------------------------------------- valid_from via source_path
#
# Claim / EvidenceSpan nodes carry NO timestamp metadata of their own — they
# are minted from a document and inherit only its ``source_path``. On a corpus
# ingested into dated directories (``.../daily/2026-04-06/...``) that path
# already names the day the document was observed, in bytes already stored in
# graph.json. These tests pin that as the LAST rung of the ladder, under two
# bounds that keep it a SOURCE-derived timestamp rather than a disguised
# wall clock:
#
# 1. only the project-root-RELATIVE part of the path is scanned, so a dated
#    ANCESTOR of the checkout (``~/.blackhole/<proj>/2026-08-09/<slug>/``,
#    which OPERATIONS.md mandates for every agent worktree) cannot date the
#    corpus, and
# 2. the date must be a WHOLE directory segment, so a filename authored as
#    ``2026-08-02-handoff.md`` is not mistaken for an observation day.
#
# The root is read off the graph's own Session nodes (``project_root``), the
# same graph-intrinsic derivation ``okf`` already uses to relativise a
# resource — no argument, no cwd, no clock.

_ROOT = "/repo"


def _span(node_id, name, source_path, **metadata):
    return ResearchNode(
        id=node_id,
        name=name,
        type=ResearchNodeType.EVIDENCE_SPAN,
        description=f"description of {name}",
        source_path=source_path,
        metadata=dict(metadata),
    )


def _session(project_root=_ROOT):
    """A Session node — the only thing that declares the project root."""
    return ResearchNode(
        id="Session:root",
        name="a session",
        type=ResearchNodeType.SESSION,
        metadata={"session_id": "s1", "project_root": project_root},
    )


def _rooted(*nodes_and_edges):
    nodes, edges = nodes_and_edges
    return ResearchGraph(nodes=[*nodes, _session()], edges=edges)


def test_valid_from_falls_back_to_the_dated_ingest_path():
    """A Claim/EvidenceSpan with no metadata is dated by its own source_path."""
    path = "/repo/data/research/daily/2026-04-06/papers/2510.24907/paper.md"
    span = _span("EvidenceSpan:a", "some evidence", path)
    claim = ResearchNode(
        id="Claim:a",
        name="a claim",
        type=ResearchNodeType.CLAIM,
        source_path=path,
    )
    graph = _rooted(
        [claim, span],
        [ResearchEdge(source=claim.id, target=span.id, type="evidenced_by")],
    )

    facts = TemporalFactProjector().project(graph)

    assert _fact(facts, "evidenced_by").valid_from == "2026-04-06"


def test_dated_source_path_never_shadows_a_metadata_rung():
    """The path rung is LAST — an explicit metadata timestamp still wins."""
    span = _span(
        "EvidenceSpan:a",
        "some evidence",
        "/repo/data/research/daily/2026-04-06/p.md",
        analysis_date="2026-01-15",
    )
    doc = _doc()
    graph = _rooted(
        [span, doc],
        [ResearchEdge(source=span.id, target=doc.id, type="derived_from")],
    )

    facts = TemporalFactProjector().project(graph)

    assert _fact(facts, "derived_from").valid_from == "2026-01-15"


def test_impossible_date_in_source_path_is_not_a_timestamp():
    """A segment that only LOOKS like a date must not poison the ladder."""
    span = _span("EvidenceSpan:a", "some evidence", "/repo/runs/2026-13-45/p.md")
    doc = _doc()
    graph = _rooted(
        [span, doc],
        [ResearchEdge(source=span.id, target=doc.id, type="derived_from")],
    )

    facts = TemporalFactProjector().project(graph)

    assert _fact(facts, "derived_from").valid_from == "undated"


def test_deepest_dated_path_segment_wins():
    """With several dated segments the one nearest the file is the observation."""
    span = _span(
        "EvidenceSpan:a",
        "some evidence",
        "/repo/archive/2019-01-01/data/daily/2026-04-06/p.md",
    )
    doc = _doc()
    graph = _rooted(
        [span, doc],
        [ResearchEdge(source=span.id, target=doc.id, type="derived_from")],
    )

    facts = TemporalFactProjector().project(graph)

    assert _fact(facts, "derived_from").valid_from == "2026-04-06"


def test_a_dated_ancestor_of_the_project_root_never_dates_the_corpus():
    """D1: only the ROOT-RELATIVE path is scanned.

    ``~/.blackhole/<project>/<YYYY-MM-DD>/<slug>/`` is the mandated layout for
    every agent worktree on this machine, so an unbounded scan turns the
    directory a checkout happens to sit in into the corpus's observation date
    — a wall clock one indirection removed. The two paths below name the same
    file relative to their own roots and must therefore date identically.
    """
    plain = _span("EvidenceSpan:a", "some evidence", "/plain/proj/docs/notes/p.md")
    dated = _span("EvidenceSpan:a", "some evidence", "/blackhole/2026-08-09/proj/docs/notes/p.md")

    def project(span, root):
        graph = ResearchGraph(
            nodes=[span, _doc(), _session(project_root=root)],
            edges=[ResearchEdge(source=span.id, target="Paper:doc", type="derived_from")],
        )
        return _fact(TemporalFactProjector().project(graph), "derived_from").valid_from

    assert project(plain, "/plain/proj") == "undated"
    assert project(dated, "/blackhole/2026-08-09/proj") == "undated"


def test_source_path_outside_every_project_root_is_undated():
    """D1, stated rule: a path this project's ingest did not lay out is undated.

    A relativisation that fails means no segment of the path was chosen by
    this project, so none of them can be read as its observation day. Guessing
    from the absolute path is exactly what dates a graph by its checkout.
    """
    span = _span("EvidenceSpan:a", "some evidence", "/elsewhere/2026-04-06/p.md")
    doc = _doc()
    graph = _rooted(
        [span, doc],
        [ResearchEdge(source=span.id, target=doc.id, type="derived_from")],
    )

    facts = TemporalFactProjector().project(graph)

    assert _fact(facts, "derived_from").valid_from == "undated"


def test_a_dated_filename_is_not_an_observation_day():
    """D4: the date must be a WHOLE directory segment.

    ``docs/handoffs/2026-08-02-kg-growth.md`` is this repo's own documented
    document-naming convention (OPERATIONS.md), i.e. an AUTHORING date. The
    rung means the day Tesserae OBSERVED the file, so a filename must not
    enter it. 854 live nodes were dated this way.
    """
    span = _span("EvidenceSpan:a", "some evidence", "/repo/docs/handoffs/2026-08-02-kg-growth.md")
    doc = _doc()
    graph = _rooted(
        [span, doc],
        [ResearchEdge(source=span.id, target=doc.id, type="derived_from")],
    )

    facts = TemporalFactProjector().project(graph)

    assert _fact(facts, "derived_from").valid_from == "undated"


def test_a_dated_prefix_of_a_longer_segment_is_not_a_date():
    """D6: the match has a right boundary — ``2026-04-25-extra`` is not a date.

    Without one, any segment merely BEGINNING with ten date-shaped characters
    (a run id, a versioned directory) silently becomes an observation day.
    """
    span = _span("EvidenceSpan:a", "some evidence", "/repo/runs/2026-04-25-extra/p.md")
    doc = _doc()
    graph = _rooted(
        [span, doc],
        [ResearchEdge(source=span.id, target=doc.id, type="derived_from")],
    )

    facts = TemporalFactProjector().project(graph)

    assert _fact(facts, "derived_from").valid_from == "undated"


def test_a_newly_dated_endpoint_moves_valid_from_LATER_never_earlier():
    """D2: the path rung is additive per NODE but NOT per FACT.

    ``_latest_ts`` takes the MAX over both endpoints, so dating a previously
    undated endpoint can change which endpoint wins for an edge that already
    had a date. That is correct under the max rule — a fact cannot predate
    either endpoint — but it means "strictly additive" is false at fact level:
    1,429 live facts had a real date replaced (all of them moved LATER, none
    earlier; 1,426 survive the D1/D4 bounds). This pins the direction, which
    is the property that makes the overwrite safe.
    """
    dated = _span("EvidenceSpan:early", "early", None, analysis_date="2026-01-15")
    undated_but_pathed = ResearchNode(
        id="Claim:late",
        name="a claim",
        type=ResearchNodeType.CLAIM,
        source_path="/repo/data/daily/2026-06-01/p.md",
    )
    graph = _rooted(
        [dated, undated_but_pathed],
        [ResearchEdge(source=dated.id, target=undated_but_pathed.id, type="evidenced_by")],
    )

    facts = TemporalFactProjector().project(graph)

    assert _fact(facts, "evidenced_by").valid_from == "2026-06-01"


def test_projecting_facts_never_stamps_first_seen_at_onto_a_node():
    """D3 ruling, enforced: the path rung is READ-side and stays read-side.

    Stamping the derived day into ``metadata['first_seen_at']`` would bake a
    value computed from a directory name into graph.json for ~34,851 nodes,
    where a later correction to the rule could no longer reach it — and would
    overload a key that today means "the moment a session observed this"
    (session_graph/session_event write it; activity_summary and agent_distill
    read it) with a second, coarser provenance class.
    """
    span = _span("EvidenceSpan:a", "some evidence", "/repo/data/daily/2026-04-06/p.md")
    doc = _doc()
    graph = _rooted(
        [span, doc],
        [ResearchEdge(source=span.id, target=doc.id, type="derived_from")],
    )

    facts = TemporalFactProjector().project(graph)

    assert _fact(facts, "derived_from").valid_from == "2026-04-06"
    assert "first_seen_at" not in (span.metadata or {})
    assert "first_seen_at" not in (doc.metadata or {})


# ----------------------------------------------------------------- valid_to


def test_valid_to_set_from_earliest_superseder_timestamp():
    old = _finding("SessionInsight:old", "old", first_seen_at="2026-01-01")
    mid = _finding("SessionInsight:mid", "mid", first_seen_at="2026-02-01")
    new = _finding("SessionInsight:new", "new", first_seen_at="2026-03-01")
    doc = _doc()
    graph = ResearchGraph(
        nodes=[old, mid, new, doc],
        edges=[
            ResearchEdge(source=old.id, target=doc.id, type="discussed_in"),
            # Both supersede `old`; the EARLIER superseder closes the interval.
            ResearchEdge(source=new.id, target=old.id, type="supersedes"),
            ResearchEdge(source=mid.id, target=old.id, type="supersedes"),
        ],
    )

    facts = TemporalFactProjector().project(graph)
    fact = _fact(facts, "discussed_in", subject_id=old.id)

    assert fact.valid_to == "2026-02-01"
    assert fact.valid_to_basis == "supersedes"


def test_valid_to_stays_none_when_superseder_is_undated():
    """We know it ended; we do not know when. Never guess a boundary."""
    old = _finding("SessionInsight:old", "old", first_seen_at="2026-01-01")
    new = _finding("SessionInsight:new", "new")  # no timestamp at all
    doc = _doc()
    graph = ResearchGraph(
        nodes=[old, new, doc],
        edges=[
            ResearchEdge(source=old.id, target=doc.id, type="discussed_in"),
            ResearchEdge(source=new.id, target=old.id, type="supersedes"),
        ],
    )

    facts = TemporalFactProjector().project(graph)
    fact = _fact(facts, "discussed_in", subject_id=old.id)

    assert fact.valid_to is None
    assert fact.valid_to_basis is None
    assert fact.current is False


def test_superseded_subject_side_fact_is_not_current():
    """The whole reason "as of DATE" would lie: findings are SUBJECTS."""
    old = _finding("SessionInsight:old", "old", first_seen_at="2026-01-01")
    new = _finding("SessionInsight:new", "new", first_seen_at="2026-03-01")
    doc = _doc()
    graph = ResearchGraph(
        nodes=[old, new, doc],
        edges=[
            ResearchEdge(source=old.id, target=doc.id, type="discussed_in"),
            ResearchEdge(source=new.id, target=old.id, type="supersedes"),
        ],
    )

    facts = TemporalFactProjector().project(graph)
    fact = _fact(facts, "discussed_in", subject_id=old.id)
    supersede_fact = _fact(facts, "supersedes")

    assert fact.current is False
    assert fact.invalidated_by == [supersede_fact.id]


def test_supersedes_fact_itself_stays_current():
    """An invalidating fact is never ended by its own target."""
    old = _finding("SessionInsight:old", "old", first_seen_at="2026-01-01")
    new = _finding("SessionInsight:new", "new", first_seen_at="2026-03-01")
    graph = ResearchGraph(
        nodes=[old, new],
        edges=[ResearchEdge(source=new.id, target=old.id, type="supersedes")],
    )

    facts = TemporalFactProjector().project(graph)
    supersede_fact = _fact(facts, "supersedes")

    assert supersede_fact.current is True
    assert supersede_fact.valid_to is None


# ---------------------------------------------------------------- facts_as_of


def _interval_graph():
    old = _finding("SessionInsight:old", "old", first_seen_at="2026-01-01")
    new = _finding("SessionInsight:new", "new", first_seen_at="2026-03-01")
    doc = _doc()
    return ResearchGraph(
        nodes=[old, new, doc],
        edges=[
            ResearchEdge(source=old.id, target=doc.id, type="discussed_in"),
            ResearchEdge(source=new.id, target=doc.id, type="discussed_in"),
            ResearchEdge(source=new.id, target=old.id, type="supersedes"),
        ],
    )


def test_as_of_excludes_facts_that_ended_before_the_date():
    facts = TemporalFactProjector().project(_interval_graph())

    kept, _ = facts_as_of(facts, "2026-04-01")
    subjects = {fact.subject_id for fact in kept if fact.predicate == "discussed_in"}

    assert subjects == {"SessionInsight:new"}


def test_as_of_includes_facts_open_at_the_date():
    facts = TemporalFactProjector().project(_interval_graph())

    kept, _ = facts_as_of(facts, "2026-02-01")
    subjects = {fact.subject_id for fact in kept if fact.predicate == "discussed_in"}

    assert subjects == {"SessionInsight:old"}


def test_as_of_boundaries_are_half_open():
    facts = TemporalFactProjector().project(_interval_graph())
    old_fact = _fact(facts, "discussed_in", subject_id="SessionInsight:old")
    assert (old_fact.valid_from, old_fact.valid_to) == ("2026-01-01", "2026-03-01")

    at_start, _ = facts_as_of(facts, "2026-01-01")
    at_end, _ = facts_as_of(facts, "2026-03-01")

    assert old_fact.id in {f.id for f in at_start}  # valid_from is INCLUDED
    assert old_fact.id not in {f.id for f in at_end}  # valid_to is EXCLUDED


def test_as_of_reports_undated_included_count():
    dated = _finding("SessionInsight:a", "A", first_seen_at="2026-01-01")
    undated = _finding("SessionInsight:b", "B")
    doc = _doc()
    graph = ResearchGraph(
        nodes=[dated, undated, doc],
        edges=[
            ResearchEdge(source=dated.id, target=doc.id, type="discussed_in"),
            ResearchEdge(source=undated.id, target=doc.id, type="discussed_in"),
        ],
    )

    kept, undated_included = facts_as_of(TemporalFactProjector().project(graph), "2026-06-01")

    assert len(kept) == 2
    assert undated_included == 1


def test_as_of_unparseable_returns_error_not_full_corpus():
    facts = TemporalFactProjector().project(_interval_graph())

    with pytest.raises(ValueError):
        facts_as_of(facts, "last tuesday")


def test_as_of_query_does_not_perturb_written_temporal_facts(tmp_path):
    """Query/clock state must never leak into an artifact."""
    graph = _interval_graph()
    out = tmp_path / "temporal_facts.jsonl"
    projector = TemporalFactProjector()

    facts = projector.write_jsonl(graph, out)
    before = hashlib.sha256(out.read_bytes()).hexdigest()

    for pivot in ("2026-01-15", "2026-02-01", "2026-09-09T12:00:00Z"):
        facts_as_of(facts, pivot)
    projector.write_jsonl(graph, out)

    assert hashlib.sha256(out.read_bytes()).hexdigest() == before


def test_written_rows_carry_the_new_interval_keys(tmp_path):
    out = tmp_path / "temporal_facts.jsonl"
    TemporalFactProjector().write_jsonl(_interval_graph(), out)

    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    ended = [r for r in rows if r["subject_id"] == "SessionInsight:old" and r["predicate"] == "discussed_in"]

    assert len(ended) == 1
    assert ended[0]["valid_to"] == "2026-03-01"
    assert ended[0]["valid_to_basis"] == "supersedes"


# ------------------------------------- degenerate intervals / list ordering


def test_reasoning_edge_onto_its_own_superseder_keeps_a_usable_interval():
    """A fact whose own endpoint IS the superseder must not vanish from time.

    ``memory.contrast`` mints exactly this shape: ``B criticizes F`` alongside
    ``F supersedes B``. ``valid_from`` is ``max(ts(B), ts(F)) == ts(F)`` and the
    derived ``valid_to`` is also ``ts(F)``, so the half-open ``[from, to)`` is
    empty at EVERY instant and ``facts_as_of`` drops the fact at every pivot —
    the temporal feature silently not covering the edges this branch mints.
    """
    graph = ResearchGraph(
        nodes=[
            _finding("Insight:b", "B", first_seen_at="2026-02-01T00:00:00Z"),
            _finding("Insight:f", "F", first_seen_at="2026-04-01T00:00:00Z"),
        ],
        edges=[
            ResearchEdge(source="Insight:f", target="Insight:b", type="supersedes"),
            ResearchEdge(source="Insight:b", target="Insight:f", type="criticizes"),
        ],
    )
    facts = TemporalFactProjector().project(graph)
    crit = _fact(facts, "criticizes")

    # We know it ended; we do NOT know when. Same state an undated superseder
    # produces — never a guessed boundary, and never a silently empty one.
    assert crit.valid_from == "2026-04-01T00:00:00Z"
    assert crit.valid_to is None
    assert crit.valid_to_basis is None
    assert crit.current is False

    for pivot in ("2026-04-01T00:00:00Z", "2026-05-01T00:00:00Z"):
        kept, _ = facts_as_of(facts, pivot)
        assert any(f.id == crit.id for f in kept), f"invisible at {pivot}"


def test_a_real_boundary_after_the_start_is_still_recorded():
    """The degenerate-interval guard must not swallow genuine boundaries."""
    graph = ResearchGraph(
        nodes=[
            _finding("Insight:a", "A", first_seen_at="2026-01-01T00:00:00Z"),
            _finding("Insight:b", "B", first_seen_at="2026-02-01T00:00:00Z"),
            _finding("Insight:c", "C", first_seen_at="2026-03-01T00:00:00Z"),
        ],
        edges=[
            ResearchEdge(source="Insight:a", target="Insight:b", type="derived_from"),
            ResearchEdge(source="Insight:c", target="Insight:a", type="supersedes"),
        ],
    )
    derived = _fact(TemporalFactProjector().project(graph), "derived_from")

    assert derived.valid_from == "2026-02-01T00:00:00Z"
    assert derived.valid_to == "2026-03-01T00:00:00Z"
    assert derived.valid_to_basis == "supersedes"


def test_invalidated_by_is_sorted_not_edge_order():
    """``invalidated_by`` must be a function of the edge SET, not the edge LIST.

    Built by appending in ``graph.edges`` order, it made temporal_facts.jsonl
    inherit any upstream edge-order churn instead of damping it.
    """
    nodes = [
        _finding("Insight:a", "A", first_seen_at="2026-01-01T00:00:00Z"),
        _finding("Insight:x", "X", first_seen_at="2026-03-01T00:00:00Z"),
        _finding("Insight:y", "Y", first_seen_at="2026-03-01T00:00:00Z"),
        _finding("Insight:z", "Z", first_seen_at="2026-03-01T00:00:00Z"),
        _doc(),
    ]
    killers = [
        ResearchEdge(source="Insight:x", target="Insight:a", type="supersedes", evidence="x"),
        ResearchEdge(source="Insight:y", target="Insight:a", type="supersedes", evidence="y"),
        ResearchEdge(source="Insight:z", target="Insight:a", type="supersedes", evidence="z"),
    ]
    target = ResearchEdge(source="Insight:a", target="Paper:doc", type="discussed_in")

    seen = set()
    for order in ([0, 1, 2], [2, 0, 1], [1, 2, 0], [2, 1, 0]):
        graph = ResearchGraph(nodes=list(nodes), edges=[killers[i] for i in order] + [target])
        fact = _fact(TemporalFactProjector().project(graph), "discussed_in")
        assert fact.invalidated_by == sorted(fact.invalidated_by)
        seen.add(tuple(fact.invalidated_by))

    assert len(seen) == 1, f"edge order leaked into invalidated_by: {seen}"

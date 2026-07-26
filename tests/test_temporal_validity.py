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

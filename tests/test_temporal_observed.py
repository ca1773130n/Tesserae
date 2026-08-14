"""The transaction-time axis: when Tesserae LEARNED a fact.

Everything here guards one property — the two clocks stay two clocks. Valid
time comes off the sources and lives in the artifact; transaction time comes
off a wall clock and stops at SQLite. A test that could pass with one clock
answering both questions is not a test of this module.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tesserae.temporal import TemporalFact, facts_as_of
from tesserae.temporal_observed import (
    FactObservationLedger,
    fact_key,
    facts_observed_as_of,
    record_fact_observations,
    transaction_now,
)


def _fact(
    subject: str,
    predicate: str,
    obj: str,
    *,
    valid_from: str = "undated",
    evidence: str | None = None,
) -> TemporalFact:
    return TemporalFact(
        id=f"TemporalFact:{subject}|{predicate}|{obj}|{evidence or ''}",
        subject_id=subject,
        subject_name=subject,
        subject_type="SessionInsight",
        predicate=predicate,
        object_id=obj,
        object_name=obj,
        object_type="Paper",
        evidence=evidence,
        valid_from=valid_from,
    )


def _ledger(tmp_path) -> FactObservationLedger:
    return FactObservationLedger(tmp_path / "sqlite.db")


# ---------------------------------------------------------------------------
# The ledger itself
# ---------------------------------------------------------------------------


def test_first_sighting_is_write_once_while_last_seen_advances(tmp_path):
    """"When did we learn this" must not be overwritten by learning it again.

    If ``first_compile_at`` were in the conflict update it would track the
    latest compile, making it a duplicate of ``last_seen_compile_at`` and
    leaving the axis unable to answer the only question it exists for.
    """
    ledger = _ledger(tmp_path)
    facts = [_fact("A", "discussed_in", "Doc")]

    ledger.record(facts, "2026-01-01T00:00:00+00:00")
    ledger.record(facts, "2026-05-01T00:00:00+00:00")

    row = ledger.read()[("A", "discussed_in", "Doc")]
    assert row.first_compile_at == "2026-01-01T00:00:00+00:00"
    assert row.last_seen_compile_at == "2026-05-01T00:00:00+00:00"


def test_one_compile_stamps_one_instant_across_every_fact(tmp_path):
    """The clock ticks per compile, never per row.

    Two facts of the same compile that disagreed about when they were learned
    would make "as we knew it on DATE" depend on projection order.
    """
    ledger = _ledger(tmp_path)
    ledger.record(
        [
            _fact("A", "discussed_in", "Doc"),
            _fact("B", "discussed_in", "Doc"),
            _fact("C", "discussed_in", "Doc"),
        ],
        "2026-01-01T00:00:00+00:00",
    )

    stamps = {row.first_compile_at for row in ledger.read().values()}
    assert stamps == {"2026-01-01T00:00:00+00:00"}


def test_reworded_evidence_keeps_the_fact_s_first_sighting(tmp_path):
    """The key is the triple, not ``TemporalFact.id``, which hashes evidence.

    Re-extraction rewords spans routinely. Keying on the fact id would mint a
    fresh row every time an evidence string moved by a character and reset the
    first sighting to today — the axis would report the last re-extraction
    rather than the first time we learned anything.
    """
    ledger = _ledger(tmp_path)
    ledger.record(
        [_fact("A", "discussed_in", "Doc", evidence="the paper says X")],
        "2026-01-01T00:00:00+00:00",
    )
    ledger.record(
        [_fact("A", "discussed_in", "Doc", evidence="the paper states X")],
        "2026-05-01T00:00:00+00:00",
    )

    rows = ledger.read()
    assert len(rows) == 1
    assert rows[("A", "discussed_in", "Doc")].first_compile_at == "2026-01-01T00:00:00+00:00"


def test_duplicate_keys_inside_one_projection_collapse(tmp_path):
    ledger = _ledger(tmp_path)
    written = ledger.record(
        [
            _fact("A", "discussed_in", "Doc", evidence="one"),
            _fact("A", "discussed_in", "Doc", evidence="two"),
        ],
        "2026-01-01T00:00:00+00:00",
    )

    assert written == 1
    assert ledger.count() == 1


def test_for_project_refuses_to_create_a_sidecar_directory(tmp_path):
    """A READ must not mint ``.tesserae/`` as a side effect.

    Ad-hoc and store-backed graphs have nowhere to keep a sidecar; callers
    turn the None into a refusal rather than into an unpivoted answer.
    """
    assert FactObservationLedger.for_project(None) is None
    assert FactObservationLedger.for_project(tmp_path) is None
    assert not (tmp_path / ".tesserae").exists()

    (tmp_path / ".tesserae").mkdir()
    assert FactObservationLedger.for_project(tmp_path) is not None


def test_a_broken_sidecar_cannot_fail_a_compile(tmp_path):
    """Observability is not a compile output.

    Every artifact is already on disk when the write-through runs, so an
    unreadable sidecar must be logged and stepped over, not raised through a
    compile that otherwise succeeded.
    """
    broken = tmp_path / "sqlite.db"
    broken.write_bytes(b"this is not a database")

    assert record_fact_observations(broken, [_fact("A", "discussed_in", "Doc")], "2026-01-01") == 0


# ---------------------------------------------------------------------------
# The pivot, and the refusal to conflate it with the other one
# ---------------------------------------------------------------------------


def test_facts_learned_after_the_pivot_are_excluded(tmp_path):
    ledger = _ledger(tmp_path)
    early, late = _fact("A", "discussed_in", "Doc"), _fact("B", "discussed_in", "Doc")
    ledger.record([early], "2026-01-01T00:00:00+00:00")
    ledger.record([late], "2026-06-01T00:00:00+00:00")

    kept, unobserved = facts_observed_as_of(
        [early, late], ledger.read(), "2026-03-01"
    )

    assert [f.subject_id for f in kept] == ["A"]
    assert unobserved == 0


def test_a_fact_the_ledger_cannot_date_is_carried_through_and_counted(tmp_path):
    """Unknown transaction time is modelled as -infinity, exactly as an unknown
    ``valid_from`` is by ``facts_as_of`` — and, exactly as there, the carried
    rows are counted so a thin answer cannot look like a complete one."""
    ledger = _ledger(tmp_path)
    known, unknown = _fact("A", "discussed_in", "Doc"), _fact("B", "discussed_in", "Doc")
    ledger.record([known], "2026-01-01T00:00:00+00:00")

    kept, unobserved = facts_observed_as_of(
        [known, unknown], ledger.read(), "2026-03-01"
    )

    assert {f.subject_id for f in kept} == {"A", "B"}
    assert unobserved == 1


def test_an_unparseable_pivot_raises_rather_than_answering_over_the_corpus(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        facts_observed_as_of([_fact("A", "discussed_in", "Doc")], {}, "last tuesday")

    assert "last tuesday" in str(excinfo.value)


def test_the_two_axes_answer_two_different_questions(tmp_path):
    """THE anti-conflation test: neither pivot can stand in for the other.

    ``old`` describes something that was true in 2020 and was only learned
    last month; ``fresh`` describes something true this year that we have
    known for ages. A single clock cannot produce both answers, so a
    regression that fed one filter off the other's timestamps fails here.
    """
    ledger = _ledger(tmp_path)
    old = _fact("old", "discussed_in", "Doc", valid_from="2020-01-01")
    fresh = _fact("fresh", "discussed_in", "Doc", valid_from="2026-06-01")
    ledger.record([fresh], "2020-01-01T00:00:00+00:00")
    ledger.record([old], "2026-06-01T00:00:00+00:00")
    observations = ledger.read()

    # Valid time at the start of 2021: only the fact that was TRUE then.
    valid_kept, _undated = facts_as_of([old, fresh], "2021-01-01")
    assert [f.subject_id for f in valid_kept] == ["old"]

    # Transaction time at the same instant: only the fact we KNEW then — the
    # opposite row, from the opposite clock.
    observed_kept, _unobserved = facts_observed_as_of(
        [old, fresh], observations, "2021-01-01"
    )
    assert [f.subject_id for f in observed_kept] == ["fresh"]


def test_composing_both_pivots_narrows_to_the_intersection(tmp_path):
    """"What did we believe on DATE, as we knew it on DATE2" — the pair is the
    point, so neither may swallow the other."""
    ledger = _ledger(tmp_path)
    old = _fact("old", "discussed_in", "Doc", valid_from="2020-01-01")
    fresh = _fact("fresh", "discussed_in", "Doc", valid_from="2026-06-01")
    ledger.record([old, fresh], "2026-07-01T00:00:00+00:00")

    valid_kept, _undated = facts_as_of([old, fresh], "2021-01-01")
    both, _unobserved = facts_observed_as_of(valid_kept, ledger.read(), "2026-08-01")
    assert [f.subject_id for f in both] == ["old"]

    # Same valid-time pivot, but before we had learned anything: empty, not
    # "the same answer with a second parameter ignored".
    none_yet, _u = facts_observed_as_of(valid_kept, ledger.read(), "2026-01-01")
    assert none_yet == []


def test_transaction_now_is_the_wall_clock_and_not_a_source_timestamp():
    """The one place ``now()`` is allowed, because it never reaches an artifact.

    Reusing the compile's content-derived reference instant here (the one that
    keeps decay byte-stable) would collapse the two axes back into one: it is
    a fact about the sources, and this is a fact about us.
    """
    stamped = datetime.fromisoformat(transaction_now())

    assert stamped.tzinfo is not None
    assert abs(stamped - datetime.now(timezone.utc)) < timedelta(seconds=60)


def test_fact_key_is_the_triple():
    assert fact_key(_fact("A", "discussed_in", "Doc", evidence="x")) == (
        "A",
        "discussed_in",
        "Doc",
    )

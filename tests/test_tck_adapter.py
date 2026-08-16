"""What Tesserae does and does not do when driven as an agent-memory service.

These exercise :mod:`evals.tck.memory` — the Tesserae-backed core behind the
Neo4j agent-memory TCK adapter — and deliberately import nothing from the kit
itself, which is not on PyPI and absent from a fresh checkout. Offline, no
compile, no model, everything under ``tmp_path``.

Half of them assert refusals. That is the point: the recorded TCK result in
``evals/tck/README.md`` is 59 of 93 Bronze scenarios passing, and each of the 34
failures traces to a property pinned here. If a future change makes a refusal
stop refusing, the README's number is stale and this suite says so.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from evals.tck.memory import (
    ENTITY_TYPE_MAP,
    ENTITY_TYPE_REFUSALS,
    TCK_HARNESS,
    TesseraeMemory,
    Unsupported,
)
from tesserae.session_chunks import SessionChunksDB, chunks_db_path


@pytest.fixture()
def memory(tmp_path):
    return TesseraeMemory(tmp_path / "project")


# --------------------------------------------------------------------------- #
# What works: turns go in and come back out, without a compile                  #
# --------------------------------------------------------------------------- #


def test_messages_round_trip_in_insertion_order(memory):
    for content in ["First", "Second", "Third"]:
        memory.add_message("s1", "user", content)
    assert [m.content for m in memory.messages("s1")] == ["First", "Second", "Third"]


def test_sessions_are_isolated(memory):
    memory.add_message("alpha", "user", "in alpha")
    memory.add_message("beta", "user", "in beta")
    assert [m.content for m in memory.messages("alpha")] == ["in alpha"]
    assert [m.content for m in memory.messages("beta")] == ["in beta"]


def test_roles_and_unicode_survive_the_round_trip(memory):
    memory.add_message("s1", "system", "You are helpful")
    memory.add_message("s1", "user", "Hello 世界 \U0001f30d")
    stored = memory.messages("s1")
    assert [m.role for m in stored] == ["system", "user"]
    assert stored[1].content == "Hello 世界 \U0001f30d"


def test_duplicate_content_is_stored_separately(memory):
    """Only because the adapter stamps strictly increasing timestamps.

    The store's uniqueness key includes ``ts``, so identical content survives
    as separate rows exactly when the timestamps differ — see
    :func:`test_store_collapses_identical_turn_at_identical_timestamp` for the
    other half of that behaviour.
    """
    for _ in range(3):
        memory.add_message("s1", "user", "Duplicate")
    stored = memory.messages("s1")
    assert len(stored) == 3
    assert len({m.id for m in stored}) == 3


def test_conversation_id_is_stable_per_session(memory):
    assert memory.conversation_id("s1") == memory.conversation_id("s1")
    assert memory.conversation_id("s1") != memory.conversation_id("s2")


def test_sessions_reports_counts_and_bounds(memory):
    memory.add_message("alpha", "user", "one")
    memory.add_message("alpha", "user", "two")
    memory.add_message("beta", "user", "solo")
    by_id = {row[0]: row for row in memory.sessions()}
    assert by_id["alpha"][1] == 2
    assert by_id["beta"][1] == 1
    assert by_id["alpha"][2] <= by_id["alpha"][3]


def test_reset_empties_the_substrate(memory):
    memory.add_message("s1", "user", "gone after reset")
    memory.add_entity("Alice Johnson", "PERSON")
    assert memory.writes_path.is_file()
    memory.reset()
    assert memory.messages("s1") == []
    assert not memory.writes_path.exists()


# --------------------------------------------------------------------------- #
# What silently does not work, and is therefore made loud                       #
# --------------------------------------------------------------------------- #


def test_metadata_is_dropped_on_the_write_not_just_the_read(memory):
    """Nothing stores it, so nothing claims it — not even the returned object.

    ``record_turns`` writes the ``meta`` column as ``{"name": ...}`` and carries
    nothing else, so a metadata round-trip through this store is impossible.

    An earlier revision echoed the caller's dict back on the WRITE while the
    read dropped it. That is invisible to a caller who only inspects what
    ``add_message`` returned — which is exactly what SPEC-2.1.5 ("MUST preserve
    metadata") and SPEC-2.1.12 assert — so it bought two Bronze passes for a
    field the engine never persisted, and took the score from 57 to 59.

    Both ends now return ``{}``. This test is the ratchet: if the write ever
    starts echoing again, the reported number silently inflates by two and
    nothing else here would notice.
    """
    written = memory.add_message("s1", "user", "hi", metadata={"source": "test"})
    assert written.metadata == {}, "the write echoed metadata the store drops"
    assert memory.messages("s1")[0].metadata == {}


def test_store_collapses_identical_turn_at_identical_timestamp(tmp_path):
    """Two identical turns at one timestamp become one, and nothing raises.

    This is ``SessionChunksDB``'s idempotence guarantee — a re-delivered turn
    from a restarted tailer must not duplicate — reached directly, because the
    adapter's strictly increasing timestamps keep its own writes clear of it.
    """
    db = SessionChunksDB(chunks_db_path(tmp_path))
    stamp = datetime.now(timezone.utc).isoformat()
    turn = [{"timestamp": stamp, "role": "user", "text": "Same"}]
    assert db.record_turns(TCK_HARNESS, "s1", "s1", turn) == 1
    assert db.record_turns(TCK_HARNESS, "s1", "s1", turn) == 0


def test_add_message_raises_when_the_store_drops_the_row(memory, monkeypatch):
    """A silent zero-row write becomes a loud failure.

    ``record_turns`` returns 0 for an unparseable timestamp and for an identity
    collision alike, raising neither. A memory service that stores nothing and
    says nothing is the worse failure, so the adapter converts it.
    """
    monkeypatch.setattr(memory._db, "record_turns", lambda *a, **k: 0)
    with pytest.raises(RuntimeError, match="stored 0 of 1 turn"):
        memory.add_message("s1", "user", "dropped")


# --------------------------------------------------------------------------- #
# Refusals — each one a Bronze or Silver scenario this adapter fails on purpose #
# --------------------------------------------------------------------------- #


def test_search_is_refused_because_retrieval_needs_a_compile(memory):
    memory.add_message("s1", "user", "I love programming in Python")
    with pytest.raises(Unsupported) as caught:
        memory.search_messages("Python")
    assert "graph.json" in caught.value.blocked_by


def test_delete_and_clear_are_refused_because_the_stores_are_append_only(memory):
    written = memory.add_message("s1", "user", "cannot be removed")
    with pytest.raises(Unsupported) as deleted:
        memory.delete_message(written.id)
    assert "no delete" in deleted.value.blocked_by
    with pytest.raises(Unsupported):
        memory.clear_session("s1")


def test_preferences_and_facts_are_refused_for_want_of_a_vocabulary(memory):
    with pytest.raises(Unsupported) as pref:
        memory.add_preference("language", "Prefers Python")
    assert "no Preference node type" in pref.value.blocked_by
    with pytest.raises(Unsupported) as searched:
        memory.search_preferences("Python")
    assert searched.value.blocked_by == pref.value.blocked_by
    with pytest.raises(Unsupported) as fact:
        memory.add_fact("Alice", "WORKS_AT", "Acme")
    assert "ALLOWED_EDGE_TYPES" in fact.value.blocked_by


def test_reasoning_and_long_term_reads_are_refused(memory):
    with pytest.raises(Unsupported):
        memory.reasoning("start_trace")
    with pytest.raises(Unsupported) as read:
        memory.long_term_read("get_entity_by_name")
    assert "compile" in read.value.blocked_by


# --------------------------------------------------------------------------- #
# Entity writes reach the real overlay; three of five kit types have no home     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tck_type", sorted(ENTITY_TYPE_MAP))
def test_mapped_entity_types_reach_the_agent_write_overlay(memory, tck_type):
    stored = memory.add_entity("Alice Johnson", tck_type, description="Engineer")
    assert stored.write_status == "recorded"
    assert stored.tesserae_type == ENTITY_TYPE_MAP[tck_type]
    assert stored.tesserae_id.startswith(ENTITY_TYPE_MAP[tck_type] + ":")
    # Durable immediately, in the journal a compile replays — and invisible to
    # every read until that compile happens.
    assert stored.write_id in memory.writes_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("tck_type", sorted(ENTITY_TYPE_REFUSALS))
def test_unmapped_entity_types_are_refused_rather_than_relabelled(memory, tck_type):
    with pytest.raises(Unsupported) as caught:
        memory.add_entity("Somewhere", tck_type)
    assert caught.value.blocked_by == ENTITY_TYPE_REFUSALS[tck_type]


def test_the_same_entity_written_twice_is_one_node(memory):
    """Content-addressed identity, which SPEC-2.8.1 asks messages to violate."""
    first = memory.add_entity("Alice Johnson", "PERSON")
    second = memory.add_entity("Alice Johnson", "PERSON")
    assert second.write_status == "duplicate"
    assert first.tesserae_id == second.tesserae_id
    assert first.id == second.id

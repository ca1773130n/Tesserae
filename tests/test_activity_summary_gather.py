"""Gatherer tests for the activity summary.

Task 5 covers :func:`gather_messages` — turn-level, uncapped, windowed by each
turn's *own* timestamp (never the session's ``started_at``).

Task 6 covers :func:`gather_findings` — compiled session findings dated by their
*source turn's* timestamp (via the ``turns_by_session`` map from Task 5), never
by the session's long-running ``started_at``.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from tesserae.activity_summary import (
    gather_findings,
    gather_messages,
    resolve_windows,
)
from tesserae.harness_sessions import HarnessSession
from tesserae.harness_sessions_db import HarnessSessionsDB
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType


def _write_claude_transcript(p: Path, day: str, texts):
    """Write a minimal Claude JSONL transcript with one turn per text."""
    rows = []
    for i, t in enumerate(texts):
        role = "user" if i % 2 == 0 else "assistant"
        rows.append(
            {
                "type": role,
                "timestamp": f"{day}T10:0{i}:00Z",
                "message": {"role": role, "content": [{"type": "text", "text": t}]},
            }
        )
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _seed_session(tmp_path: Path, tx: Path) -> None:
    """Seed a real HarnessSessionsDB so gather_messages reads the live path."""
    db = HarnessSessionsDB(tmp_path / ".tesserae" / "harness_sessions.db")
    session = HarnessSession(
        id="s1",
        slug="sess",
        harness="claude",
        agent_label="claude",
        project_name="proj",
        project_root=str(tmp_path),
        # Deliberately OUTSIDE both test windows: proves the gatherer windows on
        # each turn's own timestamp, never on the session's started_at.
        started_at="2026-06-01T00:00:00Z",
        raw_transcript_path=str(tx),
    )
    db.upsert(session, jsonl_path=str(tx))


def test_gather_messages_only_in_window(tmp_path):
    tx = tmp_path / "sess.jsonl"
    _write_claude_transcript(tx, "2026-07-04", ["hi day4", "reply day4"])
    # Pin the transcript mtime inside the 07-04 window so the mtime prune is
    # deterministic regardless of the wall clock the test runs under.
    stamp = datetime(2026, 7, 4, 10, tzinfo=timezone.utc).timestamp()
    os.utime(tx, (stamp, stamp))
    _seed_session(tmp_path, tx)

    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    msgs, turns_by_session = gather_messages("proj", str(tmp_path), w)
    assert {m.text for m in msgs} == {"hi day4", "reply day4"}
    assert all(m.ts.date().isoformat() == "2026-07-04" for m in msgs)
    assert all(m.project == "proj" and m.session_id == "s1" for m in msgs)
    # The full turns list is captured for later tasks (finding resolution).
    assert turns_by_session["s1"]

    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    msgs5, _ = gather_messages("proj", str(tmp_path), w5)
    assert msgs5 == []  # day-4 turns excluded from the day-5 window


def test_finding_dated_by_source_turn(tmp_path):
    """A finding is dated by its latest source turn, not the session start.

    ``turns_by_session`` mirrors what :func:`gather_messages` returns (Task 5)
    and the compile-time index base: index 0 -> 07-04, index 1 -> 07-05.
    """
    turns_by_session = {
        "s1": [
            {"role": "assistant", "timestamp": "2026-07-04T10:00:00Z", "text": "a"},
            {"role": "assistant", "timestamp": "2026-07-05T10:00:00Z", "text": "b"},
        ]
    }
    # A real ResearchNode: findings carry their body as ``name`` and record the
    # source turns in ``metadata["turn_ids"]`` (session_graph.py mints them this
    # way; there is no ``metadata["body"]`` key in practice).
    node_valid = ResearchNode(
        id="SessionInsight:s1:zz",
        name="insight text",
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"session_id": "s1", "turn_ids": [0]},
    )
    # turn_ids out of range -> unresolvable -> skipped (never dated from start).
    node_out_of_range = ResearchNode(
        id="SessionDecision:s1:yy",
        name="undated decision",
        type=ResearchNodeType.SESSION_DECISION,
        metadata={"session_id": "s1", "turn_ids": [9]},
    )
    # session absent from the turns map -> unresolvable -> skipped.
    node_orphan = ResearchNode(
        id="SessionTODO:s2:xx",
        name="orphan todo",
        type=ResearchNodeType.SESSION_TODO,
        metadata={"session_id": "s2", "turn_ids": [0]},
    )
    # A non-finding node is ignored by the type filter.
    node_event = ResearchNode(
        id="Event:s1:ev",
        name="a session event",
        type=ResearchNodeType.EVENT,
        metadata={"session_id": "s1", "turn_ids": [0]},
    )
    graph = ResearchGraph(
        nodes=[node_valid, node_out_of_range, node_orphan, node_event]
    )

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    got = gather_findings("proj", graph, turns_by_session, w4)
    assert [f.body for f in got] == ["insight text"]
    assert [f.kind for f in got] == ["SessionInsight"]
    assert got[0].node_id == "SessionInsight:s1:zz"
    assert got[0].session_id == "s1"
    assert got[0].project == "proj"
    assert got[0].ts.date().isoformat() == "2026-07-04"

    # The day-5 window excludes the finding whose source turn is on 07-04.
    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    assert gather_findings("proj", graph, turns_by_session, w5) == []

"""Daily session chunking — parity, coverage gating, locking, fallback.

Core invariant (spec §2): for a fully covered PAST day, chunk-served results
must equal the raw scan's, byte for byte at the MessageItem level. Everything
else — uncovered days, today (still being written), non-KST-aligned windows,
corrupt/mismatched stores — must fall back to the raw scan and NEVER raise.

All date-sensitive fixtures pin a FIXED past day (recorded trap: sliding-window
assertions must pin their fixture day); the only test touching "today" derives
it from the same KST clock the module uses.
"""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tesserae.activity_summary as A
from tesserae.activity_summary import KST, resolve_windows, scan_messages
from tesserae.harness_sessions import _claude_project_dir
from tesserae.harness_sessions_db import HarnessSessionsDB
from tesserae.engine.session_tail import SessionTailer
from tesserae.session_chunks import (
    ALL_HARNESSES,
    SessionChunksDB,
    backfill,
    chunks_db_path,
    day_label,
    record_live_turns,
    served_messages_for_windows,
)

# Fixed, always-past fixture day (KST). 2026-07-01T10:00Z == 19:00 KST same day.
DAY = "2026-07-01"


# --------------------------------------------------------------------------- #
# Fixture builders (mirroring tests/test_activity_summary_gather.py)          #
# --------------------------------------------------------------------------- #
def _write_claude_transcript(p: Path, day: str, texts, *, with_tool: bool = False):
    """Minimal Claude JSONL transcript: one turn per text, optional tool turn."""
    rows = []
    for i, t in enumerate(texts):
        role = "user" if i % 2 == 0 else "assistant"
        content = [{"type": "text", "text": t}]
        if with_tool and i == len(texts) - 1:
            content.append({"type": "tool_use", "name": "Bash", "input": {"cmd": "ls"}})
        rows.append(
            {
                "type": role,
                "timestamp": f"{day}T10:0{i}:00Z",
                "message": {"role": role, "content": content},
            }
        )
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _make_claude_root(tmp_path: Path, project_root: Path, day: str, texts, **kw):
    root = tmp_path / ".claude-acct"
    slug = _claude_project_dir(project_root)
    sdir = root / "projects" / slug
    sdir.mkdir(parents=True, exist_ok=True)
    tx = sdir / "sess.jsonl"
    _write_claude_transcript(tx, day, texts, **kw)
    stamp = datetime.fromisoformat(f"{day}T10:00:00+00:00").timestamp()
    os.utime(tx, (stamp, stamp))
    return root


def _isolate_roots(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(A, "discover_harness_roots", lambda: [root])
    monkeypatch.setattr(A, "_root_supports_claude", lambda r: True)
    monkeypatch.setattr(A, "_root_supports_codex", lambda r: False)


def _items(out) -> list:
    """Flatten scan_messages output to a sorted, comparable list of tuples."""
    flat = []
    for name, by_label in out.items():
        for label, msgs in by_label.items():
            for m in msgs:
                flat.append(
                    (name, label, m.ts, m.role, m.name, m.text, m.session_id, m.harness)
                )
    return sorted(flat, key=repr)


def _turns(day: str, texts) -> list:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "timestamp": f"{day}T10:0{i}:00Z", "text": t}
        for i, t in enumerate(texts)
    ]


# --------------------------------------------------------------------------- #
# Day labels + store primitives                                               #
# --------------------------------------------------------------------------- #
def test_day_label_matches_kst_window_semantics():
    """A UTC evening instant belongs to the NEXT KST day — exactly like the
    Window.label the activity summary buckets by."""
    ts = datetime(2026, 6, 30, 16, 30, tzinfo=timezone.utc)  # 01:30 KST Jul 1
    assert day_label(ts) == "2026-07-01"
    assert day_label(datetime(2026, 6, 30, 14, 0, tzinfo=timezone.utc)) == "2026-06-30"


def test_record_turns_idempotent_on_redelivery(tmp_path):
    """Re-delivered turns (tailer offset-0 replay / backfill over tailer rows)
    are INSERT OR IGNOREd — the count does not grow."""
    db = SessionChunksDB(tmp_path / "chunks.db")
    turns = _turns(DAY, ["hello", "world"])
    assert db.record_turns("claude-code", "/x/s.jsonl", "acct:s", turns) == 2
    assert db.record_turns("claude-code", "/x/s.jsonl", "acct:s", turns) == 0
    assert len(db.turns_for_day(DAY)) == 2


def test_record_turns_preserves_duplicates_within_one_batch(tmp_path):
    """Two genuinely identical turns in ONE delivery both survive (occurrence
    suffix), while a second delivery of the same batch still dedupes."""
    db = SessionChunksDB(tmp_path / "chunks.db")
    turn = {"role": "tool", "timestamp": f"{DAY}T10:00:00Z", "text": "{}", "name": "Bash"}
    batch = [turn, dict(turn)]
    assert db.record_turns("claude-code", "/x/s.jsonl", "acct:s", batch) == 2
    assert db.record_turns("claude-code", "/x/s.jsonl", "acct:s", batch) == 0
    rows = db.turns_for_day(DAY)
    assert len(rows) == 2
    assert all(r["name"] == "Bash" for r in rows)


def test_turns_without_timestamp_are_skipped(tmp_path):
    db = SessionChunksDB(tmp_path / "chunks.db")
    n = db.record_turns(
        "claude-code", "/x/s.jsonl", "acct:s",
        [{"role": "user", "timestamp": "", "text": "undated"}],
    )
    assert n == 0
    assert db.turns_for_day(DAY) == []


def test_schema_version_mismatch_drops_and_rebuilds_empty(tmp_path):
    """A schema bump wipes turns AND coverage — the reader's coverage gate then
    forces the raw fallback, so a rebuilt-empty store is always correct."""
    path = tmp_path / "chunks.db"
    db = SessionChunksDB(path)
    db.record_turns("claude-code", "/x/s.jsonl", "acct:s", _turns(DAY, ["hi"]))
    db.mark_coverage([DAY], source="backfill")
    con = sqlite3.connect(path)
    con.execute("update meta set value = '999' where key = 'schema_version'")
    con.commit()
    con.close()

    reopened = SessionChunksDB(path)
    assert reopened.covered_days() == set()
    assert reopened.turns_for_day(DAY) == []


# --------------------------------------------------------------------------- #
# Parity — the core invariant                                                 #
# --------------------------------------------------------------------------- #
def test_parity_backfilled_day_equals_raw_scan(tmp_path, monkeypatch):
    """Raw-scan a pinned past day, backfill, then scan again with the raw
    scanner rigged to explode: the chunk-served result must be identical."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    root = _make_claude_root(
        tmp_path, project_root, DAY, ["hi day1", "reply day1", "more", "done"],
        with_tool=True,
    )
    _isolate_roots(monkeypatch, root)
    windows = resolve_windows(day=DAY)  # default tz == KST, matching the store

    raw = scan_messages([("proj", project_root)], windows)
    assert raw["proj"][DAY], "fixture must produce in-window turns"
    assert any(m.role == "tool" and m.name == "Bash" for m in raw["proj"][DAY])

    result = backfill(project_root)  # since=None -> earliest observed turn day
    assert not result.skipped
    assert result.turns_inserted == len(raw["proj"][DAY])
    assert DAY in result.days

    def _boom(*_a, **_k):  # the covered day must never touch a transcript
        raise AssertionError("raw scan ran for a chunk-covered day")

    monkeypatch.setattr(A, "_parse_jsonl", _boom)
    served = scan_messages([("proj", project_root)], windows)
    assert _items(served) == _items(raw)


def test_backfill_since_covers_zero_turn_days(tmp_path, monkeypatch):
    """With --since, coverage is claimed for every observed day up to yesterday,
    including empty ones — a covered-empty day serves [] without a raw scan."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    root = _make_claude_root(tmp_path, project_root, DAY, ["hi", "yo"])
    _isolate_roots(monkeypatch, root)

    next_day = "2026-07-02"
    result = backfill(project_root, since=DAY)
    assert not result.skipped
    assert DAY in result.days and next_day in result.days
    today = day_label(datetime.now(KST))
    assert today not in result.days  # today is never covered

    monkeypatch.setattr(A, "_parse_jsonl", lambda *_a, **_k: pytest.fail("raw scan ran"))
    out = scan_messages([("proj", project_root)], resolve_windows(day=next_day))
    assert out["proj"][next_day] == []


# --------------------------------------------------------------------------- #
# Coverage gating — everything not provably covered stays a raw scan          #
# --------------------------------------------------------------------------- #
def test_uncovered_day_still_raw_scans(tmp_path, monkeypatch):
    """A chunks db with rows but NO coverage row is ignored (raw scan runs)."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    root = _make_claude_root(tmp_path, project_root, DAY, ["raw wins", "ok"])
    _isolate_roots(monkeypatch, root)

    db = SessionChunksDB(chunks_db_path(project_root))
    db.record_turns(
        "claude-code", "/x/s.jsonl", "acct:s", _turns(DAY, ["stale chunk text"])
    )  # rows but no day_coverage

    calls = []
    real = A._parse_jsonl
    monkeypatch.setattr(A, "_parse_jsonl", lambda p: calls.append(p) or real(p))
    out = scan_messages([("proj", project_root)], resolve_windows(day=DAY))
    assert calls, "uncovered day must raw-scan"
    assert {m.text for m in out["proj"][DAY]} == {"raw wins", "ok"}


def test_partial_harness_coverage_is_not_served(tmp_path, monkeypatch):
    """Tailer coverage for only one harness does not satisfy a scan that wants
    both — the day stays raw until backfill (or the other harness) covers it."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    root = _make_claude_root(tmp_path, project_root, DAY, ["real", "turns"])
    _isolate_roots(monkeypatch, root)

    db = SessionChunksDB(chunks_db_path(project_root))
    db.record_turns("claude-code", "/x/s.jsonl", "acct:s", _turns(DAY, ["chunk only"]))
    db.mark_coverage([DAY], source="tailer", harnesses=("claude-code",))

    out = scan_messages([("proj", project_root)], resolve_windows(day=DAY))
    assert {m.text for m in out["proj"][DAY]} == {"real", "turns"}


def test_today_is_never_chunk_served(tmp_path, monkeypatch):
    """Even a coverage row for today is ignored — today is still being written,
    so the mtime-floor raw scan stays authoritative."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    today = day_label(datetime.now(KST))
    root = _make_claude_root(tmp_path, project_root, today, ["live text", "now"])
    tx = root / "projects" / _claude_project_dir(project_root) / "sess.jsonl"
    os.utime(tx, None)  # mtime "now" so today's window floor admits it
    _isolate_roots(monkeypatch, root)

    db = SessionChunksDB(chunks_db_path(project_root))
    db.record_turns(
        "claude-code", "/x/s.jsonl", "acct:s",
        [{"role": "user", "timestamp": datetime.now(KST).isoformat(), "text": "stale"}],
    )
    db.mark_coverage([today], source="tailer", harnesses=ALL_HARNESSES)

    out = scan_messages([("proj", project_root)], resolve_windows(day=today))
    texts = {m.text for m in out["proj"][today]}
    assert texts == {"live text", "now"}
    assert "stale" not in texts


def test_non_kst_aligned_windows_stay_raw(tmp_path, monkeypatch):
    """A UTC day window shares the label but not the bounds — it must not be
    served from the KST-bucketed store (parity would silently break)."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    root = _make_claude_root(tmp_path, project_root, DAY, ["utc window", "hi"])
    _isolate_roots(monkeypatch, root)
    backfill(project_root, since=DAY)

    windows = resolve_windows(day=DAY, tz=timezone.utc)
    assert served_messages_for_windows(project_root, windows) == {}
    out = scan_messages([("proj", project_root)], windows)
    assert {m.text for m in out["proj"][DAY]} == {"utc window", "hi"}


# --------------------------------------------------------------------------- #
# Fallback — any store problem degrades to the raw scan, never raises          #
# --------------------------------------------------------------------------- #
def test_missing_db_falls_back_to_raw(tmp_path, monkeypatch):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    root = _make_claude_root(tmp_path, project_root, DAY, ["no db", "here"])
    _isolate_roots(monkeypatch, root)
    out = scan_messages([("proj", project_root)], resolve_windows(day=DAY))
    assert {m.text for m in out["proj"][DAY]} == {"no db", "here"}


def test_corrupt_db_falls_back_to_raw(tmp_path, monkeypatch):
    project_root = tmp_path / "proj"
    project_root.mkdir()
    root = _make_claude_root(tmp_path, project_root, DAY, ["survives", "garbage"])
    _isolate_roots(monkeypatch, root)
    db_path = chunks_db_path(project_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"this is not a sqlite database at all")

    assert served_messages_for_windows(project_root, resolve_windows(day=DAY)) == {}
    out = scan_messages([("proj", project_root)], resolve_windows(day=DAY))
    assert {m.text for m in out["proj"][DAY]} == {"survives", "garbage"}


# --------------------------------------------------------------------------- #
# Backfill locking — non-blocking, skip-if-held                               #
# --------------------------------------------------------------------------- #
def test_backfill_skips_when_lock_held(tmp_path):
    root = tmp_path / "proj"
    (root / ".tesserae").mkdir(parents=True)
    holder = (root / ".tesserae" / "session_chunks.lock").open("a+")
    try:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = backfill(root)
        assert result.skipped
        assert result.turns_inserted == 0 and result.days_covered == 0
        assert not (root / ".tesserae" / "session_chunks.db").exists()
    finally:
        holder.close()


# --------------------------------------------------------------------------- #
# Live writer — the tailer hook path                                          #
# --------------------------------------------------------------------------- #
def test_record_live_turns_appends_and_marks_tailer_coverage(tmp_path):
    db = SessionChunksDB(tmp_path / "chunks.db")
    n = record_live_turns(
        db, "claude-code", Path("/x/s.jsonl"), "acct:s", _turns(DAY, ["a", "b"])
    )
    assert n == 2
    rows = db.turns_for_day(DAY)
    assert [r["text"] for r in rows] == ["a", "b"]
    assert all(r["session_id"] == "acct:s" for r in rows)
    cov = db.coverage_rows()
    assert cov == [
        {"day": DAY, "harness": "claude-code", "source": "tailer", "updated_at": cov[0]["updated_at"]}
    ]


def test_record_live_turns_never_raises(tmp_path, monkeypatch):
    db = SessionChunksDB(tmp_path / "chunks.db")
    monkeypatch.setattr(
        db, "record_turns", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
    )
    assert record_live_turns(db, "codex", Path("/x"), "k", _turns(DAY, ["x"])) == 0


# --------------------------------------------------------------------------- #
# Tailer integration — chunk hook fires with normalized identity, never kills  #
# the tick (unit-level on the callback; fixtures mirror test_session_tailer)   #
# --------------------------------------------------------------------------- #
def _claude_line(project: Path, role: str, text: str, ts: str) -> str:
    msg = (
        {"role": "user", "content": text}
        if role == "user"
        else {"role": "assistant", "content": [{"type": "text", "text": text}]}
    )
    return json.dumps(
        {
            "type": role,
            "timestamp": ts,
            "cwd": str(project),
            "sessionId": "abc",
            "gitBranch": "main",
            "message": msg,
        }
    )


def _tailer_fixture(tmp_path, on_chunk_turns):
    project = tmp_path / "demo-project"
    project.mkdir(exist_ok=True)
    claude_root = tmp_path / ".claude-acct"
    d = claude_root / "projects" / _claude_project_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "session-abc.jsonl"
    path.write_text(
        _claude_line(project, "user", "chunk me", f"{DAY}T10:00:00Z")
        + "\n"
        + _claude_line(project, "assistant", "chunked.", f"{DAY}T10:01:00Z")
        + "\n",
        encoding="utf-8",
    )
    sink: list = []
    db = HarnessSessionsDB(tmp_path / ".tesserae" / "harness_sessions.db")
    tailer = SessionTailer(
        project_root=project,
        sessions_db=db,
        on_new_turns=lambda p, t: sink.append((p, t)),
        watch_roots=[claude_root],
        poll_interval=0.0,
        on_chunk_turns=on_chunk_turns,
    )
    return tailer, path, claude_root, sink


def test_tailer_invokes_chunk_hook_with_scan_identity(tmp_path):
    """The hook receives the activity-summary harness name and the exact
    "<account>:<stem>" session key ``iter_project_transcripts`` would yield —
    the identities chunk-served MessageItems must reproduce."""
    calls: list = []
    tailer, path, claude_root, sink = _tailer_fixture(
        tmp_path, lambda h, p, k, t: calls.append((h, p, k, t))
    )
    tailer.tick()

    assert len(calls) == 1
    harness, got_path, key, turns = calls[0]
    assert harness == "claude-code"
    assert got_path == path
    assert key == f"{claude_root.name}:{path.stem}"
    assert [t["text"] for t in turns] == ["chunk me", "chunked."]
    assert sink and sink[0][1] == turns  # same delta the trigger callback saw


def test_tailer_survives_raising_chunk_hook(tmp_path):
    """A broken chunk writer must not break tailing: the tick neither raises
    nor drops the on_new_turns trigger delivery."""
    def explode(*_a):
        raise RuntimeError("chunk store on fire")

    tailer, _path, _root, sink = _tailer_fixture(tmp_path, explode)
    tailer.tick()  # must not raise
    assert len(sink) == 1
    assert [t["text"] for t in sink[0][1]] == ["chunk me", "chunked."]


def test_backfill_resumes_from_last_covered_day(tmp_path, monkeypatch):
    """Second backfill without --since resumes from the LAST covered day (one-day
    overlap, deduped) instead of re-walking full history from epoch."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    root = _make_claude_root(tmp_path, project_root, DAY, ["hi", "yo"])
    _isolate_roots(monkeypatch, root)

    first = backfill(project_root)
    assert not first.skipped
    assert first.days and first.days[0] == DAY

    second = backfill(project_root)
    assert not second.skipped
    assert second.days == [max(first.days)]  # resume window, not epoch
    assert DAY not in second.days or DAY == max(first.days)

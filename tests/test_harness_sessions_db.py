"""Deterministic tests for the SQLite :class:`HarnessSessionsDB` append store.

No pytest-asyncio, no sleeps, no file-watching. Run with::

    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_harness_sessions_db.py -q
"""

from __future__ import annotations

import logging
import sqlite3

from tesserae.harness_sessions import HarnessSession
from tesserae.harness_sessions_db import HarnessSessionsDB


def make_session(
    *,
    sid: str,
    project_root: str,
    turns: list | None = None,
    started_at: str = "2026-06-01T10:00:00Z",
) -> HarnessSession:
    """Tiny HarnessSession factory exercising the turns metadata payload."""
    return HarnessSession(
        id=sid,
        slug="test-session",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="proj",
        project_root=project_root,
        started_at=started_at,
        metadata={"turns": turns if turns is not None else [
            {"role": "user", "text": "hello", "timestamp": "2026-06-01T10:00:00Z"},
            {"role": "assistant", "text": "hi", "timestamp": "2026-06-01T10:00:01Z"},
        ]},
    )


def test_upsert_roundtrip_no_glob_files(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    db = HarnessSessionsDB(tmp_path / ".tesserae" / "harness_sessions.db")
    session = make_session(sid="claude-code:s1", project_root=str(project))
    db.upsert(session, jsonl_path=tmp_path / "s1.jsonl", last_offset=0)

    listed = db.list_for_project(project)
    assert len(listed) == 1
    assert listed[0].id == "claude-code:s1"
    assert listed[0].metadata["turns"][0]["text"] == "hello"

    # The whole point of SESS-03: the read path is SQLite, not a glob.
    assert list(tmp_path.rglob("*.json")) == []


def test_upsert_idempotent_no_duplicate(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    db = HarnessSessionsDB(tmp_path / ".tesserae" / "db.sqlite")
    db.upsert(make_session(sid="dup", project_root=str(project)))
    db.upsert(
        make_session(
            sid="dup",
            project_root=str(project),
            turns=[{"role": "user", "text": "first", "timestamp": "t0"},
                   {"role": "assistant", "text": "second", "timestamp": "t1"},
                   {"role": "user", "text": "third-extra", "timestamp": "t2"}],
        ),
        last_offset=4096,
    )

    listed = db.list_for_project(project)
    assert len(listed) == 1
    assert listed[0].metadata["turns"][-1]["text"] == "third-extra"

    with sqlite3.connect(db.path) as con:
        count = con.execute("select count(*) from sessions where id = 'dup'").fetchone()[0]
    assert count == 1


def test_list_for_project_filters_by_project(tmp_path):
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"
    proj_a.mkdir()
    proj_b.mkdir()
    db = HarnessSessionsDB(tmp_path / "db.sqlite")
    db.upsert(make_session(sid="a1", project_root=str(proj_a)))
    db.upsert(make_session(sid="b1", project_root=str(proj_b)))

    a_sessions = db.list_for_project(proj_a)
    assert [s.id for s in a_sessions] == ["a1"]


def test_offset_persists_across_fresh_instance(tmp_path):
    db_path = tmp_path / "db.sqlite"
    jsonl = tmp_path / "transcript.jsonl"
    db1 = HarnessSessionsDB(db_path)
    db1.set_offset(jsonl, 4096)

    # A second instance on the same file must read the persisted offset.
    db2 = HarnessSessionsDB(db_path)
    assert db2.get_offset(jsonl) == 4096
    assert db2.all_offsets() == {str(jsonl): 4096}


def test_get_offset_default_zero(tmp_path):
    db = HarnessSessionsDB(tmp_path / "db.sqlite")
    assert db.get_offset(tmp_path / "unknown.jsonl") == 0


def test_count_sessions_distinguishes_empty(tmp_path):
    """Empty store reports 0 (quiet fallback); populated store reports n."""
    project = tmp_path / "proj"
    project.mkdir()
    db = HarnessSessionsDB(tmp_path / "db.sqlite")
    assert db.count_sessions() == 0
    db.upsert(make_session(sid="s1", project_root=str(project)))
    assert db.count_sessions() == 1


def test_upsert_with_offset_is_atomic(tmp_path):
    """The session row and its tail offset are written in ONE transaction."""
    project = tmp_path / "proj"
    project.mkdir()
    jsonl = tmp_path / "t.jsonl"
    db = HarnessSessionsDB(tmp_path / "db.sqlite")
    db.upsert_with_offset(
        make_session(sid="s1", project_root=str(project)),
        jsonl_path=jsonl,
        last_offset=2048,
    )
    assert db.count_sessions() == 1
    assert db.get_offset(jsonl) == 2048


def test_upsert_with_offset_crash_no_torn_write(tmp_path, monkeypatch):
    """Simulate a crash mid-write: neither the session NOR the offset commits.

    Because both writes share one transaction, a failure before ``commit`` rolls
    BOTH back — the offset can never get ahead of (or behind) the stored
    session, so resume can't re-emit already-ingested turns (Codex #5).
    """
    project = tmp_path / "proj"
    project.mkdir()
    jsonl = tmp_path / "t.jsonl"
    db = HarnessSessionsDB(tmp_path / "db.sqlite")
    # First, a clean atomic write establishes offset 1000.
    db.upsert_with_offset(
        make_session(sid="s1", project_root=str(project)),
        jsonl_path=jsonl,
        last_offset=1000,
    )

    # Now simulate a crash AFTER the session row is staged but BEFORE commit
    # while advancing to offset 2000.
    original_set = HarnessSessionsDB._set_offset

    def boom(con, path, offset):  # noqa: ANN001
        raise RuntimeError("simulated crash between turn-write and offset-advance")

    monkeypatch.setattr(HarnessSessionsDB, "_set_offset", staticmethod(boom))
    try:
        db.upsert_with_offset(
            make_session(sid="s1", project_root=str(project)),
            jsonl_path=jsonl,
            last_offset=2000,
        )
    except RuntimeError:
        pass
    monkeypatch.setattr(HarnessSessionsDB, "_set_offset", staticmethod(original_set))

    # The torn write rolled back: the offset is STILL 1000, not 2000. On resume
    # the tailer reads 1000 and re-reads from there — no offset that skips past
    # un-stored turns, no offset that disagrees with the persisted session.
    assert db.get_offset(jsonl) == 1000


# --------------------------------------------------------------------------- #
# Compile-read fallback: visible error vs quiet empty (Codex #7)              #
# --------------------------------------------------------------------------- #


def _merge_session_graph_with(project_root):
    """Drive ProjectWiki._merge_session_graph and return its result graph."""
    from tesserae.project import ProjectWiki
    from tesserae.research_graph import ResearchGraph

    wiki = ProjectWiki(project_root)
    return wiki._merge_session_graph(ResearchGraph(), cfg={"sessions": {"enabled": True}})


def test_compile_quiet_fallback_on_empty_db(tmp_path, caplog):
    """An EMPTY live db falls back to the legacy glob with NO warning (#7)."""
    project = tmp_path / "proj"
    project.mkdir()
    db_path = project / ".tesserae" / "harness_sessions.db"
    HarnessSessionsDB(db_path)  # valid, empty store

    with caplog.at_level(logging.WARNING, logger="tesserae.project"):
        _merge_session_graph_with(project)

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_compile_warns_loudly_on_db_read_error(tmp_path, caplog):
    """A CORRUPT live db must log a WARNING (with exc_info), not fall silently (#7)."""
    project = tmp_path / "proj"
    project.mkdir()
    db_dir = project / ".tesserae"
    db_dir.mkdir(parents=True)
    # Non-SQLite bytes at the db path → read raises, exercising the error branch.
    (db_dir / "harness_sessions.db").write_bytes(b"this is not a sqlite database\x00\x01")

    with caplog.at_level(logging.WARNING, logger="tesserae.project"):
        _merge_session_graph_with(project)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "corrupt live db must produce a visible WARNING"
    assert any("sessions db" in r.getMessage() for r in warnings)
    # exc_info must be attached so the traceback is visible in logs.
    assert any(r.exc_info is not None for r in warnings)

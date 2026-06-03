"""Deterministic tests for the SQLite :class:`HarnessSessionsDB` append store.

No pytest-asyncio, no sleeps, no file-watching. Run with::

    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_harness_sessions_db.py -q
"""

from __future__ import annotations

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

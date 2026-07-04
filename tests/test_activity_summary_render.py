"""Renderer + orchestration tests for the activity summary (Task 9).

:func:`render_day` turns one window's gathered items into a deterministic
markdown block (fixed subsection order, ``_none_`` for empty sections, every
list sorted by ``(ts, id)``). :func:`build_summary` resolves the registered
projects, runs the five gatherers per project per window, renders each day, and
returns the combined markdown (plus any written paths). The end-to-end test
seeds a real ``HarnessSessionsDB`` and a real git repo so the whole gather →
render pipeline runs against live sources — proving both correct windowing (day-5
activity excluded from a day-4 summary) and byte-for-byte determinism.
"""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import tesserae.mcp_server as mcp_server
from tesserae.activity_summary import (
    CommitItem,
    FindingItem,
    Window,
    build_summary,
    render_day,
    resolve_windows,
)
from tesserae.harness_sessions import HarnessSession
from tesserae.harness_sessions_db import HarnessSessionsDB


# --------------------------------------------------------------------------- #
# render_day — deterministic single-day markdown
# --------------------------------------------------------------------------- #
def test_render_day_has_all_sections_with_none_placeholders():
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    md = render_day(w, {}, {})
    assert md.startswith("## 2026-07-04")
    for heading in (
        "### Sessions",
        "### Files touched",
        "### Decisions & Insights",
        "### Commits",
        "### Pull Requests",
        "### Ingested docs",
    ):
        assert heading in md
    # Every empty subsection renders the literal placeholder.
    assert md.count("_none_") == 6


def test_render_day_sorts_and_is_reproducible():
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    t1 = datetime(2026, 7, 4, 9, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 4, 11, tzinfo=timezone.utc)
    # Pass commits out of order; the renderer must sort by (ts, sha).
    commits = [
        CommitItem(ts=t2, sha="bbbb2222", author="T", subject="later", project="p"),
        CommitItem(ts=t1, sha="aaaa1111", author="T", subject="earlier", project="p"),
    ]
    findings = [
        FindingItem(ts=t1, kind="SessionInsight", body="an insight",
                    project="p", session_id="s1", node_id="SessionInsight:s1:zz"),
    ]
    md = render_day(w, {"commits": commits, "findings": findings}, {})
    # 'earlier' (09:00) must precede 'later' (11:00) regardless of input order.
    assert md.index("earlier") < md.index("later")
    assert "an insight" in md
    assert "**SessionInsight**" in md
    # Same inputs -> byte-identical render.
    assert render_day(w, {"commits": list(reversed(commits)), "findings": findings}, {}) == md


# --------------------------------------------------------------------------- #
# build_summary — end-to-end over live sources
# --------------------------------------------------------------------------- #
def _write_claude_transcript(p: Path, day: str, texts) -> None:
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


def _seed_session(root: Path, session_id: str, tx: Path) -> None:
    db = HarnessSessionsDB(root / ".tesserae" / "harness_sessions.db")
    session = HarnessSession(
        id=session_id,
        slug=session_id,
        harness="claude",
        agent_label="claude",
        project_name="proj",
        project_root=str(root),
        # Deliberately outside every test window: proves windowing is per-turn,
        # never on the session's long-running started_at.
        started_at="2026-06-01T00:00:00Z",
        raw_transcript_path=str(tx),
    )
    db.upsert(session, jsonl_path=str(tx))


def _git(root: Path, *args, env=None) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def _commit_on(root: Path, day: str, filename: str, subject: str) -> None:
    (root / filename).write_text("x", encoding="utf-8")
    _git(root, "add", filename)
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": f"{day}T10:00:00+00:00",
        "GIT_COMMITTER_DATE": f"{day}T10:00:00+00:00",
    }
    _git(root, "commit", "-q", "-m", subject, env=env)


def _stamp_mtime(path: Path, when: datetime) -> None:
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def _build_project(tmp_path: Path) -> None:
    # Two sessions: one with a turn on 07-04, one on 07-05.
    tx4 = tmp_path / "sess4.jsonl"
    tx5 = tmp_path / "sess5.jsonl"
    _write_claude_transcript(tx4, "2026-07-04", ["hi day4", "reply day4"])
    _write_claude_transcript(tx5, "2026-07-05", ["hi day5", "reply day5"])
    _stamp_mtime(tx4, datetime(2026, 7, 4, 10, tzinfo=timezone.utc))
    _stamp_mtime(tx5, datetime(2026, 7, 5, 10, tzinfo=timezone.utc))
    _seed_session(tmp_path, "s4", tx4)
    _seed_session(tmp_path, "s5", tx5)
    # A git repo with one commit per day (author date fixed, tz-explicit).
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "T")
    _commit_on(tmp_path, "2026-07-04", "a.txt", "day4 commit")
    _commit_on(tmp_path, "2026-07-05", "b.txt", "day5 commit")


def _register_single_project(monkeypatch, name: str, root: Path) -> None:
    monkeypatch.setattr(
        mcp_server.ProjectRegistry,
        "iter_registered_projects",
        lambda self: iter([(name, root)]),
    )


def test_e2e_deterministic_day(tmp_path, monkeypatch):
    _build_project(tmp_path)
    _register_single_project(monkeypatch, "proj", tmp_path)

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    res = build_summary([w4], ["proj"], synthesize=False, write=False)

    assert "day4 commit" in res.markdown
    assert "day5 commit" not in res.markdown  # day-5 activity excluded
    assert "a.txt" in res.markdown  # files-touched aggregate from the day-4 commit
    assert "b.txt" not in res.markdown
    assert "s4" in res.markdown  # the day-4 session is listed
    assert "s5" not in res.markdown  # the day-5 session contributed nothing on 07-04
    assert res.paths == []  # write=False

    # Determinism: identical inputs -> byte-identical output.
    res2 = build_summary([w4], ["proj"], synthesize=False, write=False)
    assert res.markdown == res2.markdown


def test_build_summary_writes_per_project_file(tmp_path, monkeypatch):
    _build_project(tmp_path)
    _register_single_project(monkeypatch, "proj", tmp_path)

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    res = build_summary([w4], ["proj"], synthesize=False, write=True)

    assert len(res.paths) == 1
    written = res.paths[0]
    assert written.name == "daily-2026-07-04.md"
    assert written.parent == tmp_path / ".tesserae" / "summaries" / "proj"
    assert "day4 commit" in written.read_text(encoding="utf-8")


def test_build_summary_default_scope_is_all_registered(tmp_path, monkeypatch):
    _build_project(tmp_path)
    _register_single_project(monkeypatch, "proj", tmp_path)

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    # project_names=None -> every registered project (here just "proj").
    res = build_summary([w4], None, synthesize=False, write=False)
    assert "# proj" in res.markdown
    assert "day4 commit" in res.markdown

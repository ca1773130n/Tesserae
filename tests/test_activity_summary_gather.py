"""Gatherer tests for the activity summary.

Task 5 covers :func:`gather_messages` — turn-level, uncapped, windowed by each
turn's *own* timestamp (never the session's ``started_at``).

Task 6 covers :func:`gather_findings` — compiled session findings dated by their
*source turn's* timestamp (via the ``turns_by_session`` map from Task 5), never
by the session's long-running ``started_at``.

Task 7 covers :func:`gather_git` (commits, read at summary time from a real git
repo, windowed by author date) and :func:`gather_prs` (GitHub PR events, gated on
an ``origin`` remote + ``gh`` — a non-repo / missing ``gh`` drops the section
gracefully instead of raising).

Task 8 covers :func:`gather_docs` — ingested ``SourceDocument`` nodes dated by
their *own* timestamp (metadata ``updated_at``/``created``/``analysis_date`` when
present, else the ``source_path`` file's mtime resolved against the project
root), never by a session's ``started_at``. Best-effort: an unresolvable/
unreadable doc is skipped, never raised.
"""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import tesserae.activity_summary as A
from tesserae.activity_summary import (
    gather_docs,
    gather_findings,
    gather_git,
    gather_prs,
    resolve_windows,
    scan_messages,
)
from tesserae.harness_sessions import _claude_project_dir
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


def _make_claude_root(tmp_path: Path, project_root: Path, day: str, texts):
    """Create a fake Claude account root holding one in-window transcript for
    ``project_root`` (under its encoded ``projects/<slug>/`` dir), mtime-pinned
    inside ``day`` so the scan's ``mtime >= window.start`` prune is deterministic.
    Returns the account root to feed to a patched ``discover_harness_roots``."""
    root = tmp_path / ".claude-acct"
    slug = _claude_project_dir(project_root)
    sdir = root / "projects" / slug
    sdir.mkdir(parents=True, exist_ok=True)
    tx = sdir / "sess.jsonl"
    _write_claude_transcript(tx, day, texts)
    stamp = datetime.fromisoformat(f"{day}T10:00:00+00:00").timestamp()
    os.utime(tx, (stamp, stamp))
    return root


def test_scan_messages_windows_across_roots(tmp_path, monkeypatch):
    """scan_messages reads live transcripts from the harness roots (all AI
    accounts), bucketing each turn by its OWN timestamp — not a stale index,
    never the session's started_at."""
    project_root = tmp_path / "proj"
    project_root.mkdir()
    root = _make_claude_root(tmp_path, project_root, "2026-07-04", ["hi day4", "reply day4"])
    # Isolate from the real machine: one fake account root, treated as Claude.
    monkeypatch.setattr(A, "discover_harness_roots", lambda: [root])
    monkeypatch.setattr(A, "_root_supports_claude", lambda r: True)
    monkeypatch.setattr(A, "_root_supports_codex", lambda r: False)

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    out = scan_messages([("proj", project_root)], [w4])
    msgs = out["proj"]["2026-07-04"]
    assert {m.text for m in msgs} == {"hi day4", "reply day4"}
    assert all(m.ts.date().isoformat() == "2026-07-04" for m in msgs)
    assert all(m.project == "proj" for m in msgs)

    # A window with no in-range turns yields an empty bucket (07-04 turns excluded).
    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    out5 = scan_messages([("proj", project_root)], [w5])
    assert out5["proj"]["2026-07-05"] == []


def test_finding_dated_by_first_seen_at(tmp_path):
    """A finding is dated by its ``first_seen_at`` (the source turn's timestamp),
    and a finding whose ``first_seen_at`` fell back to the session's
    ``started_at`` is dropped — never windowed by the long-running start."""
    # Session started at 07-04 00:00 (inside the day-4 window), so the fallback
    # case below lands IN the window and must still be excluded by the guard.
    session_node = ResearchNode(
        id="Session:s1",
        name="s1",
        type=ResearchNodeType.SESSION,
        metadata={"session_id": "s1", "started_at": "2026-07-04T00:00:00Z"},
    )
    # first_seen_at is a real source-turn timestamp, != started_at -> kept.
    node_valid = ResearchNode(
        id="SessionInsight:s1:zz",
        name="insight text",
        type=ResearchNodeType.SESSION_INSIGHT,
        metadata={"session_id": "s1", "first_seen_at": "2026-07-04T10:00:00Z"},
    )
    # first_seen_at == the session's started_at -> fallback case -> skipped,
    # even though it lands inside the window.
    node_fallback = ResearchNode(
        id="SessionDecision:s1:yy",
        name="fallback decision",
        type=ResearchNodeType.SESSION_DECISION,
        metadata={"session_id": "s1", "first_seen_at": "2026-07-04T00:00:00Z"},
    )
    # No first_seen_at -> undated -> skipped (never dated from started_at).
    node_undated = ResearchNode(
        id="SessionTODO:s1:uu",
        name="undated todo",
        type=ResearchNodeType.SESSION_TODO,
        metadata={"session_id": "s1"},
    )
    # A non-finding node is ignored by the type filter.
    node_event = ResearchNode(
        id="Event:s1:ev",
        name="a session event",
        type=ResearchNodeType.EVENT,
        metadata={"session_id": "s1", "first_seen_at": "2026-07-04T10:00:00Z"},
    )
    graph = ResearchGraph(
        nodes=[session_node, node_valid, node_fallback, node_undated, node_event]
    )

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    got = gather_findings("proj", graph, w4)
    assert [f.body for f in got] == ["insight text"]
    assert [f.kind for f in got] == ["SessionInsight"]
    assert got[0].node_id == "SessionInsight:s1:zz"
    assert got[0].session_id == "s1"
    assert got[0].project == "proj"
    assert got[0].ts.date().isoformat() == "2026-07-04"

    # The day-5 window excludes the finding first seen on 07-04.
    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    assert gather_findings("proj", graph, w5) == []


def _git(root: Path, *args, env=None) -> None:
    """Run a git subcommand against ``root``, failing loudly on non-zero exit."""
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env=env,
    )


def test_gather_git_windows_commits(tmp_path):
    """gather_git returns only commits whose author date falls in the window."""
    root = tmp_path
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "T")
    # A fixed author/committer date so the window edge is deterministic. The
    # naive stamp is read in the machine's local zone; the gatherer compares the
    # tz-aware %aI it emits against the tz-aware window, so the instant is exact.
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-07-04T10:00:00",
        "GIT_COMMITTER_DATE": "2026-07-04T10:00:00",
    }
    (root / "f").write_text("x")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "day4 commit", env=env)

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    commits = gather_git("proj", str(root), w4)
    assert [c.subject for c in commits] == ["day4 commit"]
    assert commits[0].author == "T"
    assert commits[0].project == "proj"
    assert commits[0].ts.date().isoformat() == "2026-07-04"

    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    assert gather_git("proj", str(root), w5) == []


def test_gather_git_skips_non_repo(tmp_path):
    """A directory that is not a git repo drops the commits section, no raise."""
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    assert gather_git("proj", str(tmp_path), w) == []


def test_gather_prs_skips_without_origin(tmp_path):
    """No git repo / no origin remote / no gh -> empty PR list, never raises."""
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    assert gather_prs("proj", str(tmp_path), w) == []


def _stamp_mtime(path: Path, when: datetime) -> None:
    """Pin a file's mtime to a fixed instant so the window edge is deterministic."""
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def test_gather_docs_by_own_timestamp(tmp_path):
    """A SourceDocument with no metadata timestamp is dated by its file mtime."""
    src = tmp_path / "doc.md"
    src.write_text("hello")
    _stamp_mtime(src, datetime(2026, 7, 4, 10, tzinfo=timezone.utc))
    # A real ResearchNode: ingested docs carry their path in the first-class
    # ``source_path`` attribute (not metadata) and have no per-node ingest time.
    node = ResearchNode(
        id="SourceDocument:doc:aa",
        name="Doc",
        type=ResearchNodeType.SOURCE_DOCUMENT,
        source_path=str(src),
    )
    graph = ResearchGraph(nodes=[node])

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    got = gather_docs("proj", str(tmp_path), graph, w4)
    assert [d.title for d in got] == ["Doc"]
    assert got[0].source_path == str(src)
    assert got[0].project == "proj"
    assert got[0].ts.date().isoformat() == "2026-07-04"

    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    assert gather_docs("proj", str(tmp_path), graph, w5) == []


def test_gather_docs_metadata_timestamp_wins_over_mtime(tmp_path):
    """An explicit metadata ``updated_at`` is preferred over the file mtime."""
    src = tmp_path / "doc.md"
    src.write_text("hello")
    # mtime is on 07-05 but the doc's own updated_at is 07-04 -> lands on 07-04.
    _stamp_mtime(src, datetime(2026, 7, 5, 10, tzinfo=timezone.utc))
    node = ResearchNode(
        id="SourceDocument:doc:bb",
        name="Meta Doc",
        type=ResearchNodeType.SOURCE_DOCUMENT,
        source_path=str(src),
        metadata={"updated_at": "2026-07-04T09:00:00Z"},
    )
    graph = ResearchGraph(nodes=[node])

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    assert [d.title for d in gather_docs("proj", str(tmp_path), graph, w4)] == ["Meta Doc"]
    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    assert gather_docs("proj", str(tmp_path), graph, w5) == []


def test_gather_docs_relative_source_path_resolved_against_root(tmp_path):
    """RAG-ingested docs store a project-relative ``source_path``; the mtime
    fallback must resolve it against ``root`` (that is what ``root`` is for)."""
    src = tmp_path / "notes" / "d.md"
    src.parent.mkdir()
    src.write_text("hi")
    _stamp_mtime(src, datetime(2026, 7, 4, 12, tzinfo=timezone.utc))
    node = ResearchNode(
        id="SourceDocument:rel:cc",
        name="Rel Doc",
        type=ResearchNodeType.SOURCE_DOCUMENT,
        source_path="notes/d.md",  # relative, exactly as the RAG manifest records it
    )
    graph = ResearchGraph(nodes=[node])

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    got = gather_docs("proj", str(tmp_path), graph, w4)
    assert [d.title for d in got] == ["Rel Doc"]
    assert got[0].source_path == "notes/d.md"  # the as-stored (relative) path is preserved


def test_gather_docs_best_effort_skips_unresolvable(tmp_path):
    """A non-doc node is ignored; a doc with no readable source and no metadata
    timestamp is skipped rather than crashing (best-effort)."""
    non_doc = ResearchNode(
        id="Paper:x:dd",
        name="A Paper",
        type=ResearchNodeType.PAPER,
        source_path=str(tmp_path / "whatever.md"),
    )
    no_source = ResearchNode(
        id="SourceDocument:none:ee",
        name="No Source",
        type=ResearchNodeType.SOURCE_DOCUMENT,
        source_path=None,
    )
    missing_file = ResearchNode(
        id="SourceDocument:gone:ff",
        name="Gone",
        type=ResearchNodeType.SOURCE_DOCUMENT,
        source_path=str(tmp_path / "does-not-exist.md"),
    )
    graph = ResearchGraph(nodes=[non_doc, no_source, missing_file])

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    assert gather_docs("proj", str(tmp_path), graph, w4) == []

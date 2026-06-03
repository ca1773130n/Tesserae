"""SessionTailer unit tests — dual-format, partial-line, offset-resume, ordering.

Deterministic by construction: NO pytest-asyncio, NO real sleeps, NO real
file-watching. ``tick()`` is driven directly; enqueues are captured by a stub
``on_new_turns`` that appends ``(path, turns)`` to a list. Harness roots are
pointed at ``tmp_path`` via the ``watch_roots`` seam so nothing real is touched.
A real :class:`HarnessSessionsDB` lives at ``tmp_path/.tesserae/...`` (Plan 01).

Parametrized over BOTH transcript formats (Phase-3 success criterion 3). The
fake-JSONL shapes mirror ``tests/test_harness_session_discovery.py`` so the
reused ``_claude_turns``/``_codex_turns`` parsers see exactly what they expect.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.harness_sessions import _claude_project_dir
from tesserae.harness_sessions_db import HarnessSessionsDB
from tesserae.engine.session_tail import SessionTailer


# --------------------------------------------------------------------------- #
# Fixture builders                                                            #
# --------------------------------------------------------------------------- #


def _claude_root(tmp_path: Path) -> Path:
    return tmp_path / ".claude-acct"


def _codex_root(tmp_path: Path) -> Path:
    return tmp_path / ".codex-acct"


def _claude_transcript_path(tmp_path: Path, project: Path) -> Path:
    slug = _claude_project_dir(project)
    d = _claude_root(tmp_path) / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    # Marker so _root_supports_claude / enumeration recognizes the root.
    return d / "session-abc.jsonl"


def _codex_transcript_path(tmp_path: Path) -> Path:
    d = _codex_root(tmp_path) / "sessions" / "2026" / "05" / "05"
    d.mkdir(parents=True, exist_ok=True)
    return d / "rollout-2026-05-05T11-00-00-abc.jsonl"


def _claude_line(project: Path, role: str, text: str, ts: str) -> str:
    if role == "user":
        msg = {"role": "user", "content": text}
    else:
        msg = {"role": "assistant", "content": [{"type": "text", "text": text}]}
    return json.dumps({
        "type": role,
        "timestamp": ts,
        "cwd": str(project),
        "sessionId": "abc",
        "gitBranch": "main",
        "message": msg,
    })


def _codex_meta_line(project: Path) -> str:
    return json.dumps({
        "timestamp": "2026-05-05T11:00:00Z",
        "type": "session_meta",
        "payload": {"id": "codex-abc", "cwd": str(project), "model_provider": "openai"},
    })


def _codex_line(role: str, text: str, ts: str) -> str:
    key = "input_text" if role == "user" else "output_text"
    return json.dumps({
        "timestamp": ts,
        "type": "response_item",
        "payload": {"type": "message", "role": role, "content": [{"type": key, "text": text}]},
    })


def _setup(tmp_path: Path, fmt: str):
    """Return (project, transcript_path, watch_roots, header_lines, mk_turn)."""
    project = tmp_path / "demo-project"
    project.mkdir(exist_ok=True)
    if fmt == "claude":
        path = _claude_transcript_path(tmp_path, project)
        header: list[str] = []
        def mk(role: str, text: str, ts: str) -> str:
            return _claude_line(project, role, text, ts)
    else:
        path = _codex_transcript_path(tmp_path)
        header = [_codex_meta_line(project)]
        def mk(role: str, text: str, ts: str) -> str:
            return _codex_line(role, text, ts)
    roots = [_claude_root(tmp_path), _codex_root(tmp_path)]
    return project, path, roots, header, mk


def _make_tailer(project, roots, tmp_path, sink):
    db = HarnessSessionsDB(tmp_path / ".tesserae" / "harness_sessions.db")
    tailer = SessionTailer(
        project_root=project,
        sessions_db=db,
        on_new_turns=lambda p, t: sink.append((p, t)),
        watch_roots=roots,
        poll_interval=0.0,
    )
    return tailer, db


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def _text_turns(turns: list[dict]) -> list[str]:
    return [t["text"] for t in turns if t.get("role") in {"user", "assistant"}]


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fmt", ["claude", "codex"])
def test_tick_yields_turns(tmp_path, fmt):
    project, path, roots, header, mk = _setup(tmp_path, fmt)
    _write(path, header + [
        mk("user", "Add project memory pages", "2026-05-05T10:00:00Z"),
        mk("assistant", "Implemented it.", "2026-05-05T10:01:00Z"),
    ])
    sink: list = []
    tailer, db = _make_tailer(project, roots, tmp_path, sink)

    tailer.tick()

    assert len(sink) == 1
    _, turns = sink[0]
    texts = _text_turns(turns)
    assert "Add project memory pages" in texts
    assert "Implemented it." in texts
    stored = db.list_for_project(project)
    assert len(stored) == 1
    assert stored[0].metadata["turns"]


@pytest.mark.parametrize("fmt", ["claude", "codex"])
def test_partial_line_safety(tmp_path, fmt):
    project, path, roots, header, mk = _setup(tmp_path, fmt)
    complete = mk("user", "first complete turn", "2026-05-05T10:00:00Z")
    partial = mk("assistant", "second half written", "2026-05-05T10:01:00Z")
    # One complete (newline-terminated) line + a half-written line (NO newline).
    path.write_text("\n".join(header + [complete]) + "\n" + partial, encoding="utf-8")
    sink: list = []
    tailer, db = _make_tailer(project, roots, tmp_path, sink)

    tailer.tick()
    offset_after_partial = db.get_offset(path)

    # Only the complete line's turn is yielded; offset stopped before the partial.
    assert len(sink) == 1
    assert "first complete turn" in _text_turns(sink[0][1])
    assert "second half written" not in _text_turns(sink[0][1])
    # Offset is exactly the byte length of the complete prefix.
    prefix = "\n".join(header + [complete]) + "\n"
    assert offset_after_partial == len(prefix.encode("utf-8"))

    # Now the rest of the line lands with its newline.
    _append(path, "\n")
    tailer.tick()
    assert len(sink) == 2
    assert "second half written" in _text_turns(sink[1][1])


@pytest.mark.parametrize("fmt", ["claude", "codex"])
def test_offset_resume_no_replay(tmp_path, fmt):
    project, path, roots, header, mk = _setup(tmp_path, fmt)
    _write(path, header + [mk("user", "original turn", "2026-05-05T10:00:00Z")])
    sink_a: list = []
    tailer_a, db = _make_tailer(project, roots, tmp_path, sink_a)
    tailer_a.tick()
    assert "original turn" in _text_turns(sink_a[0][1])

    # Fresh tailer on the SAME db seeds offsets from all_offsets() (Pitfall 3).
    sink_b: list = []
    tailer_b = SessionTailer(
        project_root=project,
        sessions_db=db,
        on_new_turns=lambda p, t: sink_b.append((p, t)),
        watch_roots=roots,
        poll_interval=0.0,
    )
    _append(path, mk("assistant", "brand new turn", "2026-05-05T10:05:00Z") + "\n")
    tailer_b.tick()

    assert len(sink_b) == 1
    texts = _text_turns(sink_b[0][1])
    assert "brand new turn" in texts
    assert "original turn" not in texts  # no replay


@pytest.mark.parametrize("fmt", ["claude", "codex"])
def test_store_written_before_enqueue(tmp_path, fmt):
    project, path, roots, header, mk = _setup(tmp_path, fmt)
    _write(path, header + [mk("user", "ordering turn", "2026-05-05T10:00:00Z")])

    captured: dict = {}

    db_holder: dict = {}

    def on_new_turns(p, turns):
        # When the callback fires, the upsert MUST have already happened.
        sessions = db_holder["db"].list_for_project(project)
        captured["stored_at_callback"] = len(sessions) == 1 and bool(
            sessions[0].metadata.get("turns")
        )

    db = HarnessSessionsDB(tmp_path / ".tesserae" / "harness_sessions.db")
    db_holder["db"] = db
    tailer = SessionTailer(
        project_root=project,
        sessions_db=db,
        on_new_turns=on_new_turns,
        watch_roots=roots,
        poll_interval=0.0,
    )
    tailer.tick()

    assert captured.get("stored_at_callback") is True


def test_scopes_to_project_slug(tmp_path):
    """Claude-only: an unrelated project slug under the same root is ignored."""
    project, path, roots, header, mk = _setup(tmp_path, "claude")
    _write(path, [mk("user", "target turn", "2026-05-05T10:00:00Z")])

    # A second, unrelated project slug under the SAME fake ~/.claude/projects.
    other = tmp_path / "other-project"
    other.mkdir()
    other_dir = _claude_root(tmp_path) / "projects" / _claude_project_dir(other)
    other_dir.mkdir(parents=True)
    (other_dir / "other.jsonl").write_text(
        json.dumps({
            "type": "user", "timestamp": "2026-05-05T09:00:00Z",
            "cwd": str(other), "sessionId": "zzz",
            "message": {"role": "user", "content": "unrelated turn"},
        }) + "\n",
        encoding="utf-8",
    )

    sink: list = []
    tailer, db = _make_tailer(project, roots, tmp_path, sink)
    # The unrelated file must never have been enumerated into scope.
    assert all("other-project" not in str(p) for p in tailer._known)

    tailer.tick()

    all_texts = [t for _, turns in sink for t in _text_turns(turns)]
    assert "target turn" in all_texts
    assert "unrelated turn" not in all_texts
    stored = db.list_for_project(project)
    assert len(stored) == 1

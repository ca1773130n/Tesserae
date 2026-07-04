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

import tesserae.activity_summary as summary
import tesserae.mcp_server as mcp_server
from tesserae.activity_summary import (
    CommitItem,
    FindingItem,
    Window,
    build_summary,
    render_day,
    resolve_windows,
)
from tesserae.harness_sessions import _claude_project_dir


# --------------------------------------------------------------------------- #
# render_day — deterministic single-day markdown
# --------------------------------------------------------------------------- #
def test_render_day_has_all_sections_with_none_placeholders():
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    md = render_day(w, {}, {})
    assert md.startswith("### 2026-07-04")
    for heading in (
        "#### Sessions",
        "#### Files touched",
        "#### Commits",
        "#### Pull Requests",
        "#### Ingested docs",
    ):
        assert heading in md
    # Decisions & Insights is NOT a raw-fact section (it's LLM-derived, in the
    # narrative); the five fact subsections each render the placeholder when empty.
    assert "Decisions & Insights" not in md
    assert md.count("_none_") == 5


def test_render_day_sorts_and_is_reproducible():
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    t1 = datetime(2026, 7, 4, 9, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 4, 11, tzinfo=timezone.utc)
    # Pass commits out of order; the renderer must sort by (ts, sha).
    commits = [
        CommitItem(ts=t2, sha="bbbb2222", author="T", subject="later", project="p"),
        CommitItem(ts=t1, sha="aaaa1111", author="T", subject="earlier", project="p"),
    ]
    md = render_day(w, {"commits": commits}, {})
    # 'earlier' (09:00) must precede 'later' (11:00) regardless of input order.
    assert md.index("earlier") < md.index("later")
    # Same inputs -> byte-identical render.
    assert render_day(w, {"commits": list(reversed(commits))}, {}) == md


def test_render_session_excerpts_groups_bounds_and_compresses():
    from tesserae.activity_summary import MessageItem, render_session_excerpts

    def _t(h):
        return datetime(2026, 7, 4, h, tzinfo=timezone.utc)

    msgs = [
        MessageItem(ts=_t(11), role="assistant", name=None, text="done, tests pass",
                    project="p", session_id="s1", harness="claude-code"),
        MessageItem(ts=_t(9), role="user", name=None, text="please refactor the parser",
                    project="p", session_id="s1", harness="claude-code"),
        MessageItem(ts=_t(10), role="tool", name="Edit", text="{huge tool payload}",
                    project="p", session_id="s1", harness="claude-code"),
    ]
    out = render_session_excerpts(msgs)
    assert "session s1 (claude-code, 3 turns)" in out
    # user/assistant text is kept, ordered by time (user before assistant).
    assert out.index("please refactor the parser") < out.index("done, tests pass")
    # tool turns collapse to just the tool name — no payload noise.
    assert "[tool:Edit]" in out
    assert "huge tool payload" not in out


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


def _build_project(tmp_path: Path, monkeypatch) -> None:
    # Two transcripts under a fake Claude *account* root, matched to the project
    # by its encoded slug dir — scan_messages reads the live harness roots, not
    # the index. One has a turn on 07-04, the other on 07-05.
    acct = tmp_path / ".claude-acct"
    slug = _claude_project_dir(tmp_path)
    sdir = acct / "projects" / slug
    sdir.mkdir(parents=True, exist_ok=True)
    tx4 = sdir / "sess4.jsonl"
    tx5 = sdir / "sess5.jsonl"
    _write_claude_transcript(tx4, "2026-07-04", ["hi day4", "reply day4"])
    _write_claude_transcript(tx5, "2026-07-05", ["hi day5", "reply day5"])
    _stamp_mtime(tx4, datetime(2026, 7, 4, 10, tzinfo=timezone.utc))
    _stamp_mtime(tx5, datetime(2026, 7, 5, 10, tzinfo=timezone.utc))
    # Isolate the scan from the real machine: one fake account root, Claude only.
    monkeypatch.setattr(summary, "discover_harness_roots", lambda: [acct])
    monkeypatch.setattr(summary, "_root_supports_claude", lambda r: True)
    monkeypatch.setattr(summary, "_root_supports_codex", lambda r: False)
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
    _build_project(tmp_path, monkeypatch)
    _register_single_project(monkeypatch, "proj", tmp_path)

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    res = build_summary([w4], ["proj"], synthesize=False, write=False)

    assert "day4 commit" in res.markdown
    assert "day5 commit" not in res.markdown  # day-5 activity excluded
    assert "a.txt" in res.markdown  # files-touched aggregate from the day-4 commit
    assert "b.txt" not in res.markdown
    # Sessions render as an in-window count, never a hash dump: the day-4 window
    # sees exactly the one 2-turn transcript; the day-5 transcript contributes 0.
    assert "1 sessions · 2 in-window turns" in res.markdown
    assert "sess5" not in res.markdown
    assert res.paths == []  # write=False

    # Determinism: identical inputs -> byte-identical output.
    res2 = build_summary([w4], ["proj"], synthesize=False, write=False)
    assert res.markdown == res2.markdown


def test_build_summary_writes_per_project_file(tmp_path, monkeypatch):
    _build_project(tmp_path, monkeypatch)
    _register_single_project(monkeypatch, "proj", tmp_path)

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    res = build_summary([w4], ["proj"], synthesize=False, write=True)

    assert len(res.paths) == 1
    written = res.paths[0]
    assert written.name == "daily-2026-07-04.md"
    assert written.parent == tmp_path / ".tesserae" / "summaries" / "proj"
    assert "day4 commit" in written.read_text(encoding="utf-8")


def test_build_summary_default_scope_is_all_registered(tmp_path, monkeypatch):
    _build_project(tmp_path, monkeypatch)
    _register_single_project(monkeypatch, "proj", tmp_path)

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    # project_names=None -> every registered project (here just "proj").
    res = build_summary([w4], None, synthesize=False, write=False)
    assert res.markdown.startswith("# Activity summary — 2026-07-04")
    assert "## proj" in res.markdown  # project heading under Windowed facts
    assert "# Windowed facts" in res.markdown
    assert "day4 commit" in res.markdown


# --------------------------------------------------------------------------- #
# synthesize_narrative — LLM prose prepended to the deterministic digest
# --------------------------------------------------------------------------- #
class _FakeClient:
    """Stand-in for the rotating CLI/SDK client's ``complete_text`` surface."""

    def __init__(self, prose: str = "On July 4, one commit landed.") -> None:
        self.prose = prose
        self.seen_user = None
        self.seen_system = None

    def complete_text(self, system: str, user: str) -> str:
        self.seen_system = system
        self.seen_user = user
        return self.prose


def test_synthesize_narrative_uses_client():
    from tesserae.activity_summary import synthesize_narrative

    client = _FakeClient()
    md = "## agented\n#### Commits\n- day4 commit\n"
    out = synthesize_narrative(md, client)
    # The deterministic facts are the model's context.
    assert "day4 commit" in client.seen_user
    # synthesize_narrative returns the prose ONLY — the caller assembles the doc.
    assert out == "On July 4, one commit landed."


def test_synthesize_narrative_empty_prose_returns_empty():
    from tesserae.activity_summary import synthesize_narrative

    md = "## agented\n#### Commits\n- day4 commit\n"
    # An empty/whitespace model reply yields "" (the caller then renders facts-only).
    assert synthesize_narrative(md, _FakeClient(prose="   ")) == ""


def test_build_summary_synthesize_prepends_narrative(tmp_path, monkeypatch):
    import tesserae.activity_summary as summary

    _build_project(tmp_path, monkeypatch)
    _register_single_project(monkeypatch, "proj", tmp_path)
    client = _FakeClient(prose="A concise story of the day.")
    monkeypatch.setattr(summary, "_summary_llm_client", lambda root: client, raising=False)

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    res = build_summary([w4], ["proj"], synthesize=True, write=False)

    # Narrative sits above the Windowed-facts section.
    assert "A concise story of the day." in res.markdown
    assert res.markdown.index("A concise story of the day.") < res.markdown.index("# Windowed facts")
    assert "day4 commit" in res.markdown
    # The digest — not some fabrication — was the model's context.
    assert "day4 commit" in client.seen_user
    # The narrator is given the actual session conversation, not just counts,
    # so it can summarize sessions with no commits ("session activity only" bug).
    assert "hi day4" in client.seen_user
    # But the raw excerpts never leak into the rendered output.
    assert "hi day4" not in res.markdown


def test_build_summary_narrative_falls_back_on_client_failure(tmp_path, monkeypatch):
    import tesserae.activity_summary as summary

    _build_project(tmp_path, monkeypatch)
    _register_single_project(monkeypatch, "proj", tmp_path)

    def _boom(root):
        raise RuntimeError("no LLM backend available")

    monkeypatch.setattr(summary, "_summary_llm_client", _boom, raising=False)

    (w4,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    # synthesize=True but the client blows up -> deterministic digest, no raise.
    res = build_summary([w4], ["proj"], synthesize=True, write=False)
    res_plain = build_summary([w4], ["proj"], synthesize=False, write=False)
    assert res.markdown == res_plain.markdown
    assert res.markdown.startswith("# Activity summary — 2026-07-04")

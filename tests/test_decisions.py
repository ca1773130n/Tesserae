"""Tests for the decisions module: shared transcript iteration, deterministic
AskUserQuestion human-decision parsing, LLM agent-decision extraction, and the
gather + render orchestration."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import tesserae.activity_summary as A
import tesserae.decisions as D
from tesserae.activity_summary import iter_project_transcripts, resolve_windows
from tesserae.decisions import (
    Decision,
    extract_agent_decisions,
    gather_decisions,
    parse_human_decisions,
    render_decisions,
)
from tesserae.harness_sessions import _claude_project_dir


def _auq_rows(day):
    """A minimal Claude transcript: an AskUserQuestion tool_use + its answer."""
    tuid = "toolu_x"
    return [
        {
            "type": "assistant",
            "timestamp": f"{day}T10:00:00Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": tuid,
                        "name": "AskUserQuestion",
                        "input": {
                            "questions": [
                                {
                                    "question": "Which backend?",
                                    "header": "Backend",
                                    "options": [{"label": "SQLite"}, {"label": "Postgres"}],
                                }
                            ]
                        },
                    }
                ]
            },
        },
        {
            "type": "user",
            "timestamp": f"{day}T10:01:00Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tuid,
                        "content": 'Your questions have been answered: "Which backend?"="Postgres". Continue.',
                    }
                ]
            },
        },
    ]


def _claude_root(tmp_path, project_root, day, rows):
    root = tmp_path / ".claude-acct"
    slug = _claude_project_dir(project_root)
    d = root / "projects" / slug
    d.mkdir(parents=True, exist_ok=True)
    tx = d / "sess.jsonl"
    tx.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    stamp = datetime.fromisoformat(f"{day}T10:00:00+00:00").timestamp()
    os.utime(tx, (stamp, stamp))
    return root, tx


def _isolate(monkeypatch, root):
    monkeypatch.setattr(A, "discover_harness_roots", lambda: [root])
    monkeypatch.setattr(A, "_root_supports_claude", lambda r: True)
    monkeypatch.setattr(A, "_root_supports_codex", lambda r: False)


# --- Task 1: shared iterator ------------------------------------------------
def test_iter_project_transcripts_yields_matched_in_window(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    root, _tx = _claude_root(tmp_path, proj, "2026-07-04", [{}])
    _isolate(monkeypatch, root)
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    got = list(iter_project_transcripts([("proj", proj)], [w]))
    assert len(got) == 1
    name, harness, path, key = got[0]
    assert name == "proj" and harness == "claude-code"
    assert Path(path).name == "sess.jsonl"
    assert key == ".claude-acct:sess"


# --- Task 2: deterministic human decisions ----------------------------------
def test_parse_human_decision():
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    got = parse_human_decisions(_auq_rows("2026-07-04"), "proj", "acct:sess", w)
    assert len(got) == 1
    d = got[0]
    assert d.source == "human" and d.question == "Which backend?"
    assert d.answer == "Postgres" and d.options == ["SQLite", "Postgres"]
    assert d.header == "Backend" and d.project == "proj"
    # Out-of-window is excluded (the answer turn is on 07-04).
    (w5,) = resolve_windows(day="2026-07-05", tz=timezone.utc)
    assert parse_human_decisions(_auq_rows("2026-07-04"), "proj", "acct:sess", w5) == []


# --- Task 3: agent decisions (LLM) ------------------------------------------
class _FakeClient:
    def __init__(self, out):
        self.out = out
        self.seen = None

    def complete_text(self, system, user):
        self.seen = user
        return self.out


def test_extract_agent_decisions_parses_lines():
    c = _FakeClient("Use SQLite by default :: lighter, no server\nPin origin to the ext id :: security")
    ts = datetime(2026, 7, 4, tzinfo=timezone.utc)
    got = extract_agent_decisions("<excerpts>", c, "proj", ts)
    assert [d.question for d in got] == ["Use SQLite by default", "Pin origin to the ext id"]
    assert got[0].answer == "lighter, no server"
    assert all(d.source == "agent" and d.project == "proj" for d in got)
    assert "<excerpts>" in c.seen


# --- Task 4: gather + render (deterministic path) ---------------------------
def test_gather_decisions_deterministic(tmp_path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    root, _tx = _claude_root(tmp_path, proj, "2026-07-04", _auq_rows("2026-07-04"))
    _isolate(monkeypatch, root)
    monkeypatch.setattr(D, "_resolve_projects", lambda names: [("proj", proj)])

    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    got = gather_decisions([w], ["proj"], include_agent=False)
    assert len(got) == 1 and got[0].source == "human"
    assert got[0].question == "Which backend?" and got[0].answer == "Postgres"

    md = render_decisions(got)
    assert "## proj" in md
    assert "Which backend?" in md and "Postgres" in md
    assert "### Human decisions" in md and "### Agent decisions" in md


def test_render_decisions_groups_empty_sections():
    md = render_decisions([])
    assert md.strip() == ""  # no projects -> empty
    only_agent = [Decision(ts=datetime(2026, 7, 4, tzinfo=timezone.utc), source="agent",
                           project="p", session_id="", question="Chose X", answer="because Y")]
    md2 = render_decisions(only_agent)
    assert "### Human decisions\n_none_" in md2
    assert "Chose X — because Y" in md2

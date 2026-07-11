"""Tests for live session views (serve) + the --max-turns cap."""
import json
import os
from datetime import datetime, timezone

import tesserae.activity_summary as A
from tesserae.activity_summary import resolve_windows, scan_messages
from tesserae.harness_sessions import _claude_project_dir
from tesserae.live_sessions import live_session_list, live_transcript_search


def _seed(tmp_path, monkeypatch, texts, day="2026-07-04"):
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    root = tmp_path / ".claude-acct"
    d = root / "projects" / _claude_project_dir(proj)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "type": "user" if i % 2 == 0 else "assistant",
            "timestamp": f"{day}T10:0{i}:00Z",
            "message": {"role": "user" if i % 2 == 0 else "assistant",
                        "content": [{"type": "text", "text": t}]},
        }
        for i, t in enumerate(texts)
    ]
    tx = d / "s.jsonl"
    tx.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    stamp = datetime(2026, 7, 4, 10, tzinfo=timezone.utc).timestamp()
    os.utime(tx, (stamp, stamp))
    monkeypatch.setattr(A, "discover_harness_roots", lambda: [root])
    monkeypatch.setattr(A, "_root_supports_claude", lambda r: True)
    monkeypatch.setattr(A, "_root_supports_codex", lambda r: False)
    return proj


def test_scan_messages_respects_turn_limit(tmp_path, monkeypatch):
    proj = _seed(tmp_path, monkeypatch, [f"t{i}" for i in range(5)])
    (w,) = resolve_windows(day="2026-07-04", tz=timezone.utc)
    out = scan_messages([("proj", proj)], [w], turn_limit=2)
    assert len(out["proj"]["2026-07-04"]) == 2  # capped before windowing


def test_live_session_list_and_search(tmp_path, monkeypatch):
    proj = _seed(tmp_path, monkeypatch, ["design the parser", "ok done", "ship it"])
    # A huge window so 'recent' catches the fixture regardless of wall clock.
    sessions = live_session_list([("proj", proj)], days=100_000)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["project"] == "proj" and s["turns"] == 3
    assert s["account"] == ".claude-acct" and "parser" in s["preview"]
    assert s["session_id"] == ".claude-acct:s"

    hits = live_transcript_search("parser", [("proj", proj)], days=100_000)
    assert len(hits) == 1 and "parser" in hits[0]["text"]
    assert hits[0]["project"] == "proj" and hits[0]["role"] == "user"
    assert live_transcript_search("nonexistent", [("proj", proj)], days=100_000) == []
    assert live_transcript_search("", [("proj", proj)], days=100_000) == []


def test_run_sessions_and_search_merge(tmp_path, monkeypatch):
    import tesserae.serve as S

    proj = _seed(tmp_path, monkeypatch, ["design the parser", "reply"])
    monkeypatch.setattr(
        "tesserae.memex_search.search_transcripts",
        lambda *a, **k: {"available": True, "results": [{"text": "old indexed hit"}], "total": 1},
    )
    sent = {}

    class H:  # minimal fake handler
        project_root = proj

        def _send_json(self, status, body):
            sent["status"] = status
            sent["body"] = body

    S._run_sessions(H(), "days=100000", project_root=proj, project_name="proj")
    assert sent["status"] == 200
    assert sent["body"]["sessions"][0]["project"] == "proj"

    # days=100000 like every other call here: the fixture turns are dated
    # 2026-07-04, and the default live window would silently age them out
    # (this exact test went red the evening that boundary crossed).
    S._run_transcript_search(H(), "q=parser&days=100000", project_root=proj, project_name="proj")
    results = sent["body"]["results"]
    assert any("parser" in (r.get("text") or "") for r in results)  # live hit present
    assert any(r.get("text") == "old indexed hit" for r in results)  # index hit merged
    assert sent["body"]["live"] == 1

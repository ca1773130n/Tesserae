"""Tesserae must not ingest its OWN compile-time LLM calls as user sessions.

The harness session monitor records every Claude Code / Codex CLI invocation —
including the codex/claude calls Tesserae itself runs during compile (extraction,
synthesis, community summaries, …). Ingesting those is a self-capture feedback
loop: the session DB fills with Tesserae's prompts, drowning real work, and the
next compile "extracts findings" from its own extraction calls.
"""

from __future__ import annotations

from pathlib import Path

from tesserae.harness_sessions import HarnessSession, is_tesserae_internal_session
from tesserae.harness_sessions_db import HarnessSessionsDB


def _session(sid: str, title: str, project_root: str = "/tmp/proj") -> HarnessSession:
    return HarnessSession(
        id=sid, slug=sid, harness="claude-code", agent_label="Claude Code",
        project_name="proj", project_root=project_root,
        started_at="2026-06-14T10:00:00Z", title=title,
    )


# A real coding session never opens with one of Tesserae's verbatim system prompts.
_REAL = [
    "Fix the off-by-one in the pagination helper",
    "Review this change for security vulnerabilities.",  # ambiguous → kept (conservative)
    "why is the GPU at 100%",
]
_INTERNAL = [
    "You are an extractor that reads agent/user conversation transcripts and produces JSON",
    "You are summarizing a community of related typed research-graph nodes. Return JSON",
    "You are extracting a typed research intelligence graph for Tesserae.",
    "You are an Tesserae synthesis writer. Your job is to summarize",
    "Summarize the following in 2 sentences as a TL;DR. Return only the summary",
]


def test_predicate_flags_internal_keeps_real():
    for t in _INTERNAL:
        assert is_tesserae_internal_session(_session("i", t)) is True, t
    for t in _REAL:
        assert is_tesserae_internal_session(_session("r", t)) is False, t


def test_predicate_checks_first_turns_too():
    s = HarnessSession(
        id="x", slug="x", harness="claude-code", agent_label="Claude Code",
        project_name="proj", project_root="/tmp/proj",
        started_at="2026-06-14T10:00:00Z", title="(untitled)",
        metadata={"turns": [{"role": "user",
            "text": "You are an extractor that reads agent/user conversation transcripts ..."}]},
    )
    assert is_tesserae_internal_session(s) is True


def test_prune_internal_sessions_removes_only_internal(tmp_path: Path):
    db = HarnessSessionsDB(tmp_path / "sessions.db")
    sessions = (
        [_session(f"int{i}", t) for i, t in enumerate(_INTERNAL)]
        + [_session(f"real{i}", t) for i, t in enumerate(_REAL)]
    )
    for s in sessions:
        db.upsert(s, jsonl_path=f"/tmp/{s.id}.jsonl", last_offset=0)
    assert db.count_sessions() == len(sessions)

    removed = db.prune_internal_sessions()
    assert removed == len(_INTERNAL)
    remaining = db.list_for_project("/tmp/proj")
    assert {s.title for s in remaining} == set(_REAL)
    # idempotent: a second prune removes nothing
    assert db.prune_internal_sessions() == 0

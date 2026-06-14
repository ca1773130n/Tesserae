"""Tesserae must not ingest its OWN compile-time LLM calls as user sessions.

The harness session monitor records every Claude Code / Codex CLI invocation —
including the codex/claude calls Tesserae itself runs during compile (extraction,
synthesis, community summaries, …). Ingesting those is a self-capture feedback
loop: the session DB fills with Tesserae's prompts, drowning real work, and the
next compile "extracts findings" from its own extraction calls.
"""

from __future__ import annotations

import re
from pathlib import Path

from tesserae.harness_sessions import (
    HarnessSession,
    _TESSERAE_PROMPT_SIGNATURES,
    is_tesserae_internal_session,
)
from tesserae.harness_sessions_db import HarnessSessionsDB


def _session(sid: str, title: str, project_root: str = "/tmp/proj") -> HarnessSession:
    return HarnessSession(
        id=sid, slug=sid, harness="claude-code", agent_label="Claude Code",
        project_name="proj", project_root=project_root,
        started_at="2026-06-14T10:00:00Z", title=title,
    )


# A real coding session never OPENS with one of Tesserae's verbatim system prompts.
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
    # JSON-client compile/retrieval prompts added after the codex review:
    "You distill a cluster of related coding/agent session findings into a single note",
    "You write ONE terse extraction-guidance bullet (<= 30 words) from a cluster",
    "You decide whether one research-session finding obsoletes another. Both findings",
    "You arbitrate a contradiction between two research performance claims. One claim",
    "You split a single retrieval question into a short list of focused sub-questions",
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


def test_user_quoting_a_prompt_midmessage_is_kept():
    """Conservative anchoring: a REAL session that quotes/reviews one of Tesserae's
    prompts mid-message must NOT be flagged. The signature only matches at the
    START of a blob (after trimming), so embedded quotes are retained."""
    for body in (
        'Please review this constant:\n_PROMPT_SYSTEM = "You are an extractor that '
        'reads agent/user conversation transcripts and produces JSON"',
        "Why does our distill prompt start with 'You distill a cluster of related "
        "coding/agent session findings'? Is that wording too narrow?",
        "Fix the bug where the title is set to 'You are summarizing a community of "
        "related typed research-graph nodes'.",
    ):
        s = HarnessSession(
            id="real", slug="real", harness="claude-code", agent_label="Claude Code",
            project_name="proj", project_root="/tmp/proj",
            started_at="2026-06-14T10:00:00Z", title="Investigate prompt wording",
            metadata={"turns": [{"role": "user", "text": body}]},
        )
        assert is_tesserae_internal_session(s) is False, body[:60]


def test_every_system_prompt_is_covered():
    """Anti-drift guard: every Tesserae-owned LLM system prompt in the package must
    be covered by a signature, so a newly added prompt cannot silently re-open the
    self-capture loop. Scans package source for prompt openings of the
    ``You are/distill/write/decide/arbitrate/split ...`` family and asserts each
    starts with a known signature. (See the comment on _TESSERAE_PROMPT_SIGNATURES.)
    """
    pkg = Path(__file__).resolve().parent.parent / "tesserae"
    sig_file = pkg / "harness_sessions.py"  # excluded: it DEFINES the signatures
    opening = re.compile(
        r'(?:[fr]{0,2}"""|[fr]{0,2}")'
        r'(You (?:are|distill|write|decide|arbitrate|split)\b[^"\n]{8,})'
    )
    uncovered: list[tuple[str, str]] = []
    for path in pkg.rglob("*.py"):
        if path == sig_file:
            continue
        for m in opening.finditer(path.read_text(encoding="utf-8")):
            text = m.group(1)
            if not text.startswith(_TESSERAE_PROMPT_SIGNATURES):
                uncovered.append((str(path.relative_to(pkg)), text[:80]))
    assert not uncovered, (
        "Tesserae system prompt(s) not covered by _TESSERAE_PROMPT_SIGNATURES "
        "(self-capture loop would re-open): " + "; ".join(f"{p}: {t}" for p, t in uncovered)
    )


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

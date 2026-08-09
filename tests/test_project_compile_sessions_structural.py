"""End-to-end test for the structural session pass wired into `project compile`.

Drives `ProjectWiki.compile()` programmatically against a tmp_path
project that has:

* a small docs/ corpus with one paper-shaped file the path index
  can resolve,
* a pre-populated `.tesserae/harness_sessions/` directory containing
  one normalised `HarnessSession` JSON whose `files_touched` includes
  the paper.

Pre-populating `.tesserae/harness_sessions/` (instead of relying on
the live `discover_harness_sessions` scan) keeps the test
hermetic — it does not depend on the developer's `~/.claude/`
contents.

Phase 5 will layer LLM-extracted Insight/Decision/etc nodes on top;
this Phase 3 test only verifies the structural pass: Session node,
SessionDecision nodes, and discussed_in edges.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tesserae.harness_sessions import HarnessSession, HarnessSessionStore
from tesserae.project import ProjectWiki, SessionExtractionOptions
from tesserae.research_graph import ResearchNodeType


SAMPLE_PAPER = """---
title: Test Paper for Session Linkage
---

# Test Paper for Session Linkage

This paper is the deliberate target of a session's `files_touched`
list. The structural session extractor must emit a `discussed_in`
edge from this Paper to the Session node when both are present.
"""


def _seed_project(project_root: Path) -> ProjectWiki:
    """Create the smallest project layout the compile pipeline will accept."""
    project_root.mkdir(parents=True, exist_ok=True)
    docs_dir = project_root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    paper_path = docs_dir / "test-paper.md"
    paper_path.write_text(SAMPLE_PAPER, encoding="utf-8")
    return ProjectWiki.init(project_root, name="sessions_e2e")


def _seed_session(wiki: ProjectWiki, *, files_touched, decisions=()):
    """Pre-populate `.tesserae/harness_sessions/` with one HarnessSession."""
    session = HarnessSession(
        id="test-session-001",
        slug="test-session",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="sessions_e2e",
        project_root=str(wiki.project_root.resolve()),
        started_at="2026-05-19T10:00:00Z",
        ended_at="2026-05-19T11:00:00Z",
        files_touched=list(files_touched),
        decisions=list(decisions),
    )
    HarnessSessionStore(wiki.paths.harness_sessions).write_sessions([session])


def _graph(wiki: ProjectWiki) -> dict:
    """Load the compiled graph.json after a compile."""
    return json.loads(wiki.paths.graph.read_text(encoding="utf-8"))


def test_compile_with_sessions_disabled_produces_no_session_nodes(tmp_path: Path):
    """``--no-sessions`` (== ``enabled=False``) skips the pass entirely."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    _seed_session(
        wiki,
        files_touched=[str(project_root / "docs/test-paper.md")],
        decisions=["A decision that must not appear when sessions are disabled."],
    )
    wiki.compile(
        session_options=SessionExtractionOptions(enabled=False),
        vault_pull=False,
    )
    graph = _graph(wiki)
    session_types = {
        n["type"] for n in graph["nodes"]
        if n["type"].startswith("Session")
    }
    assert session_types == set(), (
        f"with sessions disabled, no Session* node should exist; got {session_types}"
    )


def test_compile_with_structural_pass_mints_session_and_decisions(tmp_path: Path):
    """Default compile: Session node + structural SessionDecisions +
    discussed_in edge from the resolved Paper."""
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    _seed_session(
        wiki,
        files_touched=[str(project_root / "docs/test-paper.md")],
        decisions=[
            "Use atomic writes for the session findings cache",
            "Keep raw transcripts off-graph",
        ],
    )
    # Pin to llm_enabled="false" so the test asserts the STRUCTURAL pass
    # in isolation regardless of whether the dev machine has the `claude`
    # CLI signed in. Without this pin, the auto-detected LLM backend
    # would run a real subprocess and add LLM-extracted findings to the
    # graph, breaking the count assertion below.
    wiki.compile(
        session_options=SessionExtractionOptions(enabled=True, llm_enabled="false"),
        vault_pull=False,
    )
    graph = _graph(wiki)

    sessions = [n for n in graph["nodes"] if n["type"] == "Session"]
    assert len(sessions) == 1, "expected exactly one Session node"
    assert sessions[0]["metadata"]["session_id"] == "test-session-001"

    decisions = [n for n in graph["nodes"] if n["type"] == "SessionDecision"]
    assert len(decisions) == 2, (
        f"expected 2 SessionDecision nodes (one per `decisions` entry); got {len(decisions)}"
    )
    for d in decisions:
        assert d["metadata"]["session_id"] == "test-session-001"
        assert d["metadata"]["extractor"] == "session-structural"

    # `discussed_in` edge: from the Paper node to the Session.
    discussed_edges = [e for e in graph["edges"] if e["type"] == "discussed_in"]
    assert len(discussed_edges) == 1, (
        f"expected 1 discussed_in edge (Paper → Session); got {len(discussed_edges)}"
    )
    target = discussed_edges[0]["target"]
    assert target == sessions[0]["id"], (
        f"discussed_in edge target should be the Session id; got {target}"
    )

    # `derived_from_session` edges: 2, one per decision.
    derived_edges = [e for e in graph["edges"] if e["type"] == "derived_from_session"]
    assert len(derived_edges) == 2, (
        f"expected 2 derived_from_session edges; got {len(derived_edges)}"
    )


def _seed_session_with_turns(wiki: ProjectWiki, *, files_touched) -> None:
    """A session carrying a transcript, which is what the Event pass reads."""
    session = HarnessSession(
        id="test-session-002",
        slug="test-session-turns",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="sessions_e2e",
        project_root=str(wiki.project_root.resolve()),
        started_at="2026-05-19T10:00:00Z",
        ended_at="2026-05-19T11:00:00Z",
        files_touched=list(files_touched),
        metadata={
            "turns": [
                {"role": "user", "timestamp": "2026-05-19T10:00:01Z",
                 "text": "please fix the failing migration"},
                {"role": "assistant", "timestamp": "2026-05-19T10:00:02Z",
                 "text": "I'll inspect the migration and patch the ordering bug."},
                {"role": "tool", "name": "Edit", "timestamp": "2026-05-19T10:00:03Z",
                 "text": "patched docs/test-paper.md"},
            ]
        },
    )
    HarnessSessionStore(wiki.paths.harness_sessions).write_sessions([session])


def test_compile_mints_session_events_without_switching_on_the_llm_distillation(
    tmp_path: Path,
):
    """The LLM-free Event pass runs by default for a session-bearing project.

    Until roadmap step 4 the pass shared ``distillation_enabled`` with the LLM
    Runbook/Gotcha distillation, so the only way to get these deterministic,
    template-only Event nodes was to also switch on an LLM pass that builds a
    json_client and writes a distillation cache. The two gates are separate:
    this compile must produce Events and must NOT run the LLM pass.
    """
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    _seed_session_with_turns(
        wiki, files_touched=[str(project_root / "docs/test-paper.md")]
    )
    wiki.compile(
        session_options=SessionExtractionOptions(enabled=True, llm_enabled="false"),
        vault_pull=False,
    )
    graph = _graph(wiki)

    events = [n for n in graph["nodes"] if n["type"] == ResearchNodeType.EVENT.value]
    assert events, (
        "a session-bearing project must mint Event nodes from its turns; got none"
    )
    # Every one is stamped by its producer — the discriminator the procedural
    # pools now admit on.
    assert {n["metadata"].get("extractor") for n in events} == {"session-event"}

    # The LLM distillation pass stayed off: no Runbook/Gotcha minted, and no
    # cache directory built for cached LLM verdicts.
    llm_pass_types = {ResearchNodeType.RUNBOOK.value, ResearchNodeType.GOTCHA.value}
    assert not [n for n in graph["nodes"] if n["type"] in llm_pass_types], (
        "enabling the Event pass must not drag the LLM distillation pass in"
    )
    assert not (wiki.paths.root / "distillation_cache").exists()


def test_session_event_pass_can_be_switched_off(tmp_path: Path, monkeypatch):
    """Default-on, not forced-on: the pass has its own opt-out.

    Every future session adds Events monotonically, so an operator who does not
    want that volume in graph.json must be able to decline it without giving up
    session extraction.
    """
    monkeypatch.setenv("TESSERAE_SESSION_EVENT_PASS", "0")
    project_root = tmp_path / "project"
    wiki = _seed_project(project_root)
    _seed_session_with_turns(
        wiki, files_touched=[str(project_root / "docs/test-paper.md")]
    )
    wiki.compile(
        session_options=SessionExtractionOptions(enabled=True, llm_enabled="false"),
        vault_pull=False,
    )
    graph = _graph(wiki)

    assert not [
        n for n in graph["nodes"] if n["type"] == ResearchNodeType.EVENT.value
    ], "TESSERAE_SESSION_EVENT_PASS=0 must skip the pass entirely"

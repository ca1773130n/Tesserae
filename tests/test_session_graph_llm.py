"""Tests for the LLM extraction layer of the session graph extractor.

These tests use a mocked LLMJsonClient (canned responses) so they
require no network or API key.
"""

from __future__ import annotations

from typing import List, Optional, Union

import pytest

from tesserae.harness_sessions import HarnessSession
from tesserae.session_graph_llm import (
    ALLOWED_FINDING_KINDS,
    Finding,
    _chunk_turns,
    _validate_finding,
    extract_with_llm,
)


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


class _ScriptedClient:
    """LLMJsonClient stub that returns scripted responses in order."""

    def __init__(self, scripted: List[Optional[Union[dict, list]]]) -> None:
        self._scripted = list(scripted)
        self.calls: List[dict] = []

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        cache_key=None,
        max_retries: int = 2,
    ):
        self.calls.append(
            {"system": system, "user": user, "schema_name": schema_name}
        )
        if not self._scripted:
            return None
        return self._scripted.pop(0)


def _session(*, id: str = "sess-1") -> HarnessSession:
    return HarnessSession(
        id=id,
        slug=id,
        harness="claude-code",
        agent_label="Claude Code",
        project_name="test",
        project_root="/tmp/test",
        started_at="2026-05-19T10:00:00Z",
    )


def _turns(n: int) -> List[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "text": f"turn-{i}"}
        for i in range(n)
    ]


def _doc_context() -> List[tuple]:
    return [
        ("Paper:foo", "Foo Paper"),
        ("Concept:bar", "Bar Concept"),
    ]


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_well_formed_response_returns_findings():
    client = _ScriptedClient(
        [
            {
                "findings": [
                    {
                        "kind": "insight",
                        "body": "Path index needs basename fallback suppression",
                        "turn_ids": [3, 5],
                        "references": ["Paper:foo"],
                    },
                    {
                        "kind": "decision",
                        "body": "Cache findings by content hash",
                        "turn_ids": [7],
                        "references": ["Paper:foo", "Concept:bar"],
                    },
                ]
            }
        ]
    )
    findings = extract_with_llm(
        _session(), _turns(10), _doc_context(), client,
    )
    assert len(findings) == 2
    assert findings[0].kind == "insight"
    assert findings[0].references == ["Paper:foo"]
    assert findings[1].kind == "decision"
    assert findings[1].references == ["Paper:foo", "Concept:bar"]
    assert len(client.calls) == 1


def test_list_at_top_level_also_supported():
    """If the model returns a bare list rather than {"findings": [...]} it
    still parses — the tolerant orchestrator accepts both shapes."""
    client = _ScriptedClient(
        [
            [
                {"kind": "question", "body": "What about Windows paths?", "turn_ids": [], "references": []},
            ]
        ]
    )
    findings = extract_with_llm(_session(), _turns(3), _doc_context(), client)
    assert len(findings) == 1
    assert findings[0].kind == "question"


def test_empty_transcript_returns_no_findings_and_no_call():
    client = _ScriptedClient([{"findings": []}])
    findings = extract_with_llm(_session(), [], _doc_context(), client)
    assert findings == []
    assert client.calls == []  # we didn't even make a call


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_unknown_kind_is_dropped():
    """A finding with kind="rant" is dropped; siblings survive."""
    client = _ScriptedClient(
        [
            {
                "findings": [
                    {"kind": "rant", "body": "irrelevant"},
                    {"kind": "insight", "body": "Real insight"},
                ]
            }
        ]
    )
    findings = extract_with_llm(_session(), _turns(3), _doc_context(), client)
    assert len(findings) == 1
    assert findings[0].kind == "insight"


def test_empty_body_is_dropped():
    client = _ScriptedClient(
        [
            {
                "findings": [
                    {"kind": "insight", "body": "   "},
                    {"kind": "insight", "body": "Valid body"},
                ]
            }
        ]
    )
    findings = extract_with_llm(_session(), _turns(3), _doc_context(), client)
    assert len(findings) == 1
    assert findings[0].body == "Valid body"


def test_unknown_references_are_dropped_but_finding_survives():
    """Citing a non-existent node id doesn't tank the whole finding —
    it just gets dropped from `references`. Matches the spec's
    "drop unknowns" policy."""
    client = _ScriptedClient(
        [
            {
                "findings": [
                    {
                        "kind": "decision",
                        "body": "Some decision",
                        "turn_ids": [1],
                        "references": ["Paper:foo", "Paper:hallucinated-nonexistent"],
                    },
                ]
            }
        ]
    )
    findings = extract_with_llm(_session(), _turns(3), _doc_context(), client)
    assert len(findings) == 1
    assert findings[0].references == ["Paper:foo"]


def test_client_returns_none_yields_no_findings():
    """When the client gives up (no API key, retries exhausted), we get
    an empty list back — no crash."""
    client = _ScriptedClient([None])
    findings = extract_with_llm(_session(), _turns(3), _doc_context(), client)
    assert findings == []


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def test_chunk_turns_short_session_is_one_chunk():
    chunks = _chunk_turns(_turns(10), max_turns_per_chunk=30)
    assert len(chunks) == 1
    assert len(chunks[0]) == 10


def test_chunk_turns_long_session_is_overlapped_windows():
    turns = _turns(75)
    chunks = _chunk_turns(turns, max_turns_per_chunk=30, overlap=5)
    # Stride = 30 - 5 = 25. So starts at 0, 25, 50. 50 + 30 = 80 covers turn 74.
    assert len(chunks) == 3
    # First chunk: turns 0..29
    assert chunks[0][0]["text"] == "turn-0"
    assert chunks[0][-1]["text"] == "turn-29"
    # Second chunk: turns 25..54
    assert chunks[1][0]["text"] == "turn-25"
    # Third chunk: turns 50..74
    assert chunks[2][0]["text"] == "turn-50"


def test_chunked_session_produces_one_call_per_chunk():
    client = _ScriptedClient(
        [
            {"findings": [{"kind": "insight", "body": "A"}]},
            {"findings": [{"kind": "insight", "body": "B"}]},
            {"findings": [{"kind": "insight", "body": "C"}]},
        ]
    )
    findings = extract_with_llm(
        _session(), _turns(75), _doc_context(), client,
        max_turns_per_chunk=30, overlap=5,
    )
    assert len(client.calls) == 3
    bodies = sorted(f.body for f in findings)
    assert bodies == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# Roadmap step 5 — a failed run is a first-class finding.
#
# The taxonomy had six kinds, none of which can say "this ran and it failed".
# HiSkill's point: a failed anchor is a PRECONDITION for any recovery edge, so
# without a failure kind the whole Causal/Lesson/Procedure layer is built on
# prose keyword votes.
# ---------------------------------------------------------------------------


def test_a_failure_finding_survives_validation():
    finding = _validate_finding(
        {
            "kind": "failure",
            "body": "pytest exited 1: test_prune_scope failed on a stale fixture day",
            "turn_ids": [3],
            "references": [],
        },
        set(),
        session_id="sess-fail",
    )
    assert finding is not None, "an unknown kind is dropped silently at extraction"
    assert finding.kind == "failure"


def test_the_prompt_offers_failure_as_a_kind():
    """An allowed kind the prompt never names is a kind the model never emits."""
    from tesserae.session_graph_llm import _PROMPT_SYSTEM

    assert '"failure"' in _PROMPT_SYSTEM or "- \"failure\"" in _PROMPT_SYSTEM
    assert "failure" in _PROMPT_SYSTEM.split('"kind": "<one of:')[1].split(">")[0]


def test_a_still_unknown_kind_is_still_dropped():
    assert (
        _validate_finding(
            {"kind": "vibes", "body": "b", "turn_ids": [], "references": []},
            set(),
            session_id="s",
        )
        is None
    )


# ---------------------------------------------------------------------------
# The capture and its consumer
# ---------------------------------------------------------------------------


def _outcome_session(result: dict):
    from pathlib import Path

    from tesserae.harness_sessions import HarnessSession

    return HarnessSession(
        id="sess-outcome",
        slug="sess-outcome",
        harness="codex",
        agent_label="Codex",
        project_name="demo",
        project_root=str(Path("/tmp/demo").resolve()),
        started_at="2026-08-09T10:00:00Z",
        metadata={
            "turns": [
                {"role": "user", "timestamp": "", "text": "run the suite"},
                {
                    "role": "tool",
                    "timestamp": "",
                    "name": "shell",
                    "text": '{"command": "pytest"}',
                },
                {"role": "tool_result", "timestamp": "", "name": "shell", **result},
            ]
        },
    )


def test_the_model_can_tell_a_failed_run_from_a_successful_one():
    """``failure`` was added to the prompt as a kind the model may emit, but the
    only signal that a run failed is a TYPED field (``exit_code`` / ``is_error``)
    and the payload the model is shown is ``{role, text}`` exactly. Without the
    outcome in the text, the sole producer of ``SessionFailure`` cannot see the
    thing this branch captured."""
    from tesserae.session_graph import _normalised_turns

    failed = _normalised_turns(_outcome_session({"text": "1 failed", "exit_code": 1}))
    passed = _normalised_turns(_outcome_session({"text": "1 passed", "exit_code": 0}))
    failed_result = failed[2]["text"]
    passed_result = passed[2]["text"]
    assert failed_result != passed_result
    assert "1" in failed_result and "exit" in failed_result.lower()
    assert "0" in passed_result and "exit" in passed_result.lower()


def test_a_result_that_reported_nothing_does_not_read_to_the_model_as_success():
    """Absence of ``is_error`` on a Claude result is not a pass — the key is
    simply omitted for most tools. The rendered marker says so out loud."""
    from tesserae.session_graph import _normalised_turns

    silent = _normalised_turns(_outcome_session({"text": "some output"}))[2]["text"]
    assert "no outcome" in silent
    assert "success" not in silent

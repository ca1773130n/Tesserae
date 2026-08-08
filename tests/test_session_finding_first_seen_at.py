"""Turn-level ``first_seen_at`` on LLM-minted session findings.

``activity_summary.gather_findings`` already documents the contract: a finding
is dated by "the *source turn's own timestamp*, falling back to the session's
``started_at`` only when that turn has no timestamp", and it DROPS any finding
whose ``first_seen_at`` equals its Session's ``started_at``. ``session_event``
honours that. ``SessionGraphExtractor._mint_findings`` did not — it stamped the
session's ``started_at`` on every finding, so every LLM-minted finding hit the
drop rule and vanished from the digest.

These tests pin the turn-level derivation and the two things that keep it
byte-idempotent: the value comes only from transcript bytes, and the chunk
cache key stays a pure function of (role, text) so adding it invalidates no
cached extraction.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence

import pytest

import tesserae.session_graph as sg
from tesserae.activity_summary import Window, gather_findings, parse_ts
from tesserae.harness_sessions import HarnessSession
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.session_graph import SessionGraphExtractor
from tesserae.session_graph_llm import Finding


SESSION_STARTED_AT = "2026-05-19T10:00:00Z"


def _turns(stamps: Sequence[object]) -> List[dict]:
    """Turns whose timestamps are given explicitly (``None`` = absent)."""
    out: List[dict] = []
    for i, ts in enumerate(stamps):
        turn = {"role": "user" if i % 2 == 0 else "assistant", "text": f"turn-text-{i}"}
        if ts is not None:
            turn["timestamp"] = ts
        out.append(turn)
    return out


def _session(turns: List[dict], project_root: Path) -> HarnessSession:
    return HarnessSession(
        id="sess-fsa",
        slug="sess-fsa",
        harness="claude-code",
        agent_label="Claude Code",
        project_name="test",
        project_root=str(project_root.resolve()),
        started_at=SESSION_STARTED_AT,
        ended_at="2026-05-19T12:00:00Z",
        title="T",
        metadata={"turns": turns},
    )


def _extractor(tmp_path: Path, session: HarnessSession) -> SessionGraphExtractor:
    project_root = tmp_path / "project"
    cache_dir = tmp_path / ".tesserae" / "session_findings"
    project_root.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return SessionGraphExtractor(
        project_root=project_root.resolve(),
        cache_dir=cache_dir,
        doc_graph=ResearchGraph(nodes=[], edges=[]),
        sessions=[session],
        json_client=object(),
        max_turns_per_chunk=10,
    )


class _FixedFindings:
    """``extract_with_llm`` stub returning findings with fixed CHUNK-LOCAL ids."""

    def __init__(self, specs: Sequence[tuple]) -> None:
        self._specs = list(specs)

    def __call__(self, session, transcript_turns, doc_id_context, client, **kwargs):
        return [
            Finding(kind=kind, body=body, turn_ids=list(ids), references=[])
            for kind, body, ids in self._specs
        ]


def _findings(graph: ResearchGraph) -> dict:
    return {
        n.name: (n.metadata or {})
        for n in graph.nodes
        if n.type == ResearchNodeType.SESSION_INSIGHT
    }


# --------------------------------------------------------------------------- #


def test_finding_first_seen_at_is_its_own_turn_timestamp(tmp_path, monkeypatch):
    """A finding is dated by the turn it came from, not the session start."""
    monkeypatch.setattr(sg, "extract_with_llm", _FixedFindings([("insight", "A", [2])]))
    session = _session(
        _turns(
            [
                "2026-05-19T10:00:00Z",
                "2026-05-19T10:05:00Z",
                "2026-05-19T11:30:00Z",
            ]
        ),
        tmp_path / "project",
    )

    graph = _extractor(tmp_path, session).extract()

    assert _findings(graph)["A"]["first_seen_at"] == "2026-05-19T11:30:00Z"


def test_multi_turn_finding_uses_the_earliest_turn_timestamp(tmp_path, monkeypatch):
    """Turn timestamps are not monotonic in the real corpus — min(), not first-id."""
    monkeypatch.setattr(
        sg, "extract_with_llm", _FixedFindings([("insight", "A", [0, 1, 2])])
    )
    session = _session(
        _turns(
            [
                "2026-05-19T11:00:00Z",
                "2026-05-19T10:20:00Z",  # out of order, as 14/481 real sessions are
                "2026-05-19T11:30:00Z",
            ]
        ),
        tmp_path / "project",
    )

    graph = _extractor(tmp_path, session).extract()

    assert _findings(graph)["A"]["first_seen_at"] == "2026-05-19T10:20:00Z"


def test_out_of_range_turn_ids_fall_back_to_the_session_start(tmp_path, monkeypatch):
    """turn_ids come from the LLM and are never range-checked — never index blind."""
    monkeypatch.setattr(
        sg, "extract_with_llm", _FixedFindings([("insight", "A", [999, -4])])
    )
    session = _session(_turns(["2026-05-19T10:05:00Z"] * 2), tmp_path / "project")

    graph = _extractor(tmp_path, session).extract()

    assert _findings(graph)["A"]["first_seen_at"] == SESSION_STARTED_AT


def test_turn_without_a_timestamp_falls_back_to_the_session_start(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "extract_with_llm", _FixedFindings([("insight", "A", [0])]))
    session = _session(_turns([None, "2026-05-19T10:05:00Z"]), tmp_path / "project")

    graph = _extractor(tmp_path, session).extract()

    assert _findings(graph)["A"]["first_seen_at"] == SESSION_STARTED_AT


def test_first_seen_at_is_re_derived_on_a_chunk_cache_hit(tmp_path, monkeypatch):
    """The chunk cache stores no timestamp, so the stamp must survive a replay."""
    stub = _FixedFindings([("insight", "A", [1])])
    monkeypatch.setattr(sg, "extract_with_llm", stub)
    session = _session(
        _turns(["2026-05-19T10:00:00Z", "2026-05-19T11:45:00Z"]), tmp_path / "project"
    )

    first = _extractor(tmp_path, session).extract()

    def _explode(*a, **k):  # cache hit -> the LLM must not be called again
        raise AssertionError("chunk cache miss: the extraction was not replayed")

    monkeypatch.setattr(sg, "extract_with_llm", _explode)
    second = _extractor(tmp_path, session).extract()

    assert _findings(first)["A"]["first_seen_at"] == "2026-05-19T11:45:00Z"
    assert _findings(second)["A"]["first_seen_at"] == "2026-05-19T11:45:00Z"


def test_chunk_cache_key_ignores_turn_timestamps(tmp_path):
    """The chunk hash must stay a function of (role, text) ONLY.

    ``_chunk_content_hash`` hashes the normalised chunk verbatim, so letting a
    timestamp into that payload would invalidate every cached extraction on
    disk and re-bill the whole corpus to the LLM.
    """
    stamped = _session(_turns(["2026-05-19T10:00:00Z"] * 3), tmp_path / "project")
    unstamped = _session(_turns([None] * 3), tmp_path / "project")

    assert sg._chunk_content_hash(sg._normalised_turns(stamped)) == (
        sg._chunk_content_hash(sg._normalised_turns(unstamped))
    )


def test_turn_stamped_findings_survive_the_activity_digest(tmp_path, monkeypatch):
    """The live consumer: gather_findings drops started_at-fallback findings."""
    monkeypatch.setattr(
        sg,
        "extract_with_llm",
        _FixedFindings([("insight", "A", [1]), ("insight", "B", [2])]),
    )
    session = _session(
        _turns(
            [
                "2026-05-19T10:00:00Z",
                "2026-05-19T10:40:00Z",
                "2026-05-19T11:30:00Z",
            ]
        ),
        tmp_path / "project",
    )

    graph = _extractor(tmp_path, session).extract()
    window = Window(
        start=parse_ts("2026-05-19T00:00:00Z"),
        end=parse_ts("2026-05-20T00:00:00Z"),
        label="2026-05-19",
    )
    kept = gather_findings("test", graph, window)

    assert {item.body for item in kept} == {"A", "B"}

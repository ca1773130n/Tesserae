"""Per-turn LLM cache tests (SESS-02) — measure the (N-1)/N hit ratio.

The v2 cache in :class:`SessionGraphExtractor` keys findings per turn on
``(session_id, turn_index, turn_content_hash)`` and stores them at
``.tesserae/session_findings/<safe_id>/turn-<N>.json``. The point of this
suite is to *measure* the cache-hit ratio: appending one turn to an
N-turn session must trigger exactly one LLM extract call, not N.

We monkeypatch ``tesserae.session_graph.extract_with_llm`` with a wrapper
that (1) increments a call counter and (2) returns a deterministic
Finding whose body embeds the turn text — so both the miss-count and the
merged-findings set are exact. No pytest-asyncio, no sleeps.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence, Tuple

import pytest

import tesserae.session_graph as sg
from tesserae.harness_sessions import HarnessSession
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.session_graph import SessionGraphExtractor
from tesserae.session_graph_llm import Finding


class _CountingExtract:
    """Drop-in replacement for ``extract_with_llm`` that counts calls.

    Returns one deterministic Finding per call whose body embeds the
    extracted turn's text, so the merged-findings set is exact and the
    call count directly equals the number of cache-miss (delta) turns.
    """

    def __init__(self) -> None:
        self.calls: int = 0
        self.turn_counts: List[int] = []

    def __call__(
        self,
        session: HarnessSession,
        transcript_turns: Sequence[dict],
        doc_id_context,
        client,
        **kwargs,
    ) -> List[Finding]:
        self.calls += 1
        self.turn_counts.append(len(transcript_turns))
        out: List[Finding] = []
        for turn in transcript_turns:
            text = str(turn.get("text") or "")
            out.append(
                Finding(kind="insight", body=f"insight::{text}", turn_ids=[], references=[])
            )
        return out


def _turns(n: int) -> List[dict]:
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "text": f"turn-text-{i}",
            "timestamp": f"2026-05-19T10:{i:02d}:00Z",
        }
        for i in range(n)
    ]


def _session(turns: List[dict], project_root: Path, *, id: str = "sess-1") -> HarnessSession:
    return HarnessSession(
        id=id,
        slug=id,
        harness="claude-code",
        agent_label="Claude Code",
        project_name="test",
        project_root=str(project_root.resolve()),
        started_at="2026-05-19T10:00:00Z",
        title="T",
        metadata={"turns": turns},
    )


def _doc_graph() -> ResearchGraph:
    return ResearchGraph(
        nodes=[
            ResearchNode(
                id="Paper:foo",
                name="Foo Paper",
                type=ResearchNodeType.PAPER,
                source_path="docs/foo.md",
            ),
        ],
        edges=[],
    )


def _make_extractor(project_root: Path, cache_dir: Path, session: HarnessSession):
    project_root.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return SessionGraphExtractor(
        project_root=project_root.resolve(),
        cache_dir=cache_dir,
        doc_graph=_doc_graph(),
        sessions=[session],
        # A non-None client is required for the LLM pass to run; the
        # monkeypatched extract_with_llm ignores it entirely.
        json_client=object(),
    )


def _finding_pairs(graph: ResearchGraph) -> set:
    """The (kind, body) set of finding nodes minted into the graph."""
    finding_types = {
        ResearchNodeType.SESSION_INSIGHT,
        ResearchNodeType.SESSION_DECISION,
        ResearchNodeType.SESSION_QUESTION,
        ResearchNodeType.SESSION_TODO,
        ResearchNodeType.SESSION_HYPOTHESIS,
        ResearchNodeType.SESSION_TAKEAWAY,
    }
    return {(n.type, n.name) for n in graph.nodes if n.type in finding_types}


def test_cold_extract_calls_llm_per_turn(tmp_path: Path, monkeypatch):
    counter = _CountingExtract()
    monkeypatch.setattr(sg, "extract_with_llm", counter)

    project_root = tmp_path / "project"
    cache_dir = tmp_path / ".tesserae" / "session_findings"
    session = _session(_turns(20), project_root)
    extractor = _make_extractor(project_root, cache_dir, session)

    extractor.extract()

    assert counter.calls == 20, "cold extract must call the LLM once per turn"
    # Every call extracts a single-turn chunk (chunk of size 1).
    assert all(c == 1 for c in counter.turn_counts)
    safe_dir = cache_dir / sg._safe(session.id)
    turn_files = sorted(safe_dir.glob("turn-*.json"))
    assert len(turn_files) == 20, "one cache file per turn"


def test_append_one_turn_extracts_delta_only(tmp_path: Path, monkeypatch):
    """SESS-02 criterion: append 1 turn → exactly 1 LLM extract (hit 19/20)."""
    counter = _CountingExtract()
    monkeypatch.setattr(sg, "extract_with_llm", counter)

    project_root = tmp_path / "project"
    cache_dir = tmp_path / ".tesserae" / "session_findings"

    # Pre-seed the cache with the first 19 turns.
    base_turns = _turns(19)
    seed = _session(list(base_turns), project_root)
    _make_extractor(project_root, cache_dir, seed).extract()
    assert counter.calls == 19

    # Append a 20th turn and extract the full 20.
    counter.calls = 0
    counter.turn_counts.clear()
    full_turns = _turns(20)
    appended = _session(full_turns, project_root)
    _make_extractor(project_root, cache_dir, appended).extract()

    assert counter.calls == 1, "only the appended turn must re-extract"
    n = 20
    hit_ratio = (n - counter.calls) / n
    assert hit_ratio >= 0.95, f"hit ratio {hit_ratio} below 0.95 target"


def test_changed_turn_reextracted(tmp_path: Path, monkeypatch):
    """Mutating one turn's text re-extracts only that turn (content-keyed)."""
    counter = _CountingExtract()
    monkeypatch.setattr(sg, "extract_with_llm", counter)

    project_root = tmp_path / "project"
    cache_dir = tmp_path / ".tesserae" / "session_findings"

    base_turns = _turns(20)
    _make_extractor(project_root, cache_dir, _session(list(base_turns), project_root)).extract()
    assert counter.calls == 20

    counter.calls = 0
    counter.turn_counts.clear()
    mutated = _turns(20)
    mutated[5] = {**mutated[5], "text": "COMPLETELY-DIFFERENT-TEXT"}
    _make_extractor(project_root, cache_dir, _session(mutated, project_root)).extract()

    assert counter.calls == 1, "only the mutated turn must re-extract"


def test_merge_equivalence(tmp_path: Path, monkeypatch):
    """Per-turn (chunk-of-1) findings equal a single whole-session extract."""
    counter = _CountingExtract()
    monkeypatch.setattr(sg, "extract_with_llm", counter)

    turns = _turns(5)

    # Per-turn extraction (chunk of size 1 each).
    pr_a = tmp_path / "proj-a"
    cache_a = tmp_path / "cache-a"
    graph_a = _make_extractor(pr_a, cache_a, _session(list(turns), pr_a)).extract()
    per_turn = _finding_pairs(graph_a)

    # Whole-session extraction: a single extract over all 5 turns. The
    # deterministic stub emits the same per-turn Finding bodies, so the
    # set of (kind, body) findings must be identical.
    pr_b = tmp_path / "proj-b"
    cache_b = tmp_path / "cache-b"

    def _whole_session(session, transcript_turns, doc_id_context, client, **kwargs):
        out: List[Finding] = []
        for turn in transcript_turns:
            out.append(
                Finding(kind="insight", body=f"insight::{turn.get('text')}", turn_ids=[], references=[])
            )
        return out

    monkeypatch.setattr(sg, "extract_with_llm", _whole_session)
    # Force a single whole-session call by giving max_turns_per_chunk >= len.
    extractor_b = _make_extractor(pr_b, cache_b, _session(list(turns), pr_b))
    extractor_b.max_turns_per_chunk = 100
    graph_b = extractor_b.extract()
    whole = _finding_pairs(graph_b)

    assert per_turn == whole, "per-turn merge must equal whole-session findings"

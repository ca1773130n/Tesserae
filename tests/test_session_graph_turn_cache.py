"""Per-CHUNK LLM cache tests (SESS-02, Codex #2) — chunk-level incrementality.

The v3 cache in :class:`SessionGraphExtractor` partitions a session's
normalised turns into stable, NON-overlapping chunks of
``max_turns_per_chunk`` aligned to the ORIGINAL transcript indices
(chunk k = turns[k*size:(k+1)*size]) and keys each chunk's findings on
``(session_id, chunk_index, chunk_content_hash)`` + project_root_hash +
schema version, stored at
``.tesserae/session_findings/<safe_id>/chunk-<K>.json``.

Why chunk-level (not per-turn)? The real extractor produces findings that
span multiple turns WITHIN a chunk. The old per-turn cache extracted one
turn at a time, which (a) renumbered every turn to id 0 and (b) made
cross-turn findings impossible — so "incremental == whole-session" was
false for any non-trivial extractor. These tests use a REALISTIC
multi-turn extractor stub that:
  * actually emits a cross-turn finding (referencing two turn indices in
    the same chunk), and
  * uses ORIGINAL transcript indices in the turn_ids it returns,
and assert the incremental path is byte-identical (kind, body, ORIGINAL
turn_ids) to a single whole-session extraction over the same chunking,
while re-extracting only the affected chunk on append.

No pytest-asyncio, no sleeps.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence

import pytest

import tesserae.session_graph as sg
from tesserae.harness_sessions import HarnessSession
from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.session_graph import SessionGraphExtractor
from tesserae.session_graph_llm import Finding


class _RealisticExtract:
    """A realistic multi-turn extractor stub that counts chunk extractions.

    For each chunk it receives (a list of turns enumerated from 0, exactly
    like the real ``extract_with_llm``), it emits:
      * one per-turn "insight" Finding whose body embeds the turn text and
        whose turn_ids reference that turn's CHUNK-LOCAL index, and
      * one cross-turn "takeaway" Finding spanning the chunk's first and
        last turn (when the chunk has >= 2 turns) — chunk-local indices
        [0, len-1].

    Returning chunk-local turn_ids mirrors the real extractor (which
    enumerates the passed chunk from 0). The orchestrator is responsible
    for remapping those back to ORIGINAL transcript indices via the chunk
    offset, so the test asserts the orchestrator did that correctly.
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
        n = len(transcript_turns)
        self.turn_counts.append(n)
        out: List[Finding] = []
        for j, turn in enumerate(transcript_turns):
            text = str(turn.get("text") or "")
            out.append(
                Finding(
                    kind="insight",
                    body=f"insight::{text}",
                    turn_ids=[j],  # chunk-local; orchestrator remaps to original
                    references=[],
                )
            )
        if n >= 2:
            first = str(transcript_turns[0].get("text") or "")
            last = str(transcript_turns[-1].get("text") or "")
            out.append(
                Finding(
                    kind="takeaway",
                    body=f"cross::{first}->{last}",
                    turn_ids=[0, n - 1],  # cross-turn, chunk-local
                    references=[],
                )
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


def _make_extractor(
    project_root: Path,
    cache_dir: Path,
    session: HarnessSession,
    *,
    max_turns_per_chunk: int = 10,
):
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
        max_turns_per_chunk=max_turns_per_chunk,
    )


_FINDING_TYPES = {
    ResearchNodeType.SESSION_INSIGHT,
    ResearchNodeType.SESSION_DECISION,
    ResearchNodeType.SESSION_QUESTION,
    ResearchNodeType.SESSION_TODO,
    ResearchNodeType.SESSION_HYPOTHESIS,
    ResearchNodeType.SESSION_TAKEAWAY,
}


def _finding_pairs(graph: ResearchGraph) -> set:
    """The (kind, body) set of finding nodes minted into the graph."""
    return {(n.type, n.name) for n in graph.nodes if n.type in _FINDING_TYPES}


def _finding_records(graph: ResearchGraph) -> set:
    """(type, body, tuple(original turn_ids)) for every finding node.

    turn_ids come from the node metadata the orchestrator mints, so this
    asserts the ORIGINAL (remapped) indices, not chunk-local ones.
    """
    out = set()
    for n in graph.nodes:
        if n.type not in _FINDING_TYPES:
            continue
        tids = tuple((n.metadata or {}).get("turn_ids") or [])
        out.add((n.type, n.name, tids))
    return out


# ---------------------------------------------------------------------------
# Cold extract + chunking
# ---------------------------------------------------------------------------


def test_cold_extract_calls_llm_per_chunk(tmp_path: Path, monkeypatch):
    counter = _RealisticExtract()
    monkeypatch.setattr(sg, "extract_with_llm", counter)

    project_root = tmp_path / "project"
    cache_dir = tmp_path / ".tesserae" / "session_findings"
    # 25 turns, chunk size 10 → 3 chunks of [10, 10, 5].
    session = _session(_turns(25), project_root)
    extractor = _make_extractor(project_root, cache_dir, session, max_turns_per_chunk=10)

    extractor.extract()

    assert counter.calls == 3, "cold extract must call the LLM once per chunk"
    assert counter.turn_counts == [10, 10, 5]
    safe_dir = cache_dir / sg._safe(session.id)
    chunk_files = sorted(safe_dir.glob("chunk-*.json"))
    assert len(chunk_files) == 3, "one cache file per chunk"


def test_correct_original_turn_ids(tmp_path: Path, monkeypatch):
    """Findings must reference ORIGINAL transcript indices, never renumbered-from-0."""
    counter = _RealisticExtract()
    monkeypatch.setattr(sg, "extract_with_llm", counter)

    project_root = tmp_path / "project"
    cache_dir = tmp_path / ".tesserae" / "session_findings"
    # 12 turns, chunk size 10 → chunks [0..9] and [10..11].
    session = _session(_turns(12), project_root)
    extractor = _make_extractor(project_root, cache_dir, session, max_turns_per_chunk=10)
    graph = extractor.extract()

    recs = _finding_records(graph)

    # The cross-turn takeaway in chunk-1 spans original turns 10 and 11,
    # NOT chunk-local 0 and 1.
    cross_second = (
        ResearchNodeType.SESSION_TAKEAWAY,
        "cross::turn-text-10->turn-text-11",
        (10, 11),
    )
    assert cross_second in recs, "second chunk's cross-turn finding must use original ids 10,11"

    # The cross-turn takeaway in chunk-0 spans original turns 0 and 9.
    cross_first = (
        ResearchNodeType.SESSION_TAKEAWAY,
        "cross::turn-text-0->turn-text-9",
        (0, 9),
    )
    assert cross_first in recs

    # A per-turn insight from chunk-1 must carry its ORIGINAL index (e.g. 10),
    # never 0.
    insight_10 = (ResearchNodeType.SESSION_INSIGHT, "insight::turn-text-10", (10,))
    assert insight_10 in recs

    # Sanity: no finding sourced from a non-first chunk should claim turn_id 0
    # unless it genuinely is original turn 0.
    for ntype, body, tids in recs:
        if body == "insight::turn-text-10":
            assert tids == (10,), "renumbering bug: chunk-local id leaked"


# ---------------------------------------------------------------------------
# Incrementality
# ---------------------------------------------------------------------------


def test_append_one_turn_reextracts_only_affected_chunk(tmp_path: Path, monkeypatch):
    """Append 1 turn → exactly 1 chunk re-extracts (the last/changed one)."""
    counter = _RealisticExtract()
    monkeypatch.setattr(sg, "extract_with_llm", counter)

    project_root = tmp_path / "project"
    cache_dir = tmp_path / ".tesserae" / "session_findings"

    # Pre-seed: 19 turns, chunk size 10 → chunks [10, 9].
    base_turns = _turns(19)
    seed = _session(list(base_turns), project_root)
    _make_extractor(project_root, cache_dir, seed, max_turns_per_chunk=10).extract()
    assert counter.calls == 2, "cold: 2 chunks for 19 turns @ size 10"

    # Append a 20th turn → chunk 0 (turns 0..9) unchanged, chunk 1 (9→10
    # turns) changed. Only chunk 1 re-extracts.
    counter.calls = 0
    counter.turn_counts.clear()
    full_turns = _turns(20)
    appended = _session(full_turns, project_root)
    _make_extractor(project_root, cache_dir, appended, max_turns_per_chunk=10).extract()

    assert counter.calls == 1, "append must re-extract only the affected (last) chunk"
    chunks = 2
    hit_ratio = (chunks - counter.calls) / chunks
    assert hit_ratio == pytest.approx(0.5), f"chunk hit ratio {hit_ratio} != 1/2"


def test_middle_turn_mutation_invalidates_downstream_chunks(tmp_path: Path, monkeypatch):
    """Mutating a chunk-0 turn re-extracts only chunk 0 (downstream hashes hold).

    With non-overlapping chunks aligned to fixed index ranges, mutating a
    turn in chunk 0 changes ONLY chunk 0's content hash (chunk 1's turn
    range is untouched). Inserting/removing turns is what shifts downstream
    chunks; in-place mutation is local to its own chunk.
    """
    counter = _RealisticExtract()
    monkeypatch.setattr(sg, "extract_with_llm", counter)

    project_root = tmp_path / "project"
    cache_dir = tmp_path / ".tesserae" / "session_findings"

    base_turns = _turns(20)  # chunks [0..9], [10..19]
    _make_extractor(
        project_root, cache_dir, _session(list(base_turns), project_root),
        max_turns_per_chunk=10,
    ).extract()
    assert counter.calls == 2

    counter.calls = 0
    counter.turn_counts.clear()
    mutated = _turns(20)
    mutated[5] = {**mutated[5], "text": "COMPLETELY-DIFFERENT-TEXT"}
    _make_extractor(
        project_root, cache_dir, _session(mutated, project_root),
        max_turns_per_chunk=10,
    ).extract()

    assert counter.calls == 1, "in-place mutation re-extracts only its own chunk"


def test_inserted_turn_shifts_downstream_chunks(tmp_path: Path, monkeypatch):
    """Inserting a middle turn re-extracts its chunk AND all downstream chunks."""
    counter = _RealisticExtract()
    monkeypatch.setattr(sg, "extract_with_llm", counter)

    project_root = tmp_path / "project"
    cache_dir = tmp_path / ".tesserae" / "session_findings"

    base_turns = _turns(30)  # chunks [0..9], [10..19], [20..29]
    _make_extractor(
        project_root, cache_dir, _session(list(base_turns), project_root),
        max_turns_per_chunk=10,
    ).extract()
    assert counter.calls == 3

    counter.calls = 0
    counter.turn_counts.clear()
    # Insert a new turn at index 5 → chunk 0 changes, and every turn after
    # shifts by one, so chunks 1 and 2 also change. 31 turns → 4 chunks
    # [10,10,10,1]; chunk 0's content changed and chunks 1..3 are all new
    # content ranges → all 4 extract.
    inserted = _turns(30)
    inserted.insert(5, {"role": "user", "text": "INSERTED-TURN", "timestamp": "2026-05-19T10:99:00Z"})
    _make_extractor(
        project_root, cache_dir, _session(inserted, project_root),
        max_turns_per_chunk=10,
    ).extract()

    # All 4 chunks of the now-31-turn session re-extract: chunk boundaries
    # shifted so no chunk's content hash matches the cached 3-chunk layout.
    assert counter.calls == 4, "insert shifts all downstream chunk contents"


# ---------------------------------------------------------------------------
# Merge equivalence (the core Codex #2 guarantee)
# ---------------------------------------------------------------------------


def test_merge_equivalence_with_cross_turn_findings(tmp_path: Path, monkeypatch):
    """Incremental (chunked) findings == a single whole-session extraction.

    Byte-identical means same (kind, body, ORIGINAL turn_ids) for every
    finding — INCLUDING the cross-turn takeaways that the old per-turn
    cache could never produce. This is the guarantee the per-turn cache
    violated: a trivial single-turn mock cannot satisfy it.
    """
    counter = _RealisticExtract()
    monkeypatch.setattr(sg, "extract_with_llm", counter)

    # 25 turns, chunk size 10 → 3 chunks. Pre-seed only the first 24 turns
    # so the incremental run reuses 2 cached chunks and re-extracts 1.
    pr_a = tmp_path / "proj-a"
    cache_a = tmp_path / "cache-a"

    seed_turns = _turns(24)
    _make_extractor(
        pr_a, cache_a, _session(list(seed_turns), pr_a, id="sess-a"),
        max_turns_per_chunk=10,
    ).extract()
    seed_calls = counter.calls
    assert seed_calls == 3  # [10,10,4]

    # Grow to 25 turns → chunk 0,1 cached (unchanged), chunk 2 (4→5) misses.
    counter.calls = 0
    full_turns = _turns(25)
    graph_inc = _make_extractor(
        pr_a, cache_a, _session(list(full_turns), pr_a, id="sess-a"),
        max_turns_per_chunk=10,
    ).extract()
    assert counter.calls == 1, "incremental: only the grown last chunk re-extracts"
    chunks = 3
    hit_ratio = (chunks - counter.calls) / chunks
    assert hit_ratio == pytest.approx(2 / 3), f"chunk hit ratio {hit_ratio} != 2/3"

    inc_records = _finding_records(graph_inc)

    # Whole-session, cold cache, identical chunking — no reuse.
    pr_b = tmp_path / "proj-b"
    cache_b = tmp_path / "cache-b"
    counter.calls = 0
    graph_whole = _make_extractor(
        pr_b, cache_b, _session(list(full_turns), pr_b, id="sess-a"),
        max_turns_per_chunk=10,
    ).extract()
    assert counter.calls == 3, "whole-session cold: all 3 chunks extract"
    whole_records = _finding_records(graph_whole)

    assert inc_records == whole_records, (
        "incremental chunked extraction must be byte-identical (kind, body, "
        "ORIGINAL turn_ids) to a single whole-session extraction"
    )

    # And the guarantee specifically covers cross-turn findings.
    cross = {r for r in whole_records if r[0] == ResearchNodeType.SESSION_TAKEAWAY}
    assert cross, "stub must actually produce cross-turn findings"
    assert cross <= inc_records, "cross-turn findings must survive the incremental path"

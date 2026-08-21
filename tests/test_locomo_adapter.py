"""LoCoMo adapter — staging, isolation, the reuse gate, and the protocol gate.

Offline and synthetic: no compile, no model, no network. ``compile_fn`` and
``search_fn`` take stubs, on ``MabMemory``'s reasoning — the real pair is an
hours-long extraction and a metered embedding call, and a harness whose wiring
can only be checked by running the benchmark does not get checked.

What is pinned here is the handful of decisions that would otherwise produce a
plausible wrong number: which directory a conversation compiles into, whether a
document number survives the round trip to a retrieved ``source_path``, whether
a reused graph is proved to belong to the conversation being scored, and whether
the protocol gate can be satisfied by typing flags.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pytest

from evals.lme_mab.adapter import MabHit, document_index, document_title
from evals.locomo.adapter import (
    EVIDENCE_SOURCE_CHARS,
    PROTOCOL_BACKBONE,
    PROTOCOL_DATASET_REVISION,
    PROTOCOL_JUDGE,
    PROTOCOL_JUDGE_RUNS,
    LocomoMemory,
    RefusedToCompileInRepo,
    document_name,
    guard_work_dir,
    protocol_blockers,
    render_session,
)
from evals.locomo.dataset import Conversation, LocomoQuestion, LocomoSession, Turn


class _Node:
    def __init__(self, name: str, description: str = "", source_path: str = ""):
        self.name = name
        self.description = description
        self.source_path = source_path


class _Scored:
    def __init__(self, node: _Node) -> None:
        self.node = node


class _Result:
    def __init__(self, nodes: Sequence[_Node], total_matches: int = 0) -> None:
        self.scored = [_Scored(n) for n in nodes]
        self.total_matches = total_matches or len(nodes)


def _turn(session: int, index: int, text: str, caption: str = "") -> Turn:
    return Turn(dia_id=f"D{session}:{index}", session=session, speaker="Ada",
                text=text, blip_caption=caption)


def _conversation(sample_id: str = "conv-test", sessions: int = 2) -> Conversation:
    return Conversation(
        sample_id=sample_id,
        speaker_a="Ada",
        speaker_b="Bo",
        sessions=[
            LocomoSession(
                number=n, date=f"noon on {n} May, 2023",
                turns=[_turn(n, 1, f"session {n} first"),
                       _turn(n, 2, f"session {n} second", "a photo of a bicycle")],
            )
            for n in range(1, sessions + 1)
        ],
        questions=[LocomoQuestion(question="What?", category=4, evidence=["D1:1"],
                                  conversation=sample_id, answer="first")],
    )


def _memory(**kwargs) -> LocomoMemory:
    kwargs.setdefault("compile_fn", lambda work: None)
    kwargs.setdefault("backend", object())
    return LocomoMemory(**kwargs)


def _staged(tmp_path: Path, memory: LocomoMemory, conversation: Conversation):
    """Ingest without compiling, then stand a graph in for the compiled one.

    ``_resolve_graph`` would otherwise read a ``graph.json`` that only a real
    compile writes, and the search lane is stubbed anyway — what these cases
    exercise is what the adapter does with hits, not what produced them.
    """
    result = memory.ingest(conversation, work=tmp_path, compile_project=False)
    memory._graph = object()
    return result


# ------------------------------------------------------------------ the guard


def test_staging_refuses_the_repo_root():
    repo = Path(__file__).resolve().parents[1]
    with pytest.raises(RefusedToCompileInRepo):
        guard_work_dir(repo)


def test_staging_refuses_a_subdirectory_of_the_repo():
    repo = Path(__file__).resolve().parents[1]
    with pytest.raises(RefusedToCompileInRepo):
        guard_work_dir(repo / "evals" / "locomo" / "scratch")


def test_staging_refuses_any_directory_holding_a_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    with pytest.raises(RefusedToCompileInRepo):
        guard_work_dir(tmp_path)


def test_the_refusal_fires_before_anything_is_written(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    memory = _memory()
    with pytest.raises(RefusedToCompileInRepo):
        memory.ingest(_conversation(), work=repo / "scratch")
    assert not (repo / "scratch").exists()


# --------------------------------------------------------------------- layout


def test_each_conversation_compiles_into_its_own_directory(tmp_path):
    """Speaker names repeat across LoCoMo's conversations.

    A pooled corpus would let a question about one conversation retrieve
    another's turns about a different person of the same name, and nothing in a
    reported number would show it.
    """
    memory = _memory()
    first = memory.ingest(_conversation("conv-26"), work=tmp_path)
    second = memory.ingest(_conversation("conv-41"), work=tmp_path)
    assert first.work == tmp_path / "conv-26"
    assert second.work == tmp_path / "conv-41"
    assert first.corpus_dir != second.corpus_dir


def test_the_document_number_is_the_sessions_own(tmp_path):
    """``session_1`` stages as ``session-0001.md``, so ``D1:3`` needs no table."""
    memory = _memory()
    result = memory.ingest(_conversation(sessions=3), work=tmp_path)
    assert sorted(p.name for p in result.corpus_dir.glob("*.md")) == [
        "session-0001.md", "session-0002.md", "session-0003.md"]


def test_a_document_name_round_trips_to_its_session_number():
    for number in (1, 7, 19, 32):
        assert document_index(document_name(number)) == number
        assert document_index(f"corpus/{document_name(number)}") == number


def test_a_document_body_carries_the_date_and_the_caption():
    session = LocomoSession(number=4, date="1:56 pm on 8 May, 2023",
                            turns=[_turn(4, 1, "hello", "a photo of a bicycle")])
    body = render_session(session)
    assert body.startswith(f"# {document_title(4)}")
    # The date is in the BODY. A date that lives only in a filename is a date
    # the temporal category cannot retrieve.
    assert "Chat Time: 1:56 pm on 8 May, 2023" in body
    assert "a photo of a bicycle" in body


def test_restaging_the_same_conversation_is_byte_identical(tmp_path):
    memory = _memory()
    first = memory.ingest(_conversation(), work=tmp_path)
    before = {p.name: p.read_bytes() for p in first.corpus_dir.glob("*.md")}
    second = memory.ingest(_conversation(), work=tmp_path)
    after = {p.name: p.read_bytes() for p in second.corpus_dir.glob("*.md")}
    assert before == after


def test_a_shorter_conversation_does_not_inherit_the_longer_ones_documents(tmp_path):
    """A stale document is retrievable evidence from a corpus this run never saw."""
    memory = _memory()
    memory.ingest(_conversation("conv-x", sessions=4), work=tmp_path)
    result = memory.ingest(_conversation("conv-x", sessions=2), work=tmp_path)
    assert sorted(p.name for p in result.corpus_dir.glob("*.md")) == [
        "session-0001.md", "session-0002.md"]


def test_a_conversation_with_no_sessions_raises_rather_than_staging_nothing(tmp_path):
    empty = Conversation(sample_id="conv-empty", speaker_a="", speaker_b="",
                         sessions=[], questions=[])
    with pytest.raises(ValueError):
        _memory().ingest(empty, work=tmp_path)


def test_the_ingest_result_counts_what_it_staged(tmp_path):
    result = _memory().ingest(_conversation(sessions=3), work=tmp_path)
    assert result.documents == 3
    assert result.turns == 6
    assert result.dated_sessions == 3
    assert result.captioned_turns == 3
    assert result.compiled and not result.reused


# ---------------------------------------------------------------- reuse gate


def _compile_marker(work: Path, documents: Sequence[str]) -> None:
    graph = work / ".tesserae"
    graph.mkdir(parents=True, exist_ok=True)
    (graph / "graph.json").write_text(
        json.dumps({"nodes": [{"source_path": f"corpus/{name}"}
                              for name in documents]}),
        encoding="utf-8")


def test_reuse_refuses_without_a_staged_corpus(tmp_path):
    with pytest.raises(FileNotFoundError):
        _memory().ingest(_conversation(), work=tmp_path, reuse_compiled=True)


def test_reuse_refuses_when_the_corpus_differs(tmp_path):
    memory = _memory()
    result = memory.ingest(_conversation("conv-x", sessions=2), work=tmp_path)
    _compile_marker(result.work, ["session-0001.md", "session-0002.md"])
    (result.corpus_dir / "session-0001.md").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not this conversation's corpus"):
        memory.ingest(_conversation("conv-x", sessions=2), work=tmp_path,
                      reuse_compiled=True)


def test_reuse_refuses_when_the_graph_indexes_another_conversation(tmp_path):
    """The corpus check alone is not enough, and this is why.

    ``ingest`` restages BEFORE compiling, so a directory can hold one
    conversation's fresh documents beside another's graph — and the corpus check
    passes on both. Tying the graph to the documents is what stops a run
    reporting "reused" while retrieving from the wrong corpus.
    """
    memory = _memory()
    result = memory.ingest(_conversation("conv-x", sessions=3), work=tmp_path)
    _compile_marker(result.work, ["session-0001.md"])  # a two-session graph
    with pytest.raises(ValueError, match="does not index"):
        memory.ingest(_conversation("conv-x", sessions=3), work=tmp_path,
                      reuse_compiled=True)


def test_reuse_accepts_a_matching_corpus_and_graph_and_writes_nothing(tmp_path):
    memory = _memory()
    result = memory.ingest(_conversation("conv-x", sessions=2), work=tmp_path)
    _compile_marker(result.work, ["session-0001.md", "session-0002.md"])
    before = {p.name: p.stat().st_mtime_ns for p in result.corpus_dir.glob("*.md")}
    reused = memory.ingest(_conversation("conv-x", sessions=2), work=tmp_path,
                           reuse_compiled=True)
    after = {p.name: p.stat().st_mtime_ns for p in reused.corpus_dir.glob("*.md")}
    assert reused.reused and not reused.compiled
    assert before == after


# ------------------------------------------------------------------- querying


def test_query_returns_at_most_k_and_never_pads(tmp_path):
    nodes = [_Node(document_title(n), source_path=f"corpus/{document_name(n)}")
             for n in (1, 2)]
    memory = _memory(search_fn=lambda *a, **kw: _Result(nodes))
    _staged(tmp_path, memory, _conversation(sessions=2))
    hits = memory.query_hits("q", k=5)
    assert len(hits) == 2
    assert memory.shortfalls and memory.shortfalls[0]["returned"] == 2
    assert memory.shortfalls[0]["conversation"] == "conv-test"


def test_documents_de_duplicate_onto_the_session_at_its_best_rank(tmp_path):
    nodes = [
        _Node(document_title(2), source_path=f"corpus/{document_name(2)}"),
        _Node("A concept", source_path=f"corpus/{document_name(2)}"),
        _Node(document_title(1), source_path=f"corpus/{document_name(1)}"),
    ]
    memory = _memory(search_fn=lambda *a, **kw: _Result(nodes))
    _staged(tmp_path, memory, _conversation(sessions=2))
    assert memory.search_documents("q", k=3) == [2, 1]


def test_a_hit_with_no_staged_document_is_counted_and_dropped(tmp_path):
    """Never resolved to a nearby number: a fabricated hit scores better and
    means nothing."""
    nodes = [_Node("Elsewhere", source_path="/somewhere/else/notes.md"),
             _Node(document_title(1), source_path=f"corpus/{document_name(1)}")]
    memory = _memory(search_fn=lambda *a, **kw: _Result(nodes))
    _staged(tmp_path, memory, _conversation(sessions=2))
    assert memory.search_documents("q", k=2) == [1]
    assert memory.n_unmapped_hits == 1


def test_only_the_document_anchor_expands_and_only_once(tmp_path):
    """Two nodes from one session must not spend two evidence items on one file."""
    memory = _memory()
    result = memory.ingest(_conversation(sessions=2), work=tmp_path,
                           compile_project=False)
    path = str(result.corpus_dir / document_name(1))
    hits = [
        MabHit(text="anchor", source_path=path, name=document_title(1)),
        MabHit(text="twin", source_path=path, name=document_title(1)),
        MabHit(text="concept", source_path=path, name="A concept"),
    ]
    evidence = memory.answer_evidence(hits)
    assert "session 1 first" in evidence[0]
    assert evidence[1] == "twin"
    assert evidence[2] == "concept"


def test_the_summary_control_expands_nothing(tmp_path):
    memory = _memory()
    result = memory.ingest(_conversation(), work=tmp_path, compile_project=False)
    hits = [MabHit(text="anchor",
                   source_path=str(result.corpus_dir / document_name(1)),
                   name=document_title(1))]
    assert memory.answer_evidence(hits, expand=False) == ["anchor"]


def test_the_evidence_cap_covers_every_session_in_this_corpus():
    """Measured this phase: the largest staged document is 7,275 characters.

    The cap equals ``hybrid.SOURCE_LEXICAL_CHARS``, so the backbone reads
    exactly the text the retriever scored. A smaller cap would buy nothing —
    there is no session long enough for it to bind — and would reintroduce the
    ranked-but-unanswerable failure it exists to prevent.
    """
    from tesserae.retrieval.hybrid import SOURCE_LEXICAL_CHARS

    assert EVIDENCE_SOURCE_CHARS == SOURCE_LEXICAL_CHARS == 8_000


# -------------------------------------------------------------- protocol gate


def _met_meta(**overrides) -> Dict[str, Any]:
    meta = {
        "llm_model": PROTOCOL_BACKBONE,
        "judge": PROTOCOL_JUDGE,
        "judge_runs": str(PROTOCOL_JUDGE_RUNS),
        "dataset_revision": PROTOCOL_DATASET_REVISION,
        "embedding_model": "model2vec",
        "evidence_budget": "10",
        "evidence": {"llm_judge_calls": 12, "answer_calls": 12, "canary_calls": 1},
    }
    meta.update(overrides)
    return meta


def test_the_gate_passes_only_when_every_control_is_met_and_evidenced():
    assert protocol_blockers(_met_meta()) == []


def test_a_missing_declaration_blocks_exactly_as_a_wrong_one_does():
    """"We did not record which model answered" is not "the model matched"."""
    missing = protocol_blockers(_met_meta(llm_model=""))
    wrong = protocol_blockers(_met_meta(llm_model="some-other-model"))
    assert len(missing) == 1 and len(wrong) == 1
    assert missing[0].startswith("llm_model:") and wrong[0].startswith("llm_model:")


def test_an_unfixed_control_blocks_only_when_undeclared():
    """The publication fixes no embedder, so any value passes and none blocks."""
    assert protocol_blockers(_met_meta(embedding_model="anything-at-all")) == []
    blockers = protocol_blockers(_met_meta(embedding_model=""))
    assert len(blockers) == 1 and blockers[0].startswith("embedding_model:")


def test_declaring_a_judge_without_judging_does_not_unlock_the_table():
    """The deterministic judge grades everything and calls no model.

    Declaring ``gpt-4o-mini`` and serving zero LLM verdicts is exactly the hole
    a hand-written answers file walked through in the LongMemEval arm, so it is
    closed here before any run exists.
    """
    blockers = protocol_blockers(_met_meta(
        evidence={"llm_judge_calls": 0, "answer_calls": 12, "canary_calls": 1}))
    assert any(b.startswith("llm_judge_calls:") for b in blockers)


def test_a_run_with_no_canary_is_blocked():
    blockers = protocol_blockers(_met_meta(
        evidence={"llm_judge_calls": 3, "answer_calls": 12, "canary_calls": 0}))
    assert any(b.startswith("canary_calls:") for b in blockers)


def test_a_declaration_with_no_evidence_block_is_an_unverified_claim():
    blockers = protocol_blockers({k: v for k, v in _met_meta().items()
                                  if k != "evidence"})
    assert len(blockers) == 1 and blockers[0].startswith("evidence:")


def test_a_changed_dataset_revision_blocks():
    blockers = protocol_blockers(_met_meta(dataset_revision="sha256:000000000000"))
    assert any(b.startswith("dataset_revision:") for b in blockers)


def test_the_gate_blocks_todays_deterministic_run():
    """The headline property of this phase, asserted rather than asserted about.

    Nothing on this machine can meet the judge control today, and the report
    must therefore withhold its published-comparable table. That refusal is the
    feature; a test is what keeps it from being softened later.
    """
    today = {
        "llm_model": "", "judge": "deterministic", "judge_runs": "1",
        "dataset_revision": PROTOCOL_DATASET_REVISION,
        "embedding_model": "model2vec", "evidence_budget": "10",
        "evidence": {"llm_judge_calls": 0, "answer_calls": 0, "canary_calls": 0},
    }
    blockers = protocol_blockers(today)
    assert any(b.startswith("judge:") for b in blockers)
    assert any(b.startswith("llm_judge_calls:") for b in blockers)

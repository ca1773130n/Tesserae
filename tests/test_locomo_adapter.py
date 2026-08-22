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
    EVIDENCE_EXTRA_SOURCE_CHARS,
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
    session_date,
)
from evals.locomo.dataset import Conversation, LocomoQuestion, LocomoSession, Turn
from tesserae.research_graph import ResearchNode, ResearchNodeType
from tesserae.retrieval.hybrid import HybridSearchResult, ScoredNode


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
                number=n, date=f"1:56 pm on {n} May, 2023",
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


def test_one_file_is_pasted_once_however_many_hits_name_it(tmp_path):
    """Two nodes from one session must not spend two evidence items on one file.

    This is the property ``is_document_anchor`` was doing double duty to protect
    and no longer has to: ``chosen`` and ``spent`` are keyed on the FILE, so a
    session's text goes to the first hit that names it whether that hit is the
    anchor or a concept extracted from it.
    """
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
    assert sum("session 1 first" in item for item in evidence) == 1
    assert evidence[1] == "twin — session date: 1 May 2023"
    assert evidence[2] == "concept — session date: 1 May 2023"


def _padded(memory, tmp_path, pad: Sequence[int]):
    """Stage ``len(pad)`` documents, session ``n`` padded by ``pad[n - 1]`` chars.

    ``LocomoSession`` is frozen, so the padding turn goes in at construction.
    """
    conversation = _conversation(sessions=len(pad))
    conversation = Conversation(
        sample_id=conversation.sample_id,
        speaker_a=conversation.speaker_a,
        speaker_b=conversation.speaker_b,
        sessions=[
            LocomoSession(number=session.number, date=session.date,
                          turns=list(session.turns) + [
                              _turn(session.number, 3,
                                    f"pad{session.number} " * (extra // 6))])
            for session, extra in zip(conversation.sessions, pad)
        ],
        questions=conversation.questions,
    )
    return memory.ingest(conversation, work=tmp_path, compile_project=False)


def test_a_concept_node_expands_the_session_it_came_from(tmp_path):
    """The single largest loss in the benchmark, as one assertion.

    ``documents_of`` scored the gold session as retrieved for 93.3% of conv-26's
    gradeable questions; its TEXT reached the prompt for 53.3%, because the rest
    were reached through a concept node whose whole contribution was a name and a
    ~75-character description. The session behind a hit is evidence whether or
    not the hit is the node that stands for it.
    """
    memory = _memory()
    result = memory.ingest(_conversation(sessions=2), work=tmp_path,
                           compile_project=False)
    hits = [MabHit(text="A concept", name="A concept",
                   source_path=str(result.corpus_dir / document_name(2)))]
    (evidence,) = memory.answer_evidence(hits)
    assert "session 2 first" in evidence


def test_the_extra_budget_never_costs_an_anchor_its_expansion(tmp_path):
    """The addition is ADDITIVE, and it is the code that guarantees it.

    Anchors are chosen before the budget is consulted, so an anchor ranked below
    enough concept hits to exhaust the budget still expands. Spending one budget
    across both — even with anchors given first claim within it — regressed 14 of
    conv-26's 150 gradeable questions, because the rule it replaced had no budget
    at all and a question whose hits were nine anchors used to paste all nine.
    """
    memory = _memory()
    result = _padded(memory, tmp_path,
                     pad=[EVIDENCE_EXTRA_SOURCE_CHARS // 2] * 3)
    hits = [
        MabHit(text="c1", name="c1", source_path=str(result.corpus_dir / document_name(1))),
        MabHit(text="c2", name="c2", source_path=str(result.corpus_dir / document_name(2))),
        MabHit(text="anchor", name=document_title(3),
               source_path=str(result.corpus_dir / document_name(3))),
    ]
    evidence = memory.answer_evidence(hits)
    assert "session 3 first" in evidence[2], "the anchor lost its text to the budget"
    assert "session 1 first" in evidence[0], "the first extra should still fit"


def test_the_extra_budget_bounds_what_the_new_expansion_adds(tmp_path):
    """Some extra, and never more than the budget. Both halves are the point.

    An unbounded version reaches 93.3% gold-session coverage on conv-26 and
    doubles the prompt; the bound is what keeps the growth statable, and it is
    measured against adversarial abstention on every run rather than assumed
    safe.
    """
    memory = _memory()
    result = _padded(memory, tmp_path, pad=[3_000] * 6)
    hits = [MabHit(text=f"c{n}", name=f"c{n}",
                   source_path=str(result.corpus_dir / document_name(n)))
            for n in range(1, 7)]
    summaries = memory.answer_evidence(hits, expand=False)
    expanded = memory.answer_evidence(hits)
    added = sum(len(a) for a in expanded) - sum(len(b) for b in summaries)
    assert added > 0, "no concept hit expanded at all"
    assert added <= EVIDENCE_EXTRA_SOURCE_CHARS + sum(
        len(" — session date: 1 May 2023") + 1 for _ in hits)


def test_a_document_too_large_for_what_is_left_is_skipped_not_truncated(tmp_path):
    """A session cut mid-way is the ranked-but-unanswerable failure
    ``EVIDENCE_SOURCE_CHARS`` exists to prevent, so a document is admitted whole
    or not at all — and a smaller one further down still gets the remainder.

    Session 1 takes most of the budget, session 2 no longer fits in what is left,
    session 3 does. Skipping ends that hit's expansion, not the loop.
    """
    memory = _memory()
    budget = EVIDENCE_EXTRA_SOURCE_CHARS
    result = _padded(memory, tmp_path,
                     pad=[budget - budget // 4, budget // 2, 0])
    hits = [MabHit(text=f"c{n}", name=f"c{n}",
                   source_path=str(result.corpus_dir / document_name(n)))
            for n in (1, 2, 3)]
    first, skipped, last = memory.answer_evidence(hits)
    assert "session 1 first" in first, "the first document should fit"
    assert "session 2 first" not in skipped, "a document past the budget was pasted"
    assert "pad2" not in skipped, "the document was truncated instead of skipped"
    assert "session 3 first" in last, "the leftover budget bought nothing"


def test_every_evidence_item_carries_the_date_of_the_session_behind_it(tmp_path):
    """The date lives in the document header and nowhere else the model can see.

    Measured on the 45 wrong answers of the 2026-08-21 conv-26 run, 13 (28.9%) —
    the largest single class — answered a WHEN question with a relative
    expression ("Yesterday", "Last week") where the gold is a calendar date. The
    extraction keeps the speaker's deixis and drops the anchor: 1,120 of the
    1,124 unexpanded concept evidence items (99.6%) carried no date at all.
    """
    memory = _memory()
    result = memory.ingest(_conversation(sessions=2), work=tmp_path,
                           compile_project=False)
    hits = [MabHit(text="anchor", name=document_title(1),
                   source_path=str(result.corpus_dir / document_name(1))),
            MabHit(text="concept", name="concept",
                   source_path=str(result.corpus_dir / document_name(2)))]
    anchor, concept = memory.answer_evidence(hits)
    assert anchor.splitlines()[0] == "anchor — session date: 1 May 2023"
    assert concept.splitlines()[0] == "concept — session date: 2 May 2023"


def test_a_hit_from_outside_the_staging_root_is_neither_pasted_nor_dated(tmp_path):
    """``source_path`` is untrusted, and widening expansion must not widen it."""
    memory = _memory()
    memory.ingest(_conversation(sessions=1), work=tmp_path, compile_project=False)
    outside = tmp_path.parent / "elsewhere.md"
    outside.write_text("# Session 0001\n\nChat Time: 1:56 pm on 8 May, 2023\n\nsecret\n",
                       encoding="utf-8")
    hits = [MabHit(text="thief", name=document_title(1), source_path=str(outside))]
    assert memory.answer_evidence(hits) == ["thief"]


@pytest.mark.parametrize("stated, expected", [
    ("1:56 pm on 8 May, 2023", "8 May 2023"),          # the conv-26 spelling
    ("2:35 pm, 16 March 2023", "16 March 2023"),       # conv-30/41/42/47/50
    ("not recorded in this conversation.", ""),        # render_session's fallback
])
def test_session_date_reads_the_spellings_the_corpus_writes(stated, expected):
    """All 272 staged documents of ``locomo10.json`` parse under these two.

    Reading only the ``on`` spelling passes conv-26 perfectly and silently drops
    13 documents across five other conversations — the same near-miss the
    temporal read-side fix caught one commit ago. A year is REQUIRED, so the
    undated fallback stamps nothing rather than stamping a sentence.
    """
    assert session_date(f"# Session 0001\n\nChat Time: {stated}\n\nturn\n") == expected


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


# ------------------------------------------------------ the reranking stage


class _CountingSearch:
    """Records the ``top_k`` it was asked for, returns real ``ScoredNode``s."""

    def __init__(self, nodes: Sequence[Any]) -> None:
        self._nodes = list(nodes)
        self.top_k: List[int] = []

    def __call__(self, graph: Any, query: str, **kwargs: Any) -> Any:
        self.top_k.append(int(kwargs["top_k"]))
        scored = [
            ScoredNode(node=n, score=1.0 / (i + 1), per_lane={"bm25": 1.0},
                       ranks={"bm25": i + 1})
            for i, n in enumerate(self._nodes)
        ]
        return HybridSearchResult(query=query, mode="hybrid", backend="stub",
                                  weights={}, scored=scored[: kwargs["top_k"]],
                                  total_matches=len(scored))


class _LastWins:
    """A reranker that inverts whatever order it is handed."""

    name = "last-wins"

    def score(self, query: str, documents: Sequence[str]) -> List[float]:
        return [float(i) for i in range(len(documents))]


def _session_nodes(count: int) -> List[ResearchNode]:
    return [
        ResearchNode(
            id=f"Session:{n}",
            name=document_title(n),
            type=ResearchNodeType.SESSION,
            description=f"session {n}",
            source_path=f"corpus/{document_name(n)}",
        )
        for n in range(1, count + 1)
    ]


def test_without_a_reranker_the_lanes_are_asked_for_the_budget_exactly(tmp_path):
    """The shipped path must not silently start overfetching."""
    search = _CountingSearch(_session_nodes(3))
    memory = _memory(search_fn=search)
    _staged(tmp_path, memory, _conversation(sessions=3))
    memory.query_hits("q", k=2)
    assert search.top_k == [2]


def test_a_reranker_overfetches_and_returns_its_own_top_k(tmp_path):
    """The lanes become a candidate generator; the cross-encoder picks the k."""
    search = _CountingSearch(_session_nodes(8))
    memory = _memory(search_fn=search, reranker=_LastWins(), rerank_overfetch=4)
    _staged(tmp_path, memory, _conversation(sessions=8))
    hits = memory.query_hits("q", k=2)
    assert search.top_k == [8], "lanes must see k * overfetch, not k"
    assert len(hits) == 2, "the budget still bounds what the backbone reads"
    assert [h.name for h in hits] == [document_title(8), document_title(7)], (
        "the reranker's order must survive, not the fused order"
    )


def test_a_reranker_cannot_widen_the_budget(tmp_path):
    """Overfetch buys candidates to choose from, never more evidence."""
    search = _CountingSearch(_session_nodes(8))
    memory = _memory(search_fn=search, reranker=_LastWins(), rerank_overfetch=4)
    _staged(tmp_path, memory, _conversation(sessions=8))
    assert len(memory.search_documents("q", k=2)) == 2


def test_overfetch_below_one_is_refused(tmp_path):
    """Overfetch 0 would ask the lanes for nothing and rerank an empty set."""
    with pytest.raises(ValueError, match="rerank_overfetch"):
        _memory(reranker=_LastWins(), rerank_overfetch=0)

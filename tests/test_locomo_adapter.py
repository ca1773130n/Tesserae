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
from tesserae.research_graph import (
    ResearchEdge,
    ResearchGraph,
    ResearchNode,
    ResearchNodeType,
)
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


def test_the_harness_default_matches_the_library_default():
    """`RERANK_MAX_LENGTH` is a copy, kept out of an import that pulls torch."""
    from tesserae.retrieval.rerank import DEFAULT_MAX_LENGTH

    from evals.locomo.adapter import RERANK_MAX_LENGTH

    assert RERANK_MAX_LENGTH == DEFAULT_MAX_LENGTH


# -------------------------------------------------------- the fan-out stage


class _RecordingSearch(_CountingSearch):
    """``_CountingSearch`` that also records the fan-out kwargs it was handed."""

    def __init__(self, nodes: Sequence[Any]) -> None:
        super().__init__(nodes)
        self.kwargs: List[Dict[str, Any]] = []

    def __call__(self, graph: Any, query: str, **kwargs: Any) -> Any:
        self.kwargs.append(dict(kwargs))
        return super().__call__(graph, query, **kwargs)


def test_without_fanout_the_search_call_is_unchanged(tmp_path):
    """The opt-in contract at the harness boundary.

    Not one fan-out keyword reaches the search function on the shipped path, so
    a `search_fn` written before this stage existed still binds — and the real
    `hybrid_search`, which would raise `TypeError` on an unknown keyword, is
    still callable with exactly the arguments it always got.
    """
    search = _RecordingSearch(_session_nodes(3))
    memory = _memory(search_fn=search)
    _staged(tmp_path, memory, _conversation(sessions=3))
    memory.query_hits("q", k=2)

    assert search.top_k == [2]
    assert set(search.kwargs[0]) == {
        "top_k", "weights", "mode", "backend", "source_root",
        "document_first",  # the session is the unit of recall; see hybrid_search(document_first=...)
    }


def test_the_shipped_path_binds_hybrid_search_and_fanout_binds_fanout_search():
    from tesserae.retrieval.fanout import fanout_search
    from tesserae.retrieval.hybrid import hybrid_search

    assert LocomoMemory()._resolve_search() is hybrid_search
    assert LocomoMemory(fanout=True)._resolve_search() is fanout_search


def test_fanout_forwards_every_knob_it_owns(tmp_path):
    """Each knob is sweepable through `--memory-arg` only if it reaches here."""
    search = _RecordingSearch(_session_nodes(3))
    memory = _memory(search_fn=search, fanout=True, fanout_overfetch=3,
                     source_cap=2, ubiquity_df_ratio=0.4, extra_facets=1)
    _staged(tmp_path, memory, _conversation(sessions=3))
    memory.query_hits("q", k=2)

    assert search.top_k == [2], "the fan-out overfetches INSIDE the call, not here"
    assert search.kwargs[0]["overfetch"] == 3
    assert search.kwargs[0]["source_cap"] == 2
    assert search.kwargs[0]["ubiquity_df_ratio"] == 0.4
    assert search.kwargs[0]["extra_facets"] == 1


def test_a_reranker_moves_the_cap_downstream_of_itself(tmp_path):
    """`rerank_nodes` has no notion of documents and would undo the cap.

    So with both stages on the fan-out runs UNCAPPED and the cap is applied to
    what the cross-encoder produced. Untested against a real cross-encoder;
    the two are never both on in the sweep this was built for.
    """
    nodes = [
        ResearchNode(id=f"Node:{i}", name=f"node {i}",
                     type=ResearchNodeType.SESSION, description=f"n{i}",
                     source_path=f"corpus/{document_name(1 + i // 3)}")
        for i in range(6)
    ]
    search = _RecordingSearch(nodes)
    memory = _memory(search_fn=search, fanout=True, source_cap=1,
                     reranker=_LastWins(), rerank_overfetch=4)
    _staged(tmp_path, memory, _conversation(sessions=2))
    hits = memory.query_hits("q", k=2)

    assert search.kwargs[0]["source_cap"] is None, (
        "capping before the reranker lets it re-cluster one session"
    )
    # Two sessions, two hits, one each: the cap survived the reorder.
    assert len({h.source_path for h in hits}) == 2


def test_fanout_overfetch_below_one_is_refused():
    """Overfetch 0 would ask each sub-query for nothing."""
    with pytest.raises(ValueError, match="fanout_overfetch"):
        _memory(fanout=True, fanout_overfetch=0)


def test_prefer_anchor_text_rebuilds_a_hit_from_the_node_that_stands_for_it(tmp_path):
    """The repair `source_cap` requires, and what it is measured to prevent.

    With one hit per session the concept node that WON the slot carries a
    ~75-character summary, and `answer_evidence` only expands anchors
    unconditionally — so the document metric rises while the prompt starves
    (multi-hop gold-turn coverage 0.468 -> 0.420). Rebuilding the hit from the
    session's own anchor node is what puts the text back.
    """
    path = f"corpus/{document_name(1)}"
    concept = ResearchNode(id="Concept:pots", name="pottery",
                           type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
                           description="a short summary", source_path=path)
    anchor = ResearchNode(id="SourceDocument:1", name=document_title(1),
                          type=ResearchNodeType.SOURCE_DOCUMENT,
                          description="the session itself", source_path=path)

    class _Graph:
        nodes = [concept, anchor]

    search = _RecordingSearch([concept])
    memory = _memory(search_fn=search, fanout=True, prefer_anchor_text=True)
    memory.ingest(_conversation(sessions=1), work=tmp_path, compile_project=False)
    memory._graph = _Graph()

    assert [h.name for h in memory.query_hits("q", k=1)] == [document_title(1)]

    # ...and off, the retrieved node is the hit, exactly as before.
    plain = _memory(search_fn=_RecordingSearch([concept]))
    plain.ingest(_conversation(sessions=1), work=tmp_path, compile_project=False)
    plain._graph = _Graph()
    assert [h.name for h in plain.query_hits("q", k=1)] == ["pottery"]


def test_an_impostor_of_the_right_type_does_not_become_the_anchor(tmp_path):
    """A Project that was TALKED ABOUT in a session is not that session.

    `_SOURCE_ANCHOR_TYPES` matches 214 nodes of the compiled group-0 graph and
    only 111 are transcripts; the rest are things somebody mentioned, carrying
    the path of the chat that mentioned them. Picking one here rewrites every
    hit from that file into a node that then FAILS `is_document_anchor`, so it
    is not expanded unconditionally and starves in the shared budget — the
    exact failure `prefer_anchor_text` exists to repair.

    Measured on the type test, 33 of 272 pooled session files chose an
    impostor. conv-26 chose zero, which is why the sweep could not see it.
    """
    path = f"corpus/{document_name(1)}"
    concept = ResearchNode(id="Concept:pots", name="pottery",
                           type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
                           description="a short summary", source_path=path)
    # Anchor TYPE, wrong identity, and FIRST — so a type test picks it.
    impostor = ResearchNode(id="Project:styling", name="Fashion Styling Video",
                            type=ResearchNodeType.PROJECT,
                            description="something they discussed",
                            source_path=path)
    real = ResearchNode(id="SourceDocument:1", name=document_title(1),
                        type=ResearchNodeType.SOURCE_DOCUMENT,
                        description="the session itself", source_path=path)

    class _Graph:
        nodes = [impostor, real, concept]

    memory = _memory(search_fn=_RecordingSearch([concept]), fanout=True,
                     prefer_anchor_text=True)
    memory.ingest(_conversation(sessions=1), work=tmp_path, compile_project=False)
    memory._graph = _Graph()

    hits = memory.query_hits("q", k=1)
    assert [h.name for h in hits] == [document_title(1)], (
        "the node whose name IS the file's H1 must win over one that merely "
        "shares its type and came first"
    )
    assert hits[0].is_document_anchor, (
        "the chosen anchor must pass the SAME test answer_evidence applies, "
        "or the substitution buys nothing"
    )


def test_the_harness_source_cap_default_is_the_librarys_named_value():
    """1 here and None in the library, and neither is a magic number."""
    import inspect

    from tesserae.retrieval.fanout import DEFAULT_SOURCE_CAP, fanout_search

    assert LocomoMemory()._source_cap == DEFAULT_SOURCE_CAP == 1
    assert inspect.signature(fanout_search).parameters["source_cap"].default is None


# ------------------------------------------------- the tiered evidence budget


def _span(dia: str, path: str, *, key: str = "turn",
          node_id: str = "") -> ResearchNode:
    """An EvidenceSpan that POINTS at ``dia`` — empty description and all."""
    return ResearchNode(id=node_id or f"EvidenceSpan:{dia}",
                        name=f"{dia} evidence",
                        type=ResearchNodeType.EVIDENCE_SPAN,
                        description="", source_path=path,
                        metadata={key: dia, "speaker": "Ada"})


def _claim(node_id: str, path: str, description: str = "a claim") -> ResearchNode:
    return ResearchNode(id=node_id, name="a claim", type=ResearchNodeType.CLAIM,
                        description=description, source_path=path)


def _graph(nodes: Sequence[ResearchNode],
           edges: Sequence[tuple] = ()) -> ResearchGraph:
    return ResearchGraph(
        nodes=list(nodes),
        edges=[ResearchEdge(source=s, target=t, type=ty) for s, t, ty in edges],
    )


def _tiered(tmp_path, conversation, nodes, edges=(), **kwargs):
    """A tiered memory over ``conversation``, standing ``nodes``/``edges`` in
    for the compiled graph the search lane is stubbed out of anyway."""
    memory = _memory(tiered_evidence=True, **kwargs)
    result = memory.ingest(conversation, work=tmp_path, compile_project=False)
    memory._graph = _graph(nodes, edges)
    return memory, result


def test_tiered_evidence_off_is_byte_identical(tmp_path):
    """The opt-in guarantee, proved on the code rather than on a measurement.

    ``answer_evidence`` dispatches to ``_answer_evidence_sessions`` — today's
    body moved verbatim — unless the constructor was told otherwise, so the
    tiered branch is unreachable by default. The receipt index is replaced with
    a landmine here to say so out loud: if the shipped path ever consults it,
    this fails rather than quietly costing a graph walk per question.

    The expected strings are spelled out rather than compared against another
    run of the same code, because "equal to itself" is not the claim.
    """
    memory = _memory()
    result = memory.ingest(_conversation(sessions=2), work=tmp_path,
                           compile_project=False)

    def _landmine():
        raise AssertionError("the receipt tier ran on the shipped path")

    memory._receipt_index = _landmine

    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2)]
    hits = [MabHit(text="anchor", name=document_title(1), source_path=paths[0],
                   node_id="SourceDocument:1"),
            MabHit(text="a claim", name="a claim", source_path=paths[1],
                   node_id="Claim:c")]
    body = [Path(p).read_text(encoding="utf-8") for p in paths]

    assert memory.answer_evidence(hits) == [
        f"anchor — session date: 1 May 2023\n{body[0]}",
        f"a claim — session date: 2 May 2023\n{body[1]}",
    ]
    assert memory.answer_evidence(hits, expand=False) == ["anchor", "a claim"]
    assert memory.receipt_chars == memory.receipt_lines == 0


def test_a_zero_receipt_budget_reproduces_the_session_paste_exactly(tmp_path):
    """The kill control, and it is exact.

    With no receipt budget the tiered path emits the same bytes the shipped one
    does. That is what separates the tier's contribution from the path strip
    that pays for it: pooled, this setting reproduces multi-hop 0.465 / overall
    0.790 — the shipped numbers — while the default reaches 0.511 / 0.822.
    """
    conversation = _conversation(sessions=2)
    shipped = _memory()
    result = shipped.ingest(conversation, work=tmp_path, compile_project=False)
    path = str(result.corpus_dir / document_name(1))
    hits = [MabHit(text="a claim", name="a claim", source_path=path,
                   node_id="Claim:c")]

    span = _span("D1:1", path)
    memory, _ = _tiered(tmp_path, conversation,
                        [_claim("Claim:c", path), span],
                        [("Claim:c", span.id, "evidenced_by")],
                        evidence_receipt_chars=0)
    assert memory.answer_evidence(hits) == shipped.answer_evidence(hits)


def test_tiered_evidence_is_a_superset_of_the_session_paste(tmp_path):
    """Every document the shipped rule pastes is still pasted.

    Tier 3 is the shipped selection rule byte for byte and it runs FIRST, so
    tiering can only add. A design that traded session text for receipts would
    be a different change with a different risk, and the numbers behind this one
    were measured on the additive shape.
    """
    conversation = _conversation(sessions=2)
    shipped = _memory()
    result = shipped.ingest(conversation, work=tmp_path, compile_project=False)
    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2)]
    hits = [MabHit(text="anchor", name=document_title(1), source_path=paths[0],
                   node_id="SourceDocument:1"),
            MabHit(text="a claim", name="a claim", source_path=paths[1],
                   node_id="Claim:c")]

    span = _span("D1:2", paths[0])
    memory, _ = _tiered(tmp_path, conversation,
                        [_claim("Claim:c", paths[1]), span],
                        [("Claim:c", span.id, "evidenced_by")])
    for plain, tiered in zip(shipped.answer_evidence(hits),
                             memory.answer_evidence(hits)):
        head = plain.splitlines()[0]
        assert tiered.splitlines()[0] == head, "the head line changed"
        pasted = plain[len(head):].lstrip("\n")
        if pasted:
            assert pasted in tiered, "a document the shipped rule pasted is gone"


def test_a_receipt_inside_a_pasted_session_is_not_emitted_twice(tmp_path):
    """Tier 3 is SELECTED before tier 2 spends, so it never buys a turn twice.

    This ordering is what keeps measured tier-2 spend at 477 characters instead
    of thousands: on this corpus tier 3 already pastes 4.44 of ~19 sessions, and
    every turn inside them is free.
    """
    conversation = _conversation(sessions=2)
    memory, result = _tiered(tmp_path, conversation, [], [])
    path = str(result.corpus_dir / document_name(1))
    span = _span("D1:1", path)
    memory._graph = _graph([_claim("Claim:c", path), span],
                           [("Claim:c", span.id, "evidenced_by")])

    hits = [MabHit(text="anchor", name=document_title(1), source_path=path,
                   node_id="SourceDocument:1"),
            MabHit(text="a claim", name="a claim", source_path=path,
                   node_id="Claim:c")]
    prompt = "\n\n".join(memory.answer_evidence(hits))
    assert prompt.count("[D1:1]") == 1, "the pasted session's turn was bought again"
    assert memory.receipt_lines == 0


def test_the_turn_id_is_found_under_a_key_the_compile_invented(tmp_path):
    """The locator is matched by VALUE SHAPE, never by key name.

    The extraction wrote the turn id under 14 different keys across the ten
    compiled graphs. ``metadata["turn"]`` resolves 1,352 of 1,668 spans (81.1%)
    and reports the other 242 as having no receipt at all — a silent 19% loss
    that would read as a graph defect rather than a reader defect.
    """
    memory = _memory(tiered_evidence=True)
    result = _padded(memory, tmp_path, pad=[0, 0])
    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2)]
    span = _span("D1:3", paths[0], key="message_id")
    memory._graph = _graph([_claim("Claim:c", paths[1]), span],
                           [("Claim:c", span.id, "evidenced_by")])

    hits = [MabHit(text="a claim", name="a claim", source_path=paths[1],
                   node_id="Claim:c")]
    (item,) = memory.answer_evidence(hits)
    assert "[D1:3]" in item
    assert memory.unresolvable_spans == 0
    assert memory.witness_yield == 1.0


def test_a_compound_locator_goes_through_parse_dia_ids(tmp_path):
    """46 of 1,580 turn values are ranges or lists, in five delimiter spellings.

    ``dataset.parse_dia_ids`` is the ANSWER KEY's own parser and already
    resolves them; a second parser here would be duplication and a place for a
    silent loss to hide.
    """
    conversation = _conversation(sessions=2)
    memory, result = _tiered(tmp_path, conversation, [], [])
    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2)]
    span = _span("D1:1,D1:2", paths[0])
    memory._graph = _graph([_claim("Claim:c", paths[1]), span],
                           [("Claim:c", span.id, "evidenced_by")])

    hits = [MabHit(text="a claim", name="a claim", source_path=paths[1],
                   node_id="Claim:c")]
    (item,) = memory.answer_evidence(hits)
    assert "[D1:1]" in item and "[D1:2]" in item


def test_a_reversed_evidenced_by_edge_still_carries_the_receipt(tmp_path):
    """`evidenced_by` is emitted in BOTH directions, so the walk is undirected.

    conv-26 alone carries 6 ``EvidenceSpan -evidenced_by-> Event`` edges beside
    its 48 forward ones. A directed walk drops 6-7 receipts per graph and
    reports the facts behind them as unwitnessed.
    """
    conversation = _conversation(sessions=2)
    memory, result = _tiered(tmp_path, conversation, [], [])
    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2)]
    span = _span("D1:1", paths[0])
    event = ResearchNode(id="Event:e", name="an event",
                         type=ResearchNodeType.EVENT, description="it happened",
                         source_path=paths[1])
    # The span is the SOURCE of the edge and the fact is the target.
    memory._graph = _graph([event, span], [(span.id, "Event:e", "evidenced_by")])

    hits = [MabHit(text="an event", name="an event", source_path=paths[1],
                   node_id="Event:e")]
    (item,) = memory.answer_evidence(hits)
    assert "[D1:1]" in item


def test_a_span_whose_turn_is_not_on_disk_is_not_a_receipt(tmp_path):
    """Reachability is not provenance. The turn has to actually be staged."""
    conversation = _conversation(sessions=2)
    memory, result = _tiered(tmp_path, conversation, [], [])
    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2)]
    span = _span("D9:9", paths[0])
    memory._graph = _graph([_claim("Claim:c", paths[1]), span],
                           [("Claim:c", span.id, "evidenced_by")])

    hits = [MabHit(text="a claim", name="a claim", source_path=paths[1],
                   node_id="Claim:c")]
    (item,) = memory.answer_evidence(hits)
    assert "[D9:9]" not in item
    assert memory.dangling_receipts == 1
    assert memory.unresolvable_spans == 0, "it named a turn; the turn is absent"
    assert memory.witness_yield == 0.0


def test_a_receipt_whose_source_path_escapes_the_staging_root_is_dropped(tmp_path):
    """``source_path`` is untrusted frontmatter, and this is a second door.

    ``answer_evidence`` already refuses to paste a file outside the staging
    root; a receipt reads through the same ``_confined_source`` guard, so the
    turn tier cannot become the way a stolen file reaches a prompt one line at
    a time.
    """
    conversation = _conversation(sessions=2)
    memory, result = _tiered(tmp_path, conversation, [], [])
    outside = tmp_path.parent / "stolen.md"
    outside.write_text('[D1:1] Ada said, "a secret"\n', encoding="utf-8")
    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2)]
    span = _span("D1:1", str(outside))
    memory._graph = _graph([_claim("Claim:c", paths[1]), span],
                           [("Claim:c", span.id, "evidenced_by")])

    hits = [MabHit(text="a claim", name="a claim", source_path=paths[1],
                   node_id="Claim:c")]
    (item,) = memory.answer_evidence(hits)
    assert "a secret" not in item
    assert memory.dangling_receipts == 1


def test_documents_of_is_unchanged_under_tiering(tmp_path):
    """`search_k` does not move, so the retrieval score cannot.

    Tiering buys coverage out of the SAME ten hits rather than a deeper slice.
    The branch audit already falsified the document proxy once — ALL-gold-doc@10
    rose while pooled turn coverage fell — so this metric is kept as a control,
    and a control that moved with the change would not be one.
    """
    nodes = [ResearchNode(id=f"SourceDocument:{n}", name=document_title(n),
                          type=ResearchNodeType.SOURCE_DOCUMENT,
                          description=f"session {n}",
                          source_path=f"corpus/{document_name(n)}")
             for n in (2, 1)]

    def _run(**kwargs):
        search = _RecordingSearch(nodes)
        memory = _memory(search_fn=search, **kwargs)
        _staged(tmp_path, memory, _conversation(sessions=2))
        memory._graph = _graph(nodes)
        return search, memory.documents_of(memory.query_hits("q", k=2))

    plain_search, plain = _run()
    tiered_search, tiered = _run(tiered_evidence=True)
    assert plain == tiered == [2, 1]
    assert plain_search.top_k == tiered_search.top_k == [2]


def test_the_fact_head_drops_the_absolute_path_the_backbone_cannot_open(tmp_path):
    """37% of every shipped head is ``" — source: /Users/.../session-00NN.md"``.

    990 characters of every 10-hit prompt, naming a file nothing downstream can
    read. Dropping it is free — pooled, coverage is unchanged to three decimals
    — and it is what pays for the receipt tier.
    """
    node = _claim("Claim:c", f"corpus/{document_name(1)}", "a short summary")
    search = _RecordingSearch([node])
    memory = _memory(search_fn=search, tiered_evidence=True)
    _staged(tmp_path, memory, _conversation(sessions=1))
    (hit,) = memory.query_hits("q", k=1)
    assert hit.text == "a claim — a short summary"
    assert hit.node_id == "Claim:c"

    plain = _memory(search_fn=_RecordingSearch([node]))
    _staged(tmp_path, plain, _conversation(sessions=1))
    (shipped,) = plain.query_hits("q", k=1)
    assert shipped.text.endswith(f" — source: corpus/{document_name(1)}")
    assert shipped.node_id == "Claim:c", "carried on both paths, read on one"


def test_receipt_window_zero_emits_exactly_the_named_turn(tmp_path):
    """The default emits the turn and nothing around it.

    The window ships OFF because it is unmeasured in this frame: one design
    measured it worth +0.139 overall coverage, another measured it bit-for-bit
    inert, and the two differ in whether their fill had already pasted the
    neighbours. The knob works — the second half of this test is the proof —
    and it stays at 0 until a measurement in THIS frame says otherwise.
    """
    memory = _memory(tiered_evidence=True)
    result = _padded(memory, tmp_path, pad=[0, 0])
    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2)]
    span = _span("D1:2", paths[0])
    nodes = [_claim("Claim:c", paths[1]), span]
    edges = [("Claim:c", span.id, "evidenced_by")]
    memory._graph = _graph(nodes, edges)
    hits = [MabHit(text="a claim", name="a claim", source_path=paths[1],
                   node_id="Claim:c")]

    (item,) = memory.answer_evidence(hits)
    assert "[D1:2]" in item
    assert "[D1:1]" not in item and "[D1:3]" not in item

    windowed = _memory(tiered_evidence=True, receipt_window=1)
    _padded(windowed, tmp_path, pad=[0, 0])
    windowed._graph = _graph(nodes, edges)
    (item,) = windowed.answer_evidence(hits)
    assert all(f"[D1:{t}]" in item for t in (1, 2, 3))


def test_the_receipt_budget_skips_a_line_it_cannot_afford_without_ending(tmp_path):
    """SKIP, not break — ``answer_evidence``'s own idiom for a unit that does
    not fit, so leftover budget still buys a cheaper line further down."""
    memory = _memory(tiered_evidence=True)
    # Session 1's third turn is padded far past the budget; session 2's is not.
    result = _padded(memory, tmp_path, pad=[600, 0])
    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2)]
    expensive = _span("D1:3", paths[0], node_id="EvidenceSpan:expensive")
    cheap = _span("D2:3", paths[1], node_id="EvidenceSpan:cheap")
    memory._graph = _graph(
        [_claim("Claim:a", ""), _claim("Claim:b", ""), expensive, cheap],
        [("Claim:a", expensive.id, "evidenced_by"),
         ("Claim:b", cheap.id, "evidenced_by")],
    )
    memory._evidence_receipt_chars = (
        len(memory._turn_lines(paths[1])[1]["D2:3"][1]) + 1)

    # Rank 0's receipt does not fit; rank 1's does, and the loop reached it.
    first, second = memory.answer_evidence([
        MabHit(text="a", name="a", source_path="", node_id="Claim:a"),
        MabHit(text="b", name="b", source_path="", node_id="Claim:b"),
    ])
    assert "[D1:3]" not in first, "a line past the budget was emitted"
    assert "[D2:3]" in second, "the loop ended instead of skipping"
    assert memory.receipt_lines == 1


def test_a_turn_that_spans_lines_is_not_truncated_at_the_newline(tmp_path):
    """A receipt is the whole turn, and LoCoMo turns contain newlines.

    Keyed on the first physical line, 13 of the 272 staged documents' receipts
    were pasted cut off at the first newline. conv-49 session-0021 D21:19
    rendered as `[D21:19] Sam said, "` — zero content and an unterminated
    quote, which is worse than no receipt at all because it reads as evidence.
    The coverage instrument could not catch it: it matches the `[D<n>:<t>]`
    marker, and the marker survives the truncation that removes the words.
    """
    corpus = tmp_path / "conv-test" / "corpus"
    corpus.mkdir(parents=True)
    doc = corpus / document_name(1)
    doc.write_text(
        "# Session 0001\n\n"
        '[D1:1] Ada said, "first line\nsecond line\nthird line"\n'
        '[D1:2] Bo said, "single"\n',
        encoding="utf-8",
    )
    memory = _memory()
    memory.work = tmp_path
    ordered, by_id = memory._turn_lines(str(doc))

    assert ordered == ["D1:1", "D1:2"], "file order must survive the merge"
    _, text = by_id["D1:1"]
    assert "second line" in text and "third line" in text, (
        "the continuation lines are the evidence; truncating them pastes a "
        "quote with no content in it"
    )
    assert "[D1:2]" not in text, "a turn must stop at the next turn's header"
    assert by_id["D1:2"][1].strip() == '[D1:2] Bo said, "single"'


# ------------------------------------------------------- the turn pack budget


def _packed(tmp_path, conversation, **kwargs):
    """A turn-unit memory over ``conversation``, ingested but not compiled.

    ``bm25_ctx`` alone by default: the dense lane would resolve a real
    embedding backend, and these cases run on an install with no model, no
    torch and no network. The lanes are exercised separately by
    ``test_the_dense_lane_reads_the_backend_and_a_zero_weight_skips_it``.
    """
    kwargs.setdefault("turn_weights", {"bm25_ctx": 1.0})
    memory = _memory(evidence_unit="turn", **kwargs)
    result = memory.ingest(conversation, work=tmp_path, compile_project=False)
    return memory, result


def _hit(path: str, text: str = "a claim", name: str = "a claim",
         node_id: str = "") -> MabHit:
    return MabHit(text=text, name=name, source_path=path, node_id=node_id)


def test_the_turn_unit_off_is_byte_identical(tmp_path):
    """The opt-in guarantee, proved on the code rather than on a measurement.

    ``answer_evidence`` reaches ``_answer_evidence_sessions`` — today's body
    moved verbatim — unless the constructor was told otherwise, so the turn
    branch is unreachable by default. Both new branches are replaced with
    landmines here to say so out loud, and the expected strings are spelled out
    rather than compared against another run of the same code, because "equal
    to itself" is not the claim.
    """
    memory = _memory()

    def _landmine(*_args, **_kwargs):
        raise AssertionError("the turn pack ran on the shipped path")

    memory._answer_evidence_turns = _landmine
    memory._turn_pool_paths = _landmine
    result = memory.ingest(_conversation(sessions=2), work=tmp_path,
                           compile_project=False)

    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2)]
    hits = [MabHit(text="anchor", name=document_title(1), source_path=paths[0]),
            MabHit(text="a claim", name="a claim", source_path=paths[1])]
    body = [Path(p).read_text(encoding="utf-8") for p in paths]

    # The question is threaded to EVERY unit now. The session units must ignore
    # it: a run whose prompt moved because a parameter was added is not the
    # same run.
    assert memory.answer_evidence(hits, question="anything at all") == [
        f"anchor — session date: 1 May 2023\n{body[0]}",
        f"a claim — session date: 2 May 2023\n{body[1]}",
    ]
    assert memory.answer_evidence(hits) == memory.answer_evidence(
        hits, question="anything at all")
    assert memory.pack_chars == memory.pack_turns == memory.pack_sessions == 0


def test_the_turn_unit_and_the_tiered_one_refuse_each_other():
    """Tier 2 IS a degenerate turn pack; running both spends two budgets."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        _memory(evidence_unit="turn", tiered_evidence=True)


@pytest.mark.parametrize("kwargs", [
    {"evidence_unit": "paragraph"},
    {"evidence_unit": "turn", "turn_pool": "everything"},
    {"evidence_unit": "turn", "turn_heads": "summary"},
    {"evidence_unit": "turn", "evidence_pack_chars": -1},
    {"evidence_unit": "turn", "turn_emit_window": -1},
    {"evidence_unit": "turn", "turn_score_window": -1},
    {"evidence_unit": "turn", "turn_weights": {"bm25": 1.0}},
    # Every lane off is not a control, it is a pack chosen by tie-break alone.
    {"evidence_unit": "turn", "turn_weights": {}},
    {"evidence_unit": "turn", "turn_weights": {"bm25_ctx": 0.0}},
])
def test_an_unreadable_pack_setting_is_refused_at_construction(kwargs):
    """A misspelled lane is the invisible failure: a sweep would otherwise
    report a three-lane result with a lane silently off and never say so."""
    with pytest.raises(ValueError):
        _memory(**kwargs)


def test_a_turn_pack_without_a_question_is_refused(tmp_path):
    """The lexical lane is uniformly zero without one, so the pack would be
    chosen by session rank alone — a plausible prompt built from nothing the
    question said."""
    memory, result = _packed(tmp_path, _conversation(sessions=2))
    hits = [_hit(str(result.corpus_dir / document_name(1)))]
    with pytest.raises(ValueError, match="needs it"):
        memory.answer_evidence(hits)
    # ...and the summary control still short-circuits before the check, because
    # it assembles nothing at all.
    assert memory.answer_evidence(hits, expand=False) == ["a claim"]


def test_the_pack_never_exceeds_its_budget_and_the_cost_model_is_exact(tmp_path):
    """The cap is on the number ``run.py`` records, not on an internal proxy.

    An item is ``header + "\n" + "\n".join(lines)``, so the first line of a
    session costs its header and a newline too. Sweeping the budget one
    character at a time is what proves the arithmetic in ``spend()`` and the
    arithmetic in the renderer are the same arithmetic — a drift between them
    would report coverage bought with characters the arm was not allowed to
    spend.
    """
    conversation = _conversation(sessions=3)
    for cap in range(0, 420, 7):
        memory, result = _packed(tmp_path, conversation,
                                 evidence_pack_chars=cap)
        hits = [_hit(str(result.corpus_dir / document_name(n)))
                for n in (1, 2, 3)]
        items = memory.answer_evidence(hits, question="session 2 second")
        spent = sum(len(item) for item in items)
        assert spent <= cap, f"cap {cap} overrun by {spent - cap}"
        assert memory.pack_chars == spent


def test_a_budget_of_zero_packs_nothing_rather_than_one_turn(tmp_path):
    """SKIP, not break, and it bottoms out honestly: no budget, no prompt."""
    memory, result = _packed(tmp_path, _conversation(sessions=2),
                             evidence_pack_chars=0)
    assert memory.answer_evidence([_hit(str(result.corpus_dir / document_name(1)))],
                                  question="first") == []


def test_the_pack_keeps_the_turn_the_question_names(tmp_path):
    """The point of the unit. A 43,413-character prompt whose gold turns are
    318 characters is 0.73% signal; the packer's whole job is to raise that."""
    memory, result = _packed(tmp_path, _conversation(sessions=3),
                             evidence_pack_chars=140,
                             turn_score_window=0)
    hits = [_hit(str(result.corpus_dir / document_name(n))) for n in (1, 2, 3)]
    prompt = "\n\n".join(memory.answer_evidence(hits,
                                                question="session 3 second"))
    assert "[D3:2]" in prompt, "the turn the question names was not admitted"
    assert "[D1:1]" not in prompt, "a budget this small bought an unrelated turn"


def test_the_retrieved_pool_holds_the_retrieved_sessions_and_no_others(tmp_path):
    """Levy-safe BY CONSTRUCTION: the pack cannot name a session the ranking
    did not. That is what costs it 0.013 turn coverage against the wide pool
    and what buys it the same distinct-session count as the shipped arm."""
    conversation = _conversation(sessions=4)
    memory, result = _packed(tmp_path, conversation)
    hits = [_hit(str(result.corpus_dir / document_name(n))) for n in (1, 3)]
    items = memory.answer_evidence(hits, question="session 3 second chatter")
    prompt = "\n\n".join(items)
    assert "[D2:" not in prompt and "[D4:" not in prompt
    assert memory.pack_sessions == len(items) <= 2


def test_the_corpus_pool_is_a_strict_superset_of_the_retrieved_one(tmp_path):
    """The second ARM, not a better default — and on a 19-session corpus it is
    indistinguishable from reading everything, which any writeup quoting it has
    to say. De-duplication is by session NUMBER, so a hit's frontmatter
    spelling and the glob's filesystem spelling cannot pool one session twice.
    """
    conversation = _conversation(sessions=4)
    narrow, result = _packed(tmp_path, conversation)
    hits = [_hit(str(result.corpus_dir / document_name(1)))]
    assert narrow._turn_pool_paths(hits) == [str(result.corpus_dir /
                                                 document_name(1))]

    wide, _ = _packed(tmp_path, conversation, turn_pool="corpus")
    pooled = wide._turn_pool_paths(hits)
    assert pooled[0] == str(result.corpus_dir / document_name(1))
    assert len(pooled) == 4 and len(set(pooled)) == 4


# ------------------------------------------ retrieve-wide / pack-narrow


def test_both_pack_knobs_are_off_and_the_pack_is_byte_identical(tmp_path):
    """The opt-in guarantee, proved on the code rather than on a measurement.

    With ``turn_pool_k=0`` the pool is exactly the documents the answer-time
    hits named and NO second retrieval happens at all — a landmine on
    ``query_hits`` says so out loud, because a widening that ran silently would
    make every turn-unit number on this branch describe a different pool. With
    ``turn_session_cap=0`` admission is uncapped, as it has always been.
    """
    conversation = _conversation(sessions=4)
    memory, result = _packed(tmp_path, conversation)
    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 3)]
    hits = [_hit(p) for p in paths]
    expected = memory.answer_evidence(hits, question="session 3 second")

    guarded, _ = _packed(tmp_path, conversation)

    def _landmine(*_args, **_kwargs):
        raise AssertionError("the pool widened on the shipped path")

    guarded.query_hits = _landmine
    assert guarded.answer_evidence(hits, question="session 3 second") == expected
    assert guarded._turn_pool_paths(hits, "session 3 second") == paths


@pytest.mark.parametrize("kwargs", [
    {"evidence_unit": "turn", "turn_pool_k": -1},
    {"evidence_unit": "turn", "turn_session_cap": -1},
])
def test_a_negative_pack_knob_is_refused_at_construction(kwargs):
    with pytest.raises(ValueError):
        _memory(**kwargs)


def _wide(tmp_path, conversation, **kwargs):
    """A turn-unit memory whose session stage returns every staged session.

    The stub stands in for the ranking, so what these cases exercise is that
    the widening goes through ``query_hits`` at a stated ``k`` — a retrieval —
    rather than through a filesystem glob.
    """
    seen: List[Dict[str, Any]] = []
    corpus = tmp_path / "conv-test" / "corpus"

    def _search(graph, query, **kw):
        seen.append(dict(kw))
        return _Result([_Node(document_title(n),
                              source_path=str(corpus / document_name(n)))
                        for n in range(1, len(conversation.sessions) + 1)])

    memory, result = _packed(tmp_path, conversation, search_fn=_search, **kwargs)
    memory._graph = object()
    return memory, result, seen


def test_the_widened_pool_is_a_retrieval_at_a_stated_k_and_never_a_glob(tmp_path):
    """A pool bought by globbing the corpus does not transfer to any corpus
    where the pool cannot be everything — the adapter's own ``turn_pool``
    docstring says so about ``"corpus"``, and that glob is the measurement
    stand-in, not the implementation. The widening must therefore be a search
    with a declared budget, and this pins that it is.
    """
    conversation = _conversation(sessions=4)
    memory, result, seen = _wide(tmp_path, conversation, turn_pool_k=3)
    named = str(result.corpus_dir / document_name(1))

    pooled = memory._turn_pool_paths([_hit(named)], "session 3 second")

    assert seen and seen[-1]["top_k"] == 3, (
        "the pool was not asked for at a stated k"
    )
    assert pooled[0] == named, "the answer-time hits must stay first, in rank order"
    # k IS A BUDGET, and the difference from a glob is visible right here: the
    # conversation has four sessions and k=3 pools three of them. A glob would
    # have pooled all four however small k was, which is what makes a coverage
    # number bought that way untransferable.
    assert len(pooled) == len(set(pooled)) == 3
    # ...and the shortfall ledger is untouched: a pool query asks for more
    # sessions than the conversation has BY DESIGN, and ledgering it would
    # drown the shortfalls that mean the ANSWERING budget went unfilled.
    assert memory.shortfalls == []


def test_the_widened_pool_puts_turns_in_the_prompt_the_hits_never_named(tmp_path):
    """The 14.7% this exists for: gold turns that never became candidates."""
    conversation = _conversation(sessions=4)
    narrow, result = _packed(tmp_path, conversation)
    named = str(result.corpus_dir / document_name(1))
    assert "[D3:" not in "\n\n".join(
        narrow.answer_evidence([_hit(named)], question="session 3 second"))

    wide, _, _ = _wide(tmp_path, conversation, turn_pool_k=4)
    assert "[D3:" in "\n\n".join(
        wide.answer_evidence([_hit(named)], question="session 3 second"))


def test_the_pool_order_is_deterministic_under_shuffled_hit_order(tmp_path):
    """This repository has been bitten four times by iteration order reaching an
    artifact. The pool is rank order then the widening's own order, and the
    widening is a deterministic search — so the same question packs the same
    prompt however the hits arrive."""
    conversation = _conversation(sessions=4)
    memory, result, _ = _wide(tmp_path, conversation, turn_pool_k=4)
    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2)]

    forward = memory._turn_pool_paths([_hit(p) for p in paths], "q")
    again = memory._turn_pool_paths([_hit(p) for p in paths], "q")
    assert forward == again
    reversed_hits = memory._turn_pool_paths(
        [_hit(p) for p in reversed(paths)], "q")
    # The hits' own rank order is respected — that is what a ranking IS — and
    # everything the widening adds follows it in the search's order.
    assert reversed_hits[:2] == list(reversed(paths))
    assert sorted(reversed_hits) == sorted(forward)


def test_the_session_cap_is_never_exceeded(tmp_path):
    """The distinct-document count is the number Levy's distractor penalty is
    argued on, so it is a hard cap and not a preference."""
    conversation = _conversation(sessions=4)
    for cap in (1, 2, 3):
        memory, result, _ = _wide(tmp_path, conversation, turn_pool_k=4,
                                  turn_session_cap=cap)
        items = memory.answer_evidence(
            [_hit(str(result.corpus_dir / document_name(1)))],
            question="session 3 second")
        assert len(items) <= cap
        assert memory.pack_sessions <= cap


def test_receipts_spend_the_cap_rather_than_being_exempt_from_it(tmp_path):
    """Receipts are force-admitted AHEAD of the score, so an exemption would let
    them open every block the cap exists to refuse — and the cap would be a
    preference wearing a hard-cap's name. They land in their fact's own session
    99.0% of the time, so this costs almost nothing."""
    conversation = _conversation(sessions=4)
    memory, result = _packed(tmp_path, conversation, turn_session_cap=2)
    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2, 3)]
    spans = [_span(f"D{n}:1", path, node_id=f"EvidenceSpan:D{n}:1")
             for n, path in enumerate(paths, start=1)]
    memory._graph = _graph(
        [_claim(f"Claim:c{n}", path) for n, path in enumerate(paths, start=1)]
        + spans,
        [(f"Claim:c{n}", spans[n - 1].id, "evidenced_by")
         for n in (1, 2, 3)])

    items = memory.answer_evidence(
        [_hit(path, node_id=f"Claim:c{n}")
         for n, path in enumerate(paths, start=1)],
        question="session 1 first")

    assert len(items) == 2, "a receipt opened a block past the cap"
    assert memory.receipt_lines == 2


def test_the_cap_still_admits_turns_into_an_already_open_session(tmp_path):
    """The cap counts BLOCKS, not turns. A turn in a block that is already open
    is free of it — which is exactly why cap16 admits 6.1 fewer turns than the
    narrow pool rather than starving."""
    conversation = _conversation(sessions=4)
    memory, result, _ = _wide(tmp_path, conversation, turn_pool_k=4,
                              turn_session_cap=1)
    items = memory.answer_evidence(
        [_hit(str(result.corpus_dir / document_name(2)))],
        question="session 2 first second")
    assert len(items) == 1
    assert "[D2:1]" in items[0] and "[D2:2]" in items[0]


def test_the_budget_assertion_still_fires_with_both_knobs_on(tmp_path):
    """The whole claim of this design is a number: the pack must still be a cap
    on what ``run.py`` records, header-for-header, however wide the pool is."""
    conversation = _conversation(sessions=4)
    for budget in (60, 140, 400):
        memory, result, _ = _wide(tmp_path, conversation, turn_pool_k=4,
                                  turn_session_cap=3,
                                  evidence_pack_chars=budget)
        items = memory.answer_evidence(
            [_hit(str(result.corpus_dir / document_name(1)))],
            question="session 3 second")
        assert sum(len(item) for item in items) <= budget
        assert memory.pack_chars <= budget


def test_sessions_render_in_session_order_and_turns_in_file_order(tmp_path):
    """SESSION order, unlike every other unit here, and the reason is the pack.

    Whole sessions can be pasted in rank order because each one is internally
    chronological. A pack is fragments, and a reader resolving "yesterday" in
    one turn against a date in another needs them in the order they happened —
    rank order would interleave three months at random.
    """
    conversation = _conversation(sessions=3)
    memory, result = _packed(tmp_path, conversation)
    # Ranked 3, 1, 2 — deliberately not the session order.
    hits = [_hit(str(result.corpus_dir / document_name(n))) for n in (3, 1, 2)]
    items = memory.answer_evidence(hits, question="session first second")
    assert [item.splitlines()[0] for item in items] == [
        f"## {document_title(n)} — {n} May 2023" for n in (1, 2, 3)]
    for item in items:
        turns = [line for line in item.splitlines() if line.startswith("[")]
        assert turns == sorted(turns), "a session's turns left file order"


def test_the_session_date_is_paid_for_once_per_session_not_once_per_hit(tmp_path):
    """The stamp moves into the header. Same information, 390 characters
    against the 298 of per-item stamps plus 530 of in-file ``Chat Time``
    headers — and in the one place a reader will look for it."""
    memory, result = _packed(tmp_path, _conversation(sessions=2))
    hits = [_hit(str(result.corpus_dir / document_name(1)), node_id=""),
            _hit(str(result.corpus_dir / document_name(1)), name="another")]
    items = memory.answer_evidence(hits, question="session 1 first")
    assert len(items) == 1
    assert items[0].count("1 May 2023") == 1
    assert "session date:" not in items[0]
    assert "Chat Time:" not in items[0]


def test_the_score_window_sees_neighbours_the_emission_does_not(tmp_path):
    """The one non-obvious knob, and it is measured in BOTH directions.

    Scoring with the +/-1 neighbourhood is worth +0.023 turn coverage at a
    fixed budget; EMITTING it costs 0.015, because the neighbours eat slots.
    So a turn whose own words say nothing must still be reachable through its
    neighbour's — and must arrive alone.
    """
    conversation = _conversation(sessions=2)
    hits = None

    # "bicycle" is only ever the CAPTION on turn 2, so turn 1's OWN words say
    # nothing about it and it is reachable only through the window.
    memory, result = _packed(tmp_path, conversation,
                             evidence_pack_chars=100, turn_score_window=1)
    hits = [_hit(str(result.corpus_dir / document_name(n))) for n in (1, 2)]
    windowed = "\n\n".join(memory.answer_evidence(hits, question="bicycle"))
    assert "[D1:1]" in windowed, "the window never reached the neighbour's words"
    assert "bicycle" not in windowed, (
        "the neighbour was EMITTED, not merely scored — that is the setting "
        "measured to cost 0.015 coverage at a fixed budget"
    )
    assert windowed.count("[D") == 1

    # Blind to its neighbours, only the turn that actually carries the word
    # scores, so the pack keeps that one instead.
    blind, _ = _packed(tmp_path, conversation, evidence_pack_chars=100,
                       turn_score_window=0)
    bare = "\n\n".join(blind.answer_evidence(hits, question="bicycle"))
    assert "[D1:2]" in bare and "bicycle" in bare
    assert "[D1:1]" not in bare


def test_the_emit_window_brings_neighbours_and_they_cost_budget(tmp_path):
    """Off by default because it is measured negative — but when it is asked
    for it must actually arrive, and out of the SAME budget."""
    conversation = _conversation(sessions=1)
    memory, result = _packed(tmp_path, conversation, turn_emit_window=1,
                             turn_score_window=0)
    hits = [_hit(str(result.corpus_dir / document_name(1)))]
    prompt = "\n\n".join(memory.answer_evidence(hits, question="session 1 second"))
    assert "[D1:1]" in prompt and "[D1:2]" in prompt

    # ...and a budget that fits the named turn alone still refuses to overrun.
    tight, _ = _packed(tmp_path, conversation, turn_emit_window=1,
                       turn_score_window=0, evidence_pack_chars=90)
    items = tight.answer_evidence(hits, question="session 1 second")
    assert sum(len(item) for item in items) <= 90


def test_a_turn_pack_is_deterministic_under_repetition(tmp_path):
    """Ties break on (session, position) and the render order is total.

    This repository has been bitten four times by mutable or wall-clock state
    reaching an artifact, and set iteration order is the same class of bug: a
    pack that reordered between two runs of one question would make every
    replicate comparison noise.
    """
    conversation = _conversation(sessions=3)
    hits = None
    packs = []
    for _ in range(3):
        memory, result = _packed(tmp_path, conversation,
                                 evidence_pack_chars=300)
        hits = [_hit(str(result.corpus_dir / document_name(n)))
                for n in (2, 3, 1)]
        packs.append(memory.answer_evidence(hits, question="second chatter"))
    assert packs[0] == packs[1] == packs[2]


def test_a_turn_outside_the_staging_root_never_reaches_the_pack(tmp_path):
    """``source_path`` is UNTRUSTED frontmatter, and this is the side where it
    matters most: ranking buries a stolen file in a score, answering pastes it
    into a prompt. A path that escapes reads as "" and yields no turns."""
    outside = tmp_path / "outside.md"
    outside.write_text('# Session 0009\n\nChat Time: 1 Jan, 2020\n\n'
                       '[D9:1] Mallory said, "secret"\n', encoding="utf-8")
    memory, result = _packed(tmp_path, _conversation(sessions=1))
    items = memory.answer_evidence(
        [_hit(str(outside)), _hit(str(result.corpus_dir / document_name(1)))],
        question="secret")
    prompt = "\n\n".join(items)
    assert "secret" not in prompt and "[D9:1]" not in prompt
    assert "Session 0009" not in prompt


def test_a_pack_with_nothing_reachable_is_empty_rather_than_invented(tmp_path):
    """``evidence_chars`` 0 on the row is a visible failure. A fabricated
    prompt is not."""
    memory, _ = _packed(tmp_path, _conversation(sessions=1))
    assert memory.answer_evidence([_hit(str(tmp_path / "nowhere.md"))],
                                  question="anything") == []


def test_the_fact_heads_are_off_by_default_and_drop_the_path_when_on(tmp_path):
    """Heads cost 2,230 characters, which at a fixed cap is ~14 turns, and the
    turns are worth more (coverage 0.942 -> 0.927, open-domain 0.773 -> 0.682).
    Kept as a flag because LongMemEval §5.2 finds fact decomposition helps
    multi-session reasoning and nothing else — and affordable only because the
    99-character absolute path comes off first."""
    conversation = _conversation(sessions=1)
    memory, result = _packed(tmp_path, conversation)
    path = str(result.corpus_dir / document_name(1))
    hits = [_hit(path, text=f"pottery — a short summary — source: {path}",
                 name="pottery")]
    assert "pottery" not in "\n\n".join(
        memory.answer_evidence(hits, question="session 1 first"))

    headed, _ = _packed(tmp_path, conversation, turn_heads="fact")
    prompt = "\n\n".join(headed.answer_evidence(hits, question="session 1 first"))
    assert "pottery — a short summary" in prompt
    assert path not in prompt, "the head kept a path the backbone cannot open"


def test_the_dense_lane_reads_the_backend_and_a_zero_weight_skips_it(tmp_path):
    """A lane weighted 0.0 is not computed at all, which is what lets a
    BM25-only arm run with no model download and no network.

    ``turn_weights`` is a COMPLETE mapping and never a patch: naming one lane
    zeroes the others, which is what makes ``{"dense": 1.0}`` below a real
    single-lane arm rather than the defaults with dense restated.
    """
    calls: List[Any] = []

    class _Backend:
        name, dim = "stub", 2

        def embed(self, texts):
            calls.append(list(texts))
            # Aligns with anything mentioning "second", orthogonal otherwise.
            return [[1.0, 0.0] if "second" in t else [0.0, 1.0] for t in texts]

    conversation = _conversation(sessions=1)
    memory = _memory(evidence_unit="turn", backend=_Backend(),
                     turn_weights={"dense": 1.0}, evidence_pack_chars=100,
                     turn_score_window=0)
    result = memory.ingest(conversation, work=tmp_path, compile_project=False)
    prompt = "\n\n".join(memory.answer_evidence(
        [_hit(str(result.corpus_dir / document_name(1)))], question="second"))
    assert calls, "the dense lane never reached the backend"
    assert "[D1:2]" in prompt

    calls.clear()
    off, _ = _packed(tmp_path, conversation, backend=_Backend())
    off.answer_evidence([_hit(str(result.corpus_dir / document_name(1)))],
                        question="second")
    assert not calls, "a zero-weighted lane still constructed its backend"


def test_a_receipt_turn_is_admitted_ahead_of_the_score(tmp_path):
    """The one signal in the pack that comes from the GRAPH and not a scorer.

    ~6.7 turns / ~1,200 characters, 4.3% of the budget — and they only became
    reachable at all when ``query_hits`` stopped reading ``node_id`` off the
    anchor-substituted node. The budget here fits ONE turn, and BM25 would
    never choose this one.
    """
    conversation = _conversation(sessions=1)
    memory, result = _packed(tmp_path, conversation, evidence_pack_chars=90,
                             turn_score_window=0)
    path = str(result.corpus_dir / document_name(1))
    span = _span("D1:1", path)
    memory._graph = _graph([_claim("Claim:c", path), span],
                           [("Claim:c", span.id, "evidenced_by")])
    prompt = "\n\n".join(memory.answer_evidence(
        [_hit(path, node_id="Claim:c")], question="session 1 second"))
    assert "[D1:1]" in prompt, "the receipt lost its force-admission to the score"
    assert memory.receipt_lines == 1


def test_a_receipt_outside_the_pool_never_widens_the_prompt(tmp_path):
    """Confined to the POOL. A receipt in a session the pool does not hold
    would add a document and inflate the distinct-session count the retrieved
    pool exists to bound — and it costs almost nothing to refuse, because a
    one-hop ``evidenced_by`` receipt lands in the fact's own session 99.0% of
    the time."""
    conversation = _conversation(sessions=2)
    memory, result = _packed(tmp_path, conversation)
    paths = [str(result.corpus_dir / document_name(n)) for n in (1, 2)]
    span = _span("D2:1", paths[1])
    memory._graph = _graph([_claim("Claim:c", paths[0]), span],
                           [("Claim:c", span.id, "evidenced_by")])
    items = memory.answer_evidence([_hit(paths[0], node_id="Claim:c")],
                                   question="session 1 first")
    assert len(items) == 1 and "[D2:" not in items[0]
    assert memory.receipt_lines == 0


def test_the_pack_consults_no_graph_when_no_hit_carries_a_node_id(tmp_path):
    """A landmine, because the alternative is a graph walk per question on an
    arm that has nothing to walk it for."""
    memory, result = _packed(tmp_path, _conversation(sessions=1))

    def _landmine():
        raise AssertionError("the receipt index ran with no node ids to redeem")

    memory._receipt_index = _landmine
    assert memory.answer_evidence([_hit(str(result.corpus_dir / document_name(1)))],
                                  question="first")


def test_the_node_id_is_the_retrieved_nodes_and_never_the_anchors(tmp_path):
    """The bug fix, and it is a correctness fix with a measured effect.

    ``_hit_nodes`` substitutes the session ANCHOR, and anchors — SourceDocument
    / Session — carry no ``evidenced_by`` edge at all, so reading ``node_id``
    off the substituted node handed the receipt index an id with nothing behind
    it: 0.9 receipts per question on the shipped fan-out arm, flagging 1 of 183
    gold turns (0.5%). Taking the id from the node the RANKING chose, while
    text, name and path stay the anchor's, gives 6.7 per question flagging 65
    of 183 (35.5%).

    It changes no prompt byte on the shipped arm — ``node_id`` is read on none
    but the tiered and turn paths — and it changes no ranking: the session
    path-set is bit-identical either way, matched 199/199 over all conv-26
    questions. That is asserted here rather than merely stated.
    """
    path = f"corpus/{document_name(1)}"
    concept = ResearchNode(id="Concept:pots", name="pottery",
                           type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
                           description="a short summary", source_path=path)
    anchor = ResearchNode(id="SourceDocument:1", name=document_title(1),
                          type=ResearchNodeType.SOURCE_DOCUMENT,
                          description="the session itself", source_path=path)

    class _Graph:
        nodes = [concept, anchor]

    memory = _memory(search_fn=_RecordingSearch([concept]), fanout=True,
                     prefer_anchor_text=True)
    memory.ingest(_conversation(sessions=1), work=tmp_path, compile_project=False)
    memory._graph = _Graph()
    substituted = memory.query_hits("q", k=1)

    assert [h.node_id for h in substituted] == ["Concept:pots"], (
        "node_id came off the anchor, so the receipt index has nothing to walk"
    )
    assert [h.name for h in substituted] == [document_title(1)]
    assert [h.source_path for h in substituted] == [path]

    plain = _memory(search_fn=_RecordingSearch([concept]))
    plain.ingest(_conversation(sessions=1), work=tmp_path, compile_project=False)
    plain._graph = _Graph()
    unsubstituted = plain.query_hits("q", k=1)
    assert [h.node_id for h in unsubstituted] == ["Concept:pots"]
    assert ([h.source_path for h in substituted]
            == [h.source_path for h in unsubstituted]), (
        "the document path-set moved, so this is not the free fix it claims"
    )


def test_the_receipt_tripwires_count_the_whole_run_not_the_last_conversation(tmp_path):
    """One memory indexes ten conversations; meta is written once at the end.

    These two were assigned per graph while every other counter on the class
    accumulated, so a ten-conversation artifact reported run-wide receipt spend
    beside tripwires from whichever conversation happened to be last. Measured
    over the 2026-08-21 graphs the true total is 52 unresolvable spans, spread
    across five conversations; a run ending on conv-26 printed 0.
    """
    span = ResearchNode(
        id="EvidenceSpan:nowhere", name="D9:9 evidence",
        type=ResearchNodeType.EVIDENCE_SPAN, description="",
        source_path=f"corpus/{document_name(1)}",
    )

    class _Graph:
        nodes = [span]
        edges = []

    memory = _memory()
    conversation = _conversation(sessions=1)
    for _ in range(2):
        memory.ingest(conversation, work=tmp_path, compile_project=False)
        memory._graph = _Graph()
        memory._receipt_index()

    assert memory.unresolvable_spans == 2, (
        "the second conversation's tripwire overwrote the first — a run's "
        f"total cannot be {memory.unresolvable_spans} when two were seen"
    )

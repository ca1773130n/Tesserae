"""The LongMemEval-MAB adapter and runner — offline, on synthetic haystacks.

Nothing here compiles, embeds, or reaches a model. The compile and
``hybrid_search`` are both injected stubs, which is the point: the real pair is
an hours-long extraction and a metered embedding call, so wiring that can only
be checked by running the benchmark never gets checked at all.

What is pinned is the small set of decisions that would silently produce a
wrong number:

* the compile-inside-the-repo refusal, which would otherwise overwrite the
  project's own ``.tesserae/graph.json``;
* ``query`` returning EXACTLY K and never padding to it — K is the control the
  whole comparison rests on;
* the session split, including that dates survive it;
* every refusal layer in the runner;
* the report withholding its quotable table when a control is unmet.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pytest

from evals.lme_mab import run as runner
from evals.lme_mab.adapter import (
    EVIDENCE_SOURCE_CHARS,
    PROTOCOL_K,
    MabMemory,
    RefusedToCompileInRepo,
    Session,
    document_index,
    document_title,
    guard_work_dir,
    protocol_blockers,
    split_sessions,
)
from evals.lme_mab.dataset import MabGroup

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------- stubs


@dataclass
class _Node:
    name: str
    description: str = ""
    source_path: str = ""


@dataclass
class _Scored:
    node: _Node


@dataclass
class _Result:
    scored: List[_Scored]
    total_matches: int = 0


class _NotATerminal:
    """Stands in for ``sys.stdin`` so the confirmation cannot block a test run.

    pytest already replaces stdin with something whose ``isatty()`` is False —
    but not under ``-s``, and a test suite that hangs on an ``input()`` prompt
    under one flag is not a test suite.
    """

    def isatty(self) -> bool:
        return False


def _search_returning(n: int, *, total: int = 0, paths: Sequence[str] = ()):
    """A ``hybrid_search`` stub that yields ``n`` hits regardless of ``top_k``.

    Deliberately ignores ``top_k``: the adapter must slice to K itself rather
    than trusting the backend to have honoured it. ``paths`` gives hit ``i``
    the ``source_path`` a compiled node would carry; hits past its end carry
    none, which is what a node the compile gave no provenance looks like.
    """

    def _search(graph, query, **kwargs):
        return _Result([_Scored(_Node(f"node-{i}", f"about {query}",
                                      paths[i] if i < len(paths) else ""))
                        for i in range(n)],
                       total_matches=total or n)

    return _search


def _memory(work: Path, *, hits: int, paths: Sequence[str] = ()) -> MabMemory:
    memory = MabMemory(compile_fn=lambda w: None,
                       search_fn=_search_returning(hits, paths=paths),
                       backend=object())
    memory.work = work
    memory._graph = object()  # a compiled graph would be loaded here
    return memory


#: Two sessions in the shape MAB's ``context`` really uses — measured on the
#: parquet: a flat literal alternating a ``Chat Time:`` header with a list of
#: ``{role, content, has_answer}`` turns.
_CONTEXT = repr([
    "Chat Time: 2022/11/17 (Thu) 12:04",
    [{"role": "user", "content": "where did I leave my keys", "has_answer": False},
     {"role": "assistant", "content": "on the hall table", "has_answer": True}],
    "Chat Time: 2022/12/28 (Wed) 16:10",
    [{"role": "user", "content": "book me a flight", "has_answer": False}],
    "Chat Time: 2023/01/05 (Thu) 12:34",
    [{"role": "user", "content": "and a hotel", "has_answer": False}],
])


def _group(**overrides: Any) -> MabGroup:
    base: Dict[str, Any] = {
        "index": 0, "source": "longmemeval_s*", "context": _CONTEXT,
        "questions": ["where are the keys"], "answers": [["the hall table"]],
        "question_types": ["single-session-user"],
    }
    base.update(overrides)
    return MabGroup(**base)


# ------------------------------------------------------- the in-repo refusal


def test_ingest_refuses_to_compile_at_the_repo_root():
    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"))
    with pytest.raises(RefusedToCompileInRepo, match="refusing to compile inside the repo"):
        memory.ingest(_group(), work=REPO)


def test_ingest_refuses_a_subdirectory_of_the_repo(tmp_path):
    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"))
    with pytest.raises(RefusedToCompileInRepo):
        memory.ingest(_group(), work=REPO / "evals" / "lme_mab" / "scratch")


def test_ingest_refuses_any_directory_holding_a_pyproject(tmp_path):
    """A checkout somewhere else is still somebody's project to overwrite."""
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"))
    with pytest.raises(RefusedToCompileInRepo):
        memory.ingest(_group(), work=tmp_path)


def test_the_refusal_fires_before_anything_is_written(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    memory = MabMemory(compile_fn=lambda w: None)
    with pytest.raises(RefusedToCompileInRepo):
        memory.ingest(_group(), work=tmp_path)
    assert not (tmp_path / "corpus").exists()


def test_a_scratch_directory_outside_the_repo_is_allowed(tmp_path):
    assert guard_work_dir(tmp_path) == tmp_path.resolve()


# ------------------------------------------------------------ session split


def test_the_split_is_by_session_and_keeps_the_dates():
    sessions = split_sessions(_group())

    assert len(sessions) == 3
    assert [s.date for s in sessions] == [
        "2022/11/17 (Thu) 12:04", "2022/12/28 (Wed) 16:10", "2023/01/05 (Thu) 12:34",
    ]
    assert [len(s.turns) for s in sessions] == [2, 1, 1]


def test_ingest_writes_one_document_per_session(tmp_path):
    memory = MabMemory(compile_fn=lambda w: None)

    result = memory.ingest(_group(), work=tmp_path)

    assert result.documents == 3
    assert result.turns == 4
    assert result.dated_sessions == 3
    assert result.session_source == "context"
    assert sorted(p.name for p in result.corpus_dir.glob("*.md")) == [
        "session-0000.md", "session-0001.md", "session-0002.md",
    ]


def test_the_staged_documents_carry_the_date_and_not_the_gold_marker(tmp_path):
    """``has_answer`` marks the gold evidence turn. Staging it would let
    retrieval key on "this is the answer" and score the leak."""
    memory = MabMemory(compile_fn=lambda w: None)
    result = memory.ingest(_group(), work=tmp_path)

    body = (result.corpus_dir / "session-0000.md").read_text(encoding="utf-8")
    assert "Chat Time: 2022/11/17 (Thu) 12:04" in body
    assert "on the hall table" in body
    assert "has_answer" not in body


def test_restaging_the_same_group_is_byte_identical(tmp_path):
    """No clock reaches this path, so a re-ingest must produce the same bytes."""
    memory = MabMemory(compile_fn=lambda w: None)
    first = memory.ingest(_group(), work=tmp_path)
    before = {p.name: p.read_bytes() for p in sorted(first.corpus_dir.glob("*.md"))}

    second = memory.ingest(_group(), work=tmp_path)
    after = {p.name: p.read_bytes() for p in sorted(second.corpus_dir.glob("*.md"))}

    assert before == after


def test_a_second_smaller_group_does_not_inherit_the_first_ones_documents(tmp_path):
    memory = MabMemory(compile_fn=lambda w: None)
    memory.ingest(_group(), work=tmp_path)

    small = _group(context=repr(["Chat Time: 2024/01/01 (Mon) 09:00",
                                 [{"role": "user", "content": "hello"}]]))
    result = memory.ingest(small, work=tmp_path)

    assert result.documents == 1
    assert [p.name for p in result.corpus_dir.glob("*.md")] == ["session-0000.md"]


def test_the_split_falls_back_to_haystack_sessions_without_dates():
    """The parsed view carries no ``Chat Time:`` at all — measured on the real
    parquet — so a fallback split cannot answer the temporal stratum."""
    group = _group(context="not a python literal at all", haystack_sessions=[
        [[{"role": "user", "content": "a"}], [{"role": "user", "content": "b"}]],
        [[{"role": "user", "content": "c"}]],
    ])

    sessions = split_sessions(group)

    assert len(sessions) == 3
    assert [s.date for s in sessions] == ["", "", ""]


def test_a_group_with_neither_view_raises_rather_than_staging_nothing():
    with pytest.raises(ValueError, match="no sessions"):
        split_sessions(_group(context="", haystack_sessions=[]))


def test_disagreeing_views_are_recorded_and_do_not_stop_the_run(tmp_path):
    group = _group(haystack_sessions=[[[{"role": "user", "content": "a"}]]])  # 1 vs 3
    memory = MabMemory(compile_fn=lambda w: None)

    result = memory.ingest(group, work=tmp_path)

    assert result.documents == 3          # the dated view still wins
    assert result.views_agree is False


# ------------------------------------------------------------------- query


def test_query_returns_exactly_k(tmp_path):
    memory = _memory(tmp_path, hits=25)

    assert len(memory.query("where are the keys")) == PROTOCOL_K
    assert memory.shortfalls == []


def test_query_honours_a_non_default_k(tmp_path):
    memory = _memory(tmp_path, hits=25)

    assert len(memory.query("q", k=3)) == 3


def test_query_records_a_shortfall_and_never_pads(tmp_path):
    memory = _memory(tmp_path, hits=4)

    evidence = memory.query("where are the keys")

    assert len(evidence) == 4                       # short, not padded
    assert all(text.strip() for text in evidence)   # and not padded with blanks
    assert memory.shortfalls == [{
        "question": "where are the keys", "requested": PROTOCOL_K,
        "returned": 4, "total_matches": 4,
    }]


def test_an_empty_result_is_a_shortfall_not_an_empty_success(tmp_path):
    memory = _memory(tmp_path, hits=0)

    assert memory.query("q") == []
    assert memory.shortfalls[0]["returned"] == 0


def test_query_before_ingest_raises_rather_than_scoring_zero():
    memory = MabMemory(search_fn=_search_returning(3), backend=object())
    with pytest.raises(RuntimeError, match="before ingest"):
        memory.query("q")


# ------------------------------------------- the documents behind the hits


def test_document_index_round_trips_a_staged_document_name():
    """``document_index`` is the inverse of ``Session.document_name`` and the
    only place either direction is written — nobody formats or parses
    ``session-%04d.md`` themselves."""
    for index in (0, 7, 113, 12345):
        name = Session(index=index, date="", turns=[]).document_name
        assert document_index(name) == index


def test_document_index_reads_a_relative_or_an_absolute_source_path():
    """A retrieved node carries whatever path the compile recorded."""
    assert document_index("corpus/session-0007.md") == 7
    assert document_index("/tmp/work/corpus/session-0113.md") == 113


@pytest.mark.parametrize("name", [
    "", None, "notes.md", "session-7.md", "session-0007.md.bak", "xsession-0007.md",
])
def test_document_index_refuses_a_name_this_adapter_did_not_write(name):
    """Strict rather than forgiving: a near-miss resolved to a plausible index
    is fabricated evidence, and the caller counts ``None`` instead."""
    assert document_index(name) is None


def test_query_hits_carry_the_document_they_came_from(tmp_path):
    memory = _memory(tmp_path, hits=2,
                     paths=["corpus/session-0002.md", "corpus/session-0000.md"])

    hits = memory.query_hits("q", k=2)

    assert [hit.document for hit in hits] == [2, 0]
    assert all(hit.text for hit in hits)
    assert memory.shortfalls == []


def test_query_is_the_text_of_query_hits(tmp_path):
    """One search, one shortfall recorder — ``query`` adds nothing of its own."""
    memory = _memory(tmp_path, hits=4)

    texts = memory.query("q")

    assert texts == [hit.text for hit in memory.query_hits("q")]
    assert len(memory.shortfalls) == 2  # one per call, both through query_hits


def test_search_documents_dedups_and_keeps_the_first_occurrence(tmp_path):
    """Two nodes from one session are one document, at the better rank: they
    are not two pieces of evidence about where the answer lives."""
    memory = _memory(tmp_path, hits=4, paths=[
        "corpus/session-0005.md", "corpus/session-0002.md",
        "corpus/session-0005.md", "corpus/session-0009.md",
    ])

    assert memory.search_documents("q", k=4) == [5, 2, 9]
    assert memory.n_unmapped_hits == 0


def test_search_documents_drops_an_unmappable_hit_and_counts_it(tmp_path):
    """``merge_node_group`` keeps ONE ``source_path`` when it collapses a
    concept extracted from many sessions, so some hits point at no staged
    document at all. They are counted — the row is a lower bound — never
    resolved to a nearby index."""
    memory = _memory(tmp_path, hits=3, paths=[
        "corpus/session-0001.md", "", "concept-page.md",
    ])

    assert memory.search_documents("q", k=3) == [1]
    assert memory.n_unmapped_hits == 2


# ------------------------------------------- the text the backbone reads


def _staged(work: Path, *indices: int, body: str = "") -> None:
    """Write ``session-NNNN.md`` for each index, as ``ingest`` would."""
    corpus = work / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    for index in indices:
        session = Session(index=index, date="2023/05/21 (Sun) 09:00",
                          turns=[{"role": "user", "content": body or f"body {index}"}])
        (corpus / session.document_name).write_text(session.render(), encoding="utf-8")


def _doc(work: Path, index: int) -> str:
    """The ``source_path`` a compile records: ABSOLUTE, as the real graph's are.

    ``hybrid._confined_source`` resolves a path as given, so a relative one
    resolves against the process's cwd and falls outside the work tree — which
    is why the real graph's paths are absolute and these are too."""
    return str(work / "corpus" / f"session-{index:04d}.md")


def _anchored(work: Path, nodes: Sequence[tuple]) -> MabMemory:
    """A memory whose search returns ``(name, source_path)`` in that order."""
    def _search(graph, query, **kwargs):
        return _Result([_Scored(_Node(name, f"about {query}", path))
                        for name, path in nodes], total_matches=len(nodes))

    memory = MabMemory(compile_fn=lambda w: None, search_fn=_search, backend=object())
    memory.work = work
    memory._graph = object()
    return memory


def test_the_document_title_is_the_h1_the_staged_document_carries():
    """``is_document_anchor`` compares a node's name against this string, and a
    compile names a document's anchor after the document's own H1. Two
    spellings of it would silently stop every anchor matching, and the evidence
    would quietly go back to being an 88-character summary."""
    for index in (0, 20, 113):
        rendered = Session(index=index, date="", turns=[]).render()
        assert rendered.splitlines()[0] == f"# {document_title(index)}"


def test_an_anchor_gets_its_session_and_an_impostor_sharing_the_path_does_not(tmp_path):
    """The trap. Every node in this graph carries the ``source_path`` of the
    chat it was extracted from, so ``session-0015.md`` is the provenance of the
    session AND of "Banksy: Wall and Piece", a book mentioned in it. Measured on
    the compiled group-0 graph, 103 of the 214 nodes a TYPE-based rule would
    admit are impostors like that one — and under the lexical lane 219 of 600
    retrieved hits are.

    The impostor ranks FIRST here on purpose. De-duplication would hide a
    broken gate if the anchor came first, and the retrieval that matters is the
    one where the anchor is not in the top-10 at all: the book alone would then
    hand the backbone a whole transcript under somebody else's name."""
    _staged(tmp_path, 15, body="the keys are on the hall table")
    path = _doc(tmp_path, 15)
    memory = _anchored(tmp_path, [("Banksy: Wall and Piece", path),
                                  ("Session 0015", path)])

    impostor, anchor = memory.answer_evidence(memory.query_hits("q", k=2))

    assert "the keys are on the hall table" not in impostor
    assert impostor == f"Banksy: Wall and Piece — about q — source: {path}"
    assert "the keys are on the hall table" in anchor

    alone = _anchored(tmp_path, [("Banksy: Wall and Piece", path)])
    assert alone.answer_evidence(alone.query_hits("q", k=1)) == [impostor]


def test_a_session_is_expanded_once_however_many_of_its_nodes_rank(tmp_path):
    """17 of group 0's 111 sessions carry a ``Session`` summary node named
    exactly like their ``SourceDocument`` anchor, so both pass the identity
    test and both point at one file. Eleven of the 60 real questions retrieved
    such a pair. Paying twice for the same bytes is the failure the gate exists
    to prevent, so the text goes to the FIRST hit that stands for the file —
    the rule ``documents_of`` already scores by."""
    _staged(tmp_path, 43, body="an unmistakable sentence")
    memory = _anchored(tmp_path, [("Session 0043", _doc(tmp_path, 43)),
                                  ("Session 0043", _doc(tmp_path, 43))])

    first, second = memory.answer_evidence(memory.query_hits("q", k=2))

    assert "an unmistakable sentence" in first
    assert "an unmistakable sentence" not in second
    assert len({first, second}) == 2


def test_the_session_text_is_capped_and_taken_from_the_front(tmp_path):
    """A ranking cap is not an answering cap — see ``EVIDENCE_SOURCE_CHARS``.
    Truncation is from the front because gold answers are front-loaded here
    (median offset 268 over group 0's 53 literally locatable golds)."""
    _staged(tmp_path, 7, body="A" * 40_000 + "TAIL")
    memory = _anchored(tmp_path, [("Session 0007", _doc(tmp_path, 7))])

    (evidence,) = memory.answer_evidence(memory.query_hits("q", k=1))

    head = evidence.split("\n", 1)[1]
    assert len(head) == EVIDENCE_SOURCE_CHARS
    assert head.startswith("# Session 0007")
    assert "TAIL" not in evidence


def test_a_source_path_outside_the_work_directory_is_never_read(tmp_path):
    """``source_path`` arrives from document frontmatter and is UNTRUSTED. The
    answering side is the dangerous one: ranking buries a stolen file in a BM25
    score, answering pastes it verbatim into an LLM prompt. The read is
    confined to the directory this adapter staged into."""
    outside = tmp_path.parent / "secret"
    outside.mkdir(exist_ok=True)
    (outside / "session-0001.md").write_text("BEGIN OPENSSH PRIVATE KEY", encoding="utf-8")
    work = tmp_path / "work"
    _staged(work, 1)
    memory = _anchored(work, [("Session 0001", str(outside / "session-0001.md"))])

    (evidence,) = memory.answer_evidence(memory.query_hits("q", k=1))

    assert "PRIVATE KEY" not in evidence
    assert evidence == memory.query_hits("q", k=1)[0].text


def test_the_expansion_is_answering_only_and_moves_no_retrieval_number(tmp_path):
    """The deterministic half of this benchmark is its most reproducible
    evidence — BM25 reproduced to four decimals across four runs — and nothing
    on the answering side may perturb it. ``query``, ``query_hits`` and
    ``search_documents`` see the node text and no file at all."""
    _staged(tmp_path, 5, body="a sentence only the file has")
    memory = _anchored(tmp_path, [("Session 0005", _doc(tmp_path, 5)),
                                  ("Digital Detox", _doc(tmp_path, 5))])

    hits = memory.query_hits("q", k=2)

    assert all("a sentence only the file has" not in hit.text for hit in hits)
    assert memory.query("q") == [hit.text for hit in hits]
    assert memory.documents_of(hits) == [5]
    assert memory.search_documents("q", k=2) == [5]


def test_answer_evidence_before_ingest_returns_the_node_text_rather_than_raising(tmp_path):
    """``work`` is None until ``ingest`` runs. There is no tree to confine a
    read to, so there is no read — the evidence is what it was before this
    existed rather than an exception on the answering path."""
    memory = _memory(tmp_path, hits=2, paths=["corpus/session-0002.md"])
    memory.work = None
    hits = memory.query_hits("q", k=2)

    assert memory.answer_evidence(hits) == [hit.text for hit in hits]


def test_the_answer_rows_record_what_the_backbone_actually_read(tmp_path):
    """An IDENTICAL generative config has swung 0.043 token F1 between two runs
    in this repo, so the evidence content is the one thing worth persisting per
    row. ``n_evidence`` counts items and stopped describing the budget the
    moment items stopped carrying comparable amounts of text."""
    _staged(tmp_path, 2, body="the hall table")
    memory = _anchored(tmp_path, [("Session 0002", _doc(tmp_path, 2))])
    seen: List[Sequence[str]] = []

    rows, retrieved = runner.answer_group(
        memory, _group(), lambda q, evidence: seen.append(evidence) or "the hall table",
        k=1, progress=False)

    assert retrieved == [[2]]
    assert "the hall table" in seen[0][0]              # the backbone read the file
    assert rows[0]["n_evidence"] == 1
    assert rows[0]["evidence_chars"] == len(seen[0][0])


def test_the_report_states_the_evidence_budget_in_characters(tmp_path):
    """K counts ITEMS. Once an item can be a summary or a summary plus 2,400
    characters of transcript, "the full K=10 evidence items" no longer
    describes the budget, and §4 would be certifying a control it stopped
    checking."""
    rows = [{"question": "q", "answer": "a", "gold": ["a"],
             "stratum": "multi-session", "n_evidence": 10, "evidence_chars": 23_281}]
    meta = _met_meta(evidence_source_chars=2_400, **runner._evidence_chars(rows))
    report = runner.score_system(rows, system="Tesserae", meta=meta)

    text = runner.build_report([report])

    assert "Mean 23,281 per question" in text
    assert "first 2,400 characters" in text


def test_an_answers_file_written_before_this_existed_declares_nothing(tmp_path):
    """``--score`` re-reads saved rows. One without ``evidence_chars`` must
    declare no distribution rather than declare a distribution of zero."""
    assert runner._evidence_chars([{"question": "q", "n_evidence": 10}]) == {}


# -------------------------------------------------------- protocol controls


def _met_meta(**overrides: Any) -> Dict[str, Any]:
    """A meta describing a run that ACTUALLY HAPPENED.

    The `evidence` block is not decoration. Without it these controls are a
    hand-written claim, and a hand-written claim used to return zero blockers
    and print a table captioned "in the same units as the published table" —
    with no key, no judge and no run behind it. Declarations are checked
    against the protocol; evidence is what says a run produced them.
    """
    meta = {"llm_model": "gpt-5.4-mini", "embedding_model": "text-embedding-3-small",
            "judge": "gpt-4o-mini", "evidence_budget": 10,
            "evidence": {"answer_calls": 300, "judge_calls": 300}}
    meta.update(overrides)
    return meta


def test_all_four_controls_met_blocks_nothing():
    assert protocol_blockers(_met_meta()) == []


@pytest.mark.parametrize("key", ["llm_model", "embedding_model", "judge", "evidence_budget"])
def test_a_missing_declaration_is_a_blocker(key):
    blockers = protocol_blockers(_met_meta(**{key: ""}))
    assert any(b.startswith(f"{key}: not declared") for b in blockers)


def test_a_wrong_value_names_both_sides():
    (blocker,) = protocol_blockers(_met_meta(evidence_budget=20))
    assert "this run used 20" in blocker and "fixes 10" in blocker


def test_the_report_withholds_the_comparable_table_when_a_control_is_unmet():
    report = runner.score_system(
        [{"question": "q", "answer": "a", "gold": ["a"], "stratum": "multi-session"}],
        system="Tesserae", meta=_met_meta(judge=""),
    )

    text = runner.build_report([report])

    assert "**Withheld" in text
    assert "**UNMET**" in text
    # The quotable row must not appear anywhere, not even below a caveat.
    assert "| Tesserae |" not in text


def test_the_report_prints_the_table_when_every_control_is_met():
    report = runner.score_system(
        [{"question": "q", "answer": "a", "gold": ["a"], "stratum": "multi-session"}],
        system="Tesserae", meta=_met_meta(),
    )

    text = runner.build_report([report])

    assert "**Withheld" not in text
    assert "| Tesserae | gpt-5.4-mini | text-embedding-3-small | 10 |" in text


def test_the_report_reads_no_clock():
    report = runner.score_system(
        [{"question": "q", "answer": "a", "gold": ["a"], "stratum": "multi-session"}],
        system="Tesserae", meta=_met_meta(),
    )

    assert runner.build_report([report]) == runner.build_report([report])


def test_a_shortfall_is_reported_rather_than_averaged_away():
    report = runner.score_system(
        [{"question": "q", "answer": "a", "gold": ["a"], "stratum": "multi-session"}],
        system="Tesserae", meta=_met_meta(),
    )

    text = runner.build_report(
        [report], shortfalls=[{"question": "q", "requested": 10, "returned": 2,
                             "total_matches": 2}])

    assert "1 of 1 queries returned fewer than K=10" in text


# --------------------------------------------------------- runner refusals


def test_the_runner_skips_when_ci_is_set(monkeypatch, capsys):
    monkeypatch.setenv("CI", "1")

    assert runner.main(["--i-know-this-costs-money", "--yes"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("SKIP: CI is set")
    assert "LongMemEval-MAB — ESTIMATED COST" not in out  # nothing ran at all


def test_the_runner_skips_without_the_money_flag(monkeypatch, capsys):
    monkeypatch.delenv("CI", raising=False)

    assert runner.main([]) == 0

    out = capsys.readouterr().out
    assert "ESTIMATED COST" in out            # the banner prints first
    assert "SKIP: this run compiles a haystack" in out
    assert "--i-know-this-costs-money" in out


def test_the_runner_skips_when_the_parquet_is_missing(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert runner.main([
        "--parquet", str(tmp_path / "nope.parquet"),
        "--work", str(tmp_path / "work"),
        "--i-know-this-costs-money", "--yes",
    ]) == 0

    out = capsys.readouterr().out
    assert "SKIP: MemoryAgentBench parquet not found" in out
    assert "ai-hyz/MemoryAgentBench" in out


def test_the_runner_skips_without_a_key_when_the_openai_embedder_is_asked_for(
    monkeypatch, capsys, tmp_path
):
    """``--embedding-prefer openai`` is what bills, so it is what needs the key."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    parquet = tmp_path / "m.parquet"
    parquet.write_bytes(b"not really a parquet")

    assert runner.main([
        "--parquet", str(parquet), "--work", str(tmp_path / "work"),
        "--embedding-prefer", "openai",
        "--i-know-this-costs-money", "--yes",
    ]) == 0

    out = capsys.readouterr().out
    assert "SKIP:" in out and "OPENAI_API_KEY" in out


def test_the_runner_does_not_demand_a_key_for_the_local_embedder(
    monkeypatch, capsys, tmp_path
):
    """A key it will never use must not stand between a run and the local arms.

    ``--embedding-prefer`` defaults to the local backend so §6 can hold ONE
    embedder still across all three arms. The gate used to fire for every
    Tesserae run regardless, which refused the self-consistent local comparison
    that section exists to print — over a credential the run would not have
    touched. The run still stops here, but on the corrupt parquet rather than on
    the key.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    parquet = tmp_path / "m.parquet"
    parquet.write_bytes(b"not really a parquet")

    assert runner.main([
        "--parquet", str(parquet), "--work", str(tmp_path / "work"),
        "--i-know-this-costs-money", "--yes",
    ]) == 0

    out = capsys.readouterr().out
    assert "SKIP:" in out
    assert "OPENAI_API_KEY" not in out


def test_the_runner_skips_when_the_work_dir_is_inside_the_repo(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    parquet = tmp_path / "m.parquet"
    parquet.write_bytes(b"not really a parquet")

    assert runner.main([
        "--parquet", str(parquet), "--work", str(REPO),
        "--i-know-this-costs-money", "--yes",
    ]) == 0

    out = capsys.readouterr().out
    assert "SKIP: refusing to compile inside the repo" in out


def test_the_runner_will_not_spend_without_a_confirmation(monkeypatch, capsys, tmp_path):
    """Non-interactive and no ``--yes``: the estimate cannot be confirmed, so
    nothing runs."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(runner.sys, "stdin", _NotATerminal())
    # With the answer key, because the runner aligns gold before it asks for
    # money — a group whose ``haystack_sessions`` is missing refuses first, and
    # this test is about the confirmation and not about that refusal.
    gold = [{"role": "user", "content": "where did I leave my keys", "has_answer": False},
            {"role": "assistant", "content": "on the hall table", "has_answer": True}]
    monkeypatch.setattr(runner, "load_groups_or_skip",
                        lambda p: [_group(haystack_sessions=[[gold]])])
    parquet = tmp_path / "m.parquet"
    parquet.write_bytes(b"stub")

    assert runner.main([
        "--parquet", str(parquet), "--work", str(tmp_path / "work"),
        "--i-know-this-costs-money",
    ]) == 0

    assert "SKIP: not a terminal" in capsys.readouterr().out


# ------------------------------------------------------------- cost banner


def test_the_banner_scales_off_the_measured_totals():
    one, five = runner.estimate_cost(1), runner.estimate_cost(5)

    assert five.chars == runner.MEASURED["chars"]           # 8,140,368
    assert five.questions == 300
    assert one.questions == 60
    assert one.chars == round(runner.MEASURED["chars"] / 5)
    assert five.codex_tokens == 15_600_000
    assert five.api_tokens == 3_400_000
    assert one.codex_tokens == 3_120_000


def test_the_banner_names_both_columns_and_the_overhead():
    text = runner.cost_banner(runner.estimate_cost(1))

    assert "1 of 5 group(s)" in text
    assert "via codex" in text and "via OpenAI API" in text
    assert "15,090 tok/call" in text


def test_the_banner_is_printed_before_any_input_is_read(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CI", raising=False)

    runner.main(["--parquet", str(tmp_path / "absent.parquet")])

    out = capsys.readouterr().out
    assert out.index("ESTIMATED COST") < out.index("SKIP:")


# ---------------------------------------------------------- reuse_compiled
#
# A compile is ~an hour per group, so re-measuring a retrieval change on a
# group that has already been built has to be possible without paying it
# again. The flag's whole safety is that it refuses when the staged corpus is
# not the one this group would stage: a graph compiled from other text would
# answer about a haystack the questions were never asked about, and would
# look like a valid measurement while doing it.


def _already_compiled(work, group, *, graph=True, indexes=True):
    """Stage ``group`` in ``work`` the way a previous run would have.

    ``indexes=False`` writes a graph that indexes NO staged document — the
    foreign-graph case. The original helper always wrote the literal "{}",
    which means every reuse test exercised the corpus half of the check and
    none of them could see the graph half; a graph indexing a different group
    passed all nine.
    """
    memory = MabMemory(compile_fn=lambda w: None)
    memory.ingest(group, work=work)
    if graph:
        tess = work / ".tesserae"
        tess.mkdir(parents=True, exist_ok=True)
        nodes = []
        if indexes:
            nodes = [
                {"id": p.stem, "source_path": str(p)}
                for p in sorted((work / "corpus").glob("*.md"))
            ]
        (tess / "graph.json").write_text(
            json.dumps({"nodes": nodes, "edges": []}), encoding="utf-8"
        )
    return memory


def test_reuse_compiled_does_not_compile(tmp_path):
    _already_compiled(tmp_path, _group())
    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"))

    result = memory.ingest(_group(), work=tmp_path, reuse_compiled=True)

    assert result.reused is True
    assert result.compiled is False
    assert result.documents == 3
    assert result.turns == 4


def test_reuse_compiled_writes_nothing(tmp_path):
    """The compiled group is a read-mostly measurement target, not scratch."""
    _already_compiled(tmp_path, _group())
    corpus = tmp_path / "corpus"
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
              for p in sorted(corpus.glob("*.md"))}

    MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway")).ingest(
        _group(), work=tmp_path, reuse_compiled=True)

    after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
             for p in sorted(corpus.glob("*.md"))}
    assert before == after


def test_reuse_compiled_refuses_when_a_staged_document_differs(tmp_path):
    _already_compiled(tmp_path, _group())
    (tmp_path / "corpus" / "session-0001.md").write_text("something else\n",
                                                         encoding="utf-8")
    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"))

    with pytest.raises(ValueError, match="not this group's corpus"):
        memory.ingest(_group(), work=tmp_path, reuse_compiled=True)


def test_reuse_compiled_refuses_when_the_corpus_holds_an_extra_session(tmp_path):
    """The graph would index a session this group does not contain."""
    _already_compiled(tmp_path, _group())
    (tmp_path / "corpus" / "session-0099.md").write_text("# stray\n",
                                                         encoding="utf-8")
    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"))

    with pytest.raises(ValueError, match="unexpected"):
        memory.ingest(_group(), work=tmp_path, reuse_compiled=True)


def test_reuse_compiled_refuses_a_smaller_group_against_a_bigger_corpus(tmp_path):
    _already_compiled(tmp_path, _group())
    small = _group(context=repr(["Chat Time: 2024/01/01 (Mon) 09:00",
                                 [{"role": "user", "content": "hello"}]]))
    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"))

    with pytest.raises(ValueError, match="unexpected"):
        memory.ingest(small, work=tmp_path, reuse_compiled=True)


def test_reuse_compiled_refuses_when_there_is_no_graph(tmp_path):
    _already_compiled(tmp_path, _group(), graph=False)
    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"))

    with pytest.raises(FileNotFoundError, match="nothing to reuse"):
        memory.ingest(_group(), work=tmp_path, reuse_compiled=True)


def test_reuse_compiled_refuses_when_there_is_no_corpus(tmp_path):
    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"))

    with pytest.raises(FileNotFoundError, match="nothing to reuse"):
        memory.ingest(_group(), work=tmp_path, reuse_compiled=True)


def test_the_default_ingest_still_compiles_and_is_not_marked_reused(tmp_path):
    """Every existing caller must be byte-identical: the flag is opt-in."""
    compiled = []
    memory = MabMemory(compile_fn=compiled.append)

    result = memory.ingest(_group(), work=tmp_path)

    assert compiled == [tmp_path.resolve()]
    assert result.compiled is True
    assert result.reused is False


def test_reuse_refuses_a_graph_compiled_from_a_different_corpus(tmp_path):
    """Verifying the CORPUS is not verifying the GRAPH.

    ``ingest`` restages the corpus BEFORE compiling, so a work dir can hold one
    group's freshly staged documents beside another group's graph — and the
    corpus check passes on both. Reuse would then print "reused (earlier run)"
    while retrieving from a different haystack than the one being scored, which
    is the exact failure the flag's docstring claims to prevent.
    """
    _already_compiled(tmp_path, _group(), indexes=False)
    memory = MabMemory(compile_fn=lambda w: pytest.fail("must refuse, not compile"))

    with pytest.raises(ValueError, match="does not index"):
        memory.ingest(_group(), work=tmp_path, reuse_compiled=True)


def test_reuse_accepts_a_graph_that_indexes_the_staged_corpus(tmp_path):
    """The positive half — the guard must not refuse a legitimate reuse."""
    _already_compiled(tmp_path, _group())
    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"))

    result = memory.ingest(_group(), work=tmp_path, reuse_compiled=True)

    assert result.reused is True


def test_reuse_tolerates_a_relocated_work_directory(tmp_path):
    """Source paths are compared by BASENAME, so a work dir moved between runs
    reads as reusable rather than foreign — relocation is not corruption."""
    _already_compiled(tmp_path, _group())
    graph = tmp_path / ".tesserae" / "graph.json"
    payload = json.loads(graph.read_text())
    for node in payload["nodes"]:
        node["source_path"] = "/somewhere/else/corpus/" + Path(node["source_path"]).name
    graph.write_text(json.dumps(payload), encoding="utf-8")

    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"))
    assert memory.ingest(_group(), work=tmp_path, reuse_compiled=True).reused is True

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
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pytest

from evals.lme_mab import run as runner
from evals.lme_mab.adapter import (
    PROTOCOL_K,
    MabMemory,
    RefusedToCompileInRepo,
    Session,
    document_index,
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

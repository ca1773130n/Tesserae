"""LoCoMo runner — the three-number contract, the canary, and the guards.

Offline and synthetic: a dataset written into ``tmp_path``, stub search and
stub backbones, no compile and no network. Every case here is about a way a
report could be WRONG while looking finished — a headline printed without its
decomposition, an adversarial score printed alone, a dead backbone reported as
a cautious one, or a compile landing in the repository.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

import pytest

from evals.locomo import run as runner
from evals.locomo.dataset import LocomoQuestion, load_conversations
from evals.locomo.judge import DeadBackbone, DeterministicJudge, Verdict
from evals.locomo.scoring import (
    GradedRow,
    decompose,
    gap_decomposition,
    grade,
    question_key,
    replicate_spread,
)


def _question(category: int = 4, answer="teal", conversation="conv-test"):
    return LocomoQuestion(question="What colour?", category=category,
                          evidence=["D1:1"], conversation=conversation,
                          answer=answer)


def _conversation_with(n_questions: int):
    """A minimal Conversation carrying ``n_questions`` questions."""
    from evals.locomo.dataset import Conversation

    return Conversation(
        sample_id="conv-test", speaker_a="A", speaker_b="B", sessions=[],
        questions=[_question() for _ in range(n_questions)],
    )

def _row(arm: str, key: str, *, correct: bool = False, refused: bool = False,
         errored: bool = False, score: float = 0.0, category: int = 4,
         replicate: int = 0, reference_correct=None) -> GradedRow:
    return GradedRow(
        key=key, arm=arm, replicate=replicate, conversation="conv-test",
        question="q", category=category, stratum="single-hop", answer="a",
        verdict=Verdict(correct=correct, score=score,
                        label="CORRECT" if correct else "WRONG", judge="stub",
                        refused=refused, errored=errored,
                        reference_correct=reference_correct),
    )


# ------------------------------------------------- the three-number contract


def test_all_three_numbers_are_computed():
    rows = [
        _row("a", "k1", correct=True, score=1.0),
        _row("a", "k2", refused=True),
        _row("b", "k1", correct=True, score=1.0),
        _row("b", "k2", correct=True, score=1.0),
    ]
    result = decompose(rows)
    assert result.complete
    assert result.all_questions["a"].n == 2
    assert result.all_questions["a"].accuracy == 0.5
    assert result.like_for_like["a"].n == 1
    assert result.like_for_like["a"].accuracy == 1.0
    assert result.refusals == {"a": 1, "b": 0}


def test_a_question_any_arm_refused_leaves_the_like_for_like_subset():
    """Dropping refusals per ARM would compare each arm on a different set of
    questions, which is not a like-for-like comparison at all."""
    rows = [_row("a", "k1", refused=True), _row("b", "k1", correct=True, score=1.0)]
    result = decompose(rows)
    assert result.n_all == 1
    assert result.like_for_like["b"].n == 0


def test_an_errored_question_leaves_the_subset_too():
    rows = [_row("a", "k1", errored=True), _row("b", "k1", correct=True)]
    assert decompose(rows).n_like_for_like == 0


def test_an_empty_like_for_like_subset_makes_the_decomposition_incomplete():
    """Three numbers or none. Two of three is the failure mode this prevents."""
    rows = [_row("a", "k1", refused=True), _row("b", "k1", correct=True)]
    result = decompose(rows)
    assert not result.complete
    assert any("like-for-like" in reason for reason in result.missing)


def test_a_run_of_only_adversarial_questions_is_incomplete():
    """Category 5 alone is where a dead backbone scores perfectly."""
    result = decompose([_row("a", "k1", correct=True, category=5)])
    assert not result.complete
    assert any("category 5" in reason for reason in result.missing)


def test_no_rows_at_all_is_incomplete_rather_than_zero():
    result = decompose([])
    assert not result.complete and result.missing


def test_the_adversarial_category_is_never_in_the_answerable_numbers():
    """A refusal is the gold answer there, so mixing it into a refusal rate
    averages a virtue and a defect."""
    rows = [_row("a", "k1", correct=True, score=1.0),
            _row("a", "k2", correct=True, category=5, refused=True)]
    result = decompose(rows)
    assert result.all_questions["a"].n == 1
    assert result.refusals["a"] == 0
    assert result.adversarial["a"].n == 1


def test_the_adversarial_block_carries_the_published_rule_beside_ours():
    rows = [_row("a", "k1", correct=True, score=1.0),
            _row("a", "k2", correct=True, category=5, reference_correct=False)]
    result = decompose(rows)
    assert result.adversarial["a"].accuracy == 1.0
    assert result.adversarial_reference["a"].accuracy == 0.0


def test_one_replicate_can_be_scored_on_its_own():
    rows = [_row("a", "k1", correct=True, score=1.0, replicate=0),
            _row("a", "k1", replicate=1)]
    assert decompose(rows, replicate=0).all_questions["a"].accuracy == 1.0
    assert decompose(rows, replicate=1).all_questions["a"].accuracy == 0.0


# --------------------------------------------------------- the gap it exposes


def test_a_gap_that_is_all_refusal_is_shown_as_all_refusal():
    """The measured failure this exists for: a +0.077 headline that was 72% one
    arm declining to answer."""
    rows = [
        _row("a", "k1", correct=True, score=1.0),
        _row("a", "k2", correct=True, score=1.0),
        _row("b", "k1", correct=True, score=1.0),
        _row("b", "k2", refused=True),
    ]
    result = decompose(rows)
    gap = gap_decomposition(result, "a", "b")
    assert gap.gap_all == pytest.approx(0.5)
    assert gap.gap_like_for_like == pytest.approx(0.0)
    assert gap.answer_rate_share == pytest.approx(1.0)


def test_a_zero_gap_has_no_share_rather_than_a_zero_share():
    rows = [_row("a", "k1", correct=True), _row("b", "k1", correct=True)]
    gap = gap_decomposition(decompose(rows), "a", "b")
    assert gap.gap_all == 0.0 and gap.answer_rate_share is None


def test_a_gap_needs_two_arms_that_ran():
    rows = [_row("a", "k1", correct=True)]
    assert gap_decomposition(decompose(rows), "a", "missing") is None


# ------------------------------------------------------------- the replicates


def test_one_replicate_reports_no_spread_rather_than_zero():
    """The spread of one number is not 0.0 — it is unmeasured, and printing 0.0
    would claim a reproducibility the run did not observe."""
    spread = replicate_spread([_row("a", "k1", correct=True)])["a"]
    assert spread.n == 1 and spread.sd is None and spread.spread is None


def test_three_replicates_report_mean_and_population_sd():
    rows = []
    for replicate, correct in enumerate((True, True, False)):
        rows.append(_row("a", "k1", correct=correct, replicate=replicate))
    spread = replicate_spread(rows)["a"]
    assert spread.values == (1.0, 1.0, 0.0)
    assert spread.mean == pytest.approx(2 / 3)
    assert spread.sd == pytest.approx(0.4714045, abs=1e-6)
    assert spread.spread == pytest.approx(1.0)


def test_the_spread_is_over_whole_run_accuracies():
    """Averaging the questions first would produce a tighter interval that
    describes a different experiment."""
    rows = [_row("a", "k1", correct=True, replicate=0),
            _row("a", "k2", correct=False, replicate=0),
            _row("a", "k1", correct=True, replicate=1),
            _row("a", "k2", correct=True, replicate=1)]
    assert replicate_spread(rows)["a"].values == (0.5, 1.0)


def test_grading_produces_one_row_per_answer():
    judge = DeterministicJudge()
    row = grade(judge, _question(), "teal", key="k", arm="a", replicate=0)
    assert row.verdict.correct and row.key == "k"
    assert row.as_dict()["label"] == "CORRECT"


def test_questions_are_keyed_by_position_not_text():
    """Twelve questions in the shipped file repeat a question already asked in
    the same conversation; keying on text would merge them."""
    first, second = _question(), _question()
    assert question_key(first, 3) != question_key(second, 9)


# --------------------------------------------------------------- the canary


def test_the_canary_passes_a_backbone_that_reads_its_evidence():
    assert runner.canary_backbone(lambda q, e: "teal") == 1


def test_a_backbone_returning_nothing_fails_the_canary():
    """The measured failure: None becomes "", is_refusal("") is True, and the
    run prints refusal_rate 1.000 with error_rate 0.000 — which on the
    adversarial category is a perfect score for a dead system."""
    with pytest.raises(DeadBackbone):
        runner.canary_backbone(lambda q, e: "")


def test_a_backbone_that_refuses_everything_fails_the_canary():
    with pytest.raises(DeadBackbone):
        runner.canary_backbone(lambda q, e: "I don't know.")


def test_a_backbone_that_raises_fails_the_canary():
    def explode(question, evidence):
        raise RuntimeError("no credit")

    with pytest.raises(DeadBackbone, match="raised on the canary"):
        runner.canary_backbone(explode)


def test_a_verbose_but_correct_backbone_passes():
    assert runner.canary_backbone(lambda q, e: "It was teal, I think.") == 1


def test_a_dead_backbone_is_not_a_skip():
    from evals.qa.run_qa_eval import Skip

    assert not issubclass(DeadBackbone, Skip)


# ---------------------------------------------------------------- the report


def _report(**kwargs) -> str:
    conversations = kwargs.pop("conversations", None)
    if conversations is None:
        conversations = load_conversations(_dataset(Path(kwargs.pop("tmp_path"))))
    return runner.build_report(conversations=conversations, **kwargs)


def _dataset(tmp_path: Path) -> Path:
    payload = [{
        "sample_id": "conv-test",
        "conversation": {
            "speaker_a": "Ada", "speaker_b": "Bo",
            "session_1_date_time": "1:56 pm on 8 May, 2023",
            "session_1": [
                {"dia_id": "D1:1", "speaker": "Ada", "text": "I bought a teal bike.",
                 "blip_caption": "a photo of a bicycle"},
            ],
            "session_2_date_time": "noon on 9 May, 2023",
            "session_2": [
                {"dia_id": "D2:1", "speaker": "Bo", "text": "Rode it to work."},
            ],
        },
        "qa": [
            {"question": "What colour was the bike?", "answer": "teal",
             "evidence": ["D1:1"], "category": 4},
            {"question": "What did Ada never buy?", "evidence": ["D2:1"],
             "category": 5, "adversarial_answer": "a canoe"},
        ],
    }]
    path = tmp_path / "locomo10.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_the_report_withholds_all_three_numbers_when_one_is_missing(tmp_path):
    conversations = load_conversations(_dataset(tmp_path))
    rows = [_row("a", "k1", refused=True), _row("b", "k1", correct=True)]
    text = runner.build_report(conversations=conversations,
                               decomposition=decompose(rows))
    assert "three numbers or none" in text
    assert "**Withheld.**" in text


def test_the_report_prints_all_three_when_they_exist(tmp_path):
    conversations = load_conversations(_dataset(tmp_path))
    rows = [_row("a", "k1", correct=True, score=1.0),
            _row("a", "k2", refused=True),
            _row("b", "k1", correct=True, score=1.0),
            _row("b", "k2", correct=True, score=1.0)]
    text = runner.build_report(conversations=conversations,
                               decomposition=decompose(rows))
    assert "n (all)" in text and "n (like-for-like)" in text and "refusals" in text


def test_the_comparable_table_is_withheld_while_the_judge_is_unmet(tmp_path):
    """Today's headline property: nothing on this machine can meet the judge
    control, so the published-comparable table must not print."""
    conversations = load_conversations(_dataset(tmp_path))
    rows = [_row("a", "k1", correct=True, score=1.0)]
    text = runner.build_report(
        conversations=conversations, decomposition=decompose(rows),
        meta={"judge": "deterministic", "llm_model": "",
              "evidence": {"llm_judge_calls": 0, "answer_calls": 1,
                           "canary_calls": 1}})
    assert "Withheld — see the controls below" in text
    assert "**UNMET**" in text
    # And it must not quote anybody else's number in its place.
    assert "92.5" in text.split("Withheld — see the controls below")[1][:800]


def test_the_adversarial_section_never_prints_without_its_warning(tmp_path):
    conversations = load_conversations(_dataset(tmp_path))
    rows = [_row("a", "k1", correct=True, score=1.0),
            _row("a", "k2", correct=True, category=5, refused=True)]
    text = runner.build_report(conversations=conversations,
                               decomposition=decompose(rows))
    assert "means nothing without the answerable numbers" in text


def test_the_report_is_byte_identical_for_the_same_inputs(tmp_path):
    """No timestamps: a report that changes at midnight cannot be diffed."""
    conversations = load_conversations(_dataset(tmp_path))
    rows = [_row("a", "k1", correct=True, score=1.0)]
    first = runner.build_report(conversations=conversations,
                                decomposition=decompose(rows))
    second = runner.build_report(conversations=conversations,
                                 decomposition=decompose(rows))
    assert first == second


def test_the_section_numbers_do_not_move_with_the_flags(tmp_path):
    """A report is quoted BY number, so an empty section keeps its heading."""
    conversations = load_conversations(_dataset(tmp_path))
    retrieval_only = runner.build_report(conversations=conversations)
    answered = runner.build_report(conversations=conversations,
                                   decomposition=decompose(
                                       [_row("a", "k1", correct=True)]))
    for heading in ("## 3. Retrieval", "## 4. Answer scoring",
                    "## 7. Published-comparable result", "## 8. Protocol controls"):
        assert heading in retrieval_only and heading in answered


def test_a_single_replicate_says_the_spread_is_unmeasured(tmp_path):
    conversations = load_conversations(_dataset(tmp_path))
    text = runner.build_report(conversations=conversations, replicates=1)
    assert "the spread of one number is not zero" in text


# ----------------------------------------------------------------- the guards


def test_ci_skips_before_anything_is_read(monkeypatch, capsys):
    monkeypatch.setenv("CI", "1")
    assert runner.main(["--data", "/does/not/exist.json"]) == 0
    assert "SKIP: CI is set" in capsys.readouterr().out


def test_a_missing_dataset_skips_with_the_command_that_fixes_it(monkeypatch, capsys):
    monkeypatch.delenv("CI", raising=False)
    assert runner.main(["--data", "/does/not/exist.json"]) == 0
    out = capsys.readouterr().out
    assert "SKIP:" in out and "snap-research/locomo" in out


def test_a_work_directory_inside_the_repo_skips(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CI", raising=False)
    repo = Path(__file__).resolve().parents[1]
    assert runner.main([
        "--data", str(_dataset(tmp_path)), "--conversations", "conv-test",
        "--work", str(repo / "scratch"), "--stage-only",
    ]) == 0
    assert "SKIP:" in capsys.readouterr().out
    assert not (repo / "scratch").exists()


def test_answering_without_consent_skips(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CI", raising=False)
    assert runner.main([
        "--data", str(_dataset(tmp_path)), "--conversations", "conv-test",
        "--work", str(tmp_path / "work"),
    ]) == 0
    out = capsys.readouterr().out
    assert "--i-know-this-costs-money" in out
    assert not (tmp_path / "work").exists()


def test_stage_only_writes_documents_and_compiles_nothing(monkeypatch, capsys,
                                                          tmp_path):
    monkeypatch.delenv("CI", raising=False)
    # If --stage-only ever compiled, this is the call it would make.
    monkeypatch.setattr("evals.locomo.adapter._default_compile", _forbidden)
    assert runner.main([
        "--data", str(_dataset(tmp_path)), "--conversations", "conv-test",
        "--work", str(tmp_path / "work"), "--stage-only",
    ]) == 0
    corpus = tmp_path / "work" / "conv-test" / "corpus"
    assert sorted(p.name for p in corpus.glob("*.md")) == [
        "session-0001.md", "session-0002.md"]
    assert not (tmp_path / "work" / "conv-test" / ".tesserae").exists()
    assert "NOTHING HAS BEEN COMPILED" in capsys.readouterr().out


def _forbidden(work):  # pragma: no cover - a compile here is the failure
    raise AssertionError("--stage-only must never compile")


def test_stage_only_without_the_tesserae_arm_refuses(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CI", raising=False)
    assert runner.main([
        "--data", str(_dataset(tmp_path)), "--arms", "bm25", "--stage-only",
        "--work", str(tmp_path / "work"),
    ]) == 0
    assert "SKIP:" in capsys.readouterr().out


def test_reuse_and_stage_only_contradict_each_other(monkeypatch, capsys, tmp_path):
    monkeypatch.delenv("CI", raising=False)
    assert runner.main([
        "--data", str(_dataset(tmp_path)), "--stage-only", "--reuse-compile",
        "--work", str(tmp_path / "work"),
    ]) == 0
    assert "contradict each other" in capsys.readouterr().out


def test_an_unknown_arm_refuses_rather_than_being_dropped(monkeypatch, capsys,
                                                          tmp_path):
    monkeypatch.delenv("CI", raising=False)
    assert runner.main([
        "--data", str(_dataset(tmp_path)), "--arms", "bm52",
        "--work", str(tmp_path / "work"),
    ]) == 0
    assert "no such arm" in capsys.readouterr().out


def test_the_retrieval_only_run_spends_nothing_and_scores_every_k(monkeypatch,
                                                                   capsys, tmp_path):
    """The whole free half of this benchmark, end to end.

    No backbone is constructed, no judge grades anything, and §4 says so — the
    result is the retrieval table, at every K in the frozen set.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(runner, "build_backbone", _forbidden)
    out_path = tmp_path / "report.md"
    assert runner.main([
        "--data", str(_dataset(tmp_path)), "--arms", "bm25",
        "--retrieval-only", "--work", str(tmp_path / "work"),
        "--out", str(out_path),
    ]) == 0
    text = out_path.read_text(encoding="utf-8")
    assert "## 3. Retrieval" in text
    for k in runner.PROTOCOL_KS:
        assert f"| bm25 | {k} |" in text
    assert "No arm answered a question in this run" in text
    assert "random floor" in text


def test_the_cost_banner_counts_documents_and_not_dollars(tmp_path):
    conversations = load_conversations(_dataset(tmp_path))
    banner = runner.cost_banner(conversations, replicates=3)
    assert "2 session documents" in banner
    assert "extraction calls" in banner
    assert "$" not in banner


def test_an_answering_run_goes_end_to_end_without_a_model(monkeypatch, tmp_path):
    """Canary, search, answer, grade, decompose, report — the whole path.

    The backbone and the search lane are stubs and the judge is the
    deterministic one, so this costs nothing and still exercises every step a
    paid run will take. What it proves is that the pieces are wired: a harness
    whose wiring can only be checked by spending money does not get checked.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("evals.locomo.adapter._default_compile", lambda work: None)

    class _Node:
        def __init__(self, name, source_path):
            self.name, self.source_path, self.description = name, source_path, ""

    class _Result:
        def __init__(self, nodes):
            self.scored = [type("S", (), {"node": n})() for n in nodes]
            self.total_matches = len(nodes)

    def _search(graph, question, **kwargs):
        root = kwargs.get("source_root")
        return _Result([_Node("Session 0001", str(root / "corpus" / "session-0001.md"))])

    monkeypatch.setattr(runner.LocomoMemory, "_resolve_graph", lambda self: object())
    monkeypatch.setattr(runner.LocomoMemory, "_resolve_search", lambda self: _search)
    monkeypatch.setattr(runner.LocomoMemory, "embedding_backend",
                        lambda self: type("B", (), {"name": "stub", "dim": 8})())
    # A backbone that answers the single-hop question and declines the
    # adversarial one — which is the correct behaviour on both.
    monkeypatch.setattr(runner, "build_backbone",
                        lambda model: lambda q, e: ("teal" if "colour" in q
                                                    else "I don't know."))

    out_path = tmp_path / "report.md"
    answers_path = tmp_path / "answers.json"
    assert runner.main([
        "--data", str(_dataset(tmp_path)), "--conversations", "conv-test",
        "--work", str(tmp_path / "work"), "--out", str(out_path),
        "--answers-out", str(answers_path),
        "--i-know-this-costs-money", "--yes",
    ]) == 0

    text = out_path.read_text(encoding="utf-8")
    assert "accuracy (all)" in text
    assert "**UNMET**" in text          # the judge control, today
    assert "Withheld — see the controls below" in text

    saved = json.loads(answers_path.read_text(encoding="utf-8"))
    assert saved["meta"]["evidence"] == {"answer_calls": 2, "llm_judge_calls": 0,
                                         "canary_calls": 1}
    assert {row["answer"] for row in saved["rows"]} == {"teal", "I don't know."}


def test_saved_answers_can_be_regraded_offline(monkeypatch, tmp_path):
    """The judge boundary, exercised: re-grading reads no backbone at all."""
    monkeypatch.delenv("CI", raising=False)
    data = _dataset(tmp_path)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({
        "meta": {"dataset_revision": None},
        "rows": [
            {"key": "conv-test#0", "arm": "tesserae", "replicate": 0,
             "conversation": "conv-test", "question_index": 0,
             "question": "What colour was the bike?", "category": 4,
             "answer": "teal"},
            {"key": "conv-test#1", "arm": "tesserae", "replicate": 0,
             "conversation": "conv-test", "question_index": 1,
             "question": "What did Ada never buy?", "category": 5,
             "answer": "I don't know."},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(runner, "build_backbone", _forbidden)
    out_path = tmp_path / "regraded.md"
    assert runner.main(["--data", str(data), "--score", str(answers),
                        "--out", str(out_path)]) == 0
    text = out_path.read_text(encoding="utf-8")
    assert "accuracy (all)" in text
    assert "scored apart" in text


def test_regrading_against_a_different_dataset_refuses(monkeypatch, capsys,
                                                       tmp_path):
    """Re-grading against a changed answer key silently changes every verdict."""
    monkeypatch.delenv("CI", raising=False)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({
        "meta": {"dataset_revision": "sha256:deadbeefdead"},
        "rows": [{"key": "conv-test#0", "arm": "a", "replicate": 0,
                  "conversation": "conv-test", "question_index": 0,
                  "question": "q", "category": 4, "answer": "teal"}],
    }), encoding="utf-8")
    assert runner.main(["--data", str(_dataset(tmp_path)),
                        "--score", str(answers)]) == 0
    assert "SKIP:" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# A broken run must not be able to report success
# ---------------------------------------------------------------------------


def test_a_retriever_that_never_returns_aborts_instead_of_scoring_zero():
    """A wholly dead retriever produced a complete, exit-0, byte-reproducible
    report with a clean 0.000-recall table on all 199 conv-26 questions.

    Recall of zero is a publishable claim about a memory. A retriever that never
    returned is not, and the report could not tell them apart — there was a
    mandatory canary for the backbone and none at all for retrieval.
    """
    from evals.locomo.run import _RETRIEVAL_CANARY, search_conversation

    class _Dead:
        def query_hits(self, *a, **k):
            raise RuntimeError("graph.json is truncated")
        def documents_of(self, hits):
            return []
        def answer_evidence(self, hits, **k):
            return []

    conv = _conversation_with(n_questions=_RETRIEVAL_CANARY + 4)
    with pytest.raises(RuntimeError, match="retrieval canary"):
        search_conversation(_Dead(), conv, k=10)


def test_a_retriever_that_degrades_partway_is_still_scored():
    """The canary guards TOTAL death, not difficulty. A retriever that works and
    then fails on some questions is a real result and must still be scored."""
    from evals.locomo.run import _RETRIEVAL_CANARY, search_conversation

    class _Flaky:
        def __init__(self): self.n = 0
        def query_hits(self, *a, **k):
            self.n += 1
            if self.n > _RETRIEVAL_CANARY:
                raise RuntimeError("degraded")
            return []
        def documents_of(self, hits): return []
        def answer_evidence(self, hits, **k): return []

    conv = _conversation_with(n_questions=_RETRIEVAL_CANARY + 4)
    documents, _evidence, errors = search_conversation(_Flaky(), conv, k=10)
    assert len(documents) == _RETRIEVAL_CANARY + 4
    assert sum(1 for e in errors if e) == 4


def test_the_shortfall_section_does_not_claim_completeness_it_cannot_see():
    """`shortfalls` increments only after a search RETURNS, so an empty list
    means "no returning search was short" — never "every query succeeded"."""
    from evals.locomo.run import _shortfall_section

    text = "\n".join(_shortfall_section([], 199, {}))
    assert "Every one of the" not in text
    assert "incremented on return" in text

    short = "\n".join(_shortfall_section([], 199, {}, n_searched=150))
    assert "49 of 199" in short
    assert "missing data" in short


def test_the_declining_phrase_is_accepted_by_both_graders():
    """A phrasing choice turned over an entire benchmark category.

    The reference grader accepts exactly "no information available" and "not
    mentioned"; ``evals.qa.scorer.is_refusal`` accepts those plus "I don't
    know". An answerer told to say "I don't know" therefore abstains correctly
    under our rule and wrongly under the published one — measured on conv-26's
    141 adversarial questions, the same answers scored 66.7% ours and 0.0%
    theirs.

    The prompt must name a phrase BOTH accept, so the score reflects whether the
    model abstained rather than which words it happened to use.
    """
    from evals.qa.scorer import is_refusal
    from evals.locomo.judge import _REFERENCE_ABSTENTION

    prompt = runner._SYSTEM_PROMPT
    phrase = prompt.rsplit("reply exactly:", 1)[1].strip().rstrip(".").strip()

    assert is_refusal(phrase), f"{phrase!r} is not a refusal under our own rule"
    assert any(a in phrase.casefold() for a in _REFERENCE_ABSTENTION), (
        f"{phrase!r} is not one of the reference grader's accepted phrases "
        f"{_REFERENCE_ABSTENTION} — an abstention in this wording scores zero "
        f"on the adversarial category however correct the decision was"
    )


def test_the_prompt_permits_inference_and_still_requires_abstention():
    """Abstention is for UNSUPPORTED, not for UNSTATED.

    An extraction-only instruction ("use exact words from the evidence", decline
    when "the evidence does not contain the answer") made the model refuse 21 of
    21 open-domain questions whose gold session it had retrieved. That category
    is inference — "Would Caroline likely have Dr. Seuss books?" -> "Yes, since
    she collects classic children's books" — so the answer is entailed by the
    evidence and appears nowhere in it verbatim.

    Both halves must hold: reasoning from evidence is permitted, and abstention
    on genuinely unsupported questions is still demanded.
    """
    prompt = runner._SYSTEM_PROMPT.casefold()

    assert "reason from it" in prompt, "inference is not permitted"
    assert "likely" in prompt or "implied" in prompt, "inference is not named"
    assert "supports no answer" in prompt, "abstention is no longer required"
    assert "not mentioned" in prompt, "the accepted declining phrase is gone"

    # The instruction that caused the 21/21 failure must not come back.
    assert "does not contain the answer" not in prompt, (
        "an extraction-only abstention rule refuses every inference question"
    )

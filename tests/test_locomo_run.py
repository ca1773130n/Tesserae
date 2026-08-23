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
    # ``**_`` because ``build_backbone`` grew ``modal_gate``: a stub pinned to
    # the old arity would fail on the wiring rather than on the behaviour it
    # exists to check.
    monkeypatch.setattr(runner, "build_backbone",
                        lambda model, **_: lambda q, e: ("teal" if "colour" in q
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
    assert saved["meta"]["evidence"] == {"answer_calls": 2, "empty_replies": 0,
                                         "llm_judge_calls": 0, "canary_calls": 1}
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
    regraded = tmp_path / "regraded.json"
    assert runner.main(["--data", str(data), "--score", str(answers),
                        "--out", str(out_path),
                        "--answers-out", str(regraded)]) == 0
    text = out_path.read_text(encoding="utf-8")
    assert "accuracy (all)" in text
    assert "scored apart" in text

    # Re-grading is the cheap way to get per-question verdicts — no backbone, no
    # retrieval — so it is the last path that should drop them. It did: the fold
    # ran only on the answering path, so the one route to a decomposition that
    # costs nothing threw the labels away and printed aggregates.
    saved = json.loads(regraded.read_text(encoding="utf-8"))
    assert [row["label"] for row in saved["rows"]] == ["CORRECT", "ABSTAINED"]
    assert saved["rows"][0]["answer"] == "teal"


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


def _answering(replies):
    """A backbone that serves ``replies`` in order and fails loudly if
    over-called — a silent StopIteration would be caught as a backbone error and
    read as the very failure these tests are about."""
    remaining = list(replies)

    def _fn(question, items):
        assert remaining, "the backbone was called more times than expected"
        return remaining.pop(0)

    return _fn, remaining


def _one_conversation(n=1):
    from evals.locomo.dataset import Conversation
    return Conversation(sample_id="conv-test", speaker_a="A", speaker_b="B",
                        sessions=[], questions=[_question() for _ in range(n)])


def test_an_empty_backbone_reply_is_retried_once_then_recorded_as_an_error():
    """A LOST CALL was being counted as a DECISION.

    ``is_refusal("")`` is True, and correct as a scorer predicate — a system that
    returns nothing has declined. But the backbone returning nothing is the
    harness losing a call, not the memory choosing to abstain, and the two were
    indistinguishable in every reported number. Measured on the 2026-08-22
    conv-26 run, ``gpt-5.6-luna`` returned the empty string on 66 of 398
    answering calls (16.6%), every one of which the old code filed as a refusal.
    """
    from evals.qa.scorer import is_error, is_refusal

    answer_fn, remaining = _answering(["", "   "])
    (row,) = runner.answer_conversation(
        _one_conversation(), [["ev"]], [""], answer_fn,
        replicate=0, progress=False)

    assert row["answer"] == runner._EMPTY_ANSWER
    assert is_error(row["answer"]) and not is_refusal(row["answer"])
    assert row["empty_replies"] == 2, "the failure count is not persisted"
    assert not remaining, "the retry did not happen"


def test_a_retry_that_answers_rescues_the_question_and_still_counts_the_failure():
    """The empties are near-independent per call — 33 in each replicate of the
    2026-08-22 run and only 7 in both, against 5.5 expected by chance — which is
    the shape a retry is for. Counting the failure anyway is what keeps the
    provider's rate a reported number rather than one the retry hid."""
    answer_fn, remaining = _answering(["", "teal"])
    (row,) = runner.answer_conversation(
        _one_conversation(), [["ev"]], [""], answer_fn,
        replicate=0, progress=False)

    assert row["answer"] == "teal"
    assert row["empty_replies"] == 1
    assert not remaining


def test_a_search_failure_costs_no_backbone_call_at_all():
    """A non-regression pin. The retry loop sits inside the branch that runs only
    when the search succeeded; a misplaced one would spend two calls per
    traceback."""
    answer_fn, _ = _answering([])
    (row,) = runner.answer_conversation(
        _one_conversation(), [[]], ["Error: the retriever fell over"], answer_fn,
        replicate=0, progress=False)
    assert row["answer"] == "Error: the retriever fell over"
    assert row["empty_replies"] == 0


def test_a_real_answer_is_left_exactly_as_the_backbone_wrote_it():
    """The empty-reply guard must not become an answer rewriter."""
    from evals.locomo.dataset import Conversation

    conversation = Conversation(sample_id="conv-test", speaker_a="A",
                                speaker_b="B", sessions=[], questions=[_question()])
    (row,) = runner.answer_conversation(
        conversation, [["ev"]], [""], lambda q, e: "  teal  ",
        replicate=0, progress=False)
    assert row["answer"] == "  teal  "


def test_the_prompt_requires_a_relative_time_expression_to_be_resolved():
    """"Prefer the evidence's own words" was answering WHEN with "Yesterday".

    Every evidence item now carries the date of the session it came from, so the
    anchor a deictic expression needs is in the prompt; without a rule naming it
    the model copies the deixis instead. Measured on the 45 wrong answers of the
    2026-08-21 conv-26 run, 13 (28.9%) — the largest single class — are a
    relative expression where the gold is a calendar date, and 3 of those 13
    already had the session's own date pasted into the prompt.

    The rule must stay narrow: it fires on time, and it must not reopen the
    extraction-only wording that refused 21 of 21 open-domain questions.
    """
    prompt = runner._SYSTEM_PROMPT.casefold()

    assert "session date" in prompt, "the stamp the answer must resolve against is not named"
    assert "calendar date" in prompt, "a when question is not asked for a date"
    assert "yesterday" in prompt, "the deixis to resolve is not named"
    assert "reason from it" in prompt and "supports no answer" in prompt, (
        "the time rule was written over inference or abstention"
    )


def test_the_saved_answers_carry_the_judges_verdict():
    """One field, and without it the report's decomposition cannot be re-joined.

    The saved answers carried the answer, its category and its evidence size; the
    verdicts lived only inside the printed aggregates. So a report could say
    "32% wrong with the gold retrieved" and nobody could list WHICH 32% without
    paying for a re-grade — and grepping the judge log finds CORRECT and WRONG
    inside the judge's own instruction text rather than its answers. Reading the
    failures is how the defects this commit fixes were found.
    """
    rows = [{"answer": "teal", "question_index": 0, "conversation": "conv-test"},
            {"answer": "Not mentioned.", "question_index": 1,
             "conversation": "conv-test"}]
    graded = [grade(DeterministicJudge(), _question(), row["answer"],
                    key=f"k{i}", arm="tesserae", replicate=0)
              for i, row in enumerate(rows)]

    runner._fold_verdicts(rows, graded)

    assert [row["label"] for row in rows] == ["CORRECT", "WRONG"]
    assert rows[0]["correct"] is True and rows[1]["refused"] is True
    # The row's own identity is never overwritten by the verdict's copy of it.
    assert rows[0]["question_index"] == 0 and "key" not in rows[0]


def test_folding_a_verdict_list_that_does_not_line_up_raises():
    """``_grade_rows`` returns one verdict per answer, in order. A short zip
    would label the first rows and leave the rest looking ungraded."""
    with pytest.raises(RuntimeError, match="no longer positional"):
        runner._fold_verdicts(
            [{"answer": "teal"}, {"answer": "blue"}],
            [grade(DeterministicJudge(), _question(), "teal",
                   key="k", arm="tesserae", replicate=0)])


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


def test_tiered_evidence_and_prefer_anchor_text_refuse_each_other(monkeypatch,
                                                                  tmp_path):
    """A pair that would silently produce the failure tiering exists to prevent.

    `--prefer-anchor-text` rewrites EVERY hit to its session's anchor, and
    SourceDocument/Session nodes carry no `evidenced_by` edges at all — measured,
    it drives provenance to exactly 0.000. It is not gated on `--fanout`, so
    absent this refusal it composes with anything. A hard exit rather than a
    Skip: a Skip returns 0, which reads as a run that measured something.
    """
    monkeypatch.delenv("CI", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        runner.main([
            "--data", str(_dataset(tmp_path)), "--work", str(tmp_path / "work"),
            "--tiered-evidence", "--prefer-anchor-text",
        ])
    assert exit_info.value.code == 2


def test_the_tiered_knobs_read_zero_when_the_stage_is_off():
    """False/0 rather than an omitted key, on `--fanout`'s terms: silent meta
    predates the stage, and 0 says the run did not use the knob."""
    args = runner.build_parser().parse_args([])
    assert args.tiered_evidence is False
    assert (args.evidence_receipt_chars, args.receipt_window) == (8_000, 0)


def test_a_tiered_run_puts_the_receipt_in_the_prompt_and_the_spend_in_the_meta(
        monkeypatch, tmp_path):
    """`--tiered-evidence` end to end: flag -> memory -> prompt -> artifact.

    The retrieved fact lives in session 2 and its `evidenced_by` span points at
    a turn of session 1, so the receipt is a turn tier 3 does NOT paste — which
    is the whole case the tier exists for. A harness whose wiring can only be
    checked by spending money does not get checked, so the backbone and the
    search lane are stubs and this costs nothing.

    Verified to fail on every assertion below with the flag removed.
    """
    from tesserae.research_graph import (
        ResearchEdge,
        ResearchGraph,
        ResearchNode,
        ResearchNodeType,
    )

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("evals.locomo.adapter._default_compile", lambda work: None)

    def _graph_of(memory):
        corpus = memory.work / "corpus"
        return ResearchGraph(
            nodes=[
                ResearchNode(id="Claim:bike", name="Ada owns a teal bike",
                             type=ResearchNodeType.CLAIM,
                             description="Ada bought a bike and rode it to work.",
                             source_path=str(corpus / "session-0002.md")),
                ResearchNode(id="EvidenceSpan:D1:1", name="D1:1 evidence",
                             type=ResearchNodeType.EVIDENCE_SPAN, description="",
                             source_path=str(corpus / "session-0001.md"),
                             metadata={"turn": "D1:1", "speaker": "Ada"}),
            ],
            edges=[ResearchEdge(source="Claim:bike", target="EvidenceSpan:D1:1",
                                type="evidenced_by")],
        )

    class _Result:
        def __init__(self, nodes):
            self.scored = [type("S", (), {"node": n})() for n in nodes]
            self.total_matches = len(nodes)

    monkeypatch.setattr(runner.LocomoMemory, "_resolve_graph", _graph_of)
    monkeypatch.setattr(runner.LocomoMemory, "_resolve_search",
                        lambda self: lambda graph, q, **kw: _Result(graph.nodes[:1]))
    monkeypatch.setattr(runner.LocomoMemory, "embedding_backend",
                        lambda self: type("B", (), {"name": "stub", "dim": 8})())
    prompts: List[str] = []

    def _backbone(model, **_):
        def _answer(question, evidence):
            prompts.append("\n\n".join(evidence))
            return "teal"
        return _answer

    monkeypatch.setattr(runner, "build_backbone", _backbone)

    answers_path = tmp_path / "answers.json"
    assert runner.main([
        "--data", str(_dataset(tmp_path)), "--conversations", "conv-test",
        "--work", str(tmp_path / "work"), "--answers-out", str(answers_path),
        "--out", str(tmp_path / "report.md"), "--tiered-evidence",
        "--i-know-this-costs-money", "--yes",
    ]) == 0

    prompt = prompts[-1]
    assert "[D1:1]" in prompt, "the receipt tier emitted nothing"
    assert "teal bike" in prompt, "session 2's own text stopped being pasted"
    assert " — source: " not in prompt, "the absolute path survived the strip"

    meta = json.loads(answers_path.read_text(encoding="utf-8"))["meta"]
    assert meta["tiered_evidence"] is True
    assert meta["evidence_receipt_chars"] == 8_000 and meta["receipt_window"] == 0
    assert meta["receipt_lines"] >= 1 and meta["receipt_chars"] > 0
    assert meta["witness_yield"] == 1.0
    assert meta["unresolvable_spans"] == 0 and meta["dangling_receipts"] == 0


# ------------------------------------------------------------- the modal gate


#: Real LoCoMo questions, one per category, frozen here so the router has a
#: ratchet that does not depend on the 350 MB dataset being on disk. The
#: dataset-wide leak check below is the real falsifier; this is the one that
#: still runs when it is skipped.
_ROUTER_CASES = [
    # category 3 — dispositional, the branch's whole population
    ("Would Melanie be considered a member of the LGBTQ community?", True),
    ("Would Caroline likely have Dr. Seuss books?", True),
    ("Would Joanna prefer a quiet evening?", True),
    ("Might Nate take up running again?", True),
    ("Is Melanie probably religious?", True),
    # category 3 — factual questions the benchmark happens to label 3, which
    # the router deliberately does NOT claim
    ("What console does Nate own?", False),
    ("What nickname does Nate use for Joanna?", False),
    # category 5 — adversarial. NOT ONE of these may route dispositional.
    ("What was grandma's gift to Melanie?", False),
    ("Where did Oscar hide his bone once?", False),
    ("Did Caroline make the black and white bowl in the photo?", False),
    ("How did Caroline feel while watching the meteor shower?", False),
    ("What is Melanie excited about in her adoption process?", False),
    # categories 1 / 2 / 4
    ("What state did Joanna visit in summer 2021?", False),
    ("When did Ada buy the bike?", False),
]


@pytest.mark.parametrize("question,expected", _ROUTER_CASES)
def test_the_router_reads_modality_and_nothing_else(question, expected):
    """Computed on the QUESTION STRING ALONE, before retrieval.

    That is what makes it orthogonal to evidence sufficiency. The failed edit
    gated on how much the evidence "bears on" the question — an axis BOTH
    open-domain and adversarial sit low on — and moved open-domain refusals
    54% -> 0% while moving adversarial 72% -> 49%: +7 questions against -11.
    Modality cannot move when retrieval quality changes.
    """
    assert runner.dispositional_question(question) is expected


def test_no_adversarial_question_in_the_benchmark_routes_dispositional():
    """KILL CONDITION 1, and it costs nothing to run.

    The claim is that the two populations are lexically DISJOINT on this
    feature, not that they rarely overlap, so a SINGLE leaked adversarial
    question out of 446 falsifies the design. Measured today over all 1,986
    questions: 0 of 446 adversarial and 0 of 1,444 on categories 1/2/4, with
    38 of 96 open-domain routed (25 of 83 on the nine conversations held out
    from the rule's design).
    """
    if not runner.DEFAULT_DATA.is_file():
        pytest.skip(f"{runner.DEFAULT_DATA} is not on this machine; the frozen "
                    f"_ROUTER_CASES above still pin the rule")
    leaked: Dict[int, List[str]] = {}
    routed = total = 0
    for conversation in load_conversations(runner.DEFAULT_DATA):
        for question in conversation.questions:
            disposed = runner.dispositional_question(question.question)
            if question.category == 3:
                total += 1
                routed += int(disposed)
            elif disposed:
                leaked.setdefault(question.category, []).append(question.question)
    assert leaked == {}, (
        f"the router leaked into non-open-domain categories: "
        f"{ {k: v[:3] for k, v in leaked.items()} }. The design's whole "
        f"separation claim is that this set is empty — DO NOT widen "
        f"_DISPOSITIONAL_MODALS to recover open-domain recall; adding "
        f"{{attributes, personality, traits, describe}} was measured at "
        f"+0.010 recall for 0.0224 adversarial leakage."
    )
    assert routed == 38 and total == 96, (
        f"open-domain routing moved to {routed}/{total} from 38/96. That is "
        f"not a failure on its own, but every projection in this design is "
        f"arithmetic over that denominator."
    )


def test_the_gate_off_is_the_shipped_prompt_for_every_question():
    """The opt-in guarantee. An A/B on ``_SYSTEM_PROMPT`` is in flight on this
    branch, and a run that silently answered under a different rule would
    corrupt it."""
    for question, _ in _ROUTER_CASES:
        assert runner.system_for(question, modal_gate=False) is runner._SYSTEM_PROMPT


def test_the_dispositional_branch_contains_no_abstention_string_at_all():
    """Abstention Inflation (arXiv:2507.16199v6) finds the inflation is
    STRUCTURAL, not semantic — the presence of a decline option raises
    declining however it is worded — so only literal absence works. This is the
    mechanism, not a style rule.
    """
    text = runner._DISPOSITIONAL_SYSTEM.lower()
    for marker in ("not mentioned", "decline", "refus", "insufficient",
                   "unanswerable", "cannot answer", "don't know",
                   "do not know", "unknown", "no answer"):
        assert marker not in text, (
            f"{marker!r} is in the dispositional prompt. Its 11 refusals are "
            f"caused by the structural presence of an abstention option; "
            f"rewording one back in reinstates the failure."
        )


def test_the_event_branch_refuses_in_the_shipped_words():
    """THE CONTROL. An EVENT branch that refuses in different words than the
    arm it is compared against is a prompt rewrite wearing a router's clothes.

    The refusal sentence is copied from ``_BOTH_BRANCHES_RULE`` character for
    character. That prompt is under active A/B on this branch and its wording
    has already moved once mid-edit — if this goes red, update ``_EVENT_RULE``
    to match it rather than relaxing the assertion.
    """
    shipped = runner._BOTH_BRANCHES_RULE
    sentence = shipped[shipped.rindex("If the evidence supports no answer"):]
    assert sentence and sentence in runner._EVENT_SYSTEM
    assert "Not mentioned." in sentence, (
        "the refusal phrase left the shipped prompt. It is the one BOTH the "
        "published grader's abstention rule and ours accept, and an earlier "
        "run turned an entire category over on that choice: the same answers "
        "scored 66.7% under our rule and 0.0% under the published one."
    )


def test_neither_branch_carries_the_other_and_both_carry_the_head():
    """The separation IS the design: the dispositional instruction is not
    softened for adversarial questions, it is never shown to them. Both keep
    the formatting head verbatim, so no arm can win by being told to answer
    more tersely."""
    head = runner._ANSWER_FORMAT_RULES
    assert runner._SYSTEM_PROMPT.startswith(head)
    assert runner._DISPOSITIONAL_SYSTEM.startswith(head)
    assert runner._EVENT_SYSTEM.startswith(head)
    assert runner._DISPOSITIONAL_RULE not in runner._EVENT_SYSTEM
    assert runner._EVENT_RULE not in runner._DISPOSITIONAL_SYSTEM
    # Token-neutral to marginally token-NEGATIVE. It neither funds nor
    # obstructs the packing budget and must not be credited with either.
    assert len(runner._DISPOSITIONAL_SYSTEM) < len(runner._SYSTEM_PROMPT)
    assert len(runner._EVENT_SYSTEM) < len(runner._SYSTEM_PROMPT)


def test_the_gate_hands_each_question_its_own_branch():
    assert runner.system_for("Would she likely say yes?", modal_gate=True) == \
        runner._DISPOSITIONAL_SYSTEM
    assert runner.system_for("What was the gift?", modal_gate=True) == \
        runner._EVENT_SYSTEM


def test_the_backbone_sends_the_branch_the_router_chose(monkeypatch):
    """The wiring, checked without an LLM client — the thing a paid run would
    otherwise be the only way to check."""
    seen: List[str] = []

    class _Client:
        def complete_json(self, *, system, user, schema_name):
            seen.append(system)
            return {"answer": "ok"}

    monkeypatch.setattr("tesserae.llm_json.build_default_json_client",
                        lambda model: _Client())

    gated = runner.build_backbone("m", modal_gate=True)
    gated("Would she likely say yes?", ["e"])
    gated("What was the gift?", ["e"])
    assert seen == [runner._DISPOSITIONAL_SYSTEM, runner._EVENT_SYSTEM]

    seen.clear()
    plain = runner.build_backbone("m")
    plain("Would she likely say yes?", ["e"])
    assert seen == [runner._SYSTEM_PROMPT]


def test_the_row_records_the_branch_only_when_the_gate_ran(tmp_path):
    """"" is not the same claim as "event": one says the question was never
    routed, the other says it was and landed there. The branch is persisted so
    the router is auditable in the answers file rather than re-derivable from a
    regex someone has to trust."""
    conversation = load_conversations(_dataset(tmp_path))[0]
    evidence = [["e"], ["e"]]
    errors = ["", ""]

    off = runner.answer_conversation(conversation, evidence, errors,
                                     lambda q, e: "x", replicate=0,
                                     progress=False)
    assert [row["branch"] for row in off] == ["", ""]

    on = runner.answer_conversation(conversation, evidence, errors,
                                    lambda q, e: "x", replicate=0,
                                    modal_gate=True, progress=False)
    assert [row["branch"] for row in on] == ["event", "event"]
    assert all(runner.dispositional_question(row["question"]) is False
               for row in on)


# --------------------------------------------------- the turn pack, wired up


def test_the_turn_unit_and_tiering_are_refused_together(capsys):
    """Tier 2 IS a degenerate turn pack, and tier 3 spends a budget the pack
    does not account for. Refused at the parser, like the other incoherent
    pair, because a Skip exits 0 and reads as a run that measured something."""
    with pytest.raises(SystemExit):
        runner.main(["--evidence-unit", "turn", "--tiered-evidence"])
    assert "mutually exclusive" in capsys.readouterr().err


def test_the_pack_settings_default_to_the_shipped_session_unit():
    args = runner.build_parser().parse_args([])
    assert args.evidence_unit == "session"
    assert args.turn_pool == "retrieved"
    assert args.turn_heads == "none"
    assert args.modal_gate is False
    assert args.evidence_pack_chars == runner.EVIDENCE_PACK_CHARS
    assert args.turn_emit_window == runner.TURN_EMIT_WINDOW


def test_a_turn_unit_run_declares_its_budget_and_its_spend(monkeypatch,
                                                           tmp_path):
    """An arm's budget is declared in its OWN record. A coverage number without
    the character cost it was bought at is unreadable, and this branch has
    already lost two thirds of one headline to a missing budget-matched
    control.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("evals.locomo.adapter._default_compile", lambda work: None)

    class _Node:
        def __init__(self, name, source_path):
            self.name, self.source_path, self.description = name, source_path, ""
            self.id = "SourceDocument:1"

    class _Result:
        def __init__(self, nodes):
            self.scored = [type("S", (), {"node": n})() for n in nodes]
            self.total_matches = len(nodes)

    def _search(graph, question, **kwargs):
        root = kwargs.get("source_root")
        return _Result([_Node("Session 0001",
                              str(root / "corpus" / "session-0001.md"))])

    monkeypatch.setattr(runner.LocomoMemory, "_resolve_graph", lambda self: object())
    monkeypatch.setattr(runner.LocomoMemory, "_resolve_search", lambda self: _search)
    monkeypatch.setattr(runner.LocomoMemory, "_receipt_index", lambda self: {})
    # The dense lane is weighted 0.5 by default, so this run resolves a backend
    # and calls it. A stub keeps the case on a normal install: no model, no
    # torch, no network.
    class _Backend:
        name, dim = "stub", 2

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(runner.LocomoMemory, "embedding_backend",
                        lambda self: _Backend())
    monkeypatch.setattr(runner, "build_backbone",
                        lambda model, **_: lambda q, e: "teal")

    answers = tmp_path / "answers.json"
    assert runner.main([
        "--data", str(_dataset(tmp_path)), "--conversations", "conv-test",
        "--work", str(tmp_path / "work"), "--out", str(tmp_path / "report.md"),
        "--answers-out", str(answers),
        "--evidence-unit", "turn", "--evidence-pack-chars", "400",
        "--turn-pool", "corpus", "--turn-heads", "fact", "--modal-gate",
        "--i-know-this-costs-money", "--yes",
    ]) == 0

    meta = json.loads(answers.read_text(encoding="utf-8"))["meta"]
    assert meta["evidence_unit"] == "turn"
    assert meta["evidence_pack_chars"] == 400
    assert meta["turn_pool"] == "corpus"
    assert meta["turn_heads"] == "fact"
    assert meta["turn_score_window"] == runner.TURN_SCORE_WINDOW
    assert meta["modal_gate"] is True
    assert 0 < meta["pack_chars"] <= 400 * 2
    assert meta["pack_turns"] >= 1 and meta["pack_sessions"] >= 1

    rows = json.loads(answers.read_text(encoding="utf-8"))["rows"]
    assert {row["branch"] for row in rows} == {"event"}
    assert all(0 < row["evidence_chars"] <= 400 for row in rows)


def test_a_session_unit_run_says_so_rather_than_going_silent(monkeypatch,
                                                             tmp_path):
    """"session"/0 rather than an omitted key, on the same terms as `fanout`: a
    result whose meta is silent about the unit predates the stage, and one that
    says "session" pasted whole sessions the shipped way."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr("evals.locomo.adapter._default_compile", lambda work: None)
    monkeypatch.setattr(runner, "build_backbone",
                        lambda model, **_: lambda q, e: "teal")
    monkeypatch.setattr(runner.LocomoMemory, "_resolve_graph", lambda self: object())
    monkeypatch.setattr(
        runner.LocomoMemory, "_resolve_search",
        lambda self: lambda graph, question, **kwargs: type(
            "R", (), {"scored": [], "total_matches": 0})())
    monkeypatch.setattr(runner.LocomoMemory, "embedding_backend",
                        lambda self: type("B", (), {"name": "stub", "dim": 8})())

    answers = tmp_path / "answers.json"
    assert runner.main([
        "--data", str(_dataset(tmp_path)), "--conversations", "conv-test",
        "--work", str(tmp_path / "work"), "--out", str(tmp_path / "report.md"),
        "--answers-out", str(answers),
        "--i-know-this-costs-money", "--yes",
    ]) == 0
    meta = json.loads(answers.read_text(encoding="utf-8"))["meta"]
    assert meta["evidence_unit"] == "session"
    assert meta["evidence_pack_chars"] == 0
    assert meta["turn_pool"] == "" and meta["turn_heads"] == ""
    assert meta["turn_emit_window"] == meta["turn_score_window"] == 0
    assert meta["modal_gate"] is False
    assert meta["pack_chars"] == meta["pack_turns"] == meta["pack_sessions"] == 0


def test_the_prompt_cuts_by_what_is_asked_not_by_how_much_evidence():
    """The distinction that separates open-domain from adversarial.

    Two kinds of question need opposite treatment and a single sufficiency
    threshold cannot serve both: an open-domain question has evidence that
    implies but does not state its answer, an adversarial question has topically
    related evidence that supports nothing, so a rule gated on how much the
    evidence "bears on" the question moves both the same way. Measured when
    exactly that was tried: open-domain refusal 54% -> 0% AND adversarial
    72% -> 49%, +7 questions against -11.

    Cutting on WHAT IS ASKED FOR instead moves them in opposite directions.
    Measured on conv-26, three replicates of both arms, 360 backbone calls:

        open-domain refusal  48.7% -> 10.3%   (spread 7.7 / 7.7)
        adversarial refusal  62.4% -> 79.4%   (spread 6.4 / 2.1)

    Every replicate of the new prompt beat every replicate of the old on BOTH
    axes, and the effects are five times the spreads.
    """
    prompt = runner._SYSTEM_PROMPT.casefold()

    assert "asked for" in prompt, "the cut is not stated"
    assert "character" in prompt and "would probably do" in prompt, (
        "the dispositional case is not named, so it falls back to the "
        "sufficiency reading that moved both categories together"
    )
    assert "specific event" in prompt, "the event case is not named"
    assert "does not establish it" in prompt, (
        "without this, topically related evidence reads as establishment and "
        "adversarial questions get answered"
    )
    # The two rules the earlier failures produced must not come back.
    assert "bears on" not in prompt, "the refuted sufficiency gate returned"
    assert "does not contain the answer" not in prompt, (
        "an extraction-only abstention rule refuses every inference question"
    )

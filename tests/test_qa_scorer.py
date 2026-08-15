"""The scorer is the artifact, so the scorer is what gets tested.

Every metric below is checked against a hand-built fixture whose expected value
was worked out by hand and written into the assertion — known answers in, known
numbers out. No LLM, no corpus, no compile, no network: if the tests needed any
of those they would be skipped in practice and the scorer would ship unmeasured,
which is the exact failure this harness exists to fix.

The awkward cases get their own tests because they are where a QA scorer is
usually wrong: an empty answer, an answer that refuses, a harness error that
looks like an answer, a tie between gold aliases, and a system that scores
identically to another.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from evals.metrics import prf1
from evals.qa import scorer
from evals.qa.scorer import (
    exact_match,
    fairness_blockers,
    is_error,
    is_refusal,
    normalize_answer,
    rank_systems,
    score_row,
    score_system,
    summarize,
    token_f1,
    tokenize,
)

# --------------------------------------------------------------- shared metric


def test_prf1_conventions():
    """The empty-denominator conventions the federation eval already shipped."""
    assert prf1(0, 0, 0) == {"tp": 0, "fp": 0, "fn": 0,
                             "precision": 0.0, "recall": 0.0, "f1": 0.0}
    assert prf1(3, 1, 1)["precision"] == pytest.approx(0.75)
    assert prf1(3, 1, 1)["recall"] == pytest.approx(0.75)
    assert prf1(3, 1, 1)["f1"] == pytest.approx(0.75)
    # Predicting nothing is not perfect precision.
    assert prf1(0, 0, 5)["precision"] == 0.0
    assert prf1(0, 5, 0)["recall"] == 0.0


def test_federation_and_qa_share_one_f1_implementation():
    """A second copy of this arithmetic is how two evals stop being comparable."""
    pytest.importorskip("model2vec")
    from evals.federation import run_eval

    assert run_eval.prf1 is prf1
    assert scorer.prf1 is prf1


# ------------------------------------------------------------------ primitives


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("The Longest Yard", "longest yard"),
        ("Brooklyn, New York", "brooklyn new york"),
        ("  MULTIPLE   spaces\t", "multiple spaces"),
        ("gpt-5.4", "gpt 5 4"),
        ("café", "cafe"),
        ('"Dr. Death" Steve Williams', "dr death steve williams"),
        (None, ""),
        ("", ""),
        ("a an the", ""),
    ],
)
def test_normalize_answer(raw, expected):
    assert normalize_answer(raw) == expected


def test_punctuation_becomes_a_space_not_nothing():
    """Deleting punctuation would fuse '1,000' into '1000' and split nothing —
    the wrong trade in both directions. Splitting is the documented choice."""
    assert tokenize("1,000") == ["1", "000"]
    assert tokenize("co-slam") == ["co", "slam"]


def test_exact_match_is_normalized():
    assert exact_match("The Longest Yard", "the longest yard")
    assert exact_match("  scotland.  ", "Scotland")
    assert not exact_match("scotland", "england")
    assert exact_match("", None), "two empty answers agree"


def test_token_f1_perfect_and_partial():
    perfect = token_f1("Brooklyn, New York", "brooklyn new york")
    assert (perfect["tp"], perfect["fp"], perfect["fn"]) == (3, 0, 0)
    assert perfect["f1"] == pytest.approx(1.0)

    partial = token_f1("New York City", "Brooklyn, New York")
    assert (partial["tp"], partial["fp"], partial["fn"]) == (2, 1, 1)
    assert partial["f1"] == pytest.approx(2 / 3)


def test_token_f1_is_a_multiset_not_a_set():
    """Repeating a gold token buys credit once and costs a false positive —
    otherwise 'york york york' scores a perfect 1.0 against 'york'."""
    repeated = token_f1("york york york", "york")
    assert (repeated["tp"], repeated["fp"], repeated["fn"]) == (1, 2, 0)
    assert repeated["precision"] == pytest.approx(1 / 3)
    assert repeated["recall"] == pytest.approx(1.0)
    assert repeated["f1"] == pytest.approx(0.5)


def test_token_f1_empty_answers():
    """The one case prf1 cannot decide, decided here and pinned."""
    assert token_f1("", "kansas")["f1"] == 0.0, "no credit for silence"
    assert token_f1(None, "kansas")["f1"] == 0.0
    assert token_f1("", "")["f1"] == pytest.approx(1.0), "two empty answers agree"
    assert token_f1("kansas", "")["f1"] == 0.0


@pytest.mark.parametrize(
    "text",
    ["I don't know", "i do not know.", "I'm not sure.", "I am not sure",
     "The provided context does not contain that information.",
     "There is no information about this in the documents.",
     "That is not mentioned.", "Unanswerable.", "", "   ", None],
)
def test_is_refusal_true(text):
    assert is_refusal(text)


@pytest.mark.parametrize("text", ["scotland", "28 January 1864", "no", "16,825"])
def test_is_refusal_false(text):
    assert not is_refusal(text)


def test_yes_no_answers_are_not_refusals():
    """'no' is a legitimate gold answer in this question set (question 16).
    A refusal detector that swallows it would silently zero a correct answer."""
    assert not is_refusal("no")
    assert token_f1("no", "no")["f1"] == pytest.approx(1.0)


def test_a_hedged_answer_is_counted_as_a_refusal_but_still_scores():
    """The documented substring limitation, pinned so it stays known. The refusal
    rate over-counts here; exact match and F1 do not, so the answer still gets
    the credit it earned."""
    hedged = score_row({"question": "q", "answer": "I do not know for certain, but Scotland",
                        "gold": "scotland"})
    assert hedged["refused"] is True, "substring match — an upper bound, not a lie"
    assert hedged["f1"] > 0.0, "the correct token still scores"


def test_harness_error_is_neither_an_answer_nor_a_refusal():
    """The vendored ABC records a crashed query as 'Error: ...'. Counting that
    as a refusal would let a broken run read as a cautious one."""
    assert is_error("Error: connection reset")
    assert not is_refusal("Error: connection reset")
    assert not is_error("no error was reported")


# ------------------------------------------------------------------ score_row


def test_score_row_uses_the_best_gold_alias():
    row = {"question": "q", "answer": "new york", "gold": ["big apple", "new york"]}
    scored = score_row(row)
    assert scored["exact_match"] is True
    assert scored["matched_gold"] == "new york"


def test_gold_alias_tie_breaks_toward_the_first_alias():
    """Both aliases score f1=0.5 here. The winner must be the earlier one, every
    run, or the same answers file scores differently on different days."""
    row = {"question": "q", "answer": "york apple", "gold": ["big apple", "new york"]}
    scored = score_row(row)
    assert scored["f1"] == pytest.approx(0.5)
    assert scored["matched_gold"] == "big apple"
    reversed_row = {**row, "gold": ["new york", "big apple"]}
    assert score_row(reversed_row)["matched_gold"] == "new york"


def test_score_row_accepts_the_vendored_golden_answer_key():
    """QABenchmarkRAG.save_results writes 'golden_answer', so a competitor's own
    results file scores without being reshaped by hand."""
    scored = score_row({"question": "q", "answer": "kansas", "golden_answer": "kansas"})
    assert scored["exact_match"] is True


def test_unanswerable_row_hallucinates_or_refuses():
    hallucinated = score_row({"question": "q", "answer": "38,394 sq km", "gold": None})
    assert hallucinated["answerable"] is False
    assert hallucinated["hallucinated"] is True
    assert hallucinated["refused"] is False

    refused = score_row({"question": "q", "answer": "I don't know", "gold": None})
    assert refused["hallucinated"] is False
    assert refused["refused"] is True

    errored = score_row({"question": "q", "answer": "Error: boom", "gold": None})
    assert errored["errored"] is True
    assert errored["hallucinated"] is False, "a crash is not a hallucination"
    assert errored["refused"] is False, "a crash is not caution"


def test_empty_gold_string_is_answerable_and_a_refusal_matches_it():
    """An empty gold answer is a degenerate but legal fixture. Correctness and
    refusal are measured independently, so an empty answer is BOTH — pinned here
    so a future change to either has to face the interaction deliberately."""
    scored = score_row({"question": "q", "answer": "", "gold": ""})
    assert scored["answerable"] is True
    assert scored["exact_match"] is True
    assert scored["f1"] == pytest.approx(1.0)
    assert scored["refused"] is True


# ------------------------------------------------------------------- aggregate

#: Seven rows, five answerable, whose every aggregate is computed by hand in
#: ``test_summarize_matches_hand_computed_values``.
FIXTURE_ROWS = [
    {"question": "q1", "answer": "Scotland", "gold": "scotland", "stratum": "hard"},
    {"question": "q2", "answer": "New York City", "gold": "Brooklyn, New York",
     "stratum": "hard"},
    {"question": "q3", "answer": "", "gold": "Kansas", "stratum": "hard"},
    {"question": "q4", "answer": "I don't know", "gold": "Istanbul", "stratum": "hard"},
    {"question": "q5", "answer": "38,394 square kilometres", "gold": None,
     "stratum": "unanswerable"},
    {"question": "q6", "answer": "I don't know", "gold": None, "stratum": "unanswerable"},
    {"question": "q7", "answer": "Error: boom", "gold": "Muir", "stratum": "hard"},
]


def test_summarize_matches_hand_computed_values():
    summary = summarize([score_row(r) for r in FIXTURE_ROWS])
    assert summary["n"] == 7
    assert summary["n_answerable"] == 5
    assert summary["n_unanswerable"] == 2

    # q1 is the only exact match of the five answerable rows.
    assert summary["exact_match"] == pytest.approx(1 / 5)
    # per-question F1s: 1.0, 2/3, 0, 0, 0
    assert summary["f1_macro"] == pytest.approx((1.0 + 2 / 3) / 5)
    # micro: tp=3, fp=6, fn=4  ->  2*3 / (2*3 + 6 + 4) = 0.375
    assert summary["f1_micro"] == pytest.approx(0.375)
    assert summary["precision_micro"] == pytest.approx(3 / 9)
    assert summary["recall_micro"] == pytest.approx(3 / 7)
    # q3 (empty) and q4 (I don't know) refused an ANSWERABLE question.
    assert summary["refusal_rate"] == pytest.approx(2 / 5)
    # q5 answered an unanswerable question; q6 refused it.
    assert summary["hallucination_rate"] == pytest.approx(0.5)
    assert summary["unanswerable_refusal_rate"] == pytest.approx(0.5)
    assert summary["error_rate"] == pytest.approx(1 / 7)


def test_macro_and_micro_f1_are_genuinely_different_numbers():
    """Reported side by side because they disagree — a wide gap means the system
    is failing selectively on short answers or on long ones."""
    summary = summarize([score_row(r) for r in FIXTURE_ROWS])
    assert abs(summary["f1_macro"] - summary["f1_micro"]) > 0.03


def test_a_system_that_refuses_everything_looks_perfect_on_hallucination_alone():
    """The reason refusal_rate is emitted next to hallucination_rate and never
    on its own. This is the null-model lesson from probe_anchors.py in metric
    form: a number that cannot be gamed is not the same as a number that means
    something."""
    always_refuses = [{**row, "answer": "I don't know"} for row in FIXTURE_ROWS]
    summary = summarize([score_row(r) for r in always_refuses])
    assert summary["hallucination_rate"] == 0.0, "perfect, and worthless alone"
    assert summary["refusal_rate"] == pytest.approx(1.0), "the number that exposes it"
    assert summary["f1_macro"] == 0.0


def test_score_system_splits_by_stratum():
    report = score_system(FIXTURE_ROWS, system="fixture", meta={"llm_model": "m"})
    assert report["system"] == "fixture"
    assert set(report["strata"]) == {"hard", "unanswerable"}
    assert report["strata"]["hard"]["n"] == 5
    assert report["strata"]["unanswerable"]["n_unanswerable"] == 2
    assert report["meta"] == {"llm_model": "m"}


def test_level_is_accepted_as_the_stratum_key():
    """HotpotQA pairs carry 'level', not 'stratum'."""
    report = score_system([{"question": "q", "answer": "a", "gold": "a", "level": "hard"}],
                          system="s")
    assert set(report["strata"]) == {"hard"}


# --------------------------------------------------------------------- ranking


def test_rank_systems_holds_ties_as_ties():
    reports = [
        {"system": "beta", "overall": {"f1_macro": 0.5}},
        {"system": "alpha", "overall": {"f1_macro": 0.5}},
        {"system": "gamma", "overall": {"f1_macro": 0.2}},
    ]
    ranking = rank_systems(reports, key="f1_macro")
    assert [(r["rank"], r["system"]) for r in ranking] == [
        (1, "alpha"), (1, "beta"), (3, "gamma"),
    ], "competition ranking, ties ordered by name so the output is stable"
    assert [r["tied"] for r in ranking] == [True, True, False]


def test_rank_systems_does_not_order_float_noise():
    """Two systems differing in the twelfth decimal of a mean over 24 questions
    are not distinguishable, and reporting them as ordered is a false claim."""
    reports = [
        {"system": "a", "overall": {"f1_macro": 0.3333333333}},
        {"system": "b", "overall": {"f1_macro": 0.3333333334}},
    ]
    assert all(entry["rank"] == 1 for entry in rank_systems(reports))


# -------------------------------------------------------------------- fairness


def _report(system, **meta):
    return {"system": system, "overall": {"f1_macro": 0.0}, "meta": meta}


def test_single_system_has_nothing_to_block():
    assert fairness_blockers([_report("tesserae")]) == []


def test_the_model_gap_in_this_repo_blocks_publication():
    """The committed LightRAG store was seeded on gpt-5.4
    (examples/demo-corpus/scripts/seed_raganything_store.py:63) while Tesserae
    defaults to gpt-5.6-luna. Publishing across that gap measures the models."""
    blockers = fairness_blockers([
        _report("tesserae", llm_model="gpt-5.6-luna",
                embedding_model="model2vec:minishlab/potion-base-8M",
                embedding_dim=256, corpus="hotpot24", question_set="hotpot24"),
        _report("lightrag", llm_model="gpt-5.4",
                embedding_model="all-MiniLM-L6-v2",
                embedding_dim=384, corpus="hotpot24", question_set="hotpot24"),
    ])
    joined = "\n".join(blockers)
    assert any(b.startswith("llm_model:") for b in blockers)
    assert "gpt-5.4" in joined and "gpt-5.6-luna" in joined
    assert any(b.startswith("embedding_model:") for b in blockers)
    assert any(b.startswith("embedding_dim:") for b in blockers)


def test_matching_declarations_clear_the_gate():
    common = dict(llm_model="gpt-5.6-luna", embedding_model="m2v",
                  embedding_dim=256, corpus="hotpot24", question_set="hotpot24")
    assert fairness_blockers([_report("a", **common), _report("b", **common)]) == []


def test_an_undeclared_run_is_blocked_not_assumed_fair():
    """'We did not record which model answered' is not 'the models matched'."""
    blockers = fairness_blockers([
        _report("tesserae", llm_model="gpt-5.6-luna", embedding_model="m2v",
                embedding_dim=256, corpus="c", question_set="q"),
        _report("mystery"),
    ])
    assert blockers, "an undeclared system must not pass the gate"
    assert all("mystery" in b for b in blockers)


def test_baseline_is_exempt_from_embeddings_but_not_from_the_model():
    """A null model has no retrieval — that is what it is for — but a null model
    run on a DIFFERENT model than the system it baselines measures nothing."""
    system = _report("tesserae", llm_model="gpt-5.6-luna", embedding_model="m2v",
                     embedding_dim=256, corpus="c", question_set="q")
    matched = _report("null", role="baseline", llm_model="gpt-5.6-luna",
                      embedding_model="none", embedding_dim="none",
                      corpus="c", question_set="q")
    assert fairness_blockers([system, matched]) == []

    mismatched = {**matched, "meta": {**matched["meta"], "llm_model": "gpt-5.4"}}
    blockers = fairness_blockers([system, mismatched])
    assert [b.split(":")[0] for b in blockers] == ["llm_model"]


# ---------------------------------------------------------------- probe set


def test_unanswerable_probe_set_is_well_formed():
    from evals.qa.run_qa_eval import UNANSWERABLE_JSON

    probes = json.loads(UNANSWERABLE_JSON.read_text(encoding="utf-8"))
    assert len(probes) >= 8
    assert {p["level"] for p in probes} == {"unanswerable-absent", "unanswerable-fictitious"}
    for probe in probes:
        assert probe["answer"] is None, "gold None IS the unanswerable marker"
        assert probe["absent_tokens"] and probe["why"]


def test_unanswerable_probes_really_are_absent_from_the_corpus():
    """The claim 'this question has no answer in the corpus' is checkable, so it
    is checked rather than trusted. Skipped when the gitignored clone is absent."""
    from evals.qa.run_qa_eval import UNANSWERABLE_JSON
    from evals.qa.vendor_base import CORPUS_JSON

    if not CORPUS_JSON.is_file():
        pytest.skip(f"vendored corpus not present at {CORPUS_JSON}")
    blob = " ".join(json.loads(CORPUS_JSON.read_text(encoding="utf-8"))).casefold()
    for probe in json.loads(UNANSWERABLE_JSON.read_text(encoding="utf-8")):
        for token in probe["absent_tokens"]:
            assert token.casefold() not in blob, (
                f"{token!r} IS in the corpus — {probe['question']!r} is answerable "
                f"and must not be scored as a refusal probe"
            )


# ------------------------------------------------------------------ the runner


def test_runner_skips_with_no_arguments(capsys):
    from evals.qa import run_qa_eval

    assert run_qa_eval.main([]) == 0
    assert capsys.readouterr().out.startswith("SKIP:")


def test_runner_skips_in_ci_before_touching_anything(capsys, monkeypatch):
    """Guard 1. This benchmark must never run in CI: the answer phase spends
    quota and the stage phase writes a corpus directory."""
    from evals.qa import run_qa_eval

    monkeypatch.setenv("CI", "true")
    assert run_qa_eval.main(["--system", "null", "--answer",
                             "--i-know-this-costs-money"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("SKIP:") and "CI" in out


def test_runner_refuses_to_spend_money_without_consent(capsys, monkeypatch):
    """Guard 3. There is no default path that reaches an LLM."""
    from evals.qa import run_qa_eval

    monkeypatch.delenv("CI", raising=False)
    assert run_qa_eval.main(["--system", "null", "--answer"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("SKIP:") and "--i-know-this-costs-money" in out


def test_runner_skip_lines_name_the_remedy(capsys, monkeypatch, tmp_path):
    """The probe_anchors.py contract: SKIP says what is missing AND the command
    that fixes it, then exits 0."""
    from evals.qa import run_qa_eval

    monkeypatch.delenv("CI", raising=False)
    assert run_qa_eval.main(["--score", str(tmp_path / "nope.json")]) == 0
    out = capsys.readouterr().out
    assert "SKIP: answers file not found" in out
    assert "--system <name> --answer" in out


def test_runner_scores_saved_answers_and_writes_a_report(tmp_path, monkeypatch, capsys):
    from evals.qa import run_qa_eval

    monkeypatch.delenv("CI", raising=False)
    answers = tmp_path / "fixture.json"
    answers.write_text(json.dumps({
        "system": "fixture", "meta": {"llm_model": "gpt-5.6-luna"}, "rows": FIXTURE_ROWS,
    }), encoding="utf-8")
    out = tmp_path / "report.md"
    assert run_qa_eval.main(["--score", str(answers), "--out", str(out)]) == 0
    capsys.readouterr()

    report = out.read_text(encoding="utf-8")
    assert report.startswith("# QA benchmark")
    assert "## 1. Overall" in report and "## 2. Per stratum" in report
    assert "## 4. Fairness preconditions" in report
    assert "Latency is not measured" in report
    assert "| fixture | 5 | 20.0% | 0.333 | 0.375 | 40.0% | 50.0% | 14.3% |" in report


def test_report_is_byte_identical_across_runs(tmp_path, monkeypatch, capsys):
    """No timestamps, no dict-order dependence: re-scoring the same answers must
    produce the same bytes, the same house invariant a compile is held to."""
    from evals.qa import run_qa_eval

    monkeypatch.delenv("CI", raising=False)
    answers = tmp_path / "fixture.json"
    answers.write_text(json.dumps({"system": "fixture", "meta": {}, "rows": FIXTURE_ROWS}),
                       encoding="utf-8")
    first, second = tmp_path / "a.md", tmp_path / "b.md"
    run_qa_eval.main(["--score", str(answers), "--out", str(first)])
    run_qa_eval.main(["--score", str(answers), "--out", str(second)])
    capsys.readouterr()
    assert first.read_bytes() == second.read_bytes()


def test_report_refuses_to_publish_a_comparison_across_the_model_gap(tmp_path,
                                                                    monkeypatch, capsys):
    from evals.qa import run_qa_eval

    monkeypatch.delenv("CI", raising=False)
    paths = []
    for name, model in (("tesserae", "gpt-5.6-luna"), ("lightrag", "gpt-5.4")):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({
            "system": name, "meta": {"llm_model": model}, "rows": FIXTURE_ROWS,
        }), encoding="utf-8")
        paths.append(str(path))
    out = tmp_path / "report.md"
    assert run_qa_eval.main(["--score", *paths, "--out", str(out)]) == 0
    capsys.readouterr()
    report = out.read_text(encoding="utf-8")
    assert "**These numbers are NOT publishable as a comparison.**" in report
    assert "gpt-5.4" in report
    # Two identical fixtures score identically — the ranking must say "tied".
    assert "## 3. Ranking" in report and "tied" in report


def test_answers_file_without_declarations_scores_but_cannot_be_published(tmp_path,
                                                                         monkeypatch,
                                                                         capsys):
    """A bare vendored results list (question/answer/golden_answer) is scoreable;
    it just cannot be published as a comparison, because nobody wrote down what
    answered it."""
    from evals.qa import run_qa_eval

    monkeypatch.delenv("CI", raising=False)
    bare = tmp_path / "competitor.json"
    bare.write_text(json.dumps([
        {"question": "q1", "answer": "scotland", "golden_answer": "scotland"},
    ]), encoding="utf-8")
    declared = tmp_path / "ours.json"
    declared.write_text(json.dumps({
        "system": "tesserae", "meta": {"llm_model": "gpt-5.6-luna"},
        "rows": [{"question": "q1", "answer": "scotland", "gold": "scotland"}],
    }), encoding="utf-8")
    out = tmp_path / "report.md"
    assert run_qa_eval.main(["--score", str(bare), str(declared), "--out", str(out)]) == 0
    capsys.readouterr()
    report = out.read_text(encoding="utf-8")
    assert "competitor" in report
    assert "not declared by competitor" in report


# ------------------------------------------------------------------ null model


def _skip_without_vendored_base():
    from evals.qa.vendor_base import MissingPrerequisite, load_qa_benchmark_base

    try:
        load_qa_benchmark_base()
    except MissingPrerequisite as exc:
        pytest.skip(f"vendored QA benchmark base unavailable: {exc.what}")


class _RecordingClient:
    """Stands in for the LLM. Records prompts; never calls anything."""

    def __init__(self):
        self.prompts = []

    def complete_text(self, *, system, user, max_retries=2):
        self.prompts.append((system, user))
        return "I don't know"


def test_null_model_cannot_see_the_corpus():
    """The property the whole baseline rests on: there is no code path from a
    document to a prompt. Checked with a marker token, not by reading the code."""
    _skip_without_vendored_base()
    from evals.qa.null_model import NullModelConfig, QABenchmarkNullModel

    marker = "ZORVATHMARKER"
    corpus = [f"Document {i} about {marker}." for i in range(5)]
    benchmark = QABenchmarkNullModel(
        corpus, [{"question": "Where is Angus?", "answer": "scotland"}],
        NullModelConfig(print_results=False, results_file=""),
    )
    client = _RecordingClient()
    benchmark.client_factory = lambda: client

    async def _drive():
        benchmark.rag_client = await benchmark.initialize_rag()
        await benchmark.load_corpus_to_rag()
        return await benchmark.answer_questions()

    results = asyncio.run(_drive())
    assert benchmark.documents_discarded == 5, "the corpus was offered and dropped"
    assert client.prompts, "the model was actually asked"
    assert all(marker not in system and marker not in user
               for system, user in client.prompts)
    assert not any(marker in json.dumps(r) for r in results)
    assert benchmark.declared_meta()["role"] == "baseline"


def test_null_model_surfaces_an_exhausted_client_as_an_error_not_a_refusal():
    """complete_text returns None when every account is rate-limited. Scoring
    that as a refusal would credit a billing failure as caution."""
    _skip_without_vendored_base()
    from evals.qa.null_model import NullModelConfig, QABenchmarkNullModel

    class _Exhausted:
        def complete_text(self, *, system, user, max_retries=2):
            return None

    benchmark = QABenchmarkNullModel([], [], NullModelConfig(results_file=""))
    benchmark.client_factory = _Exhausted

    async def _drive():
        benchmark.rag_client = await benchmark.initialize_rag()
        return await benchmark.query_rag("anything")

    answer = asyncio.run(_drive())
    assert is_error(answer) and not is_refusal(answer)
    assert score_row({"question": "q", "answer": answer, "gold": None})["hallucinated"] is False


def test_null_model_skips_loudly_without_an_llm_client():
    _skip_without_vendored_base()
    from evals.qa.null_model import NullModelConfig, QABenchmarkNullModel
    from evals.qa.vendor_base import MissingPrerequisite

    benchmark = QABenchmarkNullModel([], [], NullModelConfig(results_file=""))
    benchmark.client_factory = lambda: None
    with pytest.raises(MissingPrerequisite) as excinfo:
        asyncio.run(benchmark.initialize_rag())
    assert "codex login" in excinfo.value.remedy


# -------------------------------------------------------------- tesserae adapter


def test_tesserae_adapter_stages_without_compiling(tmp_path):
    """insert_document writes files and does nothing else. No compile, no LLM,
    no graph.json — the compile between the phases is a human's decision."""
    _skip_without_vendored_base()
    from evals.qa.benchmark_tesserae import QABenchmarkTesserae, TesseraeConfig

    project = tmp_path / "qa-run"
    benchmark = QABenchmarkTesserae(
        ["alpha document", "beta document"], [],
        TesseraeConfig(project_root=str(project), print_results=False, results_file=""),
    )
    asyncio.run(benchmark.load_corpus_to_rag())

    staged = sorted((project / "corpus").iterdir())
    assert [p.name for p in staged] == ["doc-00001.md", "doc-00002.md"]
    assert staged[0].read_text(encoding="utf-8") == "alpha document"
    assert not (project / ".tesserae").exists(), "staging must not create a project"


def test_tesserae_adapter_restages_byte_identically(tmp_path):
    """Filenames come from the document index alone — no clock, no hash of
    mutable state — so a re-stage is byte-identical."""
    _skip_without_vendored_base()
    from evals.qa.benchmark_tesserae import QABenchmarkTesserae, TesseraeConfig

    project = tmp_path / "qa-run"

    def _stage():
        benchmark = QABenchmarkTesserae(
            ["alpha", "beta"], [],
            TesseraeConfig(project_root=str(project), print_results=False, results_file=""),
        )
        asyncio.run(benchmark.load_corpus_to_rag())
        return {p.name: p.read_bytes() for p in sorted((project / "corpus").iterdir())}

    assert _stage() == _stage()


def test_tesserae_adapter_refuses_an_uncompiled_project(tmp_path):
    """Fail loud: a run against a project that was never compiled would score
    zero and read as 'Tesserae cannot answer these questions'."""
    _skip_without_vendored_base()
    from evals.qa.benchmark_tesserae import QABenchmarkTesserae, TesseraeConfig
    from evals.qa.vendor_base import MissingPrerequisite

    benchmark = QABenchmarkTesserae(
        [], [], TesseraeConfig(project_root=str(tmp_path), print_results=False,
                               results_file=""),
    )
    with pytest.raises(MissingPrerequisite) as excinfo:
        asyncio.run(benchmark.initialize_rag())
    assert "tesserae compile" in excinfo.value.remedy


def test_answer_text_normalizes_every_envelope_shape():
    _skip_without_vendored_base()
    from evals.qa.benchmark_tesserae import answer_text

    assert answer_text({"backend": "wiki", "answer": "scotland"}) == "scotland"
    # ask_project's raganything branch returns answer=None when it has nothing;
    # the wiki branch may omit the key entirely with synthesis off.
    assert answer_text({"backend": "raganything", "answer": None}) == ""
    assert answer_text({"backend": "wiki", "hits": []}) == ""
    assert is_refusal(answer_text({"answer": None})), "no answer reads as a refusal"

"""The LongMemEval-MAB loader — offline, on a synthetic parquet.

The real split is a 20MB download that is not in the repo, so these build a
parquet with the same schema. What is pinned here is the selection rule and the
refusal, both of which decide whether a published comparison is valid:

* ``Accurate_Retrieval`` carries ruler_* and eventqa_* records ALONGSIDE the
  longmemeval ones. Scoring the wrong subset would produce a number that looks
  fine and answers a different benchmark.
* An empty result must raise. "Zero groups" and "wrong file" are
  indistinguishable at the call site, and a benchmark that silently scores
  nothing is worse than one that stops.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

from evals.lme_mab.dataset import MabGroup, load_groups  # noqa: E402


def _write_parquet(path: Path, rows: list) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.Table.from_pylist(rows), str(path))
    return path


def _row(source: str, context: str, questions: list, answers: list) -> dict:
    return {
        "context": context,
        "questions": questions,
        "answers": answers,
        "metadata": {"source": source},
    }


def test_selects_only_the_longmemeval_records(tmp_path):
    p = _write_parquet(
        tmp_path / "m.parquet",
        [
            _row("ruler_qa1_197K", "ruler haystack", ["rq"], [["ra"]]),
            _row("longmemeval_s*", "dialogue one", ["q1", "q2"], [["a1"], ["a2"]]),
            _row("eventqa_full", "event haystack", ["eq"], [["ea"]]),
            _row("longmemeval_s*", "dialogue two", ["q3"], [["a3"]]),
        ],
    )

    groups = load_groups(p)

    assert [g.context for g in groups] == ["dialogue one", "dialogue two"]
    assert [g.index for g in groups] == [0, 1]
    assert sum(len(g.questions) for g in groups) == 3


def test_a_file_with_no_matching_records_raises(tmp_path):
    p = _write_parquet(
        tmp_path / "m.parquet",
        [_row("ruler_qa1_197K", "ruler haystack", ["rq"], [["ra"]])],
    )

    with pytest.raises(ValueError, match="longmemeval"):
        load_groups(p)


def test_token_estimate_is_labelled_crude_and_scales_with_context():
    g = MabGroup(index=0, source="longmemeval_s*", context="x" * 4000,
                 questions=[], answers=[])
    assert g.approx_tokens == 1000


def test_answers_survive_as_lists_per_question(tmp_path):
    """Each question carries a LIST of acceptable answers; flattening one to a
    string would silently mark correct alternates wrong."""
    p = _write_parquet(
        tmp_path / "m.parquet",
        [_row("longmemeval_s*", "d", ["q"], [["Scotland", "the UK"]])],
    )

    (group,) = load_groups(p)

    assert group.answers == [["Scotland", "the UK"]]

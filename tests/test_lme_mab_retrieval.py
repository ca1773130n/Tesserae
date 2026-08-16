"""LongMemEval-MAB retrieval — gold alignment and the retrieval-only metrics.

Offline and synthetic: no parquet, no compile, no model, no network. What is
pinned here is the handful of decisions that would otherwise print a plausible
wrong number:

* alignment keys on CONTENT, so a haystack session in a different position than
  the context view still resolves to the right document — measured on the real
  parquet, positional alignment is wrong for every group;
* every ambiguity refuses instead of guessing;
* an unmatched session is counted, a question with no gold is excluded rather
  than scored zero, and a short result list is never padded;
* the three arms index ONE corpus — the bytes a Tesserae run stages — and all
  answer in the same units, so the table's rows differ by retriever and by
  nothing else.

The dense arm runs against a stub backend, on ``MabMemory``'s reasoning: a lane
whose wiring can only be checked by loading a model does not get checked.
``tests/test_real_embeddings_phase6.py`` is where the real model2vec backend is
exercised.

One exception, and it is the point of the case that makes it:
``test_no_socket_is_opened_while_the_real_local_embedder_loads`` loads the real
local model behind a spy on ``socket.getaddrinfo``, because "this arm reaches no
network" is a claim about the actual load and a stub cannot fail it. It skips
rather than downloads when the model is not already cached.
"""

from __future__ import annotations

import os
import socket
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Sequence

import pytest

from tesserae.retrieval.hybrid import _rank_bm25_available

from evals.lme_mab import baselines
from evals.lme_mab import run as runner
from evals.lme_mab.adapter import PROTOCOL_EMBEDDING_MODEL, MabMemory, split_sessions
from evals.lme_mab.baselines import (
    LOCAL_EMBEDDING_PREFER,
    DenseArm,
    LexicalArm,
    RefusedToEmbedLocally,
)
from evals.lme_mab.dataset import MabGroup
from evals.lme_mab.retrieval import (
    NOT_COMPARABLE,
    RefusedToAlignGold,
    align_gold,
    score_retrieval,
    session_signature,
)
from evals.qa.run_qa_eval import Skip

# -------------------------------------------------------------------- fixture
#
# The same shape ``tests/test_lme_mab_adapter.py`` uses, measured on the real
# parquet: ``context`` is a flat literal alternating a ``Chat Time:`` header
# with a list of ``{role, content, has_answer}`` turns. The turn lists are named
# here as well, because a haystack entry is a COPY of one of them and the point
# of these tests is which copy resolves to which document.

_KEYS = [{"role": "user", "content": "where did I leave my keys", "has_answer": False},
         {"role": "assistant", "content": "on the hall table", "has_answer": True}]
_FLIGHT = [{"role": "user", "content": "book me a flight", "has_answer": False}]
_HOTEL = [{"role": "user", "content": "and a hotel", "has_answer": False}]

_CONTEXT = repr([
    "Chat Time: 2022/11/17 (Thu) 12:04", _KEYS,
    "Chat Time: 2022/12/28 (Wed) 16:10", _FLIGHT,
    "Chat Time: 2023/01/05 (Thu) 12:34", _HOTEL,
])


def _group(**overrides: Any) -> MabGroup:
    base: Dict[str, Any] = {
        "index": 0, "source": "longmemeval_s*", "context": _CONTEXT,
        "questions": ["where are the keys"], "answers": [["the hall table"]],
        "question_types": ["single-session-user"],
    }
    base.update(overrides)
    return MabGroup(**base)


def _copy(turns: Sequence[Mapping[str, Any]], *, gold: bool) -> List[Dict[str, Any]]:
    """A haystack copy of a context session, gold-marked or not.

    ``has_answer`` lives on the haystack view — the context view's own flag is
    not the answer key this alignment reads — so a copy carries whichever value
    the test is about.
    """
    copied = [dict(turn) for turn in turns]
    copied[-1]["has_answer"] = gold
    return copied


def _rows(*specs: Sequence[Any], stratum: str = "single-session-user") -> List[Dict[str, Any]]:
    """``(gold, retrieved)`` pairs as scoreable rows."""
    return [
        {"question": f"q{i}", "stratum": stratum, "group": 0,
         "gold": list(gold), "retrieved": list(retrieved)}
        for i, (gold, retrieved) in enumerate(specs)
    ]


# ------------------------------------------------------------------ signature


def test_signature_ignores_whitespace_and_the_role():
    """The two views differ in wrapping, not in words — see ``_normalise``."""
    a = [{"role": "user", "content": "where did I leave\n  my keys"}]
    b = [{"role": "assistant", "content": "  where did I leave my keys "}]
    assert session_signature(a) == session_signature(b)


def test_signature_separates_turns():
    """One turn of ``ab`` is not two turns of ``a`` and ``b``."""
    assert session_signature([{"content": "ab"}]) != session_signature(
        [{"content": "a"}, {"content": "b"}]
    )


def test_signature_ignores_the_gold_marker():
    """A signature that moved with ``has_answer`` would key the map on the
    answer itself, and every gold session would stop matching."""
    plain = [{"role": "user", "content": "on the hall table", "has_answer": False}]
    gold = [{"role": "user", "content": "on the hall table", "has_answer": True}]
    assert session_signature(plain) == session_signature(gold)


# ------------------------------------------------------------------ alignment


def test_align_gold_matches_by_content_not_position():
    """The measured ground truth: a question's sessions are NOT in context order.

    Question 0 lists the hotel session first and the gold keys session second.
    Positional alignment would call document 1 gold; content alignment calls
    document 0 gold, which is where the turns actually are.
    """
    group = _group(
        questions=["where are the keys", "what did I book"],
        answers=[["the hall table"], ["a flight"]],
        question_types=["single-session-user", "multi-session"],
        haystack_sessions=[
            [_copy(_HOTEL, gold=False), _copy(_KEYS, gold=True)],
            [_copy(_FLIGHT, gold=True), _copy(_HOTEL, gold=True)],
        ],
    )
    alignment = align_gold(group, split_sessions(group))
    assert alignment.gold == [[0], [1, 2]]
    assert alignment.n_unmatched == 0
    assert alignment.n_no_gold == 0


def test_align_gold_refuses_without_haystack_sessions():
    group = _group()  # the parquet's other view only — no answer key at all
    with pytest.raises(RefusedToAlignGold, match="no metadata.haystack_sessions"):
        align_gold(group, split_sessions(group))


def test_align_gold_refuses_duplicate_signatures():
    """Two identical context sessions make a gold match belong to both."""
    twice = repr([
        "Chat Time: 2022/11/17 (Thu) 12:04", _KEYS,
        "Chat Time: 2023/01/05 (Thu) 12:34", _KEYS,
    ])
    group = _group(context=twice,
                   haystack_sessions=[[_copy(_KEYS, gold=True)]])
    with pytest.raises(RefusedToAlignGold, match="identical turn contents"):
        align_gold(group, split_sessions(group))


def test_align_gold_refuses_when_questions_and_haystack_disagree():
    """``haystack_sessions`` is per QUESTION; unequal lengths misattribute gold."""
    group = _group(
        questions=["where are the keys", "what did I book"],
        answers=[["the hall table"], ["a flight"]],
        question_types=["single-session-user", "multi-session"],
        haystack_sessions=[[_copy(_KEYS, gold=True)]],
    )
    with pytest.raises(RefusedToAlignGold, match="1 haystack entries against 2 questions"):
        align_gold(group, split_sessions(group))


def test_align_gold_counts_an_unmatched_session_and_does_not_guess():
    """Measured once in group 4, on a NON-gold session — which is what this
    fixture models. It is counted, never resolved to a neighbour. The gold case
    is a refusal: see ``test_align_gold_refuses_an_unmatched_gold_session``."""
    stranger = [{"role": "user", "content": "a session in no context view",
                 "has_answer": False}]
    group = _group(haystack_sessions=[[stranger, _copy(_FLIGHT, gold=True)]])
    alignment = align_gold(group, split_sessions(group))
    assert alignment.n_unmatched == 1
    # Only the session that actually matched is gold; the stranger contributes
    # no index at all — not 0, not "the next one".
    assert alignment.gold == [[1]]
    assert alignment.n_no_gold == 0


def test_align_gold_refuses_an_unmatched_gold_session():
    """An unmatched GOLD session is not the measured case, and not survivable.

    Counting it beside the non-gold one would leave a question with two golds,
    one of them unfindable, scored 1.000 when the truth is 0.500 — and a
    question whose ONLY gold went missing would drop into ``n_no_gold`` and
    leave the mean entirely, removing exactly the question the arms would most
    likely have missed. Both errors point the same way: at a better-looking
    number.
    """
    stranger = [{"role": "user", "content": "a gold session in no context view",
                 "has_answer": True}]
    group = _group(haystack_sessions=[[stranger, _copy(_KEYS, gold=True)]])
    with pytest.raises(RefusedToAlignGold, match="GOLD session"):
        align_gold(group, split_sessions(group))


def test_align_gold_reports_a_question_with_no_gold():
    """10 of 300 real questions have none. Gold comes from the haystack copy's
    ``has_answer``, so the context view's own flag does not make one."""
    group = _group(haystack_sessions=[[_copy(_KEYS, gold=False)]])
    alignment = align_gold(group, split_sessions(group))
    assert alignment.gold == [[]]
    assert alignment.n_no_gold == 1
    assert alignment.n_unmatched == 0


# -------------------------------------------------------------------- metrics


def test_score_retrieval_excludes_a_question_with_no_gold():
    """Excluded, not scored zero: there was nothing to retrieve."""
    report = score_retrieval(_rows(([0], [0]), ([], [1, 2])), system="BM25", k=10)
    overall = report["overall"]
    assert overall["n"] == 2
    assert overall["n_scored"] == 1
    assert overall["n_no_gold"] == 1
    assert overall["recall_at_k"] == 1.0  # 0.5 would be the zeroed answer
    assert overall["mrr"] == 1.0
    assert report["rows"][1]["recall_at_k"] is None


def test_score_retrieval_caps_multi_gold_recall_at_k():
    """3 golds at K=2, both returned documents gold: 2/min(3,2) = 1.0, not 0.67."""
    report = score_retrieval(_rows(([1, 2, 3], [1, 2]),), system="Dense", k=2)
    row = report["rows"][0]
    assert row["n_hits"] == 2
    assert row["recall_at_k"] == 1.0
    # n_gold rides along so a reader can recompute the uncapped 2/3.
    assert report["overall"]["n_gold"] == 3
    assert report["overall"]["n_hits"] == 2


def test_score_retrieval_does_not_pad_a_short_result():
    """5 golds, 2 documents returned against K=10: 2/min(5,10) = 0.4.

    The denominator stays the budget. Shrinking it to what came back would score
    an arm that returned almost nothing 1.0, which inverts the meaning of an
    under-filled budget.
    """
    report = score_retrieval(_rows(([1, 2, 3, 4, 5], [1, 2]),), system="Dense", k=10)
    assert report["rows"][0]["recall_at_k"] == pytest.approx(0.4)
    assert report["overall"]["n_under_k"] == 1
    assert report["under_k"] == [{"question": "q0", "requested": 10, "returned": 2}]


def test_a_short_result_record_never_claims_a_candidate_count_it_never_saw():
    """The scorer is handed a ranked list, not the lane's candidate pool.

    ``total_matches: 0`` here was a hardcoded zero wearing the shape of a
    measurement: the arms DO count their candidates (``_Arm.shortfalls``,
    ``MabMemory.shortfalls``), so the first render of a baseline record would
    have read "0 candidates" for a lane that found two.
    """
    report = score_retrieval(_rows(([1], [1, 2]),), system="Dense", k=10)
    record = report["under_k"][0]

    assert "total_matches" not in record
    # And §5's renderer prints "not measured" for it rather than a zero.
    assert "| — |" in "\n".join(runner._shortfall_section([record], 1))


def test_score_retrieval_scores_a_full_result_with_nothing_under_k():
    report = score_retrieval(_rows(([0], list(range(10))),), system="BM25", k=10)
    assert report["under_k"] == []
    assert report["overall"]["n_under_k"] == 0
    assert report["overall"]["recall_at_k"] == 1.0


def test_under_k_counts_documents_and_is_not_called_a_shortfall():
    """K hits that de-duplicate to fewer sessions are not a retrieval failure.

    ``MabMemory.search_documents`` returns DISTINCT session indices, so a full K
    hits drawn from two sessions comes back as two documents — "the budget doing
    its job rather than a shortfall to fix", in the adapter's own words. Naming
    that the same thing as a lane that matched nothing makes the footnote
    unreadable, and on a real run it fires on nearly every Tesserae question.
    """
    report = score_retrieval(_rows(([0], [0, 1]),), system="Tesserae", k=10)

    assert report["overall"]["n_under_k"] == 1
    assert "n_shortfall" not in report["overall"]
    assert "shortfalls" not in report


def test_the_footnote_separates_de_duplication_from_an_empty_lane():
    """The footnote has to say which of the two it counted, or it is misread."""
    report = score_retrieval(_rows(([0], [0, 1]),), system="Tesserae", k=10)

    notes = "\n".join(runner._retrieval_footnotes([report]))

    assert "distinct document" in notes
    assert "de-duplicat" in notes
    # Named so a reader can go and find the count that IS a search shortfall.
    assert "MabMemory.shortfalls" in notes


def test_score_retrieval_refuses_k_below_one():
    """``min(len(gold), k)`` is an unguarded denominator and ``[:k]`` silently
    drops elements for a negative k — a ZeroDivisionError and a negative rate,
    from the one control the whole comparison rests on."""
    rows = _rows(([0], [0]))
    for bad in (0, -1):
        # A ``Skip``, like every other refusal here: the runner prints
        # ``SKIP: <what>`` plus the fix and exits 0 rather than tracebacking.
        with pytest.raises(Skip, match="K must be at least 1"):
            score_retrieval(rows, system="BM25", k=bad)


def test_score_retrieval_mrr_counts_only_the_first_hit():
    """Gold at ranks 2 and 3 is 1/2, not 1/2 + 1/3 and not 1/3."""
    report = score_retrieval(_rows(([3, 9], [7, 3, 9]),), system="BM25", k=10)
    row = report["rows"][0]
    assert row["rr"] == pytest.approx(0.5)
    assert row["recall_at_k"] == 1.0  # both golds retrieved, |G| = 2 <= K
    assert report["overall"]["mrr"] == pytest.approx(0.5)


def test_score_retrieval_mrr_is_zero_when_nothing_hits():
    report = score_retrieval(_rows(([4], [1, 2, 3]),), system="BM25", k=10)
    assert report["overall"]["mrr"] == 0.0
    assert report["overall"]["recall_at_k"] == 0.0
    assert report["overall"]["n_scored"] == 1  # a miss IS a measurement


def test_score_retrieval_truncates_to_k_and_ignores_repeats():
    """K is the control: gold at rank 11 is not retrieved, and a repeated
    document is one hit, not two."""
    truncated = score_retrieval(_rows(([10], list(range(11))),), system="BM25", k=10)
    assert truncated["overall"]["recall_at_k"] == 0.0
    repeated = score_retrieval(_rows(([1, 2], [1, 1, 1]),), system="BM25", k=10)
    assert repeated["rows"][0]["n_hits"] == 1
    assert repeated["rows"][0]["recall_at_k"] == pytest.approx(0.5)


def test_score_retrieval_strata_are_the_benchmarks_question_types():
    rows = _rows(([0], [0]), stratum="temporal-reasoning")
    rows += _rows(([1], [5]), stratum="knowledge-update")
    report = score_retrieval(rows, system="Dense", k=10, meta={"embedder": "local"})
    assert sorted(report["strata"]) == ["knowledge-update", "temporal-reasoning"]
    assert report["strata"]["temporal-reasoning"]["recall_at_k"] == 1.0
    assert report["strata"]["knowledge-update"]["recall_at_k"] == 0.0
    assert report["strata"]["knowledge-update"]["n_scored"] == 1
    assert report["meta"] == {"embedder": "local"}
    assert report["system"] == "Dense" and report["k"] == 10


# ----------------------------------------------------------------------- arms


class _StubBackend:
    """A ``model2vec``-shaped backend: three axes, one per fixture session.

    Every text lands on the axes of the words it contains, so a question's
    similarity to a document is something the test states rather than something
    a model decides. ``refund`` is the negative axis — it exists to check that a
    non-positive cosine is dropped rather than ranked into the budget.
    """

    dim = 3
    _AXES = (
        ("keys", (1.0, 0.0, 0.0)),
        ("flight", (0.0, 1.0, 0.0)),
        ("hotel", (0.0, 0.0, 1.0)),
        ("refund", (-1.0, 0.0, 0.0)),
    )

    def __init__(self, *, name: str = "model2vec:stub") -> None:
        self.name = name
        #: One entry per ``embed`` call, so a test can see the corpus was
        #: embedded once and not once per question.
        self.calls: List[List[str]] = []

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> List[float]:
        folded = text.casefold()
        vector = [0.0, 0.0, 0.0]
        for needle, axis in self._AXES:
            if needle in folded:
                vector = [a + b for a, b in zip(vector, axis)]
        return vector


def _arms(sessions: Sequence[Any]) -> List[Any]:
    """The two baseline arms over one session list."""
    return [LexicalArm(sessions), DenseArm(sessions, backend=_StubBackend())]


def _tesserae_arm(work: Any, *paths: str) -> MabMemory:
    """``MabMemory`` with its search stubbed to return one hit per path."""

    def _search(graph, query, **kwargs):
        return SimpleNamespace(
            scored=[SimpleNamespace(node=SimpleNamespace(
                name=f"node about {query}", description="", source_path=path))
                for path in paths],
            total_matches=len(paths),
        )

    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"),
                       search_fn=_search, backend=object())
    memory.work = work
    memory._graph = object()  # a compiled graph would be loaded here
    return memory


def test_the_arms_index_the_bytes_the_tesserae_arm_stages(tmp_path):
    """One corpus, or the table compares corpora rather than retrievers."""
    group = _group()
    sessions = split_sessions(group)
    staged = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway")).ingest(
        group, work=tmp_path, compile_project=False)

    for arm in _arms(sessions):
        assert len(arm.documents) == len(sessions)
        for session, document in zip(sessions, arm.documents):
            assert document == session.render()
            assert document == (staged.corpus_dir / session.document_name).read_text(
                encoding="utf-8")


def test_the_gold_marker_never_reaches_a_baseline_document():
    """``has_answer`` is ground truth for SCORING ONLY. A baseline that indexed
    it would retrieve "this is the answer" and score the leak."""
    for arm in _arms(split_sessions(_group())):
        assert "has_answer" not in "".join(arm.documents)


def test_bm25_ranks_the_session_that_shares_the_question_terms():
    arm = LexicalArm(split_sessions(_group()))

    assert arm.search_documents("where did I leave my keys", k=10) == [0]
    assert arm.search_documents("book me a flight", k=10) == [1, 2]


def test_bm25_returns_nothing_rather_than_filling_the_budget():
    """A zero BM25 score is no shared term, not a weak match. Ranking those in
    would fill K with documents the lane rejected — and since ties break on the
    index, an arm that matched nothing would return sessions 0-9 every time and
    could hit gold by luck."""
    arm = LexicalArm(split_sessions(_group()))

    assert arm.search_documents("photosynthesis", k=10) == []
    assert arm.shortfalls == [{"question": "photosynthesis", "requested": 10,
                               "returned": 0, "total_matches": 0}]


def test_bm25_records_which_implementation_actually_ran():
    """``_bm25_scores`` prefers ``rank_bm25`` whenever it imports and otherwise
    runs the local Okapi, and the two are different formulas — a number is not
    reproducible without knowing which one produced it."""
    arm = LexicalArm(split_sessions(_group()))

    assert arm.bm25_impl.startswith("rank_bm25") is _rank_bm25_available()
    assert arm.meta["bm25_impl"] == arm.bm25_impl


def test_dense_asks_for_model2vec_by_name_and_never_auto(monkeypatch):
    """``auto`` degrades to the hash stub on a ``UserWarning`` nobody sees in a
    benchmark run, and the stub prints numbers that look semantic."""
    asked: List[str] = []

    def _resolve(prefer):
        asked.append(prefer)
        return _StubBackend()

    monkeypatch.setattr(baselines, "active_embedding_backend", _resolve)
    arm = DenseArm(split_sessions(_group()))

    arm.search_documents("keys", k=1)

    assert asked == [LOCAL_EMBEDDING_PREFER] == ["model2vec"]


def test_dense_refuses_a_backend_that_is_not_the_local_embedder():
    """Checked on every use, not once at construction: "the arm asked for
    model2vec" is not the same claim as "the arm embedded with model2vec"."""
    arm = DenseArm(split_sessions(_group()), backend=_StubBackend(name="hash"))

    with pytest.raises(RefusedToEmbedLocally, match="which is not model2vec"):
        arm.search_documents("keys", k=1)


def test_dense_embeds_the_corpus_once_not_once_per_question():
    """``hybrid._embedding_scores`` re-embeds the corpus on every call, which
    over a 60-question group would embed ~110 sessions sixty times."""
    backend = _StubBackend()
    arm = DenseArm(split_sessions(_group()), backend=backend)

    for question in ("keys", "flight", "hotel"):
        arm.search_documents(question, k=2)

    assert backend.calls[0] == arm.documents
    assert backend.calls[1:] == [["keys"], ["flight"], ["hotel"]]


def test_dense_ranks_by_cosine_and_breaks_ties_on_the_session_index():
    backend = _StubBackend()
    arm = DenseArm(split_sessions(_group()), backend=backend)

    assert arm.search_documents("the keys", k=10) == [0]
    # Equal cosine against all three sessions: the order is the index, so a
    # re-run ranks the same way.
    assert arm.search_documents("keys flight hotel", k=10) == [0, 1, 2]
    assert arm.search_documents("keys flight hotel", k=2) == [0, 1]


def test_dense_drops_a_non_positive_cosine():
    """The same rule BM25 gets: a lane that scored a document at or below zero
    did not retrieve it, and filling the budget with it would be padding."""
    arm = DenseArm(split_sessions(_group()), backend=_StubBackend())

    assert arm.search_documents("refund", k=10) == []
    assert arm.shortfalls[-1]["total_matches"] == 0


def test_the_arm_meta_declares_the_live_embedder_not_a_hardcoded_one():
    sessions = split_sessions(_group())

    dense = DenseArm(sessions, backend=_StubBackend(name="model2vec:potion-x"))
    assert dense.meta["embedder"] == "model2vec:potion-x"
    assert dense.meta["embedding_dim"] == 3
    assert dense.meta["corpus"].startswith("3 session documents")

    lexical = LexicalArm(sessions)
    assert lexical.meta["embedder"].startswith("none")
    assert lexical.meta["corpus"] == dense.meta["corpus"]


def test_all_three_arms_expose_search_documents(tmp_path):
    """Duck typing plus this test, rather than a ``Protocol`` with one method,
    three implementers in one package and one call site. All three answer the
    same question in the same units: context-session indices, best first."""
    sessions = split_sessions(_group())
    arms = _arms(sessions) + [_tesserae_arm(tmp_path, "corpus/session-0000.md")]

    for arm in arms:
        documents = arm.search_documents("where did I leave my keys", k=2)
        assert documents[0] == 0
        assert len(documents) <= 2
        assert all(isinstance(index, int) for index in documents)
        assert len(set(documents)) == len(documents)


# --------------------------------------------------------------------- runner
#
# The carve-out ``--arms`` makes over the money layers, checked from ``main``
# rather than from the predicate: the whole point is which guards a given
# invocation reaches, and a unit test of ``spends`` would pass with every guard
# wired to the wrong side of it.


def _gold_group() -> MabGroup:
    """One question whose gold is the keys session — the shape align_gold reads."""
    return _group(haystack_sessions=[[_copy(_KEYS, gold=True)]])


def _no_money(monkeypatch) -> None:
    """A machine that cannot spend: no key, and a backbone that fails the test.

    ``build_backbone`` is the last money layer and the only one that constructs
    an LLM client, so patching it to ``pytest.fail`` turns "the free arms went
    through the paid path" from a bill into a failure.
    """
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(runner, "build_backbone",
                        lambda model: pytest.fail("built an LLM backbone"))
    monkeypatch.setattr(baselines, "active_embedding_backend",
                        lambda prefer: _StubBackend())
    monkeypatch.setattr(runner, "load_groups_or_skip", lambda path: [_gold_group()])


def _argv(tmp_path, *extra: str) -> List[str]:
    parquet = tmp_path / "m.parquet"
    parquet.write_bytes(b"stub")  # require_parquet checks for a file, not a schema
    return ["--parquet", str(parquet), "--work", str(tmp_path / "work"),
            "--out", str(tmp_path / "report.md"), *extra]


def test_the_baselines_reach_retrieval_with_no_key_and_no_backbone(
        monkeypatch, capsys, tmp_path):
    """The run this whole change exists for: two arms, zero money layers."""
    _no_money(monkeypatch)

    assert runner.main(_argv(tmp_path, "--arms", "bm25,dense", "--retrieval-only")) == 0

    out = capsys.readouterr().out
    assert "ESTIMATED COST" not in out      # no bill to approve
    assert "SKIP:" not in out               # and nothing to skip for
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| BM25 |" in text and "| Dense |" in text
    # The fixture's one question is gold in document 0 and both lanes rank it
    # first, so this is hand-checkable: recall@10 = 1/min(1,10), MRR = 1/1.
    assert text.count("| 10 | 1.000 | 1.000 | 1 |") == 2


def test_the_report_puts_the_caveat_above_the_table_it_qualifies(
        monkeypatch, capsys, tmp_path):
    """A screenshot of the table has to carry its own caveat — the same reason
    ``_comparable_section`` prints its withholding above the reasons."""
    _no_money(monkeypatch)

    assert runner.main(_argv(tmp_path, "--arms", "bm25,dense", "--retrieval-only")) == 0

    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert NOT_COMPARABLE in text
    assert text.index(NOT_COMPARABLE) < text.index("| method |")
    assert "## 6. Retrieval comparison" in text


def test_the_money_gate_is_still_armed_for_the_tesserae_arm(
        monkeypatch, capsys, tmp_path):
    """The carve-out is for the arms that cannot spend, and for nothing else.
    ``--retrieval-only`` does not relax it: Tesserae still compiles a haystack."""
    _no_money(monkeypatch)

    assert runner.main(_argv(tmp_path, "--arms", "tesserae,bm25",
                             "--retrieval-only")) == 0

    out = capsys.readouterr().out
    assert "ESTIMATED COST" in out
    assert "SKIP: this run compiles a haystack" in out
    assert not (tmp_path / "report.md").exists()


def test_ci_still_skips_every_arm(monkeypatch, capsys, tmp_path):
    """Free of money is not free of time: the baselines read a 20MB parquet and
    a benchmark that runs in CI under ONE set of flags is one edit from all."""
    _no_money(monkeypatch)
    monkeypatch.setenv("CI", "1")

    assert runner.main(_argv(tmp_path, "--arms", "bm25,dense", "--retrieval-only")) == 0

    assert capsys.readouterr().out.startswith("SKIP: CI is set")
    assert not (tmp_path / "report.md").exists()


def test_an_unknown_arm_refuses_rather_than_being_dropped(monkeypatch, capsys, tmp_path):
    """Dropping it silently would print a one-row table that reads as two."""
    _no_money(monkeypatch)

    assert runner.main(_argv(tmp_path, "--arms", "bm25,bm52")) == 0

    assert "SKIP: no such arm(s): bm52" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_the_runner_refuses_k_below_one_before_it_reads_the_parquet(
        monkeypatch, capsys, tmp_path, bad):
    """K is the control every arm shares, and the parquet is 20MB.

    A traceback after the load is the wrong answer twice over: it is not the
    refusal every other bad input in this package gets, and it arrives after the
    expensive half of the invocation.
    """
    _no_money(monkeypatch)
    monkeypatch.setattr(runner, "load_groups_or_skip",
                        lambda path: pytest.fail("read the parquet anyway"))

    assert runner.main(_argv(tmp_path, "--arms", "bm25", "--retrieval-only",
                             "--k", bad)) == 0

    assert f"SKIP: --k {bad}" in capsys.readouterr().out


def test_an_answer_failure_does_not_erase_the_retrieval(tmp_path):
    """A 429 on the backbone is not a retrieval miss.

    One try block around the search AND the answer scored a question whose
    search ranked gold first as recall@10 = 0.000, RR = 0.000 — and counted it
    in ``n_scored`` rather than excluding it, so it was recorded as a total
    retrieval miss. Only the Tesserae arm calls an LLM, so that deflates exactly
    the arm §6 exists to measure.
    """
    memory = _tesserae_arm(tmp_path, "corpus/session-0000.md")

    def _rate_limited(question, evidence):
        raise RuntimeError("429 rate limited")

    rows, retrieved = runner.answer_group(memory, _group(), _rate_limited,
                                          k=10, progress=False)

    assert retrieved == [[0]]           # the search ranked gold at position 1
    assert rows[0]["answer"].startswith("Error: ")
    assert rows[0]["n_evidence"] == 1   # the evidence the backbone was handed
    scored = score_retrieval(
        runner.retrieval_rows(_group(), [[0]], retrieved), system="Tesserae", k=10)
    assert scored["overall"]["recall_at_k"] == 1.0
    assert scored["overall"]["mrr"] == 1.0


def test_a_search_failure_retrieves_nothing_and_the_run_continues(tmp_path):
    """The other half of the split, and what ``answer_group``'s docstring
    promises: a question whose SEARCH raised retrieved nothing, which is what it
    scores as, and the other questions survive."""
    memory = MabMemory(compile_fn=lambda w: pytest.fail("compiled anyway"),
                       search_fn=lambda *a, **kw: (_ for _ in ()).throw(
                           RuntimeError("graph is not loaded")),
                       backend=object())
    memory.work = tmp_path
    memory._graph = object()

    rows, retrieved = runner.answer_group(
        memory, _group(), lambda q, e: pytest.fail("answered without evidence"),
        k=10, progress=False)

    assert retrieved == [[]]
    assert rows[0]["n_evidence"] == 0
    assert "graph is not loaded" in rows[0]["answer"]


# --------------------------------------------------------------------- caveat


def test_not_comparable_is_one_paragraph_naming_the_protocol_embedder():
    """One string, imported by every consumer. It has to survive being placed
    directly above a table, so it is a single paragraph and it names the control
    it fails rather than gesturing at "differences"."""
    assert "\n" not in NOT_COMPARABLE
    assert PROTOCOL_EMBEDDING_MODEL in NOT_COMPARABLE
    assert "may not be quoted" in NOT_COMPARABLE


# ------------------------------------------------------- one local embedder
#
# ``NOT_COMPARABLE`` asserts, above the §6 table, that every arm was measured
# "with one local embedder". These are what makes that sentence true by
# enforcement rather than by wording: the default invocation resolves the local
# embedder for every arm, and §6 refuses to print a table whose rows did not.


def test_the_default_invocation_holds_one_local_embedder():
    """The claim printed above §6 is about the DEFAULT run or it is about nothing.

    ``--embedding-prefer openai`` resolved ``OpenAIEmbeddingBackend`` for the
    Tesserae arm while the dense arm resolved model2vec, so the rendered table
    named two embedders under a sentence claiming one — and gave, as its reason
    for not being comparable, that the embedder in the table "is not
    text-embedding-3-small", which the same table then printed.
    """
    assert runner.build_parser().parse_args([]).embedding_prefer == LOCAL_EMBEDDING_PREFER


def test_section_6_refuses_to_render_when_two_arms_used_different_embedders():
    """Naming both, because "the arms disagreed" does not say which to fix."""
    tesserae = score_retrieval(
        _rows(([0], [0])), system="Tesserae", k=10,
        meta={"embedder": f"openai:{PROTOCOL_EMBEDDING_MODEL}", "n_unmapped_hits": 0})
    dense = score_retrieval(
        _rows(([0], [0])), system="Dense", k=10,
        meta={"embedder": f"{LOCAL_EMBEDDING_PREFER}:minishlab/potion-base-8M"})

    text = "\n".join(runner._retrieval_section([tesserae, dense]))

    assert "| method |" not in text          # no table under a false caveat
    assert NOT_COMPARABLE not in text        # and no caveat about a missing table
    assert "Tesserae" in text and "Dense" in text
    assert PROTOCOL_EMBEDDING_MODEL in text
    assert LOCAL_EMBEDDING_PREFER in text
    assert "--embedding-prefer" in text      # the flag that fixes it, by name


def test_section_6_renders_when_the_lexical_arm_has_no_embedder_at_all():
    """BM25 declaring `none` is not a disagreement — it has no lane to hold still."""
    bm25 = score_retrieval(_rows(([0], [0])), system="BM25", k=10,
                           meta={"embedder": "none"})
    dense = score_retrieval(_rows(([0], [0])), system="Dense", k=10,
                            meta={"embedder": f"{LOCAL_EMBEDDING_PREFER}:stub"})

    text = "\n".join(runner._retrieval_section([bm25, dense]))

    assert "| method |" in text
    assert NOT_COMPARABLE in text


# ----------------------------------------------------------- the lower bound


def test_the_tesserae_row_carries_its_lower_bound_label_in_the_row():
    """A caveat below the crop is not a caveat — ``_retrieval_section``'s own
    reasoning about ``NOT_COMPARABLE``, applied to the row it also qualifies.

    The method cell used to be the bare system name, with the lower bound said
    only in a footnote under the table: the one cell that travels with the
    number said nothing about it.
    """
    tesserae = score_retrieval(
        _rows(([0], [0])), system="Tesserae", k=10,
        meta={"embedder": f"{LOCAL_EMBEDDING_PREFER}:stub", "n_unmapped_hits": 3})
    bm25 = score_retrieval(_rows(([0], [0])), system="BM25", k=10,
                           meta={"embedder": "none"})

    text = "\n".join(runner._retrieval_section([tesserae, bm25]))

    assert "| Tesserae (lower bound) |" in text
    assert "| BM25 |" in text                 # and not on an arm that has none
    assert "| BM25 (lower bound) |" not in text


# ------------------------------------------------------ the offline embedder
#
# ``baselines`` claims zero network for both arms. Measured with a spy on
# ``socket.getaddrinfo``, ``StaticModel.from_pretrained`` opened
# ``('huggingface.co', 443)`` on every construction even with the model fully
# cached, so the claim was false on the one line that loads a model.


def test_the_dense_arm_loads_its_embedder_with_the_hub_switched_off(monkeypatch):
    """The load path sets the offline flags, and puts them back afterwards.

    Both flags: the environment variable for a ``huggingface_hub`` that has not
    been imported yet, and the already-evaluated module constant for one that
    has. Setting only the first leaves the claim false in every process that
    touched the hub first.
    """
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    seen: Dict[str, Any] = {}

    def _resolve(prefer: str) -> Any:
        from huggingface_hub import constants

        seen["prefer"] = prefer
        seen["env"] = os.environ.get("HF_HUB_OFFLINE")
        seen["constant"] = constants.HF_HUB_OFFLINE
        return _StubBackend()

    monkeypatch.setattr(baselines, "active_embedding_backend", _resolve)

    assert DenseArm(split_sessions(_group())).backend().name.startswith("model2vec:")

    assert seen == {"prefer": LOCAL_EMBEDDING_PREFER, "env": "1", "constant": True}
    # Restored: switching the hub off is this load's business and not the
    # process's, and a benchmark that leaves it set breaks the next download.
    assert os.environ.get("HF_HUB_OFFLINE") is None


def test_no_socket_is_opened_while_the_real_local_embedder_loads(monkeypatch):
    """The measurement itself, against the real model2vec backend.

    Skipped rather than downloaded when the model is not in this machine's
    Hugging Face cache: warming it is a deliberate one-time command the refusal
    names, and a test that fetched 8MB to prove nothing was fetched would be
    the claim it is checking.
    """
    pytest.importorskip("model2vec")
    from tesserae.retrieval import hybrid

    calls: List[Any] = []
    real = socket.getaddrinfo

    def _spy(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append((host, port))
        return real(host, port, *args, **kwargs)

    hybrid.reset_embedding_backend()
    monkeypatch.setattr(socket, "getaddrinfo", _spy)
    arm = DenseArm(split_sessions(_group()))
    try:
        backend = arm.backend()
    except RefusedToEmbedLocally as refusal:
        pytest.skip(f"local model not cached on this machine: {refusal.what}")
    finally:
        hybrid.reset_embedding_backend()

    assert calls == []
    assert backend.name.startswith(f"{LOCAL_EMBEDDING_PREFER}:")


def test_the_refusal_does_not_tell_an_operator_to_install_what_they_have(monkeypatch):
    """Remediation for the cause that actually fired.

    "install it with `uv sync --all-extras`" was printed for every failure,
    including the one this change creates — the package installed, the model
    not in the cache — which sends the operator to re-run an install that
    already succeeded.
    """
    def _offline(prefer: str) -> Any:
        raise OSError("offline mode: minishlab/potion-base-8M is not in the cache")

    monkeypatch.setattr(baselines, "active_embedding_backend", _offline)

    with pytest.raises(RefusedToEmbedLocally) as caught:
        DenseArm(split_sessions(_group())).backend()

    assert "uv sync --all-extras" not in caught.value.remedy
    assert "cache" in caught.value.remedy
    assert "huggingface.co" in caught.value.remedy   # the one call that IS allowed


def test_the_refusal_still_names_the_install_when_the_package_is_missing(monkeypatch):
    """The other cause keeps the answer it always had."""
    def _offline(prefer: str) -> Any:
        raise ImportError("No module named 'model2vec'")

    monkeypatch.setattr(baselines, "active_embedding_backend", _offline)
    monkeypatch.setitem(sys.modules, "model2vec", None)  # import raises from here

    with pytest.raises(RefusedToEmbedLocally) as caught:
        DenseArm(split_sessions(_group())).backend()

    assert "uv sync --all-extras" in caught.value.remedy


# ------------------------------------------- one arm refusing is not the run


def _cannot_embed(monkeypatch) -> None:
    """A machine that cannot load the local model: the dense arm refuses."""
    def _offline(prefer: str) -> Any:
        raise OSError("offline mode: the model is not in the cache")

    monkeypatch.setattr(baselines, "active_embedding_backend", _offline)


def test_an_arm_that_refuses_does_not_discard_the_arm_that_finished(
        monkeypatch, capsys, tmp_path):
    """BM25 had already scored every question when the dense arm refused.

    ``RefusedToEmbedLocally`` propagated out of the group loop to ``main``'s
    ``except Skip``, which printed the refusal, exited 0 and wrote NO report —
    throwing away an arm that had completed, in a run that costs nothing to
    repeat only because it is the free one.
    """
    _no_money(monkeypatch)
    _cannot_embed(monkeypatch)

    assert runner.main(_argv(tmp_path, "--arms", "bm25,dense", "--retrieval-only")) == 0

    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| BM25 |" in text
    assert "| 10 | 1.000 | 1.000 | 1 |" in text        # BM25's own numbers, kept
    assert "| Dense |" not in text                     # never a row of blanks
    # The missing arm is named ABOVE the table, on the caveat's own reasoning:
    # a crop showing one row reads as a comparison that ran.
    assert "Dense" in text.split("| method |")[0]
    assert "the model is not in the cache" in text


def test_a_run_whose_every_arm_refuses_still_skips_and_writes_nothing(
        monkeypatch, capsys, tmp_path):
    """The boundary of the change above: keeping the arms that finished is not
    the same as writing a report about none of them."""
    _no_money(monkeypatch)
    _cannot_embed(monkeypatch)

    assert runner.main(_argv(tmp_path, "--arms", "dense", "--retrieval-only")) == 0

    assert capsys.readouterr().out.startswith("SKIP: the dense arm's embedder")
    assert not (tmp_path / "report.md").exists()

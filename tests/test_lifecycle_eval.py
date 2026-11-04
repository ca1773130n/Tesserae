"""evals/lifecycle — the operation-level lifecycle eval harness (roadmap R1).

Tiny in-repo fixtures and a fake LLM only: nothing here downloads, spends, or
reads the real benchmark files.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from evals.lifecycle import agreement, datasets, end_to_end, extraction, llm, run, update
from evals.qa.run_qa_eval import Skip

FIXTURES = Path(__file__).parent / "fixtures" / "lifecycle"


# ---------------------------------------------------------------------------
# fake LLM
# ---------------------------------------------------------------------------


class FakeClient:
    """Routes by schema / system prompt; records every call."""

    def __init__(self, *, findings=None, matches=None, verdict=None, reader=None, judge=None):
        self.findings = findings or (lambda user: [])
        self.matches = matches
        self.verdict = verdict
        self.reader = reader or (lambda user: "I do not know.")
        self.judge = judge or (lambda user: "yes")
        self.calls = []

    def complete_json(self, *, system, user, schema_name, cache_key=None, max_retries=2):
        self.calls.append(schema_name)
        if schema_name == "session-finding-v1":
            return {"findings": [{"kind": "insight", "body": b, "turn_ids": [0], "references": []}
                                 for b in self.findings(user)]}
        if schema_name == "lifecycle_extraction_match":
            return self.matches(user) if self.matches else None
        if schema_name == "supersede_judgement":
            return self.verdict(user) if self.verdict else None
        raise AssertionError(f"unexpected schema {schema_name}")

    def complete_text(self, *, system, user, max_retries=2):
        self.calls.append(system[:20])
        if system == llm.READER_SYSTEM:
            return self.reader(user)
        if system == llm.QA_JUDGE_SYSTEM:
            return self.judge(user)
        raise AssertionError("unexpected system prompt")


def _users():
    return datasets.load_halumem(FIXTURES / "halumem_tiny.jsonl")


# ---------------------------------------------------------------------------
# agreement
# ---------------------------------------------------------------------------


def test_cohen_kappa_matches_hand_computation():
    # po = 0.75; pe = 0.5*0.25 + 0.5*0.75 = 0.5 -> kappa = 0.5
    assert agreement.cohen_kappa([1, 1, 0, 0], [1, 0, 0, 0]) == pytest.approx(0.5)
    assert agreement.cohen_kappa(list("aabbc"), list("aabbc")) == pytest.approx(1.0)
    assert agreement.cohen_kappa([1, 0], [0, 1]) == pytest.approx(-1.0)


def test_cohen_kappa_degenerate_and_length_mismatch():
    assert agreement.cohen_kappa(["x", "x"], ["x", "x"]) == 1.0
    with pytest.raises(ValueError):
        agreement.cohen_kappa([1], [1, 0])


def test_spearman_handles_ties_and_constants():
    assert agreement.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert agreement.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    # ranks x = [1.5, 1.5, 3, 4], y = [1, 2, 3, 4]
    assert agreement.spearman([1, 1, 2, 3], [1, 2, 3, 4]) == pytest.approx(0.9486833, rel=1e-6)
    assert agreement.spearman([1, 1, 1], [1, 2, 3]) is None


@pytest.mark.parametrize("runs,expected", [
    ([{"a": 1, "b": 0, "c": 1, "d": 0}] * 3, "gate"),
    ([{"a": 1, "b": 0, "c": 1, "d": 0}, {"a": 1, "b": 0, "c": 0, "d": 1}], "rejected"),
    ([{"a": 1, "b": 0, "c": 1, "d": 0}], "insufficient-runs"),
])
def test_test_retest_verdicts(runs, expected):
    assert agreement.test_retest(runs).verdict == expected


def test_test_retest_reads_the_minimum_pair_and_intersects_items():
    labels = [{f"i{i}": i % 2 for i in range(10)} for _ in range(3)]
    labels[2] = dict(labels[2], i0=1)  # one flip in one run only
    labels[1]["extra"] = 1  # present in one run: not compared
    res = agreement.test_retest(labels, [{"u": 0.5, "v": 0.7}] * 3)
    assert res.items == 10
    assert res.kappa_min < res.kappa_mean < 1.0
    assert res.kappa_min == pytest.approx(0.8)  # po 0.9, pe 0.5
    assert res.verdict == "gate"
    assert res.spearman_min == pytest.approx(1.0)
    assert agreement.verdict_for(0.7, 3) == "diagnostic"


# ---------------------------------------------------------------------------
# datasets
# ---------------------------------------------------------------------------


def test_halumem_loader_parses_string_booleans_strictly():
    (user,) = _users()
    points = [mp for s in user.sessions for mp in s.memory_points]
    assert [mp.is_update for mp in points] == [False, False, False, True, False, True]
    assert [mp.is_target for mp in points].count(False) == 1
    assert user.sessions[0].turns()[0] == {"role": "user", "text": "Hi, I'm Ana. I live in Lisbon and I work as a nurse."}
    with pytest.raises(ValueError, match="neither"):
        datasets._strict_bool("true", where="x")


def test_parse_facts_rejects_a_numbering_gap():
    assert datasets.parse_facts("Here is a list of facts:\n0. A is B.\n1. C is D.\n") == ["A is B.", "C is D."]
    with pytest.raises(ValueError, match="numbering"):
        datasets.parse_facts("0. A is B.\n2. C is D.\n")


def test_conflict_pairs_use_the_longest_shared_subject_relation():
    facts = [
        "Joan Didion was educated at University of California, Berkeley.",  # 0
        "The headquarters of Acme is located in the city of Paris.",  # 1
        "The headquarters of Initech is located in the city of Austin.",  # 2
        "Joan Didion was educated at Baldwin Wallace University.",  # 3
        "The headquarters of Acme is located in the city of Lyon.",  # 4
        "The capital of Romania is Bucharest.",  # 5
    ]
    # Initech shares only "The headquarters of" with Acme; that prefix leaves a
    # verb in the remainder, so it is not a key and Initech pairs with nothing.
    assert datasets.conflict_pairs(facts) == [(3, 0), (4, 1)]


def test_lme_loader_selects_subsets_and_drops_has_answer():
    qs = datasets.load_lme(FIXTURES / "lme_tiny.json")
    assert [q.question_id for q in qs] == ["ku1", "ss1_abs"]
    assert qs[0].subsets == ("knowledge-update",) and qs[1].subsets == ("abstention",)
    assert all(set(t) == {"role", "text"} for _, turns in qs[0].sessions for t in turns)
    with pytest.raises(ValueError, match="unknown LME subset"):
        datasets.load_lme(FIXTURES / "lme_tiny.json", subsets=("temporal",))


def _fake_source(monkeypatch, payload: bytes, *, sha=None):
    src = datasets.Source("x/y", "rev0", "f.bin", len(payload), sha or hashlib.sha256(payload).hexdigest())
    monkeypatch.setitem(datasets.SOURCES, "fake", src)
    monkeypatch.setattr(datasets.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(payload))
    return src


def test_fetch_refuses_without_permission_then_verifies_sha(tmp_path, monkeypatch):
    _fake_source(monkeypatch, b"hello")
    with pytest.raises(Skip, match="not cached"):
        datasets.fetch("fake", tmp_path)
    path = datasets.fetch("fake", tmp_path, allow_download=True)
    assert path.read_bytes() == b"hello"
    assert datasets.fetch("fake", tmp_path) == path  # cached: no download


def test_fetch_sha_mismatch_caches_nothing(tmp_path, monkeypatch):
    _fake_source(monkeypatch, b"hello", sha="0" * 64)
    with pytest.raises(ValueError, match="sha256"):
        datasets.fetch("fake", tmp_path, allow_download=True)
    assert not list(tmp_path.rglob("f.bin*"))


def test_cache_inside_the_repo_is_refused():
    with pytest.raises(Skip, match="inside the repository"):
        datasets.refuse_in_repo(datasets.REPO_ROOT / "evals" / "cache")


def test_factconsolidation_loader_reads_parquet(tmp_path):
    pa = pytest.importorskip("pyarrow")
    import pyarrow.parquet as pq

    rows = [{"context": "Here is a list of facts:\n0. A is married to B.\n1. A is married to C.\n",
             "questions": ["Who is A married to?"], "answers": [["C"]],
             "metadata": {"source": "factconsolidation_mh_6k"}},
            {"context": "", "questions": [], "answers": [], "metadata": {"source": "other"}}]
    path = tmp_path / "cr.parquet"
    pq.write_table(pa.Table.from_pylist(rows), str(path))
    (group,) = datasets.load_factconsolidation(path)
    assert group.hops == "mh" and group.facts == ("A is married to B.", "A is married to C.")
    with pytest.raises(ValueError):
        datasets.load_factconsolidation(path, sources=["factconsolidation_sh_6k"])


# ---------------------------------------------------------------------------
# stage 1 — extraction
# ---------------------------------------------------------------------------


def _extractor_for_fixture():
    def findings(user):
        if "Lisbon" in user:
            # one target, one planted interference fact, one ungrounded claim
            return ["Ana lives in Lisbon", "Ana owns a red bicycle", "Ana enjoys surfing"]
        if "Porto" in user and "piano" in user:
            return ["Ana lives in Porto", "Ana started learning piano"]
        return []
    return FakeClient(findings=findings)


def test_extraction_scores_recall_fabrication_and_fmr_with_lexical_judge():
    (user,) = _users()
    out = extraction.run_once(user.sessions, _extractor_for_fixture(), extraction.LexicalJudge())
    m = out["metrics"]
    assert m["sessions"] == 3 and m["findings"] == 5 and m["target_points"] == 5
    assert m["recall"] == pytest.approx(3 / 5) and m["omission_rate"] == pytest.approx(0.4)
    assert m["weighted_recall"] == pytest.approx((1.0 + 1.0 + 0.5) / (1.0 + 0.5 + 1.0 + 0.5 + 0.8), abs=1e-4)
    assert m["interference_rate"] == pytest.approx(1 / 5) and m["ungrounded_rate"] == pytest.approx(1 / 5)
    assert m["fabrication_rate"] == pytest.approx(2 / 5)
    assert m["false_memory_resistance"] == 0.0
    assert out["labels"]["u-ana:0000:T0"] == "covered" and out["labels"]["u-ana:0000:T1"] == "missed"
    assert out["unit_rates"]["u-ana:0002"] == 0.0


def test_llm_extraction_judge_parses_matches_and_counts_gaps():
    (user,) = _users()
    session = user.sessions[0]
    judge_client = FakeClient(matches=lambda user: {"matches": [{"finding": 0, "point": "t0"},
                                                                {"finding": 1, "point": "X0"},
                                                                {"finding": 9, "point": "T1"}]})
    judge = extraction.LLMExtractionJudge(judge_client, "fake")
    labels = judge.match(session, ["Ana lives in Lisbon", "red bike", "surfing"])
    assert labels == ["T0", "X0", "none"]
    assert judge.unparseable == 1


def test_a_failed_judge_or_extraction_excludes_the_session_instead_of_scoring_it():
    (user,) = _users()
    dead_judge = extraction.LLMExtractionJudge(FakeClient(), "fake")  # matches -> None
    out = extraction.run_once(user.sessions[:1], _extractor_for_fixture(), dead_judge)
    assert out["metrics"]["sessions"] == 0
    assert out["excluded"] == [{"session": "u-ana:0000", "reason": "judge call failed"}]

    class DeadExtractor(FakeClient):
        def complete_json(self, **kw):
            return None
    out = extraction.run_once(user.sessions[:1], DeadExtractor(), extraction.LexicalJudge())
    assert out["excluded"][0]["reason"] == "every extraction call failed"


def test_sample_sessions_is_seeded_and_stable():
    users = _users()
    a = [s.key for s in extraction.sample_sessions(users, 2, seed="s")]
    assert a == [s.key for s in extraction.sample_sessions(users, 2, seed="s")]
    assert len(set(a)) == 2


# ---------------------------------------------------------------------------
# stage 2 — update
# ---------------------------------------------------------------------------


def test_halumem_case_mints_paraphrased_originals_and_pairs_them():
    (user,) = _users()
    case = update.halumem_case(user)
    text = {nid: t for nid, t, _ in case.nodes}
    pairs = {(text[n], text[o]) for n, o in case.gold}
    assert pairs == {
        ("Ana lives in Porto", "Ana lives in Lisbon"),
        ("Ana works as a senior nurse at Porto General Hospital", "Ana works as a nurse at a clinic"),
    }
    assert "Ana owns a red bicycle" not in text.values()  # interference is never a node


def test_update_stage_labels_correct_omitted_and_spurious_deterministically():
    (user,) = _users()
    out = update.run_once([update.halumem_case(user)])
    m = out["metrics"]
    # Lisbon->Porto is offered (Jaccard 0.6) and the later session wins;
    # the nurse paraphrase is never offered (Jaccard 0.5).
    assert m["gold_pairs"] == 2 and m["correct_rate"] == 0.5 and m["omission_rate"] == 0.5
    assert m["candidate_recall"] == 0.5 and m["hallucination_rate"] == 0.0
    # "Ana works as a nurse" is retired by the minted clinic original — no gold
    # pair says so, so it is spurious and wrongly hidden.
    assert m["spurious_edges"] == 1 and m["wrongly_hidden_nodes"] == 1
    assert m["contradiction_pairs"] == 0


def test_an_llm_arbiter_that_points_backwards_is_scored_as_hallucination():
    (user,) = _users()
    backwards = FakeClient(verdict=lambda user: {
        # Finding A is listed first; always claim the one mentioning Lisbon/plain nurse wins.
        "verdict": "a_obsoletes_b" if user.index("Lisbon") < user.index("Finding B") else "b_obsoletes_a"
    } if "Lisbon" in user else {"verdict": "distinct"})
    out = update.run_once([update.halumem_case(user)], backwards)
    assert out["metrics"]["hallucination_rate"] == 0.5
    assert out["metrics"]["spurious_edges"] == 0
    assert "supersede_judgement" in backwards.calls


def test_factconsolidation_case_uses_index_as_recency():
    group = datasets.FactGroup("factconsolidation_sh_6k",
                               ("Ann is married to Bob.", "Ann is married to Carl."), (), ())
    out = update.run_once([update.factconsolidation_case(group)])
    assert out["metrics"]["correct_rate"] == 1.0
    assert out["labels"] == {"factconsolidation_sh_6k:f00001>f00000": "correct"}


# ---------------------------------------------------------------------------
# stage 3 — end to end
# ---------------------------------------------------------------------------


def test_rubric_prompt_is_upstreams_and_abstention_wins():
    ku, abs_q = datasets.load_lme(FIXTURES / "lme_tiny.json")
    assert end_to_end.rubric_prompt(ku, "Porto").startswith(
        "I will give you a question, a correct answer, and a response from a model.")
    assert "updated answer is the required answer" in end_to_end.rubric_prompt(ku, "Porto")
    assert end_to_end.rubric_prompt(abs_q, "I don't know").startswith("I will give you an unanswerable question")


def test_e2e_raw_arm_orders_sessions_by_date_and_grades_with_the_judge():
    qs = datasets.load_lme(FIXTURES / "lme_tiny.json")
    seen = []

    def reader(user):
        seen.append(user)
        return "You live in Porto." if "Porto" in user else "I do not know."
    client = FakeClient(reader=reader, judge=lambda user: "Yes." if "Porto" in user or "unanswerable" in user else "no")
    out = end_to_end.run_once_lme(qs, reader=client, judge=client, memory="raw")
    assert out["metrics"]["knowledge-update_accuracy"] == 1.0
    assert out["metrics"]["abstention_accuracy"] == 1.0
    assert seen[0].index("Lisbon") < seen[0].index("Porto")  # 05-01 before 05-20
    assert out["labels"] == {"ku1": "yes", "ss1_abs": "yes"}


def test_e2e_lifecycle_arm_drops_superseded_findings():
    (ku, _) = datasets.load_lme(FIXTURES / "lme_tiny.json")
    extractor = FakeClient(findings=lambda user: ["User lives in Porto"] if "Porto" in user else ["User lives in Lisbon"])
    live, stats = end_to_end.lifecycle_memory(ku, extractor)
    assert [t for _, t in live] == ["User lives in Porto"]
    assert stats["findings"] == 2 and stats["retired"] == 1


def test_e2e_excludes_failed_reader_calls():
    qs = datasets.load_lme(FIXTURES / "lme_tiny.json")
    client = FakeClient(reader=lambda user: None)
    out = end_to_end.run_once_lme(qs, reader=client, judge=client, memory="raw")
    assert out["metrics"]["questions"] == 0 and len(out["excluded"]) == 2


def test_fc_qa_lifecycle_arm_hides_the_older_fact_and_grades_by_substring():
    group = datasets.FactGroup("factconsolidation_mh_6k",
                               ("Ann is married to Bob.", "Ann is married to Carl."),
                               ("Who is Ann married to?",), (("Carl",),))
    shown = []

    def reader(user):
        shown.append(user)
        return "Carl." if "Carl" in user else "Bob"
    out = end_to_end.run_once_fc(group, reader=FakeClient(reader=reader), memory="lifecycle")
    assert out["metrics"]["facts_retired"] == 1 and out["metrics"]["accuracy"] == 1.0
    assert "Bob" not in shown[0]
    assert end_to_end.substring_match("The answer is CARL!", ["carl"])
    assert not end_to_end.substring_match("", ["Carl"])


# ---------------------------------------------------------------------------
# runner, results, doctor_report
# ---------------------------------------------------------------------------


def _common_args(tmp_path):
    return ["--out-dir", str(tmp_path / "results"), "--work-dir", str(tmp_path / "work"),
            "--cache-dir", str(tmp_path / "cache")]


def _seed_cache(tmp_path, monkeypatch, name, source_file):
    payload = Path(source_file).read_bytes()
    src = datasets.SOURCES[name]
    monkeypatch.setitem(datasets.SOURCES, name, datasets.Source(src.repo, src.revision, src.path,
                                                                len(payload), hashlib.sha256(payload).hexdigest()))
    dest = datasets.cached_path(name, tmp_path / "cache")
    dest.parent.mkdir(parents=True)
    dest.write_bytes(payload)


def test_runner_writes_a_dated_result_the_latest_reader_finds(tmp_path, monkeypatch, capsys):
    from tesserae.eval_results import latest_lifecycle_results

    monkeypatch.delenv("CI", raising=False)
    _seed_cache(tmp_path, monkeypatch, "halumem-medium", FIXTURES / "halumem_tiny.jsonl")
    assert run.main(["update", "--dataset", "halumem-medium", "--runs", "2", *_common_args(tmp_path)]) == 0
    (result,) = (tmp_path / "results").glob("*-lifecycle-update-halumem-medium.json")
    payload = json.loads(result.read_text())
    assert payload["schema"] == "tesserae.lifecycle-eval/1" and len(payload["runs"]) == 2
    assert payload["summary"]["correct_rate"] == 0.5
    assert payload["agreement"]["verdict"] == "gate"  # deterministic arm: identical by construction
    assert payload["dataset"]["revision"] == datasets.SOURCES["halumem-medium"].revision

    monkeypatch.setenv("TESSERAE_LIFECYCLE_RESULTS_DIR", str(tmp_path / "results"))
    latest = latest_lifecycle_results()
    assert latest["stages"]["update:halumem-medium:deterministic"]["summary"]["correct_rate"] == 0.5


def test_llm_stages_refuse_without_consent_and_ci_skips_everything(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.chdir(tmp_path)
    _seed_cache(tmp_path, monkeypatch, "halumem-medium", FIXTURES / "halumem_tiny.jsonl")
    assert run.main(["extraction", "--llm", "fake", *_common_args(tmp_path)],
                    client_factory=lambda m: FakeClient()) == 0
    assert "--i-know-this-costs-money" in capsys.readouterr().out
    monkeypatch.setenv("CI", "1")
    assert run.main(["update", *_common_args(tmp_path)]) == 0
    assert "SKIP: CI is set" in capsys.readouterr().out
    assert not (tmp_path / "results").exists()


def test_extraction_stage_runs_end_to_end_on_a_fake_llm_with_a_budget(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.chdir(tmp_path)  # the runner chdirs into --work-dir; pytest restores this
    _seed_cache(tmp_path, monkeypatch, "halumem-medium", FIXTURES / "halumem_tiny.jsonl")
    fake = _extractor_for_fixture()
    assert run.main(["extraction", "--llm", "fake", "--judge", "lexical", "--sample", "3", "--runs", "2",
                     "--max-llm-calls", "4", "--i-know-this-costs-money", *_common_args(tmp_path)],
                    client_factory=lambda m: fake) == 0
    (result,) = (tmp_path / "results").glob("*-lifecycle-extraction-*.json")
    payload = json.loads(result.read_text())
    # 3 sessions x 2 runs = 6 extraction calls wanted, 4 allowed.
    assert payload["llm"]["calls"] == {"extractor": 4}
    assert payload["llm"]["refused_over_budget"] == {"extractor": 2}
    assert any("budget ran out" in note for note in payload["notes"])
    assert Path.cwd() == (tmp_path / "work").resolve()


def test_unreadable_results_are_reported_not_fatal(tmp_path, caplog):
    from tesserae.eval_results import latest_lifecycle_results

    (tmp_path / "2026-01-01T000000-lifecycle-update-x.json").write_text("{not json")
    (tmp_path / "2026-01-02T000000-lifecycle-update-x.json").write_text(json.dumps({"schema": "other"}))
    for stamp, rate in (("2026-01-03T00:00:00+00:00", 0.1), ("2026-01-04T00:00:00+00:00", 0.2)):
        (tmp_path / f"{stamp[:10]}T000000-lifecycle-e2e-lme-oracle.json").write_text(json.dumps({
            "schema": "tesserae.lifecycle-eval/1", "stage": "e2e", "arm": "raw", "generated_at": stamp,
            "dataset": {"name": "lme-oracle"}, "summary": {"accuracy": rate},
            "agreement": {"verdict": "diagnostic", "kappa_min": 0.7}, "runs": [{}, {}, {}]}))
    out = latest_lifecycle_results(tmp_path)
    assert len(out["errors"]) == 2
    assert out["stages"]["e2e:lme-oracle:raw"]["summary"] == {"accuracy": 0.2}
    assert "unreadable" in caplog.text


# ---------------------------------------------------------------------------
# self-capture
# ---------------------------------------------------------------------------


def test_every_eval_prompt_is_covered_by_a_self_capture_signature():
    """The AST scan in test_harness_self_capture only walks tesserae/, so it
    cannot see these; a CLI transcript of any of them must still be dropped."""
    from tesserae.harness_sessions import (
        _TESSERAE_PROMPT_SIGNATURES, HarnessSession, is_tesserae_internal_session,
    )
    from tesserae.llm_json import _stitch_json_prompt

    assert llm.SYSTEM_PROMPTS
    for prompt in llm.SYSTEM_PROMPTS:
        assert prompt.startswith(_TESSERAE_PROMPT_SIGNATURES), prompt[:60]
        stitched = _stitch_json_prompt(system=prompt, user="Extracted memories: ...", schema_name="s")
        session = HarnessSession(id="x", slug="x", harness="claude-code", agent_label="Claude Code",
                                 project_name="p", project_root="/tmp/p", started_at="",
                                 metadata={"turns": [{"role": "user", "text": stitched}]})
        assert is_tesserae_internal_session(session)


def test_every_system_prompt_constant_in_the_harness_is_listed():
    import ast

    listed = set(llm.SYSTEM_PROMPTS)
    for path in (Path(run.__file__).parent).glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords or []:
                    if kw.arg != "system":
                        continue
                    if isinstance(kw.value, ast.Name) and kw.value.id.isupper():
                        value = getattr(llm, kw.value.id, None)
                        assert value in listed, f"{path.name}: system={kw.value.id} is not in SYSTEM_PROMPTS"
                    else:  # a pass-through parameter (BudgetedClient) is fine; a literal is not
                        assert not isinstance(kw.value, ast.Constant), f"{path.name}: inline system prompt"

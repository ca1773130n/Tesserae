"""The context-efficiency arms: what is counted, what is fitted, what is refused.

Offline and synthetic. No compile, no network, no model — the only real thing
loaded is the pinned tokenizer, because a token count computed with a stub is
not the instrument these arms are measured with.

Every case here is about a way the harness could produce a finished-looking
report that is wrong: a token count with a smuggling channel left open, a budget
an arm quietly exceeded, an efficiency ratio that rewards silence, an arm
compiling context from a conversation the question was not asked about, or a
compiled arm degraded into a different arm by a seed the graph silently dropped.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import pytest

from evals.locomo import context_arms, efficiency, run_context, tokens
from evals.locomo.run import _SYSTEM_PROMPT
from evals.qa.run_qa_eval import Skip

SYSTEM = "You answer questions."


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------


class _StubRanker:
    """``search_documents`` returning a fixed order. The lexical lane, frozen."""

    def __init__(self, order: Sequence[int]) -> None:
        self.order = list(order)
        self.calls: List[int] = []

    def search_documents(self, question: str, *, k: int) -> List[int]:
        self.calls.append(k)
        return self.order[:k]


class _StubNode:
    def __init__(self, node_id: str) -> None:
        self.id = node_id


class _StubGraph:
    def __init__(self, node_ids: Sequence[str]) -> None:
        self.nodes = [_StubNode(i) for i in node_ids]


class _StubBundle:
    def __init__(self, body: str) -> None:
        self.body = body
        self.selected_nodes = ["n1"]
        self.char_budget_used = len(body)


def _stub_compile(monkeypatch, bodies_for_budget) -> Dict[str, Any]:
    """Patch ``compile_context`` to a pure function of the requested budget.

    Records every call, so a test can assert WHICH seeds an arm passed — the
    difference between arm B and arm C is exactly that argument.
    """
    seen: Dict[str, Any] = {"calls": []}

    def fake(graph, project_root=None, query="", seeds=None, depth=2,
             budget=32_000, **kwargs):
        seen["calls"].append({"query": query, "seeds": list(seeds or []),
                              "budget": budget})
        return _StubBundle(bodies_for_budget(budget))

    import tesserae.context_compiler as compiler

    monkeypatch.setattr(compiler, "compile_context", fake)
    return seen


# --------------------------------------------------------------------------
# 1. Token counting — what is inside the number
# --------------------------------------------------------------------------


def test_serialized_request_is_byte_identical_to_what_the_client_sends():
    """The token unit must be the REQUEST, not a reconstruction of it.

    ``llm_json._stitch_json_prompt`` is the string the CLI clients actually
    send and is also their cache identity. If this harness restated the
    stitching, the two would drift and every token number would silently start
    describing a prompt nobody sent.
    """
    from tesserae.llm_json import _stitch_json_prompt

    assert tokens.serialized_request("sys", "usr", "sch") == _stitch_json_prompt(
        system="sys", user="usr", schema_name="sch")


def test_the_system_half_is_inside_the_count_so_it_cannot_be_smuggled():
    """Moving text from the user turn into the system prompt must not be free.

    Counting only the evidence leaves a channel open: instructions, few-shot
    examples or a fatter schema description could move into the system half and
    an arm would pay nothing for them.
    """
    payload = "a sentence of instructions that has to be paid for somewhere. " * 5
    in_user = tokens.count_tokens(tokens.serialized_request("s", payload, "x"))
    in_system = tokens.count_tokens(tokens.serialized_request(payload, "s", "x"))
    bare = tokens.count_tokens(tokens.serialized_request("s", "s", "x"))
    assert in_user > bare
    assert in_system > bare
    assert abs(in_user - in_system) <= 2


def test_a_tokenizer_whose_digest_moved_is_refused_not_used(monkeypatch, tmp_path):
    """Two runs measured with two vocabularies produce two ladders."""
    impostor = tmp_path / "tokenizer.json"
    impostor.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(tokens, "_TOKENIZER", None)
    monkeypatch.setattr(tokens, "find_tokenizer", lambda: impostor)
    with pytest.raises(Skip) as raised:
        tokens.load_tokenizer()
    assert "sha256" in raised.value.what
    monkeypatch.setattr(tokens, "_TOKENIZER", None)


def test_fit_by_prefix_is_maximal_and_never_overshoots():
    """The cut is measured in tokens, not sliced in characters.

    Characters and tokens are not proportional, so a character slice at a
    character budget is a different experiment from the one being run.
    """
    text = "Melanie said, \"I painted the harbour at dawn.\" " * 40
    budget = 200
    cut = tokens.fit_by_prefix(SYSTEM, "What did Melanie paint?", text,
                               budget_tokens=budget)
    assert cut and len(cut) < len(text)
    fitted = tokens.count_tokens(tokens.serialized_request(
        SYSTEM, tokens.user_turn("What did Melanie paint?", [cut]),
        tokens.SCHEMA_NAME))
    assert fitted <= budget
    one_more = tokens.count_tokens(tokens.serialized_request(
        SYSTEM, tokens.user_turn("What did Melanie paint?", [text[:len(cut) + 1]]),
        tokens.SCHEMA_NAME))
    assert one_more > budget


# --------------------------------------------------------------------------
# 2. Prompt construction
# --------------------------------------------------------------------------


def test_evidence_framing_matches_the_shape_the_other_runner_sends():
    """``[1] …\\n\\n[2] …`` — restated here, so a test pins the two together."""
    items = ["first", "second"]
    assert tokens.numbered_evidence(items) == "[1] first\n\n[2] second"
    assert tokens.user_turn("Q?", items) == (
        "Evidence:\n[1] first\n\n[2] second\n\nQuestion: Q?")


def test_an_arm_that_supplies_nothing_says_so_inside_the_count():
    """Empty evidence must not read as "never asked".

    The words are inside the request and therefore inside the token count, like
    every other word an arm spends.
    """
    user = tokens.user_turn("Q?", [])
    assert "none supplied" in user
    assert tokens.count_tokens(tokens.serialized_request(SYSTEM, user, "x")) > 0


def test_every_prompt_row_carries_its_own_token_count():
    prompt = tokens.Prompt(system=SYSTEM, user=tokens.user_turn("Q?", ["e"]),
                           schema_name=tokens.SCHEMA_NAME, items=["e"])
    row = prompt.as_row()
    assert row["prompt_tokens"] == prompt.tokens > 0
    assert row["truncated"] is False
    assert row["n_evidence"] == 1


# --------------------------------------------------------------------------
# 3. The BM25 + documents arm turns its own knob
# --------------------------------------------------------------------------


def _documents(n: int, size: int = 900) -> Dict[int, str]:
    return {i: f"Session {i}. " + ("word%d " % i) * size for i in range(1, n + 1)}


def test_bm25_documents_fills_the_budget_with_whole_documents_and_stops():
    documents = _documents(6)
    arm = context_arms.Bm25DocumentsArm(
        "conv-a", SYSTEM, _StubRanker([1, 2, 3, 4, 5, 6]), documents)
    for budget in (512, 2048, 8192):
        prompt = arm.prompt("Q?", budget_tokens=budget)
        assert prompt.tokens <= budget, (budget, prompt.tokens)
        if not prompt.truncated:
            # Whole documents only: every item is a document verbatim.
            assert all(item in documents.values() for item in prompt.items)


def test_bm25_documents_truncates_only_when_nothing_fits_and_flags_it():
    """A fixed budget measures truncation skill unless truncation is counted."""
    documents = _documents(3)
    arm = context_arms.Bm25DocumentsArm(
        "conv-a", SYSTEM, _StubRanker([1, 2, 3]), documents)
    tight = arm.prompt("Q?", budget_tokens=300)
    assert tight.truncated is True
    assert tight.tokens <= 300
    assert tight.items and tight.items[0] != documents[1]

    roomy = arm.prompt("Q?", budget_tokens=100_000)
    assert roomy.truncated is False


def test_bm25_documents_ranks_the_whole_corpus_so_the_budget_is_the_only_cut():
    """A smaller k would be a second, undeclared budget inside the ranking."""
    ranker = _StubRanker([1, 2, 3, 4])
    arm = context_arms.Bm25DocumentsArm("conv-a", SYSTEM, ranker, _documents(4))
    arm.prompt("Q?", budget_tokens=4096)
    assert ranker.calls == [4]


# --------------------------------------------------------------------------
# 4. The compiled arms fit their budget, and B really seeds
# --------------------------------------------------------------------------


def test_compiled_arms_never_exceed_the_budget_they_were_fitted_to(monkeypatch):
    _stub_compile(monkeypatch, lambda budget: "compiled body. " * (budget // 10))
    arm = context_arms.GraphOnlyArm("conv-a", SYSTEM, _StubGraph([]), "/root")
    for budget in (512, 2048, 8192):
        prompt = arm.prompt("Q?", budget_tokens=budget)
        assert prompt.tokens <= budget, (budget, prompt.tokens)
        assert prompt.fit["budget_tokens"] == budget
        assert prompt.fit["grid_tokens"], "the fitting scan must be auditable"


def test_bm25_compiled_seeds_the_walk_and_graph_only_does_not(monkeypatch):
    """The one structural difference between arm B and arm C."""
    seen = _stub_compile(monkeypatch, lambda budget: "body " * (budget // 10))
    graph = _StubGraph(["SourceDocument:session-0001:aa",
                        "SourceDocument:session-0002:bb"])
    seed_nodes = {1: "SourceDocument:session-0001:aa",
                  2: "SourceDocument:session-0002:bb"}
    arm_b = context_arms.Bm25CompiledArm(
        "conv-a", SYSTEM, graph, "/root", _StubRanker([2, 1]), seed_nodes,
        region_k=2)
    arm_b.prompt("Q?", budget_tokens=2048)
    assert seen["calls"], "the arm never compiled"
    assert seen["calls"][0]["seeds"] == ["SourceDocument:session-0002:bb",
                                         "SourceDocument:session-0001:aa"]

    seen["calls"].clear()
    context_arms.GraphOnlyArm("conv-a", SYSTEM, graph, "/root").prompt(
        "Q?", budget_tokens=2048)
    assert seen["calls"][0]["seeds"] == []


def test_a_seed_the_graph_does_not_hold_refuses_instead_of_degrading():
    """``compile_context`` drops an unknown seed silently.

    Measured on the compiled conv-26 graph: a bundle requested with the single
    seed ``'nope'`` came back with a full body and ten substituted seeds of its
    own. An off-by-one in the mapping would therefore turn arm B into arm C
    while every persisted row still claimed it had passed seeds.
    """
    graph = _StubGraph(["SourceDocument:session-0001:aa"])
    with pytest.raises(Skip) as raised:
        context_arms.Bm25CompiledArm(
            "conv-a", SYSTEM, graph, "/root", _StubRanker([1, 2]),
            {1: "SourceDocument:session-0001:aa", 2: "not-in-the-graph"})
    assert "node ids the graph does not hold" in raised.value.what


# --------------------------------------------------------------------------
# 5. Arm isolation
# --------------------------------------------------------------------------


def test_an_arm_built_for_one_conversation_cannot_emit_another_s_text():
    """Speaker names repeat across LoCoMo conversations; a leak would score."""
    a = context_arms.Bm25DocumentsArm(
        "conv-a", SYSTEM, _StubRanker([1]), {1: "CONV-A ONLY: Melanie paints."})
    b = context_arms.Bm25DocumentsArm(
        "conv-b", SYSTEM, _StubRanker([1]), {1: "CONV-B ONLY: Melanie sails."})
    prompt_a = a.prompt("What does Melanie do?", budget_tokens=4096)
    prompt_b = b.prompt("What does Melanie do?", budget_tokens=4096)
    assert "CONV-B ONLY" not in prompt_a.request
    assert "CONV-A ONLY" not in prompt_b.request
    assert a.conversation == "conv-a" and b.conversation == "conv-b"


def test_build_arms_refuses_rather_than_falling_back(monkeypatch):
    with pytest.raises(Skip):
        context_arms.build_arms(["graph_only"], conversation="c",
                                system=SYSTEM, documents={1: "x"}, graph=None)
    with pytest.raises(Skip):
        context_arms.build_arms(["bm25_docs"], conversation="c",
                                system=SYSTEM, documents={1: "x"}, ranker=None)
    with pytest.raises(Skip):
        context_arms.parse_arms("bm25_docs,not_an_arm")
    with pytest.raises(Skip):
        context_arms.parse_budgets("0,512")


def test_source_document_nodes_inverts_the_staging_rule():
    class _Node:
        def __init__(self, node_id, path):
            self.id = node_id
            self.type = "SourceDocument"
            self.source_path = path

    class _G:
        nodes = [_Node("SourceDocument:session-0007:x",
                       "/w/corpus/session-0007.md"),
                 _Node("Concept:whatever:y", "/w/corpus/session-0007.md")]

    _G.nodes[1].type = "Concept"
    assert context_arms.source_document_nodes(_G()) == {
        7: "SourceDocument:session-0007:x"}


# --------------------------------------------------------------------------
# 6. The efficiency metric's pathologies
# --------------------------------------------------------------------------


def _point(arm: str, budget: int, *, n=10, correct=5, tokens_sum=10_000,
           refused=0, truncated=0, max_tokens=None) -> efficiency.Point:
    return efficiency.Point(
        arm=arm, budget=budget, n=n, n_correct=correct, n_refused=refused,
        n_errored=0, n_truncated=truncated, score_sum=float(correct),
        token_sum=tokens_sum, max_tokens=max_tokens or (tokens_sum // max(n, 1)))


def test_silence_does_not_score_infinity():
    """The pathology the ratio has and the curve does not."""
    silent = _point("closed_book", 512, correct=1, tokens_sum=0)
    assert efficiency.correctness_per_1k(silent) is None
    real = _point("bm25_docs", 512, correct=5, tokens_sum=10_000)
    assert efficiency.correctness_per_1k(real) == pytest.approx(0.5)


def test_the_ratio_ranking_against_the_accuracy_ranking_is_flagged():
    """A cheap, mostly-wrong arm wins the ratio and loses the measurement."""
    cheap_and_wrong = _point("graph_only", 512, correct=2, tokens_sum=1_000)
    dear_and_right = _point("bm25_docs", 512, correct=9, tokens_sum=100_000)
    flags = efficiency.dominates_by_ratio_only(
        [cheap_and_wrong, dear_and_right])
    assert flags and "graph_only" in flags[0] and "bm25_docs" in flags[0]


def test_aulbc_is_none_rather_than_zero_on_a_single_rung():
    """0.0 would rank a one-rung arm last for a reason that is not about it."""
    assert efficiency.aulbc([_point("a", 512)]) is None
    two = [_point("a", 512, correct=0), _point("a", 8192, correct=10)]
    area = efficiency.aulbc(two)
    assert area is not None and 0.0 < area < 1.0


def test_tokens_to_tau_is_censored_never_imputed():
    ladder = [_point("a", 512, correct=1), _point("a", 8192, correct=3)]
    censored = efficiency.tokens_to_tau(ladder, 0.5)
    assert censored.censored is True and censored.budget is None
    reached = efficiency.tokens_to_tau(
        [_point("a", 512, correct=1), _point("a", 2048, correct=8)], 0.5)
    assert reached.censored is False and reached.budget == 2048


def test_a_row_without_a_token_count_stops_the_curve():
    """A missing count would enter every token mean as free context."""
    result = efficiency.curve([
        {"arm": "a", "budget": 512, "prompt_tokens": 100, "correct": True},
        {"arm": "a", "budget": 512, "correct": True},
    ])
    assert not result.points
    assert result.missing and "prompt_tokens" in result.missing[0]


def test_an_unbudgeted_control_is_never_flagged_over_budget():
    control = _point("closed_book", efficiency.UNBUDGETED, max_tokens=130)
    assert control.over_budget is False
    overshoot = _point("bm25_docs", 512, max_tokens=900)
    assert overshoot.over_budget is True


def test_the_no_evidence_floor_is_reported_against():
    points = [_point("closed_book", efficiency.UNBUDGETED, correct=4),
              _point("graph_only", 512, correct=3),
              _point("bm25_docs", 512, correct=8)]
    flags = efficiency.free_lunch(points)
    assert len(flags) == 1 and "graph_only" in flags[0]


# --------------------------------------------------------------------------
# 7. The report must not report a step that did not run
# --------------------------------------------------------------------------


def test_a_dry_run_ladder_says_not_answered_rather_than_zero():
    built = efficiency.curve([
        {"arm": "bm25_docs@512", "budget": 512, "prompt_tokens": 500,
         "truncated": False},
    ])
    text = "\n".join(run_context._ladder_section(built, None))
    assert "not answered" in text
    assert "0.000" not in text


def test_the_fitting_table_reports_the_knob_the_arm_actually_turned():
    """Regression: the join was on the bare arm name and matched nothing.

    Every budgeted row printed "no knob (unbudgeted)" — a silence that read as
    a finished table describing arms that had turned no knob at all.
    """
    prompts = [
        {"arm": "bm25_docs", "budget": 512, "prompt_tokens": 500,
         "evidence_chars": 1_400, "truncated": True,
         "fit": {"n_kept": 1}},
        {"arm": "graph_only", "budget": 512, "prompt_tokens": 480,
         "evidence_chars": 1_200, "truncated": False,
         "fit": {"requested_chars": 1_000}},
    ]
    # The curve is keyed on the CELL label, exactly as the runner builds it;
    # the prompt rows carry the bare arm name. Joining them is the thing this
    # test exists to hold.
    built = efficiency.curve([
        {**r, "arm": run_context._cell_arm(r["arm"], r["budget"])}
        for r in prompts])
    text = "\n".join(run_context._fitting_section(built, prompts))
    assert "1.00 documents kept" in text
    assert "1,000 chars requested" in text
    assert "no knob (unbudgeted)" not in text


def test_the_three_number_section_prints_all_three_or_names_what_is_missing():
    from evals.locomo.dataset import LocomoQuestion
    from evals.locomo.judge import DeterministicJudge
    from evals.locomo.scoring import grade

    judge = DeterministicJudge()
    question = LocomoQuestion(question="What colour?", category=4,
                              evidence=["D1:1"], conversation="conv-a",
                              answer="teal")
    rows = [grade(judge, question, "teal", key="conv-a#0",
                  arm="bm25_docs@512", replicate=0),
            grade(judge, question, "teal", key="conv-a#0",
                  arm="graph_only@512", replicate=0)]
    from evals.locomo.scoring import decompose

    text = "\n".join(run_context._three_numbers_section({512: decompose(rows)}))
    assert "n (all)" in text and "n (both answered)" in text
    assert "refusals" in text


def test_the_free_lunch_section_says_so_when_the_floor_was_not_run():
    """A missing control must not read as a control that passed."""
    scored = efficiency.curve([
        {"arm": "bm25_docs@512", "budget": 512, "prompt_tokens": 500,
         "correct": True},
    ])
    text = "\n".join(run_context._free_lunch_section(scored))
    assert "closed-book control was not run" in text


def test_the_limits_section_prints_the_corpus_that_fits_in_a_window():
    """conv-26 cannot demonstrate the at-scale claim, and the report says it."""
    built = efficiency.curve([
        {"arm": "whole_corpus", "budget": efficiency.UNBUDGETED,
         "prompt_tokens": 19_906},
    ])
    text = "\n".join(run_context._limits_section(built))
    assert "19,906 tokens" in text
    assert "cannot confirm" in text


# --------------------------------------------------------------------------
# 8. The canary the measured run runs first
# --------------------------------------------------------------------------


def test_the_canary_reaches_the_prompt_backbone_through_the_run_s_own_check():
    """A second canary is a second thing that can pass while the first fails."""
    from evals.locomo.judge import CANARY_GOLD, DeadBackbone
    from evals.locomo.run import canary_backbone

    seen: List[tokens.Prompt] = []

    def alive(prompt: tokens.Prompt) -> str:
        seen.append(prompt)
        return CANARY_GOLD

    assert canary_backbone(run_context.canary_shim(alive)) == 1
    assert seen and CANARY_GOLD in seen[0].user
    assert seen[0].system == _SYSTEM_PROMPT

    with pytest.raises(DeadBackbone):
        canary_backbone(run_context.canary_shim(lambda prompt: ""))


def test_build_prompts_persists_the_request_verbatim(tmp_path):
    """A token claim must be re-derivable from disk without re-spending."""
    from evals.locomo.dataset import Conversation, LocomoQuestion

    conversation = Conversation(
        sample_id="conv-a", speaker_a="A", speaker_b="B", sessions=[],
        questions=[LocomoQuestion(question="What colour?", category=4,
                                  evidence=["D1:1"], conversation="conv-a",
                                  answer="teal")])
    arm = context_arms.Bm25DocumentsArm(
        "conv-a", SYSTEM, _StubRanker([1]), {1: "The bicycle was teal."})
    rows = run_context.build_prompts(conversation, [arm], [2048])
    assert len(rows) == 1
    assert rows[0]["request"] == tokens.serialized_request(
        rows[0]["system"], rows[0]["user"], rows[0]["schema_name"])
    assert rows[0]["prompt_tokens"] == tokens.count_tokens(rows[0]["request"])

    out = tmp_path / "prompts.jsonl"
    run_context.write_prompts(out, rows, {"who": "test"})
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # a meta line, then one prompt
    assert "The bicycle was teal." in lines[1]


def test_unbudgeted_controls_are_built_once_not_once_per_rung():
    from evals.locomo.dataset import Conversation, LocomoQuestion

    conversation = Conversation(
        sample_id="conv-a", speaker_a="A", speaker_b="B", sessions=[],
        questions=[LocomoQuestion(question="Q?", category=4, evidence=[],
                                  conversation="conv-a", answer="teal")])
    arm = context_arms.ClosedBookArm("conv-a", SYSTEM)
    rows = run_context.build_prompts(conversation, [arm], [512, 2048, 8192])
    assert len(rows) == 1
    assert rows[0]["budget"] == efficiency.UNBUDGETED

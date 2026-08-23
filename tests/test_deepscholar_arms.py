"""The two DeepScholar arms, and the parity that makes the control a control.

Offline and synthetic — the graph is built in memory, the backbone is a stub, no
compile and no network. That is not a convenience: the real pair is a subprocess
compile and a metered LLM call, and a harness whose fairness can only be checked
by running the benchmark does not get checked.

Three things are pinned, and each is a way the comparison could be quietly void:

* **Parity.** Both arms go through one budget function, one table renderer and
  one prompt builder, and the tests assert the resulting prompts differ ONLY in
  the evidence block. A control that got a different prompt would not be a
  control, and the difference would not show up in any score.
* **The Tesserae arm reads the graph.** Claim sentences reach the writer,
  through the ``supports_claim`` / ``evidenced_by`` / ``part_of`` edges and not
  through the abstract; and a paper the graph is thin about still gets cited,
  because a claim-only renderer cannot reach a third of a real corpus.
* **The control does not.** ``bm25_cards`` has nowhere in its signature to
  accept a graph, and the test that asserts it never reads one is the test that
  keeps a later edit from smuggling one in.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import pytest

from evals.deepscholar.control import ORIGIN_BM25, bm25_cards, rank_papers
from evals.deepscholar.dataset import CitedPaper, ParentPaper, Query
from evals.deepscholar.evidence import (
    EvidenceBudget,
    EvidenceCard,
    apply_budget,
    graph_cards,
    render_table,
)
from evals.deepscholar.stage import stage_query
from evals.deepscholar.writer import (
    SYSTEM_PROMPT,
    build_user_prompt,
    cited_arxiv_ids,
    render,
    repair_versions,
    strip_links,
)
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType

# Two cue-phrase abstracts the deterministic extractor turns into claims, and
# one phrased so it yields none — the 34.5% case measured on a real corpus.
CLAIMFUL = (
    "We present INLP, a method for removing information from representations. "
    "We propose an iterative projection procedure. "
    "Our method outperforms adversarial training on three tasks. "
    "Code is released."
)
CLAIMLESS = (
    "Fiscal policy shapes inequality. "
    "Taxation has been studied for a century. "
    "This document surveys the area. "
    "Data are scarce."
)

PAPER_A = CitedPaper("2004.07667v1", "2004.07667", "Null It Out", CLAIMFUL)
PAPER_B = CitedPaper("2310.17512v2", "2310.17512", "CompeteAI", CLAIMLESS)

PARENT = ParentPaper(
    row_index=0,
    arxiv_id="2506.02838v1",
    title="TaxAgent",
    abstract="We study removing information from representations with iterative projection.",
    published_date="2025-06-03",
)
QUERY = Query(parent=PARENT, corpus=(PAPER_A, PAPER_B))


def _paper_node(paper: CitedPaper, source: Optional[Path]) -> ResearchNode:
    return ResearchNode(
        id=f"Paper:{paper.arxiv_bare}",
        name=paper.title,
        type=ResearchNodeType("Paper"),
        aliases=[f"arXiv:{paper.arxiv_bare}"],
        source_path=str(source) if source else None,
        metadata={"arxiv_id": paper.arxiv_bare, "title_quality": "paper_file"},
    )


def _graph_with_claims(corpus_dir: Optional[Path] = None) -> ResearchGraph:
    """PAPER_A carries two claims and their spans; PAPER_B carries none.

    The claim and the span hold the SAME sentence, as the deterministic
    extractor emits them, so this fixture also exercises the de-duplication
    that stops a card printing every claim twice.
    """
    def source(paper: CitedPaper) -> Optional[Path]:
        if corpus_dir is None:
            return None
        from evals.deepscholar.stage import document_dir_name

        return corpus_dir / "papers" / document_dir_name(paper.arxiv_bare) / "paper.md"

    nodes = [_paper_node(PAPER_A, source(PAPER_A)), _paper_node(PAPER_B, source(PAPER_B))]
    edges: List[ResearchEdge] = []
    sentences = [
        "We present INLP, a method for removing information from representations.",
        "Our method outperforms adversarial training on three tasks.",
    ]
    for index, sentence in enumerate(sentences):
        claim_id = f"ContributionClaim:c{index}"
        span_id = f"EvidenceSpan:s{index}"
        nodes.append(ResearchNode(id=claim_id, name=f"Contribution: {sentence}",
                                  type=ResearchNodeType("ContributionClaim"),
                                  description=sentence))
        nodes.append(ResearchNode(id=span_id, name=f"Evidence: {sentence}",
                                  type=ResearchNodeType("EvidenceSpan"),
                                  description=sentence))
        edges.append(ResearchEdge(source=nodes[0].id, target=claim_id,
                                  type="supports_claim"))
        edges.append(ResearchEdge(source=claim_id, target=span_id, type="evidenced_by"))
        edges.append(ResearchEdge(source=span_id, target=nodes[0].id, type="part_of"))
    return ResearchGraph(nodes=nodes, edges=edges)


@pytest.fixture()
def staged(tmp_path: Path):
    """A real staged corpus, so the abstract fallback has a file to confine to."""
    return stage_query(QUERY, tmp_path / "work")


class StubBackbone:
    """Records every (system, user) pair and replies from a scripted list."""

    def __init__(self, replies: List[Optional[str]]) -> None:
        self.replies = list(replies)
        self.calls: List[tuple] = []

    def complete_text(self, *, system: str, user: str) -> Optional[str]:
        self.calls.append((system, user))
        return self.replies.pop(0) if self.replies else None


# ------------------------------------------------------- the Tesserae arm


def test_claim_sentences_reach_the_writer_through_the_graph(staged):
    cards = graph_cards(_graph_with_claims(staged.corpus_dir), QUERY,
                        root=staged.work, budget=EvidenceBudget(lines_per_paper=2))
    first = next(c for c in cards if c.paper.arxiv_versioned == PAPER_A.arxiv_versioned)
    assert first.origin == "claim"
    assert first.claim_lines == 2
    assert first.lines == (
        "We present INLP, a method for removing information from representations.",
        "Our method outperforms adversarial training on three tasks.",
    )


def test_a_claim_and_its_evidence_span_are_printed_once(staged):
    """Both carry the same sentence in this extractor. Printing both wastes a
    third of the card's budget saying nothing new."""
    cards = graph_cards(_graph_with_claims(staged.corpus_dir), QUERY,
                        root=staged.work, budget=EvidenceBudget(lines_per_paper=5))
    first = cards[0]
    assert len(set(first.lines)) == len(first.lines)


def test_a_paper_the_graph_is_thin_about_is_still_cited(staged):
    """The 34.5% case. A claim-only renderer cannot cite a third of a real
    corpus, which caps reference_coverage before the writer runs."""
    cards = graph_cards(_graph_with_claims(staged.corpus_dir), QUERY, root=staged.work)
    thin = next(c for c in cards if c.paper.arxiv_versioned == PAPER_B.arxiv_versioned)
    assert thin.origin == "abstract"
    assert thin.claim_lines == 0
    assert thin.lines[0] == "Fiscal policy shapes inequality."


def test_the_abstract_fallback_is_confined_and_fails_closed(tmp_path: Path):
    """``_source_text`` returns "" for a path outside ``root``, silently. A
    fallback that read anything would be reading an unrelated query's corpus."""
    staged = stage_query(QUERY, tmp_path / "work")
    graph = _graph_with_claims(staged.corpus_dir)
    elsewhere = graph_cards(graph, QUERY, root=tmp_path / "somewhere-else")
    assert [c.paper.arxiv_versioned for c in elsewhere] == [PAPER_A.arxiv_versioned]


def test_a_paper_absent_from_the_graph_is_dropped_not_cited_blank(staged):
    graph = _graph_with_claims(staged.corpus_dir)
    pruned = ResearchGraph(
        nodes=[n for n in graph.nodes if n.id != f"Paper:{PAPER_B.arxiv_bare}"],
        edges=graph.edges,
    )
    cards = graph_cards(pruned, QUERY, root=staged.work)
    assert [c.paper.arxiv_versioned for c in cards] == [PAPER_A.arxiv_versioned]


def test_the_arm_never_invents_a_version_suffix(staged):
    """The card's id comes from the DATASET row, not from the Paper node —
    Tesserae normalises the arXiv id to its bare form and never carries v1."""
    cards = graph_cards(_graph_with_claims(staged.corpus_dir), QUERY, root=staged.work)
    assert {c.paper.arxiv_versioned for c in cards} == {"2004.07667v1", "2310.17512v2"}


# ----------------------------------------------------------- the control


def test_the_control_ranks_sentences_by_relevance_not_reading_order():
    cards = bm25_cards(QUERY, budget=EvidenceBudget(lines_per_paper=1))
    first = next(c for c in cards if c.paper.arxiv_versioned == PAPER_A.arxiv_versioned)
    assert first.origin == ORIGIN_BM25
    # The parent abstract is about "removing information ... iterative
    # projection", which is sentence 1 or 2 — never the trailing "Code is
    # released."
    assert "Code is released." not in first.lines


def test_the_control_ranks_papers_by_bm25_over_abstracts():
    order = [position for position, _ in rank_papers(QUERY)]
    assert order[0] == 0  # PAPER_A shares the parent's vocabulary; PAPER_B does not


def test_the_control_cannot_be_handed_a_graph():
    """Structural, not aspirational.

    Two checks, because either alone is escapable. There is nowhere in the
    signature to pass a graph; and no executable line of the module names the
    graph loader or any of the three edges the Tesserae arm walks — asserted
    against the source with its docstrings stripped, so the prose above
    (which does name them, to explain what it is NOT doing) cannot satisfy the
    test on the code's behalf.
    """
    import ast
    import inspect

    import evals.deepscholar.control as control

    assert set(inspect.signature(bm25_cards).parameters) == {"query", "budget"}

    tree = ast.parse(inspect.getsource(control))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(
            body[0].value, ast.Constant
        ) and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    code = ast.unparse(ast.fix_missing_locations(tree))
    for forbidden in ("graph_from_payload", "supports_claim", "evidenced_by",
                      "part_of", "CLAIM_TYPES", "graph_cards"):
        assert forbidden not in code, forbidden


# ------------------------------------------------------------- the parity


@pytest.mark.parametrize("lines_per_paper", [1, 2, 3])
def test_both_arms_show_the_writer_the_same_number_of_lines(staged, lines_per_paper):
    """The comparison is of SELECTION, not of volume. If one arm shows more
    text than the other, any gap between them is unattributable."""
    budget = EvidenceBudget(lines_per_paper=lines_per_paper)
    tesserae = graph_cards(_graph_with_claims(staged.corpus_dir), QUERY,
                           root=staged.work, budget=budget)
    control = bm25_cards(QUERY, budget=budget)
    assert [len(c.lines) for c in tesserae] == [len(c.lines) for c in control]
    assert {c.paper.arxiv_versioned for c in tesserae} == {
        c.paper.arxiv_versioned for c in control
    }


def test_both_arms_obey_the_same_paper_budget(staged):
    budget = EvidenceBudget(papers=1, lines_per_paper=2)
    tesserae = graph_cards(_graph_with_claims(staged.corpus_dir), QUERY,
                           root=staged.work, budget=budget)
    control = bm25_cards(QUERY, budget=budget)
    assert len(tesserae) == len(control) == 1


def test_the_character_cap_binds_both_arms_identically(staged):
    budget = EvidenceBudget(lines_per_paper=3, chars=1)
    assert graph_cards(_graph_with_claims(staged.corpus_dir), QUERY,
                       root=staged.work, budget=budget) == []
    assert bm25_cards(QUERY, budget=budget) == []


def test_apply_budget_never_reorders_an_arms_ranking():
    cards = [
        EvidenceCard(PAPER_B, ("b1", "b2"), "bm25", score=9.0),
        EvidenceCard(PAPER_A, ("a1", "a2"), "bm25", score=1.0),
    ]
    kept = apply_budget(cards, EvidenceBudget(lines_per_paper=1))
    assert [c.paper.arxiv_versioned for c in kept] == [
        PAPER_B.arxiv_versioned, PAPER_A.arxiv_versioned
    ]


def test_trimming_lines_keeps_claim_lines_a_true_count():
    card = EvidenceCard(PAPER_A, ("c1", "c2", "fill"), "claim", claim_lines=2)
    kept = apply_budget([card], EvidenceBudget(lines_per_paper=1))[0]
    assert kept.claim_lines == 1


def test_the_two_arms_differ_only_in_the_evidence_block(staged):
    budget = EvidenceBudget(lines_per_paper=2)
    tesserae = graph_cards(_graph_with_claims(staged.corpus_dir), QUERY,
                           root=staged.work, budget=budget)
    control = bm25_cards(QUERY, budget=budget)
    a = build_user_prompt(QUERY, tesserae)
    b = build_user_prompt(QUERY, control)
    head_a, _, tail_a = a.partition("EVIDENCE\n")
    head_b, _, tail_b = b.partition("EVIDENCE\n")
    assert head_a == head_b
    assert tail_a.partition("\n\nTASK\n")[2] == tail_b.partition("\n\nTASK\n")[2]
    assert tail_a != tail_b


def test_the_evidence_table_prints_the_versioned_id(staged):
    cards = graph_cards(_graph_with_claims(staged.corpus_dir), QUERY, root=staged.work)
    table = render_table(cards)
    assert "arxiv_id: 2004.07667v1" in table
    assert "arxiv_id: 2004.07667\n" not in table


# -------------------------------------------------------------- the writer


def _cited(paper: CitedPaper) -> str:
    return f"[{paper.title}]({paper.url})"


def test_cited_arxiv_ids_reads_what_the_scorer_reads():
    text = (
        f"A claim {_cited(PAPER_A)}. "
        "A reference-style link [name][node-1] and a bare url http://arxiv.org/abs/9.9."
    )
    assert cited_arxiv_ids(text) == [PAPER_A.arxiv_versioned]


def test_render_spends_one_call_and_keeps_a_clean_answer(staged):
    cards = bm25_cards(QUERY)
    body = f"Prior work {_cited(PAPER_A)} and later {_cited(PAPER_B)}."
    backbone = StubBackbone([body])
    result = render(QUERY, cards, backbone=backbone)
    assert result.ok and result.calls == 1
    assert result.text == body
    assert set(result.cited) == {PAPER_A.arxiv_versioned, PAPER_B.arxiv_versioned}
    assert result.stripped_citations == 0
    assert backbone.calls[0][0] == SYSTEM_PROMPT


def test_a_citation_outside_the_corpus_is_retried_then_stripped(staged, capsys):
    cards = bm25_cards(QUERY)
    bad = "Prior work [Elsewhere](http://arxiv.org/abs/1111.11111v1) said so."
    backbone = StubBackbone([bad, bad])
    result = render(QUERY, cards, backbone=backbone)
    assert result.calls == 2
    assert result.stripped_citations == 1
    assert "arxiv.org/abs/1111.11111v1" not in result.text
    assert result.text == "Prior work Elsewhere said so."
    assert result.cited == ()
    assert "stripped 1 citation" in capsys.readouterr().err


def test_a_repaired_retry_is_kept(staged):
    cards = bm25_cards(QUERY)
    good = f"Prior work {_cited(PAPER_A)}."
    backbone = StubBackbone(
        ["A claim [X](http://arxiv.org/abs/1111.11111v1).", good]
    )
    result = render(QUERY, cards, backbone=backbone)
    assert result.calls == 2 and result.stripped_citations == 0
    assert result.text == good
    assert "CORRECTION" in backbone.calls[1][1]


def test_a_silent_backbone_is_an_error_not_an_empty_section(staged):
    result = render(QUERY, bm25_cards(QUERY), backbone=StubBackbone([None]))
    assert not result.ok
    assert result.error == "backbone returned no text"
    assert result.calls == 1


def test_a_dropped_version_suffix_is_repaired_not_zeroed():
    """The failure the first live run of this harness actually produced.

    ``gpt-4o-mini`` wrote ``.../abs/2004.07667`` for a paper the table printed
    as ``2004.07667v1``. The paper is in the corpus and was named correctly;
    only the spelling was wrong, and unrepaired it costs the whole sentence.
    """
    cards = bm25_cards(QUERY)
    body = f"Prior work [Null It Out](http://arxiv.org/abs/{PAPER_A.arxiv_bare})."
    backbone = StubBackbone([body])
    result = render(QUERY, cards, backbone=backbone)
    assert result.calls == 1  # no retry: the repair happens before the check
    assert result.repaired_citations == 1
    assert result.stripped_citations == 0
    assert result.cited == (PAPER_A.arxiv_versioned,)


def test_a_wrong_version_of_a_corpus_paper_is_repaired_too():
    text, repaired = repair_versions(
        "[x](http://arxiv.org/abs/2004.07667v9).", {PAPER_A.arxiv_versioned}
    )
    assert repaired == 1 and PAPER_A.arxiv_versioned in text


def test_repair_never_invents_a_paper_the_writer_was_not_shown():
    """A bare id matching nothing in the corpus is a fabricated source, and
    fabrication must reach ``strip_links`` rather than be guessed at."""
    text, repaired = repair_versions(
        "[x](http://arxiv.org/abs/1111.11111).", {PAPER_A.arxiv_versioned}
    )
    assert repaired == 0 and text == "[x](http://arxiv.org/abs/1111.11111)."


def test_the_table_hands_the_writer_the_finished_citation(staged):
    cards = graph_cards(_graph_with_claims(staged.corpus_dir), QUERY, root=staged.work)
    table = render_table(cards)
    assert f"cite as: [Null It Out]({PAPER_A.url})" in table


def test_strip_links_leaves_good_citations_alone():
    text = f"{_cited(PAPER_A)} and [X](http://arxiv.org/abs/9999.99999v1)."
    out, removed = strip_links(text, {"9999.99999v1"})
    assert removed == 1
    assert _cited(PAPER_A) in out
    assert "9999.99999v1" not in out


def test_both_arms_get_the_identical_system_prompt_and_retry_policy(staged):
    """Neither arm can be advantaged by a prompt it does not share."""
    budget = EvidenceBudget(lines_per_paper=2)
    tesserae = graph_cards(_graph_with_claims(staged.corpus_dir), QUERY,
                           root=staged.work, budget=budget)
    control = bm25_cards(QUERY, budget=budget)
    body = f"Work {_cited(PAPER_A)}."
    left = StubBackbone([body])
    right = StubBackbone([body])
    render(QUERY, tesserae, backbone=left)
    render(QUERY, control, backbone=right)
    assert left.calls[0][0] == right.calls[0][0] == SYSTEM_PROMPT
    assert len(left.calls) == len(right.calls) == 1

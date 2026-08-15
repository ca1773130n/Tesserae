import json
from datetime import date, timedelta

import pytest

from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.temporal import (FACT_MATCH_CEILING, TIMELINE_PAGE_CEILING,
                               TemporalFact, TemporalFactProjector, is_dated,
                               search_facts, timeline)


def temporal_sample_graph():
    paper = ResearchNode(
        id="Paper:a",
        name="Paper A",
        type=ResearchNodeType.PAPER,
        source_path="papers/a.md",
        metadata={"analysis_date": "2026-04-20"},
    )
    method = ResearchNode(
        id="Method:gs",
        name="Gaussian Splatting",
        type=ResearchNodeType.METHODOLOGICAL_CONCEPT,
    )
    claim_old = ResearchNode(
        id="Claim:old",
        name="Claim: old result",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="Old result claim",
        source_path="papers/a.md",
        metadata={"confidence": "medium"},
    )
    claim_new = ResearchNode(
        id="Claim:new",
        name="Claim: new result",
        type=ResearchNodeType.PERFORMANCE_CLAIM,
        description="New result claim",
        source_path="papers/b.md",
        metadata={"analysis_date": "2026-04-27", "confidence": "high"},
    )
    return ResearchGraph(
        nodes=[paper, method, claim_old, claim_new],
        edges=[
            ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="Paper A uses Gaussian Splatting"),
            ResearchEdge(source=paper.id, target=claim_old.id, type="supports_claim", evidence="old evidence"),
            ResearchEdge(source=claim_new.id, target=claim_old.id, type="contradicts_claim", evidence="new evidence contradicts old evidence"),
        ],
    )


def test_temporal_fact_projector_adds_provenance_validity_and_current_status():
    facts = TemporalFactProjector().project(temporal_sample_graph())

    uses_fact = next(fact for fact in facts if fact.predicate == "uses")
    contradiction = next(fact for fact in facts if fact.predicate == "contradicts_claim")
    old_claim_fact = next(fact for fact in facts if fact.object_id == "Claim:old" and fact.predicate == "supports_claim")

    assert uses_fact.valid_from == "2026-04-20"
    assert uses_fact.provenance["source_path"] == "papers/a.md"
    assert uses_fact.evidence == "Paper A uses Gaussian Splatting"
    assert contradiction.confidence == "high"
    assert old_claim_fact.invalidated_by == [contradiction.id]
    assert old_claim_fact.current is False


def test_temporal_fact_projector_writes_jsonl(tmp_path):
    output = tmp_path / "temporal_facts.jsonl"

    facts = TemporalFactProjector().write_jsonl(temporal_sample_graph(), output)

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(facts)
    assert rows[0]["subject_name"]
    assert "provenance" in rows[0]


def _fact(**overrides):
    """A TemporalFact with only the fields a test cares about spelled out.

    In-memory on purpose: search and sort are pure functions of a fact list,
    so pinning them needs no compile, no graph and no LLM.
    """
    base = dict(
        id="TemporalFact:f",
        subject_id="Claim:s",
        subject_name="a subject",
        subject_type="PerformanceClaim",
        predicate="supports_claim",
        object_id="Paper:o",
        object_name="an object",
        object_type="Paper",
    )
    base.update(overrides)
    return TemporalFact(**base)


def test_search_facts_scores_content_not_the_serialized_fact():
    """A query term hiding in an id, a provenance path or a metadata value is
    NOT a match. ``search_facts`` used to score
    ``json.dumps(fact.model_dump())``, so searching for a node-id fragment
    returned every fact carrying that id — ranked as though the fact were
    about the query — and a source path put every fact under that directory
    in front of the one that actually said something."""
    hidden = _fact(
        id="TemporalFact:aldrin",
        subject_id="SessionInsight:aldrin",
        subject_name="a lunar module pilot",
        object_name="Apollo 11 mission report",
        provenance={"source_path": "notes/aldrin.md"},
        metadata={"reviewer": "aldrin"},
    )
    stated = _fact(subject_name="Buzz Aldrin", object_name="Apollo 11 mission report")

    assert search_facts([hidden], "aldrin")["total_matches"] == 0
    assert search_facts([hidden, stated], "aldrin")["total_matches"] == 1
    assert search_facts([hidden, stated], "aldrin")["facts"][0]["subject_name"] == "Buzz Aldrin"


def test_search_facts_matches_every_content_field_and_nothing_else():
    corpus = [
        _fact(subject_name="Buzz Aldrin"),
        _fact(object_name="Buzz Aldrin"),
        _fact(evidence="interviewed Buzz Aldrin in 1971"),
        _fact(predicate="supersedes"),
        _fact(metadata={"note": "Buzz Aldrin"}, provenance={"source_path": "aldrin.md"}),
    ]

    assert search_facts(corpus, "aldrin")["total_matches"] == 3
    assert search_facts(corpus, "supersedes")["total_matches"] == 1


def test_search_facts_ranks_by_bm25_rather_than_term_presence():
    """The old counter scored 1 for "the term is in there somewhere", so a
    passing mention in a wall of evidence tied with a fact that is about the
    term. BM25 breaks that tie on length normalisation and term frequency —
    the ranking signal the substring counter never had."""
    filler = [_fact(id=f"TemporalFact:filler-{i}", subject_name=f"unrelated {i}") for i in range(4)]
    passing_mention = _fact(
        id="TemporalFact:long",
        subject_name="mission log",
        evidence=" ".join(["the crew recorded routine telemetry"] * 8) + " aldrin was aboard",
    )
    about_it = _fact(id="TemporalFact:short", subject_name="Buzz Aldrin", object_name="Apollo 11")

    # `passing_mention` is FIRST in the corpus, so index order cannot be what
    # puts the shorter, denser fact on top.
    ranked = search_facts([passing_mention, about_it, *filler], "aldrin")

    assert [f["id"] for f in ranked["facts"]] == ["TemporalFact:short", "TemporalFact:long"]


def test_search_facts_keeps_substring_recall_that_bm25_alone_would_drop():
    """Admission is lexical, ranking is BM25: "splat" must still find
    "splatting", which whole-token BM25 scores at zero."""
    fact = _fact(subject_name="splatting finding")

    assert search_facts([fact], "splat")["total_matches"] == 1


def test_search_facts_dated_filter_has_three_states_and_refuses_a_fourth():
    dated = _fact(id="TemporalFact:dated", valid_from="2026-01-01")
    undated = _fact(id="TemporalFact:undated", valid_from="undated")
    corpus = [dated, undated]

    assert search_facts(corpus, "")["total_matches"] == 2
    assert "dated" not in search_facts(corpus, "")  # unfiltered says nothing
    only_dated = search_facts(corpus, "", dated="dated")
    only_undated = search_facts(corpus, "", dated="undated")

    assert [f["id"] for f in only_dated["facts"]] == ["TemporalFact:dated"]
    assert [f["id"] for f in only_undated["facts"]] == ["TemporalFact:undated"]
    # Echoed back, so a caller can tell a filtered answer from a thin corpus.
    assert only_dated["dated"] == "dated" and only_undated["dated"] == "undated"

    # A misspelled state must not degrade into "any": that answers a question
    # nobody asked, wearing the label of one they did.
    with pytest.raises(ValueError, match="dated filter"):
        search_facts(corpus, "", dated="undatd")


def test_is_dated_is_parseability_not_the_undated_sentinel():
    """The sentinel is what the projector writes; the predicate is what every
    temporal filter applies. An unparseable non-sentinel is just as unorderable
    and must be bucketed with it, or the filter, the sort and the count drift."""
    assert is_dated("2026-01-01") and is_dated("2026-01-01T00:00:00Z")
    assert not is_dated("undated")
    assert not is_dated("last tuesday")
    assert not is_dated(None) and not is_dated("")


def test_timeline_orders_on_the_parsed_timestamp_not_the_raw_string():
    """Offsets and date-only values are the same instant scale but not the
    same string, so a string sort puts 2026-01-02T00:00+05:00 (which is
    2026-01-01T19:00Z) AFTER the plain 2026-01-02 it precedes."""
    offset = _fact(id="TemporalFact:offset", valid_from="2026-01-02T00:00:00+05:00")
    plain = _fact(id="TemporalFact:plain", valid_from="2026-01-02")

    events = timeline([plain, offset])["events"]

    assert [e["id"] for e in events] == ["TemporalFact:offset", "TemporalFact:plain"]


def test_timeline_buckets_undated_events_last_and_counts_them():
    """73% of this project's own facts are undated. Sorting the literal
    "undated" as a string put them last by the accident that 'u' > '2' and
    said nothing about it; bucketing puts them there on purpose and reports
    how much of the answer carries no time at all."""
    corpus = [
        _fact(id="TemporalFact:u1", subject_name="zeta", valid_from="undated"),
        _fact(id="TemporalFact:d1", valid_from="2026-03-01"),
        _fact(id="TemporalFact:u2", subject_name="alpha", valid_from="undated"),
        _fact(id="TemporalFact:d2", valid_from="2026-01-01"),
    ]

    result = timeline(corpus)

    assert [e["id"] for e in result["events"]] == [
        "TemporalFact:d2",
        "TemporalFact:d1",
        # Bucketed behind every dated row, ordered among themselves rather
        # than by whatever order the projector happened to emit.
        "TemporalFact:u2",
        "TemporalFact:u1",
    ]
    assert result["undated_events"] == 2


def test_timeline_counts_undated_over_the_rows_returned_not_the_matches():
    """Same discipline as ``undated_included``: the count describes the page
    the caller was handed. The undated bucket sits at the tail, so a limit
    eats it first and a corpus-scoped count would claim rows that are not
    there."""
    corpus = [
        _fact(id="TemporalFact:d", valid_from="2026-01-01"),
        _fact(id="TemporalFact:u", valid_from="undated"),
    ]

    page = timeline(corpus, limit=1)

    assert [e["id"] for e in page["events"]] == ["TemporalFact:d"]
    assert page["total_events"] == 2
    assert page["undated_events"] == 0


def _dated_corpus(count):
    """``count`` facts, projected NEWEST first, one calendar day apart.

    Reverse-chronological projection order is what makes the ceiling visible:
    a page taken before the sort keeps the LATEST rows, so the earliest event
    is exactly what a truncating timeline drops.
    """
    start = date(2026, 1, 1)
    return [
        _fact(
            id=f"TemporalFact:e{index}",
            subject_name="Buzz Aldrin",
            valid_from=(start + timedelta(days=count - 1 - index)).isoformat(),
        )
        for index in range(count)
    ]


def test_timeline_total_events_counts_the_matches_not_the_page():
    """``total_events`` must be corpus coverage, never a page-size artefact.

    It was ``len(search_facts(limit=10_000)["facts"])``, and ``search_facts``
    clamps at ``FACT_MATCH_CEILING``, so a 250-match query reported 100 —
    a number shaped exactly like "this is the whole answer"."""
    corpus = _dated_corpus(250)

    result = timeline(corpus, query="aldrin")

    assert result["total_events"] == 250
    assert len(result["events"]) == 50  # the default page, and it says so
    # search_facts keeps its own page clamp, and was already honest above it.
    paged = search_facts(corpus, "aldrin", limit=10_000)
    assert paged["total_matches"] == 250
    assert len(paged["facts"]) == FACT_MATCH_CEILING


def test_timeline_pages_up_to_its_own_ceiling_not_search_facts_one():
    """``limit`` above ``FACT_MATCH_CEILING`` used to be unreachable: timeline
    advertised a 200-row page and could never fill more than 100 of it."""
    corpus = _dated_corpus(250)

    assert len(timeline(corpus, query="aldrin", limit=150)["events"]) == 150
    assert len(timeline(corpus, query="aldrin", limit=10_000)["events"]) == TIMELINE_PAGE_CEILING


def test_timeline_sorts_every_match_not_just_the_first_page():
    """A chronology built from a page is a rank-selected sample wearing a time
    order. The corpus is projected newest-first, so truncating before the sort
    kept the LATEST 100 and a timeline that claims to start at the beginning
    began four months late."""
    corpus = _dated_corpus(250)

    events = timeline(corpus, query="aldrin", limit=5)["events"]

    earliest = min(fact.valid_from for fact in corpus)
    assert events[0]["valid_from"] == earliest
    assert [e["valid_from"] for e in events] == sorted(e["valid_from"] for e in events)


def test_timeline_dated_filter_narrows_before_the_sort():
    corpus = [
        _fact(id="TemporalFact:d", valid_from="2026-01-01"),
        _fact(id="TemporalFact:u", valid_from="undated"),
    ]

    only_dated = timeline(corpus, dated="dated")

    assert [e["id"] for e in only_dated["events"]] == ["TemporalFact:d"]
    assert only_dated["total_events"] == 1
    assert only_dated["undated_events"] == 0
    assert only_dated["dated"] == "dated"


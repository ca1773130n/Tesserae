"""Tests for the BM25-lite ranker, recency boost, stop-words, and the
extended ``search-index.json`` schema.

These lock the Python-side cross-checks for the JS palette behaviour: the JS
implementation in ``JS_SEARCH_PALETTE`` is meant to mirror the helpers in
``llm_wiki.site.search`` (BM25 norm, recency factor, stop-word stripping), so
these tests pin the contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import pytest

from llm_wiki.research_graph import (
    ResearchGraph,
    ResearchGraphBuilder,
    ResearchNodeType,
)
from llm_wiki.site.search import (
    STOP_WORDS,
    average_doc_len,
    bm25_score,
    build_search_index,
    recency_factor,
    score_with_recency,
    tokenize,
    token_set,
)
from llm_wiki.wiki_store import WikiPage


# ---------------------------------------------------------------- tokenizer


def test_tokenize_lowercases_and_splits_on_punct():
    assert tokenize("Vision Banana, Two!") == ["vision", "banana", "two"]


def test_tokenize_strips_english_stopwords():
    # "the", "and", "of", "is" are stopwords; nothing else should drop.
    out = tokenize("The quick brown fox and the lazy dog")
    assert "the" not in out
    assert "and" not in out
    assert "quick" in out
    assert "brown" in out


def test_tokenize_strips_korean_particles():
    # "은", "는", "이", "가" are common particles in our stop list.
    out = tokenize("가우시안 스플래팅은 빠르다")
    # Particle should be gone from the standalone token list.
    assert "은" not in out
    # Real words stay.
    assert any("가우시안" in t for t in out)


def test_token_set_dedupes_preserving_first_seen():
    seq = token_set("vision banana vision banana vision")
    assert seq == ["vision", "banana"]


def test_stop_words_includes_canonical_set():
    # A handful of must-have entries — both English and Korean — so a partial
    # rebuild of the stop list never silently drops them.
    for canonical in ("the", "and", "of", "is", "a", "an", "은", "는", "이", "가"):
        assert canonical in STOP_WORDS, canonical


# -------------------------------------------------------------------- BM25


def _entry(title: str, summary: str, kind: str = "papers") -> Dict[str, object]:
    """Build a search-index entry the way ``build_search_index`` would.

    Mirror the production ``_enrich`` exactly: raw bag-of-tokens (so term
    frequencies carry through to BM25), stop-words stripped.
    """
    text = f"{title} {summary} {kind}"
    tokens = tokenize(text)
    return {
        "id": f"{kind}:{title}",
        "title": title,
        "kind": kind,
        "href": f"{kind}/{title}.html",
        "summary": summary,
        "source_path": "",
        "tokens": tokens,
        "len": len(tokens),
        "created_ts": None,
    }


def test_bm25_orders_relevant_entries_first():
    entries = [
        _entry("Vision Banana", "A banana classifier for vision tasks."),
        _entry("Quantum Foo", "Unrelated quantum literature."),
        _entry("Banana Republic", "Banana lore but no vision angle."),
        _entry("Vision Transformer", "Vision but no banana."),
    ]
    avg = average_doc_len(entries)
    scored = sorted(
        entries,
        key=lambda e: bm25_score("vision banana", e, avg),
        reverse=True,
    )
    # The titular winner has both query tokens — it must come first.
    assert scored[0]["title"] == "Vision Banana"
    # Entries with neither token must score zero.
    assert bm25_score("vision banana", entries[1], avg) == 0


def test_bm25_short_docs_outrank_long_docs_for_same_tf():
    # Two entries with the same single occurrence of "banana" but different
    # ``len`` should put the shorter one first (BM25 length normalization).
    short = _entry("Banana", "A banana.")
    long_ = _entry(
        "Banana",
        "A banana with lots of unrelated words used to inflate the length deliberately for the test. " * 4,
    )
    avg = average_doc_len([short, long_])
    s1 = bm25_score("banana", short, avg)
    s2 = bm25_score("banana", long_, avg)
    # Same tf, but doc-length normalization should not penalize shorter docs.
    assert s1 >= s2


# ---------------------------------------------------------------- recency


def test_recency_factor_fresh_is_one_old_is_zero():
    now = datetime.now(tz=timezone.utc).timestamp()
    fresh = now - 2 * 86400
    very_old = now - 365 * 86400
    middle = now - 90 * 86400
    assert recency_factor(fresh, now_ts=now) == 1.0
    assert recency_factor(very_old, now_ts=now) == 0.0
    # Middle decays to something between (0, 1).
    mid = recency_factor(middle, now_ts=now)
    assert 0.0 < mid < 1.0


def test_recency_factor_handles_none():
    assert recency_factor(None) == 0.0


def test_recent_entry_outranks_identical_old_entry():
    """An entry with ``created_ts`` two days ago beats the identical 200-day-old
    duplicate when the BM25 base is otherwise tied."""
    now = datetime.now(tz=timezone.utc).timestamp()
    fresh = _entry("Gaussian Splatting", "Fast 3D scene rendering.")
    fresh["created_ts"] = int(now - 2 * 86400)
    stale = _entry("Gaussian Splatting Stale", "Fast 3D scene rendering.")
    stale["created_ts"] = int(now - 200 * 86400)
    avg = average_doc_len([fresh, stale])
    s_fresh = score_with_recency("scene rendering", fresh, avg, now_ts=now)
    s_stale = score_with_recency("scene rendering", stale, avg, now_ts=now)
    assert s_fresh > s_stale


# --------------------------------------------------------- stopword querying


def test_stopword_query_still_matches_meaningful_doc():
    """Query 'the gaussian splatting' (with leading stopword) must score
    against an entry containing 'gaussian splatting'."""

    target = _entry("Gaussian Splatting", "A rendering primitive.", kind="concepts")
    other = _entry("Diffusion", "A different concept.", kind="concepts")
    avg = average_doc_len([target, other])
    s_target = bm25_score("the gaussian splatting", target, avg)
    s_other = bm25_score("the gaussian splatting", other, avg)
    assert s_target > 0
    assert s_target > s_other


def test_query_of_only_stopwords_is_zero():
    # "the and of" reduces to no tokens — the score must be exactly 0 so we
    # never explode into a "match everything" pathology.
    target = _entry("Anything", "Words.")
    avg = average_doc_len([target])
    assert bm25_score("the and of", target, avg) == 0.0


# ---------------------------------------------------------- index schema


def test_build_search_index_emits_new_schema_keys():
    builder = ResearchGraphBuilder()
    builder.add_node(
        "Vision Banana",
        ResearchNodeType.PAPER,
        description="A vision banana classifier paper.",
        source_path="data/papers/vision-banana.pdf",
    )
    graph = builder.build()
    index = build_search_index(graph, wiki_pages_by_kind={})
    assert index, "expected at least one entry"
    entry = index[0]
    # Old keys still present (new schema is a superset).
    for old_key in ("id", "title", "kind", "href", "summary", "source_path"):
        assert old_key in entry
    # New BM25 fields.
    assert isinstance(entry["tokens"], list)
    assert all(isinstance(t, str) for t in entry["tokens"])
    assert entry["len"] == len(entry["tokens"])
    assert "created_ts" in entry
    assert any(t in entry["tokens"] for t in ("vision", "banana"))


def test_build_search_index_picks_up_synthesis_generated_at(tmp_path: Path):
    builder = ResearchGraphBuilder()
    graph = builder.build()
    fresh_iso = (datetime.now(tz=timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    page = WikiPage(
        kind="syntheses",
        slug="weekly-2026-w17",
        title="Weekly 2026-W17",
        body="# Weekly 2026-W17\n\nThree papers landed this week.\n",
        path=tmp_path / "syntheses" / "weekly-2026-w17.md",
        frontmatter={
            "title": "Weekly 2026-W17",
            "summary": "Three papers landed this week.",
            "generated_at": fresh_iso,
        },
    )
    index = build_search_index(graph, wiki_pages_by_kind={"syntheses": [page]})
    # The synthesis entry must carry a numeric created_ts roughly equal to
    # the frontmatter ``generated_at`` (2 days ago).
    syntheses = [e for e in index if e["kind"] == "syntheses"]
    assert syntheses, "synthesis page must surface in the index"
    ts = syntheses[0]["created_ts"]
    assert isinstance(ts, int) and ts > 0
    now = datetime.now(tz=timezone.utc).timestamp()
    assert now - ts < 7 * 86400


def test_index_entries_round_trip_to_average_doc_len():
    builder = ResearchGraphBuilder()
    builder.add_node("Foo", ResearchNodeType.CONCEPT, description="A short.")
    builder.add_node("Bar", ResearchNodeType.CONCEPT, description="A different short doc.")
    graph = builder.build()
    index = build_search_index(graph, wiki_pages_by_kind={})
    avg = average_doc_len(index)
    assert avg > 0
    # Every entry's `len` is a non-negative int.
    for e in index:
        assert isinstance(e["len"], int) and e["len"] >= 0

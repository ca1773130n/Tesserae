"""Extraction confidence + revisit signals (Jonasb8/memex ideas).

Confidence/rationale/revisit ride the content-keyed extraction cache, so like
body/turn_ids they are byte-stable across compiles of unchanged sources.
"""

from __future__ import annotations

from tesserae.session_graph import _finding_from_dict, _finding_to_dict
from tesserae.session_graph_llm import Finding, _validate_finding


def test_validate_finding_reads_quality_signals():
    f = _validate_finding(
        {"kind": "decision", "body": "Use FTS5.", "turn_ids": [1],
         "confidence": 0.9, "confidence_rationale": "stated explicitly",
         "revisit_signals": ["if memex is adopted", ""]},
        set(), session_id="s1",
    )
    assert f.confidence == 0.9
    assert f.confidence_rationale == "stated explicitly"
    assert f.revisit_signals == ["if memex is adopted"]  # blank dropped


def test_validate_finding_tolerates_missing_and_clamps():
    f = _validate_finding({"kind": "insight", "body": "x"}, set(), session_id="s1")
    assert f.confidence is None and f.confidence_rationale == "" and f.revisit_signals == []
    hi = _validate_finding({"kind": "insight", "body": "x", "confidence": 5}, set(), session_id="s1")
    assert hi.confidence == 1.0  # clamped to [0, 1]
    bad = _validate_finding({"kind": "insight", "body": "x", "confidence": "nope"}, set(), session_id="s1")
    assert bad.confidence is None


def test_cache_round_trip_preserves_and_stays_lean():
    rich = Finding(kind="decision", body="b", turn_ids=[2], references=["r"],
                   confidence=0.7, confidence_rationale="why", revisit_signals=["when X"])
    assert _finding_from_dict(_finding_to_dict(rich)) == rich
    # A finding with no signals serializes WITHOUT the new keys — caches that
    # predate the feature stay byte-identical to new ones.
    plain = Finding(kind="insight", body="b")
    d = _finding_to_dict(plain)
    assert d == {"kind": "insight", "body": "b", "turn_ids": [], "references": []}
    assert _finding_from_dict(d) == plain


def test_validate_finding_string_revisit_is_one_signal():
    # A bare string must NOT be iterated char-by-char (codex review).
    f = _validate_finding(
        {"kind": "todo", "body": "x", "revisit_signals": "if the API changes"},
        set(), session_id="s1",
    )
    assert f.revisit_signals == ["if the API changes"]


def test_cache_from_dict_degrades_on_bad_values():
    # A malformed/future cache must DEGRADE, not crash compile on a cache hit.
    f = _finding_from_dict({"kind": "insight", "body": "x", "confidence": "nope",
                            "revisit_signals": "single"})
    assert f.confidence is None
    assert f.revisit_signals == ["single"]
    over = _finding_from_dict({"kind": "insight", "body": "x", "confidence": 5})
    assert over.confidence == 1.0  # clamped, never out-of-range into graph.json

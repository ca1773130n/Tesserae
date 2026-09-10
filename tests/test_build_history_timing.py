"""The build-history ledger records what a compile COST, not only what it made.

238 entries on the flagship project recorded node and edge counts and no
duration field at all — so "are compiles getting slower", and the question that
decides whether incremental compile is worth its correctness cost, could not be
answered from the ledger kept for exactly that purpose.
"""
from __future__ import annotations

import json
from pathlib import Path

from tesserae.project import ProjectWiki


def _history(root: Path) -> list[dict]:
    path = root / ".tesserae" / ".build-history.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _seed(tmp_path: Path) -> ProjectWiki:
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "a.md").write_text(
        "# Alpha\n\nRetrieval-Augmented Generation improves grounding.\n", encoding="utf-8"
    )
    return ProjectWiki.init(tmp_path, name="timing", sources=["docs"])


def test_a_compile_records_its_duration_and_mode(tmp_path: Path) -> None:
    wiki = _seed(tmp_path)
    wiki.compile()

    rows = _history(tmp_path)
    assert rows, "a compile must append exactly one ledger row"
    row = rows[-1]
    assert "duration_seconds" in row, f"no duration recorded: {sorted(row)}"
    assert isinstance(row["duration_seconds"], (int, float))
    # monotonic-derived, so it can never be negative even across a clock change.
    assert row["duration_seconds"] >= 0
    assert row["mode"] == "full", "a plain compile is a full compile"
    # The counts that were already there must survive the addition.
    assert "research_nodes" in row and "research_edges" in row


def test_the_row_says_how_much_of_the_corpus_was_re_extracted(tmp_path: Path) -> None:
    """`sources_extracted` is what tells a full compile from an incremental one
    that re-extracted everything anyway."""
    wiki = _seed(tmp_path)
    wiki.compile()
    row = _history(tmp_path)[-1]
    assert row.get("sources_extracted") == 1, row


def test_the_ledger_stays_one_json_object_per_line(tmp_path: Path) -> None:
    """Doctor's build-history trim and karpathy_layer's log.md both parse this
    file line by line; a pretty-printed row would break both."""
    wiki = _seed(tmp_path)
    wiki.compile()
    wiki.compile()
    text = (tmp_path / ".tesserae" / ".build-history.jsonl").read_text(encoding="utf-8")
    assert text.endswith("\n") and "\n\n" not in text
    assert len(_history(tmp_path)) == 2

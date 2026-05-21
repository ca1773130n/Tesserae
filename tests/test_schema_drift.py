"""Tests for the EDC-style schema-drift CLI / module."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Union

import pytest

from tesserae.research_graph import ResearchGraph, ResearchNode, ResearchNodeType
from tesserae.schema_drift import (
    HostTypeReport,
    _cluster_cache_key,
    analyze_schema_drift,
    cluster_nodes_by_jaccard,
    render_report,
)


# --- Test doubles -----------------------------------------------------------


class _ScriptedLLM:
    """Deterministic LLMJsonClient stub.

    Returns proposal payloads in the order they were registered; records every
    call so the test can assert "cache hits skip the LLM on re-run".
    """

    def __init__(self, scripted: List[Optional[Union[dict, list]]]) -> None:
        self._scripted = list(scripted)
        self.calls: List[dict] = []

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema_name: str,
        cache_key: Optional[str] = None,
        max_retries: int = 2,
    ) -> Optional[Union[dict, list]]:
        self.calls.append(
            {"system": system, "user": user, "schema_name": schema_name, "cache_key": cache_key}
        )
        if not self._scripted:
            raise AssertionError("LLM called more times than scripted responses")
        return self._scripted.pop(0)


def _node(node_id: str, name: str) -> ResearchNode:
    return ResearchNode(id=node_id, name=name, type=ResearchNodeType.SOURCE_DOCUMENT)


def _build_two_cluster_graph() -> ResearchGraph:
    """10 SourceDocument nodes, 5 paper-like and 5 codeblock-like."""
    papers = [
        _node(f"p{i}", f"Attention is All You Need — Paper Section {i}")
        for i in range(5)
    ]
    code = [
        _node(f"c{i}", f"Codeblock snippet Python loop iteration {i}")
        for i in range(5)
    ]
    return ResearchGraph(nodes=papers + code, edges=[])


# --- Pure-function tests ----------------------------------------------------


def test_cluster_nodes_by_jaccard_splits_two_obvious_groups():
    graph = _build_two_cluster_graph()
    clusters = cluster_nodes_by_jaccard(
        graph.nodes, threshold=0.34, min_cluster_size=3
    )
    assert len(clusters) == 2, f"expected 2 clusters, got {len(clusters)}"
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [5, 5]
    # Membership: every cluster should be homogeneous (all p* or all c*).
    for cluster in clusters:
        prefixes = {n.id[0] for n in cluster}
        assert len(prefixes) == 1, f"cluster mixed prefixes: {prefixes}"


def test_cluster_cache_key_stable_across_member_order():
    a = [_node("x1", "A"), _node("x2", "B"), _node("x3", "C")]
    b = list(reversed(a))
    assert _cluster_cache_key(a) == _cluster_cache_key(b)


# --- End-to-end test --------------------------------------------------------


def test_analyze_schema_drift_writes_report_and_caches(tmp_path: Path):
    graph = _build_two_cluster_graph()
    tesserae_dir = tmp_path / ".tesserae"
    tesserae_dir.mkdir()

    llm = _ScriptedLLM(
        [
            {
                "sub_types": [
                    {
                        "name": "PaperSection",
                        "description": "Subsection of a research paper document.",
                        "examples": ["p0", "p1", "p2"],
                    }
                ]
            },
            {
                "sub_types": [
                    {
                        "name": "CodeSnippet",
                        "description": "Inline code block excerpt.",
                        "examples": ["c0", "c1", "c2"],
                    }
                ]
            },
        ]
    )

    report_path, reports = analyze_schema_drift(
        graph,
        tesserae_dir=tesserae_dir,
        llm=llm,
        min_volume=5,
        top_k_clusters=5,
        min_cluster_size=3,
    )

    # Report file written at the expected path.
    assert report_path == tesserae_dir / "schema-drift.md"
    assert report_path.exists()
    content = report_path.read_text(encoding="utf-8")

    # Per-host section present.
    assert "## SourceDocument" in content
    # Both proposed sub-type names land in the report body.
    assert "PaperSection" in content
    assert "CodeSnippet" in content
    # Copy-pasteable Suggested enum additions block appears with both.
    assert "## Suggested enum additions" in content
    additions_block = content.split("## Suggested enum additions", 1)[1]
    assert "PAPER_SECTION" in additions_block
    assert "CodeSnippet" in additions_block
    assert "```python" in additions_block

    # The structured report mirror.
    assert len(reports) == 1
    [src_report] = reports
    assert src_report.host_type == "SourceDocument"
    assert src_report.member_count == 10
    assert len(src_report.clusters) == 2
    proposed_names = [
        prop["name"] for _cluster, props in src_report.clusters for prop in props
    ]
    assert sorted(proposed_names) == ["CodeSnippet", "PaperSection"]

    # Cache file exists for SourceDocument.
    cache_path = tesserae_dir / "schema_drift_cache" / "SourceDocument.json"
    assert cache_path.exists()

    # 2 LLM calls (one per cluster) on the first run.
    assert len(llm.calls) == 2

    # Second invocation: cache hit, LLM is NOT called.
    llm2 = _ScriptedLLM([])  # any call raises AssertionError
    report_path2, reports2 = analyze_schema_drift(
        graph,
        tesserae_dir=tesserae_dir,
        llm=llm2,
        min_volume=5,
        top_k_clusters=5,
        min_cluster_size=3,
    )
    assert llm2.calls == [], "cache hits must short-circuit the LLM"
    assert report_path2.exists()
    # Same proposals on the re-run.
    proposed_names2 = sorted(
        prop["name"] for r in reports2 for _c, props in r.clusters for prop in props
    )
    assert proposed_names2 == ["CodeSnippet", "PaperSection"]


def test_analyze_skips_host_below_min_volume(tmp_path: Path):
    graph = ResearchGraph(
        nodes=[_node("p1", "Only one paper-ish doc")], edges=[]
    )
    tesserae_dir = tmp_path / ".tesserae"
    tesserae_dir.mkdir()
    llm = _ScriptedLLM([])
    _path, reports = analyze_schema_drift(
        graph,
        tesserae_dir=tesserae_dir,
        llm=llm,
        min_volume=10,
    )
    assert llm.calls == []
    assert reports[0].clusters == []


def test_render_report_handles_empty_input():
    text = render_report([])
    assert "## Suggested enum additions" in text
    assert "_No candidate sub-types" in text


# --- Regression tests for codex P2 / P3 -------------------------------------


def test_llm_failure_does_not_cache_empty_proposals(tmp_path: Path):
    """P2: a transient LLM failure (None payload) must NOT be cached.

    Otherwise the next run sees an empty `proposals` list, treats it as a
    cache hit, and never retries until a human deletes the cache file.
    """
    graph = _build_two_cluster_graph()
    tesserae_dir = tmp_path / ".tesserae"
    tesserae_dir.mkdir()

    # First run: backend fails for BOTH clusters (returns None twice).
    failing_llm = _ScriptedLLM([None, None])
    _path1, reports1 = analyze_schema_drift(
        graph,
        tesserae_dir=tesserae_dir,
        llm=failing_llm,
        min_volume=5,
        top_k_clusters=5,
        min_cluster_size=3,
    )
    assert len(failing_llm.calls) == 2
    # Reports render, but no proposals landed.
    for _cluster, props in reports1[0].clusters:
        assert props == []

    # Cache file must either not exist or contain no entries — otherwise
    # the next run will short-circuit and skip the LLM forever.
    cache_path = tesserae_dir / "schema_drift_cache" / "SourceDocument.json"
    if cache_path.exists():
        import json as _json

        cached = _json.loads(cache_path.read_text(encoding="utf-8"))
        assert cached == {}, (
            f"failed LLM call must not write cache entries; got {cached!r}"
        )

    # Second run: backend recovers — LLM IS called again for both clusters.
    good_llm = _ScriptedLLM(
        [
            {
                "sub_types": [
                    {
                        "name": "PaperSection",
                        "description": "Paper subsection.",
                        "examples": ["p0", "p1", "p2"],
                    }
                ]
            },
            {
                "sub_types": [
                    {
                        "name": "CodeSnippet",
                        "description": "Code excerpt.",
                        "examples": ["c0", "c1", "c2"],
                    }
                ]
            },
        ]
    )
    _path2, reports2 = analyze_schema_drift(
        graph,
        tesserae_dir=tesserae_dir,
        llm=good_llm,
        min_volume=5,
        top_k_clusters=5,
        min_cluster_size=3,
    )
    assert len(good_llm.calls) == 2, (
        "second run must retry both clusters — the failed cache was not persisted"
    )
    proposed = sorted(
        prop["name"] for r in reports2 for _c, props in r.clusters for prop in props
    )
    assert proposed == ["CodeSnippet", "PaperSection"]
    # Now the cache IS written.
    assert cache_path.exists()


def test_report_uses_actual_min_cluster_size(tmp_path: Path):
    """P3: the 'no clusters found' message must reflect the actual threshold.

    A graph with >= min_volume members but no token-cohesive clusters
    triggers the empty branch. The message should say `>= 2`, not the
    hard-coded `>= 5`.
    """
    # 5 totally-unrelated names — Jaccard never crosses threshold.
    graph = ResearchGraph(
        nodes=[
            _node("a", "Alpha"),
            _node("b", "Bravo"),
            _node("c", "Charlie"),
            _node("d", "Delta"),
            _node("e", "Echo"),
        ],
        edges=[],
    )
    tesserae_dir = tmp_path / ".tesserae"
    tesserae_dir.mkdir()
    llm = _ScriptedLLM([])  # any call raises

    report_path, _reports = analyze_schema_drift(
        graph,
        tesserae_dir=tesserae_dir,
        llm=llm,
        min_volume=5,
        top_k_clusters=5,
        min_cluster_size=2,
    )
    content = report_path.read_text(encoding="utf-8")
    assert "_No clusters of size >= 2 found" in content
    assert "size >= 5" not in content, (
        "report must interpolate the actual min_cluster_size, not the default"
    )


def test_render_report_interpolates_custom_min_cluster_size():
    """Direct unit test on render_report — no clusters, custom threshold."""
    rpt = HostTypeReport(host_type="SourceDocument", member_count=20)
    text = render_report([rpt], min_cluster_size=7)
    assert "_No clusters of size >= 7 found" in text
    assert ">= 5" not in text

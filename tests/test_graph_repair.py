"""graph-repair applies the compile's post-extraction passes to a graph on disk.

The point is parity: a repaired graph and a recompiled graph agree, and a user
does not need hours of recompile to get one anchor per document and one node
per entity.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tesserae.graph_repair import collapse_document_anchors, repair_graph
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def _n(nid, name, typ, path=None):
    return ResearchNode(id=nid, name=name, type=ResearchNodeType(typ), source_path=path)


def _chunked_paper():
    """What a chunk-compiled paper looked like before #238: three anchors for
    one file, each with its own contains edges, plus a cited work the model
    typed as a document (no contains edges of its own)."""
    P = "/corpus/paper.md"
    nodes = [
        _n("SourceDocument:title", "The Title", "SourceDocument", P),
        _n("SourceDocument:related", "2.2. Related work", "SourceDocument", P),
        _n("SourceDocument:further", "Further, we", "SourceDocument", P),
        _n("SourceDocument:cited", "S2orc: the open research corpus", "SourceDocument", P),
        _n("EvidenceSpan:a", "span a", "EvidenceSpan", P),
        _n("EvidenceSpan:b", "span b", "EvidenceSpan", P),
        _n("EvidenceSpan:c", "span c", "EvidenceSpan", P),
        _n("Model:m", "Alpha-Net", "Model", P),
    ]
    edges = [
        ResearchEdge(source="SourceDocument:title", type="contains", target="EvidenceSpan:a"),
        ResearchEdge(source="SourceDocument:title", type="contains", target="EvidenceSpan:b"),
        ResearchEdge(source="SourceDocument:related", type="contains", target="EvidenceSpan:c"),
        ResearchEdge(source="SourceDocument:further", type="contains", target="EvidenceSpan:a"),
        ResearchEdge(source="SourceDocument:further", type="discusses", target="Model:m"),
        ResearchEdge(source="SourceDocument:title", type="references", target="SourceDocument:cited"),
    ]
    return ResearchGraph(nodes=nodes, edges=edges)


def test_chunk_anchors_collapse_to_the_document_and_the_citation_survives():
    g, n = collapse_document_anchors(_chunked_paper())
    assert n == 2
    docs = sorted(x.name for x in g.nodes if x.type.value == "SourceDocument")
    assert docs == ["S2orc: the open research corpus", "The Title"]
    title = "SourceDocument:title"
    ids = {x.id for x in g.nodes}
    assert all(e.source in ids and e.target in ids for e in g.edges)
    assert {e.target for e in g.edges if e.source == title and e.type == "contains"} == {
        "EvidenceSpan:a", "EvidenceSpan:b", "EvidenceSpan:c"}
    assert any(e.source == title and e.type == "discusses" and e.target == "Model:m" for e in g.edges)
    assert len([e for e in g.edges if e.type == "contains"]) == 3, "the duplicate contains edge was deduped"


def test_repair_reports_and_is_idempotent():
    g = _chunked_paper()
    g = ResearchGraph(nodes=g.nodes + [_n("Benchmark:pdb", "PDB", "Benchmark", "/corpus/other.md"),
                                       _n("Dataset:pdb", "PDB", "Dataset", "/corpus/paper.md")], edges=g.edges)
    repaired, report = repair_graph(g)
    assert report["anchors_collapsed"] == 2 and report["entities_merged"] >= 1
    assert report["nodes_after"] == report["nodes_before"] - report["anchors_collapsed"] - report["entities_merged"]
    again, report2 = repair_graph(repaired)
    assert report2["anchors_collapsed"] == 0 and report2["entities_merged"] == 0
    assert again is repaired, "nothing to change must cost nothing"


def test_a_clean_graph_is_returned_untouched():
    g = ResearchGraph(nodes=[_n("SourceDocument:t", "T", "SourceDocument", "/p.md"),
                             _n("EvidenceSpan:a", "a", "EvidenceSpan", "/p.md")],
                      edges=[ResearchEdge(source="SourceDocument:t", type="contains", target="EvidenceSpan:a")])
    out, n = collapse_document_anchors(g)
    assert n == 0 and out is g


def _cli(tmp_path, *argv):
    root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        [sys.executable, "-m", "tesserae.cli", "graph-repair", "--project", str(tmp_path), *argv],
        cwd=str(root), capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(root), "HOME": str(tmp_path)},
    )


def test_cli_dry_run_writes_nothing_and_the_real_run_rewrites_the_graph(tmp_path):
    from tesserae.project import ProjectWiki

    ProjectWiki.init(tmp_path, name="gr")
    wiki = ProjectWiki.load(tmp_path)
    wiki.paths.graph.write_text(_chunked_paper().to_json(indent=2) + "\n", encoding="utf-8")
    before = wiki.paths.graph.read_bytes()

    dry = _cli(tmp_path, "--dry-run", "--json")
    assert dry.returncode == 0, dry.stderr
    rep = json.loads(dry.stdout)
    assert rep["anchors_collapsed"] == 2 and rep["written"] is False
    assert wiki.paths.graph.read_bytes() == before, "a dry run must not touch the file"

    real = _cli(tmp_path, "--json")
    assert real.returncode == 0, real.stderr
    rep = json.loads(real.stdout)
    assert rep["written"] is True
    after = wiki.paths.graph.read_bytes()
    assert after != before and after.endswith(b"\n")
    payload = json.loads(after)
    assert len([n for n in payload["nodes"] if n["type"] == "SourceDocument"]) == 2

    twice = _cli(tmp_path, "--json")
    assert json.loads(twice.stdout)["written"] is False, "second run finds nothing to change"

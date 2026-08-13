"""Kuzu is an export, not a store — pinned in both directions.

The round-trip tests need the optional package and skip without it. The
architectural assertions do not, and must not: they are the ones that fail if
someone reinstates Kuzu as a second authoritative store.
"""

import sys

import pytest

from tesserae.cli import main
from tesserae.kuzu_adapter import KuzuExportUnavailableError, KuzuResearchGraphAdapter, import_kuzu
from tesserae.research_graph import ResearchEdge, ResearchGraph, ResearchNode, ResearchNodeType


def kuzu_sample_graph():
    paper = ResearchNode(id="Paper:p:test", name="Paper A", type=ResearchNodeType.PAPER, metadata={"arxiv_id": "2601.00001"})
    method = ResearchNode(id="MethodologicalConcept:gs:test", name="Gaussian Splatting", type=ResearchNodeType.METHODOLOGICAL_CONCEPT, aliases=["3DGS"])
    return ResearchGraph(
        nodes=[paper, method],
        edges=[ResearchEdge(source=paper.id, target=method.id, type="uses", evidence="uses Gaussian Splatting")],
    )


def test_kuzu_export_writes_nodes_edges_and_can_count(tmp_path):
    pytest.importorskip("kuzu")
    db_path = tmp_path / "research_graph.kuzu"
    adapter = KuzuResearchGraphAdapter(db_path)
    adapter.write_graph(kuzu_sample_graph(), replace=True)

    assert adapter.counts() == {"nodes": 2, "edges": 1}
    loaded = adapter.read_graph()
    assert {node.name for node in loaded.nodes} == {"Paper A", "Gaussian Splatting"}
    assert loaded.edges[0].type == "uses"
    # Base64 round-trip: Kuzu 0.16.0 mangles bracketed STRING values on read,
    # which silently corrupted aliases and metadata before _kuzu_encode.
    aliases = {node.name: node.aliases for node in loaded.nodes}
    assert aliases["Gaussian Splatting"] == ["3DGS"]


def test_cli_export_kuzu_projects_a_bare_graph_file(tmp_path):
    pytest.importorskip("kuzu")
    graph_path = tmp_path / "extracted.graph.json"
    graph_path.write_text(kuzu_sample_graph().to_json(), encoding="utf-8")
    out = tmp_path / "graph.kuzu"

    assert main(["export", "kuzu", "--graph", str(graph_path), "--output", str(out)]) == 0

    assert KuzuResearchGraphAdapter(out).counts() == {"nodes": 2, "edges": 1}


def test_kuzu_export_names_its_missing_package(monkeypatch):
    """A missing optional export dependency names itself, like Graphiti's does."""
    monkeypatch.setitem(sys.modules, "kuzu", None)

    with pytest.raises(KuzuExportUnavailableError) as exc:
        import_kuzu()

    assert "kuzu" in str(exc.value)
    assert "pip install" in str(exc.value)


def test_kuzu_is_not_a_persistence_store():
    """The verdict, as an assertion: no Kuzu store beside the SQLite one.

    ``KuzuResearchGraphStore`` living in :mod:`tesserae.persistence` is what
    made "should Tesserae adopt a graph database?" read as an open question. A
    second authoritative store can disagree with ``graph.json``, and
    byte-idempotence would stop being a sorted-key pure function and start
    depending on a database's write ordering.
    """
    from tesserae import persistence

    assert not [name for name in dir(persistence) if "kuzu" in name.lower()]


def test_extract_kuzu_output_points_at_the_export_verb(capsys):
    """The old store-shaped flag is a clean break, never a silent alias."""
    with pytest.raises(SystemExit) as exc:
        main(["extract", "notes/", "--kuzu-output", "graph.kuzu"])

    assert exc.value.code == 2
    assert "tesserae export kuzu" in capsys.readouterr().err
